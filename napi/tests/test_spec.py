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

        def raise_for_status(self):
            pass

    monkeypatch.setattr(requests, "get", lambda *a, **k: Resp())
    with pytest.raises(SpecError, match="OpenAPI/Swagger 문서가 아니에요"):
        load_spec(SpecSource(source="url", location="http://x/"))
    with pytest.raises(SpecError, match="OpenAPI/Swagger 문서가 아니에요"):
        parse_spec_document({"foo": 1})
