"""tools/scrub.py 테스트. 실제 사내 정보는 쓰지 않고 가짜 치환표로만 확인한다."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from scrub import find_forbidden, load_map, scrub_tree  # noqa: E402

REPL = [("https://mail.real.example", "https://internal.mailapi.com"),
        ("sec@real.example", "security@foo.bar"),
        ("/data/websrv/AAA/", "/data/webserver/APP/"),
        ("/data/websrv/", "/data/webserver/")]
FORBID = ["real.example", "/AAA/", "/data/websrv"]


def test_replaces_in_order_and_keeps_crlf(tmp_path):
    f = tmp_path / "a.py"
    f.write_bytes(b'U="https://mail.real.example"\r\nT="sec@real.example"\r\n'
                  b'P="/data/websrv/AAA/log/*.log*"\r\nR="/data/websrv/log"\r\nF="webtob_default"\r\n')
    changed, unreadable = scrub_tree(tmp_path, REPL)
    assert changed == [f] and unreadable == []
    assert f.read_bytes() == (b'U="https://internal.mailapi.com"\r\nT="security@foo.bar"\r\n'
                              b'P="/data/webserver/APP/log/*.log*"\r\nR="/data/webserver/log"\r\nF="webtob_default"\r\n')


def test_reports_unreadable_and_skips_binary(tmp_path):
    (tmp_path / "cp949.md").write_bytes("보안팀 real.example".encode("cp949"))
    (tmp_path / "img.png").write_bytes(b"\x89PNG\r\n\x1a\nreal.example")
    changed, unreadable = scrub_tree(tmp_path, REPL)
    assert unreadable == [tmp_path / "cp949.md"] and changed == []


def test_find_forbidden_is_case_insensitive(tmp_path):
    f = tmp_path / "x.json"
    f.write_text('{"a": 1}\n{"to": "a@REAL.Example"}', encoding="utf-8")
    assert find_forbidden([f], FORBID) == [(f, 2, "real.example")]


def test_load_map(tmp_path):
    m = tmp_path / "m.json"
    m.write_text('{"replacements": [["a", "b"]], "forbidden": ["a"]}', encoding="utf-8")
    assert load_map(m) == ([("a", "b")], ["a"])
