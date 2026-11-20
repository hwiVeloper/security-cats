"""OpenAPI 3 / Swagger 2 명세 로딩."""
from __future__ import annotations

import json
from urllib.parse import urlparse

import requests
import yaml

from napi.models import SpecOperation, SpecSource

# HEAD/OPTIONS는 판정에서 제외한다 (설계 §5.7.4). 매처도 이 메서드의 로그 행을 버리므로
# 명세에 남기면 항상 "잠듦"이 된다.
_METHODS = ("get", "put", "post", "delete", "patch", "trace")


class SpecError(Exception):
    """명세를 읽지 못했을 때. 메시지는 사용자에게 그대로 보인다."""


def _not_spec() -> SpecError:
    return SpecError("OpenAPI/Swagger 문서가 아니에요. JSON 또는 YAML 형식의 명세인지 확인해 주세요.")


def _parse_text(text: str) -> dict:
    try:
        doc = json.loads(text)
    except ValueError:
        try:
            doc = yaml.safe_load(text)
        except yaml.YAMLError:
            raise _not_spec() from None
    if not isinstance(doc, dict):
        raise _not_spec()
    return doc


def _decode_body(resp) -> str:
    """명세 응답은 UTF-8로 읽는다 (requests의 charset 추측은 한글을 깨뜨린다). BOM은 버린다."""
    try:
        return resp.content.decode("utf-8-sig")
    except UnicodeDecodeError:
        return resp.text


def load_spec(src: SpecSource, timeout: float = 15.0) -> list[SpecOperation]:
    try:
        if src.source == "url":
            resp = requests.get(src.location, timeout=timeout)
            resp.raise_for_status()
            text = _decode_body(resp)
        else:
            with open(src.location, encoding="utf-8-sig") as f:
                text = f.read()
    except (requests.RequestException, OSError, UnicodeDecodeError) as e:
        raise SpecError(f"명세를 가져오지 못했어요: {src.location} ({e})") from e
    return parse_spec_document(_parse_text(text))


def _prefix(doc: dict) -> str:
    if "openapi" in doc:
        servers = doc.get("servers")
        url = ""
        if isinstance(servers, list) and servers and isinstance(servers[0], dict):
            url = str(servers[0].get("url") or "")
        raw = urlparse(url).path
    else:
        raw = str(doc.get("basePath") or "")
    raw = raw.strip()
    if not raw.startswith("/"):
        raw = "/" + raw if raw else ""
    return raw.rstrip("/")


def parse_spec_document(doc: dict) -> list[SpecOperation]:
    if not isinstance(doc, dict) or not ("openapi" in doc or "swagger" in doc):
        raise _not_spec()
    paths = doc.get("paths")
    if not isinstance(paths, dict):
        raise SpecError("명세에 paths 항목이 없어요.")
    prefix = _prefix(doc)
    ops: list[SpecOperation] = []
    for path, item in paths.items():
        if not isinstance(item, dict):
            continue
        full = prefix + (path if str(path).startswith("/") else "/" + str(path))
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
