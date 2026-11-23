"""OpenAPI 3 / Swagger 2 / Swagger 1.2 명세 로딩.

URL로 읽을 때는 swagger-ui 화면 주소나 서비스 기본 주소를 넣어도 명세를 찾아간다.
넣은 주소가 명세가 아니면 서비스 기본 주소를 구해서 아래 순서로 찾아본다 (_CANDIDATES).
  /swagger-resources            springfox 2.x 그룹 목록 → 그룹별 /v2/api-docs?group=… 를 모두 합친다
  /v3/api-docs/swagger-config   springdoc 그룹 목록
  /v3/api-docs                  springdoc (OpenAPI 3)
  /v2/api-docs                  springfox 2.x (Swagger 2.0)
  /api-docs                     swagger-springmvc 1.x (Swagger 1.2) → 컨트롤러별 문서를 모두 합친다
"""
from __future__ import annotations

import json
from urllib.parse import urlparse, urlsplit, urlunsplit

import requests
import yaml

from napi.models import SpecOperation, SpecSource

# HEAD/OPTIONS는 판정에서 제외한다 (설계 §5.7.4). 매처도 이 메서드의 로그 행을 버리므로
# 명세에 남기면 항상 "잠듦"이 된다.
_METHODS = ("get", "put", "post", "delete", "patch", "trace")

# 넣은 주소에서 떼어 내면 서비스 기본 주소가 되는 끝부분. 긴 것부터 검사한다.
_UI_SUFFIXES = ("/v3/api-docs/swagger-config", "/swagger-ui/index.html", "/swagger-ui.html",
                "/swagger-resources", "/v3/api-docs", "/v2/api-docs", "/api-docs",
                "/swagger-ui", "/index.html", "/doc.html")
_CANDIDATES = ("/swagger-resources", "/v3/api-docs/swagger-config", "/v3/api-docs",
               "/v2/api-docs", "/api-docs")


class SpecError(Exception):
    """명세를 읽지 못했을 때. 메시지는 사용자에게 그대로 보인다."""


class _Miss(Exception):
    """이 주소에는 명세가 없다 (404, HTML 화면 등). 다른 후보 주소를 찾아본다."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


_NOT_SPEC = "OpenAPI/Swagger 문서가 아니에요. JSON 또는 YAML 형식의 명세인지 확인해 주세요."


def _not_spec() -> SpecError:
    return SpecError(_NOT_SPEC)


# ── 본문 해석 ────────────────────────────────────────────────────────────────

def _parse_any(text: str):
    """JSON 또는 YAML로 읽는다. 명세가 아닌 본문은 _Miss."""
    try:
        doc = json.loads(text)
    except ValueError:
        if text.lstrip()[:1] == "<":
            raise _Miss("HTML 페이지") from None
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError:
            raise _Miss("명세 아님") from None
    doc = _unwrap(doc)
    if _kind(doc) is None:
        raise _Miss("명세 아님")
    return doc


def _unwrap(doc):
    """springfox 명세가 한 번 더 감싸져 온 경우를 푼다.

    - 서버가 Gson/fastjson으로 응답하면 springfox의 Json 객체가 {"value": "<명세 JSON 문자열>"} 로 나간다.
    - 메시지 변환기 설정에 따라 명세 JSON 전체가 문자열로 한 번 더 인코딩되어 나오기도 한다.
    """
    for _ in range(3):
        if isinstance(doc, dict) and set(doc) == {"value"} and isinstance(doc["value"], str):
            inner = doc["value"]
        elif isinstance(doc, str):
            inner = doc
        else:
            return doc
        try:
            doc = json.loads(inner)
        except ValueError:
            return doc
    return doc


def _kind(doc) -> str | None:
    if isinstance(doc, dict):
        if "openapi" in doc or "swagger" in doc:
            return "openapi"
        if "swaggerVersion" in doc:
            apis = doc.get("apis") if isinstance(doc.get("apis"), list) else []
            if "resourcePath" in doc or any(isinstance(a, dict) and "operations" in a for a in apis):
                return "sw12-decl"
            return "sw12-listing"
        if isinstance(doc.get("urls"), list) or ("configUrl" in doc and isinstance(doc.get("url"), str)):
            return "groups"
    if isinstance(doc, list) and doc and all(isinstance(g, dict) and ("url" in g or "location" in g) for g in doc):
        return "groups"
    return None


def _decode_body(resp) -> str:
    """명세 응답은 UTF-8로 읽는다 (requests의 charset 추측은 한글을 깨뜨린다). BOM은 버린다."""
    try:
        return resp.content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return resp.text


# ── 명세 → SpecOperation ────────────────────────────────────────────────────

def _url_path(url) -> str:
    raw = urlparse(str(url or "")).path.strip()
    if not raw.startswith("/"):
        raw = "/" + raw if raw else ""
    return raw.rstrip("/")


def _prefix(doc: dict) -> str:
    if "openapi" in doc:
        servers = doc.get("servers")
        url = ""
        if isinstance(servers, list) and servers and isinstance(servers[0], dict):
            url = str(servers[0].get("url") or "")
        return _url_path(url)
    return _url_path(doc.get("basePath"))


def _join(prefix: str, path) -> str:
    path = str(path)
    return prefix + (path if path.startswith("/") else "/" + path)


def parse_spec_document(doc: dict) -> list[SpecOperation]:
    """OpenAPI 3, Swagger 2.0 문서 또는 Swagger 1.2 컨트롤러별 문서(API declaration)를 읽는다."""
    kind = _kind(doc)
    if kind == "sw12-decl":
        return _parse_swagger12(doc)
    if kind == "sw12-listing":
        raise SpecError("Swagger 1.2 목차(/api-docs)라서 컨트롤러별 문서를 따로 받아야 해요. "
                        "명세 출처를 URL로 바꾸고 …/api-docs 주소를 넣어 주세요.")
    if kind == "groups":
        raise SpecError("명세 묶음 목록이에요. 명세 출처를 URL로 바꾸고 swagger-ui 주소를 넣어 주세요.")
    if kind != "openapi":
        raise _not_spec()
    paths = doc.get("paths")
    if not isinstance(paths, dict):
        raise SpecError("명세에 paths 항목이 없어요.")
    prefix = _prefix(doc)
    ops: list[SpecOperation] = []
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        full = _join(prefix, path)
        for method in _METHODS:
            op = item.get(method)
            if not isinstance(op, dict):
                continue
            ops.append(SpecOperation(
                method=method.upper(),
                path=full,
                operation_id=op.get("operationId"),
                tags=[str(t) for t in (op.get("tags") or [])],
                summary=op.get("summary"),
                deprecated=op.get("deprecated") is True,
            ))
    return ops


def _parse_swagger12(decl: dict) -> list[SpecOperation]:
    """Swagger 1.2 API declaration. 경로는 basePath(전체 URL일 수 있음)의 경로 부분 + apis[].path."""
    prefix = _url_path(decl.get("basePath"))
    resource = str(decl.get("resourcePath") or "").strip("/")
    tags = [resource] if resource else []
    ops: list[SpecOperation] = []
    for api in decl.get("apis") or []:
        if not isinstance(api, dict) or not api.get("path"):
            continue
        full = _join(prefix, api["path"])
        for op in api.get("operations") or []:
            if not isinstance(op, dict):
                continue
            method = str(op.get("method") or op.get("httpMethod") or "").lower()  # 1.0/1.1은 httpMethod
            if method not in _METHODS:
                continue
            ops.append(SpecOperation(
                method=method.upper(),
                path=full,
                operation_id=op.get("nickname"),
                tags=list(tags),
                summary=op.get("summary"),
                deprecated=str(op.get("deprecated")).lower() == "true",  # 1.2는 문자열 "true"
            ))
    return ops


def _merge(groups: list[list[SpecOperation]]) -> list[SpecOperation]:
    """여러 묶음을 합친다. 같은 메서드·경로는 처음 나온 것 하나만 남긴다."""
    seen: set[str] = set()
    out: list[SpecOperation] = []
    for ops in groups:
        for o in ops:
            if o.key not in seen:
                seen.add(o.key)
                out.append(o)
    return out


# ── URL에서 읽기 ─────────────────────────────────────────────────────────────

def _fetch(url: str, timeout: float):
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
    except requests.HTTPError as e:
        status = getattr(e.response, "status_code", None)
        raise _Miss(f"HTTP {status}" if status else str(e)) from e
    except requests.RequestException as e:
        # 서버에 닿지 않으면 다른 주소도 마찬가지이므로 바로 멈춘다
        raise SpecError(f"명세를 가져오지 못했어요: {url} ({e})") from e
    return _parse_any(_decode_body(resp))


def _service_base(url: str) -> str:
    """swagger-ui·명세 주소에서 서비스 기본 주소(scheme://host/context)를 구한다."""
    p = urlsplit(url.strip())
    path = p.path.rstrip("/")
    changed = True
    while changed:
        changed = False
        for suffix in _UI_SUFFIXES:
            if path.endswith(suffix):
                path = path[: -len(suffix)].rstrip("/")
                changed = True
                break
    return urlunsplit((p.scheme, p.netloc, path, "", ""))


def _resolve(base: str, ref: str) -> str:
    """그룹 목록에 적힌 주소를 서비스 기본 주소 기준으로 푼다.

    springfox는 컨텍스트 경로 없이(/v2/api-docs?group=…), springdoc은 컨텍스트 경로를 붙여(/app/v3/…) 적는다.
    """
    ref = ref.strip()
    if urlsplit(ref).scheme:
        return ref
    p = urlsplit(base)
    if not ref.startswith("/"):
        return base + "/" + ref
    if p.path and (ref == p.path or ref.startswith(p.path + "/")):
        return urlunsplit((p.scheme, p.netloc, "", "", "")) + ref
    return base + ref


def _ops_at(url: str, timeout: float, doc=None) -> list[SpecOperation]:
    doc = _fetch(url, timeout) if doc is None else doc
    kind = _kind(doc)
    if kind in ("openapi", "sw12-decl"):
        return parse_spec_document(doc)
    if kind == "sw12-listing":
        return _swagger12_listing(url, doc, timeout)
    return _groups(url, doc, timeout)


def _swagger12_listing(url: str, listing: dict, timeout: float) -> list[SpecOperation]:
    # 1.2 목차의 apis[].path 는 목차 주소 기준 상대 경로다 (/api-docs + /default/order-controller).
    # 1.1 이하는 목차에 basePath 를 따로 적기도 한다.
    p = urlsplit(url)
    root = str(listing.get("basePath") or "") if urlsplit(str(listing.get("basePath") or "")).scheme else ""
    root = (root or urlunsplit((p.scheme, p.netloc, p.path, "", ""))).rstrip("/")
    parts = []
    for api in listing.get("apis") or []:
        if not isinstance(api, dict) or not api.get("path"):
            continue
        sub = root + _join("", api["path"])
        try:
            decl = _fetch(sub, timeout)
        except _Miss as m:
            raise SpecError(f"Swagger 1.2 컨트롤러 문서를 읽지 못했어요: {sub} ({m.reason})") from None
        if _kind(decl) != "sw12-decl":
            raise SpecError(f"Swagger 1.2 컨트롤러 문서가 아니에요: {sub}")
        parts.append(_parse_swagger12(decl))
    return _merge(parts)


def _groups(url: str, doc, timeout: float) -> list[SpecOperation]:
    """springfox /swagger-resources 또는 springdoc swagger-config 의 그룹을 모두 읽어 합친다."""
    if isinstance(doc, list):
        entries = doc
    elif isinstance(doc.get("urls"), list):
        entries = doc["urls"]
    else:
        entries = [{"url": doc["url"]}]
    base = _service_base(url)
    parts = []
    for g in entries:
        if not isinstance(g, dict):
            continue
        ref = g.get("url") or g.get("location")
        if not ref:
            continue
        sub = _resolve(base, str(ref))
        name = g.get("name") or sub
        try:
            sub_doc = _fetch(sub, timeout)
        except _Miss as m:
            raise SpecError(f"명세 묶음 '{name}'을(를) 읽지 못했어요: {sub} ({m.reason})") from None
        if _kind(sub_doc) == "groups":  # 목록 안에 또 목록 — 돌고 도는 것을 막는다
            raise SpecError(f"명세 묶음 '{name}'이(가) 명세가 아니에요: {sub}")
        parts.append(_ops_at(sub, timeout, sub_doc))
    if not parts:
        raise _Miss("빈 명세 묶음 목록")
    return _merge(parts)


def _load_url(url: str, timeout: float) -> list[SpecOperation]:
    url = url.strip().split("#", 1)[0]  # swagger-ui 화면 주소의 #/controller 부분은 서버로 가지 않는다
    tried: list[tuple[str, str]] = []
    try:
        return _ops_at(url, timeout)
    except _Miss as m:
        tried.append((url, m.reason))
    base = _service_base(url)
    for suffix in _CANDIDATES:
        cand = base + suffix
        if cand.rstrip("/") == url.rstrip("/"):
            continue
        try:
            return _ops_at(cand, timeout)
        except _Miss as m:
            tried.append((cand, m.reason))
    where = ", ".join(f"{u} ({why})" for u, why in tried)
    raise SpecError(f"{_NOT_SPEC} 찾아본 주소: {where}")


def load_spec(src: SpecSource, timeout: float = 15.0) -> list[SpecOperation]:
    if src.source == "url":
        return _load_url(src.location, timeout)
    try:
        with open(src.location, encoding="utf-8-sig") as f:
            text = f.read()
    except (OSError, UnicodeDecodeError) as e:
        raise SpecError(f"명세를 가져오지 못했어요: {src.location} ({e})") from e
    try:
        doc = _parse_any(text)
    except _Miss:
        raise _not_spec() from None
    return parse_spec_document(doc)
