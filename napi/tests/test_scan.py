import threading
from datetime import date

import pytest

from napi.models import (
    AggRow, FileStat, LogPath, ServerConfig, ServerScanResult, ServiceConfig, Settings,
    SpecOperation, SpecSource, Status,
)
from napi.scan import ScanError, run_scan
from napi.spec import SpecError

TODAY = date(2026, 11, 19)


def ops():
    return [SpecOperation("GET", "/v1/a", None, [], None, False),
            SpecOperation("GET", "/v1/b", None, [], None, False)]


class FakeCollector:
    def __init__(self, results):
        self.results, self.calls = results, []

    def collect(self, servers, since, progress, cancel):
        self.calls.append((servers, since))
        return self.results


def cfg():
    return ServiceConfig(name="svc", spec=SpecSource(location="http://s"),
                         servers=[ServerConfig("W1", "h", "u", [LogPath("/l")]),
                                  ServerConfig("W2", "h", "u", [LogPath("/l")])])


def test_run_scan_end_to_end():
    ok = ServerScanResult(
        "W1", True,
        rows=[AggRow("GET", "/v1/a", "2xx", 4, "20260101", "20261115")],
        probe404=[AggRow("GET", "/wp-login", "4xx", 9, "20261101", "20261102")],
        files=[FileStat("/l", "20250101", "20261118", 10, 0, 0)], elapsed_sec=2.0)
    bad = ServerScanResult("W2", False, error="접속 실패",
                           rows=[AggRow("GET", "/v1/b", "2xx", 99, "20261101", "20261101")])
    col = FakeCollector([ok, bad])
    events = []
    r = run_scan(cfg(), Settings(), "365", col, lambda s, p: events.append((s, p)),
                 threading.Event(), today=TODAY, spec_loader=lambda src: ops())
    assert events[0] == ("*", "spec")
    assert col.calls[0][1] == "20251119"
    by = {a.path: a for a in r.apis}
    assert by["/v1/a"].status == Status.AWAKE
    assert by["/v1/b"].status == Status.ASLEEP  # 실패 서버 행은 매칭에서 제외
    assert by["/wp-login"].status == Status.PROBE
    assert r.counts["awake"] == 1 and r.counts["asleep"] == 1 and r.counts["probe"] == 1
    assert r.sleeping == 1 and r.sleeping_noted == 0
    assert r.summary.startswith("API 2개 중 1개가")
    assert (r.since, r.until, r.window) == ("20251119", "20261119", "365")
    assert r.spec_location == "http://s"
    assert r.reliability.level == "low"
    assert [(s.name, s.ok, s.oldest, s.newest) for s in r.servers] == [
        ("W1", True, "20250101", "20261118"), ("W2", False, None, None)]
    assert r.scanned_at[:4].isdigit() and "T" in r.scanned_at


def test_spec_error_propagates_before_collect():
    col = FakeCollector([])

    def bad(src):
        raise SpecError("명세 실패")

    with pytest.raises(SpecError):
        run_scan(cfg(), Settings(), "all", col, lambda s, p: None,
                 threading.Event(), today=TODAY, spec_loader=bad)
    assert col.calls == []


def test_cancelled_still_returns_result():
    cancel = threading.Event()
    cancel.set()
    s = ServerScanResult("W1", False, error="취소됨")
    r = run_scan(cfg(), Settings(), "all", FakeCollector([s]), lambda a, b: None,
                 cancel, today=TODAY, spec_loader=lambda src: ops())
    assert r.since is None and r.servers[0].error == "취소됨"


def run_with(servers, c=None):
    return run_scan(c or cfg(), Settings(), "365", FakeCollector(servers), lambda s, p: None,
                    threading.Event(), today=TODAY, spec_loader=lambda src: ops())


def test_no_server_with_logs_fails_scan():
    nofiles = ServerScanResult("W1", True, rows=[AggRow("GET", "/v1/a", "2xx", 1, "20261101", "20261101")])
    bad = ServerScanResult("W2", False, error="인증 실패")
    with pytest.raises(ScanError) as e:
        run_with([nofiles, bad])
    assert str(e.value) == ("로그를 가져온 서버가 없어 판정하지 않았어요 "
                            "(W1: 로그 파일을 찾지 못했어요, W2: 인증 실패)")


def test_zero_servers_fails_scan():
    c = cfg()
    c.servers = []
    with pytest.raises(ScanError) as e:
        run_with([], c)
    assert str(e.value) == "로그를 가져온 서버가 없어 판정하지 않았어요 (등록된 서버가 없어요)"


def test_one_server_with_logs_is_enough():
    ok = ServerScanResult("W1", True, files=[FileStat("/l", "20250101", "20261118", 3, 0, 0)])
    r = run_with([ok, ServerScanResult("W2", False, error="x")])
    assert r.reliability.level == "low"


def test_server_summary_carries_line_counts():
    ok = ServerScanResult("W1", True, files=[FileStat("/a", "20250101", "20261118", 10, 2, 0),
                                             FileStat("/b", "20250101", "20261118", 5, 1, 1)])
    r = run_with([ok, ServerScanResult("W2", False, error="x")])
    assert [(s.name, s.lines, s.parse_fail) for s in r.servers] == [("W1", 15, 3), ("W2", 0, 0)]
