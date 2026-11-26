"""구형 Swagger(1.2), springfox 특유의 응답, 명세 주소 자동 탐색."""
import json

import pytest
import requests

from napi.models import SpecSource
from napi.spec import SpecError, load_spec, parse_spec_document


def keys(ops):
    return {o.key for o in ops}


def _site(monkeypatch, pages):
    """pages: {url: dict/list/str 본문 또는 (상태코드, 본문)}. 없는 주소는 404. 요청한 주소 목록을 돌려준다."""
    calls = []

    class Resp:
        def __init__(self, status, body):
            self.status_code = status
            text = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
            self.text, self.content = text, text.encode("utf-8")

        def raise_for_status(self):
            if self.status_code >= 400:
                raise requests.HTTPError(f"{self.status_code} Error", response=self)

    def fake_get(url, timeout=None, **kw):
        calls.append(url)
        page = pages.get(url, (404, "<html>Not Found</html>"))
        status, body = page if isinstance(page, tuple) else (200, page)
        return Resp(status, body)

    monkeypatch.setattr(requests, "get", fake_get)
    return calls


def _url(location):
    return load_spec(SpecSource(source="url", location=location))


SWAGGER2 = {"swagger": "2.0", "basePath": "/pay",
            "paths": {"/orders/{id}": {"get": {"operationId": "getOrder"}}}}

# swagger-springmvc 1.0.2 (com.mangofactory) 가 /api-docs 에 내놓는 Swagger 1.2 목차와 컨트롤러별 문서
SW12_LISTING = {"apiVersion": "1.0", "swaggerVersion": "1.2",
                "apis": [{"path": "/default/order-controller", "description": "Order"},
                         {"path": "/default/legacy-controller", "description": "Legacy"}]}
SW12_ORDER = {"apiVersion": "1.0", "swaggerVersion": "1.2", "basePath": "http://svc.local:8080/pay",
              "resourcePath": "/order", "produces": ["application/json"],
              "apis": [{"path": "/orders/{id}", "operations": [
                           {"method": "GET", "nickname": "getOrder", "summary": "주문 조회"},
                           {"method": "DELETE", "nickname": "cancelOrder", "deprecated": "true"},
                           {"method": "OPTIONS", "nickname": "opts"}]},
                       {"path": "/orders", "operations": [{"method": "POST", "nickname": "createOrder"}]}]}
# Swagger 1.0/1.1 은 method 대신 httpMethod 를 쓴다
SW12_LEGACY = {"swaggerVersion": "1.2", "basePath": "/pay", "resourcePath": "/legacy",
               "apis": [{"path": "/old", "operations": [{"httpMethod": "GET", "nickname": "old"}]}]}


def test_swagger12_declaration_parsed():
    ops = parse_spec_document(SW12_ORDER)
    assert keys(ops) == {"GET /pay/orders/{id}", "DELETE /pay/orders/{id}", "POST /pay/orders"}
    get = next(o for o in ops if o.method == "GET")
    assert (get.operation_id, get.summary, get.tags, get.deprecated) == ("getOrder", "주문 조회", ["order"], False)
    assert next(o for o in ops if o.method == "DELETE").deprecated is True


def test_swagger12_listing_by_url_follows_each_declaration(monkeypatch):
    calls = _site(monkeypatch, {
        "http://svc.local:8080/pay/api-docs": SW12_LISTING,
        "http://svc.local:8080/pay/api-docs/default/order-controller": SW12_ORDER,
        "http://svc.local:8080/pay/api-docs/default/legacy-controller": SW12_LEGACY,
    })
    ops = _url("http://svc.local:8080/pay/api-docs")
    assert keys(ops) == {"GET /pay/orders/{id}", "DELETE /pay/orders/{id}", "POST /pay/orders", "GET /pay/old"}
    assert len(calls) == 3


def test_swagger12_listing_with_group_query(monkeypatch):
    _site(monkeypatch, {
        "http://svc/api-docs?group=pay": {"swaggerVersion": "1.2", "apis": [{"path": "/pay/order-controller"}]},
        "http://svc/api-docs/pay/order-controller": SW12_ORDER,
    })
    assert len(_url("http://svc/api-docs?group=pay")) == 3


def test_swagger12_declaration_failure_is_reported(monkeypatch):
    _site(monkeypatch, {
        "http://svc/api-docs": SW12_LISTING,
        "http://svc/api-docs/default/order-controller": SW12_ORDER,
    })
    with pytest.raises(SpecError, match="legacy-controller"):
        _url("http://svc/api-docs")


def test_swagger12_listing_file_asks_for_url(tmp_path):
    f = tmp_path / "listing.json"
    f.write_text(json.dumps(SW12_LISTING), encoding="utf-8")
    with pytest.raises(SpecError, match="URL"):
        load_spec(SpecSource(source="file", location=str(f)))


def test_swagger12_declaration_file(tmp_path):
    f = tmp_path / "order.json"
    f.write_text(json.dumps(SW12_ORDER, ensure_ascii=False), encoding="utf-8")
    ops = load_spec(SpecSource(source="file", location=str(f)))
    assert len(ops) == 3


def test_springfox_gson_wrapped_json(monkeypatch):
    """서버가 Gson으로 응답하면 springfox 명세가 {"value": "<JSON 문자열>"} 로 감싸져 온다."""
    _site(monkeypatch, {"http://svc/v2/api-docs": {"value": json.dumps(SWAGGER2)}})
    assert keys(_url("http://svc/v2/api-docs")) == {"GET /pay/orders/{id}"}


def test_double_encoded_json_string(monkeypatch):
    _site(monkeypatch, {"http://svc/v2/api-docs": json.dumps(json.dumps(SWAGGER2))})
    assert keys(_url("http://svc/v2/api-docs")) == {"GET /pay/orders/{id}"}


def test_wrapped_json_in_file(tmp_path):
    f = tmp_path / "api-docs.json"
    f.write_text(json.dumps({"value": json.dumps(SWAGGER2)}), encoding="utf-8")
    assert keys(load_spec(SpecSource(source="file", location=str(f)))) == {"GET /pay/orders/{id}"}


def test_swagger_ui_url_discovers_springfox_groups(monkeypatch):
    """swagger-ui.html 주소를 넣으면 /swagger-resources 의 모든 그룹 명세를 합친다."""
    g2 = {"swagger": "2.0", "basePath": "/",
          "paths": {"/pay/orders/{id}": {"get": {}}, "/pay/refunds": {"post": {}}}}
    calls = _site(monkeypatch, {
        "http://svc/ctx/swagger-ui.html": "<!DOCTYPE html><html><title>Swagger UI</title></html>",
        "http://svc/ctx/swagger-resources": [
            {"name": "order", "url": "/v2/api-docs?group=order", "swaggerVersion": "2.0",
             "location": "/v2/api-docs?group=order"},
            {"name": "refund", "url": "/v2/api-docs?group=refund", "swaggerVersion": "2.0",
             "location": "/v2/api-docs?group=refund"}],
        "http://svc/ctx/v2/api-docs?group=order": SWAGGER2,
        "http://svc/ctx/v2/api-docs?group=refund": g2,
    })
    ops = _url("http://svc/ctx/swagger-ui.html#/order-controller")
    # 두 그룹에 같은 API(GET /pay/orders/{id})가 있으면 하나로 합친다
    assert sorted(o.key for o in ops) == ["GET /pay/orders/{id}", "POST /pay/refunds"]
    assert calls[:2] == ["http://svc/ctx/swagger-ui.html", "http://svc/ctx/swagger-resources"]


def test_v3_url_on_springfox2_falls_back_to_v2(monkeypatch):
    """설정 화면 예시대로 /v3/api-docs 를 넣었는데 springfox 2.x라 404인 경우."""
    calls = _site(monkeypatch, {"http://svc/v2/api-docs": SWAGGER2})
    assert keys(_url("http://svc/v3/api-docs")) == {"GET /pay/orders/{id}"}
    assert calls == ["http://svc/v3/api-docs", "http://svc/swagger-resources",
                     "http://svc/v3/api-docs/swagger-config", "http://svc/v2/api-docs"]


def test_base_url_discovers_swagger12(monkeypatch):
    _site(monkeypatch, {
        "http://svc/api-docs": SW12_LISTING,
        "http://svc/api-docs/default/order-controller": SW12_ORDER,
        "http://svc/api-docs/default/legacy-controller": SW12_LEGACY,
    })
    assert len(_url("http://svc/")) == 4


def test_springdoc_swagger_config_groups(monkeypatch):
    _site(monkeypatch, {
        "http://svc/app/v3/api-docs/swagger-config": {
            "configUrl": "/app/v3/api-docs/swagger-config",
            "urls": [{"url": "/app/v3/api-docs/a", "name": "a"}, {"url": "/app/v3/api-docs/b", "name": "b"}]},
        "http://svc/app/v3/api-docs/a": {"openapi": "3.0.1", "paths": {"/a": {"get": {}}}},
        "http://svc/app/v3/api-docs/b": {"openapi": "3.0.1", "paths": {"/b": {"get": {}}}},
    })
    assert keys(_url("http://svc/app/swagger-ui/index.html")) == {"GET /a", "GET /b"}


def test_group_failure_is_reported(monkeypatch):
    _site(monkeypatch, {
        "http://svc/swagger-resources": [{"name": "order", "url": "/v2/api-docs?group=order"},
                                         {"name": "refund", "url": "/v2/api-docs?group=refund"}],
        "http://svc/v2/api-docs?group=order": SWAGGER2,
    })
    with pytest.raises(SpecError, match="refund"):
        _url("http://svc/swagger-ui.html")


def test_nothing_found_lists_tried_urls(monkeypatch):
    _site(monkeypatch, {"http://svc/swagger-ui.html": "<html>login</html>"})
    with pytest.raises(SpecError) as e:
        _url("http://svc/swagger-ui.html")
    msg = str(e.value)
    assert "OpenAPI/Swagger 문서가 아니에요" in msg
    assert "http://svc/swagger-ui.html (HTML 페이지)" in msg
    assert "http://svc/v2/api-docs (HTTP 404)" in msg


def test_connection_error_stops_discovery(monkeypatch):
    calls = []

    def boom(url, **k):
        calls.append(url)
        raise requests.ConnectionError("down")

    monkeypatch.setattr(requests, "get", boom)
    with pytest.raises(SpecError, match="명세를 가져오지 못했어요"):
        _url("http://svc/swagger-ui.html")
    assert calls == ["http://svc/swagger-ui.html"]
