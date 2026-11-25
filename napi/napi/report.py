"""자체 완결형 HTML 리포트, Edge headless PDF, 엑셀(xlsx) 내보내기."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from napi.models import ApiResult, ServiceResult, Status
from napi.store import result_to_dict
from napi.xlsx import Sheet, write_xlsx

_UI = Path(__file__).parent / "ui"
_PDF_TIMEOUT_SEC = 60


class ReportError(Exception):
    """사용자에게 그대로 보여 줄 한국어 메시지를 담는다."""


def _embed_json(data: dict) -> str:
    text = json.dumps(data, ensure_ascii=False)
    return (text.replace("</", "<\\/")
                .replace("<!--", "<\\u0021--")
                .replace(chr(0x2028), "\\u2028")
                .replace(chr(0x2029), "\\u2029"))


def build_report_html(r: ServiceResult) -> str:
    template = (_UI / "report.html").read_text(encoding="utf-8")
    css = (_UI / "css" / "app.css").read_text(encoding="utf-8")
    js = "\n".join((_UI / "js" / name).read_text(encoding="utf-8")
                   for name in ("cat.js", "render.js"))
    data = _embed_json(result_to_dict(r))
    # 데이터는 마지막에 넣는다 (다른 자리표시자 문자열이 데이터에 있어도 건드리지 않게).
    # str.replace는 치환 문자열을 해석하지 않으므로 역슬래시도 안전하다.
    html = template.replace("/*NAPI_CSS*/", css, 1).replace("/*NAPI_JS*/", js, 1)
    head, sep, tail = html.rpartition('"__NAPI_DATA__"')
    return head + data + tail


def export_html(r: ServiceResult, out: Path) -> Path:
    out = Path(out)
    try:
        out.write_text(build_report_html(r), encoding="utf-8")
    except OSError as e:
        raise ReportError(f"파일을 저장하지 못했어요: {e}") from None
    return out


def find_edge() -> Path | None:
    for var in ("ProgramFiles(x86)", "ProgramFiles"):
        base = os.environ.get(var)
        if not base:
            continue
        p = Path(base) / "Microsoft" / "Edge" / "Application" / "msedge.exe"
        if p.is_file():
            return p
    return None


def export_pdf(r: ServiceResult, out: Path, edge: Path | None = None) -> Path:
    out = Path(out)
    edge = edge or find_edge()
    if edge is None:
        raise ReportError("Edge를 찾지 못했어요. HTML로 내보낸 뒤 브라우저에서 인쇄 → PDF로 저장해 주세요")
    tmp = Path(tempfile.mkdtemp(prefix="napi-report-"))
    try:
        html_path = tmp / "report.html"
        html_path.write_text(build_report_html(r), encoding="utf-8")
        pdf_path = tmp / "report.pdf"
        cmd = [str(edge), "--headless=new", "--disable-gpu", "--no-pdf-header-footer",
               f"--user-data-dir={tmp / 'profile'}",
               f"--print-to-pdf={pdf_path}", html_path.as_uri() + "?print=1"]
        try:
            subprocess.run(cmd, timeout=_PDF_TIMEOUT_SEC, check=True,
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL)
        except subprocess.TimeoutExpired:
            raise ReportError("PDF를 만드는 데 너무 오래 걸려서 멈췄어요. HTML로 내보내 주세요") from None
        except (subprocess.CalledProcessError, OSError) as e:
            raise ReportError(f"Edge로 PDF를 만들지 못했어요: {e}") from None
        if not pdf_path.is_file() or pdf_path.stat().st_size == 0:
            raise ReportError("PDF 파일이 만들어지지 않았어요. HTML로 내보낸 뒤 브라우저에서 인쇄해 주세요")
        try:
            out.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(pdf_path, out)
        except OSError as e:
            raise ReportError(f"파일을 저장하지 못했어요: {e}") from None
        return out
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ------------------------------------------------------------------ xlsx

# 화면(render.js STATUS_META)과 같은 이름
STATUS_LABEL = {
    Status.AWAKE: "깨어있음", Status.DROWSY: "꾸벅꾸벅", Status.ASLEEP: "잠듦", Status.RIP: "영면",
    Status.SLEEPTALK: "잠꼬대", Status.GHOST: "유령", Status.PROBE: "외부 탐색 시도",
}
# 요약 시트에 적는 순서 (화면 도넛·범례 순서 + 외부 탐색 시도)
_SUMMARY_ORDER = [Status.AWAKE, Status.DROWSY, Status.ASLEEP, Status.RIP, Status.SLEEPTALK,
                  Status.GHOST, Status.PROBE]
# 응답 분포는 상태코드 구간별 숫자 열로 나눈다 (엑셀에서 정렬·필터·합계가 되게).
# "???" 는 상태코드가 없는 로그 형식에서 온 호출이다.
DIST_BUCKETS = [("2xx", "응답 2xx"), ("3xx", "응답 3xx"), ("4xx", "응답 4xx"), ("5xx", "응답 5xx"),
                ("???", "응답 상태 미상")]
XLSX_HEADERS = (["메서드", "경로", "설명", "상태", "호출 수", "마지막 호출"]
                + [label for _, label in DIST_BUCKETS]
                + ["태그", "유지 사유", "메모 작성자", "메모 날짜"])
_XLSX_WIDTHS = [9, 48, 30, 12, 11, 13] + [11] * len(DIST_BUCKETS) + [20, 36, 12, 12]


def _ymd(s: str | None) -> str:
    """"20261123" → "2026-11-23". 모르는 형식은 그대로."""
    s = s or ""
    return f"{s[:4]}-{s[4:6]}-{s[6:8]}" if len(s) == 8 and s.isdigit() else s


def _dist_cells(by_status: dict[str, int]) -> list[int | None]:
    known = {k for k, _ in DIST_BUCKETS}
    cells: list[int | None] = []
    for key, _ in DIST_BUCKETS:
        n = int(by_status.get(key) or 0)
        if key == "???":  # 구간에 안 맞는 코드(예: 1xx)는 상태 미상에 더한다
            n += sum(int(v or 0) for k, v in by_status.items() if k not in known)
        cells.append(n or None)
    return cells


def _api_row(a: ApiResult) -> list:
    note = a.note
    return ([a.method, a.path, a.summary or "", STATUS_LABEL.get(a.status, str(a.status)),
             int(a.calls), _ymd(a.last)]
            + _dist_cells(a.by_status)
            + [", ".join(a.tags), note.reason if note else "", note.author if note else "",
               note.date if note else ""])


def _log_range(r: ServiceResult) -> str:
    """render.js logRange 와 같은 계산: 분석 기간과 실제로 읽은 로그 범위가 겹치는 구간."""
    oldest = min((s.oldest for s in r.servers if s.oldest), default=None)
    newest = max((s.newest for s in r.servers if s.newest), default=None)
    start = r.since if r.since and (not oldest or r.since > oldest) else oldest
    end = r.until if newest and r.until and newest > r.until else (newest or r.until)
    return f"{_ymd(start) or '—'} ~ {_ymd(end) or '—'}"


def build_report_sheets(r: ServiceResult) -> list[Sheet]:
    """'전체' 시트(화면의 전체 탭: 외부 탐색 시도 제외, 결과 순서 그대로)와 '요약' 시트."""
    rows = [_api_row(a) for a in r.apis if a.status != Status.PROBE]
    summary = [
        ["항목", "값"],
        ["서비스", r.service],
        ["스캔", r.scanned_at.replace("T", " ")[:16]],
        ["로그 기간", _log_range(r)],
        ["서버", f"{len(r.servers)}대"],
        ["명세", r.spec_location],
        ["요약", r.summary],
    ]
    summary += [[STATUS_LABEL[s], int(r.counts.get(s.value, 0))] for s in _SUMMARY_ORDER]
    return [
        Sheet("전체", [XLSX_HEADERS] + rows, _XLSX_WIDTHS, autofilter=True),
        Sheet("요약", summary, [14, 90]),
    ]


def export_xlsx(r: ServiceResult, out: Path) -> Path:
    try:
        return write_xlsx(Path(out), build_report_sheets(r))
    except OSError as e:
        raise ReportError(f"파일을 저장하지 못했어요: {e}") from None
