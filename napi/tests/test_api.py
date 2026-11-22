import inspect
import json
import sys
import threading
import time
from dataclasses import replace
from datetime import date
from pathlib import Path

import pytest

from napi import __main__ as entry
from napi.api import Api
from napi.classifier import DEFAULT_PROBE_PATTERNS
from napi.config import load_config, save_config
from napi.models import (
    AggRow, AppConfig, CustomFormat, FileStat, LogFormatDef, LogPath, Note, ServerConfig, ServerScanResult,
    ServiceConfig, SpecOperation, SpecSource,
)
from napi.secrets import SessionSecrets
from napi.spec import SpecError

TODAY = date(2026, 11, 19)
SVC = "라운드페이 API"
PW = "S3cr3t-PW!xyz"
PP = "Key-Pass#987"
USER_A = '[%{%Y-%m-%dT%H:%M:%S}t] [CLIENT(%h)] "%r" %s'
USER_A_LINE = '[2026-11-18T10:00:01] [CLIENT(10.0.0.1)] "GET /v1/users/123?token=abc HTTP/1.1" 200'


def ops():
    return [SpecOperation("GET", "/v1/a", None, ["t"], "a", False),
            SpecOperation("GET", "/v1/b", None, [], None, False),
            SpecOperation("POST", "/v1/c", None, [], None, False)]


def servers():
    return [ServerConfig("WAS-01", "10.0.0.1", "app", [LogPath("/log/access.log*")]),
            ServerConfig("WAS-02", "10.0.0.2", "app", [LogPath("/log/access.log*")]),
            ServerConfig("WAS-03", "10.0.0.3", "web", [LogPath("/log/access.log*")], port=2222)]


def app_config():
    return AppConfig(services=[
        ServiceConfig(name=SVC, spec=SpecSource(location="http://spec"), servers=servers()),
        ServiceConfig(name="키 서비스", spec=SpecSource(location="http://k"), servers=[
            ServerConfig("K1", "10.0.1.1", "ops", [LogPath("/log/a.log")], auth="key",
                         key_file="C:/keys/id_ed25519")]),
    ])


def ok_result(name):
    return ServerScanResult(
        name, True, rows=[AggRow("GET", "/v1/a", "2xx", 4, "20260101", "20261115")],
        files=[FileStat("/log/access.log", "20250101", "20261118", 10, 0, 0)],
        elapsed_sec=1.0)


class FakeCollector:
    """collect()가 부르는 동작을 테스트마다 behavior로 바꾼다."""

    def __init__(self, settings, secrets, prompt, known_hosts, behavior=None, formats=()):
        self.prompt = prompt
        self.known_hosts = known_hosts
        self.behavior = behavior
        self.formats = formats

    def collect(self, servers, since, progress, cancel):
        if self.behavior:
            return self.behavior(self, servers, since, progress, cancel)
        for s in servers:
            progress(s.name, "connecting")
            progress(s.name, "done")
        return [ok_result(s.name) for s in servers]


class Dialogs:
    def __init__(self):
        self.open_answer = None
        self.save_answer = None
        self.save_calls = []
        self.key_answer = None
        self.key_calls = 0

    def open_file(self):
        return self.open_answer

    def open_key_file(self):
        self.key_calls += 1
        return self.key_answer

    def save_file(self, default_name, kind):
        self.save_calls.append((default_name, kind))
        return self.save_answer


class FakeExporter:
    class ReportError(Exception):
        pass

    def __init__(self):
        self.calls = []
        self.fail = None

    def export_html(self, result, path):
        self.calls.append(("html", result.service, Path(path)))
        Path(path).write_text("<html></html>", encoding="utf-8")
        return Path(path)

    def export_pdf(self, result, path):
        if self.fail:
            raise self.fail
        self.calls.append(("pdf", result.service, Path(path)))
        return Path(path)


@pytest.fixture
def env(tmp_path):
    cfg_path = tmp_path / "napi-config.yaml"
    save_config(app_config(), cfg_path)
    holder = {"behavior": None, "spec": lambda src: ops(), "collectors": []}

    def factory(settings, secrets, prompt, known_hosts, *, formats):
        c = FakeCollector(settings, secrets, prompt, known_hosts, holder["behavior"],
                          formats=formats)
        holder["collectors"].append(c)
        return c

    secrets = SessionSecrets()
    dialogs = Dialogs()
    exporter = FakeExporter()

    def make(**overrides):
        kw = dict(config_path=cfg_path, known_hosts=tmp_path / "known_hosts",
                  results_dir=tmp_path / "results", secrets=secrets,
                  collector_factory=factory, spec_loader=lambda src: holder["spec"](src),
                  open_file=dialogs.open_file, save_file=dialogs.save_file,
                  open_key_file=dialogs.open_key_file,
                  exporter=exporter, today=lambda: TODAY, hostkey_timeout=5.0)
        return Api(**{**kw, **overrides})

    return {"api": make(), "make": make, "path": cfg_path, "tmp": tmp_path, "holder": holder,
            "secrets": secrets, "dialogs": dialogs, "exporter": exporter,
            "results": tmp_path / "results"}


def wait_for(fn, timeout=5.0):
    end = time.monotonic() + timeout
    while time.monotonic() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.01)
    raise AssertionError("timed out waiting")


def wait_phase(api, scan_id, phases=("done", "failed", "cancelled")):
    return wait_for(lambda: (p := api.get_scan_progress(scan_id))["phase"] in phases and p)


# ------------------------------------------------------------------ config


def test_get_state_shape(env):
    api = env["api"]
    st = api.get_state()
    assert st["config_path"] == str(env["path"])
    assert [s["name"] for s in st["config"]["services"]] == [SVC, "키 서비스"]
    assert st["config"]["settings"]["max_parallel_servers"] == 2
    assert st["results"] == {SVC: None, "키 서비스": None}
    assert st["error"] is None
    json.dumps(st)


def test_hand_edited_unquoted_note_date_keeps_app_working(env):
    text = env["path"].read_text(encoding="utf-8")
    assert "notes: {}" in text
    env["path"].write_text(text.replace(
        "notes: {}", 'notes:\n    "GET /v1/b":\n      reason: 연 1회\n      author: 홍\n      date: 2026-11-19',
        1), encoding="utf-8")
    api = env["api"]
    st = api.get_state()
    assert st["error"] is None
    assert st["config"]["services"][0]["notes"]["GET /v1/b"]["date"] == "2026-11-19"
    json.dumps(st)
    p = wait_phase(api, api.start_scan(SVC, "365")["scan_id"])
    assert p["phase"] == "done"  # save_result의 json.dump가 date 때문에 죽지 않는다
    by = {a["path"]: a for a in api.get_result(SVC)["apis"]}
    assert by["/v1/b"]["note"]["date"] == "2026-11-19"


def test_get_state_exposes_default_probe_patterns(env):
    st = env["api"].get_state()
    assert st["default_probe_patterns"] == DEFAULT_PROBE_PATTERNS
    assert st["default_probe_patterns"] is not DEFAULT_PROBE_PATTERNS
    svc = st["config"]["services"][0]
    assert svc["api_scopes"] == [] and svc["probe_patterns"] is None
    env["path"].write_text("services: [1, 2", encoding="utf-8")
    assert env["api"].get_state()["default_probe_patterns"] == DEFAULT_PROBE_PATTERNS


def test_save_config_scope_fields(env):
    api = env["api"]
    cfg = api.get_state()["config"]
    cfg["services"][0]["api_scopes"] = ["/legacy"]
    cfg["services"][0]["probe_patterns"] = ["**/*.php"]
    assert api.save_config(cfg) == {"ok": True, "errors": []}
    svc = load_config(env["path"]).services[0]
    assert svc.api_scopes == ["/legacy"] and svc.probe_patterns == ["**/*.php"]
    cfg["services"][0]["api_scopes"] = ["legacy*"]
    res = api.save_config(cfg)
    assert res["ok"] is False and "API 범위" in res["errors"][0]


def test_get_state_reports_broken_config(env):
    env["path"].write_text("services: [1, 2", encoding="utf-8")
    st = env["api"].get_state()
    assert st["error"] and "설정 파일" in st["error"]
    assert st["config"]["services"] == [] and st["results"] == {}
    assert st["config_path"] == str(env["path"])


def test_save_config_validates(env):
    api = env["api"]
    before = env["path"].read_text(encoding="utf-8")
    bad = api.get_state()["config"]
    bad["services"][1]["servers"][0]["key_file"] = None
    res = api.save_config(bad)
    assert res["ok"] is False and res["errors"]
    assert "키 파일" in res["errors"][0]
    assert env["path"].read_text(encoding="utf-8") == before

    malformed = {"services": [{"spec": {}}]}
    res = api.save_config(malformed)
    assert res["ok"] is False and res["errors"]

    good = api.get_state()["config"]
    good["services"][0]["thresholds"]["awake_days"] = 45
    assert api.save_config(good) == {"ok": True, "errors": []}
    assert load_config(env["path"]).services[0].thresholds.awake_days == 45


def test_open_config_dialog_switches_path(env, tmp_path):
    api, dialogs = env["api"], env["dialogs"]
    other = tmp_path / "other.yaml"
    save_config(AppConfig(services=[ServiceConfig(
        name="다른", spec=SpecSource(location="x"), servers=[])]), other)

    dialogs.open_answer = None  # 취소
    assert api.open_config_dialog() == {"ok": False, "path": str(env["path"])}

    broken = tmp_path / "broken.yaml"
    broken.write_text("services: [1, 2", encoding="utf-8")
    dialogs.open_answer = str(broken)
    res = api.open_config_dialog()
    assert res["ok"] is False and "설정 파일" in res["error"]
    assert api.get_state()["config_path"] == str(env["path"])

    dialogs.open_answer = str(other)
    assert api.open_config_dialog() == {"ok": True, "path": str(other)}
    st = api.get_state()
    assert st["config_path"] == str(other)
    assert [s["name"] for s in st["config"]["services"]] == ["다른"]
    # 이후 저장도 고른 파일로 간다
    assert api.save_config(st["config"])["ok"] is True
    assert load_config(other).services[0].name == "다른"


def test_test_log_line(env):
    api = env["api"]
    line = ('10.0.0.1 - - [18/Nov/2026:10:00:01 +0900] '
            '"GET /v1/users/123?token=abc HTTP/1.1" 200 512')
    res = api.test_log_line("webtob", None, line)
    assert res["ok"] is True and res["normalized"] == "/v1/users/{*}"
    custom = {"method_field": "2", "path_field": 3, "status_field": 4,
              "time_field": 1, "time_format": "iso8601"}
    res = api.test_log_line("custom", custom, "2026-11-18T10:00:00 GET /v1/x 200")
    assert res["ok"] is True and res["path"] == "/v1/x" and res["date"] == "20261118"
    res = api.test_log_line("custom", {"method_field": 1}, "x")
    assert res["ok"] is False and res["error"] == "사용자 정의 형식이 올바르지 않아요"


def test_api_test_log_line_with_draft_formats(env):
    api = env["api"]
    draft = [{"name": "user_a", "pattern": USER_A}]
    res = api.test_log_line("user_a", None, USER_A_LINE, draft)
    assert res["ok"] is True and res["normalized"] == "/v1/users/{*}", res
    assert res["status"] == 200 and res["date"] == "20261118"
    # 초안이 없으면 저장된 설정의 형식을 쓴다 (아직 없음)
    res = api.test_log_line("user_a", None, USER_A_LINE)
    assert res["ok"] is False and res["error"] == "모르는 로그 형식이에요: user_a"
    cfg = load_config(env["path"])
    cfg.log_formats = [LogFormatDef("user_a", USER_A)]
    save_config(cfg, env["path"])
    assert api.test_log_line("user_a", None, USER_A_LINE)["ok"] is True
    # 빈 초안은 "형식 없음"이다 (저장된 설정으로 돌아가지 않는다)
    assert api.test_log_line("user_a", None, USER_A_LINE, [])["ok"] is False
    # 초안 패턴이 틀리면 한국어 오류
    res = api.test_log_line("bad", None, USER_A_LINE, [{"name": "bad", "pattern": "%h%l"}])
    assert res["ok"] is False and res["error"]
    # 형식이 아닌 항목은 예외 대신 한국어 오류
    for bad in ([{"name": "x"}], ["abc"], [{"name": 1, "pattern": USER_A}], "abc"):
        res = api.test_log_line("webtob", None, USER_A_LINE, bad)
        assert res == {"ok": False, "error": "형식 정의가 올바르지 않아요"}, bad


def test_api_test_log_line_with_broken_config_still_tests_builtin(env):
    env["path"].write_text("services: [\n", encoding="utf-8")
    line = '10.0.0.1 - - [18/Nov/2026:10:00:01 +0900] "GET /v1/a HTTP/1.1" 200 512'
    assert env["api"].test_log_line("webtob", None, line)["ok"] is True


def test_api_test_format(env):
    api = env["api"]
    res = api.test_format(USER_A, USER_A_LINE)
    assert res["ok"] is True and res["normalized"] == "/v1/users/{*}"
    assert res["method"] == "GET" and res["status"] == 200
    res = api.test_format('%h%l %t "%r" %s', USER_A_LINE)
    assert res["ok"] is False
    assert any("\uac00" <= ch <= "\ud7a3" for ch in res["error"]), res
    res = api.test_format(USER_A, "전혀 다른 줄")
    assert res["ok"] is False and res["error"]


def test_preview_commands_uses_user_formats(env):
    api = env["api"]
    cfg = load_config(env["path"])
    cfg.log_formats = [LogFormatDef("user_a", USER_A)]
    cfg.services[0].servers[0].log_paths[0].log_format = "user_a"
    save_config(cfg, env["path"])
    res = api.preview_commands(SVC, "90")
    assert "CLIENT(" in res["WAS-01"]
    assert "CLIENT(" not in res["WAS-02"]


def test_preview_uses_per_path_formats(env):
    api = env["api"]
    cfg = load_config(env["path"])
    cfg.log_formats = [LogFormatDef("user_a", USER_A)]
    cfg.services[0].servers[0].log_paths = [LogPath("/log/a*.log"),
                                            LogPath("/log/b*.log", "user_a")]
    save_config(cfg, env["path"])
    res = api.preview_commands(SVC, "90")
    assert "CLIENT(" in res["WAS-01"]
    assert "CLIENT(" not in res["WAS-02"]


def test_get_state_exposes_log_paths(env):
    api = env["api"]
    cfg = load_config(env["path"])
    cfg.services[0].servers[0].log_paths = [
        LogPath("/log/a*.log"), LogPath("/log/b*.log", "custom", CustomFormat(6, 7, 9, 4, "clf"))]
    save_config(cfg, env["path"])
    sv = api.get_state()["config"]["services"][0]["servers"][0]
    assert sv["log_paths"] == [
        {"glob": "/log/a*.log", "log_format": "webtob", "custom_format": None},
        {"glob": "/log/b*.log", "log_format": "custom",
         "custom_format": {"method_field": 6, "path_field": 7, "status_field": 9,
                           "time_field": 4, "time_format": "clf"}}]
    for k in ("log_glob", "log_globs", "log_format", "custom_format"):
        assert k not in sv


def test_scan_one_server_bad_path_format_others_continue(env):
    """진짜 Collector: 경로 하나의 형식이 잘못된 서버만 설정 오류로 실패하고 나머지는 계속된다."""
    from napi.collector import Collector

    class Refuse:  # 접속 단계까지 온 서버는 접속 실패로 끝난다
        def set_missing_host_key_policy(self, p): pass
        def load_host_keys(self, f): pass
        def connect(self, **kw): raise OSError("no route")
        def close(self): pass

    cfg = load_config(env["path"])
    cfg.services[0].servers[0].log_paths = [LogPath("/log/a*.log"), LogPath("/log/b*.log", "nope")]
    save_config(cfg, env["path"])
    got = {}

    class Spy(Collector):
        def collect(self, servers, since, progress, cancel):
            res = super().collect(servers, since, progress, cancel)
            got.update({r.server: r.error for r in res})
            return res

    for u, h, p in (("app", "10.0.0.1", 22), ("app", "10.0.0.2", 22), ("web", "10.0.0.3", 2222)):
        env["secrets"].set_password(u, h, p, PW)
    api = env["make"](collector_factory=lambda settings, secrets, prompt, known_hosts, *, formats:
                      Spy(settings, secrets, prompt, known_hosts,
                          client_factory=Refuse, formats=formats))
    scan_id = api.start_scan(SVC, "365")["scan_id"]
    wait_phase(api, scan_id)
    assert got["WAS-01"] == "설정 오류: 경로 /log/b*.log: 모르는 로그 형식이에요: nope"
    assert got["WAS-02"] == got["WAS-03"] == "접속할 수 없어요"


def test_start_scan_passes_user_formats(env):
    api, holder = env["api"], env["holder"]
    cfg = load_config(env["path"])
    cfg.log_formats = [LogFormatDef("user_a", USER_A)]
    save_config(cfg, env["path"])
    scan_id = api.start_scan(SVC, "365")["scan_id"]
    assert wait_phase(api, scan_id)["phase"] == "done"
    assert list(holder["collectors"][0].formats) == [LogFormatDef("user_a", USER_A)]


def test_preview_commands(env):
    api = env["api"]
    cfg = load_config(env["path"])
    cfg.services[0].servers[2].log_paths = [LogPath("relative/*.log")]
    save_config(cfg, env["path"])
    res = api.preview_commands(SVC, "90")
    assert set(res) == {"WAS-01", "WAS-02", "WAS-03"}
    assert "napi-scan-00000000" in res["WAS-01"]
    assert res["WAS-01"].startswith("timeout 3600 nice -n 19")
    assert "20260821" in res["WAS-01"]  # 90일 전
    assert res["WAS-03"].startswith("설정 오류: ")
    assert api.preview_commands(SVC, "all")["WAS-01"].count("napi-scan-00000000") >= 1
    bad = api.preview_commands("없는 서비스", "90")
    assert bad["ok"] is False and "없는 서비스" in bad["error"]
    bad = api.preview_commands(SVC, "7")
    assert bad == {"ok": False, "error": "분석 기간이 올바르지 않아요"}


# ----------------------------------------------------------------- secrets


def test_missing_secrets_and_passphrase(env):
    api, secrets = env["api"], env["secrets"]
    missing = api.missing_secrets(SVC)
    assert missing == [
        {"server": "WAS-01", "user": "app", "host": "10.0.0.1", "port": 22, "auth": "password"},
        {"server": "WAS-02", "user": "app", "host": "10.0.0.2", "port": 22, "auth": "password"},
        {"server": "WAS-03", "user": "web", "host": "10.0.0.3", "port": 2222, "auth": "password"},
    ]
    assert api.missing_secrets("키 서비스") == []
    assert api.set_passphrase("C:/keys/id_ed25519", PP) == {"ok": True}
    assert secrets.get_passphrase("C:/keys/id_ed25519") == PP
    assert api.set_password("app", "10.0.0.1", 22, PW) == {"ok": True}
    assert [m["server"] for m in api.missing_secrets(SVC)] == ["WAS-02", "WAS-03"]


def test_api_secret_status_and_clear(env):
    api, secrets = env["api"], env["secrets"]
    assert api.secret_status(SVC) == {"WAS-01": "missing", "WAS-02": "missing",
                                      "WAS-03": "missing"}
    api.set_password("app", "10.0.0.1", 22, PW)
    assert api.secret_status(SVC) == {"WAS-01": "set", "WAS-02": "missing",
                                      "WAS-03": "missing"}
    assert api.secret_status("키 서비스") == {"K1": "n/a"}
    api.set_passphrase("C:/keys/id_ed25519", PP)
    assert api.secret_status("키 서비스") == {"K1": "set"}

    assert api.clear_secret(SVC, "WAS-01") == {"ok": True}
    assert secrets.get_password("app", "10.0.0.1", 22) is None
    assert api.secret_status(SVC)["WAS-01"] == "missing"
    assert api.clear_secret("키 서비스", "K1") == {"ok": True}
    assert secrets.get_passphrase("C:/keys/id_ed25519") is None
    assert api.secret_status("키 서비스") == {"K1": "n/a"}

    assert api.clear_secret(SVC, "없는서버") == {"ok": False,
                                                "error": "서버를 찾을 수 없어요: 없는서버"}
    assert api.clear_secret("없음", "WAS-01")["ok"] is False
    assert api.secret_status("없음")["ok"] is False


def test_api_secret_status_key_server_matches_exact_key_file(env):
    api = env["api"]
    api.set_passphrase("c:/keys/id_ed25519", PP)  # 대소문자가 다른 경로
    assert api.secret_status("키 서비스") == {"K1": "n/a"}


def test_api_pick_key_file(env):
    api, dialogs = env["api"], env["dialogs"]
    p = str(env["tmp"] / "no-such-dir" / "id_ed25519")  # 존재하지 않아도 된다 (열지 않음)
    dialogs.key_answer = p
    assert api.pick_key_file() == {"ok": True, "path": p}
    dialogs.key_answer = None
    assert api.pick_key_file() == {"ok": False, "path": None}
    assert dialogs.key_calls == 2


def test_set_password_apply_to_service(env):
    api, secrets = env["api"], env["secrets"]
    assert api.set_password("app", "10.0.0.1", "22", PW, apply_to_service=SVC) == {"ok": True}
    for s in servers():
        assert secrets.get_password(s.user, s.host, s.port) == PW
    assert api.missing_secrets(SVC) == []
    assert api.set_password("a", "h", 22, PW, apply_to_service="없음")["ok"] is False


# -------------------------------------------------------------------- scan


def test_scan_flow_with_fakes(env):
    api, holder = env["api"], env["holder"]
    gate, reached = threading.Event(), threading.Event()

    def behavior(col, servers_, since, progress, cancel):
        assert since == "20251119"
        for s in servers_:
            progress(s.name, "connecting")
        progress(servers_[0].name, "aggregating")
        reached.set()
        assert gate.wait(5)
        for s in servers_:
            progress(s.name, "done")
        return [ok_result(s.name) for s in servers_]

    holder["behavior"] = behavior
    started = api.start_scan(SVC, "365")
    scan_id = started["scan_id"]
    assert reached.wait(5)
    p = api.get_scan_progress(scan_id)
    assert p["phase"] == "collect"
    assert p["servers"] == {"WAS-01": "aggregating", "WAS-02": "connecting",
                            "WAS-03": "connecting"}
    assert p["hostkey_pending"] is None and p["error"] is None
    assert holder["collectors"][0].known_hosts == env["tmp"] / "known_hosts"
    gate.set()
    p = wait_phase(api, scan_id)
    assert p["phase"] == "done", p
    assert set(p["servers"].values()) == {"done"}
    r = api.get_result(SVC)
    assert isinstance(r, dict) and r["service"] == SVC
    json.dumps(r)
    by = {a["path"]: a for a in r["apis"]}
    assert by["/v1/a"]["status"] == "awake" and by["/v1/b"]["status"] == "asleep"
    assert r["sleeping"] == 2
    st = api.get_state()["results"]
    assert st[SVC] == {"sleeping": 2, "scanned_at": r["scanned_at"]}
    assert st["키 서비스"] is None


def test_scan_spec_phase_before_collect(env):
    api, holder = env["api"], env["holder"]
    gate, reached = threading.Event(), threading.Event()

    def slow_spec(src):
        reached.set()
        assert gate.wait(5)
        return ops()

    holder["spec"] = slow_spec
    scan_id = api.start_scan(SVC, "all")["scan_id"]
    assert reached.wait(5)
    p = api.get_scan_progress(scan_id)
    assert p["phase"] == "spec"
    assert p["servers"] == {"WAS-01": "waiting", "WAS-02": "waiting", "WAS-03": "waiting"}
    gate.set()
    assert wait_phase(api, scan_id)["phase"] == "done"


def test_scan_spec_error_fails_without_saving(env):
    api, holder = env["api"], env["holder"]

    def bad(src):
        raise SpecError("명세를 가져오지 못했어요: http://spec")

    holder["spec"] = bad
    scan_id = api.start_scan(SVC, "365")["scan_id"]
    p = wait_phase(api, scan_id)
    assert p["phase"] == "failed"
    assert p["error"] == "명세를 가져오지 못했어요: http://spec"
    assert api.get_result(SVC) is None


def test_scan_unexpected_error_hides_details(env):
    api, holder = env["api"], env["holder"]
    api.set_password("app", "10.0.0.1", 22, PW)

    def boom(col, servers_, since, progress, cancel):
        raise RuntimeError(f"leaked {PW}")

    holder["behavior"] = boom
    scan_id = api.start_scan(SVC, "365")["scan_id"]
    p = wait_phase(api, scan_id)
    assert p["phase"] == "failed"
    assert p["error"] == "스캔 중 오류가 발생했어요 (RuntimeError)"
    # 끝난 뒤에는 같은 서비스를 다시 스캔할 수 있다
    holder["behavior"] = None
    again = api.start_scan(SVC, "365")["scan_id"]
    assert wait_phase(api, again)["phase"] == "done"


def test_scan_with_failed_server_is_saved(env):
    api, holder = env["api"], env["holder"]

    def partial(col, servers_, since, progress, cancel):
        progress("WAS-01", "done")
        progress("WAS-02", "failed")
        return [ok_result("WAS-01"), ServerScanResult("WAS-02", False, error="인증 실패"),
                ok_result("WAS-03")]

    holder["behavior"] = partial
    scan_id = api.start_scan(SVC, "365")["scan_id"]
    p = wait_phase(api, scan_id)
    assert p["phase"] == "done"
    assert p["servers"]["WAS-02"] == "failed"
    r = api.get_result(SVC)
    assert r["reliability"]["level"] == "low"
    assert [s["error"] for s in r["servers"]] == [None, "인증 실패", None]


def test_scan_without_any_logs_fails_and_keeps_previous_result(env):
    api, holder = env["api"], env["holder"]
    first = api.start_scan(SVC, "365")["scan_id"]
    assert wait_phase(api, first)["phase"] == "done"
    previous = api.get_result(SVC)

    def no_logs(col, servers_, since, progress, cancel):
        return [ServerScanResult("WAS-01", False, error="인증 실패"),
                ServerScanResult("WAS-02", True),
                ServerScanResult("WAS-03", False, error="접속 실패")]

    holder["behavior"] = no_logs
    p = wait_phase(api, api.start_scan(SVC, "365")["scan_id"])
    assert p["phase"] == "failed"
    assert p["error"] == ("로그를 가져온 서버가 없어 판정하지 않았어요 (WAS-01: 인증 실패, "
                          "WAS-02: 로그 파일을 찾지 못했어요, WAS-03: 접속 실패)")
    assert api.get_result(SVC) == previous


def test_scan_of_service_without_servers_fails(env):
    cfg = app_config()
    cfg.services[0].servers = []
    save_config(cfg, env["path"])
    api = env["api"]
    p = wait_phase(api, api.start_scan(SVC, "365")["scan_id"])
    assert p["phase"] == "failed"
    assert p["error"] == "로그를 가져온 서버가 없어 판정하지 않았어요 (등록된 서버가 없어요)"
    assert api.get_result(SVC) is None


def test_cancel_scan_keeps_previous_result(env):
    api, holder = env["api"], env["holder"]
    first = api.start_scan(SVC, "365")["scan_id"]
    assert wait_phase(api, first)["phase"] == "done"
    previous = api.get_result(SVC)

    reached = threading.Event()

    def until_cancel(col, servers_, since, progress, cancel):
        progress("WAS-01", "aggregating")
        reached.set()
        assert cancel.wait(5)
        progress("WAS-01", "cancelled")
        return [ServerScanResult(s.name, False, error="취소됨") for s in servers_]

    holder["behavior"] = until_cancel
    scan_id = api.start_scan(SVC, "all")["scan_id"]
    assert reached.wait(5)
    assert api.cancel_scan(scan_id) == {"ok": True}
    p = wait_phase(api, scan_id)
    assert p["phase"] == "cancelled" and p["error"] is None
    assert api.get_result(SVC) == previous


def test_one_active_scan_per_service(env):
    api, holder = env["api"], env["holder"]
    gate = threading.Event()

    def blocked(col, servers_, since, progress, cancel):
        assert gate.wait(5)
        return [ok_result(s.name) for s in servers_]

    holder["behavior"] = blocked
    first = api.start_scan(SVC, "365")["scan_id"]
    assert api.start_scan(SVC, "365") == {"ok": False, "error": "이미 스캔 중이에요"}
    other = api.start_scan("키 서비스", "365")
    assert "scan_id" in other and other["scan_id"] != first
    gate.set()
    assert wait_phase(api, first)["phase"] == "done"
    assert wait_phase(api, other["scan_id"])["phase"] == "done"


def test_start_scan_rejects_bad_input(env):
    api = env["api"]
    assert api.start_scan("없는 서비스", "365")["ok"] is False
    assert api.start_scan(SVC, "30") == {"ok": False, "error": "분석 기간이 올바르지 않아요"}
    assert api.get_scan_progress("nope") == {"ok": False, "error": "스캔을 찾을 수 없어요"}
    assert api.cancel_scan("nope") == {"ok": False, "error": "스캔을 찾을 수 없어요"}
    assert api.answer_hostkey("nope", True) == {"ok": False, "error": "스캔을 찾을 수 없어요"}


# ---------------------------------------------------------------- host key


def test_hostkey_roundtrip(env):
    api, holder = env["api"], env["holder"]
    answers = []

    def asks(col, servers_, since, progress, cancel):
        progress("WAS-01", "connecting")
        answers.append(col.prompt("10.0.0.1", 22, "ssh-ed25519", "SHA256:abc"))
        progress("WAS-01", "done")
        return [ok_result(s.name) for s in servers_]

    holder["behavior"] = asks
    scan_id = api.start_scan(SVC, "365")["scan_id"]
    p = wait_for(lambda: (q := api.get_scan_progress(scan_id))["hostkey_pending"] and q)
    pending = p["hostkey_pending"]
    assert {k: pending[k] for k in ("host", "port", "key_type", "fingerprint")} == {
        "host": "10.0.0.1", "port": 22, "key_type": "ssh-ed25519", "fingerprint": "SHA256:abc"}
    assert isinstance(pending["prompt_id"], str) and pending["prompt_id"]
    assert p["phase"] == "collect"
    assert answers == []  # 답을 기다리는 중
    assert api.answer_hostkey(scan_id, True) == {"ok": True}
    p = wait_phase(api, scan_id)
    assert p["phase"] == "done" and p["hostkey_pending"] is None
    assert answers == [True]
    assert api.answer_hostkey(scan_id, True) == {"ok": False, "error": "확인할 호스트 키가 없어요"}


def test_hostkey_concurrent_prompts_fifo(env):
    api, holder = env["api"], env["holder"]
    answers = {}
    scan_ref = []

    def asks(col, servers_, since, progress, cancel):
        def ask(host):
            answers[host] = col.prompt(host, 22, "ssh-rsa", "SHA256:" + host)

        t1 = threading.Thread(target=ask, args=("h1",))
        t1.start()
        wait_for(lambda: api.get_scan_progress(scan_ref[0])["hostkey_pending"])
        t2 = threading.Thread(target=ask, args=("h2",))
        t2.start()
        t1.join(5)
        t2.join(5)
        return [ok_result(s.name) for s in servers_]

    holder["behavior"] = asks
    scan_ref.append(api.start_scan(SVC, "365")["scan_id"])
    scan_id = scan_ref[0]
    first = wait_for(lambda: api.get_scan_progress(scan_id)["hostkey_pending"])
    assert first["host"] == "h1"
    assert api.answer_hostkey(scan_id, True, prompt_id="zzz") == {
        "ok": False, "error": "확인할 호스트 키가 없어요"}
    assert api.answer_hostkey(scan_id, True, prompt_id=first["prompt_id"]) == {"ok": True}
    second = wait_for(lambda: (p := api.get_scan_progress(scan_id)["hostkey_pending"])
                      and p["host"] == "h2" and p)
    assert second["prompt_id"] != first["prompt_id"]
    assert api.answer_hostkey(scan_id, False) == {"ok": True}
    assert wait_phase(api, scan_id)["phase"] == "done"
    assert answers == {"h1": True, "h2": False}


def test_hostkey_timeout_rejects(env):
    api, holder = env["make"](hostkey_timeout=0.05), env["holder"]
    answers = []

    def asks(col, servers_, since, progress, cancel):
        answers.append(col.prompt("10.0.0.1", 22, "ssh-ed25519", "SHA256:abc"))
        return [ok_result(s.name) for s in servers_]

    holder["behavior"] = asks
    scan_id = api.start_scan(SVC, "365")["scan_id"]
    p = wait_phase(api, scan_id)
    assert answers == [False]
    assert p["hostkey_pending"] is None


def test_cancel_rejects_pending_prompts(env):
    api, holder = env["api"], env["holder"]
    answers = []

    def asks(col, servers_, since, progress, cancel):
        answers.append(col.prompt("10.0.0.1", 22, "ssh-ed25519", "SHA256:abc"))
        return [ServerScanResult(s.name, False, error="취소됨") for s in servers_]

    holder["behavior"] = asks
    scan_id = api.start_scan(SVC, "365")["scan_id"]
    wait_for(lambda: api.get_scan_progress(scan_id)["hostkey_pending"])
    started = time.monotonic()
    assert api.cancel_scan(scan_id) == {"ok": True}
    p = wait_phase(api, scan_id)
    assert time.monotonic() - started < 2
    assert answers == [False]
    assert p["phase"] == "cancelled" and p["hostkey_pending"] is None
    # 취소된 뒤 들어오는 프롬프트는 바로 거절된다
    assert holder["collectors"][0].prompt("h", 22, "t", "f") is False


# ------------------------------------------------------------------- notes


def test_note_updates_result(env):
    api = env["api"]
    scan_id = api.start_scan(SVC, "365")["scan_id"]
    assert wait_phase(api, scan_id)["phase"] == "done"
    before = api.get_result(SVC)
    assert before["sleeping"] == 2

    assert api.save_note(SVC, "GET /v1/b", "  배치에서 호출 ", "홍길동") == {"ok": True}
    after = api.get_result(SVC)
    note = {a["path"]: a for a in after["apis"]}["/v1/b"]["note"]
    assert note == {"reason": "배치에서 호출", "author": "홍길동", "date": "2026-11-19"}
    assert after["sleeping"] == 1 and after["sleeping_noted"] == 1
    assert load_config(env["path"]).services[0].notes["GET /v1/b"] == Note(
        "배치에서 호출", "홍길동", "2026-11-19")
    assert api.get_state()["results"][SVC]["sleeping"] == 1

    # 다른 note가 이미 있어도 전체 notes로 갱신된다
    assert api.save_note(SVC, "POST /v1/c", "외부 연동", "김")["ok"] is True
    assert api.get_result(SVC)["sleeping"] == 0

    assert api.delete_note(SVC, "GET /v1/b") == {"ok": True}
    r = api.get_result(SVC)
    assert {a["path"]: a for a in r["apis"]}["/v1/b"]["note"] is None
    assert r["sleeping"] == 1
    assert "GET /v1/b" not in load_config(env["path"]).services[0].notes


def test_note_without_result_and_empty_reason(env):
    api = env["api"]
    assert api.save_note(SVC, "GET /v1/b", "   ", "홍") == {
        "ok": False, "error": "유지 사유를 입력해 주세요"}
    assert api.save_note(SVC, "GET /v1/b", "사유", "") == {"ok": True}
    assert load_config(env["path"]).services[0].notes["GET /v1/b"].reason == "사유"
    assert api.get_result(SVC) is None
    assert api.save_note("없음", "GET /x", "r", "a")["ok"] is False


def test_scan_result_uses_notes_saved_during_scan(env):
    api, holder = env["api"], env["holder"]
    gate, reached = threading.Event(), threading.Event()

    def blocked(col, servers_, since, progress, cancel):
        reached.set()
        assert gate.wait(5)
        return [ok_result(s.name) for s in servers_]

    holder["behavior"] = blocked
    scan_id = api.start_scan(SVC, "365")["scan_id"]
    assert reached.wait(5)
    assert api.save_note(SVC, "GET /v1/b", "배치", "홍")["ok"] is True
    gate.set()
    assert wait_phase(api, scan_id)["phase"] == "done"
    r = api.get_result(SVC)
    assert {a["path"]: a for a in r["apis"]}["/v1/b"]["note"]["reason"] == "배치"
    assert r["sleeping"] == 1


# ------------------------------------------------------------------ export


def test_export_with_fake_exporter(env, tmp_path):
    api, dialogs, exporter = env["api"], env["dialogs"], env["exporter"]
    assert api.export(SVC, "html") == {"ok": False, "error": "아직 스캔 결과가 없어요"}
    scan_id = api.start_scan(SVC, "365")["scan_id"]
    assert wait_phase(api, scan_id)["phase"] == "done"

    dialogs.save_answer = None  # 취소
    assert api.export(SVC, "html") == {"ok": False, "path": None}
    assert dialogs.save_calls == [("napi_라운드페이 API_20261119.html", "html")]

    out = tmp_path / "out.html"
    dialogs.save_answer = str(out)
    assert api.export(SVC, "html") == {"ok": True, "path": str(out)}
    assert exporter.calls == [("html", SVC, out)]

    dialogs.save_answer = str(tmp_path / "out.pdf")
    assert api.export(SVC, "pdf")["ok"] is True
    assert dialogs.save_calls[-1] == ("napi_라운드페이 API_20261119.pdf", "pdf")
    exporter.fail = FakeExporter.ReportError("Edge를 찾지 못했어요")
    assert api.export(SVC, "pdf") == {"ok": False, "error": "Edge를 찾지 못했어요"}
    exporter.fail = OSError(f"disk {PW}")
    assert api.export(SVC, "pdf") == {"ok": False, "error": "처리 중 오류가 발생했어요 (OSError)"}
    assert api.export(SVC, "docx") == {"ok": False, "error": "내보내기 형식이 올바르지 않아요"}


def test_export_default_name_is_filename_safe(env, tmp_path):
    api, dialogs = env["api"], env["dialogs"]
    cfg = load_config(env["path"])
    cfg.services.append(replace(cfg.services[0], name="a/b:c"))
    save_config(cfg, env["path"])
    scan_id = api.start_scan("a/b:c", "365")["scan_id"]
    assert wait_phase(api, scan_id)["phase"] == "done"
    dialogs.save_answer = None
    api.export("a/b:c", "pdf")
    assert dialogs.save_calls == [("napi_a_b_c_20261119.pdf", "pdf")]


# ------------------------------------------------------------------ safety


def test_unexpected_exception_becomes_korean_error(env, monkeypatch):
    api = env["api"]

    def boom(*a, **k):
        raise KeyError(PW)

    monkeypatch.setattr("napi.api.load_result", boom)
    assert api.get_result(SVC) == {"ok": False, "error": "처리 중 오류가 발생했어요 (KeyError)"}


def test_public_methods_keep_signature(env):
    params = list(inspect.signature(Api.set_password).parameters)
    assert params == ["self", "user", "host", "port", "password", "apply_to_service"]
    spec = inspect.getfullargspec(env["api"].answer_hostkey)
    assert spec.args == ["self", "scan_id", "accept", "prompt_id"]


PUBLIC = {
    "get_state", "save_config", "open_config_dialog", "test_log_line", "preview_commands",
    "missing_secrets", "set_password", "set_passphrase", "start_scan", "get_scan_progress",
    "answer_hostkey", "cancel_scan", "get_result", "save_note", "delete_note", "export",
    "test_format", "secret_status", "clear_secret", "pick_key_file",
}


def test_public_method_count():
    assert len(PUBLIC) == 20


def test_no_secret_in_any_return(env, tmp_path):
    api, holder, dialogs = env["api"], env["holder"], env["dialogs"]
    public = {n for n in dir(api) if not n.startswith("_")}
    assert public == PUBLIC

    outs = []
    call = lambda name, *a, **k: outs.append((name, getattr(api, name)(*a, **k)))  # noqa: E731
    call("set_password", "app", "10.0.0.1", 22, PW, apply_to_service=SVC)
    call("set_password", "app", "10.0.0.1", 22, PW, apply_to_service="없음")
    call("set_passphrase", "C:/keys/id_ed25519", PP)

    def asks(col, servers_, since, progress, cancel):
        progress("WAS-01", "connecting")
        col.prompt("10.0.0.1", 22, "ssh-ed25519", "SHA256:abc")
        return [ok_result(s.name) for s in servers_]

    holder["behavior"] = asks
    call("start_scan", SVC, "365")
    scan_id = outs[-1][1]["scan_id"]
    wait_for(lambda: api.get_scan_progress(scan_id)["hostkey_pending"])
    call("start_scan", SVC, "365")
    call("get_scan_progress", scan_id)
    call("answer_hostkey", scan_id, True)
    wait_phase(api, scan_id)
    call("get_scan_progress", scan_id)
    call("cancel_scan", scan_id)
    call("get_state")
    call("save_config", api.get_state()["config"])
    call("save_config", {"services": [{"name": "x", "servers": [{"bogus": 1}]}]})
    dialogs.open_answer = None
    call("open_config_dialog")
    call("test_log_line", "webtob", None, f"x {PW}")
    call("test_log_line", "user_a", None, USER_A_LINE, [{"name": "user_a", "pattern": USER_A}])
    call("test_format", USER_A, f"x {PW}")
    call("secret_status", SVC)
    call("secret_status", "키 서비스")
    dialogs.key_answer = "C:/keys/id_ed25519"
    call("pick_key_file")
    call("preview_commands", SVC, "365")
    call("missing_secrets", SVC)
    call("missing_secrets", "키 서비스")
    call("get_result", SVC)
    call("save_note", SVC, "GET /v1/b", "사유", "홍")
    call("delete_note", SVC, "GET /v1/b")
    dialogs.save_answer = str(tmp_path / "r.html")
    call("export", SVC, "html")
    call("clear_secret", SVC, "WAS-01")
    call("clear_secret", "키 서비스", "K1")
    call("clear_secret", SVC, "없는서버")

    assert {name for name, _ in outs} == PUBLIC
    for name, out in outs:
        text = json.dumps(out, ensure_ascii=False)
        assert PW not in text, name
        assert PP not in text, name
    for f in tmp_path.rglob("*"):
        if f.is_file() and f.suffix in (".yaml", ".json"):
            content = f.read_text(encoding="utf-8")
            assert PW not in content and PP not in content


# -------------------------------------------------------------- __main__


def test_ui_index_path(monkeypatch, tmp_path):
    monkeypatch.delattr(sys, "frozen", raising=False)
    expected = Path(entry.__file__).parent / "ui" / "index.html"
    assert entry.ui_index_path() == expected
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(tmp_path), raising=False)
    assert entry.ui_index_path() == tmp_path / "napi" / "ui" / "index.html"


# ------------------------------------------------------------- shutdown


def test_shutdown_cancels_active_scans(env):
    api, holder = env["api"], env["holder"]
    reached = threading.Event()
    answers = []

    def until_cancel(col, servers_, since, progress, cancel):
        reached.set()
        answers.append(col.prompt("10.0.0.1", 22, "ssh-ed25519", "SHA256:abc"))
        assert cancel.wait(5)
        return [ServerScanResult(s.name, False, error="취소됨") for s in servers_]

    holder["behavior"] = until_cancel
    scan_id = api.start_scan(SVC, "365")["scan_id"]
    assert reached.wait(5)
    wait_for(lambda: api.get_scan_progress(scan_id)["hostkey_pending"])
    started = time.monotonic()
    api._shutdown()  # noqa: SLF001 — 앱 종료 시 __main__이 부른다
    p = wait_phase(api, scan_id)
    assert time.monotonic() - started < 2
    assert p["phase"] == "cancelled"
    assert answers == [False]
    assert api.get_result(SVC) is None
    api._shutdown()  # 활성 스캔이 없어도 안전하다


# ----------------------------------------------------------- WebView2


def fake_registry(values):
    def read(hive, path, name):
        return values.get((hive, path, name))
    return read


NET = ("HKEY_LOCAL_MACHINE", r"SOFTWARE\Microsoft\NET Framework Setup\NDP\v4\Full", "Release")
RUNTIME = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"
HKLM64 = ("HKEY_LOCAL_MACHINE",
          rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{RUNTIME}", "pv")
HKLM32 = ("HKEY_LOCAL_MACHINE", rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{RUNTIME}", "pv")
HKCU = ("HKEY_CURRENT_USER", rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{RUNTIME}", "pv")
CANARY_HKCU = ("HKEY_CURRENT_USER",
               r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{65C35B14-6C1D-4122-AC46-7148CC9D6497}",
               "pv")


@pytest.mark.parametrize("values, machine, expected", [
    ({NET: 528040, HKLM64: "131.0.2903.70"}, "AMD64", True),
    ({NET: 528040, HKCU: "120.0.1.0"}, "AMD64", True),
    ({NET: 528040, CANARY_HKCU: "133.0.1.0"}, "AMD64", True),
    ({NET: 528040, HKLM32: "131.0.2903.70"}, "x86", True),
    ({NET: 528040, HKLM32: "131.0.2903.70"}, "AMD64", False),  # pywebview도 이 경로를 안 본다
    ({NET: 528040}, "AMD64", False),
    ({NET: 528040, HKLM64: "0.0.0.0"}, "AMD64", False),
    ({NET: 528040, HKLM64: "86.0.621.9"}, "AMD64", False),
    ({NET: 528040, HKLM64: "86.0.622.0"}, "AMD64", True),
    ({NET: 528040, HKLM64: "garbage"}, "AMD64", False),
    ({NET: 394801, HKLM64: "131.0.2903.70"}, "AMD64", False),  # .NET 4.6.2 미만
    ({HKLM64: "131.0.2903.70"}, "AMD64", False),
])
def test_webview2_available(values, machine, expected):
    assert entry.webview2_available(read=fake_registry(values), machine=machine) is expected


def test_webview2_available_real_registry_does_not_raise():
    assert entry.webview2_available() in (True, False)


class FakeWebview:
    class _Handlers:
        def __init__(self):
            self.items = []

        def __iadd__(self, fn):
            self.items.append(fn)
            return self

    def __init__(self, start_error=None):
        self.start_error = start_error
        self.created = []
        self.started = []

    def create_window(self, title, **kw):
        win = type("W", (), {})()
        win.events = type("E", (), {})()
        win.events.closed = self._Handlers()
        self.created.append((title, kw, win))
        return win

    def start(self, **kw):
        self.started.append(kw)
        if self.start_error:
            raise self.start_error


@pytest.fixture
def main_env(monkeypatch, tmp_path):
    monkeypatch.setenv("NAPI_HOME", str(tmp_path))
    boxes = []
    monkeypatch.setattr(entry, "_message_box", boxes.append)
    shutdowns = []
    real = Api._shutdown

    def spy(self):
        shutdowns.append(self)
        real(self)

    monkeypatch.setattr(Api, "_shutdown", spy)
    return boxes, shutdowns


def test_main_without_webview2_shows_message_and_no_window(monkeypatch, main_env):
    boxes, _ = main_env
    fake = FakeWebview()
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(entry, "webview2_available", lambda: False)
    with pytest.raises(SystemExit) as exc:
        entry.main()
    assert exc.value.code == 1
    assert boxes == [entry.WEBVIEW2_MISSING]
    assert entry.WEBVIEW2_MISSING == ("WebView2 런타임이 필요해요. "
                                      "https://go.microsoft.com/fwlink/p/?LinkId=2124703 "
                                      "에서 설치해 주세요")
    assert fake.created == [] and fake.started == []


def test_main_opens_window_and_shuts_down(monkeypatch, main_env):
    boxes, shutdowns = main_env
    fake = FakeWebview()
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(entry, "webview2_available", lambda: True)
    entry.main()
    (title, kw, win), = fake.created
    assert title == "napi — 잠자는 API를 찾아요"
    assert kw["url"] == str(entry.ui_index_path())
    assert isinstance(kw["js_api"], Api)
    assert (kw["width"], kw["height"], kw["min_size"]) == (1280, 820, (1000, 640))
    assert fake.started == [{"gui": "edgechromium"}]
    assert len(win.events.closed.items) == 1
    assert shutdowns == [kw["js_api"]]
    assert boxes == []


def test_main_start_failure_shows_generic_message(monkeypatch, main_env, capsys):
    boxes, shutdowns = main_env
    fake = FakeWebview(start_error=RuntimeError("boom detail"))
    monkeypatch.setitem(sys.modules, "webview", fake)
    monkeypatch.setattr(entry, "webview2_available", lambda: True)
    with pytest.raises(SystemExit) as exc:
        entry.main()
    assert exc.value.code == 1
    assert boxes == ["napi를 시작하지 못했어요: RuntimeError"]
    assert "boom detail" in capsys.readouterr().err  # traceback은 stderr로
    assert len(shutdowns) == 1


# pywebview는 Python 매개변수 이름을 그대로 써서 JS 함수를 만든다 (new Function(params, body)).
# 이름이 JS 전역(window 등)과 같으면 생성된 함수 본문의 window.pywebview 호출이 깨져
# 버튼을 눌러도 아무 일도 일어나지 않는다 (실행될 명령 보기 / 스캔 버그).
_JS_UNSAFE_PARAM_NAMES = {"window", "document", "pywebview", "arguments", "Promise", "Array",
                          "Math", "JSON", "Object", "String", "undefined", "eval", "self",
                          "globalThis", "navigator", "location", "console"}


def test_js_safe_param_names():
    import inspect
    from napi.api import Api
    bad = {}
    for name in dir(Api):
        if name.startswith("_") or not callable(getattr(Api, name)):
            continue
        params = inspect.getfullargspec(getattr(Api, name)).args[1:]  # self 제외
        hit = [p for p in params if p in _JS_UNSAFE_PARAM_NAMES]
        if hit:
            bad[name] = hit
    assert bad == {}
