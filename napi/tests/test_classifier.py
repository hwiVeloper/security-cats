from datetime import date

import pytest

from napi.classifier import (
    DEFAULT_PROBE_PATTERNS, classify, probe_rest, summary_sentence, window_bounds,
)
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
    un = {("GET", "/a/z"): stats(6, "20261101", {"2xx": 1, "4xx": 5}),
          ("GET", "/a/b"): stats(9, "20261101", {"4xx": 9})}
    apis, _, counts, *_ = run([op()], {"GET /a": stats(1, "20261110")}, unmatched=un)
    assert [(a.path, a.status) for a in apis] == [
        ("/a", Status.AWAKE), ("/a/b", Status.PROBE), ("/a/z", Status.GHOST)]
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


def test_reliability_no_servers():
    r = run([op()], {"GET /a": stats()}, [])[1]
    assert r.level == "low" and r.coverage_days == 0
    assert r.reasons == ["등록된 서버가 없어요"]


@pytest.mark.parametrize("files,reason", [
    ([FileStat("/l", "20250101", "20261118", 100, 25, 0)], "25%"),
    ([FileStat("/a", "20250101", "20261118", 50, 10, 0),
      FileStat("/b", "20250101", "20261118", 50, 10, 0)], "20%"),  # 파일 합산, 경계(포함)
    ([FileStat("/l", "20250101", "20261118", 100, 19, 0)], None),
    ([FileStat("/l", "20250101", "20261118", 0, 0, 0)], None),
])
def test_reliability_parse_fail_ratio(files, reason):
    r = run([op()], {"GET /a": stats()}, [server("A", files=files)])[1]
    if reason is None:
        assert r.level == "ok" and r.reasons == []
    else:
        assert r.level == "low"
        assert r.reasons == [
            f"A은 로그 줄의 {reason}를 읽지 못했어요 — 로그 형식 설정을 확인해 주세요"]


def test_reliability_all_lines_unparsed_is_not_missing_files():
    r = run([op()], {"GET /a": stats()},
            [server("A", files=[FileStat("/l", None, None, 40, 40, 0)])])[1]
    assert r.level == "low"
    assert r.reasons == ["A은 로그 줄의 100%를 읽지 못했어요 — 로그 형식 설정을 확인해 주세요"]


@pytest.mark.parametrize("newest,stale", [
    ("20260601", True), ("20261019", True), ("20261020", False), ("20261119", False)])
def test_reliability_stale_logs(newest, stale):
    s = server("A", files=[FileStat("/l", "20250101", newest, 10, 0, 0)])
    r = run([op()], {"GET /a": stats()}, [s])[1]
    if stale:
        y, m, d = newest[:4], newest[4:6], newest[6:]
        assert r.level == "low"
        assert r.reasons == [f"A의 최신 로그가 {y}-{m}-{d}예요 — 최근 로그가 포함됐는지 확인해 주세요"]
    else:
        assert r.level == "ok" and r.reasons == []


def test_reliability_glob_empty():
    s = server("WAS-01", file_errors=[("glob_empty", "/x/b*"), ("glob_empty", "/y/c*.log")])
    r = run([op()], {"GET /a": stats()}, [s])[1]
    assert r.level == "low"
    assert r.reasons == ["WAS-01의 경로 /x/b*에서 파일을 찾지 못했어요",
                         "WAS-01의 경로 /y/c*.log에서 파일을 찾지 못했어요"]
    # glob_empty는 "읽지 못한 로그 파일"이 아니다
    assert "WAS-01에서 읽지 못한 로그 파일이 있어요" not in r.reasons


@pytest.mark.parametrize("oldest", ["20261131", "00001118", "20260230"])
def test_reliability_invalid_oldest_date_does_not_raise(oldest):
    s = server("A", files=[FileStat("/l", oldest, "20261118", 10, 0, 0)])
    r = run([op()], {"GET /a": stats()}, [s, server("B", "20250101")], window="all")[1]
    assert r.level == "low"
    assert r.reasons == [f"A의 로그 날짜 {oldest}를 읽을 수 없어요 — 로그 형식 설정을 확인해 주세요"]
    assert r.coverage_days > 300  # 읽을 수 없는 서버는 기간 계산에서 뺀다


# ------------------------------------------------ 유령 / 외부 탐색 구분 (API 범위 + 스캐너 패턴)


def _un(path, by_status, method="GET"):
    calls = sum(by_status.values())
    return {(method, path): MatchedStats(calls=calls, first="20261101", last="20261101",
                                         by_status=by_status)}


def _one(path, by_status, ops=None, c=None):
    ops = ops if ops is not None else [op("/v1/users/{id}")]
    apis, *_ = run(ops, {o.key: stats() for o in ops}, c=c, unmatched=_un(path, by_status))
    return next(a for a in apis if not a.in_spec)


def test_default_probe_patterns_exact():
    assert DEFAULT_PROBE_PATTERNS == [
        "**/.*", "**/*.yml", "**/*.yaml", "**/*.env", "**/*.ini", "**/*.bak", "**/*.old",
        "**/*.sql", "**/*.php", "**/*.asp", "**/*.aspx", "**/*.cgi", "**/wp-*",
        "**/phpmyadmin*", "/cgi-bin/**", "**/.*/**", "**/wp-*/**", "**/phpmyadmin*/**",
    ]


@pytest.mark.parametrize("path", ["/.git/config", "/blog/wp-admin/x", "/v1/.well-known/x",
                                  "/phpMyAdmin2/setup/x"])
def test_scanner_directories_anywhere_in_path(path):
    a = _one(path, {"2xx": 1})
    assert (a.status, a.probe_reason) == (Status.PROBE, "scanner")


def test_scanner_pattern_beats_2xx():
    a = _one("/v1/.env", {"2xx": 3})
    assert (a.status, a.probe_reason) == (Status.PROBE, "scanner")
    a = _one("/.env", {"2xx": 3})
    assert (a.status, a.probe_reason) == (Status.PROBE, "scanner")


@pytest.mark.parametrize("path", ["/v1/config.YML", "/v1/Wp-Login.PHP", "/CGI-BIN/x",
                                  "/v1/a/b/.DS_Store", "/v1/phpMyAdmin2"])
def test_scanner_pattern_case_insensitive(path):
    a = _one(path, {"2xx": 1})
    assert (a.status, a.probe_reason) == (Status.PROBE, "scanner")


def test_jsp_and_do_not_default_scanner():
    assert _one("/v1/a.jsp", {"2xx": 1}).status == Status.GHOST
    assert _one("/v1/a.do", {"5xx": 1}).status == Status.GHOST


def test_out_of_scope_redirect_is_probe():
    a = _one("/admin/login", {"3xx": 4})
    assert (a.status, a.probe_reason) == (Status.PROBE, "out_of_scope")


def test_in_scope_unmatched_5xx_is_ghost():
    a = _one("/v1/orders", {"5xx": 2})
    assert (a.status, a.probe_reason) == (Status.GHOST, None)


def test_in_scope_4xx_only_is_probe():
    a = _one("/v1/orders", {"4xx": 2})
    assert (a.status, a.probe_reason) == (Status.PROBE, "4xx_only")


def test_param_first_segment_means_unrestricted_scope():
    ops = [op("/v1/users"), op("/{tenant}/items")]
    a = _one("/admin/login", {"3xx": 1}, ops=ops)
    assert (a.status, a.probe_reason) == (Status.GHOST, None)


def test_no_ops_means_unrestricted_scope():
    a = _one("/anything", {"2xx": 1}, ops=[])
    assert (a.status, a.probe_reason) == (Status.GHOST, None)


def test_excluded_ops_do_not_widen_scope():
    ops = [op("/v1/a"), op("/admin/x")]
    m = MatchResult(per_op={"GET /v1/a": stats()}, unmatched=_un("/admin/y", {"2xx": 1}),
                    excluded_ops={"GET /admin/x"})
    apis, *_ = classify(ops, m, [server()], cfg(), "365", TODAY)
    a = next(a for a in apis if not a.in_spec)
    assert (a.status, a.probe_reason) == (Status.PROBE, "out_of_scope")


def test_user_api_scopes_segment_boundary():
    c = cfg()
    c.api_scopes = ["/legacy", "/internal/api"]
    assert _one("/legacy", {"2xx": 1}, c=c).status == Status.GHOST
    assert _one("/legacy/x", {"2xx": 1}, c=c).status == Status.GHOST
    assert _one("/internal/api/y", {"2xx": 1}, c=c).status == Status.GHOST
    a = _one("/legacyx", {"2xx": 1}, c=c)
    assert (a.status, a.probe_reason) == (Status.PROBE, "out_of_scope")
    a = _one("/internal/other", {"2xx": 1}, c=c)
    assert (a.status, a.probe_reason) == (Status.PROBE, "out_of_scope")


def test_empty_probe_patterns_disable_defaults():
    c = cfg()
    c.probe_patterns = []
    a = _one("/v1/.env", {"2xx": 1}, c=c)
    assert (a.status, a.probe_reason) == (Status.GHOST, None)


def test_custom_probe_patterns_replace_defaults():
    c = cfg()
    c.probe_patterns = ["/v1/debug/**"]
    assert _one("/v1/debug/vars", {"2xx": 1}, c=c).probe_reason == "scanner"
    assert _one("/v1/x.php", {"2xx": 1}, c=c).status == Status.GHOST


def test_statusless_unmatched_by_scope():
    a = _one("/v1/orders", {"???": 1})
    assert (a.status, a.probe_reason) == (Status.GHOST, None)
    a = _one("/admin", {"???": 1})
    assert (a.status, a.probe_reason) == (Status.PROBE, "out_of_scope")


def test_spec_ops_never_get_probe_reason():
    ops = [op("/v1/.env")]
    apis, *_ = run(ops, {"GET /v1/.env": stats(3, "20261110")})
    assert (apis[0].status, apis[0].probe_reason) == (Status.AWAKE, None)
