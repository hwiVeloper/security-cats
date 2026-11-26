import os

from conftest import FIX
from iptrace.core import ioc


def parse_fixture(cfg):
    text = ioc.read_file(os.path.join(FIX, "ioc_typed.list"))
    return ioc.parse_text(text, cfg["general"]["own_networks"])


def test_fixture_matches_plan_11_1(cfg):
    res = parse_fixture(cfg)
    assert [e.key for e in res.entries] == [
        "203.0.113.45", "198.51.100.7", "192.0.2.10", "203.0.113.50", "198.51.100.200", "203.0.113.0/28"]
    assert res.duplicates == 1
    assert [(v, "자사" in why) for _, v, why in res.excluded] == [("198.51.100.80", True)]
    assert res.error_lines == [9]
    assert ioc.summary_text(res) == "6개 IP · 중복 1 · 제외 1 · 오류 1"


def test_documentation_ranges_are_not_treated_as_private():
    res = ioc.parse_text("192.0.2.1\n198.51.100.2\n203.0.113.3")
    assert len(res.entries) == 3 and not res.excluded


def test_private_and_special_ranges_are_excluded():
    res = ioc.parse_text("10.0.0.5\n127.0.0.1\n172.16.3.4\n192.168.0.1\n::1\n8.8.8.8")
    assert [e.key for e in res.entries] == ["8.8.8.8"]
    assert len(res.excluded) == 5
    assert len(ioc.parse_text("10.0.0.5", include_private=True).entries) == 1


def test_token_forms():
    text = "\n".join([
        "hxxp://203.0.113.1:8080/path",
        "203.0.113[.]2",
        "203.0.113.3:443",
        "203.0.113.10-203.0.113.11",
        "2001:db8::1",
        "[2001:db8::2]:443",
        "203.0.113.4;203.0.113.5 | 203.0.113.6",
        "메모 203.0.113.7",
    ])
    res = ioc.parse_text(text)
    keys = [e.key for e in res.entries]
    assert keys == ["203.0.113.1", "203.0.113.2", "203.0.113.3", "203.0.113.10/31", "2001:db8::1",
                    "2001:db8::2", "203.0.113.4", "203.0.113.5", "203.0.113.6", "203.0.113.7"]
    assert not res.errors


def test_typos_are_errors_with_line_numbers():
    res = ioc.parse_text("203.0.113.1\n203.0.113\n203.0.113.256\n1.2.3.4.5\n203.0.113.9-203.0.113.1")
    assert res.error_lines == [2, 3, 4, 5]
    assert [e.key for e in res.entries] == ["203.0.113.1"]


def test_comments_and_blank_lines_are_ignored():
    res = ioc.parse_text("# 메모\n\n   \n203.0.113.1  # 10/21 메모는 무시\n")
    assert [e.key for e in res.entries] == ["203.0.113.1"]
    assert not res.errors


def test_wide_range_warning():
    res = ioc.parse_text("1.0.0.0/4")
    assert res.warnings


def test_broad_range_warns_about_many_lines():
    """/16보다 넓은 대역은 서버에서 관련 없는 줄도 많이 걸리므로 경고한다."""
    from iptrace.core import ioc as iocmod
    assert any("넓은 대역" in w for w in iocmod.parse_text("1.0.0.0/8").warnings)
    assert any("넓은 대역" in w for w in iocmod.parse_text("203.0.0.0/12").warnings)
    assert not iocmod.parse_text("203.0.0.0/16").warnings
    assert not iocmod.parse_text("203.0.113.45").warnings
