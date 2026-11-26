"""tools/scrub.py 테스트. 실제 사내 정보는 쓰지 않고 가짜 치환표로만 확인한다."""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import json  # noqa: E402
import subprocess  # noqa: E402

from scrub import find_forbidden, load_map, main, scrub_tree  # noqa: E402

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


def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


def _repo(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@example.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "config", "core.autocrlf", "false")
    m = tmp_path / "map.json"
    m.write_text(json.dumps({"replacements": [list(r) for r in REPL], "forbidden": FORBID}), encoding="utf-8")
    return repo, m


def _commit(repo, msg):
    _git(repo, "add", "-A")
    _git(repo, "commit", "-q", "-m", msg)


def test_check_finds_terms_everywhere_without_printing_them(tmp_path, monkeypatch, capsys):
    repo, m = _repo(tmp_path)
    (repo / "문서_가.md").write_text("주소 sec@REAL.example\n", encoding="utf-8")
    (repo / "old.py").write_text('P="/data/websrv/x"\n', encoding="utf-8")
    _commit(repo, "first real.example")
    (repo / "old.py").unlink()
    _commit(repo, "remove old")
    (repo / "new.py").write_text('Q="/AAA/"\n', encoding="utf-8")  # 추적 안 함, 무시 안 됨
    monkeypatch.chdir(repo)
    assert main(["--map", str(m), "--check"]) == 1
    out = capsys.readouterr().out
    assert "문서_가.md:1 금지어 #1" in out
    assert "new.py:1 금지어 #2" in out
    assert "커밋 메시지" in out and "#1" in out
    assert "이력 old.py" in out and "#3" in out
    assert "real.example" not in out.lower() and "/data/websrv" not in out and "/AAA/" not in out


def test_check_clean_from_subfolder(tmp_path, monkeypatch, capsys):
    repo, m = _repo(tmp_path)
    (repo / "sub").mkdir()
    (repo / "sub" / "a.py").write_text("x = 1\n", encoding="utf-8")
    (repo / "b.md").write_text("ok\n", encoding="utf-8")
    _commit(repo, "init")
    monkeypatch.chdir(repo / "sub")
    assert main(["--map", str(m), "--check"]) == 0
    assert capsys.readouterr().out.strip().endswith("clean")
