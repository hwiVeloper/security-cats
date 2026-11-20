from napi.matcher import apply_mapping, match
from napi.models import AggRow, PathMapping, SpecOperation


def op(method, path):
    return SpecOperation(method, path, None, [], None, False)


def row(method, path, sc="2xx", count=1, first="20261001", last="20261101"):
    return AggRow(method, path, sc, count, first, last)


NOMAP = PathMapping()


def test_param_matches_star():
    ops = [op("GET", "/api/v1/users/{id}")]
    r = match({"A": [row("GET", "/v1/users/{*}")]}, ops, PathMapping(add_prefix="/api"), [])
    assert r.per_op["GET /api/v1/users/{id}"].calls == 1
    assert r.unmatched == {}


def test_literal_wins():
    ops = [op("GET", "/api/v1/users/{id}"), op("GET", "/api/v1/users/me")]
    r = match({"A": [row("GET", "/api/v1/users/me")]}, ops, NOMAP, [])
    assert r.per_op["GET /api/v1/users/me"].calls == 1
    assert r.per_op["GET /api/v1/users/{id}"].calls == 0


def test_star_does_not_match_literal():
    ops = [op("GET", "/api/v1/users/me")]
    r = match({"A": [row("GET", "/api/v1/users/{*}")]}, ops, NOMAP, [])
    assert r.per_op["GET /api/v1/users/me"].calls == 0
    assert ("GET", "/api/v1/users/{*}") in r.unmatched


def test_strip_prefix():
    ops = [op("GET", "/v1/ping")]
    r = match({"A": [row("GET", "/roundpay/v1/ping")]}, ops, PathMapping(strip_prefix="/roundpay"), [])
    assert r.per_op["GET /v1/ping"].calls == 1


def test_mapping_segment_boundary():
    m = PathMapping(strip_prefix="/roundpay/")
    assert apply_mapping("/roundpay", m) == "/"
    assert apply_mapping("/roundpay/v1/x", m) == "/v1/x"
    assert apply_mapping("/roundpayment/x", m) == "/roundpayment/x"
    assert apply_mapping("/x", PathMapping(add_prefix="/api/")) == "/api/x"
    assert apply_mapping("/x", NOMAP) == "/x"


def test_method_must_match():
    ops = [op("GET", "/v1/a")]
    r = match({"A": [row("POST", "/v1/a")]}, ops, NOMAP, [])
    assert r.per_op["GET /v1/a"].calls == 0
    assert r.unmatched[("POST", "/v1/a")].calls == 1


def test_head_options_dropped():
    ops = [op("GET", "/v1/a")]
    r = match({"A": [row("HEAD", "/v1/a"), row("OPTIONS", "/v1/zzz")]}, ops, NOMAP, [])
    assert r.unmatched == {}
    assert r.per_op["GET /v1/a"].calls == 0


def test_exclude_globstar():
    ops = [op("GET", "/v1/a"), op("GET", "/actuator/info")]
    rows = [row("GET", "/actuator/health/liveness"), row("GET", "/actuator"), row("GET", "/v1/a")]
    r = match({"A": rows}, ops, NOMAP, ["/actuator/**"])
    assert r.unmatched == {}
    assert r.excluded_ops == {"GET /actuator/info"}
    assert "GET /actuator/info" not in r.per_op
    assert r.per_op["GET /v1/a"].calls == 1


def test_exclude_single_star_within_segment():
    r = match({"A": [row("GET", "/a/x/y"), row("GET", "/a/x")]}, [], NOMAP, ["/a/*"])
    assert list(r.unmatched) == [("GET", "/a/x/y")]


def test_trailing_slash_stripped():
    ops = [op("GET", "/v1/a")]
    r = match({"A": [row("GET", "/v1/a/")]}, ops, NOMAP, [])
    assert r.per_op["GET /v1/a"].calls == 1


def test_tie_first_in_spec_order():
    ops = [op("GET", "/v1/{a}/x"), op("GET", "/v1/y/{b}")]
    r = match({"A": [row("GET", "/v1/y/x")]}, ops, NOMAP, [])
    assert r.per_op["GET /v1/{a}/x"].calls == 1
    assert r.per_op["GET /v1/y/{b}"].calls == 0


def test_partial_template_is_literal():
    ops = [op("GET", "/f/{id}.json")]
    r = match({"A": [row("GET", "/f/{*}"), row("GET", "/f/{id}.json")]}, ops, NOMAP, [])
    assert r.per_op["GET /f/{id}.json"].calls == 1
    assert ("GET", "/f/{*}") in r.unmatched


def test_case_sensitive():
    ops = [op("GET", "/v1/Users")]
    r = match({"A": [row("GET", "/v1/users")]}, ops, NOMAP, [])
    assert r.per_op["GET /v1/Users"].calls == 0


def test_merge_servers():
    ops = [op("GET", "/v1/a")]
    a = [
        row("GET", "/v1/a", "2xx", 2, "20260901", "20261101"),
        row("GET", "/v1/a", "5xx", 1, "20261005", "20261020"),
    ]
    b = [row("GET", "/v1/a", "2xx", 2, "20260801", "20261110")]
    r = match({"A": a, "B": b}, ops, NOMAP, [])
    s = r.per_op["GET /v1/a"]
    assert s.calls == 5
    assert s.first == "20260801"
    assert s.last == "20261110"
    assert s.by_server == {"A": 3, "B": 2}
    assert s.by_status == {"2xx": 4, "5xx": 1}


def test_zero_stats_entry():
    r = match({}, [op("GET", "/v1/a")], NOMAP, [])
    s = r.per_op["GET /v1/a"]
    assert (s.calls, s.first, s.last, s.by_status, s.by_server) == (0, None, None, {}, {})


# --- 로그 쪽 {*}와 "정규화되면 {*}가 되는" 명세 리터럴 (I2) ---

def test_star_matches_literal_that_normalizes_to_star():
    """숫자를 섞은 긴 이름이나 숫자뿐인 리터럴은 서버에서 {*}로 정규화된다."""
    ops = [op("GET", "/api/retrieveAccountBalanceV2"), op("GET", "/v1/codes/2024")]
    rows = [row("GET", "/api/{*}", count=3), row("GET", "/v1/codes/{*}", count=2)]
    r = match({"A": rows}, ops, NOMAP, [])
    assert r.per_op["GET /api/retrieveAccountBalanceV2"].calls == 3
    assert r.per_op["GET /v1/codes/2024"].calls == 2
    assert r.unmatched == {}


def test_normalizing_literal_vs_param_credits_both_in_any_order():
    """{*} 행은 리터럴인지 {param}인지 알 수 없다 → 동률 후보 모두에 기록한다 (거짓 '잠듦' 방지)."""
    lit, par = op("GET", "/accounts/retrieveAccountBalanceV2"), op("GET", "/accounts/{accountNo}")
    for order in ([lit, par], [par, lit]):
        r = match({"A": [row("GET", "/accounts/{*}", count=7)]}, order, NOMAP, [])
        assert (r.per_op[lit.key].calls, r.per_op[par.key].calls) == (7, 7)
        assert r.unmatched == {}


def test_two_normalizing_literal_siblings_both_credited():
    bal = op("GET", "/accounts/retrieveAccountBalanceV2")
    his = op("GET", "/accounts/retrieveAccountHistoryV2")
    r = match({"A": [row("GET", "/accounts/{*}", count=4)]}, [bal, his], NOMAP, [])
    assert (r.per_op[bal.key].calls, r.per_op[his.key].calls) == (4, 4)


def test_plain_param_tie_still_goes_to_first_in_spec_order():
    """정규화 리터럴이 끼지 않은 동률은 그대로 스펙 순서상 먼저 나온 것에만 기록한다."""
    a, b = op("GET", "/v1/{x}/items"), op("GET", "/v1/{y}/items")
    r = match({"A": [row("GET", "/v1/{*}/items")]}, [a, b], NOMAP, [])
    assert (r.per_op[a.key].calls, r.per_op[b.key].calls) == (1, 0)
    r = match({"A": [row("GET", "/v1/{*}/items")]}, [b, a], NOMAP, [])
    assert (r.per_op[a.key].calls, r.per_op[b.key].calls) == (0, 1)


def test_normalizing_literal_in_lower_ranked_candidate_does_not_spread():
    """정규화 리터럴로 맞은 후보가 최고 리터럴 수에 못 미치면 동률에 끼지 않는다."""
    a = op("GET", "/v1/retrieveAccountBalanceV2/{z}")  # 리터럴 1 (v1)
    b, c = op("GET", "/v1/{x}/items"), op("GET", "/v1/{y}/items")  # 리터럴 2
    r = match({"A": [row("GET", "/v1/{*}/items")]}, [a, b, c], NOMAP, [])
    assert (r.per_op[a.key].calls, r.per_op[b.key].calls, r.per_op[c.key].calls) == (0, 1, 0)


def test_exact_literal_unaffected_by_normalizing_sibling():
    me = op("GET", "/api/v1/users/me")
    par = op("GET", "/api/v1/users/{id}")
    nl = op("GET", "/api/v1/users/retrieveAccountBalanceV2")
    r = match({"A": [row("GET", "/api/v1/users/me", count=5)]}, [nl, par, me], NOMAP, [])
    assert r.per_op[me.key].calls == 5
    assert (r.per_op[par.key].calls, r.per_op[nl.key].calls) == (0, 0)


def test_credited_row_stats_merge_into_each_op_without_double_counting():
    lit, par = op("GET", "/accounts/retrieveAccountBalanceV2"), op("GET", "/accounts/{accountNo}")
    a = [
        row("GET", "/accounts/{*}", "2xx", 3, "20260901", "20261020"),
        row("GET", "/accounts/{*}", "5xx", 1, "20261001", "20261101"),
    ]
    b = [row("GET", "/accounts/{*}", "2xx", 2, "20260801", "20261110")]
    r = match({"A": a, "B": b}, [lit, par], NOMAP, [])
    for key in (lit.key, par.key):
        s = r.per_op[key]
        assert s.calls == 6
        assert (s.first, s.last) == ("20260801", "20261110")
        assert s.by_server == {"A": 4, "B": 2}
        assert s.by_status == {"2xx": 5, "5xx": 1}


def test_normalizing_literal_does_not_count_as_literal():
    a = op("GET", "/api/retrieveAccountBalanceV2/{b}")
    b = op("GET", "/api/{a}/x")
    r = match({"A": [row("GET", "/api/{*}/x")]}, [a, b], NOMAP, [])
    assert (r.per_op[a.key].calls, r.per_op[b.key].calls) == (0, 1)


def test_star_still_does_not_match_plain_literal():
    ops = [op("GET", "/api/retrieveAccountBalance")]  # 숫자 없음 → 정규화되지 않는 리터럴
    r = match({"A": [row("GET", "/api/{*}")]}, ops, NOMAP, [])
    assert r.per_op["GET /api/retrieveAccountBalance"].calls == 0
    assert ("GET", "/api/{*}") in r.unmatched
