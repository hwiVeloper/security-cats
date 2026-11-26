import ipaddress
import os

import pytest

from conftest import FIX, XFF_FORMAT, kst
from iptrace.core import engine, history, ioc
from iptrace.core.matcher import Matcher
from iptrace.core.models import CheckRequest, IocEntry
from iptrace.core.parser import detect_format


def _run(cfg):
    res = ioc.parse_text(open(os.path.join(FIX, "ioc_typed.list"), encoding="utf-8").read(),
                         cfg["general"]["own_networks"])
    req = CheckRequest(iocs=res.entries, start=kst(2026, 7, 1), end=kst(2026, 10, 31),
                       local_logs=[("web01", os.path.join(FIX, "access_sample.log"))], log_format="test_xff")
    return engine.run_check(req, cfg)


def test_history_saves_summary_without_raw_log(cfg):
    result = _run(cfg)
    hid = history.save_result(result, "tester")
    rec = history.get(hid)
    assert rec["review"] == "검토 전" and rec["user"] == "tester"
    assert rec["iocs"][0] == "203.0.113.45" and rec["rule_version"] == result.rule_version
    row = next(r for r in rec["results"] if r["ip"] == "203.0.113.45")
    assert row["grade"] == "CRITICAL" and row["evidence"][0][2] == 7
    blob = str(rec)
    for raw in open(os.path.join(FIX, "access_sample.log"), encoding="utf-8").read().splitlines():
        assert raw not in blob  # 로그 원문 줄은 저장하지 않는다
    assert "/upload/files/x.jsp?cmd=whoami" not in blob and "UNION%20SELECT" not in blob
    history.update_review(hid, "확인 완료", "조치 불필요")
    assert history.get(hid)["memo"] == "조치 불필요"
    # 같은 결과를 다시 저장(재점검 후 합침)하면 새 행이 아니라 갱신
    assert history.save_result(result) == hid and len(history.list_checks()) == 1
    ih = history.ip_history("203.0.113.45")
    assert ih["count"] == 1 and len(ih["hits"]) == 1
    assert history.list_checks("8.8.8.8") == []
    with pytest.raises(ValueError):
        history.update_review(hid, "아무거나", "")
    history.delete(hid)
    assert history.get(hid) is None


def test_detect_format_prefers_matching_and_detailed(cfg):
    lines = open(os.path.join(FIX, "access_sample.log"), encoding="utf-8").read().splitlines()[:5]
    best = detect_format(lines, cfg)[0]
    assert best[1] == XFF_FORMAT or best[1].endswith('"%{X-Forwarded-For}i"')
    assert best[2] == 5


def _e(s):
    return IocEntry(network=ipaddress.ip_network(s, strict=False), original=s, line_no=1)


def test_filter_strings():
    assert Matcher.build_filter_strings([_e("203.0.113.45"), _e("203.0.113.0/28"), _e("10.0.0.0/8")]) == \
        ["203.0.113.45", "203.0.113.", "10."]
    assert Matcher.build_filter_strings([_e("2001:db8::/32")]) == ["2001:db8:"]
    assert Matcher.build_filter_strings([_e("1.0.0.0/4")]) is None


def test_wide_range_cannot_be_checked_on_server(cfg):
    req = CheckRequest(iocs=[_e("1.0.0.0/4")], start=kst(2026, 1, 1), end=kst(2026, 2, 1),
                       servers=[{"name": "a", "host": "h", "format": "test_xff"}])
    with pytest.raises(ValueError, match="넓은 대역"):
        engine.run_check(req, cfg)


def test_csv_fallback_without_openpyxl(cfg, tmp_path, monkeypatch):
    """openpyxl이 없는 내부망 PC에서도 저장이 된다 (CSV로)."""
    from iptrace.core import report
    result = _run(cfg)
    monkeypatch.setattr(report, "has_xlsx", lambda: False)
    kind, saved = report.save(result, tmp_path / "r.xlsx")
    assert kind == "csv" and len(saved) == 5 and all(os.path.exists(p) for p in saved)
    assert os.path.dirname(saved[0]).endswith("r_csv")


def test_ssh_without_paramiko_gives_clear_message(monkeypatch):
    import sys
    from iptrace.core.collector import CollectError, SshSource
    monkeypatch.setitem(sys.modules, "paramiko", None)
    src = SshSource({"name": "a", "host": "h"}, {"user": "u"}, "pw")
    with pytest.raises(CollectError, match="paramiko"):
        src.connect()
