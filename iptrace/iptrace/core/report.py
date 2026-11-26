"""보고서 저장(HTML 기본, 엑셀·CSV)과 회신 요약 (PLAN.md 5.8).

파일은 사용자가 버튼(또는 CLI -o)을 눌렀을 때만 만든다. 엔진은 이 모듈을 자동으로 부르지 않는다.
"""
import csv
import os
import re
from datetime import datetime

from .detector import TAG_LABELS

_QUERY_VALUE = re.compile(r"=([^&]*)")


def mask_query(url: str) -> str:
    path, sep, query = url.partition("?")
    return path + (sep + _QUERY_VALUE.sub("=***", query) if sep else "")


def _fmt_time(t, with_date=True):
    if not t:
        return ""
    return t.strftime("%Y-%m-%d %H:%M:%S" if with_date else "%m-%d %H:%M:%S")


def _period(result):
    r = result.request
    return f"{r.start:%Y-%m-%d %H:%M} ~ {r.end:%Y-%m-%d %H:%M}"


def default_filename(result, ext=".html") -> str:
    return f"IPtrace_점검결과_{result.started:%Y%m%d_%H%M}{ext}"


def attachment_filename(result, prefix="") -> str:
    """메일 첨부 파일 이름: 제목 앞말을 붙인다 ('[IPtrace]' -> '[IPtrace]점검결과_20261123_1451.html')."""
    p = re.sub(r'[\\/:*?"<>|]+', "", prefix or "").strip()
    if not p:
        return default_filename(result)
    sep = "" if p.endswith(("]", ")", "_", "-")) else "_"
    return f"{p}{sep}점검결과_{result.started:%Y%m%d_%H%M}.html"


def status_text(s) -> str:
    parts = []
    if s.c2xx:
        parts.append(f"200대 {s.c2xx}건")
    if s.c3xx:
        parts.append(f"300대 {s.c3xx}건")
    if s.c4xx:
        parts.append(f"400대 {s.c4xx}건" + (f"(404 {s.c404})" if s.c404 else ""))
    if s.c5xx:
        parts.append(f"500대 {s.c5xx}건" + (f"(500 {s.c500})" if s.c500 else ""))
    return ", ".join(parts)


def reply_text(result) -> str:
    """보안팀 회신용 요약 문구."""
    c = result.summary_counts()
    servers = result.servers
    failed = result.failed_servers
    server_line = f"서버 {len(servers)}대"
    if failed:
        server_line += f" ({', '.join(s.name for s in failed)} 수집 실패, 재점검 필요)"
    lines = [
        "[침해의심 IP 로그 점검 결과]",
        f"- 점검 일시: {result.started:%Y-%m-%d %H:%M}",
        f"- 대상: 침해의심 IP {c['iocs']}개 / {server_line}",
        f"- 조회 기간: {_period(result)}",
        f"- 결과: 로그 발견 {c['accessed']}개, 조회 범위 내 미발견 {c['not_accessed']}개",
    ]
    n = 0
    for s in result.summaries:
        if not s.accessed:
            continue
        n += 1
        lines.append(f"  {n}) {s.ioc_key}: {s.requests}건 ({status_text(s)}) - {s.verdict} [{s.grade}]")
    if not c["accessed"]:
        lines.append("  - 조회 범위 내에서 해당 IP가 남은 로그를 찾지 못함")
    lines.append(f"- 분석 범위: {result.coverage_text()}")
    if result.saved_path:
        lines.append(f"- 상세: {os.path.basename(result.saved_path)}")
    return "\n".join(lines)


_GRADE_ORDER = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
_GRADE_COLOR = {"CRITICAL": ("#fde2e2", "#991b1b"), "HIGH": ("#ffedd5", "#9a3412"),
                "MEDIUM": ("#fef6c7", "#854d0e"), "LOW": ("#eef1f4", "#374151")}
MEMO = "정보보안팀에서 전달받은 침해의심 IP의 서버 접속 기록을 확인한 결과를 공유드립니다."


def _found(result):
    """로그가 발견된 IP를 위험도 높은 순으로."""
    rank = {g: i for i, g in enumerate(_GRADE_ORDER)}
    return sorted((s for s in result.summaries if s.accessed), key=lambda s: (rank.get(s.grade, 9), -s.requests))


def email_title(result, prefix="") -> str:
    c = result.summary_counts()
    head = "위험 IP 발견" if c["danger"] else ("로그 발견" if c["accessed"] else "발견 없음")
    return (f"{prefix} 침해의심 IP 점검 결과 [{head}] 위험 {c['danger']} · 발견 {c['accessed']} / 점검 {c['iocs']}개 "
            f"({result.started:%m-%d %H:%M})").strip()


def email_text(result, memo=MEMO, attach_name="") -> str:
    """메일 본문 (글자만). 보내기 창 미리보기와 [본문 복사]에 쓴다."""
    c = result.summary_counts()
    failed = result.failed_servers
    lines = [memo.strip(), "", "■ 요약",
             f"- 점검 IP {c['iocs']}개 중 로그 발견 {c['accessed']}개 (위험 CRITICAL·HIGH {c['danger']}개), "
             f"조회 범위 내 미발견 {c['not_accessed']}개",
             f"- 조회 기간: {_period(result)}",
             f"- 점검 서버: {len(result.servers)}대" + (f" (수집 실패 {len(failed)}대: {', '.join(s.name for s in failed)}, 재점검 필요)"
                                                    if failed else " (모두 완료)"),
             f"- 점검 일시: {result.started:%Y-%m-%d %H:%M}", "", "■ 로그가 발견된 IP"]
    found = _found(result)
    for n, s in enumerate(found, 1):
        lines.append(f"{n}) [{s.grade}] {s.ioc_key} - {s.requests}건 ({status_text(s)})")
        lines.append(f"   판정: {s.verdict}")
    if not found:
        lines.append("- 조회 범위 내에서 해당 IP가 남긴 로그를 찾지 못했습니다.")
    lines += ["", f"■ 분석 범위: {result.coverage_text()}"]
    if attach_name:
        lines.append(f"■ 상세: 첨부한 {attach_name}을 브라우저로 열면 IP별 로그·확인 근거·원본 위치를 볼 수 있습니다.")
    lines += ["", "※ 위험도는 탐지 규칙 기준이며, AI 분석은 참고용입니다. (IPtrace에서 만든 메일)"]
    return "\n".join(lines)


def email_html(result, memo=MEMO, attach_name="") -> str:
    """메일 본문 (HTML). 메일 프로그램에서 깨지지 않게 표·인라인 스타일만 쓴다."""
    from html import escape as e
    c = result.summary_counts()
    failed = result.failed_servers
    font = "font-family:'Malgun Gothic',Arial,sans-serif;"
    td = "padding:6px 10px;border-bottom:1px solid #e3e7ec;font-size:13px;vertical-align:top;"
    th = "padding:7px 10px;background:#0f766e;color:#fff;font-size:12px;text-align:left;white-space:nowrap;"

    def card(name, value, color):
        return (f"<td style='padding:10px 14px;border:1px solid #e3e7ec;border-top:3px solid {color};background:#fff'>"
                f"<div style='font-size:12px;color:#6b7785'>{name}</div>"
                f"<div style='font-size:22px;font-weight:bold;color:{color}'>{value}<span style='font-size:12px'> 개</span></div></td>")
    rows = []
    for s in _found(result):
        bg, fg = _GRADE_COLOR.get(s.grade, ("#eef1f4", "#374151"))
        rows.append(f"<tr><td style='{td}'><span style='background:{bg};color:{fg};padding:2px 8px;border-radius:10px;"
                    f"font-size:11px;font-weight:bold'>{e(s.grade)}</span></td>"
                    f"<td style='{td}font-family:Consolas,monospace'>{e(s.ioc_key)}</td>"
                    f"<td style='{td}text-align:right'>{s.requests}</td><td style='{td}'>{e(status_text(s))}</td>"
                    f"<td style='{td}'>{e(s.verdict)}</td></tr>")
    table = (f"<table cellspacing='0' cellpadding='0' style='border-collapse:collapse;width:100%;border:1px solid #e3e7ec'>"
             f"<tr><th style='{th}'>위험도</th><th style='{th}'>의심 IP</th><th style='{th}text-align:right'>요청</th>"
             f"<th style='{th}'>응답</th><th style='{th}'>판정</th></tr>{''.join(rows)}</table>" if rows else
             "<p style='font-size:13px;color:#6b7785'>조회 범위 내에서 해당 IP가 남긴 로그를 찾지 못했습니다.</p>")
    server = (f"{len(result.servers)}대 (모두 완료)" if not failed else
              f"{len(result.servers)}대 · <b style='color:#b91c1c'>수집 실패 {len(failed)}대: "
              f"{e(', '.join(s.name for s in failed))} (재점검 필요)</b>")
    info = "".join(f"<tr><td style='{td}color:#6b7785;width:90px'>{k}</td><td style='{td}'>{v}</td></tr>" for k, v in (
        ("조회 기간", e(_period(result))), ("점검 서버", server), ("점검 일시", f"{result.started:%Y-%m-%d %H:%M}"),
        ("분석 범위", e(result.coverage_text()))))
    attach = (f"<p style='font-size:13px;margin:16px 0 0'>상세 내용은 첨부한 <b>{e(attach_name)}</b>을 브라우저로 열어 확인하세요. "
              "IP별 로그, 확인 근거, 원본 위치(파일:줄)가 들어 있습니다.</p>" if attach_name else "")
    return (f"<div style='{font}color:#1f2933;max-width:820px'>"
            f"<p style='font-size:14px;margin:0 0 14px'>{e(memo.strip()).replace(chr(10), '<br>')}</p>"
            f"<div style='font-size:16px;font-weight:bold;color:#0f766e;margin:0 0 8px'>침해의심 IP 로그 점검 결과</div>"
            f"<table cellspacing='6' cellpadding='0' style='margin:0 -6px'><tr>"
            f"{card('점검한 IP', c['iocs'], '#475569')}{card('로그 발견', c['accessed'], '#2563eb')}"
            f"{card('위험 (CRITICAL·HIGH)', c['danger'], '#b91c1c')}{card('미발견', c['not_accessed'], '#6b7785')}</tr></table>"
            f"<table cellspacing='0' cellpadding='0' style='border-collapse:collapse;width:100%;margin:10px 0 16px'>{info}</table>"
            f"<div style='font-size:14px;font-weight:bold;margin:0 0 6px'>로그가 발견된 IP</div>{table}{attach}"
            f"<p style='font-size:11px;color:#6b7785;margin:18px 0 0;border-top:1px solid #e3e7ec;padding-top:8px'>"
            f"위험도는 탐지 규칙 기준이며, AI 분석은 참고용입니다. 이 메일은 IPtrace에서 만들었습니다.</p></div>")


def _summary_rows(result):
    c = result.summary_counts()
    r = result.request
    rows = [
        ("점검 일시", f"{result.started:%Y-%m-%d %H:%M:%S}"),
        ("조회 기간", _period(result)),
        ("점검 IP 수", c["iocs"]),
        ("로그 발견", c["accessed"]),
        ("조회 범위 내 미발견", c["not_accessed"]),
        ("404 있는 IP", c["with_404"]),
        ("500 있는 IP", c["with_500"]),
    ]
    rows += [(f"등급 {g}", n) for g, n in c["grades"].items()]
    rows.append(("해석 실패 줄", result.parse_errors))
    rows.append(("분석 범위", result.coverage_text()))
    rows.append(("규칙 버전", result.rule_version))
    rows.append(("", ""))
    rows.append(("서버", "결과"))
    for s in result.servers:
        lines_txt = f"{s.total_lines:,}줄 중 " if s.total_lines else "서버에서 거른 "
        rows.append((s.name, f"{s.state} · 파일 {len(s.files)}개 · {lines_txt}후보 {s.candidate_lines}줄 · 매칭 {s.matched_lines}줄"
                     + (f" · 해석 실패 {s.parse_errors}줄" if s.parse_errors else "")
                     + (f" · 끝까지 못 본 파일 {len(s.limited_files)}개" if s.limited_files else "")
                     if s.ok else f"{s.state}: {s.reason}"))
    rows.append(("", ""))
    rows.append(("참고", "접근로그에는 POST 본문이 남지 않아 POST 공격은 경로·빈도·응답코드로만 추정합니다."))
    rows.append(("입력 IP 목록 해시", r.ioc_text_hash))
    return rows


def _ip_rows(result):
    head = ["위험도", "의심 IP", "접속", "요청", "2xx", "3xx", "4xx", "404", "5xx", "500", "판정 근거",
            "서버별 요청", "매칭 IP", "최초 시각", "최종 시각", "User-Agent", "근거 위치"]
    rows = []
    for s in result.summaries:
        rows.append([s.grade if s.accessed else "-", s.ioc_key, "확인" if s.accessed else "미발견", s.requests,
                     s.c2xx, s.c3xx, s.c4xx, s.c404, s.c5xx, s.c500, s.verdict,
                     ", ".join(f"{k} {v}" for k, v in s.server_counts.items()),
                     ", ".join(s.matched_ips) if s.is_range else "", _fmt_time(s.first_seen),
                     _fmt_time(s.last_seen), " | ".join(s.user_agents),
                     " / ".join(f"{sv} {os.path.basename(f)}:{ln}" for sv, f, ln, _ in s.evidence)])
    return head, rows


def _log_rows(result, mask=False):
    head = ["의심 IP", "매칭 IP", "매칭 필드", "시각", "서버", "메서드", "URL", "응답", "크기", "태그",
            "User-Agent", "파일", "줄번호", "원문"]
    rows = []
    for r in sorted(result.records, key=lambda x: (x.ioc_key, x.time is None, x.time)):
        url = mask_query(r.url) if mask else r.url
        raw = r.raw.replace(r.url, url) if mask else r.raw
        field = "해석 실패" if r.unparsed else r.matched_field
        rows.append([r.ioc_key, r.client_ip, field, _fmt_time(r.time) or ("시간 모름" if r.unparsed else ""),
                     r.server, r.method, url,
                     r.status, r.bytes, ", ".join(TAG_LABELS.get(t, t) for t in r.tags), r.user_agent,
                     r.file, r.line_no, raw])
    return head, rows


def _file_rows(result):
    head = ["서버", "파일", "크기", "수정 시각", "전체 줄 수", "서버에서 받은 줄", "SHA256"]
    rows = []
    for s in result.servers:
        for f in s.files:
            rows.append([s.name, f.path, f.size, datetime.fromtimestamp(f.mtime).strftime("%Y-%m-%d %H:%M:%S"),
                         f.lines or "-", f.matched if s.server_filter else "-", f.sha256])
    return head, rows


def _warn_rows(result, ioc_result=None):
    head = ["구분", "내용"]
    rows = [["수집 실패", f"{s.name}: {s.reason}"] for s in result.failed_servers]
    if ioc_result:
        rows += [["제외된 IP", f"{ln}번째 줄 {v}: {why}"] for ln, v, why in ioc_result.excluded]
        rows += [["입력 오류", f"{ln}번째 줄: {tok}"] for ln, _, tok in ioc_result.errors]
    if result.parse_errors:
        rows.append(["해석 실패", f"{result.parse_errors}줄 (로그 형식과 맞지 않아 결과에 넣지 못한 줄. "
                                  "의심 IP가 든 줄이면 놓친 기록일 수 있으니 형식을 확인하세요)"])
    for s in result.servers:
        for f, ln, text, has_ip in s.parse_error_lines[:20]:
            rows.append(["해석 실패 줄", f"{s.name} {f}:{ln}{' [의심 IP 포함]' if has_ip else ''}  {text[:300]}"])
    return head, rows


def _ai_rows(result):
    head = ["의심 IP", "규칙 등급", "AI 분석 (참고)", "모델", "분석 시각", "보낸 줄", "민감값 가림"]
    grades = {s.ioc_key: s.grade for s in result.summaries}
    rows = [[k, grades.get(k, ""), n.text, n.model, _fmt_time(n.time), f"{n.lines_sent}/{n.lines_total}",
             "예" if n.masked else "아니오"] for k, n in result.ai_notes.items()]
    return head, rows


def has_xlsx() -> bool:
    """openpyxl이 설치돼 있으면 엑셀로, 없으면 CSV로 저장한다."""
    try:
        import openpyxl  # noqa: F401
        return True
    except ImportError:
        return False


def save(result, path, mask=False, ioc_result=None):
    """파일 확장자로 형식을 고른다: .html(보고서, 기본) / .xlsx(엑셀) / .csv('<이름>_csv' 폴더에 여러 개).
    엑셀을 골랐는데 openpyxl이 없으면 CSV로 저장한다.

    돌려주는 값: ("html", 파일 경로) / ("xlsx", 파일 경로) / ("csv", [파일 경로...])
    """
    low = str(path).lower()
    if low.endswith((".html", ".htm")):
        from .report_html import save_html
        return "html", save_html(result, path, mask, ioc_result)
    if has_xlsx() and not low.endswith(".csv"):
        return "xlsx", save_xlsx(result, path, mask, ioc_result)
    folder = os.path.splitext(str(path))[0] + "_csv"
    return "csv", save_csv(result, folder, mask, ioc_result)


# 엑셀 꾸미기: 프로그램 화면과 같은 색 (청록 머리글, 위험도 색)
_FONT = "맑은 고딕"
_TEAL, _TEAL_LIGHT, _LINE, _SUB, _BAND = "0F766E", "E6F2F1", "D5DBE1", "6B7785", "F7F9FB"
_GRADE_STYLE = {"CRITICAL": ("FDE2E2", "991B1B"), "HIGH": ("FFEDD5", "9A3412"), "MEDIUM": ("FEF6C7", "854D0E"),
                "LOW": ("EEF1F4", "374151"), "NONE": ("FFFFFF", "9AA3AD"), "-": ("FFFFFF", "9AA3AD")}
_STATE_COLOR = {"완료": "15803D", "부분 분석": "B45309", "연결 실패": "B91C1C", "수집 실패": "B91C1C"}
# 시트별 칸: (너비, 정렬, 숫자 형식). 정렬 l/c/r
_COLS = {
    "IP별 결과": [(11, "c"), (18, "l"), (7, "c"), (8, "r#"), (7, "r#"), (7, "r#"), (7, "r#"), (7, "r#"), (7, "r#"),
               (7, "r#"), (58, "lw"), (24, "lw"), (20, "l"), (19, "c"), (19, "c"), (40, "lw"), (46, "lw")],
    "상세 로그": [(18, "l"), (16, "l"), (10, "c"), (19, "c"), (16, "l"), (8, "c"), (60, "l"), (7, "c"), (10, "r#"),
              (18, "l"), (32, "l"), (44, "l"), (8, "r#"), (110, "m")],
    "분석 파일 목록": [(16, "l"), (64, "l"), (14, "r#"), (19, "c"), (12, "r#"), (16, "r#"), (70, "m")],
    "경고·오류": [(14, "c"), (110, "lw")],
    "AI 분석": [(18, "l"), (11, "c"), (100, "lw"), (18, "l"), (19, "c"), (9, "c"), (11, "c")],
}
_TAB = {"요약": _TEAL, "IP별 결과": "2563EB", "상세 로그": "64748B", "분석 파일 목록": "94A3B8", "경고·오류": "DC2626",
        "AI 분석": "7C3AED"}


def _lines_needed(text, width) -> int:
    """줄바꿈 칸의 줄 수 어림 (엑셀은 열 때 행 높이를 다시 맞추지 않는다). 한글은 2칸으로 센다."""
    n = 0
    for line in str(text or "").split("\n"):
        w = sum(2 if ord(ch) > 0x2E80 else 1 for ch in line)
        n += max(1, -(-w // max(1, int(width * 1.1))))
    return n


def save_xlsx(result, path, mask=False, ioc_result=None) -> str:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    thin = Side(style="thin", color=_LINE)
    box = Border(left=thin, right=thin, top=thin, bottom=thin)

    def fill(color):
        return PatternFill("solid", fgColor=color)

    def font(size=10, bold=False, color="1F2933", mono=False):
        return Font(name="Consolas" if mono else _FONT, size=size, bold=bold, color=color)

    def finish(ws, landscape=True):
        ws.sheet_properties.tabColor = _TAB.get(ws.title, _TEAL)
        ws.page_setup.orientation = "landscape" if landscape else "portrait"
        ws.page_setup.fitToWidth, ws.page_setup.fitToHeight = 1, 0
        ws.sheet_properties.pageSetUpPr.fitToPage = True
        ws.print_options.horizontalCentered = True

    def table(ws, head, rows, grade_col=None, freeze="B2", row_style=None):
        """머리글(청록) + 테두리 + 칸별 정렬·숫자 형식 + 위험도 색 + 줄무늬"""
        cols = _COLS.get(ws.title, [(14, "l")] * len(head))
        ws.append(head)
        ws.row_dimensions[1].height = 24
        for c in ws[1]:
            c.font, c.fill, c.border = font(10, True, "FFFFFF"), fill(_TEAL), box
            c.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
        for i, row in enumerate(rows):
            ws.append(row)
            r = ws.max_row
            g = row[grade_col] if grade_col is not None else None
            band = fill(_GRADE_STYLE[g][0]) if g in ("CRITICAL", "HIGH", "MEDIUM") else (fill(_BAND) if i % 2 else None)
            lines = 1
            for j, c in enumerate(ws[r]):
                width, kind = cols[j] if j < len(cols) else (14, "l")
                c.border = box
                c.font = font(9, mono=True) if kind == "m" else font()
                c.alignment = Alignment(horizontal={"c": "center", "r": "right"}.get(kind[0], "left"),
                                        vertical="top" if "w" in kind else "center", wrap_text="w" in kind)
                if "#" in kind and isinstance(c.value, (int, float)):
                    c.number_format = "#,##0"
                if band:
                    c.fill = band
                if "w" in kind and c.value:
                    lines = max(lines, _lines_needed(c.value, width))
            if g in _GRADE_STYLE:   # 위험도 칸은 배지처럼 굵게
                gc = ws.cell(r, grade_col + 1)
                gc.font = font(10, True, _GRADE_STYLE[g][1])
            if row_style:
                row_style(ws, r, row)
            if lines > 1:
                ws.row_dimensions[r].height = min(409, 15 * lines + 4)
        for j, (width, _) in enumerate(cols[:len(head)], 1):
            ws.column_dimensions[get_column_letter(j)].width = width
        ws.freeze_panes = freeze
        if rows:
            ws.auto_filter.ref = ws.dimensions
        finish(ws)

    # ---------------------------------------------------------------- 요약
    ws = wb.active
    ws.title = "요약"
    ws.sheet_view.showGridLines = False
    for col, w in zip("ABCDEF", (22, 17, 17, 17, 17, 17)):
        ws.column_dimensions[col].width = w
    c = result.summary_counts()
    req = result.request
    ws["A1"] = "침해의심 IP 로그 점검 결과"
    ws["A1"].font = font(18, True, _TEAL)
    ws.merge_cells("A1:F1")
    ws.row_dimensions[1].height = 34
    ws["A2"] = f"점검 {result.started:%Y-%m-%d %H:%M}  ·  조회 기간 {_period(result)}  ·  규칙 {result.rule_version or '-'}"
    ws["A2"].font = font(10, color=_SUB)
    ws.merge_cells("A2:F2")
    kpis = (("점검한 IP", c["iocs"], "475569"), ("로그 발견", c["accessed"], "2563EB"),
            ("조회 범위 내 미발견", c["not_accessed"], "64748B"), ("위험 (CRITICAL·HIGH)", c["danger"], "DC2626"),
            ("404 있는 IP", c["with_404"], "D97706"), ("500 있는 IP", c["with_500"], "7C3AED"))
    for j, (name, val, color) in enumerate(kpis, 1):
        top, num = ws.cell(4, j, name), ws.cell(5, j, val)
        accent = Side(style="thick", color=color)
        top.font, top.fill = font(9, color=_SUB), fill("F8FAFC")
        top.border = Border(top=accent, left=thin, right=thin)
        top.alignment = Alignment(horizontal="left", vertical="center", indent=1)
        num.font, num.fill = font(20, True, color), fill("F8FAFC")
        num.border = Border(bottom=thin, left=thin, right=thin)
        num.alignment = Alignment(horizontal="left", vertical="center", indent=1)
    ws.row_dimensions[4].height, ws.row_dimensions[5].height = 22, 34

    row = 7

    def section(title):
        nonlocal row
        cell = ws.cell(row, 1, title)
        cell.font = font(12, True, _TEAL)
        for j in range(1, 7):
            ws.cell(row, j).border = Border(bottom=Side(style="medium", color=_TEAL))
        ws.row_dimensions[row].height = 22
        row += 1

    def kv(name, value, color=None):
        nonlocal row
        a, b = ws.cell(row, 1, name), ws.cell(row, 2, value)
        a.font, a.fill, a.border = font(10, True, "374151"), fill(_TEAL_LIGHT), box
        a.alignment = Alignment(vertical="center", indent=1)
        b.font = font(10, color=color or "1F2933")
        b.alignment = Alignment(vertical="center", wrap_text=True, indent=1, horizontal="left")
        if isinstance(value, int):
            b.number_format = "#,##0"
        ws.merge_cells(start_row=row, start_column=2, end_row=row, end_column=6)
        for j in range(2, 7):
            ws.cell(row, j).border = box
        lines = _lines_needed(value, 80)
        ws.row_dimensions[row].height = max(20, 15 * lines + 5)
        row += 1

    section("점검 정보")
    kv("점검 일시", f"{result.started:%Y-%m-%d %H:%M:%S}")
    kv("조회 기간", _period(result))
    kv("점검 IP 수", c["iocs"])
    kv("로그 발견", c["accessed"])
    kv("조회 범위 내 미발견", c["not_accessed"])
    kv("404 있는 IP", c["with_404"])
    kv("500 있는 IP", c["with_500"])
    kv("해석 실패 줄", result.parse_errors, "B45309" if result.parse_errors else None)
    kv("분석 범위", result.coverage_text())
    kv("규칙 버전", result.rule_version)
    kv("입력 IP 목록 해시", req.ioc_text_hash)
    row += 1
    section("위험도별 IP 수")
    for g, n in c["grades"].items():
        a, b = ws.cell(row, 1, f"등급 {g}"), ws.cell(row, 2, n)
        bg, fg = _GRADE_STYLE[g]
        a.font, a.fill, a.border = font(10, True, fg), fill(bg if g != "NONE" else "F8FAFC"), box
        a.alignment = Alignment(vertical="center", indent=1)
        b.font, b.border = font(10, True), box
        b.alignment = Alignment(horizontal="right", vertical="center", indent=1)
        ws.row_dimensions[row].height = 20
        row += 1
    row += 1
    section("서버별 결과")
    for j, h in enumerate(("서버", "상태", "파일", "서버에서 받은 줄", "매칭 줄", "비고"), 1):
        cell = ws.cell(row, j, h)
        cell.font, cell.fill, cell.border = font(10, True, "FFFFFF"), fill(_TEAL), box
        cell.alignment = Alignment(horizontal="center", vertical="center")
    ws.row_dimensions[row].height = 22
    row += 1
    for s in result.servers:
        note = []
        if s.ok:
            if s.parse_errors:
                note.append(f"해석 실패 {s.parse_errors}줄")
            if s.limited_files:
                note.append(f"끝까지 못 본 파일 {len(s.limited_files)}개")
        else:
            note.append(s.reason)
        vals = (s.name, s.state, len(s.files) if s.ok else "-", s.candidate_lines if s.ok else "-",
                s.matched_lines if s.ok else "-", ", ".join(note))
        for j, v in enumerate(vals, 1):
            cell = ws.cell(row, j, v)
            cell.border, cell.font = box, font()
            cell.alignment = Alignment(horizontal="left" if j in (1, 6) else "center", vertical="center",
                                       wrap_text=j == 6, indent=1 if j in (1, 6) else 0)
            if isinstance(v, int):
                cell.number_format = "#,##0"
        ws.cell(row, 2).font = font(10, True, _STATE_COLOR.get(s.state, "1F2933"))
        ws.row_dimensions[row].height = max(20, 15 * _lines_needed(vals[5], 17) + 5)
        row += 1
    row += 1
    note = ws.cell(row, 1, "참고: 접근로그에는 POST 본문이 남지 않아 POST 공격은 경로·빈도·응답코드로만 추정합니다. "
                           "위험도는 규칙(rules/signatures.json) 기준이며, AI 분석은 참고용입니다.")
    note.font = font(9, color=_SUB)
    note.alignment = Alignment(wrap_text=True, vertical="top")
    ws.merge_cells(start_row=row, start_column=1, end_row=row, end_column=6)
    ws.row_dimensions[row].height = 32
    finish(ws, landscape=False)

    # ---------------------------------------------------------------- 표 시트
    table(wb.create_sheet("IP별 결과"), *_ip_rows(result), grade_col=0, freeze="C2")

    def log_style(ws, r, row):
        st = row[7]
        color = ("15803D" if isinstance(st, int) and st < 300 else "B45309" if isinstance(st, int) and st < 500
                 else "B91C1C" if isinstance(st, int) else None)
        if color:
            ws.cell(r, 8).font = font(10, True, color)
        if row[9] and any(TAG_LABELS[t] in row[9] for t in ("SQLI", "XSS", "TRAVERSAL", "RCE", "JAVA", "WEBSHELL")):
            ws.cell(r, 10).font = font(10, True, "B91C1C")
        if row[2] == "해석 실패":
            ws.cell(r, 3).font = font(10, color="B45309")
    table(wb.create_sheet("상세 로그"), *_log_rows(result, mask), freeze="B2", row_style=log_style)
    table(wb.create_sheet("분석 파일 목록"), *_file_rows(result), freeze="A2")

    def warn_style(ws, r, row):
        ws.cell(r, 1).font = font(10, True, "B91C1C" if row[0] in ("수집 실패", "입력 오류") else "B45309")
    table(wb.create_sheet("경고·오류"), *_warn_rows(result, ioc_result), freeze="A2", row_style=warn_style)
    if result.ai_notes:   # [AI 분석]을 한 IP가 있을 때만
        head, rows = _ai_rows(result)
        table(wb.create_sheet("AI 분석"), head, rows, grade_col=1, freeze="B2")
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    wb.save(path)
    result.saved_path = str(path)
    return str(path)


def save_csv(result, folder, mask=False, ioc_result=None) -> list:
    """openpyxl이 없을 때: 같은 구성을 csv 여러 개로 저장한다."""
    os.makedirs(folder, exist_ok=True)
    stem = default_filename(result, "")
    out = []
    sheets = [("IP별결과", _ip_rows(result)), ("상세로그", _log_rows(result, mask)),
              ("파일목록", _file_rows(result)), ("경고오류", _warn_rows(result, ioc_result))]
    if result.ai_notes:
        sheets.append(("AI분석", _ai_rows(result)))
    for name, (head, rows) in sheets:
        p = os.path.join(folder, f"{stem}_{name}.csv")
        with open(p, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.writer(f)
            w.writerow(head)
            w.writerows(rows)
        out.append(p)
    p = os.path.join(folder, f"{stem}_요약.csv")
    with open(p, "w", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerows(_summary_rows(result))
    out.append(p)
    result.saved_path = out[0]
    return out


def console_summary(result) -> str:
    c = result.summary_counts()
    ok = sum(1 for s in result.servers if s.ok)
    files = sum(len(s.files) for s in result.servers)
    lines = [f"[IPtrace] 조회기간 {_period(result)} | 서버 {ok}/{len(result.servers)} 성공 | 로그 파일 {files}개",
             f"IP {c['iocs']}개 중 로그 발견 {c['accessed']}개, 조회 범위 내 미발견 {c['not_accessed']}개 "
             f"(404 있음 {c['with_404']}, 500 있음 {c['with_500']}, 위험 {c['danger']})",
             f"분석 범위: {result.coverage_text()}"]
    for s in result.summaries:
        if s.accessed:
            lines.append(f"  {s.grade:<8} {s.ioc_key:<18} 요청 {s.requests:<5} {status_text(s):<30} {s.verdict}")
    for s in result.failed_servers:
        lines.append(f"  ! {s.name} 수집 실패: {s.reason}")
    return "\n".join(lines)
