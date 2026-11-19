import json

from napi.models import (
    ApiResult, Note, Reliability, ServerSummary, ServiceResult, Status,
)
from napi.store import (
    load_result, refresh_notes, result_from_dict, result_to_dict, safe_name, save_result,
)


def make_result(service="라운드페이 API"):
    apis = [
        ApiResult("GET", "/v1/a", Status.AWAKE, 5, "20260101", "20261110",
                  {"2xx": 5}, {"WAS-01": 5}, True, tags=["t"], summary="s",
                  operation_id="getA", note=Note("사유", "홍", "2026-11-01")),
        ApiResult("GET", "/v1/b", Status.ASLEEP, 0, None, None, {}, {}, True),
        ApiResult("DELETE", "/v1/c", Status.RIP, 0, None, None, {}, {}, True, deprecated=True),
        ApiResult("GET", "/ghost", Status.GHOST, 2, "20261001", "20261002",
                  {"2xx": 2}, {"WAS-01": 2}, False),
    ]
    return ServiceResult(
        service=service, scanned_at="2026-11-19T10:00:00", window="365",
        since="20251119", until="20261119", spec_location="http://x/spec",
        summary="API 3개 중 2개가 1년째 자고 있어요.", apis=apis,
        counts={s.value: 0 for s in Status}, sleeping=2, sleeping_noted=0,
        reliability=Reliability("low", 400, ["WAS-02 스캔 실패: x"]),
        servers=[ServerSummary("WAS-01", True, None, "20250101", "20261118", 1.5, False),
                 ServerSummary("WAS-02", False, "x", None, None, 0.0, False)],
        probe_rest={"distinct": 1, "count": 3})


def test_store_roundtrip(tmp_path):
    r = make_result()
    p = save_result(r, tmp_path)
    assert p.name == "라운드페이 API.json"
    assert load_result("라운드페이 API", tmp_path) == r
    assert result_from_dict(result_to_dict(r)) == r
    assert [f.name for f in tmp_path.iterdir()] == ["라운드페이 API.json"]


def test_store_utf8_no_ascii_escape(tmp_path):
    p = save_result(make_result(), tmp_path)
    text = p.read_text(encoding="utf-8")
    assert "라운드페이" in text and r"\u" not in text
    assert json.loads(text)["apis"][0]["status"] == "awake"


def test_store_contains_no_query_or_secret(tmp_path):
    p = save_result(make_result(), tmp_path)
    text = p.read_text(encoding="utf-8")
    assert "?" not in text
    assert "password" not in text.lower()


def test_load_missing_or_corrupt_returns_none(tmp_path):
    assert load_result("없음", tmp_path) is None
    (tmp_path / "bad.json").write_text("{not json", encoding="utf-8")
    assert load_result("bad", tmp_path) is None
    (tmp_path / "partial.json").write_text("{}", encoding="utf-8")
    assert load_result("partial", tmp_path) is None


def test_safe_name():
    assert safe_name('a<b>c:d"e/f\\g|h?i*j\x01k') == "a_b_c_d_e_f_g_h_i_j_k"
    assert safe_name("name. . ") == "name"
    assert safe_name("...") == "service"
    assert safe_name("") == "service"


def test_refresh_notes():
    r = make_result()
    n = Note("정기 점검용", "김", "2026-11-19")
    new = refresh_notes(r, {"GET /v1/b": n, "GET /ghost": n})
    assert new is not r
    assert r.apis[1].note is None and r.sleeping == 2  # 입력은 그대로
    assert new.apis[1].note == n and new.apis[3].note == n
    assert new.sleeping == 1 and new.sleeping_noted == 1
    assert new.apis[0].note == r.apis[0].note
    assert new.summary != r.summary
    assert new.summary.startswith("API 3개 중 1개가")
