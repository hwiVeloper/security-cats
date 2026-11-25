"""의존성 없는 최소 xlsx 작성기 (엔드포인트 양식·엑셀 리포트가 같이 쓴다).

문자열 셀은 텍스트 서식(@)이라 경로·메서드가 숫자·날짜로 바뀌지 않고, 정수 셀은 천 단위 구분 숫자로 쓴다.
"""
from __future__ import annotations

import re
import zipfile
from dataclasses import dataclass
from pathlib import Path

_CT = "application/vnd.openxmlformats-officedocument.spreadsheetml"
_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_DOC_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_XML_HEAD = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
# XML 1.0에 넣을 수 없는 제어 문자 (로그에서 온 경로에 섞여 있을 수 있다)
_ILLEGAL = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f￾￿]")
_MAX_CELL = 32767  # 엑셀 셀 하나에 들어가는 최대 글자 수

# 스타일 번호 (_STYLES 의 cellXfs 순서)
_S_HEADER, _S_TEXT, _S_NUMBER = 1, 2, 3

Cell = str | int | None


@dataclass
class Sheet:
    name: str
    rows: list[list[Cell]]
    widths: list[int]
    header: bool = True  # 첫 줄을 제목 줄로 (굵게·색·틀 고정)
    autofilter: bool = False  # 제목 줄에 필터 단추


def _x(s: str) -> str:
    s = _ILLEGAL.sub("", s)[:_MAX_CELL]
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace('"', "&quot;")


def col_name(i: int) -> str:
    """0 → A, 25 → Z, 26 → AA"""
    s, i = "", i + 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def _cell(ref: str, v: Cell, style: int) -> str:
    if v is None or v == "":
        return f'<c r="{ref}" s="{style}"/>'
    if isinstance(v, bool):
        v = "Y" if v else ""
        return _cell(ref, v, style)
    if isinstance(v, int):
        return f'<c r="{ref}" s="{_S_NUMBER}"><v>{v}</v></c>'
    return f'<c r="{ref}" s="{style}" t="inlineStr"><is><t xml:space="preserve">{_x(str(v))}</t></is></c>'


def _last_ref(sheet: Sheet) -> str:
    width = max((len(r) for r in sheet.rows), default=1)
    return f"{col_name(max(width, 1) - 1)}{max(len(sheet.rows), 1)}"


def _sheet_xml(sheet: Sheet) -> str:
    out = []
    for ri, row in enumerate(sheet.rows, 1):
        style = _S_HEADER if sheet.header and ri == 1 else _S_TEXT
        cells = "".join(_cell(f"{col_name(ci)}{ri}", v, style) for ci, v in enumerate(row))
        out.append(f'<row r="{ri}">{cells}</row>')
    cols = "".join(f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
                   for i, w in enumerate(sheet.widths))
    pane = ('<pane ySplit="1" topLeftCell="A2" activePane="bottomLeft" state="frozen"/>'
            '<selection pane="bottomLeft" activeCell="A2" sqref="A2"/>') if sheet.header else ""
    auto = f'<autoFilter ref="A1:{_last_ref(sheet)}"/>' if sheet.autofilter else ""
    return (f'{_XML_HEAD}<worksheet xmlns="{_MAIN}">'
            f'<sheetViews><sheetView workbookViewId="0">{pane}</sheetView></sheetViews>'
            f'<cols>{cols}</cols><sheetData>{"".join(out)}</sheetData>{auto}</worksheet>')


_STYLES = (
    f'{_XML_HEAD}<styleSheet xmlns="{_MAIN}">'
    '<fonts count="2"><font><sz val="11"/><name val="맑은 고딕"/><family val="3"/></font>'
    '<font><b/><sz val="11"/><name val="맑은 고딕"/><family val="3"/></font></fonts>'
    '<fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill>'
    '<fill><patternFill patternType="solid"><fgColor rgb="FFFCE9D6"/><bgColor indexed="64"/></patternFill></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="4"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="49" fontId="1" fillId="2" borderId="0" xfId="0" applyNumberFormat="1" applyFont="1" applyFill="1"/>'
    '<xf numFmtId="49" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/>'
    '<xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/></cellXfs>'
    '<cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>')


def _sheet_ref(name: str) -> str:
    return "'" + name.replace("'", "''") + "'"


def write_xlsx(path, sheets: list[Sheet]) -> Path:
    """sheets 를 차례대로 담은 xlsx 를 만든다. 다 쓴 뒤에 바꿔 넣어서 중간에 실패해도 기존 파일은 남는다."""
    path = Path(path)
    n = len(sheets)
    overrides = "".join(f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType="{_CT}.worksheet+xml"/>'
                        for i in range(1, n + 1))
    sheet_tags = "".join(f'<sheet name="{_x(s.name)}" sheetId="{i}" r:id="rId{i}"/>'
                         for i, s in enumerate(sheets, 1))
    names = "".join(
        f'<definedName name="_xlnm._FilterDatabase" localSheetId="{i}" hidden="1">'
        f'{_x(_sheet_ref(s.name))}!$A$1:${_last_ref(s).rstrip("0123456789")}${max(len(s.rows), 1)}</definedName>'
        for i, s in enumerate(sheets) if s.autofilter)
    rels = "".join(f'<Relationship Id="rId{i}" Type="{_DOC_REL}/worksheet" Target="worksheets/sheet{i}.xml"/>'
                   for i in range(1, n + 1))
    files = {
        "[Content_Types].xml":
            f'{_XML_HEAD}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
            '<Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/xl/workbook.xml" ContentType="{_CT}.sheet.main+xml"/>{overrides}'
            f'<Override PartName="/xl/styles.xml" ContentType="{_CT}.styles+xml"/></Types>',
        "_rels/.rels":
            f'{_XML_HEAD}<Relationships xmlns="{_PKG_REL}">'
            f'<Relationship Id="rId1" Type="{_DOC_REL}/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        "xl/workbook.xml":
            f'{_XML_HEAD}<workbook xmlns="{_MAIN}" xmlns:r="{_DOC_REL}">'
            '<bookViews><workbookView activeTab="0"/></bookViews>'
            f'<sheets>{sheet_tags}</sheets>' + (f'<definedNames>{names}</definedNames>' if names else "")
            + '</workbook>',
        "xl/_rels/workbook.xml.rels":
            f'{_XML_HEAD}<Relationships xmlns="{_PKG_REL}">{rels}'
            f'<Relationship Id="rId{n + 1}" Type="{_DOC_REL}/styles" Target="styles.xml"/></Relationships>',
        "xl/styles.xml": _STYLES,
    }
    for i, s in enumerate(sheets, 1):
        files[f"xl/worksheets/sheet{i}.xml"] = _sheet_xml(s)
    tmp = path.with_name(path.name + ".part")
    try:
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
            for name, xml in files.items():
                z.writestr(name, xml)
        tmp.replace(path)
    finally:
        tmp.unlink(missing_ok=True)
    return path
