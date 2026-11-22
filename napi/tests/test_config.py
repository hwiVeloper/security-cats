import dataclasses
import json
import os

import pytest

from napi.config import ConfigError, load_config, save_config, validate_config
from napi.models import (
    AppConfig, CustomFormat, LogFormatDef, LogPath, Note, ServerConfig, ServerScanResult,
    FileStat, ServiceConfig, SpecSource,
)


def make_service(name="결제", **kw):
    return ServiceConfig(
        name=name,
        spec=SpecSource(type="openapi", source="url", location="http://x/api-docs"),
        servers=[
            ServerConfig(name="web1", host="10.0.0.1", user="app", log_paths=[LogPath("/var/log/web/access.log*")]),
            ServerConfig(name="web2", host="10.0.0.2", user="app", log_paths=[LogPath("/var/log/web/access.log*")],
                         auth="key", key_file="C:/keys/id_rsa"),
        ],
        notes={"GET /v1/settlements/yearly": Note("연 1회 정산용 (1월)", "홍길동", "2026-11-19")},
        **kw,
    )


def test_roundtrip(tmp_path):
    p = tmp_path / "c.yaml"
    cfg = AppConfig(services=[make_service()])
    save_config(cfg, p)
    assert load_config(p) == cfg
    assert "연 1회" in p.read_text(encoding="utf-8")


def test_no_password_field_written(tmp_path):
    p = tmp_path / "c.yaml"
    save_config(AppConfig(services=[make_service()]), p)
    assert "password:" not in p.read_text(encoding="utf-8")


def test_missing_file_returns_empty(tmp_path):
    assert load_config(tmp_path / "none.yaml") == AppConfig(services=[])


def test_defaults_applied(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "services:\n- name: a\n  spec: {location: x}\n  servers:\n"
        "  - {name: s, host: h, user: u, log_glob: /a/b}\n",
        encoding="utf-8",
    )
    svc = load_config(p).services[0]
    assert svc.thresholds.awake_days == 30
    assert svc.thresholds.min_coverage_days == 90


def test_invalid_yaml_raises_config_error(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("services: [\n", encoding="utf-8")
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert "설정 파일" in str(e.value)


def _set_globs(svc, globs):
    svc.servers[0].log_paths = [LogPath(g) for g in globs]


def _errs(svc_mut):
    svc = make_service()
    svc_mut(svc)
    return validate_config(AppConfig(services=[svc]))


def test_validate():
    assert validate_config(AppConfig(services=[make_service()])) == []
    assert validate_config(AppConfig(services=[make_service(), make_service()]))

    assert _errs(lambda s: setattr(s.servers[0], "host", ""))
    assert _errs(lambda s: setattr(s.servers[0], "auth", "key"))  # key_file 없음
    assert _errs(lambda s: setattr(s.servers[0].log_paths[0], "log_format", "custom"))
    assert not _errs(lambda s: _set_globs(s, ["/var/*/log/access.log*"]))  # 디렉터리 와일드카드 허용
    assert _errs(lambda s: _set_globs(s, ["var/log/access.log"]))
    # 정상 custom
    ok = make_service()
    ok.servers[0].log_paths[0].log_format = "custom"
    ok.servers[0].log_paths[0].custom_format = CustomFormat(6, 7, 9, 4, "clf")
    assert validate_config(AppConfig(services=[ok])) == []


def test_scan_result_dates():
    r = ServerScanResult("s", True, files=[
        FileStat("a", "20260101", "20260301", 1, 0, 0),
        FileStat("b", None, None, 0, 0, 0),
        FileStat("c", "20251201", "20260201", 1, 0, 0),
    ])
    assert r.oldest_date == "20251201"
    assert r.newest_date == "20260301"
    assert ServerScanResult("s", True).oldest_date is None


def test_non_utf8_file_raises_config_error(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_bytes("services: []\n# 한글 주석\n".encode("cp949"))
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert "설정 파일" in str(e.value)


def test_unreadable_path_raises_config_error(tmp_path):
    d = tmp_path / "dir.yaml"
    d.mkdir()  # 디렉터리를 파일처럼 읽으면 OSError
    with pytest.raises(ConfigError) as e:
        load_config(d)
    assert "설정 파일" in str(e.value)


# 설계 문서 §5.2의 예시 그대로 (date가 따옴표 없이 쓰여 YAML에서 날짜가 된다)
SPEC_EXAMPLE_YAML = """version: 1
services:
  - name: 라운드페이 API
    spec:
      type: openapi          # openapi (OpenAPI 3.x / Swagger 2.0 자동 판별)
      source: url            # url | file
      location: https://test-roundpay.example.local/v3/api-docs
    path_mapping:            # 로그 경로 → 명세 경로로 맞추기 위한 보정
      strip_prefix: /roundpay
      add_prefix: ""
    thresholds:
      awake_days: 30         # 최근 N일 내 호출 → 깨어있음
      min_coverage_days: 90  # 로그 기간이 이보다 짧은 서버가 있으면 신뢰도 낮음
    exclude_paths: [/health, /actuator/**]
    servers:
      - name: WAS-01
        host: 10.0.0.11
        port: 22
        user: webtob
        auth: password       # password | key
        key_file: null
        log_glob: /webtob/log/access.log*
        log_format: webtob   # apache_common | apache_combined | nginx_combined | webtob | custom
        custom_format: null  # custom일 때 필드 정의 (5.5 참고)
    notes:
      "GET /v1/settlements/yearly":
        reason: 연 1회 정산용 (1월)
        author: 홍길동
        date: 2026-11-19
"""


def test_spec_example_yaml_loads_with_unquoted_date(tmp_path):
    p = tmp_path / "napi-config.yaml"
    p.write_text(SPEC_EXAMPLE_YAML, encoding="utf-8")
    cfg = load_config(p)
    svc = cfg.services[0]
    assert svc.notes["GET /v1/settlements/yearly"] == Note("연 1회 정산용 (1월)", "홍길동", "2026-11-19")
    assert svc.servers[0].port == 22 and svc.servers[0].key_file is None
    assert svc.exclude_paths == ["/health", "/actuator/**"]
    json.dumps(dataclasses.asdict(cfg), ensure_ascii=False)  # get_state/save_result가 하는 일
    assert validate_config(cfg) == []
    save_config(cfg, p)
    assert load_config(p) == cfg


def _write(tmp_path, body):
    p = tmp_path / "c.yaml"
    p.write_text(body, encoding="utf-8")
    return p


BASE = ("services:\n- name: {name}\n  spec: {{location: x}}\n"
        "  thresholds: {{awake_days: {awake}, min_coverage_days: 90}}\n"
        "  servers:\n  - {{name: {sv}, host: 10.0.0.1, user: {user}, log_glob: /a/b, port: {port}}}\n"
        "settings: {{max_rows: {rows}, default_window: {window}}}\n")


def test_ints_coerced_and_strings_stay_strings(tmp_path):
    p = _write(tmp_path, BASE.format(name=2024, awake='"45"', sv=1, user=1000, port='"2222"',
                                     rows="1000", window=365))
    cfg = load_config(p)
    svc, sv = cfg.services[0], cfg.services[0].servers[0]
    assert (svc.name, sv.name, sv.user, sv.host) == ("2024", "1", "1000", "10.0.0.1")
    assert (sv.port, svc.thresholds.awake_days, cfg.settings.max_rows) == (2222, 45, 1000)
    assert cfg.settings.default_window == "365"


@pytest.mark.parametrize("field,value", [
    ("port", "abc"), ("port", "true"), ("port", "22.5"), ("awake", "[1]"), ("rows", "ten"),
])
def test_non_integer_raises_korean_config_error(tmp_path, field, value):
    kw = dict(name="a", awake=30, sv="s", user="u", port=22, rows=50000, window='"365"')
    kw[field] = value
    with pytest.raises(ConfigError) as e:
        load_config(_write(tmp_path, BASE.format(**kw)))
    assert "정수" in str(e.value)


def test_non_mapping_entry_raises_config_error(tmp_path):
    p = _write(tmp_path, "services:\n- name: a\n  spec: {location: x}\n  servers: []\n"
                         "  notes: {\"GET /a\": 사유만}\n")
    with pytest.raises(ConfigError, match="설정 파일 내용이 올바르지 않습니다"):
        load_config(p)


def test_validate_integer_ranges():
    def errs(mut):
        cfg = AppConfig(services=[make_service()])
        mut(cfg)
        return validate_config(cfg)

    assert errs(lambda c: None) == []
    assert any("포트" in m for m in errs(lambda c: setattr(c.services[0].servers[0], "port", 0)))
    assert any("포트" in m for m in errs(lambda c: setattr(c.services[0].servers[0], "port", 70000)))
    assert any("기준값" in m for m in errs(lambda c: setattr(c.services[0].thresholds, "awake_days", 0)))
    assert any("기준값" in m
               for m in errs(lambda c: setattr(c.services[0].thresholds, "min_coverage_days", 0)))
    for name in ("max_parallel_servers", "remote_timeout_sec", "max_rows"):
        assert any(name in m for m in errs(lambda c: setattr(c.settings, name, 0))), name
    assert errs(lambda c: setattr(c.settings, "top_404", 0)) == []
    assert any("top_404" in m for m in errs(lambda c: setattr(c.settings, "top_404", -1)))


def test_save_config_is_atomic(tmp_path, monkeypatch):
    p = tmp_path / "c.yaml"
    save_config(AppConfig(services=[make_service()]), p)
    before = p.read_text(encoding="utf-8")

    def boom(src, dst):
        raise OSError("disk full")

    monkeypatch.setattr(os, "replace", boom)
    with pytest.raises(OSError):
        save_config(AppConfig(services=[]), p)
    assert p.read_text(encoding="utf-8") == before
    assert [f.name for f in tmp_path.iterdir()] == ["c.yaml"]  # 임시 파일이 남지 않는다


USER_FORMATS = [
    LogFormatDef("webtob_error_req", '[%{%Y-%m-%dT%H:%M:%S}t] [CLIENT(%h)] "%r" %s'),
    LogFormatDef("method_u", "%{%d/%b/%Y:%H:%M:%S}t %m %U ?%q %s"),
]


def test_log_formats_roundtrip(tmp_path):
    p = tmp_path / "c.yaml"
    svc = make_service()
    svc.servers[0].log_paths[0].log_format = "webtob_error_req"
    cfg = AppConfig(services=[svc], log_formats=list(USER_FORMATS))
    save_config(cfg, p)
    loaded = load_config(p)
    assert loaded == cfg
    assert loaded.log_formats == USER_FORMATS
    assert validate_config(loaded) == []


def test_log_formats_default_empty(tmp_path):
    p = _write(tmp_path, "services: []\n")
    assert load_config(p).log_formats == []


@pytest.mark.parametrize("body", [
    "log_formats: abc\nservices: []\n",
    "log_formats: [abc]\nservices: []\n",
    "log_formats: [{name: a}]\nservices: []\n",
    "log_formats: [{name: a, pattern: b, extra: c}]\nservices: []\n",
])
def test_bad_log_formats_raise_config_error(tmp_path, body):
    with pytest.raises(ConfigError, match="설정 파일"):
        load_config(_write(tmp_path, body))


def test_log_formats_scalars_become_strings(tmp_path):
    p = _write(tmp_path, "log_formats: [{name: 2024, pattern: '%t \"%r\"'}]\nservices: []\n")
    assert load_config(p).log_formats == [LogFormatDef("2024", '%t "%r"')]


def test_validate_log_formats():
    def errs(formats, server_format="webtob"):
        svc = make_service()
        svc.servers[0].log_paths[0].log_format = server_format
        return validate_config(AppConfig(services=[svc], log_formats=formats))

    assert errs(list(USER_FORMATS), "method_u") == []
    assert any("기본 제공 형식 이름" in m
               for m in errs([LogFormatDef("webtob", '%t "%r"')]))
    assert any("기본 제공 형식 이름" in m
               for m in errs([LogFormatDef("custom", '%t "%r"')]))
    assert any("중복" in m for m in errs([USER_FORMATS[0], USER_FORMATS[0]]))
    bad = errs([LogFormatDef("broken", "%h%l %t \"%r\"")])
    assert any("broken" in m and "구분 문자" in m for m in bad)
    assert any("형식 이름" in m for m in errs([LogFormatDef(" ", '%t "%r"')]))
    unknown = errs(list(USER_FORMATS), "nope")
    assert any("모르는 로그 형식" in m and "nope" in m for m in unknown)
    # 기본 제공 형식과 custom(+필드 번호)은 사용자 형식 없이도 알려진 이름이다
    assert errs([], "webtob_combined") == []


# ---- Task 3: 로그 경로 여러 개 ----

def test_server_config_field_order():
    assert [f.name for f in dataclasses.fields(ServerConfig)] == [
        "name", "host", "user", "log_paths", "port", "auth", "key_file"]
    assert [f.name for f in dataclasses.fields(LogPath)] == [
        "glob", "log_format", "custom_format"]


def test_config_reads_legacy_log_glob(tmp_path):
    p = _write(tmp_path, "services:\n- name: a\n  spec: {location: x}\n  servers:\n"
                         "  - {name: s, host: h, user: u, log_glob: /x/a.log*}\n")
    cfg = load_config(p)
    assert cfg.services[0].servers[0].log_paths == [LogPath("/x/a.log*")]
    save_config(cfg, p)
    text = p.read_text(encoding="utf-8")
    assert "log_paths" in text
    assert "log_glob" not in text
    assert load_config(p) == cfg


def test_config_log_globs_roundtrip(tmp_path):
    p = _write(tmp_path, "services:\n- name: a\n  spec: {location: x}\n  servers:\n"
                         "  - name: s\n    host: h\n    user: u\n"
                         "    log_globs: [/x/a.log*, /y/b.log*]\n")
    cfg = load_config(p)
    assert cfg.services[0].servers[0].log_paths == [LogPath("/x/a.log*"), LogPath("/y/b.log*")]
    save_config(cfg, p)
    assert load_config(p) == cfg


@pytest.mark.parametrize("server", [
    "{name: s, host: h, user: u, log_glob: /a, log_globs: [/b]}",
    "{name: s, host: h, user: u, log_globs: {a: 1}}",
])
def test_bad_log_globs_raise_config_error(tmp_path, server):
    p = _write(tmp_path, "services:\n- name: a\n  spec: {location: x}\n  servers:\n"
                         f"  - {server}\n")
    with pytest.raises(ConfigError) as e:
        load_config(p)
    assert "설정 파일" in str(e.value)


def test_log_globs_scalars_become_strings(tmp_path):
    p = _write(tmp_path, "services:\n- name: a\n  spec: {location: x}\n  servers:\n"
                         "  - {name: s, host: h, user: u, log_globs: /x/a.log}\n")
    assert load_config(p).services[0].servers[0].log_paths == [LogPath("/x/a.log")]


def test_validate_log_globs():
    errs = _errs(lambda s: _set_globs(s, []))
    assert len(errs) == 1 and "로그 경로를 하나 이상" in errs[0] and "web1" in errs[0]
    ok = ["/var/log/a/access.log*", "/var/log/b/access.log*"]
    assert _errs(lambda s: _set_globs(s, ok)) == []
    for bad in ("var/log/access.log",):
        errs = _errs(lambda s: _set_globs(s, [ok[0], bad]))
        assert len(errs) == 1 and bad in errs[0], (bad, errs)
    for good in ("/var/*/log/access.log*", "/var/lo?/x", "/var/[l]og/x"):  # 디렉터리 와일드카드 허용
        assert _errs(lambda s: _set_globs(s, [ok[0], good])) == [], good


# ---- 최종 리뷰 D1: split_glob 규칙과 제어 문자 / D2: // . .. 가 든 디렉터리 ----

SL = "서비스 '결제' / 서버 'web1': "


def _glob_errs(bad):
    return _errs(lambda s: _set_globs(s, ["/var/log/a/access.log*", bad]))


@pytest.mark.parametrize("bad", ["/var/log/", "/"])
def test_validate_applies_split_glob_rules(bad):
    errs = _glob_errs(bad)
    assert errs == [f"{SL}로그 경로를 쓸 수 없습니다 — 파일 이름 패턴이 비어 있어요: {bad}"]


@pytest.mark.parametrize("bad", [
    "/var/log/a\tb*", "/var/log/\x00x", "/var/lo\ng/a*", "/var/log/a\x1f", "/var/log/\x1bx*",
    "\x01/var/log/a*"])
def test_validate_rejects_control_chars_in_glob(bad):
    assert _glob_errs(bad) == [f"{SL}로그 경로에 제어 문자를 쓸 수 없습니다: {bad}"]


@pytest.mark.parametrize("bad", [
    "/var//log/a*", "/var/./log/a*", "/var/../log/a*", "/var/log/./a*", "/var/log/../a*",
    "/var/log//a*", "//a*", "/./a*", "/../a*", "/var/log/..//a*"])
def test_validate_rejects_dot_segments_and_double_slash(bad):
    assert _glob_errs(bad) == [f"{SL}경로에 // 나 . / .. 를 쓸 수 없어요: {bad}"]


@pytest.mark.parametrize("ok", [
    "/var/log/.hidden*", "/var/.log/a*", "/var/log/..a*", "/var/..x/a*", "/var/x./a*",
    "/var/log/a..b", "/a*", "/var/log/access.log.1", "/var/log/ 공백/a*"])
def test_validate_accepts_dotted_names(ok):
    assert _glob_errs(ok) == []


def test_every_valid_glob_builds_a_pipeline():
    """validate_config를 통과한 glob은 스캔 때 build_pipeline이 거부하지 않는다 (D1)."""
    from napi.models import Settings
    from napi.remote.command import build_pipeline
    candidates = ["/var/log/", "/", "/a*", "/var/log/a*", "/var//log/a*", "/var/log/a\tb",
                  "/var/l*g/a", "/var/log/.", "/x/y/z/[ab]*.log", "/var/log/a\x7f*"]
    for glob in candidates:
        svc = make_service()
        _set_globs(svc, [glob])
        if validate_config(AppConfig(services=[svc])) == []:
            build_pipeline(svc.servers[0], Settings(), None, "napi-scan-00c0ffee")


# ---- 경로별 로그 형식 (log_paths) ----

def _yaml_server(body: str) -> str:
    return "services:\n- name: a\n  spec: {location: x}\n  servers:\n" + body


def test_config_legacy_to_log_paths(tmp_path):
    from napi.models import LogPath
    # v1: log_glob 하나 + 서버 log_format
    p = _write(tmp_path, _yaml_server(
        "  - {name: s, host: h, user: u, log_glob: /x/a.log*, log_format: webtob}\n"))
    cfg = load_config(p)
    assert cfg.services[0].servers[0].log_paths == [LogPath("/x/a.log*", "webtob", None)]
    save_config(cfg, p)
    text = p.read_text(encoding="utf-8")
    assert "log_paths:" in text
    for legacy in ("log_glob:", "log_globs:", "\n    log_format:", "\n    custom_format:"):
        assert legacy not in text, legacy
    assert load_config(p) == cfg
    # 확장: log_globs 2개 + 서버 log_format: custom + custom_format → 경로마다 물려받는다
    p2 = tmp_path / "c2.yaml"
    p2.write_text(_yaml_server(
        "  - name: s\n    host: h\n    user: u\n    log_globs: [/x/a.log*, /y/b.log*]\n"
        "    log_format: custom\n"
        "    custom_format: {method_field: 6, path_field: 7, status_field: 9,"
        " time_field: 4, time_format: clf}\n"), encoding="utf-8")
    cfg2 = load_config(p2)
    cf = CustomFormat(6, 7, 9, 4, "clf")
    paths = cfg2.services[0].servers[0].log_paths
    assert paths == [LogPath("/x/a.log*", "custom", cf), LogPath("/y/b.log*", "custom", cf)]
    assert paths[0].custom_format is not paths[1].custom_format  # 따로 고칠 수 있다
    assert validate_config(cfg2) == []
    save_config(cfg2, p2)
    text2 = p2.read_text(encoding="utf-8")
    assert "log_globs" not in text2 and "log_paths:" in text2
    assert set(yaml_server_keys(p2)) == {"name", "host", "user", "log_paths", "port", "auth",
                                         "key_file"}
    assert load_config(p2) == cfg2
    # 서버 형식이 없으면 webtob
    p3 = tmp_path / "c3.yaml"
    p3.write_text(_yaml_server("  - {name: s, host: h, user: u, log_globs: [/x/a]}\n"),
                  encoding="utf-8")
    assert load_config(p3).services[0].servers[0].log_paths == [LogPath("/x/a")]


def yaml_server_keys(p):
    import yaml
    return yaml.safe_load(p.read_text(encoding="utf-8"))["services"][0]["servers"][0].keys()


def test_config_log_paths_roundtrip_and_validate(tmp_path):
    from napi.models import LogPath
    p = _write(tmp_path, _yaml_server(
        "  - name: s\n    host: h\n    user: u\n    log_format: method_u\n"
        "    log_paths:\n"
        "    - /x/plain.log*\n"  # 문자열 하나 = glob, 서버 기본 형식
        "    - {glob: /y/a.log*, log_format: webtob_error_req}\n"
        "    - {glob: /z/c.log, log_format: custom, custom_format: {method_field: 2,"
        " path_field: 3, status_field: 4, time_field: 1, time_format: iso8601}}\n"
        "    - {glob: /w/d.log}\n"
        "log_formats:\n"
        "- {name: webtob_error_req, pattern: '[%{%Y-%m-%dT%H:%M:%S}t] [CLIENT(%h)] \"%r\" %s'}\n"
        "- {name: method_u, pattern: '%{%d/%b/%Y:%H:%M:%S}t %m %U ?%q %s'}\n"))
    cfg = load_config(p)
    assert cfg.services[0].servers[0].log_paths == [
        LogPath("/x/plain.log*", "method_u"),
        LogPath("/y/a.log*", "webtob_error_req"),
        LogPath("/z/c.log", "custom", CustomFormat(2, 3, 4, 1, "iso8601")),
        LogPath("/w/d.log", "method_u"),
    ]
    assert validate_config(cfg) == []
    save_config(cfg, p)
    assert load_config(p) == cfg
    # 경로 하나만 모르는 형식 → 그 glob을 담은 오류
    cfg.services[0].servers[0].log_paths[1].log_format = "nope"
    assert validate_config(cfg) == [
        "서비스 'a' / 서버 's': 경로 /y/a.log*: 모르는 로그 형식이에요: nope"]
    cfg.services[0].servers[0].log_paths[1].log_format = "custom"
    errs = validate_config(cfg)
    assert len(errs) == 1 and "경로 /y/a.log*" in errs[0] and "custom" in errs[0]


@pytest.mark.parametrize("server", [
    "{name: s, host: h, user: u, log_glob: /a, log_paths: [/b]}",
    "{name: s, host: h, user: u, log_globs: [/a], log_paths: [/b]}",
    "{name: s, host: h, user: u, log_paths: {glob: /a}}",
    "{name: s, host: h, user: u, log_paths: [{log_format: webtob}]}",
    "{name: s, host: h, user: u, log_paths: [{glob: /a, extra: 1}]}",
    "{name: s, host: h, user: u, log_paths: [[/a]]}",
])
def test_bad_log_paths_raise_config_error(tmp_path, server):
    with pytest.raises(ConfigError, match="설정 파일"):
        load_config(_write(tmp_path, _yaml_server(f"  - {server}\n")))


def test_legacy_custom_format_only_inherited_by_custom_paths(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "services:\n- name: S\n  spec: {type: url, source: x, location: http://s}\n"
        "  servers:\n  - name: W\n    host: h\n    user: u\n"
        "    log_globs: [/a/*.log, /b/*.log]\n    log_format: webtob\n"
        "    custom_format: {method_field: 6, path_field: 7, status_field: 9,"
        " time_field: 4, time_format: clf}\n", encoding="utf-8")
    paths = load_config(p).services[0].servers[0].log_paths
    assert [x.custom_format for x in paths] == [None, None]


def test_validate_rejects_wildcard_in_first_segment():
    errs = _glob_errs("/*/logs/a.log")
    assert len(errs) == 1 and "최상위" in errs[0] and "/*/logs/a.log" in errs[0]
    assert _glob_errs("/a*") == []  # 디렉터리 없이 파일 이름만이면 괜찮다


# ------------------------------------------------ api_scopes / probe_patterns


def test_scope_fields_roundtrip(tmp_path):
    p = tmp_path / "c.yaml"
    cfg = AppConfig(services=[make_service(api_scopes=["/legacy", "/internal/api"],
                                           probe_patterns=["**/*.php", "/cgi-bin/**"])])
    save_config(cfg, p)
    assert load_config(p) == cfg
    cfg.services[0].probe_patterns = []
    save_config(cfg, p)
    assert load_config(p).services[0].probe_patterns == []


def test_old_config_without_scope_fields(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "services:\n- name: a\n  spec: {location: x}\n  servers:\n"
        "  - {name: s, host: h, user: u, log_glob: /a/b}\n",
        encoding="utf-8",
    )
    svc = load_config(p).services[0]
    assert svc.api_scopes == [] and svc.probe_patterns is None


def test_probe_patterns_none_not_written(tmp_path):
    p = tmp_path / "c.yaml"
    save_config(AppConfig(services=[make_service()]), p)
    text = p.read_text(encoding="utf-8")
    assert "api_scopes: []" in text
    assert "probe_patterns" not in text
    assert load_config(p).services[0].probe_patterns is None


def test_scope_fields_scalars_become_strings(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text(
        "services:\n- name: a\n  spec: {location: x}\n  servers: []\n"
        "  api_scopes: /legacy\n  probe_patterns: [123]\n",
        encoding="utf-8",
    )
    svc = load_config(p).services[0]
    assert svc.api_scopes == ["/legacy"] and svc.probe_patterns == ["123"]


@pytest.mark.parametrize("body", ["api_scopes: {a: 1}", "probe_patterns: {a: 1}"])
def test_bad_scope_fields_raise_config_error(tmp_path, body):
    p = tmp_path / "c.yaml"
    p.write_text("services:\n- name: a\n  spec: {location: x}\n  servers: []\n  " + body + "\n",
                 encoding="utf-8")
    with pytest.raises(ConfigError):
        load_config(p)


@pytest.mark.parametrize("scope", ["legacy", "/a*", "/a/**", "/a?b", "/a//b", "", "/a\nb"])
def test_validate_rejects_bad_api_scopes(scope):
    errs = _errs(lambda s: setattr(s, "api_scopes", [scope]))
    assert len(errs) == 1 and "API 범위" in errs[0]


@pytest.mark.parametrize("scope", ["/legacy", "/internal/api", "/v2/"])
def test_validate_accepts_api_scopes(scope):
    assert _errs(lambda s: setattr(s, "api_scopes", [scope])) == []


@pytest.mark.parametrize("pat", ["", "   ", "**/*.php\t", "/a\nb", "\x00"])
def test_validate_rejects_bad_probe_patterns(pat):
    errs = _errs(lambda s: setattr(s, "probe_patterns", [pat]))
    assert len(errs) == 1 and "스캐너 패턴" in errs[0]


def test_validate_accepts_probe_patterns():
    assert _errs(lambda s: setattr(s, "probe_patterns", ["**/.*", "/cgi-bin/**"])) == []
    assert _errs(lambda s: setattr(s, "probe_patterns", [])) == []
    assert _errs(lambda s: setattr(s, "probe_patterns", None)) == []
