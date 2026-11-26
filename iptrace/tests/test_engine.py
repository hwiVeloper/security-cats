import gzip
import os
import shutil
import threading
from datetime import timedelta

import pytest
from openpyxl import load_workbook

from conftest import FIX, kst
from iptrace.core import engine, ioc, report
from iptrace.core.models import CheckRequest

LOG = os.path.join(FIX, "access_sample.log")


def run(cfg, pattern=LOG, start=kst(2026, 7, 1), end=kst(2026, 10, 21, 23, 59, 59), **kw):
    res = ioc.parse_text(open(os.path.join(FIX, "ioc_typed.list"), encoding="utf-8").read(),
                         cfg["general"]["own_networks"])
    req = CheckRequest(iocs=res.entries, start=start, end=end, local_logs=[("web01", pattern)],
                       log_format="test_xff")
    return engine.run_check(req, cfg, **kw)


def by_key(result):
    return {s.ioc_key: s for s in result.summaries}


def test_plan_11_2_expected_results(cfg):
    result = run(cfg)
    s = by_key(result)
    expect = {  # 요청, 2xx, 3xx, 4xx, 404, 5xx, 500, 등급
        "203.0.113.45": (3, 3, 0, 0, 0, 0, 0, "CRITICAL"),
        "198.51.100.7": (5, 0, 0, 4, 3, 1, 1, "MEDIUM"),
        "192.0.2.10": (1, 1, 0, 0, 0, 0, 0, "LOW"),
        "203.0.113.0/28": (1, 1, 0, 0, 0, 0, 0, "LOW"),
        "198.51.100.200": (0, 0, 0, 0, 0, 0, 0, "NONE"),
        "203.0.113.50": (0, 0, 0, 0, 0, 0, 0, "NONE"),
    }
    for key, exp in expect.items():
        x = s[key]
        assert (x.requests, x.c2xx, x.c3xx, x.c4xx, x.c404, x.c5xx, x.c500, x.grade) == exp, key
    assert any("웹셸" in r for r in s["203.0.113.45"].reasons)
    reasons = " ".join(s["198.51.100.7"].reasons)
    assert "SQLi" in reasons and "5xx" in reasons and "경로 스캔" in reasons and "스캐너" in reasons
    assert s["203.0.113.0/28"].matched_ips == ["203.0.113.9"]
    assert s["203.0.113.0/28"].matched_fields == ["xff"]
    assert result.parse_errors == 1
    assert all(r.client_ip != "192.0.2.99" and r.remote_ip != "10.1.1.10" or r.matched_field == "xff"
               for r in result.records)
    assert result.servers[0].ok and result.servers[0].files[0].sha256
    assert [x.grade for x in result.summaries][:2] == ["CRITICAL", "MEDIUM"]
    assert s["198.51.100.200"].verdict == "조회 범위 내 미발견"
    assert s["203.0.113.45"].server_counts == {"web01": 3}
    assert s["203.0.113.45"].evidence[0][2] == 7  # 원본 7번째 줄
    assert result.servers[0].state == "부분 분석" and result.servers[0].parse_errors == 1


def test_engine_writes_no_files(cfg, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    run(cfg)
    assert list(tmp_path.iterdir()) == [tmp_path / "logs"]  # 감사 로그만


def test_gzip_logs_give_same_result(cfg, tmp_path):
    gz = tmp_path / "access_sample.log.gz"
    with open(LOG, "rb") as src, gzip.open(gz, "wb") as dst:
        shutil.copyfileobj(src, dst)
    a = {k: (v.requests, v.grade) for k, v in by_key(run(cfg)).items()}
    b = {k: (v.requests, v.grade) for k, v in by_key(run(cfg, str(gz))).items()}
    assert a == b


def test_period_filter(cfg):
    result = run(cfg, start=kst(2026, 10, 1), end=kst(2026, 10, 31))
    s = by_key(result)
    assert s["203.0.113.45"].requests == 3
    assert s["198.51.100.7"].requests == 0 and s["192.0.2.10"].requests == 0


def test_missing_server_fails_without_stopping_others(cfg):
    res = ioc.parse_text("203.0.113.45")
    req = CheckRequest(iocs=res.entries, start=kst(2026, 7, 1), end=kst(2026, 10, 31),
                       local_logs=[("web01", LOG), ("web09", "D:/nope/*.log")], log_format="test_xff")
    result = engine.run_check(req, cfg)
    assert [s.ok for s in result.servers] == [True, False]
    assert "없습니다" in result.servers[1].reason
    assert by_key(result)["203.0.113.45"].requests == 3


def test_cancel(cfg):
    ev = threading.Event()
    ev.set()
    with pytest.raises(engine.Cancelled):
        run(cfg, cancel=ev)


def test_period_range():
    now = kst(2026, 10, 21, 15, 30)
    assert engine.period_range("today", now) == (kst(2026, 10, 21), now)
    s, e = engine.period_range("yesterday", now)
    assert s == kst(2026, 10, 20) and e.date() == s.date() and e.hour == 23
    assert engine.period_range("7", now)[0] == kst(2026, 10, 15)
    with pytest.raises(ValueError, match="최대 7일"):            # 서버 점검은 최대 7일
        engine.period_range("30", now)
    assert engine.period_range("30", now, limit=False)[0] == kst(2026, 9, 22)   # 로컬 폴더는 제한 없음
    with pytest.raises(ValueError, match="앞이어야"):
        engine.period_range("custom", now, kst(2026, 10, 2), kst(2026, 10, 1))
    end = kst(2026, 10, 22) - timedelta(microseconds=1)                          # 10-15 ~ 10-21 23:59:59 = 7일
    assert engine.period_range("custom", now, kst(2026, 10, 15), end) == (kst(2026, 10, 15), end)
    with pytest.raises(ValueError, match=r"최대 7일입니다 \(지금 8일\)"):
        engine.period_range("custom", now, kst(2026, 10, 14), end)
    assert engine.period_range("custom", now, kst(2026, 10, 1), end, limit=False)[0] == kst(2026, 10, 1)


def test_report_xlsx_and_reply(cfg, tmp_path):
    result = run(cfg)
    p = report.save_xlsx(result, tmp_path / "r.xlsx", mask=True)
    wb = load_workbook(p)
    assert wb.sheetnames == ["요약", "IP별 결과", "상세 로그", "분석 파일 목록", "경고·오류"]
    detail = [r for r in wb["상세 로그"].iter_rows(values_only=True)][1:]
    assert any("cmd=***" in (r[6] or "") for r in detail)
    text = report.reply_text(result)
    assert "로그 발견 4개, 조회 범위 내 미발견 2개" in text and "203.0.113.45" in text and "r.xlsx" in text
    assert "분석 범위: 서버 1/1" in text and "해석 실패 1줄" in text
    summary = dict(r[:2] for r in wb["요약"].iter_rows(values_only=True))
    assert summary["규칙 버전"] == result.rule_version and result.rule_version


def test_report_html(cfg, tmp_path):
    """HTML 보고서: 파일 하나(외부 파일 없이), IP별 로그 펼침, 쿼리 가림, 글자 이스케이프, AI 분석 포함."""
    from datetime import datetime
    from iptrace.core.ai import AiNote
    result = run(cfg)
    top = next(s for s in result.summaries if s.accessed)
    result.ai_notes[top.ioc_key] = AiNote(top.ioc_key, "[요약] <script>x</script> 스캔", "m", datetime(2026, 10, 27))
    kind, p = report.save(result, tmp_path / "r.html", mask=True)
    text = open(p, encoding="utf-8").read()
    assert kind == "html" and text.startswith("<!doctype html>") and result.saved_path == p
    assert "http://" not in text.split("<style>")[0] and "<link" not in text and "src=" not in text   # 내부망: 외부 파일 없음
    assert "cmd=***" in text and "<script>x</script>" not in text and "&lt;script&gt;x" in text
    assert "203.0.113.45" in text and "AI 분석 (참고)" in text and text.count("class='detail'") == sum(
        1 for s in result.summaries if s.accessed)
    assert report.save(result, tmp_path / "r.xlsx")[0] == "xlsx"
    assert report.default_filename(result).endswith(".html")


def test_merge_retry(cfg):
    a = run(cfg)
    a.servers[0].ok = False
    a.records = []
    b = run(cfg)
    merged = engine.merge_retry(a, b, cfg)
    assert merged.servers[0].ok and by_key(merged)["203.0.113.45"].requests == 3



def test_was_app_log_is_checked_like_web_log(cfg, tmp_path):
    """웹 로그가 아닌 업무 로그도 형식만 맞추면 IP별 발견·시간·원본 위치가 나온다 (404·500 판정만 없음)."""
    log = tmp_path / "app.log"
    log.write_text("\n".join([
        "[2026-10-20 09:00:00] [INFO] login ok user=kim ip=203.0.113.45",
        "[2026-10-20 09:00:05] [WARN] login fail user=admin ip=203.0.113.45, agent=curl",
        "[2026-10-20 09:01:00] [INFO] health check ip=10.1.1.10",
        "[2026-06-01 09:00:00] [INFO] login ok user=old ip=203.0.113.45",   # 기간 밖
        "free text line without ip",
    ]) + "\n", encoding="utf-8")
    res = ioc.parse_text("203.0.113.45\n198.51.100.7")
    req = CheckRequest(iocs=res.entries, start=kst(2026, 10, 1), end=kst(2026, 10, 21, 23, 59, 59),
                       local_logs=[("was01", str(log))], log_format="[%{%Y-%m-%d %H:%M:%S}t] %* ip=%h%*")
    result = engine.run_check(req, cfg)
    s = by_key(result)
    assert s["203.0.113.45"].requests == 2 and s["198.51.100.7"].requests == 0
    assert s["203.0.113.45"].first_seen.isoformat() == "2026-10-20T09:00:00+09:00"
    assert s["203.0.113.45"].evidence[0][2] == 1          # 원본 위치(줄 번호)
    assert s["203.0.113.45"].c404 == 0 and s["203.0.113.45"].c500 == 0
    text = report.reply_text(result)
    assert "[침해의심 IP 로그 점검 결과]" in text and "로그 발견 1개" in text



def test_parse_failures_are_kept_per_server(cfg, tmp_path):
    """해석 실패 줄은 서버별로 위치·원문을 남기고, 의심 IP가 든 줄인지 표시한다 (놓친 기록일 수 있음)."""
    log = tmp_path / "a.log"
    good = open(LOG, encoding="utf-8").read().splitlines()[0]
    log.write_text("\n".join([good, "garbage line 203.0.113.45 without format", "plain broken line"]) + "\n",
                   encoding="utf-8")
    res = ioc.parse_text("203.0.113.45")
    req = CheckRequest(iocs=res.entries, start=kst(2026, 1, 1), end=kst(2026, 12, 31), local_logs=[("M-T-WEBAPI01", str(log))],
                       log_format="test_xff")
    st = engine.run_check(req, cfg).servers[0]
    assert st.parse_errors == 2 and st.parse_errors_ip == 1 and st.state == "부분 분석"
    assert [(ln, has_ip) for _, ln, _, has_ip in st.parse_error_lines] == [(2, True), (3, False)]
    assert st.parse_error_lines[0][2].startswith("garbage line 203.0.113.45")


def test_unparsed_lines_with_suspect_ip_go_into_results(cfg, tmp_path):
    """형식과 맞지 않아도 의심 IP와 정확히 맞는 줄은 결과에 '해석 실패'로 들어간다 (시간을 찾으면 기간도 적용)."""
    log = tmp_path / "a.log"
    good = open(LOG, encoding="utf-8").read().splitlines()[0]
    log.write_text("\n".join([
        good,
        "2026-10-20 09:00:00 ERROR login fail from 203.0.113.45 user=admin",   # 형식 불일치 + 의심 IP + 기간 안
        "2026-01-01 09:00:00 ERROR old 203.0.113.45",                          # 기간 밖 -> 제외
        "no time here 203.0.113.45",                                           # 시간 모름 -> 포함
        "near miss 203.0.113.450 and 11.203.0.113.45",                         # 정확히 맞지 않음 -> 제외
        "plain broken line",
    ]) + "\n", encoding="utf-8")
    res = ioc.parse_text("203.0.113.45")
    req = CheckRequest(iocs=res.entries, start=kst(2026, 10, 1), end=kst(2026, 10, 31, 23, 59, 59),
                       local_logs=[("was01", str(log))], log_format="test_xff")
    result = engine.run_check(req, cfg)
    st = result.servers[0]
    unp = [r for r in result.records if r.unparsed]
    assert [r.line_no for r in unp] == [2, 4] and st.unparsed_matched == 2
    assert unp[0].time.isoformat() == "2026-10-20T09:00:00+09:00" and unp[1].time is None
    assert all(r.matched_field == "unparsed" and r.client_ip == "203.0.113.45" for r in unp)
    s = by_key(result)["203.0.113.45"]
    assert s.requests == 2 and "형식이 맞지 않는 줄에서 발견 2건 (원문 확인 필요)" in s.reasons
    head, rows = report._log_rows(result)
    assert sum(1 for r in rows if r[2] == "해석 실패") == 2 and any(r[3] == "시간 모름" for r in rows)



def test_each_file_is_parsed_with_its_paths_format(cfg, tmp_path):
    """한 서버에서 접근 로그와 WebtoB 에러 로그를 각 경로의 형식으로 해석한다."""
    from iptrace.core import config as cfgmod
    from iptrace.core.collector import LocalSource
    from iptrace.core.detector import Signatures
    from iptrace.core.matcher import Matcher
    from iptrace.core.models import CheckResult
    acc = tmp_path / "access.log"
    acc.write_text(open(LOG, encoding="utf-8").read(), encoding="utf-8")
    err = tmp_path / "error.log"
    err.write_text("[2026-10-03T02:22:37] [CLIENT(203.0.113.45)] [E] [ERR-00045] A request does not belong to any "
                   "virtual host or node. Access is denied. {server address=10.2.0.11:443, host:10.2.0.11} "
                   "GET /containers/json HTTP/1.1\n", encoding="utf-8")
    src = LocalSource("M-T-WEBAPI01", str(tmp_path / "*.log"))
    orig = src.list_files

    def files_with_formats(start):
        out = orig(start)
        for f in out:
            f.log_format = "webtob_error" if f.path.endswith("error.log") else "test_xff"
        return out
    src.list_files = files_with_formats
    cfg["log_formats"]["webtob_error"] = cfgmod.normalize({})["log_formats"]["webtob_error"]
    res = ioc.parse_text("203.0.113.45")
    req = CheckRequest(iocs=res.entries, start=kst(2026, 7, 1), end=kst(2026, 10, 31))
    result = CheckResult(request=req, started=kst(2026, 10, 26))
    import threading
    st = engine._scan_source(src, cfg["log_formats"]["test_xff"], req, Matcher(res.entries, []),
                             Signatures.load(os.path.join(os.path.dirname(FIX), "..", "rules", "signatures.json")),
                             None, None, threading.Lock(), result,
                             resolve=lambda n: cfg["log_formats"][n])
    assert st.ok and st.parse_errors == 1        # 접근 로그 샘플의 깨진 줄 하나만 (에러 로그는 형식대로 읽힘)
    errs = [r for r in result.records if r.file.endswith("error.log")]
    assert len(errs) == 1 and not errs[0].unparsed
    assert (errs[0].method, errs[0].path, errs[0].status) == ("GET", "/containers/json", None)
    assert "SENSITIVE" in errs[0].tags


def test_parse_when_short_inputs_and_inclusive_end():
    now = kst(2026, 10, 26, 15, 0)
    pw = engine.parse_when
    assert pw("2026-10-21 13:00", now=now) == kst(2026, 10, 21, 13, 0)
    assert pw("2026/10/21", now=now) == pw("2026.10.21", now=now) == pw("20261021", now=now) == kst(2026, 10, 21)
    assert pw("10/21", now=now) == pw("10-21", now=now) == pw("1021", now=now) == kst(2026, 10, 21)
    assert pw("  10/21   1300 ", now=now) == kst(2026, 10, 21, 13, 0)
    assert pw("12/30", now=now) == kst(2025, 12, 30)          # 연도 없이 미래면 작년
    assert pw("오늘", now=now) == kst(2026, 10, 26)
    assert pw("어제 13:00", now=now) == kst(2026, 10, 25, 13, 0)
    # 끝: 날짜만 -> 그날 끝, 분까지 -> 그 분의 끝, 시만 -> 그 시의 끝
    assert pw("2026-10-21", end=True, now=now) == kst(2026, 10, 22) - timedelta(microseconds=1)
    assert pw("2026-10-21 23:59", end=True, now=now) == kst(2026, 10, 22) - timedelta(microseconds=1)
    assert pw("2026-10-21 13", end=True, now=now) == kst(2026, 10, 21, 14) - timedelta(microseconds=1)
    assert pw("2026-10-21 13:00:05", end=True, now=now) == kst(2026, 10, 21, 13, 0, 6) - timedelta(microseconds=1)
    for bad in ("", "abc", "13/40", "2026-02-30", "10/21 25:00"):
        with pytest.raises(ValueError):
            pw(bad, now=now)


def test_email_body_text_and_html(cfg):
    """메일 본문: 위험도 높은 IP부터, HTML은 표·인라인 스타일, 결과 글자는 이스케이프."""
    result = run(cfg)
    c = result.summary_counts()
    title = report.email_title(result, "[IPtrace]")
    assert title.startswith("[IPtrace] 침해의심 IP 점검 결과 [") and f"점검 {c['iocs']}개" in title
    text = report.email_text(result, "안녕하세요.", "r.html")
    found = [ln for ln in text.splitlines() if ln[:1].isdigit() and ") [" in ln]
    order = ["CRITICAL", "HIGH", "MEDIUM", "LOW"]
    grades = [ln.split("[", 1)[1].split("]", 1)[0] for ln in found]
    assert grades == sorted(grades, key=order.index) and len(found) == c["accessed"]
    assert text.startswith("안녕하세요.") and "첨부한 r.html" in text and "첨부한" not in report.email_text(result)
    html = report.email_html(result, "<b>메모</b>\n둘째 줄", "r.html")
    assert "&lt;b&gt;메모&lt;/b&gt;<br>둘째 줄" in html and "<table" in html and "<script" not in html
    assert html.count("<tr><td style=") >= c["accessed"] and "<b>r.html</b>" in html


def test_attachment_filename_uses_title_prefix(cfg):
    result = run(cfg)
    ts = f"{result.started:%Y%m%d_%H%M}"
    assert report.attachment_filename(result, "[IPtrace]") == f"[IPtrace]점검결과_{ts}.html"
    assert report.attachment_filename(result, "보안팀") == f"보안팀_점검결과_{ts}.html"
    assert report.attachment_filename(result, r"a/b:c\d") == f"abcd_점검결과_{ts}.html"      # 파일 이름에 못 쓰는 글자는 뺌
    assert report.attachment_filename(result, "") == report.default_filename(result)
