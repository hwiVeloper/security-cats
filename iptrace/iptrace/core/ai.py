"""AI 분석 (선택 기능): 의심 IP에 걸린 로그를 사내 AI(API 게이트웨이)에 보내 의견을 받는다.

- 보내는 것: 그 IP의 로그 줄(같은 요청은 묶어서 최대 N개)과 규칙 판정. 서버의 원본 로그 파일은 보내지 않는다.
- 판정(위험도)은 규칙 기반 그대로 두고, AI 의견은 참고로만 보여준다.
- 보내기 전에 토큰·세션·주민번호·전화번호 같은 값을 *** 로 가린다 (설정에서 끌 수 있음).
- 표준 라이브러리(urllib)만 쓴다. 반입할 패키지가 없다.

API 방식
- openai: OpenAI 호환 (vLLM, 사내 게이트웨이 대부분). POST .../v1/chat/completions
- anthropic: Anthropic Messages API (Claude). POST .../v1/messages, 헤더 x-api-key + anthropic-version
"""
import base64
import json
import re
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime

from .detector import TAG_LABELS
from .report import status_text

APIS = (("openai", "OpenAI 호환"), ("anthropic", "Anthropic"))
ANTHROPIC_VERSION = "2026-10-21"      # 게이트웨이가 옛 버전만 받으면 [추가 헤더]에 'anthropic-version: 2023-06-01'
NEED_MODEL = ("Anthropic 방식은 모델 이름이 꼭 있어야 합니다. "
              "[서버 설정] > AI 분석의 [모델 이름]에 게이트웨이 담당자가 알려 준 모델 이름을 넣으세요.")
ANTHROPIC_MAX_TOKENS = 16000

# 지시문도 합니다체로 쓴다. 지시문이 '~한다'체면 답도 '~한다'로 섞여 나온다
SYSTEM_PROMPT = """당신은 기업 보안관제 분석가입니다. 웹·WAS 서버 로그에서 침해의심 IP가 남긴 기록을 보고 판단합니다.
- 주어진 로그만 근거로 쓰고, 로그로 알 수 없는 것은 '로그로는 알 수 없습니다' 또는 추정이라고 밝힙니다.
- 접근 로그에는 POST 본문이 남지 않습니다. 응답 코드와 크기, 이어지는 요청으로 성공 여부를 추정합니다.
- 한국어로, 아래 다섯 항목만 간결하게 씁니다.
- 말투: 모든 문장을 '~합니다', '~입니다', '~보입니다'처럼 합니다체로 끝냅니다. '~한다', '~이다', '~함', '~음'으로 끝내지 않습니다.
  예) 자동 스캔으로 보입니다. (O) / 자동 스캔으로 보인다. (X) / 자동 스캔으로 보임. (X)
[요약] 한두 문장
[공격 의도] 자동 스캔, 취약점 탐색, 특정 취약점 공격, 정상 접속 등과 그 근거
[성공 가능성] 높음 / 중간 / 낮음 / 판단 불가 중 하나와 근거가 된 줄(시각·URL·응답 코드)
[추가 확인] 담당자가 서버에서 더 확인할 것 (파일 생성 여부, 다른 로그 등)
[권고 조치] 차단, 패치, 계정 점검 등"""

TEST_PROMPT = "연결 테스트입니다. 'OK'라고만 답하세요."


class AiError(Exception):
    pass


@dataclass
class AiNote:
    """IP 하나에 대한 AI 의견"""
    ioc_key: str
    text: str
    model: str = ""
    time: datetime = None
    lines_total: int = 0      # 그 IP의 로그 줄 수
    lines_sent: int = 0       # 보낸 줄 수 (묶기 전)
    groups_sent: int = 0      # 보낸 묶음 수
    masked: bool = True
    prompt: str = ""          # 보낸 내용 (화면에서 확인용)
    elapsed: float = 0.0


@dataclass
class Prompt:
    system: str
    user: str
    lines_total: int = 0
    lines_sent: int = 0
    groups_sent: int = 0
    masked: bool = True
    notes: list = field(default_factory=list)


# ------------------------------------------------------------------ 민감값 가리기
_SENSITIVE_KEYS = (r"(?:access_?token|refresh_?token|id_?token|token|jsessionid|sessionid|session|sess|sid|"
                   r"password|passwd|pwd|pw|pass|secret|api_?key|apikey|auth|authorization|key|otp|code|ticket|"
                   r"ssn|jumin|rrn|card_?no|cardno|acct_?no|account_?no|phone|mobile|tel|email)")
_PARAM = re.compile(r"((?:^|[?&;])" + _SENSITIVE_KEYS + r"=)([^&;?\s\"]*)", re.I)
_ATTACKISH = re.compile(r"[<>'\"();`|$\\]|\.\.|/etc/|select\s|union\s|\$\{", re.I)


def _mask_param(m):
    # 공격 문자열(따옴표·<script>·../ 등)은 분석 근거라 남기고, 토큰·세션 같은 값만 가린다
    value = urllib.parse.unquote_plus(m.group(2))
    return m.group(0) if _ATTACKISH.search(value) else m.group(1) + "***"


_MASKS = (
    (_PARAM, _mask_param),
    (re.compile(r"\beyJ[\w-]{6,}\.[\w-]{6,}\.[\w-]{6,}"), "***JWT***"),
    (re.compile(r"(?i)\b(bearer|basic)\s+[\w\-.~+/=]{8,}"), r"\1 ***"),
    (re.compile(r"(?<!\d)\d{6}-?[1-4]\d{6}(?!\d)"), "******-*******"),                      # 주민등록번호
    (re.compile(r"(?<!\d)01[016789]-?\d{3,4}-?\d{4}(?!\d)"), "010-****-****"),             # 휴대전화
    (re.compile(r"(?<!\d)(?:\d{4}[- ]){3}\d{4}(?!\d)"), "****-****-****-****"),            # 카드번호
    (re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+"), "***@***"),                              # 이메일
)


def mask_text(text: str) -> str:
    """보내기 전에 개인정보·인증값을 가린다. IP와 공격 문자열(경로·쿼리 키)은 분석에 필요해 남긴다."""
    for rx, rep in _MASKS:
        text = rx.sub(rep, text)
    return text


# ------------------------------------------------------------------ 보낼 내용 만들기
def _priority(r) -> int:
    """먼저 보낼 줄: 규칙 태그 + 성공 응답 > 규칙 태그 > 5xx > 나머지(많이 반복된 것부터)"""
    tagged = any(t != "UNPARSED" for t in r.tags)
    ok = r.status is not None and 200 <= r.status < 300
    if tagged and ok:
        return 0
    if tagged:
        return 1
    if r.status and r.status >= 500:
        return 2
    return 3


def build_prompt(summary, records, max_lines: int = 60, mask: bool = True) -> Prompt:
    """IP 하나의 판정과 로그를 AI에 보낼 글로 만든다. 같은 요청(메서드·URL·응답)은 한 줄로 묶어 횟수를 단다."""
    m = mask_text if mask else (lambda x: x)
    groups = {}
    for r in sorted(records, key=lambda x: (x.time is None, x.time)):
        if r.unparsed:
            key = ("raw", r.raw[:300])
        else:
            key = (r.method, r.url, r.status, r.user_agent)
        g = groups.get(key)
        if g:
            g["n"] += 1
            g["last"] = r.time or g["last"]
        else:
            groups[key] = {"r": r, "n": 1, "first": r.time, "last": r.time}
    chosen = sorted(groups.values(), key=lambda g: (_priority(g["r"]), -g["n"]))[:max(1, int(max_lines))]
    chosen.sort(key=lambda g: (g["first"] is None, g["first"]))
    lines = []
    for g in chosen:
        r = g["r"]
        when = f"{g['first']:%Y-%m-%d %H:%M:%S}" if g["first"] else "시간 모름"
        if g["n"] > 1 and g["last"] and g["last"] != g["first"]:
            when += f"~{g['last']:%m-%d %H:%M:%S}"
        cnt = f" x{g['n']}" if g["n"] > 1 else ""
        if r.unparsed:
            lines.append(f"{when} | {r.server} | (형식 해석 실패 원문) {m(r.raw[:400])}{cnt}")
            continue
        tags = ",".join(TAG_LABELS.get(t, t) for t in r.tags)
        lines.append(" | ".join([when, r.server, f"{r.method} {m(r.url)[:500]}", str(r.status or "-"),
                                 f"{r.bytes}B", tags or "-", m(r.user_agent or "-")[:160]]) + cnt)
    sent = sum(g["n"] for g in chosen)
    head = [
        f"의심 IP: {summary.ioc_key}" + (f" (대역, 발견 IP: {', '.join(summary.matched_ips[:5])})" if summary.is_range else ""),
        f"규칙 판정: {summary.grade} - {summary.verdict}",
        f"요청 {summary.requests}건 ({status_text(summary)})",
    ]
    if summary.first_seen:
        head.append(f"관찰 기간: {summary.first_seen:%Y-%m-%d %H:%M:%S} ~ {summary.last_seen:%Y-%m-%d %H:%M:%S}")
    if summary.server_counts:
        head.append("서버별 요청: " + ", ".join(f"{k} {v}건" for k, v in summary.server_counts.items()))
    if summary.user_agents:
        head.append("User-Agent: " + " | ".join(m(u)[:120] for u in summary.user_agents[:3]))
    omitted = len(records) - sent
    body = (f"\n로그 ({len(records)}줄 중 {sent}줄, 같은 요청은 묶어서 x횟수로 표시"
            + (f", 나머지 {omitted}줄은 생략" if omitted > 0 else "") + ")\n"
            "형식: 시각 | 서버 | 메서드 URL | 응답 | 크기 | 규칙 태그 | User-Agent\n" + "\n".join(lines))
    return Prompt(SYSTEM_PROMPT, "\n".join(head) + "\n" + body, len(records), sent, len(chosen), mask)


# ------------------------------------------------------------------ 호출
def endpoint(ai: dict) -> str:
    """설정의 주소로 실제 호출할 URL을 만든다. 전체 주소를 적었으면 그대로 쓴다."""
    url = (ai.get("url") or "").strip()
    if not url:
        raise AiError("AI 주소가 비어 있습니다. [서버 설정] > AI 분석에서 주소를 넣으세요.")
    if not url.lower().startswith(("http://", "https://")):
        raise AiError("AI 주소는 http:// 또는 https:// 로 시작해야 합니다.")
    api = ai.get("api", "openai")
    path = urllib.parse.urlparse(url).path.rstrip("/")
    if api == "openai" and (path.endswith("/v1") or path == ""):
        return url.rstrip("/") + ("/chat/completions" if path.endswith("/v1") else "/v1/chat/completions")
    if api == "anthropic" and (path.endswith("/v1") or path == ""):
        return url.rstrip("/") + ("/messages" if path.endswith("/v1") else "/v1/messages")
    return url


def _headers(ai: dict, key: str) -> dict:
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if key and (ai.get("key_header") or "").strip():
        h[ai["key_header"].strip()] = (ai.get("key_prefix") or "") + key
    if ai.get("api") == "anthropic":
        h["anthropic-version"] = ANTHROPIC_VERSION
        if ai.get("anthropic_fallback"):
            h["anthropic-beta"] = "server-side-fallback-2026-07-01"
    for line in str(ai.get("extra_headers") or "").splitlines():
        name, sep, value = line.partition(":")
        if sep and name.strip():
            h[name.strip()] = value.strip()
    return h


def request_body(ai: dict, prompt: Prompt) -> dict:
    model = (ai.get("model") or "").strip()
    if ai.get("api") == "anthropic":
        # system은 messages 밖 최상위에. max_tokens는 필수. temperature는 최신 API 버전에서 받지 않아 보내지 않는다
        if not model:                         # 기본 모델은 두지 않는다 (게이트웨이마다 쓸 수 있는 모델이 다르다)
            raise AiError(NEED_MODEL)
        body = {"model": model, "max_tokens": ANTHROPIC_MAX_TOKENS, "system": prompt.system,
                "messages": [{"role": "user", "content": prompt.user}]}
        if ai.get("anthropic_fallback"):
            body["fallbacks"] = "default"     # 안전 분류로 거절되면 같은 요청을 대체 모델로 다시 (Claude API 직접 연결만)
        return body
    body = {"messages": [{"role": "system", "content": prompt.system}, {"role": "user", "content": prompt.user}],
            "temperature": 0.2, "stream": False}
    if model:
        body["model"] = model
    return body


def _dig(data, path: str):
    """'choices.0.message.content' 같은 경로로 값을 꺼낸다."""
    cur = data
    for part in [p for p in path.strip().split(".") if p]:
        if isinstance(cur, list) and part.lstrip("-").isdigit():
            cur = cur[int(part)]
        elif isinstance(cur, dict) and part in cur:
            cur = cur[part]
        else:
            raise KeyError(part)
    return cur


_THINK = re.compile(r"<think>.*?</think>\s*", re.S)


def _anthropic_answer(data) -> str:
    if not isinstance(data, dict) or not isinstance(data.get("content"), list):
        raise KeyError("content")
    if data.get("stop_reason") == "refusal":
        cat = (data.get("stop_details") or {}).get("category") or "미지정"
        raise AiError(f"AI가 이 요청을 거절했습니다 (안전 분류: {cat}). 공격 로그 내용 때문일 수 있습니다. "
                      "[보낼 최대 줄 수]를 줄이거나, Claude API에 바로 연결한다면 [거절되면 대체 모델 사용]을 켜 보세요.")
    text = "\n".join(b.get("text", "") for b in data["content"] if b.get("type") == "text").strip()
    if data.get("stop_reason") == "max_tokens":
        text += "\n\n(답이 길어 끝부분이 잘렸습니다)"
    return text


def extract_answer(ai: dict, data) -> str:
    if ai.get("api") == "anthropic":
        try:
            return _anthropic_answer(data)
        except (KeyError, TypeError, AttributeError):
            raise AiError("응답에서 답을 찾지 못했습니다 (Anthropic 형식의 content가 없음). 받은 응답: "
                          + json.dumps(data, ensure_ascii=False)[:300]) from None
    paths = ("choices.0.message.content", "choices.0.text", "output_text")
    for p in paths:
        try:
            v = _dig(data, p)
        except (KeyError, IndexError, TypeError):
            continue
        if isinstance(v, str):
            return _THINK.sub("", v).strip()     # 추론 모델의 <think> 부분은 뺀다
    shown = json.dumps(data, ensure_ascii=False)[:300]
    raise AiError(f"응답에서 답을 찾지 못했습니다 (찾은 위치: {', '.join(paths)}). 받은 응답: {shown}")


def _opener(ai: dict):
    ctx = ssl.create_default_context()      # Windows 인증서 저장소(사내 CA 포함)를 쓴다
    if not ai.get("verify_tls", True):
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
    handlers = [urllib.request.HTTPSHandler(context=ctx)]
    if not ai.get("use_proxy", False):
        handlers.append(urllib.request.ProxyHandler({}))   # 사내 주소는 프록시를 거치지 않는다
    return urllib.request.build_opener(*handlers)


def call(ai: dict, key: str, prompt: Prompt) -> tuple:
    """AI를 한 번 호출한다. 돌려주는 값: (답, 걸린 초)"""
    url = endpoint(ai)
    data = json.dumps(request_body(ai, prompt), ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_headers(ai, key), method="POST")
    t0 = time.time()
    try:
        with _opener(ai).open(req, timeout=float(ai.get("timeout") or 120)) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001
            detail = ""
        hint = {401: "인증 실패 - API 키, 키 헤더 이름·앞 글자를 확인하세요",
                403: "권한 없음 - 키 권한이나 게이트웨이 허용 목록을 확인하세요",
                404: "주소를 찾을 수 없음 - URL과 API 방식을 확인하세요",
                413: "보낸 내용이 너무 큼 - '보낼 최대 줄 수'를 줄이세요",
                429: "요청 한도 초과 - 잠시 뒤 다시 하세요"}.get(e.code, "AI 서버 오류" if e.code >= 500 else "요청 거부")
        raise AiError(f"{hint} (HTTP {e.code}) {detail}".strip()) from e
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise AiError("인증서 확인 실패 - 사내 인증서라면 [TLS 인증서 확인]을 끄거나 담당자에게 문의하세요") from e
        if isinstance(reason, TimeoutError) or "timed out" in str(reason):
            raise AiError(f"시간 초과 ({ai.get('timeout')}초) - 시간 제한을 늘리거나 보낼 줄 수를 줄이세요") from e
        raise AiError(f"접속 실패 - {reason}") from e
    except TimeoutError as e:
        raise AiError(f"시간 초과 ({ai.get('timeout')}초) - 시간 제한을 늘리거나 보낼 줄 수를 줄이세요") from e
    try:
        payload = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError as e:
        raise AiError("응답이 JSON이 아닙니다: " + raw[:200].decode("utf-8", "replace")) from e
    return extract_answer(ai, payload), time.time() - t0


def analyze(ai: dict, key: str, summary, records) -> AiNote:
    p = build_prompt(summary, records, int(ai.get("max_lines") or 60), bool(ai.get("mask", True)))
    text, sec = call(ai, key, p)
    return AiNote(summary.ioc_key, text, (ai.get("model") or "").strip(), datetime.now(), p.lines_total,
                  p.lines_sent, p.groups_sent, p.masked, f"[system]\n{p.system}\n\n[user]\n{p.user}", sec)


def test_connection(ai: dict, key: str) -> str:
    text, sec = call(ai, key, Prompt("간단히 답하는 도우미입니다.", TEST_PROMPT))
    return f"연결 성공 ({sec:.1f}초) · 주소 {endpoint(ai)} · 답: {text[:60]}"


# ------------------------------------------------------------------ 키 저장 (Windows 계정으로 암호화)
def protect(secret: str) -> str:
    """Windows DPAPI로 암호화한다. 같은 Windows 계정에서만 풀 수 있다."""
    return base64.b64encode(_dpapi(secret.encode("utf-8"), True)).decode("ascii")


def unprotect(blob: str) -> str:
    return _dpapi(base64.b64decode(blob), False).decode("utf-8")


def _dpapi(data: bytes, encrypt: bool) -> bytes:
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32
    buf = ctypes.create_string_buffer(data, len(data))
    src, out = Blob(len(data), ctypes.cast(buf, ctypes.POINTER(ctypes.c_char))), Blob()
    fn = crypt32.CryptProtectData if encrypt else crypt32.CryptUnprotectData
    if not fn(ctypes.byref(src), None, None, None, None, 0x1, ctypes.byref(out)):   # 0x1: 창을 띄우지 않음
        raise AiError("저장된 API 키를 풀지 못했습니다 (다른 Windows 계정에서 저장한 키). 키를 다시 입력하세요.")
    try:
        return ctypes.string_at(out.pbData, out.cbData)
    finally:
        kernel32.LocalFree(out.pbData)
