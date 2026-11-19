"""설정 파일(YAML) 로드/저장/검증."""
from __future__ import annotations

from dataclasses import asdict
from pathlib import Path

import yaml

from .models import (
    AppConfig, CustomFormat, Note, PathMapping, ServerConfig, ServiceConfig,
    Settings, SpecSource, Thresholds,
)


class ConfigError(Exception):
    pass


def _to_dict(cfg: AppConfig) -> dict:
    # asdict는 중첩 dataclass와 None 값(custom_format, key_file)을 키째 유지한다.
    return asdict(cfg)


def _from_dict(d: dict) -> AppConfig:
    if not isinstance(d, dict):
        raise ConfigError("설정 파일 형식이 올바르지 않습니다. (최상위가 매핑이 아님)")
    try:
        services = []
        for s in d.get("services") or []:
            servers = []
            for sv in s.get("servers") or []:
                sv = dict(sv)
                cf = sv.pop("custom_format", None)
                servers.append(ServerConfig(
                    **sv, **({"custom_format": CustomFormat(**cf)} if cf else {})))
            services.append(ServiceConfig(
                name=s["name"],
                spec=SpecSource(**(s.get("spec") or {})),
                servers=servers,
                path_mapping=PathMapping(**(s.get("path_mapping") or {})),
                thresholds=Thresholds(**(s.get("thresholds") or {})),
                exclude_paths=list(s.get("exclude_paths") or []),
                notes={k: Note(**v) for k, v in (s.get("notes") or {}).items()},
            ))
        return AppConfig(
            services=services,
            settings=Settings(**(d.get("settings") or {})),
            version=d.get("version", 1),
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
    if data is None:
        return AppConfig(services=[])
    return _from_dict(data)


def save_config(cfg: AppConfig, path: Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    text = yaml.safe_dump(_to_dict(cfg), allow_unicode=True, sort_keys=False)
    path.write_text(text, encoding="utf-8", newline="\n")


def validate_config(cfg: AppConfig) -> list[str]:
    errors: list[str] = []
    seen: set[str] = set()
    for svc in cfg.services:
        label = f"서비스 '{svc.name}'"
        if not svc.name.strip():
            errors.append("서비스 이름이 비어 있습니다.")
        if svc.name in seen:
            errors.append(f"{label}: 서비스 이름이 중복되었습니다.")
        seen.add(svc.name)
        for sv in svc.servers:
            sl = f"{label} / 서버 '{sv.name}'"
            if not sv.host.strip():
                errors.append(f"{sl}: 호스트가 비어 있습니다.")
            if sv.auth == "key" and not sv.key_file:
                errors.append(f"{sl}: 키 인증인데 키 파일이 지정되지 않았습니다.")
            if sv.log_format == "custom" and sv.custom_format is None:
                errors.append(f"{sl}: 로그 형식이 custom인데 사용자 정의 형식이 없습니다.")
            if not sv.log_glob.startswith("/"):
                errors.append(f"{sl}: 로그 경로는 '/'로 시작해야 합니다.")
            elif "*" in sv.log_glob.rsplit("/", 1)[0]:
                errors.append(f"{sl}: 로그 경로의 디렉터리 부분에는 '*'를 쓸 수 없습니다.")
    return errors
