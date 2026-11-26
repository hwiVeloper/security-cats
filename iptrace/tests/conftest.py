import json
import os
import sys
from datetime import datetime

import pytest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from iptrace import paths  # noqa: E402
from iptrace.core import config as cfgmod  # noqa: E402
from iptrace.core import history  # noqa: E402
from iptrace.core.engine import KST  # noqa: E402

FIX = os.path.join(ROOT, "tests", "fixtures")
XFF_FORMAT = '%h %l %u %t "%r" %s %b "%{Referer}i" "%{User-Agent}i" "%{X-Forwarded-For}i"'


@pytest.fixture(autouse=True)
def _isolate_logs(tmp_path, monkeypatch):
    """테스트 중 감사 로그가 프로젝트 logs 폴더에 쌓이지 않게 한다."""
    monkeypatch.setattr(paths, "LOGS_DIR", tmp_path / "logs")
    monkeypatch.setattr(history, "_db_path", lambda: tmp_path / "data" / "history.db")


@pytest.fixture
def cfg():
    c = cfgmod.normalize({
        "general": {"own_networks": ["10.0.0.0/8", "198.51.100.80/32"], "trusted_proxies": ["10.1.1.10"]},
        "detect": {"scan_404_threshold": 3},
        "log_formats": {"test_xff": XFF_FORMAT},
    })
    return c


@pytest.fixture
def cfg_file(tmp_path, cfg):
    p = tmp_path / "config.json"
    p.write_text(json.dumps(cfg, ensure_ascii=False), encoding="utf-8")
    return str(p)


def kst(*a):
    return datetime(*a, tzinfo=KST)
