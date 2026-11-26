import dataclasses
import json
import os
import re
import subprocess

import pytest

from napi import report
from napi.models import ApiResult, Status
from tests.test_store import make_result


def test_report_is_self_contained():
    html = report.build_report_html(make_result())
    assert not re.search(r"""(?:src|href)\s*=\s*["']?\s*https?://""", html, re.I)
    assert "<script src" not in html.lower()
    assert "<link" not in html.lower()
    assert "/*NAPI_CSS*/" not in html and "/*NAPI_JS*/" not in html
    assert '"__NAPI_DATA__"' not in html


def test_report_embeds_data_safely():
    harmless = report.build_report_html(make_result())
    r = make_result()
    evil = dataclasses.replace(r.apis[0], path="/x</script><b>")
    r2 = dataclasses.replace(r, apis=[evil, *r.apis[1:]], summary="a\u2028b</script>")
    html = report.build_report_html(r2)
    assert html.count("</script>") == harmless.count("</script>")
    assert "<\\/script>" in html
    assert "\u2028" not in html


def test_report_neutralises_comment_open():
    harmless = report.build_report_html(make_result())
    r = make_result()
    evil = dataclasses.replace(r.apis[0], path="<!--<script>")
    html = report.build_report_html(dataclasses.replace(r, apis=[evil, *r.apis[1:]]))
    assert html.count("</script>") == harmless.count("</script>")
    assert html.count("<!--") == harmless.count("<!--")
    assert "<\\u0021--<script>" in html


def test_report_data_roundtrips_verbatim():
    r = make_result()
    nasty = ('/*NAPI_JS*/ "__NAPI_DATA__" $1 ' + chr(92) + '1 ' + chr(92) + ' ' + chr(92) + 'n </x> <!--'
             + chr(0x2028))
    html = report.build_report_html(dataclasses.replace(r, summary=nasty))
    line = next(l for l in html.splitlines() if l.lstrip().startswith("var data = "))
    block = line.split("var data = ", 1)[1].rstrip().rstrip(";")
    assert json.loads(block)["summary"] == nasty


def test_report_contains_summary_and_title():
    r = make_result()
    html = report.build_report_html(r)
    assert r.summary in html
    assert "napi 리포트" in html
    assert r.service in html


def test_export_html_writes_file(tmp_path):
    out = report.export_html(make_result(), tmp_path / "r.html")
    assert out.read_text(encoding="utf-8").startswith("<!doctype html>")


def test_export_pdf_with_edge(tmp_path):
    if os.environ.get("NAPI_RUN_EDGE_TESTS") != "1":
        pytest.skip("set NAPI_RUN_EDGE_TESTS=1 to run real Edge")
    if report.find_edge() is None:
        pytest.skip("Edge not installed")
    out = report.export_pdf(make_result(), tmp_path / "r.pdf")
    assert out.read_bytes().startswith(b"%PDF")


def test_export_pdf_no_edge(monkeypatch, tmp_path):
    monkeypatch.setattr(report, "find_edge", lambda: None)
    with pytest.raises(report.ReportError, match="Edge"):
        report.export_pdf(make_result(), tmp_path / "r.pdf")


def test_export_pdf_failure_and_timeout(monkeypatch, tmp_path):
    edge = tmp_path / "msedge.exe"

    def fake_ok(cmd, **kw):
        return subprocess.CompletedProcess(cmd, 0)  # no PDF produced

    monkeypatch.setattr(report.subprocess, "run", fake_ok)
    with pytest.raises(report.ReportError):
        report.export_pdf(make_result(), tmp_path / "r.pdf", edge=edge)

    def fake_timeout(cmd, **kw):
        raise subprocess.TimeoutExpired(cmd, 60)

    monkeypatch.setattr(report.subprocess, "run", fake_timeout)
    with pytest.raises(report.ReportError, match="오래"):
        report.export_pdf(make_result(), tmp_path / "r.pdf", edge=edge)


def test_export_pdf_cleans_temp_dir(monkeypatch, tmp_path):
    seen = {}

    def fake(cmd, **kw):
        arg = [c for c in cmd if c.startswith("--print-to-pdf=")][0]
        seen["pdf"] = arg.split("=", 1)[1]
        seen["uri"] = cmd[-1]
        with open(seen["pdf"], "wb") as f:
            f.write(b"%PDF-fake")
        return subprocess.CompletedProcess(cmd, 0)

    monkeypatch.setattr(report.subprocess, "run", fake)
    out = report.export_pdf(make_result(), tmp_path / "r.pdf", edge=tmp_path / "e.exe")
    assert out.read_bytes().startswith(b"%PDF")
    assert seen["uri"].startswith("file:///") and seen["uri"].endswith("?print=1")
    assert not os.path.exists(os.path.dirname(seen["pdf"]))


def test_report_carries_skipped_line_counts():
    html = report.build_report_html(make_result())  # WAS-01: lines 1200, parse_fail 7
    line = next(l for l in html.splitlines() if l.lstrip().startswith("var data = "))
    data = json.loads(line.split("var data = ", 1)[1].rstrip().rstrip(";"))
    assert [(s["lines"], s["parse_fail"]) for s in data["servers"]] == [(1200, 7), (0, 0)]
    assert '"건너뛴 줄 "' in html  # render.js가 리포트에 인라인되어 서버 표에 표시한다
