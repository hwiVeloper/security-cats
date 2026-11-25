"""엑셀(xlsx) 리포트와 공용 xlsx 작성기."""
from __future__ import annotations

import dataclasses
import zipfile
from xml.etree import ElementTree as ET

import pytest

from napi import report, xlsx
from napi.endpoints import _xlsx_rows
from napi.models import ApiResult, Status
from tests.test_store import make_result

_NS = {"m": "http://schemas.openxmlformats.org/spreadsheetml/2006/main"}


def _with_probe_and_dist():
    r = make_result()
    mixed = dataclasses.replace(r.apis[3], by_status={"2xx": 1200, "4xx": 3, "5xx": 2, "???": 4, "1xx": 1},
                                tags=["주문", "레거시"])
    probe = ApiResult("GET", "/.env", Status.PROBE, 9, "20261101", "20261101",
                      {"4xx": 9}, {"WAS-01": 9}, False)
    return dataclasses.replace(r, apis=[*r.apis[:3], mixed, probe],
                               counts={**r.counts, "awake": 1, "asleep": 1, "rip": 1, "ghost": 1, "probe": 1})


def _rows(path):
    return [cells for _, cells in _xlsx_rows(path.read_bytes())]


def test_all_sheet_matches_all_tab(tmp_path):
    out = report.export_xlsx(_with_probe_and_dist(), tmp_path / "r.xlsx")
    rows = _rows(out)
    assert rows[0] == ["메서드", "경로", "설명", "상태", "호출 수", "마지막 호출",
                       "응답 2xx", "응답 3xx", "응답 4xx", "응답 5xx", "응답 상태 미상",
                       "태그", "유지 사유", "메모 작성자", "메모 날짜"]
    # 전체 탭처럼 외부 탐색 시도는 빼고, 결과 순서 그대로
    assert [r[1] for r in rows[1:]] == ["/v1/a", "/v1/b", "/v1/c", "/ghost"]
    awake = rows[1]
    assert awake[:6] == ["GET", "/v1/a", "s", "깨어있음", "5", "2026-11-10"]
    assert awake[6:11] == ["5", "", "", "", ""]
    assert awake[11:] == ["t", "사유", "홍", "2026-11-01"]
    asleep = rows[2]
    assert asleep[3:6] == ["잠듦", "0", ""]
    assert rows[3][3] == "영면"
    ghost = rows[4]
    assert ghost[3] == "유령"
    assert ghost[6:11] == ["1200", "", "3", "2", "5"]  # 1xx 처럼 구간 밖 코드는 상태 미상에 더한다
    assert ghost[11] == "주문, 레거시"


def test_numbers_are_numeric_cells_and_text_is_text(tmp_path):
    out = report.export_xlsx(_with_probe_and_dist(), tmp_path / "r.xlsx")
    with zipfile.ZipFile(out) as z:
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    cells = {c.get("r"): c for c in sheet.iter(f"{{{_NS['m']}}}c")}
    assert cells["E2"].get("t") is None and cells["E2"].find("m:v", _NS).text == "5"
    assert cells["G5"].find("m:v", _NS).text == "1200"
    assert cells["B2"].get("t") == "inlineStr"
    assert cells["F2"].get("t") == "inlineStr"  # 날짜는 텍스트 (엑셀이 바꾸지 않게)


def test_workbook_has_filter_freeze_and_summary(tmp_path):
    out = report.export_xlsx(_with_probe_and_dist(), tmp_path / "r.xlsx")
    with zipfile.ZipFile(out) as z:
        wb = z.read("xl/workbook.xml").decode("utf-8")
        s1 = z.read("xl/worksheets/sheet1.xml").decode("utf-8")
        s2 = z.read("xl/worksheets/sheet2.xml").decode("utf-8")
        assert z.testzip() is None
    assert '<sheet name="전체" sheetId="1"' in wb and '<sheet name="요약" sheetId="2"' in wb
    assert "'전체'!$A$1:$O$5</definedName>" in wb
    assert '<autoFilter ref="A1:O5"/>' in s1
    assert 'state="frozen"' in s1
    assert "<autoFilter" not in s2
    assert "라운드페이 API" in s2 and "2025-11-19 ~ 2026-11-18" in s2 and "외부 탐색 시도" in s2


def test_summary_sheet_rows(tmp_path):
    sheets = report.build_report_sheets(_with_probe_and_dist())
    summary = dict((r[0], r[1]) for r in sheets[1].rows[1:])
    assert summary["서비스"] == "라운드페이 API"
    assert summary["스캔"] == "2026-11-19 10:00"
    assert summary["서버"] == "2대"
    assert summary["깨어있음"] == 1 and summary["외부 탐색 시도"] == 1 and summary["꾸벅꾸벅"] == 0


def test_hostile_strings_are_escaped(tmp_path):
    r = make_result()
    evil = dataclasses.replace(r.apis[0], path='/a<b>&"c\x01\x1f', summary="x" * 40000)
    out = report.export_xlsx(dataclasses.replace(r, apis=[evil]), tmp_path / "r.xlsx")
    rows = _rows(out)
    assert rows[1][1] == '/a<b>&"c'  # XML에 넣을 수 없는 제어 문자는 뺀다
    assert len(rows[1][2]) == 32767  # 엑셀 셀 최대 글자 수


def test_export_xlsx_reports_save_error(tmp_path):
    with pytest.raises(report.ReportError, match="파일을 저장하지 못했어요"):
        report.export_xlsx(make_result(), tmp_path / "없는 폴더" / "r.xlsx")


def test_write_xlsx_keeps_old_file_on_failure(tmp_path, monkeypatch):
    out = tmp_path / "r.xlsx"
    out.write_bytes(b"old")

    def boom(*a, **k):
        raise OSError("disk full")

    monkeypatch.setattr(xlsx.zipfile.ZipFile, "writestr", boom)
    with pytest.raises(OSError):
        xlsx.write_xlsx(out, [xlsx.Sheet("a", [["x"]], [10])])
    assert out.read_bytes() == b"old"
    assert not (tmp_path / "r.xlsx.part").exists()


def test_col_name():
    assert [xlsx.col_name(i) for i in (0, 25, 26, 51, 52)] == ["A", "Z", "AA", "AZ", "BA"]
