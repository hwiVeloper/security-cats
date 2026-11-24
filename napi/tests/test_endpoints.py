"""엑셀(xlsx)·CSV 엔드포인트 목록을 명세 대신 쓰기."""
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import pytest

from napi.endpoints import load_endpoint_list
from napi.matcher import match
from napi.models import AggRow, PathMapping, SpecOperation, SpecSource
from napi.spec import SpecError, load_spec


def keys(ops):
    return {o.key for o in ops}


def _col(i: int) -> str:
    s = ""
    i += 1
    while i:
        i, r = divmod(i - 1, 26)
        s = chr(65 + r) + s
    return s


def write_xlsx(path, *sheets, shared=True, row_offset=0):
    """최소 xlsx를 만든다. sheets: 행 목록(셀 값은 str/int/float/None). 첫 시트가 맨 앞."""
    strings: list[str] = []

    def cell(ref, v):
        if v is None:
            return ""
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            return f'<c r="{ref}"><v>{v}</v></c>'
        if shared:
            strings.append(str(v))
            return f'<c r="{ref}" t="s"><v>{len(strings) - 1}</v></c>'
        return f'<c r="{ref}" t="inlineStr"><is><t>{escape(str(v))}</t></is></c>'

    sheet_xml = []
    for rows in sheets:
        body = []
        for ri, row in enumerate(rows, start=1 + row_offset):
            cells = "".join(cell(f"{_col(ci)}{ri}", v) for ci, v in enumerate(row))
            body.append(f'<row r="{ri}">{cells}</row>')
        sheet_xml.append(
            '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            f'<sheetData>{"".join(body)}</sheetData></worksheet>')
    ns_r = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
    wb_sheets = "".join(f'<sheet name="시트{i + 1}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
                        for i in range(len(sheets)))
    rels = "".join(f'<Relationship Id="rId{i + 1}" Type="{ns_r}/worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
                   for i in range(len(sheets)))
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   f'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="{ns_r}">'
                   f'<sheets>{wb_sheets}</sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8"?>'
                   f'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">{rels}</Relationships>')
        for i, xml in enumerate(sheet_xml):
            z.writestr(f"xl/worksheets/sheet{i + 1}.xml", xml)
        if shared:
            sst = "".join(f"<si><t>{escape(s)}</t></si>" for s in strings)
            z.writestr("xl/sharedStrings.xml",
                       '<?xml version="1.0" encoding="UTF-8"?>'
                       f'<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">{sst}</sst>')
    return path


ROWS = [
    ["endpoint", "method", "설명", "폐기"],
    ["/pay/orders/{id}", "GET/POST", "주문 조회·수정", None],
    ["/pay/old", "GET", "옛 API", "Y"],
    ["/pay/refunds", "post, put", None, None],
    ["/legacy/list.jsp", None, "옛 화면", None],
]


def test_xlsx_basic(tmp_path):
    ops = load_endpoint_list(write_xlsx(tmp_path / "e.xlsx", ROWS))
    assert keys(ops) == {"GET /pay/orders/{id}", "POST /pay/orders/{id}", "GET /pay/old",
                         "POST /pay/refunds", "PUT /pay/refunds", "ANY /legacy/list.jsp"}
    old = next(o for o in ops if o.path == "/pay/old")
    assert (old.deprecated, old.summary, old.operation_id, old.tags) == (True, "옛 API", None, [])
    assert next(o for o in ops if o.method == "POST" and o.path == "/pay/orders/{id}").deprecated is False


def test_xlsx_inline_strings_and_title_rows(tmp_path):
    rows = [["라운드페이 API 목록"], [], ["No", "HTTP 메서드", "API 경로", "분류"],
            [1, "GET", "/a", "주문"], [2, "DELETE", "/b", None]]
    ops = load_endpoint_list(write_xlsx(tmp_path / "e.xlsx", rows, shared=False))
    assert keys(ops) == {"GET /a", "DELETE /b"}
    assert next(o for o in ops if o.path == "/a").tags == ["주문"]


def test_no_header_uses_first_two_columns(tmp_path):
    ops = load_endpoint_list(write_xlsx(tmp_path / "e.xlsx", [["/a", "GET"], ["/b", "GET|POST"]]))
    assert keys(ops) == {"GET /a", "GET /b", "POST /b"}


def test_only_first_sheet_is_read(tmp_path):
    p = write_xlsx(tmp_path / "e.xlsx", [["endpoint"], ["/first"]], [["endpoint"], ["/second"]])
    assert keys(load_endpoint_list(p)) == {"ANY /first"}


def test_methods_any_aliases_and_head_options_dropped(tmp_path):
    rows = [["경로", "메서드"], ["/a", "*"], ["/b", "ALL"], ["/c", "GET/HEAD/OPTIONS"], ["/d", "OPTIONS"]]
    ops = load_endpoint_list(write_xlsx(tmp_path / "e.xlsx", rows))
    assert keys(ops) == {"ANY /a", "ANY /b", "GET /c"}


def test_url_paths_and_query_are_trimmed(tmp_path):
    rows = [["URL", "method"], ["https://pay.example.com:8443/pay/a?x=1", "GET"], ["  /pay/b/  ", "GET"]]
    assert keys(load_endpoint_list(write_xlsx(tmp_path / "e.xlsx", rows))) == {"GET /pay/a", "GET /pay/b"}


def test_duplicates_merged(tmp_path):
    rows = [["endpoint", "method"], ["/a", "GET"], ["/a", "GET/POST"]]
    ops = load_endpoint_list(write_xlsx(tmp_path / "e.xlsx", rows))
    assert sorted(o.key for o in ops) == ["GET /a", "POST /a"]


def test_row_errors_report_row_numbers(tmp_path):
    rows = [["endpoint", "method"], ["/ok", "GET"], ["pay/a", "GET"], ["/b", "GETT"],
            [None, "POST"], ["/c d", "GET"]]
    with pytest.raises(SpecError) as e:
        load_endpoint_list(write_xlsx(tmp_path / "e.xlsx", rows, row_offset=1))
    msg = str(e.value)
    assert "4행" in msg and "/로 시작" in msg
    assert "5행" in msg and "GETT" in msg
    assert "6행" in msg and "경로가 비어" in msg
    assert "7행" in msg and "공백" in msg


def test_header_without_path_column(tmp_path):
    with pytest.raises(SpecError, match="endpoint"):
        load_endpoint_list(write_xlsx(tmp_path / "e.xlsx", [["이름", "메서드"], ["주문", "GET"]]))


def test_empty_list(tmp_path):
    with pytest.raises(SpecError, match="하나도"):
        load_endpoint_list(write_xlsx(tmp_path / "e.xlsx", [["endpoint", "method"]]))


def test_csv_utf8_and_cp949(tmp_path):
    text = "endpoint,method,설명\n/a,GET/POST,조회\n/b,DELETE,\n"
    p1 = tmp_path / "u.csv"
    p1.write_bytes(b"\xef\xbb\xbf" + text.encode("utf-8"))
    p2 = tmp_path / "k.csv"
    p2.write_bytes(text.encode("cp949"))
    for p in (p1, p2):
        ops = load_endpoint_list(p)
        assert keys(ops) == {"GET /a", "POST /a", "DELETE /b"}
        assert next(o for o in ops if o.method == "GET").summary == "조회"


def test_csv_tab_separated(tmp_path):
    p = tmp_path / "t.csv"
    p.write_text("endpoint\tmethod\n/a\tGET, POST\n", encoding="utf-8")
    assert keys(load_endpoint_list(p)) == {"GET /a", "POST /a"}


def test_drm_encrypted_file(tmp_path):
    p = tmp_path / "e.xlsx"
    p.write_bytes(b"<DOCUMENTSAFER_V2>" + b"\x00" * 64)
    with pytest.raises(SpecError, match="DRM"):
        load_endpoint_list(p)


def test_old_xls_or_password_file(tmp_path):
    p = tmp_path / "e.xls"
    p.write_bytes(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1" + b"\x00" * 64)
    with pytest.raises(SpecError, match="xlsx"):
        load_endpoint_list(p)


def test_not_a_spreadsheet(tmp_path):
    p = tmp_path / "e.xlsx"
    p.write_bytes(b"hello")
    with pytest.raises(SpecError, match="엑셀"):
        load_endpoint_list(p)


def test_missing_file(tmp_path):
    with pytest.raises(SpecError, match="가져오지 못했어요"):
        load_endpoint_list(tmp_path / "nope.xlsx")


def test_load_spec_dispatches_on_type(tmp_path):
    p = write_xlsx(tmp_path / "e.xlsx", [["endpoint", "method"], ["/a", "GET"]])
    assert keys(load_spec(SpecSource(type="endpoints", source="file", location=str(p)))) == {"GET /a"}
    with pytest.raises(SpecError, match="파일"):
        load_spec(SpecSource(type="endpoints", source="url", location="http://x/e.xlsx"))


def test_template_roundtrip(tmp_path):
    from napi.endpoints import write_template
    p = write_template(tmp_path / "양식.xlsx")
    with zipfile.ZipFile(p) as z:
        names = set(z.namelist())
        assert {"[Content_Types].xml", "_rels/.rels", "xl/workbook.xml", "xl/styles.xml",
                "xl/worksheets/sheet1.xml", "xl/worksheets/sheet2.xml"} <= names
        for n in names:  # 모든 XML이 올바른 형식
            ET.fromstring(z.read(n))
    ops = load_endpoint_list(p)  # 작성 방법 시트는 읽지 않는다
    assert keys(ops) == {"GET /pay/orders/{id}", "POST /pay/orders/{id}", "GET /pay/old",
                         "ANY /legacy/list.jsp"}
    assert next(o for o in ops if o.path == "/pay/old").deprecated is True


def test_config_validation_for_endpoints():
    from napi.config import validate_config
    from napi.models import AppConfig, LogPath, ServerConfig, ServiceConfig

    def errs(spec):
        svc = ServiceConfig(name="옛", spec=spec, servers=[
            ServerConfig(name="w", host="h", user="u", log_paths=[LogPath("/var/log/a.log")])])
        return validate_config(AppConfig(services=[svc]))

    assert errs(SpecSource(type="endpoints", source="file", location="C:/e.xlsx")) == []
    assert any("파일로만" in e for e in errs(SpecSource(type="endpoints", source="url", location="http://x")))
    assert any("명세 형식" in e for e in errs(SpecSource(type="raml", source="file", location="x")))


# ── 매처: ANY(모든 메서드) ──────────────────────────────────────────────────

def _row(method, path, calls=1):
    return AggRow(method=method, path=path, status_class="2xx", count=calls, first="2026-01-01",
                  last="2026-01-02")


def _op(method, path):
    return SpecOperation(method=method, path=path, operation_id=None, tags=[], summary=None, deprecated=False)


def test_matcher_any_method_takes_every_method():
    res = match({"s": [_row("GET", "/legacy/list.jsp"), _row("POST", "/legacy/list.jsp", 2)]},
                [_op("ANY", "/legacy/list.jsp")], PathMapping(), [])
    assert res.per_op["ANY /legacy/list.jsp"].calls == 3
    assert not res.unmatched


def test_matcher_exact_method_beats_any():
    res = match({"s": [_row("GET", "/a/1"), _row("POST", "/a/1")]},
                [_op("ANY", "/a/{id}"), _op("GET", "/a/{id}")], PathMapping(), [])
    assert res.per_op["GET /a/{id}"].calls == 1
    assert res.per_op["ANY /a/{id}"].calls == 1
