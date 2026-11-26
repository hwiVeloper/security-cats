"""점검 실행 진입점 (PLAN.md 5.1). GUI와 CLI는 이 모듈의 run_check()만 호출한다.

- 엔진은 파일을 만들지 않는다. 결과는 CheckResult로 돌려준다(감사 로그만 예외).
- 서버 한 대가 실패해도 나머지는 계속한다.
- cancel(threading.Event)이 설정되면 읽기를 멈추고 Cancelled를 낸다.
"""
import hashlib
import re
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from .. import paths
from . import aggregator, audit
from .collector import AuthGuard, CollectError, LocalSource, SshSource
from .config import effective_ssh
from .detector import Signatures
from .matcher import Matcher
from .models import CheckResult, ServerStatus
from .parser import LogParser, extract_ips, guess_time, resolve_format, unparsed_record

KST = timezone(timedelta(hours=9))
MAX_PARSE_ERROR_SAMPLES = 20      # 결과 전체 (엑셀 경고 시트)
MAX_PARSE_ERROR_LINES = 50        # 서버마다 (화면의 [읽지 못한 줄 보기])
PARSE_SAMPLE_LINES = 1000  # 파일마다 형식 검사를 위해 모두 해석하는 줄 수


class Cancelled(Exception):
    pass


@dataclass
class Event:
    kind: str            # log, server_started, file_progress, server_done, server_failed, phase
    server: str = ""
    message: str = ""
    files: int = 0
    file_index: int = 0
    file_name: str = ""
    done_bytes: int = 0
    total_bytes: int = 0
    matched: int = 0


_WHEN = re.compile(r"^(?:(\d{4})[-./]?)?(\d{1,2})[-./]?(\d{1,2})"
                   r"(?:[ T]+(\d{1,2})(?::?(\d{2}))?(?::?(\d{2}))?)?$")


def parse_when(text: str, end: bool = False, now: datetime = None) -> datetime:
    """조회 기간 직접 입력을 시각으로 바꾼다.

    받는 모양: 2026-10-21 13:00, 2026/10/21, 2026.10.21, 20261021 1300, 10-21, 10/21, 1021, 오늘, 어제 13:00
    - 연도가 없으면 올해(미래 날짜가 되면 작년).
    - 시각이 없으면 시작은 00:00, 끝은 그날 23:59:59.
    - 끝 시각은 적은 단위까지 포함한다 (23:59 -> 23:59:59, 13 -> 13:59:59).
    """
    now = now or datetime.now(KST)
    s = " ".join((text or "").split())
    for word, days in (("오늘", 0), ("어제", 1)):
        if s.startswith(word):
            s = (now - timedelta(days=days)).strftime("%Y-%m-%d") + s[len(word):]
    m = _WHEN.match(s)
    if not m:
        raise ValueError("날짜 형식이 올바르지 않습니다 (예: 2026-10-21 13:00, 10/21, 1021, 어제)")
    y, mo, d, hh, mi, ss = m.groups()
    try:
        t = datetime(int(y) if y else now.year, int(mo), int(d), tzinfo=KST)
        if not y and t.date() > now.date():   # 연도 없이 미래 날짜면 작년 (1월에 12/30 입력)
            t = t.replace(year=t.year - 1)
        if hh is None:
            return t + (timedelta(days=1) - timedelta(microseconds=1) if end else timedelta(0))
        t = t.replace(hour=int(hh), minute=int(mi or 0), second=int(ss or 0))
    except ValueError as e:
        raise ValueError("날짜 형식이 올바르지 않습니다 (예: 2026-10-21 13:00, 10/21, 1021, 어제)") from e
    if end:   # 적은 단위의 끝까지 포함
        t += (timedelta(seconds=1) if ss else timedelta(minutes=1) if mi else timedelta(hours=1)) - timedelta(microseconds=1)
    return t


MAX_DAYS = 7   # 서버 점검의 최대 조회 기간(일). 길수록 서버에서 읽는 로그 파일이 늘어난다


def check_span(start: datetime, end: datetime) -> None:
    """서버를 점검할 때 조회 기간이 MAX_DAYS일을 넘으면 ValueError."""
    if end - start > timedelta(days=MAX_DAYS):
        days = -(-(end - start) // timedelta(days=1))   # 올림
        raise ValueError(f"조회 기간은 최대 {MAX_DAYS}일입니다 (지금 {days}일). 서버 부하를 줄이려고 막아 두었습니다. "
                         "기간을 나눠서 점검하세요.")


def period_range(kind: str, now: datetime = None, start: datetime = None, end: datetime = None, limit: bool = True):
    """조회 기간 버튼 값 -> (시작, 끝). today / yesterday / 7 / custom.

    limit이면 최대 MAX_DAYS일까지만 받는다 (서버를 읽지 않는 로컬 폴더 점검은 limit=False).
    """
    now = now or datetime.now(KST)
    today0 = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if kind == "today":
        return today0, now
    if kind == "yesterday":
        return today0 - timedelta(days=1), today0 - timedelta(microseconds=1)
    if str(kind).isdigit():
        if limit and int(kind) > MAX_DAYS:
            raise ValueError(f"조회 기간은 최대 {MAX_DAYS}일입니다 (지금 {int(kind)}일). 서버 부하를 줄이려고 막아 두었습니다.")
        return today0 - timedelta(days=int(kind) - 1), now
    if kind == "custom":
        if not (start and end):
            raise ValueError("직접 지정한 기간이 올바르지 않습니다")
        if start > end:
            raise ValueError("시작이 끝보다 앞이어야 합니다")
        if limit:
            check_span(start, end)
        return start, end
    raise ValueError(f"알 수 없는 기간: {kind}")


def ioc_hash(iocs) -> str:
    return hashlib.sha256("\n".join(sorted(e.key for e in iocs)).encode()).hexdigest()[:16]


def _sources(req, cfg, on_unknown_host):
    out = []
    known = str(paths.resolve(cfg["ssh"].get("known_hosts", "config/known_hosts")))
    guard = AuthGuard()   # 한 번의 점검 안에서 틀린 계정·비밀번호를 다른 서버에 다시 보내지 않는다
    for s in req.servers:
        ssh = effective_ssh(cfg, s)
        out.append((SshSource(s, ssh, req.passwords.get(s["name"]), known, on_unknown_host, guard=guard),
                    resolve_format(cfg, s.get("format", "webtob_combined"))))
    for name, pattern in req.local_logs:
        out.append((LocalSource(name, pattern), resolve_format(cfg, req.log_format)))
    return out


def _add_unparsed(line, server, path, line_no, matcher, req, result, st, lock):
    """해석 실패 줄에서 IP를 찾아 의심 IP와 정확히 맞으면 '해석 실패' 기록으로 넣는다.

    시간을 찾으면 조회 기간 밖인 줄은 뺀다. 시간을 못 찾으면 기간과 상관없이 넣는다(시간 모름).
    """
    hits, seen = [], set()
    for ip in extract_ips(line):
        for key in matcher.match_ip(ip):
            if key not in seen:
                hits.append((key, ip))
                seen.add(key)
    if not hits:
        return
    t = guess_time(line)
    if t and not (req.start <= t <= req.end):
        return
    with lock:
        for key, ip in hits:
            r = unparsed_record(line, server, path, line_no, ip, t)
            r.ioc_key, r.client_ip, r.matched_field = key, ip, "unparsed"
            result.records.append(r)
        st.unparsed_matched += 1
        st.matched_lines += 1


def _group_by_host(sources) -> list:
    """SSH 주소가 같은 서버 항목을 한 묶음으로 모은다(처음 나온 순서 유지)."""
    groups = {}
    for src, fmt in sources:
        host = src.server.get("host", "").strip().lower() if isinstance(src, SshSource) else ""
        key = ("ssh", host) if host else ("local", src.name)
        groups.setdefault(key, []).append((src, fmt))
    return list(groups.values())


def _scan_source(src, fmt, req, matcher, sigs, on_event, cancel, lock, result, resolve=None):
    """resolve(형식 이름) -> Format 문자열. 파일마다 그 파일을 찾은 로그 경로의 형식으로 해석한다."""
    st = ServerStatus(name=src.name)
    emit = on_event or (lambda e: None)
    parsers = {}

    def parser_for(info):
        f = resolve(info.log_format) if (resolve and info.log_format) else fmt
        if f not in parsers:
            parsers[f] = LogParser(f)
        return parsers[f]

    try:
        parser = LogParser(fmt)
        src.connect()
        files = src.list_files(req.start)
        st.files = files
        total = sum(f.size for f in files) or 1
        emit(Event("server_started", src.name, files=len(files), total_bytes=total,
                   message=f"{src.name} 접속 성공, 대상 파일 {len(files)}개"))
        done_before = 0
        for idx, info in enumerate(files, 1):
            parser = parser_for(info)
            def progress(b, _base=done_before, _info=info, _idx=idx):
                emit(Event("file_progress", src.name, files=len(files), file_index=_idx,
                           file_name=_info.path.replace("\\", "/").rsplit("/", 1)[-1],
                           done_bytes=_base + b, total_bytes=total, matched=st.matched_lines))
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            progress(0)
            remote = src.server_filter
            st.server_filter = remote
            # 원격: 서버가 의심 IP 문자열이 든 줄만 보내 준다.
            # 로컬: 파일마다 처음 일부 줄은 모두 해석해 형식 오류를 찾고, 그 뒤로는 사전 필터에 걸린 줄만 해석한다.
            sample_left = 0 if remote else PARSE_SAMPLE_LINES
            pre = None if remote else matcher.prefilter
            for n, (line_no, raw) in enumerate(src.read_lines(info, progress, matcher.filter_strings), 1):
                if n % 2000 == 0 and cancel is not None and cancel.is_set():
                    raise Cancelled()
                if not remote:
                    st.total_lines += 1
                if sample_left:
                    sample_left -= 1
                elif pre is not None and not pre.search(raw):
                    continue
                st.candidate_lines += 1
                line = raw.decode("utf-8", errors="replace").rstrip("\r\n")
                m = parser.match(line)
                if not m:
                    if line.strip():
                        # 서버에서 받은 줄은 모두 의심 IP 문자열이 든 줄. 로컬은 형식 확인용 앞부분 줄도 섞여 있다
                        has_ip = remote or pre is None or bool(pre.search(raw))
                        with lock:
                            st.parse_errors += 1
                            st.parse_errors_ip += int(has_ip)
                            result.parse_errors += 1
                            if len(st.parse_error_lines) < MAX_PARSE_ERROR_LINES:
                                st.parse_error_lines.append((info.path, line_no, line[:500], has_ip))
                            if len(result.parse_error_samples) < MAX_PARSE_ERROR_SAMPLES:
                                result.parse_error_samples.append(f"{src.name} {info.path}:{line_no}: {line[:200]}")
                        # 형식은 안 맞아도 의심 IP가 들어 있으면 '해석 실패' 기록으로 결과에 넣는다
                        if has_ip:
                            _add_unparsed(line, src.name, info.path, line_no, matcher, req, result, st, lock)
                    continue
                # 빠른 확인: IP 필드만 먼저 매칭하고, 걸린 줄만 레코드로 만든다
                gd = m.groupdict()
                quick = matcher.match_ip(gd["remote_ip"])
                xff = gd.get("h_x_forwarded_for")
                if not quick and not (xff and xff != "-"):
                    if line_no == 1 and info.first_time is None and gd.get("time"):
                        info.first_time = parser.parse_time(gd["time"])
                    continue
                rec = parser.to_record(m, src.name, info.path, line_no, line)
                if line_no == 1 and info.first_time is None:
                    info.first_time = rec.time
                hits = matcher.match_record(rec)
                if not hits:
                    continue
                if rec.time and not (req.start <= rec.time <= req.end):
                    continue
                rec.tags = sigs.tag(rec)
                with lock:
                    for key, ip, field in hits:
                        r = rec if len(hits) == 1 else _copy(rec)
                        r.ioc_key, r.client_ip, r.matched_field = key, ip, field
                        result.records.append(r)
                st.matched_lines += 1
            done_before += info.size
        firsts = [f.first_time for f in files if f.first_time]
        st.oldest_time = min(firsts) if firsts else None
        emit(Event("server_done", src.name, matched=st.matched_lines,
                   message=f"{src.name} {st.detail_text()}"))
    except Cancelled:
        raise
    except CollectError as e:
        st.ok, st.reason = False, str(e)
        emit(Event("server_failed", src.name, message=str(e)))
    except Exception as e:  # noqa: BLE001 - 서버 하나의 실패가 전체를 멈추지 않게 한다
        st.ok, st.reason = False, f"{type(e).__name__}: {e}"
        emit(Event("server_failed", src.name, message=st.reason))
    finally:
        try:
            src.close()
        except Exception:  # noqa: BLE001
            pass
    return st


def _copy(rec):
    import copy
    return copy.copy(rec)


def run_check(req, cfg, on_event=None, cancel: threading.Event = None, on_unknown_host=None) -> CheckResult:
    """점검을 실행하고 결과를 돌려준다(파일은 만들지 않는다)."""
    result = CheckResult(request=req, started=datetime.now(KST))
    req.ioc_text_hash = req.ioc_text_hash or ioc_hash(req.iocs)
    matcher = Matcher(req.iocs, cfg["general"].get("trusted_proxies", []))
    if req.servers and not matcher.filter_strings:
        raise ValueError("너무 넓은 대역(IPv4 /8 미만, IPv6 /16 미만)은 서버에서 거를 수 없어 점검할 수 없습니다. "
                         "대역을 좁혀 입력하세요.")
    sigs = Signatures.load(paths.resolve(cfg["detect"]["signature_file"]), cfg["detect"].get("upload_paths", []))
    result.rule_version = sigs.version
    lock = threading.Lock()
    sources = _sources(req, cfg, on_unknown_host)
    workers = max(1, int(cfg["ssh"].get("workers", 2)))
    statuses = {}

    def run_group(items):
        # 같은 장비(예: web01-api, web01-fe)는 한 대씩 차례로 읽어 서버에 무거운 작업이 겹치지 않게 한다
        out = {}
        for src, fmt in items:
            if cancel is not None and cancel.is_set():
                raise Cancelled()
            out[src.name] = _scan_source(src, fmt, req, matcher, sigs, on_event, cancel, lock, result,
                                         resolve=lambda name: resolve_format(cfg, name))
        return out

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(run_group, items) for items in _group_by_host(sources)]
        try:
            for fut in futures:
                statuses.update(fut.result())
        except Cancelled:
            if cancel:
                cancel.set()
            raise
    result.servers = [statuses[src.name] for src, _ in sources]
    result.summaries = aggregator.summarize(req.iocs, result.records, int(cfg["detect"]["scan_404_threshold"]))
    result.finished = datetime.now(KST)
    audit.log_check(result)
    return result


def merge_retry(old: CheckResult, new: CheckResult, cfg) -> CheckResult:
    """실패 서버만 다시 점검한 결과(new)를 기존 결과(old)에 합친다."""
    retried = {s.name for s in new.servers}
    old.servers = [s for s in old.servers if s.name not in retried] + new.servers
    old.records = [r for r in old.records if r.server not in retried] + new.records
    old.parse_errors = sum(s.parse_errors for s in old.servers)
    old.summaries = aggregator.summarize(old.request.iocs, old.records, int(cfg["detect"]["scan_404_threshold"]))
    old.finished = new.finished
    return old
