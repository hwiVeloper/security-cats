"""점검 결과를 사내 메일 API('이메일 발송', POST /email/send)로 보낸다.

연동 규격 요약
- 요청(JSON): msgFrom·msgTo(필수, 여러 명은 ',' 또는 ';'), msgCc·msgBcc, title·contents(필수, 개인정보 마스킹 필수),
  contentType(text/plain·text/html), ordDay(YYYYMMDD)·ordTime(HHMMSS)·ordNo(요청마다 유일), param1·param2,
  fileList[{fileName, fileEncoding, fileData(Base64)}] (파일당 10MB, 전체 20MB)
- 응답(JSON): resCode·resMessage, resData{transStatus(O 요청 성공 / E 시스템 오류 / D 사업자측 오류 / X 요청 실패),
  trDay·trTime·trNo(API 거래 정보), ordNo ...}
- 공통 요청 헤더: Content-Type, Authorization(발급받은 토큰), appServiceSeq(서비스코드), apiVersion(1.0 고정)
표준 라이브러리(urllib)만 쓴다.
"""
import base64
import json
import re
import secrets
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from .ai import _opener, mask_text

KST = timezone(timedelta(hours=9))
PATH = "/email/send"
API_VERSION = "1.0"                # 규격: apiVersion 헤더 1.0 고정
MAX_FILE = 10 * 1024 * 1024        # 파일 하나 10MB
MAX_TOTAL = 20 * 1024 * 1024       # 전체 20MB
STATUS = {"O": "요청 성공", "E": "시스템 오류", "D": "사업자측 오류", "X": "요청 실패"}
_ADDR = re.compile(r"^[\w.+-]+@[\w-]+(?:\.[\w-]+)+$")


class MailError(Exception):
    pass


@dataclass
class MailResult:
    ord_no: str
    res_code: str = ""
    message: str = ""
    status: str = ""             # transStatus
    tr_no: str = ""
    tr_day: str = ""
    tr_time: str = ""
    to: list = field(default_factory=list)
    elapsed: float = 0.0


def endpoint(em: dict) -> str:
    """설정의 주소로 호출할 URL. 기본 주소만 적었으면 /email/send를 붙인다."""
    url = (em.get("url") or "").strip()
    if not url:
        raise MailError("메일 발송 주소가 비어 있습니다. [서버 설정] > 이메일에서 주소를 넣으세요.")
    if not url.lower().startswith(("http://", "https://")):
        raise MailError("메일 발송 주소는 http:// 또는 https:// 로 시작해야 합니다.")
    path = urllib.parse.urlparse(url).path.rstrip("/")
    return url if path.endswith(PATH) else url.rstrip("/") + PATH


def split_addrs(text: str) -> list:
    """'a@x.com, b@x.com; c@x.com' (줄바꿈도 됨) -> 주소 목록 (중복 제거, 순서 유지)."""
    out = []
    for a in re.split(r"[,;\s]+", text or ""):
        a = a.strip()
        if a and a.lower() not in {x.lower() for x in out}:
            out.append(a)
    return out


def check_addrs(addrs: list, what: str) -> None:
    bad = [a for a in addrs if not _ADDR.match(a)]
    if bad:
        raise MailError(f"{what} 주소 형식이 올바르지 않습니다: {', '.join(bad[:3])}")


def sender_addr(sender: str) -> str:
    """'secu@x.com;보안팀' -> 'secu@x.com'"""
    return (sender or "").split(";", 1)[0].strip()


def new_ord_no(now: datetime) -> str:
    return f"IPTRACE{now:%Y%m%d%H%M%S}{secrets.token_hex(3).upper()}"


def build_request(em: dict, to, cc, title: str, contents: str, attachments=(), bcc=(), now: datetime = None,
                  ord_no: str = None, content_type: str = "text/plain") -> dict:
    """연동 규격의 요청 본문을 만든다. 제목·본문은 개인정보를 가린다. attachments: [(파일 이름, bytes)]"""
    now = now or datetime.now(KST)
    sender = (em.get("sender") or "").strip()
    if not sender:
        raise MailError("발신자 주소가 비어 있습니다. [서버 설정] > 이메일에서 발신자를 넣으세요.")
    check_addrs([sender_addr(sender)], "발신자")
    to, cc, bcc = list(to), list(cc), list(bcc)
    if not to:
        raise MailError("받는 사람을 넣으세요.")
    check_addrs(to, "받는 사람")
    check_addrs(cc, "참조")
    check_addrs(bcc, "숨은 참조")
    title, contents = mask_text(title.strip()), mask_text(contents)      # 규격: 제목·내용 개인정보 마스킹 필수
    if not title:
        raise MailError("제목을 넣으세요.")
    body = {"msgFrom": sender, "msgTo": ",".join(to), "title": title, "contents": contents,
            "contentType": content_type, "ordDay": f"{now:%Y%m%d}", "ordTime": f"{now:%H%M%S}",
            "ordNo": ord_no or new_ord_no(now), "param1": "IPtrace"}
    if cc:
        body["msgCc"] = ",".join(cc)
    if bcc:
        body["msgBcc"] = ",".join(bcc)
    for k, limit in (("msgFrom", 256), ("msgTo", 1024), ("msgCc", 1024), ("msgBcc", 1024), ("title", 1024)):
        if len(body.get(k, "")) > limit:
            raise MailError(f"{k}가 너무 깁니다 ({len(body[k])}자, 최대 {limit}자). 받는 사람을 나눠 보내세요."
                            if k.startswith("msg") else f"제목이 너무 깁니다 (최대 {limit}자).")
    files, total = [], 0
    for name, data in attachments:
        if len(data) > MAX_FILE:
            raise MailError(f"첨부 파일이 10MB를 넘습니다 ({name}, {len(data) / 1e6:.1f}MB). 파일로 저장해 따로 전달하세요.")
        total += len(data)
        files.append({"fileName": name, "fileEncoding": "UTF-8", "fileData": base64.b64encode(data).decode("ascii")})
    if total > MAX_TOTAL:
        raise MailError(f"첨부 파일 합계가 20MB를 넘습니다 ({total / 1e6:.1f}MB).")
    if files:
        body["fileList"] = files
    return body


def same_code(got: str, want: str) -> bool:
    """응답 코드 비교. 숫자면 값으로 본다 (0 = "0" = "0000")."""
    got, want = str(got).strip(), str(want).strip()
    return got == want or (got.isdigit() and want.isdigit() and int(got) == int(want))


def _headers(em: dict, token: str) -> dict:
    """규격 공통 요청 헤더: Content-Type, Authorization(발급받은 토큰), appServiceSeq(서비스코드), apiVersion(1.0 고정)"""
    h = {"Content-Type": "application/json; charset=utf-8", "Accept": "application/json",
         "appServiceSeq": str(em.get("service_seq") or "").strip(), "apiVersion": API_VERSION}
    if token and (em.get("token_header") or "").strip():
        h[em["token_header"].strip()] = (em.get("token_prefix") or "") + token
    for line in str(em.get("extra_headers") or "").splitlines():
        name, sep, value = line.partition(":")
        if sep and name.strip():
            h[name.strip()] = value.strip()
    return h


def send(em: dict, token: str, body: dict) -> MailResult:
    """요청을 보내고 응답을 확인한다. 실패하면 MailError (이유를 사람이 읽을 수 있게)."""
    url = endpoint(em)
    if not str(em.get("service_seq") or "").strip():
        raise MailError("서비스코드(appServiceSeq)가 비어 있습니다. [서버 설정] > 이메일에서 넣으세요.")
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers=_headers(em, token), method="POST")
    t0 = time.time()
    timeout = float(em.get("timeout") or 30)
    try:
        with _opener(em).open(req, timeout=timeout) as resp:
            raw = resp.read()
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:200]
        except Exception:  # noqa: BLE001
            detail = ""
        hint = {401: "인증 실패 - 인증 토큰과 토큰 헤더 이름·앞 글자를 확인하세요",
                403: "권한 없음 - 토큰 권한이나 허용 목록을 확인하세요",
                404: "주소를 찾을 수 없음 - 메일 발송 주소를 확인하세요",
                413: "보낸 내용이 너무 큼 - 첨부 없이 보내 보세요"}.get(e.code, "메일 서버 오류" if e.code >= 500 else "요청 거부")
        raise MailError(f"{hint} (HTTP {e.code}) {detail}".strip()) from e
    except urllib.error.URLError as e:
        reason = e.reason
        if isinstance(reason, ssl.SSLCertVerificationError):
            raise MailError("인증서 확인 실패 - 사내 인증서라면 [TLS 인증서 확인]을 끄거나 담당자에게 문의하세요") from e
        if isinstance(reason, TimeoutError) or "timed out" in str(reason):
            raise MailError(f"시간 초과 ({timeout:.0f}초) - 메일 서버 상태를 확인하거나 시간 제한을 늘리세요") from e
        raise MailError(f"접속 실패 - {reason}") from e
    except TimeoutError as e:
        raise MailError(f"시간 초과 ({timeout:.0f}초) - 메일 서버 상태를 확인하거나 시간 제한을 늘리세요") from e
    try:
        res = json.loads(raw.decode("utf-8", "replace"))
    except json.JSONDecodeError as e:
        raise MailError("응답이 JSON이 아닙니다: " + raw[:200].decode("utf-8", "replace")) from e
    if not isinstance(res, dict):
        raise MailError("응답 형식이 규격과 다릅니다: " + json.dumps(res, ensure_ascii=False)[:200])
    rd = res.get("resData") if isinstance(res.get("resData"), dict) else {}
    r = MailResult(ord_no=body.get("ordNo", ""), res_code=str(res.get("resCode", "")),
                   message=str(res.get("resMessage", "")), status=str(rd.get("transStatus") or ""),
                   tr_no=str(rd.get("trNo") or ""), tr_day=str(rd.get("trDay") or ""), tr_time=str(rd.get("trTime") or ""),
                   to=split_addrs(body.get("msgTo", "")), elapsed=time.time() - t0)
    ok_code = str(em.get("success_code") or "").strip()
    if ok_code and not same_code(r.res_code, ok_code):
        raise MailError(f"메일 발송 실패 (응답 코드 {r.res_code or '없음'}: {r.message or '메시지 없음'})")
    if r.status and r.status != "O":
        raise MailError(f"메일 발송 실패 (처리 상태 {r.status}: {STATUS.get(r.status, '알 수 없음')}"
                        + (f", {r.message}" if r.message else "") + ")")
    return r
