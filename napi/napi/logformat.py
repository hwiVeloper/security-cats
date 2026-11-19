"""로그 형식 정의, 한 줄 파서, 경로 정규화.

Task 4의 awk 프로그램(LC_ALL=C)과 똑같이 동작해야 하는 Python twin이다.
그래서 정규식은 ASCII 문자 클래스만 쓰고 \\d, \\w, str.isdigit()은 쓰지 않는다.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from napi.models import CustomFormat

PRESETS: dict[str, str] = {
    "apache_common": "Apache (common)",
    "apache_combined": "Apache (combined)",
    "nginx_combined": "nginx (combined)",
    "webtob": "WebtoB",
    "custom": "직접 지정",
}

_CLF_FORMATS = ("apache_common", "apache_combined", "nginx_combined", "webtob")

_MONTHS = {m: f"{i:02d}" for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}

_CLF_TIME_RE = re.compile(r"\[([0-9]{2})/([A-Za-z]{3})/([0-9]{4})")
_CLF_TIME_FIELD_RE = re.compile(r"([0-9]{2})/([A-Za-z]{3})/([0-9]{4})")
_ISO_DATE_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
_METHOD_RE = re.compile(r"[A-Z]+")
_STATUS_RE = re.compile(r"[0-9]{3}")

_ERROR_UNREADABLE = "이 형식으로 읽을 수 없는 줄이에요"


@dataclass
class ParsedLine:
    method: str
    path: str  # 정규화 전 원문 (쿼리 문자열 포함)
    status: int
    date: str  # YYYYMMDD


def _clf_date(day: str, mon: str, year: str) -> str | None:
    m = _MONTHS.get(mon)
    return f"{year}{m}{day}" if m else None


def _parse_clf(line: str) -> ParsedLine | None:
    tm = _CLF_TIME_RE.search(line)
    if not tm:
        return None
    date = _clf_date(tm.group(1), tm.group(2), tm.group(3))
    if date is None:
        return None
    # 첫 "..." 와 그 뒤 첫 토큰
    q1 = line.find('"')
    if q1 < 0:
        return None
    q2 = line.find('"', q1 + 1)
    if q2 < 0:
        return None
    parts = line[q1 + 1:q2].split(" ")
    if len(parts) < 2 or not _METHOD_RE.fullmatch(parts[0]) or parts[1] == "":
        return None
    rest = line[q2 + 1:].split()
    if not rest or not _STATUS_RE.fullmatch(rest[0]):
        return None
    return ParsedLine(parts[0], parts[1], int(rest[0]), date)


def _parse_custom(line: str, custom: CustomFormat | None) -> ParsedLine | None:
    if custom is None:
        return None
    fields = line.split()

    def field(n: int) -> str | None:
        return fields[n - 1] if 1 <= n <= len(fields) else None

    method = field(custom.method_field)
    path = field(custom.path_field)
    status = field(custom.status_field)
    time_s = field(custom.time_field)
    if method is None or path is None or status is None or time_s is None:
        return None
    if not _METHOD_RE.fullmatch(method) or not _STATUS_RE.fullmatch(status):
        return None
    if len(path) >= 2 and path[0] == '"' and path[-1] == '"':
        path = path[1:-1]
    elif path.startswith('"'):
        path = path[1:]
    elif path.endswith('"'):
        path = path[:-1]
    if path == "":
        return None
    if custom.time_format == "iso8601":
        m = _ISO_DATE_RE.match(time_s)
        if not m:
            return None
        date = m.group(1) + m.group(2) + m.group(3)
    else:
        m = _CLF_TIME_FIELD_RE.search(time_s)
        if not m:
            return None
        date = _clf_date(m.group(1), m.group(2), m.group(3))
        if date is None:
            return None
    return ParsedLine(method, path, int(status), date)


def parse_line(line: str, fmt: str,
               custom: CustomFormat | None = None) -> ParsedLine | None:
    """한 줄을 읽는다. 읽을 수 없으면 None. path는 정규화 전 원문."""
    line = line.rstrip("\r\n")
    if fmt in _CLF_FORMATS:
        return _parse_clf(line)
    if fmt == "custom":
        return _parse_custom(line, custom)
    return None


_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_DIGITS_RE = re.compile(r"[0-9]+")
_HEX16_RE = re.compile(r"[0-9a-fA-F]{16,}")
_NUMLIKE_RE = re.compile(r"[0-9][0-9-]{5,}")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{20,}")
_HAS_DIGIT_RE = re.compile(r"[0-9]")
_HAS_ALPHA_RE = re.compile(r"[A-Za-z]")


def _is_variable(seg: str) -> bool:
    if (_DIGITS_RE.fullmatch(seg) or _UUID_RE.fullmatch(seg)
            or _HEX16_RE.fullmatch(seg) or _NUMLIKE_RE.fullmatch(seg)):
        return True
    if "@" in seg:
        return True
    if "%" in seg and len(seg) >= 20:
        return True
    return bool(_TOKEN_RE.fullmatch(seg) and _HAS_DIGIT_RE.search(seg)
                and _HAS_ALPHA_RE.search(seg))


def normalize_path(path: str) -> str:
    """Global Constraints의 경로 정규화 규칙. awk 구현과 동일해야 한다."""
    for ch in ("?", "#"):
        i = path.find(ch)
        if i >= 0:
            path = path[:i]
    segs = []
    for seg in path.split("/"):
        i = seg.find(";")
        if i >= 0:
            seg = seg[:i]
        if seg == "":
            continue  # 연속 '/'와 끝 '/' 제거
        segs.append("{*}" if _is_variable(seg) else seg)
    return "/" + "/".join(segs)


def test_sample_line(line: str, fmt: str, custom: CustomFormat | None) -> dict:
    """설정 화면의 "샘플 줄로 형식 테스트"."""
    parsed = parse_line(line, fmt, custom)
    if parsed is None:
        return {"ok": False, "method": None, "path": None, "normalized": None,
                "status": None, "date": None, "error": _ERROR_UNREADABLE}
    return {"ok": True, "method": parsed.method, "path": parsed.path,
            "normalized": normalize_path(parsed.path), "status": parsed.status,
            "date": parsed.date, "error": None}
