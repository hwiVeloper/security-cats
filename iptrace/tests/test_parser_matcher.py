import ipaddress

import pytest

from conftest import XFF_FORMAT
from iptrace.core.matcher import Matcher
from iptrace.core.models import IocEntry
from iptrace.core.parser import LogFormatError, LogParser


def test_parse_combined_with_xff():
    p = LogParser(XFF_FORMAT)
    r = p.parse('10.1.1.10 - - [04/Oct/2026:11:00:00 +0900] "GET /main.do?a=1&b=2 HTTP/1.1" 200 3000 '
                '"http://x/" "Mozilla/5.0 (X)" "203.0.113.9, 10.1.1.10"', "web01", "f", 7)
    assert (r.remote_ip, r.method, r.path, r.query, r.status, r.bytes) == ("10.1.1.10", "GET", "/main.do", "a=1&b=2", 200, 3000)
    assert r.xff == ["203.0.113.9", "10.1.1.10"]
    assert r.user_agent == "Mozilla/5.0 (X)" and r.referer == "http://x/"
    assert r.time.isoformat() == "2026-10-04T11:00:00+09:00"
    assert (r.server, r.file, r.line_no) == ("web01", "f", 7)


def test_parse_handles_escaped_quotes_dash_bytes_and_extra_spaces():
    p = LogParser("COMBINED")
    r = p.parse('203.0.113.1 - -  [04/Oct/2026:11:00:00 +0900] "GET /a?q=\\"x\\" HTTP/1.1" 304 - "-" "UA"')
    assert r.status == 304 and r.bytes == 0 and r.query == 'q="x"'


def test_broken_line_returns_none():
    assert LogParser(XFF_FORMAT).parse("this is a broken line") is None


def test_format_without_host_is_rejected():
    with pytest.raises(LogFormatError):
        LogParser('%t "%r" %s')


def _ioc(s):
    return IocEntry(network=ipaddress.ip_network(s, strict=False), original=s, line_no=1)


def test_matcher_single_range_trusted():
    m = Matcher([_ioc("203.0.113.45"), _ioc("203.0.113.0/28"), _ioc("2001:db8::/32")], ["10.1.1.10"])
    assert m.match_ip("203.0.113.45") == ("203.0.113.45",)
    assert m.match_ip("203.0.113.9") == ("203.0.113.0/28",)
    assert m.match_ip("203.0.113.16") == ()
    assert m.match_ip("2001:db8::5") == ("2001:db8::/32",)
    assert m.match_ip("10.1.1.10") == ()
    assert m.match_ip("not-an-ip") == ()


def test_matcher_record_uses_xff_and_marks_field():
    p = LogParser(XFF_FORMAT)
    r = p.parse('10.1.1.10 - - [04/Oct/2026:11:00:00 +0900] "GET / HTTP/1.1" 200 1 "-" "UA" "203.0.113.9, 10.1.1.10"')
    m = Matcher([_ioc("203.0.113.0/28")], ["10.1.1.10"])
    assert m.match_record(r) == [("203.0.113.0/28", "203.0.113.9", "xff")]


# ---------------------------------------------------------------- WAS·업무 로그 형식 (%{...}t, %*, %a %m %U %q %H)
@pytest.mark.parametrize("fmt,line,expected", [
    ("%{%Y-%m-%d %H:%M:%S,%f}t %* %h %m %U %s%*",
     "2026-10-21 13:00:00,123 INFO [http-nio-8080-exec-1] 203.0.113.45 GET /board/view.jsp?id=1 500 12ms",
     ("203.0.113.45", "2026-10-21T13:00:00.123000+09:00", "GET", "/board/view.jsp", "id=1", 500)),
    ("[%{%Y-%m-%d %H:%M:%S}t] %* ip=%h%*",
     "[2026-10-21 13:00:00] [WARN] login fail user=admin ip=198.51.100.7, agent=curl",
     ("198.51.100.7", "2026-10-21T13:00:00+09:00", "", "", "", None)),
    ("%{%Y-%m-%dT%H:%M:%S%z}t %a %m %U %q %H %s",
     "2026-10-21T13:00:00+00:00 203.0.113.45 POST /a ?x=1 HTTP/1.1 404",
     ("203.0.113.45", "2026-10-21T13:00:00+00:00", "POST", "/a", "x=1", 404)),
    ("%{begin:%d/%b/%Y %H:%M:%S}t %h %*",
     "21/Oct/2026 13:00:00 203.0.113.45 anything here",
     ("203.0.113.45", "2026-10-21T13:00:00+09:00", "", "", "", None)),
])
def test_custom_time_and_any_text(fmt, line, expected):
    r = LogParser(fmt).parse(line)
    assert r is not None
    assert (r.remote_ip, r.time.isoformat(), r.method, r.path, r.query, r.status) == expected


def test_format_errors_are_clear():
    with pytest.raises(LogFormatError, match="IP 자리"):
        LogParser("%{%Y-%m-%d}t %m %U")
    with pytest.raises(LogFormatError, match="%Q"):
        LogParser("%{%Q}t %h")


def test_examples_parse_their_own_sample():
    """형식 편집 창의 [예시 넣기] 형식은 설명에 적힌 샘플 줄을 읽을 수 있어야 한다."""
    from iptrace.core.parser import EXAMPLES
    for key, desc, fmt in EXAMPLES:
        if ": " in desc:
            sample = desc.split(": ", 1)[1].replace("[..]", "[main]")
            r = LogParser(fmt).parse(sample)
            assert r and r.remote_ip == "1.2.3.4" and r.time is not None, (key, sample)
