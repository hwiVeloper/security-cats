"""명세 로딩 → 수집 → 매칭 → 분류를 하나로 묶는 스캔 오케스트레이터."""
from __future__ import annotations

import threading
from datetime import date, datetime

from napi.classifier import classify, probe_rest, summary_sentence, window_bounds
from napi.collector import Collector, ProgressFn
from napi.matcher import match
from napi.models import ServerSummary, ServiceConfig, ServiceResult, Settings
from napi.spec import load_spec


def run_scan(cfg: ServiceConfig, settings: Settings, window: str, collector: Collector,
             progress: ProgressFn, cancel: threading.Event, today: date | None = None,
             spec_loader=load_spec) -> ServiceResult:
    today = today or date.today()
    since, until = window_bounds(window, today)
    progress("*", "spec")
    ops = spec_loader(cfg.spec)  # SpecError는 그대로 올린다
    servers = collector.collect(cfg.servers, since, progress, cancel)

    server_rows = {s.server: list(s.rows) + list(s.probe404) for s in servers if s.ok}
    matched = match(server_rows, ops, cfg.path_mapping, cfg.exclude_paths)
    apis, reliability, counts, sleeping, sleeping_noted = classify(
        ops, matched, servers, cfg, window, today)
    total = sum(1 for a in apis if a.in_spec)
    summary = summary_sentence(total, sleeping, counts["rip"], reliability.coverage_days)
    return ServiceResult(
        service=cfg.name,
        scanned_at=datetime.now().isoformat(timespec="seconds"),
        window=window, since=since, until=until,
        spec_location=cfg.spec.location, summary=summary,
        apis=apis, counts=counts, sleeping=sleeping, sleeping_noted=sleeping_noted,
        reliability=reliability,
        servers=[ServerSummary(s.server, s.ok, s.error, s.oldest_date, s.newest_date,
                               s.elapsed_sec, s.truncated) for s in servers],
        probe_rest=probe_rest(servers),
    )
