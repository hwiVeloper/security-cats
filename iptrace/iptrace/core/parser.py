"""로그 파서.

Format 문자열(Apache 스타일 % 지시자)로 정규식을 만들어 한 줄씩 해석한다.
웹서버(WebtoB http.m의 Format), WAS 접근 로그(Tomcat·JEUS pattern)는 그대로 옮겨 적고,
그 밖의 로그(WAS·애플리케이션 로그)는 %{시간형식}t, %*(아무 글자)로 한 줄 모양을 적는다.

지시자 (FORMAT_HELP와 같은 내용)
  %h, %a      IP (둘 중 하나는 꼭 있어야 한다)
  %t          [21/Oct/2026:13:00:00 +0900] 모양의 시간
  %{...}t     시간 모양 직접 지정 (strftime). 예: %{%Y-%m-%d %H:%M:%S}t, %{%Y-%m-%d %H:%M:%S,%f}t
  %r          요청 줄 "GET /a.jsp?x=1 HTTP/1.1"
  %m %U %q %H 메서드, 경로, 쿼리(?x=1), 프로토콜 (요청 줄이 나뉘어 있을 때)
  %s %b       응답 코드, 크기
  %{이름}i    요청 헤더 값 (예: %{User-Agent}i, %{X-Forwarded-For}i)
  %*          아무 글자 (메시지처럼 줄마다 다른 부분)
  그 밖의 %x  공백 없는 값 하나 (읽고 버린다)
"""
import ipaddress
import re
from datetime import datetime, timedelta, timezone

from .models import LogRecord

# WebtoB 프리셋 이름. 실제 정의는 http.m 확인 후 보완한다(PLAN.md 2.1).
PRESETS = {
    "DEFAULT": '%h %l %u %t "%r" %s %b',
    "COMMON": '%h %l %u %t "%r" %s %b',
    "COMBINED": '%h %l %u %t "%r" %s %b "%{Referer}i" "%{User-Agent}i"',
}

_QUOTED = r'(?:[^"\\]|\\.)*'
_TOKEN = re.compile(r"%(?:\{([^}]+)\})?[<>]?([a-zA-Z%*])")
_MONTHS = {m: i for i, m in enumerate(
    ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"), 1)}
_TIME = re.compile(r"(\d{2})/(\w{3})/(\d{4}):(\d{2}):(\d{2}):(\d{2})\s*([+-]\d{4})?")


# %{...}t 안의 strftime 지시자 -> 정규식
_STRF = {"Y": r"\d{4}", "y": r"\d{2}", "m": r"\d{1,2}", "d": r"\d{1,2}", "H": r"\d{1,2}", "I": r"\d{1,2}",
         "M": r"\d{2}", "S": r"\d{2}", "f": r"\d{1,6}", "b": r"[A-Za-z]{3}", "h": r"[A-Za-z]{3}", "B": r"[A-Za-z]+",
         "a": r"[A-Za-z]{3}", "A": r"[A-Za-z]+", "p": r"[AaPp][Mm]", "z": r"(?:[+-]\d{2}:?\d{2}|Z)", "Z": r"[A-Za-z]+",
         "j": r"\d{3}", "%": "%"}

# 형식 편집 창에 보여 주는 지시자 안내 (지시자, 설명)
FORMAT_HELP = (
    ("%h  %a", "IP (둘 중 하나는 꼭 필요)"),
    ("%t", "[21/Oct/2026:13:00:00 +0900] 모양의 시간"),
    ("%{...}t", "시간 모양 직접 지정  예) %{%Y-%m-%d %H:%M:%S}t   %{%Y-%m-%d %H:%M:%S,%f}t"),
    ("%r", '요청 줄  "GET /a.jsp?x=1 HTTP/1.1"'),
    ("%m %U %q %H", "메서드·경로·쿼리·프로토콜 (요청 줄이 나뉘어 있을 때)"),
    ("%s  %b", "응답 코드·크기"),
    ("%{이름}i", "요청 헤더 값  예) %{User-Agent}i  %{X-Forwarded-For}i"),
    ("%*", "아무 글자 (메시지처럼 줄마다 다른 부분)"),
    ("그 밖의 %x", "공백 없는 값 하나 (읽고 버림)"),
)

# 자주 쓰는 형식 예시 (형식 편집 창의 [예시] 버튼)
# (이름, 설명, Format)
EXAMPLES = (
    ("access_combined", "웹·WAS 접근 로그 (WebtoB·Apache·Tomcat·JEUS)",
     '%h %l %u %t "%r" %s %b "%{Referer}i" "%{User-Agent}i"'),
    ("was_log", "WAS 로그: 2026-10-21 13:00:00,123 INFO [..] 1.2.3.4 GET /a.jsp 200",
     "%{%Y-%m-%d %H:%M:%S,%f}t %* %h %m %U %s%*"),
    ("app_log", "업무 로그: [2026-10-21 13:00:00] [INFO] login ok user=kim ip=1.2.3.4",
     "[%{%Y-%m-%d %H:%M:%S}t] %* ip=%h%*"),
    ("webtob_error", "WebtoB 에러 로그: [2026-10-23T02:22:37] [CLIENT(1.2.3.4)] [E] [ERR-00045] ... GET /a HTTP/1.1",
     "[%{%Y-%m-%dT%H:%M:%S}t] [CLIENT(%h)] %*"),
)

# 요청 칸이 없는 로그(에러·WAS 로그)에서도 'GET /path HTTP/1.1' 모양이 있으면 메서드·경로를 읽는다 (공격 패턴 확인용)
_REQUEST_IN_LINE = re.compile(r"\b(GET|POST|PUT|DELETE|HEAD|OPTIONS|PATCH|CONNECT|TRACE|PROPFIND|MKCOL|COPY|MOVE)\s+"
                              r"(\S+)\s+(HTTP/\d(?:\.\d)?)")


def _time_regex(fmt: str) -> str:
    """strftime 형식(예: '%Y-%m-%d %H:%M:%S')을 정규식으로 바꾼다."""
    out, i = [], 0
    while i < len(fmt):
        c = fmt[i]
        if c == "%" and i + 1 < len(fmt):
            code = fmt[i + 1]
            if code not in _STRF:
                raise LogFormatError(f"시간 형식의 %{code}는 지원하지 않습니다")
            out.append(_STRF[code])
            i += 2
        else:
            out.append(r"\s+" if c == " " else re.escape(c))
            i += 1
    return "".join(out)


def _literal(text: str) -> str:
    """Format 문자열의 고정 글자. 공백은 1칸 이상으로 허용한다."""
    return r"\s+".join(re.escape(p) for p in text.split(" "))


def _group_name(header: str) -> str:
    return "h_" + re.sub(r"[^a-z0-9]", "_", header.lower())


class LogFormatError(ValueError):
    pass


class LogParser:
    def __init__(self, fmt: str, default_tz=None):
        fmt = PRESETS.get(fmt.upper(), fmt) if "%" not in fmt else fmt
        self.format = fmt
        self.default_tz = default_tz or timezone(timedelta(hours=9))
        self.time_format = None     # %{...}t로 지정한 시간 모양 (없으면 [dd/Mon/yyyy:HH:MM:SS +zzzz])
        self.regex = re.compile(self._build(fmt))
        self.has_xff = "h_x_forwarded_for" in self.regex.groupindex

    def _build(self, fmt: str) -> str:
        out, pos, used = ["^"], 0, set()
        for m in _TOKEN.finditer(fmt):
            out.append(_literal(fmt[pos:m.start()]))
            name, code = m.group(1), m.group(2)
            if code == "%":
                out.append("%")
            elif code == "*":
                out.append(r".*?")
            elif code in ("h", "a"):
                # IP 글자(숫자·.·:)를 먼저 잡고, 안 되면 공백 없는 값 하나 ('ip=1.2.3.4,'에서 쉼표를 빼기 위해)
                out.append(r"(?P<remote_ip>[0-9A-Fa-f:.]+|\S+)" if "ip" not in used else r"\S+")
                used.add("ip")
            elif code in ("l", "u"):
                g = "ident" if code == "l" else "user"
                out.append(rf"(?P<{g}>\S+)" if g not in used else r"\S+")
                used.add(g)
            elif code == "t" and name and "time" not in used:
                # Apache의 %{begin:...}t / %{end:...}t도 받는다
                self.time_format = re.sub(r"^(begin|end):", "", name)
                out.append(rf"(?P<time>{_time_regex(self.time_format)})")
                used.add("time")
            elif code == "t" and "time" not in used:
                out.append(r"\[(?P<time>[^\]]+)\]")
                used.add("time")
            elif code == "t":
                out.append(r"\[[^\]]+\]" if not name else r"\S+")
            elif code in ("m", "U", "q", "H") and not name:
                g = {"m": "method", "U": "urlpath", "q": "qs", "H": "proto"}[code]
                pat = r"\S*" if code == "q" else r"\S+"
                out.append(rf"(?P<{g}>{pat})" if g not in used else pat)
                used.add(g)
            elif code == "r":
                out.append(rf"(?P<request>{_QUOTED})")
            elif code == "s":
                out.append(r"(?P<status>\d{3}|-)")
            elif code in ("b", "B"):
                out.append(r"(?P<bytes>\d+|-)")
            elif code in ("D", "T"):
                out.append(r"(?P<rt>\d+|-)" if "rt" not in used else r"\S+")
                used.add("rt")
            elif code == "i" and name:
                g = _group_name(name)
                out.append(rf"(?P<{g}>{_QUOTED})" if g not in used else _QUOTED)
                used.add(g)
            else:
                out.append(r"\S+")
            pos = m.end()
        out.append(_literal(fmt[pos:]))
        out.append(r"\s*$")
        pattern = "".join(out)
        if "(?P<remote_ip>" not in pattern:
            raise LogFormatError("로그 형식에 IP 자리(%h 또는 %a)가 없습니다")
        return pattern

    def parse_time(self, s: str):
        if self.time_format:
            try:
                t = datetime.strptime(" ".join(s.split()), self.time_format)
            except ValueError:
                return None
            return t if t.tzinfo else t.replace(tzinfo=self.default_tz)
        m = _TIME.match(s)
        if not m:
            return None
        d, mon, y, hh, mm, ss, tz = m.groups()
        month = _MONTHS.get(mon)
        if not month:
            return None
        if tz:
            sign = 1 if tz[0] == "+" else -1
            tzinfo = timezone(sign * timedelta(hours=int(tz[1:3]), minutes=int(tz[3:5])))
        else:
            tzinfo = self.default_tz
        return datetime(int(y), month, int(d), int(hh), int(mm), int(ss), tzinfo=tzinfo)

    def match(self, line: str):
        return self.regex.match(line)

    def to_record(self, m, server: str, file: str, line_no: int, line: str) -> LogRecord:
        g = m.groupdict()
        req = (g.get("request") or "").replace('\\"', '"')
        parts = req.split(" ")
        if "request" not in g:
            # 요청 줄이 따로 없는 로그: %m %U %q %H로 나뉜 값을 쓴다 (없으면 빈 값)
            method, target, protocol = g.get("method") or "", g.get("urlpath") or "", g.get("proto") or ""
            if g.get("qs") and g["qs"] not in ("-", "?"):
                target = target + ("" if g["qs"].startswith("?") else "?") + g["qs"]
            if not method and not target:
                rq = _REQUEST_IN_LINE.search(line)
                if rq:
                    method, target, protocol = rq.groups()
        elif len(parts) >= 3:
            method, target, protocol = parts[0], " ".join(parts[1:-1]), parts[-1]
        elif len(parts) == 2:
            method, target, protocol = parts[0], parts[1], ""
        else:
            method, target, protocol = "", req, ""
        path, _, query = target.partition("?")
        status = g.get("status")
        size = g.get("bytes")
        xff_raw = g.get("h_x_forwarded_for") or ""
        xff = [x.strip() for x in xff_raw.split(",") if x.strip() and x.strip() != "-"]
        return LogRecord(
            server=server, file=file, line_no=line_no, raw=line,
            remote_ip=g.get("remote_ip", ""), xff=xff,
            time=self.parse_time(g["time"]) if g.get("time") else None,
            method=method, path=path, query=query, protocol=protocol,
            status=int(status) if status and status.isdigit() else None,
            bytes=int(size) if size and size.isdigit() else 0,
            referer=g.get("h_referer") or "", user_agent=g.get("h_user_agent") or "",
        )

    def parse(self, line: str, server: str = "", file: str = "", line_no: int = 0):
        m = self.regex.match(line)
        return self.to_record(m, server, file, line_no, line) if m else None


# ---------------------------------------------------------------- 형식과 맞지 않는 줄 (해석 실패)
# 형식으로 읽지 못한 줄에서도 IP와 시간을 찾아 '해석 실패' 기록으로 결과에 넣는다.
_IPV4 = re.compile(r"(?<![\d.])(?:\d{1,3}\.){3}\d{1,3}(?![\d.])")
_IPV6 = re.compile(r"(?<![0-9A-Fa-f:])(?:[0-9A-Fa-f]{0,4}:){2,7}[0-9A-Fa-f]{0,4}(?![0-9A-Fa-f:])")
_GUESS_TIME = (
    (re.compile(r"(\d{2}/[A-Za-z]{3}/\d{4}:\d{2}:\d{2}:\d{2})(?:\s*([+-]\d{4}))?"), "%d/%b/%Y:%H:%M:%S"),
    (re.compile(r"(\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}:\d{2})(?:[.,]\d+)?\s*(Z|[+-]\d{2}:?\d{2})?"), "%Y-%m-%d %H:%M:%S"),
    (re.compile(r"(\d{4}/\d{2}/\d{2} \d{2}:\d{2}:\d{2})()"), "%Y/%m/%d %H:%M:%S"),
)


def extract_ips(line: str) -> list:
    """줄 안의 IP 모양 글자를 모두 찾는다(검증은 매칭할 때). 1.2.3.4가 11.2.3.45에 걸리지 않게 경계를 본다."""
    out = []
    for x in _IPV4.findall(line) + [y for y in _IPV6.findall(line) if y.count(":") >= 2]:
        try:
            ipaddress.ip_address(x)     # '13:00:00' 같은 시간 글자는 빼고 실제 IP만
        except ValueError:
            continue
        out.append(x)
    return out


def guess_time(line: str, default_tz=None):
    """흔한 시간 모양([21/Oct/2026:13:00:00 +0900], 2026-10-21 13:00:00, 2026/10/21 13:00:00)을 찾는다. 없으면 None."""
    tz0 = default_tz or timezone(timedelta(hours=9))
    for rx, fmt in _GUESS_TIME:
        m = rx.search(line)
        if not m:
            continue
        try:
            t = datetime.strptime(m.group(1).replace("T", " "), fmt)
        except ValueError:
            continue
        z = (m.group(2) or "").replace(":", "")
        if z == "Z":
            return t.replace(tzinfo=timezone.utc)
        if z:
            sign = 1 if z[0] == "+" else -1
            return t.replace(tzinfo=timezone(sign * timedelta(hours=int(z[1:3]), minutes=int(z[3:5]))))
        return t.replace(tzinfo=tz0)
    return None


def unparsed_record(line: str, server: str, file: str, line_no: int, ip: str, time) -> LogRecord:
    """형식으로 읽지 못한 줄을 '해석 실패' 기록으로 만든다 (요청·응답 칸은 비어 있다)."""
    return LogRecord(server=server, file=file, line_no=line_no, raw=line, remote_ip=ip, xff=[], time=time,
                     method="", path="", query="", protocol="", status=None, bytes=0, referer="", user_agent="",
                     tags=["UNPARSED"])


XFF_SUFFIX = ' "%{X-Forwarded-For}i"'


def detect_format(lines, cfg: dict) -> list:
    """샘플 줄로 형식 후보를 시험해 [(이름, 맞은 줄 수, 전체 줄 수)]를 잘 맞는 순서로 돌려준다.

    후보: 설정의 log_formats, 기본 프리셋, 그리고 각각에 X-Forwarded-For를 붙인 형식.
    """
    lines = [x for x in lines if x.strip()]
    cands = dict(cfg.get("log_formats", {}))
    for k, v in PRESETS.items():
        cands.setdefault(k, v)
    for k, _, v in EXAMPLES:
        cands.setdefault(k, v)
    for k, v in list(cands.items()):
        if "X-Forwarded-For" not in v and '"%r"' in v:   # 접근 로그 모양에만 XFF를 붙여 본다
            cands.setdefault(f"{k}+xff", v + XFF_SUFFIX)
    out = []
    for name, fmt in cands.items():
        try:
            p = LogParser(fmt)
        except LogFormatError:
            continue
        ok = sum(1 for x in lines if p.match(x))
        out.append((name, fmt, ok, len(lines)))
    # 맞은 줄이 많고, 같으면 필드가 많은(더 자세한) 형식을 앞에
    out.sort(key=lambda x: (-x[2], -x[1].count("%")))
    return out


def resolve_format(cfg: dict, name: str) -> str:
    """설정의 형식 이름을 Format 문자열로 바꾼다. 이미 Format 문자열이면 그대로."""
    if "%" in name:
        return name
    if name in cfg.get("log_formats", {}):
        return cfg["log_formats"][name]
    if name.upper() in PRESETS:
        return PRESETS[name.upper()]
    raise LogFormatError(f"로그 형식 '{name}'을 찾을 수 없습니다")
