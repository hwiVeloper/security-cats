import json
from pathlib import Path

import pytest
import requests

from napi.models import SpecSource
from napi.spec import SpecError, load_spec, parse_spec_document

FIX = Path(__file__).parent / "fixtures" / "specs"


def keys(ops):
    return {o.key for o in ops}


def test_openapi3_operations():
    doc = json.loads((FIX / "openapi3.json").read_text(encoding="utf-8"))
    ops = parse_spec_document(doc)
    assert keys(ops) == {
        "GET /api/v1/users/{id}",
        "PUT /api/v1/users/{id}",
        "GET /api/v1/users/me",
        "GET /api/v1/legacy",
    }
    legacy = next(o for o in ops if o.path == "/api/v1/legacy")
    assert legacy.deprecated is True
    assert legacy.tags == ["legacy"]
    assert legacy.summary == "옛날 API"
    me = next(o for o in ops if o.path == "/api/v1/users/me")
    assert (me.tags, me.operation_id, me.deprecated) == ([], None, False)


def test_swagger2_basepath():
    ops = load_spec(SpecSource(source="file", location=str(FIX / "swagger2.yaml")))
    assert keys(ops) == {"GET /pay/orders/{orderId}", "DELETE /pay/orders/{orderId}"}


def test_absolute_server_url_uses_path_only():
    ops = load_spec(SpecSource(source="file", location=str(FIX / "openapi3_abs_server.json")))
    assert keys(ops) == {"GET /roundpay/v1/ping"}


def test_root_prefix_has_no_double_slash():
    doc = {"openapi": "3.0.0", "servers": [{"url": "/"}], "paths": {"/a": {"get": {}}}}
    assert keys(parse_spec_document(doc)) == {"GET /a"}
    doc = {"swagger": "2.0", "basePath": "/x/", "paths": {"/a": {"get": {}}}}
    assert keys(parse_spec_document(doc)) == {"GET /x/a"}


def test_load_from_file_yaml_and_json():
    a = load_spec(SpecSource(source="file", location=str(FIX / "openapi3.json")))
    b = load_spec(SpecSource(source="file", location=str(FIX / "swagger2.yaml")))
    assert len(a) == 4 and len(b) == 2


def test_load_from_url(monkeypatch):
    body = (FIX / "openapi3.json").read_text(encoding="utf-8")

    class Resp:
        text = body
        content = body.encode("utf-8")

        def raise_for_status(self):
            pass

    calls = []

    def fake_get(url, timeout=None, **kw):
        calls.append((url, timeout))
        return Resp()

    monkeypatch.setattr(requests, "get", fake_get)
    ops = load_spec(SpecSource(source="url", location="http://x/spec.json"))
    assert len(ops) == 4
    assert calls == [("http://x/spec.json", 15.0)]


def test_url_unreachable(monkeypatch):
    def boom(*a, **k):
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "get", boom)
    with pytest.raises(SpecError, match="명세를 가져오지 못했어요"):
        load_spec(SpecSource(source="url", location="http://x/spec.json"))


def test_missing_file():
    with pytest.raises(SpecError, match="명세를 가져오지 못했어요"):
        load_spec(SpecSource(source="file", location=str(FIX / "nope.json")))


def test_not_a_spec(monkeypatch):
    class Resp:
        text = "<html><body>hi</body></html>"
        content = text.encode("utf-8")

        def raise_for_status(self):
            pass

    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    with pytest.raises(SpecError, match="OpenAPI/Swagger 문서가 아니에요"):
        load_spec(SpecSource(source="url", location="http://x/"))
    with pytest.raises(SpecError, match="OpenAPI/Swagger 문서가 아니에요"):
        parse_spec_document({"foo": 1})


def test_springfox_all_methods_drops_head_and_options():
    """Springfox는 메서드 없는 @RequestMapping을 모든 메서드로 펼친다. HEAD/OPTIONS는 판정 제외."""
    item = {m: {"operationId": f"handle{m.title()}"} for m in
            ("get", "head", "post", "put", "delete", "options", "patch")}
    doc = {"swagger": "2.0", "basePath": "/", "paths": {"/v1/any": item}}
    assert keys(parse_spec_document(doc)) == {
        "GET /v1/any", "POST /v1/any", "PUT /v1/any", "DELETE /v1/any", "PATCH /v1/any"}


def _url_resp(monkeypatch, content, text):
    class Resp:
        def raise_for_status(self):
            pass

    r = Resp()
    r.content, r.text = content, text
    monkeypatch.setattr(requests, "get", lambda *a, **k: r)


def test_url_body_decoded_as_utf8_not_charset_guess(monkeypatch):
    body = json.dumps({"openapi": "3.0.0", "paths": {"/a": {"get": {"summary": "잔액 조회"}}}},
                      ensure_ascii=False)
    raw = b"\xef\xbb\xbf" + body.encode("utf-8")  # UTF-8 BOM 포함
    # requests가 charset 없는 응답을 ISO-8859-1로 추측한 경우의 resp.text
    _url_resp(monkeypatch, raw, raw.decode("iso-8859-1"))
    ops = load_spec(SpecSource(source="url", location="http://x/spec.json"))
    assert [o.summary for o in ops] == ["잔액 조회"]


def test_url_body_not_utf8_falls_back_to_text(monkeypatch):
    body = json.dumps({"openapi": "3.0.0", "paths": {"/a": {"get": {"summary": "잔액"}}}},
                      ensure_ascii=False)
    _url_resp(monkeypatch, body.encode("cp949"), body)
    ops = load_spec(SpecSource(source="url", location="http://x/spec.json"))
    assert [o.summary for o in ops] == ["잔액"]
