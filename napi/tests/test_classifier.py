from datetime import date

import pytest

from napi.classifier import classify, probe_rest, summary_sentence, window_bounds
from napi.matcher import MatchedStats, MatchResult
from napi.models import (
    FileStat, Note, ServerScanResult, ServiceConfig, SpecOperation, SpecSource, Status,
)

TODAY = date(2026, 11, 19)


def op(path="/a", dep=False, method="GET"):
    return SpecOperation(method, path, None, ["t"], "s", dep)


def cfg(notes=None):
    return ServiceConfig(name="x", spec=SpecSource(), servers=[], notes=notes or {})


def stats(calls=0, last=None, by_status=None):
    return MatchedStats(calls=calls, first=last, last=last,
                        by_status=by_status or ({"2xx": calls} if calls else {}))


def server(name="WAS-01", oldest="20250101", **kw):
    files = kw.pop("files", [FileStat("/l", oldest, "20261118", 1, 0, 0)] if oldest else [])
    return ServerScanResult(server=name, ok=kw.pop("ok", True), files=files, **kw)


def run(ops, per_op, servers=None, c=None, window="365", unmatched=None, excluded=None):
    m = MatchResult(per_op=per_op, unmatched=unmatched or {}, excluded_ops=excluded or set())
    return classify(ops, m, servers if servers is not None else [server()], c or cfg(), window, TODAY)


def test_window_bounds():
    assert window_bounds("90", TODAY) == ("20260821", "20261119")
    assert window_bounds("all", TODAY) == (None, "20261119")


@pytest.mark.parametrize("dep,st,expected", [
    (True, stats(0), Status.RIP),
    (True, stats(3, "20261101"), Status.SLEEPTALK),
    (False, stats(0), Status.ASLEEP),
    (False, stats(5, "20261101"), Status.AWAKE),
    (False, stats(5, "20260901"), Status.DROWSY),
    (False, stats(5, "20261020"), Status.AWAKE),  # awake_days 경계(포함)
    (False, stats(5, "20261019"), Status.DROWSY),
])
def test_status_rules(dep, st, expected):
    apis, *_ = run([op(dep=dep)], {"GET /a": st})
    assert apis[0].status == expected


def test_ghost_vs_probe_and_order():
    un = {("GET", "/z"): stats(6, "20261101", {"2xx": 1, "4xx": 5}),
          ("GET", "/b"): stats(9, "20261101", {"4xx": 9})}
    apis, _, counts, *_ = run([op()], {"GET /a": stats(1, "20261110")}, unmatched=un)
    assert [(a.path, a.status) for a in apis] == [
        ("/a", Status.AWAKE), ("/b", Status.PROBE), ("/z", Status.GHOST)]
    assert apis[1].in_spec is False and apis[1].tags == [] and not apis[1].deprecated
    assert counts["ghost"] == 1 and counts["probe"] == 1 and counts["rip"] == 0
    assert set(counts) == {s.value for s in Status}


def test_excluded_skipped():
    apis, *_ = run([op("/a"), op("/b")], {"GET /a": stats()}, excluded={"GET /b"})
    assert [a.path for a in apis] == ["/a"]


def test_note_excluded_from_sleeping():
    ops = [op("/a"), op("/b"), op("/c")]
    per = {o.key: stats() for o in ops}
    c = cfg({"GET /b": Note("r", "me", "20261101")})
    apis, _, _, sleeping, noted = run(ops, per, c=c)
    assert (sleeping, noted) == (2, 1)
    assert apis[1].note is not None


def test_reliability_coverage():
    ok = run([op()], {"GET /a": stats()}, [server("WAS-03", "20260820")])[1]
    assert ok.level == "ok" and ok.coverage_days == 91
    low = run([op()], {"GET /a": stats()}, [server("WAS-03", "20261001")])[1]
    assert low.level == "low" and low.coverage_days == 49
    assert "WAS-03은 로그가 49일치뿐이에요" in low.reasons


def test_coverage_window_limits_and_all():
    r = run([op()], {"GET /a": stats()}, [server(oldest="20200101")], window="90")[1]
    assert r.coverage_days == 90
    r = run([op()], {"GET /a": stats()}, [server(oldest="20260820")], window="all")[1]
    assert r.coverage_days == 91


def test_reliability_failed_server():
    r = run([op()], {"GET /a": stats()},
            [server("A", "20250101"), ServerScanResult("B", ok=False, error="timeout")])[1]
    assert r.level == "low" and r.coverage_days > 90
    assert r.reasons == ["B 스캔 실패: timeout"]


def test_reliability_no_files():
    r = run([op()], {"GET /a": stats()}, [server("A", None)])[1]
    assert r.level == "low" and r.coverage_days == 0
    assert r.reasons == ["A에서 로그 파일을 찾지 못했어요"]


def test_truncated_and_file_errors_order():
    s = server("A", "20261110", truncated=True,
               file_errors=[("unreadable", "/x"), ("no_files", "")])
    r = run([op()], {"GET /a": stats()}, [s])[1]
    assert r.reasons == ["A은 로그가 9일치뿐이에요", "A 결과가 너무 많아 일부만 집계됐어요",
                         "A에서 읽지 못한 로그 파일이 있어요"]


def test_probe_rest_sums_ok_only():
    s = [server("A", probe404_rest=(2, 10)), server("B", probe404_rest=(1, 5)),
         ServerScanResult("C", ok=False, error="x", probe404_rest=(9, 9))]
    assert probe_rest(s) == {"distinct": 3, "count": 15}


def test_summary_sentence():
    assert summary_sentence(161, 12, 3, 365) == \
        "API 161개 중 12개가 1년째 자고 있어요. 그중 3개는 deprecated라 바로 지워도 될 것 같아요 💤"
    assert summary_sentence(10, 0, 0, 200) == "API 10개 모두 깨어 있어요! 🐾"
    assert summary_sentence(10, 2, 0, 275) == "API 10개 중 2개가 9개월째 자고 있어요."
    assert summary_sentence(10, 2, 0, 12) == "API 10개 중 2개가 12일째 자고 있어요."
