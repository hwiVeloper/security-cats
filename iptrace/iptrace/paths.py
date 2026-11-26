"""실행 위치 기준 경로.

exe(PyInstaller)로 실행되면 exe 폴더, 소스로 실행되면 프로젝트 루트를 기준으로 한다.
설정·규칙·로그·출력 폴더는 모두 이 기준 폴더 아래에 둔다.
"""
import shutil
import sys
from pathlib import Path


def base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def bundled_dir() -> Path:
    """exe 안에 함께 묶인 기본 파일 위치 (소스 실행 시에는 프로젝트 루트)."""
    return Path(getattr(sys, "_MEIPASS", base_dir()))


BASE = base_dir()
CONFIG_DIR = BASE / "config"
CONFIG_FILE = CONFIG_DIR / "config.json"
KNOWN_HOSTS = CONFIG_DIR / "known_hosts"
RULES_DIR = BASE / "rules"
LOGS_DIR = BASE / "logs"
OUT_DIR = BASE / "out"


def resolve(p: str) -> Path:
    """설정에 적힌 상대 경로를 기준 폴더 기준 절대 경로로 바꾼다."""
    path = Path(p).expanduser()
    return path if path.is_absolute() else BASE / path


def _rules_version(path: Path) -> str:
    try:
        import json
        with open(path, encoding="utf-8-sig") as f:
            return str(json.load(f).get("version", ""))
    except (OSError, ValueError):
        return ""


def ensure_runtime_files() -> None:
    """기본 규칙 파일을 기준 폴더로 복사하고 필요한 폴더를 만든다.

    처음 실행이면 복사한다. 이미 있는데 exe에 든 규칙이 더 새 버전이면(예: 2026.10.23 -> 2026.10.26)
    지금 파일을 signatures.json.bak-<예전 버전>으로 남기고 새 규칙으로 바꾼다 (직접 고친 규칙은 백업에 있다).
    """
    for d in (CONFIG_DIR, RULES_DIR, LOGS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    src = bundled_dir() / "rules" / "signatures.json"
    dst = RULES_DIR / "signatures.json"
    if not src.exists() or src.resolve() == dst.resolve():
        return
    if not dst.exists():
        shutil.copyfile(src, dst)
        return
    old, new = _rules_version(dst), _rules_version(src)
    if new and old < new:
        shutil.copyfile(dst, RULES_DIR / f"signatures.json.bak-{old or 'old'}")
        shutil.copyfile(src, dst)
