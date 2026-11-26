"""설정·결과 파일 경로."""
from __future__ import annotations

import os
from pathlib import Path


def app_dir() -> Path:
    home = os.environ.get("NAPI_HOME")
    if home:
        d = Path(home)
    else:
        appdata = os.environ.get("APPDATA")
        base = Path(appdata) if appdata else Path.home() / "AppData" / "Roaming"
        d = base / "napi"
    d.mkdir(parents=True, exist_ok=True)
    return d


def config_path() -> Path:
    return app_dir() / "napi-config.yaml"


def known_hosts_path() -> Path:
    return app_dir() / "known_hosts"


def results_dir() -> Path:
    d = app_dir() / "results"
    d.mkdir(parents=True, exist_ok=True)
    return d
