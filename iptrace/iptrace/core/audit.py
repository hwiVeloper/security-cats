"""실행 감사 로그 (PLAN.md 12장): 누가, 언제, 몇 개 IP로, 어떤 서버를 조회했는지 남긴다."""
import getpass
import json
from datetime import datetime

from .. import paths


def _write(entry: dict) -> None:
    try:
        paths.LOGS_DIR.mkdir(parents=True, exist_ok=True)
        with open(paths.LOGS_DIR / "audit.log", "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
    except OSError:
        pass  # 감사 로그 실패가 점검을 막지 않게 한다


def _user() -> str:
    try:
        return getpass.getuser()
    except Exception:  # noqa: BLE001
        return "unknown"


def log_check(result) -> None:
    c = result.summary_counts()
    _write({
        "time": datetime.now().isoformat(timespec="seconds"),
        "user": _user(),
        "action": "check",
        "ioc_count": c["iocs"],
        "ioc_hash": result.request.ioc_text_hash,
        "period": [result.request.start.isoformat(), result.request.end.isoformat()],
        "servers": [{"name": s.name, "ok": s.ok, "reason": s.reason} for s in result.servers],
        "accessed": c["accessed"],
        "grades": c["grades"],
    })


def log_ai(ioc_key: str, url: str, lines: int, masked: bool, ok: bool, error: str = "") -> None:
    """AI에 무엇을 보냈는지(내용 말고 IP·주소·줄 수만) 남긴다."""
    from urllib.parse import urlparse
    _write({"time": datetime.now().isoformat(timespec="seconds"), "user": _user(), "action": "ai",
            "ioc": ioc_key, "host": urlparse(url).netloc, "lines": lines, "masked": masked, "ok": ok,
            "error": error[:200]})


def log_email(url: str, ord_no: str, to_count: int, cc_count: int, attached: bool, ok: bool,
              tr_no: str = "", error: str = "") -> None:
    """메일을 보냈는지(내용·주소 말고 건수·주문번호·거래번호만) 남긴다."""
    from urllib.parse import urlparse
    _write({"time": datetime.now().isoformat(timespec="seconds"), "user": _user(), "action": "email",
            "host": urlparse(url).netloc, "ord_no": ord_no, "to": to_count, "cc": cc_count, "attached": attached,
            "ok": ok, "tr_no": tr_no, "error": error[:200]})


def log_save(path: str) -> None:
    _write({"time": datetime.now().isoformat(timespec="seconds"), "user": _user(), "action": "save", "path": path})
