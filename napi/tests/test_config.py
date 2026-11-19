import dataclasses

import pytest

from napi.config import ConfigError, load_config, save_config, validate_config
from napi.models import (
    AppConfig, CustomFormat, Note, ServerConfig, ServerScanResult, FileStat,
    ServiceConfig, SpecSource,
)


def make_service(name="결제", **kw):
    return ServiceConfig(
        name=name,
        spec=SpecSource(type="openapi", source="url", location="http://x/api-docs"),
        servers=[
            ServerConfig(name="web1", host="10.0.0.1", user="app", log_glob="/var/log/web/access.log*"),
            ServerConfig(name="web2", host="10.0.0.2", user="app", log_glob="/var/log/web/access.log*",
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


def _errs(svc_mut):
    svc = make_service()
    svc_mut(svc)
    return validate_config(AppConfig(services=[svc]))


def test_validate():
    assert validate_config(AppConfig(services=[make_service()])) == []
    assert validate_config(AppConfig(services=[make_service(), make_service()]))

    assert _errs(lambda s: setattr(s.servers[0], "host", ""))
    assert _errs(lambda s: setattr(s.servers[0], "auth", "key"))  # key_file 없음
    assert _errs(lambda s: setattr(s.servers[0], "log_format", "custom"))
    assert _errs(lambda s: setattr(s.servers[0], "log_glob", "/var/*/log/access.log*"))
    assert _errs(lambda s: setattr(s.servers[0], "log_glob", "var/log/access.log"))
    # 정상 custom
    ok = make_service()
    ok.servers[0].log_format = "custom"
    ok.servers[0].custom_format = CustomFormat(6, 7, 9, 4, "clf")
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
