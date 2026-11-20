"""로그 형식 정의(LogFormat 컴파일러), 한 줄 파서, 경로 정규화.

원격 awk 프로그램(LC_ALL=C)과 똑같이 동작해야 하는 Python twin이다.
그래서 정규식은 ASCII 문자 클래스만 쓰고 \\d, \\w, str.isdigit()은 쓰지 않는다.

AWK PARITY CONTRACT (awk가 그대로 따라야 하는 의미, v1)
- 줄 끝: 끝의 "\n" 하나를 제거한 뒤 끝의 "\r" 하나를 제거한다 (awk: sub(/\r$/, "")).
- 공백 분할: 필드/토큰은 [ \t]+ 로만 나눈다 (앞뒤 space/tab은 먼저 제거).
  U+3000 같은 유니코드 공백은 구분자가 아니다. awk 기본 분할과 같다.
- 기본 제공 형식과 사용자 형식: 아래 FORMAT PARSE CONTRACT를 따른다.
  (Task 2 전까지 원격 awk는 v1 CLF 파서를 쓴다: 첫 [dd/Mon/yyyy에서 날짜, 첫 "..." 안의
  토큰 2개 이상 + 첫 토큰 ^[A-Z]+$, 닫는 따옴표 뒤 첫 토큰이 3자리 숫자여야 status.)
- custom: 줄을 [ \t]+로 나눈 N번째(1부터) 필드를 쓴다. path 필드의 양끝 따옴표는 제거,
  제거 후 빈 문자열이면 실패. iso8601은 필드가 yyyy-mm-dd로 시작해야 한다.
- 정규화: ?/# 이후 제거 -> "/"로 나눠 각 조각의 ";" 이후 제거 -> 빈 조각 버림
  (연속 "/", 끝 "/", ";"만 남은 조각 포함) -> 각 조각을 규칙 순서대로 검사
  (숫자, UUID, 16자 이상 hex, 숫자로 시작하는 [0-9-]{6,}, "@" 포함, "%" 포함 + 20바이트 이상,
  [A-Za-z0-9_-]{20,} 이면서 영문과 숫자 모두 포함) 하나라도 맞으면 {*}.
  결과는 항상 "/" + 조각들을 "/"로 합친 것 (원래 "/"로 시작하지 않아도 앞에 "/"를 붙인다. 빈 경로는 "/").
- "%" 규칙의 길이는 UTF-8 바이트 수다. 한글 한 글자는 3바이트.

FORMAT PARSE CONTRACT (compile_format이 만든 FormatSpec을 한 줄에 적용하는 규칙)
- 토큰: lit(정확히 맞아야 하는 리터럴 text), ws(공백 리터럴), field(directive).
  compile_format은 패턴의 리터럴을 EXACT 부분(lit)과 space/tab 연속(ws)으로 번갈아 나눈다.
  field 두 개가 붙는 것을 막으므로 field 다음 토큰은 항상 lit 또는 ws이거나 없다.
- 필수 토큰: 요청(r, m, U), 날짜(t, time_layout이 있는 tfmt), 상태(s).
  last_essential = 그중 마지막 토큰의 위치. 토큰을 앞에서부터 차례로 처리하다가 last_essential
  토큰을 처리하면 그 즉시 성공한다. 그 뒤 토큰은 보지 않는다(없거나 틀려도 된다).
  그 전까지는 하나라도 실패하면 줄 전체가 실패다.
- 줄 정리: 줄 끝의 "\n" 하나, 그다음 "\r" 하나를 제거한다. 커서는 1부터 시작한다
  (Python 구현은 0부터). 줄 앞 공백은 건너뛰지 않는다 (패턴이 ws로 시작할 때만 ws가 먹는다).
- lit: 커서 위치의 부분 문자열이 text와 정확히 같아야 한다. 같으면 커서를 length(text)만큼 옮긴다.
- ws: 커서 위치에 space/tab이 하나 이상 있어야 하고, 이어지는 space/tab을 모두 먹는다.
- field (t 제외)의 값. 다음 토큰은 last_essential 뒤에 있어도 경계로 쓴다:
  - 다음 토큰이 lit이면 커서부터 그 text가 처음 나오는 위치 직전까지 (빈 값도 된다).
    text가 없으면 실패. 커서는 그 위치(다음 lit의 시작)로 옮긴다.
  - 다음 토큰이 ws이면 커서부터 처음 나오는 space 또는 tab 직전까지, 없으면 줄 끝까지.
    값이 비어 있으면 실패. 커서는 값 끝으로 옮긴다.
  - 다음 토큰이 없으면 줄 끝까지 (빈 값도 된다).
- t: 커서 위치 문자가 "["이어야 하고, 커서부터 처음 나오는 "]"까지(포함)가 값이다. "]"가 없으면 실패.
  값의 둘째 글자부터 ^[0-9][0-9]/[A-Za-z][A-Za-z][A-Za-z]/[0-9][0-9][0-9][0-9]로 시작해야 하고
  월 이름이 v1 월 맵(Jan..Dec, 대소문자 구분)에 있어야 한다. 날짜 = yyyy mm dd.
- tfmt (%{...}t): 값의 바이트 길이가 time_width와 같아야 한다. time_layout이 비어 있지 않으면
  (Y, m 또는 b, d가 모두 있는 형식) 항목을 차례로 (오프셋 0부터, 바이트 단위) 잘라 낸다.
  Y, m, d는 ^[0-9]+$이어야 하고, b는 v1 월 맵에 있어야 한다(→ 두 자리 월). 날짜 = Y m d.
  time_layout이 비어 있으면 날짜를 정하지 않는다(폭만 검사).
- r: 값의 앞뒤 space/tab을 지우고 [ \t]+로 나눈다. 토큰이 2개 이상이고 첫 토큰이 ^[A-Z]+$이어야 한다.
  method = 첫 토큰, path = 둘째 토큰.
- m: 값이 ^[A-Z]+$이어야 한다. method = 값.  U: path = 값 그대로.
- s: 값이 ^[0-9][0-9][0-9]$이어야 한다. status = 값. 형식에 s가 없으면 status는 없음(None, awk는 "").
- 그 밖의 field(h l u q H b B D T i o skip): 값을 버린다.
- 같은 종류의 값을 정하는 field가 여러 번 나오면 나중 것이 이긴다.
- 마지막으로 처리한 토큰 뒤에 남은 글자는 무시한다.
- 바이트/문자: 리터럴 찾기와 비교는 어느 쪽이든 결과가 같다. tfmt의 폭과 오프셋은 UTF-8 바이트다
  (awk LC_ALL=C의 length/substr과 같게).
- 상태군: status가 있으면 첫 글자 + "xx", 없으면 "???".
"""
from __future__ import annotations

import functools
import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal

from napi.models import CustomFormat, LogFormatDef

BUILTIN_FORMATS: dict[str, str] = {
    "apache_common": '%h %l %u %t "%r" %>s %b',
    "apache_combined": '%h %l %u %t "%r" %>s %b "%{Referer}i" "%{User-Agent}i"',
    "nginx_combined": '%h %l %u %t "%r" %>s %b "%{Referer}i" "%{User-Agent}i"',
    "webtob": '%h %l %u %t "%r" %s %b',
    "webtob_combined": '%h %l %u %t "%r" %s %b "%{Referer}i" "%{User-Agent}i"',
}

PRESETS: dict[str, str] = {
    "apache_common": "Apache (common)",
    "apache_combined": "Apache (combined)",
    "nginx_combined": "nginx (combined)",
    "webtob": "WebtoB",
    "webtob_combined": "WebtoB (combined)",
    "custom": "직접 지정 (필드 번호)",
}

# Task 2 전까지 원격 awk_program이 v1 CLF 파서를 고를 때 쓴다.
_CLF_FORMATS = ("apache_common", "apache_combined", "nginx_combined", "webtob")

_MONTHS = {m: f"{i:02d}" for i, m in enumerate(
    ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
     "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"], start=1)}
_MONTHS_B = {k.encode("ascii"): v for k, v in _MONTHS.items()}

_CLF_TIME_RE = re.compile(r"\[([0-9]{2})/([A-Za-z]{3})/([0-9]{4})")
_CLF_TIME_FIELD_RE = re.compile(r"([0-9]{2})/([A-Za-z]{3})/([0-9]{4})")
_ISO_DATE_RE = re.compile(r"([0-9]{4})-([0-9]{2})-([0-9]{2})")
_METHOD_RE = re.compile(r"[A-Z]+")
_STATUS_RE = re.compile(r"[0-9]{3}")
_DIGITS_B_RE = re.compile(rb"[0-9]+")

_WS_RE = re.compile(r"[ \t]+")

_ERROR_UNREADABLE = "이 형식으로 읽을 수 없는 줄이에요"
_ERROR_REQUIRED = "API 호출을 셀 수 없는 형식이에요 (요청/시각 필요)"


class FormatError(ValueError):
    """형식 정의 오류. 메시지는 사용자에게 그대로 보여 줄 한국어다."""


@dataclass
class ParsedLine:
    method: str
    path: str  # 정규화 전 원문 (쿼리 문자열 포함)
    status: int | None  # None = 상태 미상 (형식에 %s가 없음)
    date: str  # YYYYMMDD


@dataclass(frozen=True)
class Token:
    kind: Literal["lit", "ws", "field"]
    # lit: 정확히 맞아야 하는 리터럴, ws: 패턴의 space/tab 연속(줄에서는 1개 이상과 맞음),
    # field: 형식에 적힌 지시자 원문 (예: "%{Referer}i")
    text: str
    directive: str = ""
    # tfmt: 날짜 항목 (Y|m|d|b, 바이트 오프셋, 길이). 날짜를 다 갖춘 형식일 때만 채운다.
    time_layout: tuple[tuple[str, int, int], ...] = ()
    time_width: int = 0  # tfmt: 값 전체의 바이트 폭


@dataclass(frozen=True)
class FormatSpec:
    tokens: tuple[Token, ...]
    has_status: bool

    @property
    def last_essential(self) -> int:
        """마지막 필수 토큰(요청, 날짜, 상태)의 위치. 없으면 -1."""
        return max((i for i, t in enumerate(self.tokens) if _is_essential(t)), default=-1)


_ESSENTIAL = frozenset(("r", "m", "U", "s", "t"))


def _is_essential(tok: Token) -> bool:
    return tok.kind == "field" and (
        tok.directive in _ESSENTIAL or (tok.directive == "tfmt" and bool(tok.time_layout)))


# 지시자 문자 -> directive. 여기에 없는 문자(와 %*)는 skip.
_DIRECTIVES = frozenset("h l u t r m U q H s b B D T i o".split())
# %{...}t 안에서 받는 strftime 지시자와 폭
_TIME_WIDTHS = {"Y": 4, "m": 2, "d": 2, "H": 2, "M": 2, "S": 2, "b": 3, "z": 5, "y": 2}
_TIME_DATE_KEYS = ("Y", "m", "d", "b")


def _time_layout(arg: str) -> tuple[tuple[tuple[str, int, int], ...], int]:
    """%{arg}t의 (날짜 항목, 전체 바이트 폭). 날짜를 다 갖추지 못하면 항목은 ()."""
    layout: list[tuple[str, int, int]] = []
    off = 0
    i = 0
    while i < len(arg):
        c = arg[i]
        if c != "%":
            off += len(c.encode("utf-8", "surrogateescape"))
            i += 1
            continue
        d = arg[i + 1] if i + 1 < len(arg) else ""
        i += 2
        if d == "%":
            off += 1
            continue
        if d not in _TIME_WIDTHS:
            raise FormatError(f"시각 형식에서 지원하지 않는 지시자예요: %{d}")
        if d in _TIME_DATE_KEYS:
            layout.append((d, off, _TIME_WIDTHS[d]))
        off += _TIME_WIDTHS[d]
    keys = {k for k, _, _ in layout}
    full = "Y" in keys and "d" in keys and ("m" in keys or "b" in keys)
    return (tuple(layout) if full else ()), off


_WS_SPLIT_RE = re.compile(r"([ \t]+)")


def _literal_tokens(text: str) -> list[Token]:
    """리터럴을 EXACT(lit)와 space/tab 연속(ws)으로 번갈아 나눈다."""
    return [Token("ws" if i % 2 else "lit", part)
            for i, part in enumerate(_WS_SPLIT_RE.split(text)) if part]


def compile_format(pattern: str) -> FormatSpec:
    """Apache LogFormat 문자열을 토큰 목록으로 바꾼다. 잘못되면 FormatError."""
    tokens: list[Token] = []
    lit: list[str] = []
    i, n = 0, len(pattern)
    while i < n:
        c = pattern[i]
        if c != "%":
            lit.append(c)
            i += 1
            continue
        j = i + 1
        if j < n and pattern[j] == "%":
            lit.append("%")
            i = j + 1
            continue
        if j < n and pattern[j] == ">":
            j += 1
        arg = None
        if j < n and pattern[j] == "{":
            k = pattern.find("}", j + 1)
            if k < 0:
                raise FormatError("닫히지 않은 %{ 가 있어요")
            arg = pattern[j + 1:k]
            j = k + 1
        letter = pattern[j] if j < n else ""  # 지시자 문자 없이 끝나면 알 수 없는 지시자(skip)
        text = pattern[i:j + 1]
        i = j + 1
        if lit:
            tokens += _literal_tokens("".join(lit))
            lit.clear()
        elif tokens and tokens[-1].kind == "field":
            raise FormatError(f"필드 사이에 구분 문자가 필요해요: {tokens[-1].text}{text}")
        if letter == "t" and arg is not None:
            layout, width = _time_layout(arg)
            tokens.append(Token("field", text, "tfmt", layout, width))
        else:
            tokens.append(Token("field", text, letter if letter in _DIRECTIVES else "skip"))
    if lit:
        tokens += _literal_tokens("".join(lit))

    dirs = {t.directive for t in tokens if t.kind == "field"}
    has_request = "r" in dirs or {"m", "U"} <= dirs
    has_time = "t" in dirs or any(t.directive == "tfmt" and t.time_layout for t in tokens)
    if not (has_request and has_time):
        raise FormatError(_ERROR_REQUIRED)
    return FormatSpec(tuple(tokens), "s" in dirs)


@functools.lru_cache(maxsize=256)
def _compiled(pattern: str) -> FormatSpec:
    return compile_format(pattern)


_BUILTIN_SPECS = {name: compile_format(p) for name, p in BUILTIN_FORMATS.items()}


def resolve_format(name: str, user_formats: Sequence[LogFormatDef] = (),
                   custom: CustomFormat | None = None) -> FormatSpec | CustomFormat:
    """형식 이름 -> FormatSpec (기본 제공 먼저, 다음 사용자 형식). "custom"이면 custom 그대로."""
    if name == "custom":
        return custom
    if name in _BUILTIN_SPECS:
        return _BUILTIN_SPECS[name]
    for f in user_formats:
        if f.name == name:
            return _compiled(f.pattern)
    raise FormatError(f"모르는 로그 형식이에요: {name}")


def _tokens(s: str) -> list[str]:
    """awk 기본 분할처럼 space/tab 연속만 구분자로 쓴다."""
    s = s.strip(" \t")
    return _WS_RE.split(s) if s else []


def _clf_date(day: str, mon: str, year: str) -> str | None:
    m = _MONTHS.get(mon)
    return f"{year}{m}{day}" if m else None


def _strip_eol(line: str) -> str:
    if line.endswith("\n"):
        line = line[:-1]
    if line.endswith("\r"):
        line = line[:-1]
    return line


def _tfmt_date(value: str, tok: Token) -> str | None:
    """tfmt 값 검사. 실패면 None, 날짜 없는 형식이면 "" , 아니면 YYYYMMDD."""
    raw = value.encode("utf-8", "surrogateescape")
    if len(raw) != tok.time_width:
        return None
    parts = {}
    for key, off, size in tok.time_layout:
        part = raw[off:off + size]
        if key == "b":
            month = _MONTHS_B.get(part)
            if month is None:
                return None
            parts["m"] = month
        else:
            if not _DIGITS_B_RE.fullmatch(part):
                return None
            parts[key] = part.decode("ascii")
    if not tok.time_layout:
        return ""
    return parts["Y"] + parts["m"] + parts["d"]


def _ws_end(line: str, pos: int) -> int:
    """pos부터 이어지는 space/tab 연속의 끝."""
    while pos < len(line) and line[pos] in " \t":
        pos += 1
    return pos


def _ws_start(line: str, pos: int) -> int:
    """pos부터 처음 나오는 space 또는 tab의 위치. 없으면 줄 끝."""
    while pos < len(line) and line[pos] not in " \t":
        pos += 1
    return pos


def parse_with_spec(line: str, spec: FormatSpec) -> ParsedLine | None:
    """FORMAT PARSE CONTRACT대로 한 줄을 읽는다. 읽을 수 없으면 None."""
    line = _strip_eol(line)
    toks = spec.tokens
    pos = 0
    method = path = date = None
    status = None
    for idx in range(spec.last_essential + 1):  # 마지막 필수 토큰까지만 본다
        tok = toks[idx]
        if tok.kind == "lit":
            if not line.startswith(tok.text, pos):
                return None
            pos += len(tok.text)
            continue
        if tok.kind == "ws":
            end = _ws_end(line, pos)
            if end == pos:
                return None
            pos = end
            continue
        d = tok.directive
        if d == "t":
            if not line.startswith("[", pos):
                return None
            end = line.find("]", pos)
            if end < 0:
                return None
            m = _CLF_TIME_RE.match(line, pos, end + 1)
            date = _clf_date(m.group(1), m.group(2), m.group(3)) if m else None
            if date is None:
                return None
            pos = end + 1
            continue
        nxt = toks[idx + 1] if idx + 1 < len(toks) else None
        if nxt is None:
            end = len(line)
        elif nxt.kind == "ws":
            end = _ws_start(line, pos)
            if end == pos:
                return None
        else:
            end = line.find(nxt.text, pos)
            if end < 0:
                return None
        value = line[pos:end]
        pos = end
        if d == "tfmt":
            got = _tfmt_date(value, tok)
            if got is None:
                return None
            if got:
                date = got
        elif d == "r":
            parts = _tokens(value)
            if len(parts) < 2 or not _METHOD_RE.fullmatch(parts[0]):
                return None
            method, path = parts[0], parts[1]
        elif d == "m":
            if not _METHOD_RE.fullmatch(value):
                return None
            method = value
        elif d == "U":
            path = value
        elif d == "s":
            if not _STATUS_RE.fullmatch(value):
                return None
            status = int(value)
    if method is None or path is None or date is None:
        return None  # compile_format을 거친 형식이면 생기지 않는다
    return ParsedLine(method, path, status, date)


def _parse_custom(line: str, custom: CustomFormat | None) -> ParsedLine | None:
    if custom is None:
        return None
    fields = _tokens(line)

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


def parse_line(line: str, fmt: str, custom: CustomFormat | None = None,
               formats: Sequence[LogFormatDef] = ()) -> ParsedLine | None:
    """한 줄을 읽는다. 읽을 수 없거나 형식을 모르면 None. path는 정규화 전 원문."""
    if fmt == "custom":
        return _parse_custom(_strip_eol(line), custom)
    try:
        spec = resolve_format(fmt, formats)
    except FormatError:
        return None
    return parse_with_spec(line, spec)


def status_class(status: int | None) -> str:
    """"2xx" 같은 상태군. 상태 미상이면 "???"."""
    return "???" if status is None else f"{status:03d}"[0] + "xx"


_UUID_RE = re.compile(
    r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_DIGITS_RE = re.compile(r"[0-9]+")
_HEX16_RE = re.compile(r"[0-9a-fA-F]{16,}")
_NUMLIKE_RE = re.compile(r"[0-9][0-9-]{5,}")
_TOKEN_RE = re.compile(r"[A-Za-z0-9_-]{20,}")
_HAS_DIGIT_RE = re.compile(r"[0-9]")
_HAS_ALPHA_RE = re.compile(r"[A-Za-z]")


def is_variable_segment(seg: str) -> bool:
    """정규화 규칙 5: 이 조각은 {*}로 바뀐다. 매처도 같은 판정을 쓴다."""
    if (_DIGITS_RE.fullmatch(seg) or _UUID_RE.fullmatch(seg)
            or _HEX16_RE.fullmatch(seg) or _NUMLIKE_RE.fullmatch(seg)):
        return True
    if "@" in seg:
        return True
    if "%" in seg and len(seg.encode("utf-8", "surrogateescape")) >= 20:
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
        segs.append("{*}" if is_variable_segment(seg) else seg)
    return "/" + "/".join(segs)


def _sample_result(parsed: ParsedLine | None, error: str | None) -> dict:
    if parsed is None:
        return {"ok": False, "method": None, "path": None, "normalized": None,
                "status": None, "status_class": None, "date": None,
                "error": error or _ERROR_UNREADABLE}
    return {"ok": True, "method": parsed.method, "path": parsed.path,
            "normalized": normalize_path(parsed.path), "status": parsed.status,
            "status_class": status_class(parsed.status), "date": parsed.date,
            "error": None}


def test_sample_line(line: str, fmt: str, custom: CustomFormat | None,
                     formats: Sequence[LogFormatDef] = ()) -> dict:
    """설정 화면의 "샘플 줄로 형식 테스트". 형식 이름을 모르거나 정의가 틀리면 그 오류를 알린다."""
    if fmt != "custom":
        try:
            resolve_format(fmt, formats)
        except FormatError as e:
            return _sample_result(None, str(e))
    return _sample_result(parse_line(line, fmt, custom, formats), None)


def test_format(pattern: str, line: str) -> dict:
    """형식 패턴을 샘플 줄로 시험한다. 컴파일 오류도 ok=False + error로 돌려준다."""
    try:
        spec = compile_format(pattern)
    except FormatError as e:
        return _sample_result(None, str(e))
    return _sample_result(parse_with_spec(line, spec), None)
