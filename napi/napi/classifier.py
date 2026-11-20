"""매칭 결과와 서버별 스캔 결과를 API 상태·신뢰도·요약 문구로 바꾼다."""
from __future__ import annotations

from datetime import date, timedelta

from napi.matcher import MatchedStats, MatchResult
from napi.models import (
    ApiResult,
    Reliability,
    ServerScanResult,
    ServiceConfig,
    SpecOperation,
    Status,
)

_FILE_ERROR_CODES = {"unreadable", "decompress_failed"}
_PARSE_FAIL_RATIO = 0.2  # 이 비율 이상 읽지 못하면 로그 형식 설정을 의심한다


def _ymd(d: date) -> str:
    return d.strftime("%Y%m%d")


def _parse(s: str) -> date:
    return date(int(s[:4]), int(s[4:6]), int(s[6:8]))


def _try_parse(s: str) -> date | None:
    """로그에서 온 YYYYMMDD. 달력에 없는 날짜(11월 31일, 0년 등)면 None (I2)."""
    try:
        return _parse(s)
    except ValueError:
        return None


def window_bounds(window: str, today: date) -> tuple[str | None, str]:
    until = _ymd(today)
    if window == "all":
        return None, until
    return _ymd(today - timedelta(days=int(window))), until


def probe_rest(servers: list[ServerScanResult]) -> dict[str, int]:
    ok = [s for s in servers if s.ok]
    return {
        "distinct": sum(s.probe404_rest[0] for s in ok),
        "count": sum(s.probe404_rest[1] for s in ok),
    }


def _spec_status(deprecated: bool, st: MatchedStats, awake_from: str) -> Status:
    if deprecated:
        return Status.SLEEPTALK if st.calls > 0 else Status.RIP
    if st.calls == 0:
        return Status.ASLEEP
    if st.last is not None and st.last >= awake_from:
        return Status.AWAKE
    return Status.DROWSY


def _reliability(
    servers: list[ServerScanResult], cfg: ServiceConfig, since: str | None, until: str
) -> Reliability:
    reasons: list[str] = []
    coverages: list[int] = []
    until_d = _parse(until)
    stale_before = _ymd(until_d - timedelta(days=cfg.thresholds.awake_days))
    if not servers:
        reasons.append("등록된 서버가 없어요")
    for s in servers:
        if not s.ok:
            reasons.append(f"{s.server} 스캔 실패: {s.error}")
            continue
        oldest = s.oldest_date
        lines, failed = s.total_lines, s.total_parse_fail
        parse_reason = None
        if lines > 0 and failed / lines >= _PARSE_FAIL_RATIO:
            parse_reason = (f"{s.server}은 로그 줄의 {failed * 100 // lines}%를 읽지 못했어요"
                            " — 로그 형식 설정을 확인해 주세요")
        if not s.files or oldest is None:
            # 줄은 있는데 하나도 못 읽었으면 "파일 없음"이 아니라 형식 문제다
            reasons.append(parse_reason or f"{s.server}에서 로그 파일을 찾지 못했어요")
        else:
            start = oldest if since is None else max(since, oldest)
            start_d = _try_parse(start)
            if start_d is None:
                # 범위 검사를 지나도 달력에 없는 날짜일 수 있다. 기간 계산에서 이 서버를 뺀다.
                reasons.append(f"{s.server}의 로그 날짜 {start}를 읽을 수 없어요"
                               " — 로그 형식 설정을 확인해 주세요")
            else:
                days = (until_d - start_d).days
                coverages.append(days)
                if days < cfg.thresholds.min_coverage_days:
                    reasons.append(f"{s.server}은 로그가 {days}일치뿐이에요")
            if parse_reason:
                reasons.append(parse_reason)
            newest = s.newest_date
            if newest is not None and newest < stale_before:
                reasons.append(f"{s.server}의 최신 로그가 {newest[:4]}-{newest[4:6]}-{newest[6:8]}예요"
                               " — 최근 로그가 포함됐는지 확인해 주세요")
        if s.truncated:
            reasons.append(f"{s.server} 결과가 너무 많아 일부만 집계됐어요")
        if any(code in _FILE_ERROR_CODES for code, _ in s.file_errors):
            reasons.append(f"{s.server}에서 읽지 못한 로그 파일이 있어요")
        for code, glob in s.file_errors:
            if code == "glob_empty":
                reasons.append(f"{s.server}의 경로 {glob}에서 파일을 찾지 못했어요")
    coverage = min(coverages) if coverages else 0
    return Reliability("low" if reasons else "ok", coverage, reasons)


def classify(
    ops: list[SpecOperation],
    match: MatchResult,
    servers: list[ServerScanResult],
    cfg: ServiceConfig,
    window: str,
    today: date,
) -> tuple[list[ApiResult], Reliability, dict[str, int], int, int]:
    since, until = window_bounds(window, today)
    awake_from = _ymd(today - timedelta(days=cfg.thresholds.awake_days))
    apis: list[ApiResult] = []
    for op in ops:
        if op.key in match.excluded_ops:
            continue
        st = match.per_op.get(op.key) or MatchedStats()
        apis.append(ApiResult(
            method=op.method, path=op.path,
            status=_spec_status(op.deprecated, st, awake_from),
            calls=st.calls, first=st.first, last=st.last,
            by_status=dict(st.by_status), by_server=dict(st.by_server),
            in_spec=True, deprecated=op.deprecated, tags=list(op.tags),
            summary=op.summary, operation_id=op.operation_id,
            note=cfg.notes.get(op.key),
        ))
    for (method, path), st in sorted(
        match.unmatched.items(), key=lambda kv: (kv[0][1], kv[0][0])
    ):
        ghost = any(k != "4xx" for k in st.by_status)
        apis.append(ApiResult(
            method=method, path=path,
            status=Status.GHOST if ghost else Status.PROBE,
            calls=st.calls, first=st.first, last=st.last,
            by_status=dict(st.by_status), by_server=dict(st.by_server),
            in_spec=False, note=cfg.notes.get(f"{method} {path}"),
        ))

    counts = {s.value: 0 for s in Status}
    for a in apis:
        counts[a.status.value] += 1
    sleepers = [a for a in apis if a.status in (Status.ASLEEP, Status.RIP)]
    sleeping = sum(1 for a in sleepers if a.note is None)
    sleeping_noted = len(sleepers) - sleeping
    reliability = _reliability(servers, cfg, since, until)
    return apis, reliability, counts, sleeping, sleeping_noted


def summary_sentence(total: int, sleeping: int, rip: int, coverage_days: int) -> str:
    if sleeping == 0:
        return f"API {total}개 모두 깨어 있어요! 🐾"
    if coverage_days >= 360:
        period = "1년째"
    elif coverage_days >= 30:
        period = f"{coverage_days // 30}개월째"
    else:
        period = f"{coverage_days}일째"
    text = f"API {total}개 중 {sleeping}개가 {period} 자고 있어요."
    if rip > 0:
        text += f" 그중 {rip}개는 deprecated라 바로 지워도 될 것 같아요 💤"
    return text
