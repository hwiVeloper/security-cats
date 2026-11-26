"""config.json 로드·검증·저장.

GUI가 읽고 쓰므로 JSON을 쓴다. 파일이 없으면 기본값으로 만들고,
일부 항목이 빠져 있으면 기본값을 채워 넣는다(버전이 달라도 동작하도록).
"""
import copy
import ipaddress
import json
import re
from pathlib import Path

from .. import paths

CONFIG_VERSION = 2

DEFAULT_MAIL_URL = "https://internal.mailapi.com"   # 사내 메일 API
DEFAULT_MAIL_TO = "security@foo.bar"               # 점검 결과를 받는 보안팀 메일

DEFAULT_CONFIG = {
    "version": CONFIG_VERSION,
    "general": {
        "theme": "light",
        "timezone": "Asia/Seoul",
        "default_period": "7",
        "save_dir": "out",
        "local_log_format": "webtob_combined",
        "own_networks": ["10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16"],
        "trusted_proxies": [],
    },
    "detect": {
        "signature_file": "rules/signatures.json",
        "scan_404_threshold": 20,
        "upload_paths": ["/upload", "/uploads", "/files", "/attach", "/data", "/fileupload"],
    },
    "report": {
        "mask_query": False,
    },
    "ssh": {
        "user": "",
        "auth": "password",
        "key_file": "",
        "known_hosts": "config/known_hosts",
        "connect_timeout": 10,
        "command_timeout": 600,      # PC가 서버 응답을 기다리는 시간(초). 이 시간 동안 아무 응답이 없으면 실패
        "server_timeout": 180,       # 서버에서 파일 하나를 읽는 최대 시간(초). 넘으면 서버가 스스로 멈춘다
        "max_lines_per_file": 100000,  # 파일 하나에서 받을 최대 줄 수. 넘으면 '부분 분석'
        "workers": 2,
        "use_ionice": True,
        "evidence_hash": False,      # 켜면 파일마다 sha256sum으로 한 번 더 읽는다(디스크 읽기 2배)
    },
    "log_formats": {
        "webtob_default": '%h %l %u %t "%r" %s %b',
        "webtob_combined": '%h %l %u %t "%r" %s %b "%{Referer}i" "%{User-Agent}i"',
        # WebtoB 에러 로그: [2026-10-23T02:22:37] [CLIENT(1.2.3.4)] [E] [ERR-00045] ... GET /a HTTP/1.1
        "webtob_error": "[%{%Y-%m-%dT%H:%M:%S}t] [CLIENT(%h)] %*",
    },
    # AI 분석 (선택): 결과 화면에서 IP 하나의 로그를 사내 AI(API 게이트웨이)에 보내 의견을 받는다
    "ai": {
        "enabled": False,
        "api": "openai",             # openai(OpenAI 호환) / anthropic
        "url": "",                   # 예: https://ai-gw.사내/v1/chat/completions  (/v1로 끝나면 뒤를 자동으로 붙임)
        "model": "",
        "key_header": "Authorization",
        "key_prefix": "Bearer ",
        "key_saved": "",             # [키 저장]을 켰을 때만: Windows 계정으로 암호화한 키 (다른 PC·계정에서는 못 풂)
        "extra_headers": "",         # 게이트웨이가 더 요구하는 헤더. 한 줄에 '이름: 값'
        "max_lines": 60,             # IP 하나당 보낼 최대 줄(묶음) 수
        "timeout": 120,
        "mask": True,                # 토큰·세션·주민번호·전화번호 등을 가리고 보냄
        "verify_tls": True,
        "use_proxy": False,          # 사내 주소는 보통 프록시를 거치지 않는다
        "anthropic_fallback": False,  # Anthropic: 안전 분류로 거절되면 대체 모델로 다시 (Claude API 직접 연결만. 게이트웨이는 400일 수 있음)
    },
    "email": {                       # 사내 메일 API (POST /email/send)로 결과 보내기
        "enabled": False,
        "url": DEFAULT_MAIL_URL,     # 기본 주소만 적으면 /email/send를 붙임
        "token_header": "Authorization",   # 규격 공통 헤더: Authorization = 발급받은 토큰
        "token_prefix": "",
        "token_saved": "",           # [토큰 저장]을 켰을 때만: Windows 계정으로 암호화한 토큰
        "service_seq": "",           # 규격 공통 헤더 appServiceSeq: 서비스코드 (관리자에게 발급받은 기준정보)
        "extra_headers": "",
        "sender": "",                # 발신자. '주소;이름' 형식 가능 (예: secu@회사.co.kr;보안팀)
        "to": DEFAULT_MAIL_TO,       # 기본 받는 사람 (',' 또는 ';')
        "cc": "",
        "title_prefix": "[IPtrace]",
        "success_code": "0",         # 성공 응답 코드(resCode). 숫자는 값으로 비교(0 = 0000). 비우면 처리 상태만 본다
        "timeout": 30,
        "mask_report": True,         # 첨부 보고서의 URL 쿼리 민감값을 가림
        "verify_tls": True,
        "use_proxy": False,
    },
    "servers": [],
}

SERVER_DEFAULTS = {
    "name": "",
    "host": "",
    "port": 22,
    "user": "",
    "auth": "",
    "key_file": "",
    # 로그 파일 경로 (* 사용 가능). 예: /data/webserver/APP/log/*_ssl/*.log*
    "log_paths": ["/data/webserver/APP/log/*_ssl/*.log*"],
    "format": "webtob_combined",
    # 경로마다 로그 형식: [{"path": ..., "format": ...}]. log_paths·format은 이것에서 만들어지는 호환용 값
    "logs": [],
}
WILDCARDS = "*?["

PERIODS = ("today", "yesterday", "7")


class ConfigError(ValueError):
    pass


def _merge(default, loaded):
    if isinstance(default, dict) and isinstance(loaded, dict):
        out = copy.deepcopy(default)
        for k, v in loaded.items():
            out[k] = _merge(default[k], v) if k in default else v
        return out
    return loaded


def _migrate_server(s: dict) -> dict:
    """v1.1 이전 설정(log_dir + file_pattern)을 log_paths로 옮긴다."""
    s = dict(s)
    if "log_dir" in s or "file_pattern" in s:
        if not s.get("log_paths"):
            s["log_paths"] = [s.get("log_dir", "/data/webserver/log").rstrip("/") + "/" + s.get("file_pattern", "access*.log*")]
        s.pop("log_dir", None)
        s.pop("file_pattern", None)
    if isinstance(s.get("log_paths"), str):
        s["log_paths"] = [x.strip() for x in s["log_paths"].splitlines() if x.strip()]
    return s


def server_logs(s: dict) -> list:
    """서버의 [(로그 경로, 로그 형식)]. 'logs'가 없으면(이전 설정) 모든 경로에 서버 형식을 쓴다."""
    default = s.get("format") or "webtob_combined"
    if s.get("logs"):
        return [(str(x.get("path", "")).strip(), (x.get("format") or default).strip())
                for x in s["logs"] if str(x.get("path", "")).strip()]
    return [(p.strip(), default) for p in s.get("log_paths", []) if p.strip()]


def set_server_logs(s: dict, pairs: list) -> None:
    """경로·형식 목록을 서버에 넣는다 (log_paths·format도 같이 맞춘다)."""
    s["logs"] = [{"path": p, "format": f} for p, f in pairs]
    s["log_paths"] = [p for p, _ in pairs]
    if pairs:
        s["format"] = pairs[0][1]


def normalize(cfg: dict) -> dict:
    old_version = (cfg or {}).get("version")
    cfg = _merge(DEFAULT_CONFIG, cfg or {})
    if old_version == 1:
        # v1에는 증적 해시를 바꾸는 화면이 없어 기본값(켜짐)이 그대로 저장돼 있다. 새 기본값(꺼짐)을 따른다
        cfg["ssh"]["evidence_hash"] = False
    cfg["servers"] = [_merge(SERVER_DEFAULTS, _migrate_server(s)) for s in cfg.get("servers", [])]
    for s in cfg["servers"]:
        set_server_logs(s, server_logs(s))   # 이전 설정(log_paths + format)은 '모든 경로에 같은 형식'으로
    ai = cfg["ai"]
    for k in ("custom_body", "custom_answer"):   # 없어진 '사내 전용'·Ollama 방식의 설정
        ai.pop(k, None)
    if ai.get("api") not in ("openai", "anthropic"):
        ai["api"] = "openai"
    # 조회 기간은 최대 7일, 서버 작업 시간 제한 기본은 180초로 바뀌었다. 예전 기본값이 저장돼 있으면 새 값으로
    em = cfg["email"]
    for k, v in (("url", DEFAULT_MAIL_URL), ("to", DEFAULT_MAIL_TO)):   # 비워 둔 칸은 기본값으로
        if not str(em.get(k) or "").strip():
            em[k] = v
    if str(cfg["general"].get("default_period")) not in PERIODS:
        cfg["general"]["default_period"] = "7"
    if cfg["ssh"].get("server_timeout") == 1800:
        cfg["ssh"]["server_timeout"] = 180
    cfg["version"] = CONFIG_VERSION
    return cfg


def validate(cfg: dict) -> list:
    """설정 오류 목록을 돌려준다(빈 목록이면 정상)."""
    errors = []
    g = cfg["general"]
    for key in ("own_networks", "trusted_proxies"):
        for v in g.get(key, []):
            try:
                ipaddress.ip_network(v, strict=False)
            except ValueError:
                errors.append(f"{key}: '{v}'는 IP/대역 형식이 아닙니다")
    if g.get("theme") not in ("light", "dark"):
        errors.append("general.theme은 light 또는 dark 여야 합니다")
    if str(g.get("default_period")) not in PERIODS:
        errors.append("general.default_period는 today, yesterday, 7 중 하나여야 합니다")
    try:
        if int(cfg["detect"]["scan_404_threshold"]) < 1:
            raise ValueError
    except (ValueError, TypeError):
        errors.append("detect.scan_404_threshold는 1 이상의 숫자여야 합니다")
    ssh = cfg["ssh"]
    for key, low, text in (("max_lines_per_file", 100, "파일당 최대 줄 수는 100 이상"),
                           ("server_timeout", 60, "서버 작업 시간 제한은 60초 이상"),
                           ("workers", 1, "동시 점검 서버 수는 1 이상")):
        try:
            if int(ssh.get(key)) < low:
                raise ValueError
        except (ValueError, TypeError):
            errors.append(f"ssh.{key}: {text}의 숫자여야 합니다")
    ai = cfg["ai"]
    if ai.get("api") not in ("openai", "anthropic"):
        errors.append("ai.api는 openai 또는 anthropic 이어야 합니다")
    for key, low, text in (("max_lines", 1, "AI에 보낼 최대 줄 수는 1 이상"), ("timeout", 5, "AI 시간 제한은 5초 이상")):
        try:
            if int(ai.get(key)) < low:
                raise ValueError
        except (ValueError, TypeError):
            errors.append(f"ai.{key}: {text}의 숫자여야 합니다")
    if ai.get("enabled") and not str(ai.get("url", "")).lower().startswith(("http://", "https://")):
        errors.append("AI 분석: 주소는 http:// 또는 https:// 로 시작해야 합니다")
    em = cfg["email"]
    try:
        if int(em.get("timeout")) < 5:
            raise ValueError
    except (ValueError, TypeError):
        errors.append("email.timeout: 메일 시간 제한은 5초 이상의 숫자여야 합니다")
    if em.get("enabled"):
        if not str(em.get("url", "")).lower().startswith(("http://", "https://")):
            errors.append("이메일: 발송 주소는 http:// 또는 https:// 로 시작해야 합니다")
        addr = re.compile(r"^[\w.+-]+@[\w-]+(?:\.[\w-]+)+$")
        sender = str(em.get("sender", "")).split(";", 1)[0].strip()
        if not sender:
            errors.append("이메일: 발신자를 넣으세요 (예: secu@회사.co.kr;보안팀)")
        elif not addr.match(sender):
            errors.append(f"이메일: 발신자 주소 형식이 올바르지 않습니다: {sender}")
        for key, name, need in (("to", "기본 받는 사람", True), ("cc", "기본 참조", False)):
            addrs = [a for a in re.split(r"[,;\s]+", str(em.get(key, ""))) if a]
            bad = [a for a in addrs if not addr.match(a)]
            if need and not addrs:
                errors.append(f"이메일: {name}을 넣으세요 (여러 명은 , 또는 ; 로 구분)")
            elif bad:
                errors.append(f"이메일: {name} 주소 형식이 올바르지 않습니다: {', '.join(bad[:3])}")
        if not str(em.get("service_seq", "")).strip():
            errors.append("이메일: 서비스코드(appServiceSeq)를 넣으세요 (관리자에게 발급받은 기준정보)")
    names = set()
    for i, s in enumerate(cfg["servers"], 1):
        label = s.get("name") or f"{i}번째 서버"
        if not s.get("name"):
            errors.append(f"{label}: 이름이 비어 있습니다")
        elif s["name"] in names:
            errors.append(f"{label}: 이름이 중복됩니다")
        names.add(s.get("name"))
        if not s.get("host"):
            errors.append(f"{label}: SSH 주소가 비어 있습니다")
        try:
            if not 1 <= int(s.get("port", 22)) <= 65535:
                raise ValueError
        except (ValueError, TypeError):
            errors.append(f"{label}: 포트는 1~65535 사이 숫자여야 합니다")
        logs = server_logs(s)
        if not logs:
            errors.append(f"{label}: 로그 경로가 비어 있습니다")
        for p, fmt in logs:
            if fmt not in cfg["log_formats"] and fmt.upper() not in ("DEFAULT", "COMMON", "COMBINED"):
                errors.append(f"{label}: 로그 경로 '{p}'의 로그 형식 '{fmt}'이 log_formats에 없습니다")
            if not p.startswith("/"):
                errors.append(f"{label}: 로그 경로 '{p}'는 /로 시작하는 절대 경로여야 합니다")
            elif p.endswith("/"):
                errors.append(f"{label}: 로그 경로 '{p}'는 파일 이름(패턴)까지 적어야 합니다 (예: .../*.log*)")
            elif ".." in p.split("/"):
                errors.append(f"{label}: 로그 경로에 '..'는 쓸 수 없습니다")
            elif any(c in p.split("/")[1] for c in WILDCARDS):
                errors.append(f"{label}: 로그 경로 '{p}'의 맨 앞 폴더에는 *를 쓸 수 없습니다")
    return errors


def load(path: Path = None) -> dict:
    """설정을 읽는다. 파일이 없으면 기본값으로 만든다."""
    path = Path(path) if path else paths.CONFIG_FILE
    if not path.exists():
        cfg = normalize({})
        save(cfg, path)
        return cfg
    try:
        with open(path, encoding="utf-8-sig") as f:
            raw = json.load(f)
    except json.JSONDecodeError as e:
        raise ConfigError(f"설정 파일 형식 오류: {path} ({e.lineno}번째 줄)") from e
    return normalize(raw)


def save(cfg: dict, path: Path = None) -> None:
    path = Path(path) if path else paths.CONFIG_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    tmp.replace(path)


def server_by_name(cfg: dict, name: str) -> dict:
    for s in cfg["servers"]:
        if s["name"] == name:
            return s
    raise KeyError(name)


def password_key(cfg: dict, server: dict) -> str:
    """비밀번호는 '계정@주소:포트' 단위로 기억한다. 계정을 바꾸면 다른 비밀번호로 본다."""
    return f"{effective_ssh(cfg, server).get('user', '')}@{server.get('host', '')}:{server.get('port', 22)}"


def effective_ssh(cfg: dict, server: dict) -> dict:
    """서버별 값이 비어 있으면 ssh 공통값을 쓴다."""
    ssh = dict(cfg["ssh"])
    for k in ("user", "auth", "key_file"):
        if server.get(k):
            ssh[k] = server[k]
    return ssh
