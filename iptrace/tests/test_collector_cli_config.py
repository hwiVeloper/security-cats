import os

import pytest

from conftest import FIX
from iptrace import cli
from iptrace.core import config as cfgmod
from iptrace.core.collector import build_command, check_allowed, split_glob
from iptrace.core.engine import KST
from datetime import datetime


def test_build_command_quotes_and_lowers_priority():
    cmd = build_command("list", path="/data/webserver/APP/log/*_ssl/*.log*", since=datetime(2026, 10, 1, tzinfo=KST))
    assert cmd.startswith("nice -n 19 ionice -c3 find /data/webserver/APP/log -mindepth 2 -maxdepth 2 -type f "
                          "-path '/data/webserver/APP/log/*_ssl/*.log*'")
    assert "-newermt '2026-10-01 00:00:00'" in cmd
    evil = build_command("grep", path="/x/a.log; rm -rf /", patterns=["203.0.113.1", "-e x"])
    assert evil == ("nice -n 19 ionice -c3 zcat -f -- '/x/a.log; rm -rf /' | nice -n 19 grep -a -n -F "
                    "-e 203.0.113.1 -e '-e x'")
    assert build_command("head", path="/a", lines=999, use_ionice=False) == "nice -n 19 zcat -f -- /a | head -n 20"


@pytest.mark.parametrize("path,expected", [
    ("/data/webserver/APP/log/*_ssl/*.log", ("/data/webserver/APP/log", 2)),
    ("/webtob/log/access*.log*", ("/webtob/log", 1)),
    ("/webtob/log/access.log", ("/webtob/log", 1)),
    ("/home/*/webtob/log/*.log", ("/home", 4)),
])
def test_split_glob(path, expected):
    assert split_glob(path) == expected


def test_list_command_is_allowed():
    check_allowed(build_command("list", path="/data/webserver/APP/log/*_ssl/*.log*"))


def test_server_timeout_and_line_limit():
    """무거운 명령은 서버 쪽 timeout으로 감싸고, grep은 -m으로 받을 줄 수를 제한한다."""
    cmd = build_command("grep", path="/a.log.gz", patterns=["1."], timeout=1800, max_count=100000)
    assert cmd == ("timeout -k 10 1800 nice -n 19 ionice -c3 zcat -f -- /a.log.gz | nice -n 19 grep -a -n -F "
                   "-m 100000 -e 1.")
    assert build_command("hash", path="/a", timeout=60).startswith("timeout -k 10 60 nice -n 19 ionice -c3 sha256sum")
    check_allowed("timeout 5 nice -n 19 find /a -type f")


@pytest.mark.parametrize("cmd", ["timeout 10 rm -rf /", "timeout 10", "timeout abc zcat /a", "timeout -k 5 10 sh -c x",
                                 "timeout 10 timeout 10 bash"])
def test_timeout_wrapper_rejects(cmd):
    with pytest.raises(ValueError):
        check_allowed(cmd)


def test_config_v1_turns_evidence_hash_off():
    """v1 설정은 화면에서 바꿀 수 없던 증적 해시가 켜진 채 저장돼 있으므로 새 기본값(꺼짐)으로 바꾼다."""
    c = cfgmod.normalize({"version": 1, "ssh": {"evidence_hash": True}})
    assert c["ssh"]["evidence_hash"] is False and c["version"] == cfgmod.CONFIG_VERSION
    assert c["ssh"]["max_lines_per_file"] == 100000 and c["ssh"]["server_timeout"] == 180
    kept = cfgmod.normalize({"version": 2, "ssh": {"evidence_hash": True}})
    assert kept["ssh"]["evidence_hash"] is True


def test_config_old_defaults_follow_new_limits():
    """예전 기본값(30일, 서버 작업 1800초)은 새 기본값(7일, 180초)으로. 직접 바꾼 시간 제한은 그대로."""
    c = cfgmod.normalize({"general": {"default_period": "30"}, "ssh": {"server_timeout": 1800}})
    assert c["general"]["default_period"] == "7" and c["ssh"]["server_timeout"] == 180 and not cfgmod.validate(c)
    c = cfgmod.normalize({"ssh": {"server_timeout": 600}})
    assert c["ssh"]["server_timeout"] == 600 and c["general"]["default_period"] == "7"


def test_cli_server_check_is_limited_to_7_days(cfg_file, capsys):
    assert cli.main(["-c", cfg_file, "check", "--ip", "8.8.8.8", "--days", "30"]) == cli.EXIT_ERROR
    assert "최대 7일" in capsys.readouterr().err
    bad = cfgmod.normalize({"ssh": {"max_lines_per_file": 0, "server_timeout": "x"}})
    errs = " ".join(cfgmod.validate(bad))
    assert "max_lines_per_file" in errs and "server_timeout" in errs


def test_config_migrates_old_log_dir():
    c = cfgmod.normalize({"servers": [{"name": "a", "host": "h", "log_dir": "/webtob/log/",
                                       "file_pattern": "access*.log*"}]})
    s = c["servers"][0]
    assert s["log_paths"] == ["/webtob/log/access*.log*"]
    assert "log_dir" not in s and "file_pattern" not in s


def test_config_validate_log_paths():
    def errs(paths):
        return " ".join(cfgmod.validate(cfgmod.normalize({"servers": [{"name": "a", "host": "h", "log_paths": paths}]})))
    assert errs(["/data/webserver/APP/log/*_ssl/*.log*"]) == ""
    assert "로그 경로" in errs([])
    assert "절대 경로" in errs(["webtob/log/*.log"])
    assert errs(["/*/log/*.log"]) and errs(["/webtob/../etc/*"]) and errs(["/webtob/log/"])


def test_password_key_is_per_account():
    """같은 서버라도 계정(api, fe)이 다르면 비밀번호를 따로 기억한다."""
    cfg = cfgmod.normalize({"ssh": {"user": "logviewer"}})
    api = {"name": "web01-api", "host": "10.2.0.11", "port": 22, "user": "api"}
    fe = dict(api, name="web01-fe", user="fe")
    other = dict(api, name="web02", host="10.2.0.12", user="")
    assert cfgmod.password_key(cfg, api) == "api@10.2.0.11:22"
    assert cfgmod.password_key(cfg, fe) != cfgmod.password_key(cfg, api)
    assert cfgmod.password_key(cfg, other) == "logviewer@10.2.0.12:22"


def test_full_log_download_command_does_not_exist():
    """로그 전체를 PC로 받는 명령은 만들 수 없다 (서버에서 걸러 받는 것만 허용)."""
    with pytest.raises(ValueError):
        build_command("read", path="/a")
    with pytest.raises(ValueError):
        build_command("grep", path="/a", patterns=[])
    with pytest.raises(ValueError):
        check_allowed("cat /a")


@pytest.mark.parametrize("cmd", [
    "rm -rf /", "find / -delete", "find / -exec sh {} ;", "zcat /a ; rm /b", "zcat /a | sh", "bash -c x",
    "nice -n 19 python x", "zcat /a > /b", "zcat /a | grep x | head", "grep x /a | head", "zcat $(id)",
    "zcat `id`", "zcat /a && rm /b",
])
def test_whitelist_rejects(cmd):
    with pytest.raises(ValueError):
        check_allowed(cmd)


def test_unknown_command_kind():
    with pytest.raises(ValueError):
        build_command("delete", path="/a")


def test_config_validate():
    c = cfgmod.normalize({"servers": [{"name": "a", "host": "", "port": 99999, "log_dir": "logs"},
                                      {"name": "a", "host": "h"}],
                          "general": {"own_networks": ["x"]}})
    errs = " ".join(cfgmod.validate(c))
    for word in ("SSH 주소", "포트", "절대 경로", "중복", "own_networks"):
        assert word in errs
    assert cfgmod.validate(cfgmod.normalize({})) == []


def test_config_load_creates_default(tmp_path):
    p = tmp_path / "c.json"
    c = cfgmod.load(p)
    assert p.exists() and c["general"]["theme"] == "light"


def test_cli_check_exit_codes_and_no_files(cfg_file, tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    log = os.path.join(FIX, "access_sample.log")
    args = ["-c", cfg_file, "check", "-i", os.path.join(FIX, "ioc_typed.list"), "--local-logs", f"web01={log}",
            "--log-format", "test_xff", "--from", "2026-07-01", "--to", "2026-10-21", "--reply"]
    assert cli.main(args) == cli.EXIT_FOUND
    out = capsys.readouterr()
    assert "CRITICAL" in out.out and "[침해의심 IP 로그 점검 결과]" in out.out
    assert "9번째 줄" in out.err
    assert not list(tmp_path.glob("*.xlsx"))
    assert cli.main(["-c", cfg_file, "check", "--ip", "8.8.8.8", "--local-logs", f"web01={log}",
                     "--log-format", "test_xff", "--from", "2026-07-01", "--to", "2026-10-21"]) == cli.EXIT_NONE


def test_cli_saves_only_with_output(cfg_file, tmp_path):
    log = os.path.join(FIX, "access_sample.log")
    out = tmp_path / "res.xlsx"
    code = cli.main(["-c", cfg_file, "check", "--ip", "203.0.113.45", "--local-logs", f"web01={log}",
                     "--log-format", "test_xff", "--days", "3650", "-o", str(out)])
    assert code == cli.EXIT_FOUND and out.exists()


def test_cli_ioc_normalize(cfg_file, capsys):
    code = cli.main(["-c", cfg_file, "ioc", "normalize", "-i", os.path.join(FIX, "ioc_typed.list")])
    assert code == cli.EXIT_ERROR  # 오류 줄이 있으면 2
    assert capsys.readouterr().out.split() == [
        "203.0.113.45", "198.51.100.7", "192.0.2.10", "203.0.113.50", "198.51.100.200", "203.0.113.0/28"]


def test_startup_error_is_logged_and_shown(tmp_path, monkeypatch):
    """화면 exe가 시작하다 실패하면 원인을 iptrace_error.log에 남기고 메시지 창으로 알린다."""
    import ctypes
    from iptrace import main as entry
    shown = []
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(ctypes.windll.user32, "MessageBoxW", lambda h, msg, title, flags: shown.append(msg))
    try:
        raise ModuleNotFoundError("No module named 'tkinter'")
    except ModuleNotFoundError as e:
        entry.report_startup_error(e)
    log = (tmp_path / "iptrace_error.log").read_text(encoding="utf-8")
    assert "tkinter" in log and "Traceback" in log
    assert shown and "tkinter" in shown[0] and "iptrace_error.log" in shown[0]


def test_doctor_runs_without_config(tmp_path, monkeypatch, capsys):
    from iptrace import paths
    monkeypatch.setattr(paths, "BASE", tmp_path)
    monkeypatch.setattr(paths, "RULES_DIR", tmp_path / "rules")
    code = cli.main(["-c", str(tmp_path / "c.json"), "doctor"])
    out = capsys.readouterr().out
    assert "실행 환경 점검" in out and "[정상] 화면(tkinter)" in out and "공격 규칙 파일" in out
    assert code in (cli.EXIT_NONE, cli.EXIT_ERROR)


def test_old_config_becomes_logs_with_same_format():
    """이전 설정(log_paths + format)은 '모든 경로에 같은 형식'으로 바뀐다."""
    c = cfgmod.normalize({"servers": [{"name": "a", "host": "h", "format": "webtob_combined",
                                       "log_paths": ["/data/a/*.log", "/data/b/*.log"]}]})
    s = c["servers"][0]
    assert s["logs"] == [{"path": "/data/a/*.log", "format": "webtob_combined"},
                         {"path": "/data/b/*.log", "format": "webtob_combined"}]
    assert "webtob_error" in c["log_formats"]
    s["logs"][1]["format"] = "nope"
    assert any("'nope'" in e for e in cfgmod.validate(c))


def test_newer_bundled_rules_replace_old_runtime_rules(tmp_path, monkeypatch):
    """exe에 든 규칙이 더 새 버전이면 예전 규칙을 백업하고 바꾼다. 같거나 예전 버전이면 그대로 둔다."""
    import json
    from iptrace import paths
    bundled = tmp_path / "bundle"
    (bundled / "rules").mkdir(parents=True)
    (bundled / "rules" / "signatures.json").write_text(json.dumps({"version": "2026.10.26"}), encoding="utf-8")
    base = tmp_path / "app"
    for k, v in (("BASE", base), ("CONFIG_DIR", base / "config"), ("RULES_DIR", base / "rules"), ("LOGS_DIR", base / "logs")):
        monkeypatch.setattr(paths, k, v)
    monkeypatch.setattr(paths, "bundled_dir", lambda: bundled)
    (base / "rules").mkdir(parents=True)
    (base / "rules" / "signatures.json").write_text(json.dumps({"version": "2026.10.23", "mine": 1}), encoding="utf-8")
    paths.ensure_runtime_files()
    assert json.loads((base / "rules" / "signatures.json").read_text(encoding="utf-8"))["version"] == "2026.10.26"
    assert json.loads((base / "rules" / "signatures.json.bak-2026.10.23").read_text(encoding="utf-8"))["mine"] == 1
    (base / "rules" / "signatures.json").write_text(json.dumps({"version": "2026.12.01"}), encoding="utf-8")
    paths.ensure_runtime_files()   # 직접 더 새 버전으로 고친 규칙은 그대로
    assert json.loads((base / "rules" / "signatures.json").read_text(encoding="utf-8"))["version"] == "2026.12.01"
