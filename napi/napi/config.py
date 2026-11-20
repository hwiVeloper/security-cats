"""설정 파일(YAML) 로드/저장/검증."""
from __future__ import annotations

import os
import re
import tempfile
from dataclasses import asdict
from datetime import date, datetime
from pathlib import Path

import yaml

from .logformat import BUILTIN_FORMATS, FormatError, compile_format
from .models import (
    AppConfig, CustomFormat, LogFormatDef, Note, PathMapping, ServerConfig, ServiceConfig,
    Settings, SpecSource, Thresholds,
)


class ConfigError(Exception):
    pass


def _to_dict(cfg: AppConfig) -> dict:
    # asdict는 중첩 dataclass와 None 값(custom_format, key_file)을 키째 유지한다.
    return asdict(cfg)


_INT_RE = re.compile(r"[+-]?[0-9]+")


def _int(value, name: str) -> int:
    """손으로 고친 설정의 정수 칸. 정수와 숫자 문자열("22")만 받는다."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and _INT_RE.fullmatch(value.strip()):
        return int(value.strip())
    raise ConfigError(f"설정 파일의 {name} 값은 정수여야 합니다: {value!r}")


def _str(value) -> str:
    """문자열 칸. YAML이 숫자·날짜로 읽은 값(예: date: 2026-11-19)도 문자열로 되돌린다."""
    if value is None:
        return ""
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    return str(value)


def _fields(d, ints: tuple[str, ...] = (), strs: tuple[str, ...] = (),
            optional_strs: tuple[str, ...] = ()) -> dict:
    """dataclass에 넘기기 전에 알려진 칸의 형을 맞춘다. 모르는 키는 그대로 둬서
    dataclass 생성이 TypeError(→ ConfigError)를 내게 한다."""
    if d is not None and not isinstance(d, dict):
        raise ConfigError(f"설정 파일 내용이 올바르지 않습니다: 매핑이 와야 할 자리에 {d!r}")
    out = dict(d or {})
    for k in ints:
        if k in out:
            out[k] = _int(out[k], k)
    for k in strs:
        if k in out:
            out[k] = _str(out[k])
    for k in optional_strs:
        if k in out and out[k] is not None:
            out[k] = _str(out[k])
    return out


_SERVER_STRS = ("name", "host", "user", "log_glob", "auth", "log_format")
_SETTINGS_INTS = ("max_parallel_servers", "remote_timeout_sec", "max_rows", "top_404")


def _log_formats(items) -> list[LogFormatDef]:
    if items is None:
        return []
    if not isinstance(items, list):
        raise ConfigError(f"설정 파일 내용이 올바르지 않습니다: log_formats는 목록이어야 합니다: {items!r}")
    return [LogFormatDef(**_fields(f, strs=("name", "pattern"))) for f in items]


def _from_dict(d: dict) -> AppConfig:
    if not isinstance(d, dict):
        raise ConfigError("설정 파일 형식이 올바르지 않습니다. (최상위가 매핑이 아님)")
    try:
        services = []
        for s in d.get("services") or []:
            servers = []
            for sv in s.get("servers") or []:
                sv = _fields(sv, ints=("port",), strs=_SERVER_STRS, optional_strs=("key_file",))
                cf = sv.pop("custom_format", None)
                servers.append(ServerConfig(
                    **sv, **({"custom_format": CustomFormat(**cf)} if cf else {})))
            services.append(ServiceConfig(
                name=_str(s["name"]),
                spec=SpecSource(**_fields(s.get("spec"), strs=("type", "source", "location"))),
                servers=servers,
                path_mapping=PathMapping(**_fields(s.get("path_mapping"),
                                                   strs=("strip_prefix", "add_prefix"))),
                thresholds=Thresholds(**_fields(s.get("thresholds"),
                                                ints=("awake_days", "min_coverage_days"))),
                exclude_paths=[_str(x) for x in (s.get("exclude_paths") or [])],
                notes={_str(k): Note(**_fields(v, strs=("reason", "author", "date")))
                       for k, v in (s.get("notes") or {}).items()},
            ))
        return AppConfig(
            services=services,
            settings=Settings(**_fields(d.get("settings"), ints=_SETTINGS_INTS,
                                        strs=("default_window",))),
            version=d.get("version", 1),
            log_formats=_log_formats(d.get("log_formats")),
        )
    except (TypeError, KeyError, AttributeError) as e:
        raise ConfigError(f"설정 파일 내용이 올바르지 않습니다: {e}") from e


def load_config(path: Path) -> AppConfig:
    path = Path(path)
    if not path.exists():
        return AppConfig(services=[])
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        raise ConfigError(f"설정 파일을 읽을 수 없습니다 ({path}): {e}") from e
    except UnicodeDecodeError as e:
        raise ConfigError(
            f"설정 파일의 인코딩이 UTF-8이 아닙니다 ({path}). UTF-8로 저장해 주세요: {e}") from e
    except OSError as e:
        raise ConfigError(f"설정 파일을 열 수 없습니다 ({path}): {e}") from e
    if data is None:
        return AppConfig(services=[])
    return _from_dict(data)


def save_config(cfg: AppConfig, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(_to_dict(cfg), allow_unicode=True, sort_keys=False)
    # 같은 폴더의 임시 파일에 쓰고 바꿔 끼운다 (쓰다가 실패해도 원래 파일은 그대로).
    fd, tmp = tempfile.mkstemp(dir=path.parent, prefix=path.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def _below(value, minimum: int) -> bool:
    return isinstance(value, bool) or not isinstance(value, int) or value < minimum


_SETTINGS_MIN = {"max_parallel_servers": 1, "remote_timeout_sec": 1, "max_rows": 1, "top_404": 0}


def _validate_log_formats(formats: list[LogFormatDef]) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for f in formats:
        label = f"로그 형식 '{f.name}'"
        if not f.name.strip():
            errors.append("로그 형식 이름이 비어 있습니다.")
        elif f.name in BUILTIN_FORMATS or f.name == "custom":
            errors.append(f"{label}: 기본 제공 형식 이름과 같아서 쓸 수 없습니다.")
        elif f.name in seen:
            errors.append(f"{label}: 형식 이름이 중복되었습니다.")
        seen.add(f.name)
        try:
            compile_format(f.pattern)
        except FormatError as e:
            errors.append(f"{label}: {e}")
    return errors


def validate_config(cfg: AppConfig) -> list[str]:
    errors: list[str] = []
    for name, minimum in _SETTINGS_MIN.items():
        if _below(getattr(cfg.settings, name), minimum):
            errors.append(f"설정 {name}은(는) {minimum} 이상의 정수여야 합니다.")
    errors += _validate_log_formats(cfg.log_formats)
    known_formats = {*BUILTIN_FORMATS, "custom", *(f.name for f in cfg.log_formats)}
    seen: set[str] = set()
    for svc in cfg.services:
        label = f"서비스 '{svc.name}'"
        if not svc.name.strip():
            errors.append("서비스 이름이 비어 있습니다.")
        if svc.name in seen:
            errors.append(f"{label}: 서비스 이름이 중복되었습니다.")
        seen.add(svc.name)
        for th in ("awake_days", "min_coverage_days"):
            if _below(getattr(svc.thresholds, th), 1):
                errors.append(f"{label}: 기준값 {th}은(는) 1 이상의 정수여야 합니다.")
        for sv in svc.servers:
            sl = f"{label} / 서버 '{sv.name}'"
            if not sv.host.strip():
                errors.append(f"{sl}: 호스트가 비어 있습니다.")
            if _below(sv.port, 1) or sv.port > 65535:
                errors.append(f"{sl}: 포트는 1~65535 사이의 정수여야 합니다.")
            if sv.auth == "key" and not sv.key_file:
                errors.append(f"{sl}: 키 인증인데 키 파일이 지정되지 않았습니다.")
            if sv.log_format not in known_formats:
                errors.append(f"{sl}: 모르는 로그 형식이에요: {sv.log_format}")
            if sv.log_format == "custom" and sv.custom_format is None:
                errors.append(f"{sl}: 로그 형식이 custom인데 사용자 정의 형식이 없습니다.")
            if not sv.log_glob.startswith("/"):
                errors.append(f"{sl}: 로그 경로는 '/'로 시작해야 합니다.")
            elif "*" in sv.log_glob.rsplit("/", 1)[0]:
                errors.append(f"{sl}: 로그 경로의 디렉터리 부분에는 '*'를 쓸 수 없습니다.")
    return errors
