"""자체 완결형 HTML 리포트와 Edge headless PDF 내보내기."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from napi.models import ServiceResult
from napi.store import result_to_dict

_UI = Path(__file__).parent / "ui"
_PDF_TIMEOUT_SEC = 60


class ReportError(Exception):
    """사용자에게 그대로 보여 줄 한국어 메시지를 담는다."""


def _embed_json(data: dict) -> str:
    text = json.dumps(data, ensure_ascii=False)
    return (text.replace("</", "<\\/")
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
