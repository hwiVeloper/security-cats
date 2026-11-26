"""서비스별 마지막 스캔 결과를 JSON으로 저장·복원한다."""
from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import asdict, replace
from pathlib import Path

from napi.classifier import summary_sentence
from napi.models import (
    ApiResult, Note, Reliability, ServerSummary, ServiceResult, Status,
)
from napi.paths import results_dir

_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def safe_name(service: str) -> str:
    name = _BAD.sub("_", service).rstrip(". ")
    return name or "service"


def result_to_dict(r: ServiceResult) -> dict:
    d = asdict(r)
    for a in d["apis"]:
        a["status"] = a["status"].value if isinstance(a["status"], Status) else a["status"]
    return d


def _note(d: dict | None) -> Note | None:
    return Note(**d) if d is not None else None


def result_from_dict(d: dict) -> ServiceResult:
    apis = [ApiResult(**{**a, "status": Status(a["status"]), "note": _note(a.get("note"))})
            for a in d["apis"]]
    return ServiceResult(
        **{**d, "apis": apis,
           "reliability": Reliability(**d["reliability"]),
           "servers": [ServerSummary(**s) for s in d["servers"]]})


def save_result(r: ServiceResult, dir: Path | None = None) -> Path:
    base = Path(dir) if dir is not None else results_dir()
    base.mkdir(parents=True, exist_ok=True)
    target = base / f"{safe_name(r.service)}.json"
    fd, tmp = tempfile.mkstemp(dir=base, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(result_to_dict(r), f, ensure_ascii=False, indent=2)
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


def load_result(service: str, dir: Path | None = None) -> ServiceResult | None:
    base = Path(dir) if dir is not None else results_dir()
    try:
        with open(base / f"{safe_name(service)}.json", encoding="utf-8") as f:
            return result_from_dict(json.load(f))
    except Exception:
        return None


def refresh_notes(r: ServiceResult, notes: dict[str, Note]) -> ServiceResult:
    apis = [replace(a, note=notes.get(f"{a.method} {a.path}")) for a in r.apis]
    sleepers = [a for a in apis if a.status in (Status.ASLEEP, Status.RIP)]
    sleeping = sum(1 for a in sleepers if a.note is None)
    total = sum(1 for a in apis if a.in_spec)
    summary = summary_sentence(total, sleeping, r.counts.get("rip", 0),
                               r.reliability.coverage_days)
    return replace(r, apis=apis, sleeping=sleeping,
                   sleeping_noted=len(sleepers) - sleeping, summary=summary)
