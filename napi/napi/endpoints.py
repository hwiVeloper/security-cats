"""엑셀(xlsx)·CSV 엔드포인트 목록을 명세 대신 읽는다.

Swagger가 없는 서비스용이다. 첫 번째 시트만 읽는다 (여러 시트는 지원하지 않는다).

양식: 제목 줄에서 열을 찾는다. 제목 줄이 없으면 1열을 경로, 2열을 메서드로 본다.
  경로(필수)   endpoint, 경로, path, url, uri, api 경로 …
  메서드       method, 메서드 — GET/POST, "GET, POST", "GET POST" 처럼 여러 개. 빈칸·*·ALL 은 모든 메서드(ANY)
  설명         설명, description, summary …
  태그         태그, tag, 분류, 그룹 …
  폐기         폐기, deprecated … — Y, O, TRUE, 1 이면 Swagger deprecated 처럼 "영면" 판정에 쓴다

xlsx는 외부 라이브러리 없이 zip 안의 XML을 직접 읽는다.
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit
from xml.etree import ElementTree as ET

from napi.models import ANY_METHOD, SpecOperation
from napi.xlsx import Sheet, write_xlsx

_METHODS = {"GET", "PUT", "POST", "DELETE", "PATCH", "TRACE"}
_DROPPED = {"HEAD", "OPTIONS"}  # 판정 제외 (spec.py 와 같은 기준)
_ANY_WORDS = {"", "*", "ALL", "ANY", "전체", "모두"}
_TRUE_WORDS = {"y", "yes", "o", "○", "●", "v", "✓", "✔", "true", "1", "예", "폐기", "deprecated"}
_METHOD_SPLIT = re.compile(r"[\s/,|;·]+")

# 제목 → 열 종류. 비교 전에 소문자로 바꾸고 공백·_·- 를 뺀다.
_HEADERS = {
    "path": {"endpoint", "endpoints", "엔드포인트", "path", "경로", "url", "uri", "api", "api경로",
             "apipath", "apiurl", "요청경로", "주소"},
    "method": {"method", "methods", "메서드", "메소드", "httpmethod", "http메서드", "http메소드", "요청방식"},
    "summary": {"설명", "description", "desc", "summary", "요약", "api명", "apiname", "이름", "name"},
    "tag": {"태그", "tag", "tags", "분류", "그룹", "group", "구분"},
    "deprecated": {"폐기", "deprecated", "폐기여부", "사용중지"},
}
_HEADER_SCAN_ROWS = 10  # 표 위에 제목·빈 줄이 있어도 이만큼은 제목 줄을 찾아본다
_MAX_ERRORS = 8

_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"


class _Err(Exception):
    pass


def _spec_error(msg: str):
    from napi.spec import SpecError  # spec.py 가 이 모듈을 불러서 순환을 피한다
    return SpecError(msg)


# ── 파일 → 행 목록 [(행 번호, [셀 문자열…])] ───────────────────────────────────

def _read_rows(path: Path) -> list[tuple[int, list[str]]]:
    try:
        data = path.read_bytes()
    except OSError as e:
        raise _spec_error(f"명세를 가져오지 못했어요: {path} ({e})") from e
    if data.startswith(b"<DOCUMENT"):
        raise _spec_error("DRM으로 암호화된 파일이라 읽을 수 없어요. 암호화되지 않은 파일로 넣어 주세요.")
    if data.startswith(b"\xd0\xcf\x11\xe0"):
        raise _spec_error("옛 엑셀 형식(xls)이거나 암호가 걸린 파일이에요. 엑셀에서 xlsx로 다시 저장해 주세요.")
    if data.startswith(b"PK"):
        try:
            return _xlsx_rows(data)
        except (zipfile.BadZipFile, KeyError, ET.ParseError, _Err) as e:
            raise _spec_error(f"엑셀(xlsx) 파일을 읽지 못했어요: {path} ({e})") from e
    if path.suffix.lower() in (".csv", ".tsv"):
        return _csv_rows(data)
    raise _spec_error("엑셀(xlsx) 또는 CSV 파일이 아니에요. 엔드포인트 목록은 .xlsx 나 .csv 로 넣어 주세요.")


def _csv_rows(data: bytes) -> list[tuple[int, list[str]]]:
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError:
        text = data.decode("cp949", errors="replace")  # 한글 엑셀이 저장한 CSV
    sample = text[:4096]
    try:
        dialect = csv.Sniffer().sniff(sample, delimiters=",\t;")
    except csv.Error:
        dialect = csv.excel_tab if "\t" in sample and "," not in sample else csv.excel
    return [(i, [c.strip() for c in row]) for i, row in enumerate(csv.reader(io.StringIO(text), dialect), 1)]


def _local(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _xlsx_rows(data: bytes) -> list[tuple[int, list[str]]]:
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        sheet = _first_sheet(z, names)
        shared = _shared_strings(z) if "xl/sharedStrings.xml" in names else []
        root = ET.fromstring(z.read(sheet))
    rows: list[tuple[int, list[str]]] = []
    next_row = 1
    for row in root.iter():
        if _local(row.tag) != "row":
            continue
        rnum = int(row.get("r") or next_row)
        next_row = rnum + 1
        cells: dict[int, str] = {}
        next_col = 0
        for c in row:
            if _local(c.tag) != "c":
                continue
            ref = c.get("r")
            col = _col_index(ref) if ref else next_col
            next_col = col + 1
            cells[col] = _cell_text(c, shared)
        if cells:
            rows.append((rnum, [cells.get(i, "") for i in range(max(cells) + 1)]))
    return rows


def _first_sheet(z: zipfile.ZipFile, names: set[str]) -> str:
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    sheets = [e for e in wb.iter() if _local(e.tag) == "sheet"]
    if not sheets:
        raise _Err("시트가 없어요")
    rid = sheets[0].get(f"{{{_NS_REL}}}id") or next(
        (v for k, v in sheets[0].attrib.items() if _local(k) == "id"), None)
    target = None
    if rid and "xl/_rels/workbook.xml.rels" in names:
        rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        for r in rels.iter():
            if _local(r.tag) == "Relationship" and r.get("Id") == rid:
                target = r.get("Target")
    if not target:
        target = "worksheets/sheet1.xml"
    path = target.lstrip("/") if target.startswith("/") else str(PurePosixPath("xl") / target)
    if path not in names:
        raise _Err(f"시트 파일이 없어요: {path}")
    return path


def _shared_strings(z: zipfile.ZipFile) -> list[str]:
    root = ET.fromstring(z.read("xl/sharedStrings.xml"))
    out = []
    for si in root:
        if _local(si.tag) != "si":
            continue
        # 서식이 섞인 글자(r/t)와 윗주(rPh)는 rPh 를 빼고 이어 붙인다
        out.append("".join(t.text or "" for t in _iter_text(si)))
    return out


def _iter_text(node):
    for child in node:
        name = _local(child.tag)
        if name == "rPh":
            continue
        if name == "t":
            yield child
        else:
            yield from _iter_text(child)


def _col_index(ref: str) -> int:
    n = 0
    for ch in ref:
        if not ch.isalpha():
            break
        n = n * 26 + (ord(ch.upper()) - 64)
    return n - 1


def _cell_text(c, shared: list[str]) -> str:
    kind = c.get("t")
    if kind == "inlineStr":
        return "".join(t.text or "" for t in _iter_text(c)).strip()
    v = next((e for e in c if _local(e.tag) == "v"), None)
    raw = (v.text or "") if v is not None else ""
    if kind == "s":
        try:
            return shared[int(raw)].strip()
        except (ValueError, IndexError):
            return ""
    if kind == "b":
        return "TRUE" if raw == "1" else "FALSE"
    if kind in (None, "n") and raw.endswith(".0"):
        return raw[:-2]  # 1.0 → 1
    return raw.strip()


# ── 행 목록 → SpecOperation ────────────────────────────────────────────────────

def _norm_header(s: str) -> str:
    return re.sub(r"[\s_\-()]+", "", s).lower()


def _find_columns(rows) -> tuple[int, dict[str, int]]:
    """(데이터 시작 위치, {열 종류: 열 번호})."""
    nonempty = [i for i, (_, cells) in enumerate(rows) if any(cells)]
    for i in nonempty[:_HEADER_SCAN_ROWS]:
        cols: dict[str, int] = {}
        for ci, cell in enumerate(rows[i][1]):
            h = _norm_header(cell)
            for kind, names in _HEADERS.items():
                if h in names and kind not in cols:
                    cols[kind] = ci
        if "path" in cols:
            return i + 1, cols
    if nonempty:
        first = rows[nonempty[0]][1][0].strip() if rows[nonempty[0]][1] else ""
        if first.startswith("/") or urlsplit(first).scheme in ("http", "https"):
            return nonempty[0], {"path": 0, "method": 1}
    raise _spec_error("endpoint 열을 찾지 못했어요. 첫 줄에 endpoint, method 같은 제목을 넣어 주세요.")


def _clean_path(raw: str) -> str:
    p = raw.strip()
    parts = urlsplit(p)
    if parts.scheme in ("http", "https"):
        p = parts.path or "/"
    p = p.split("?", 1)[0].split("#", 1)[0]
    if not p.startswith("/"):
        raise _Err(f"경로가 /로 시작하지 않아요: {raw.strip()}")
    if re.search(r"\s", p):
        raise _Err(f"경로에 공백이 있어요: {raw.strip()}")
    return p.rstrip("/") or "/"


def _methods(raw: str) -> list[str]:
    words = [w.upper() for w in _METHOD_SPLIT.split(raw.strip()) if w]
    if not words or all(w in _ANY_WORDS for w in words):
        return [ANY_METHOD]
    bad = [w for w in words if w not in _METHODS and w not in _DROPPED and w not in _ANY_WORDS]
    if bad:
        raise _Err(f"모르는 메서드예요: {', '.join(bad)}")
    if any(w in _ANY_WORDS for w in words):
        return [ANY_METHOD]
    return [w for w in dict.fromkeys(words) if w in _METHODS]


def _cell(cells: list[str], cols: dict[str, int], kind: str) -> str:
    i = cols.get(kind)
    return cells[i].strip() if i is not None and i < len(cells) else ""


def parse_rows(rows: list[tuple[int, list[str]]]) -> list[SpecOperation]:
    start, cols = _find_columns(rows)
    ops: dict[str, SpecOperation] = {}
    errors: list[str] = []
    for rnum, cells in rows[start:]:
        if not any(c.strip() for c in cells):
            continue
        try:
            raw_path = _cell(cells, cols, "path")
            if not raw_path:
                raise _Err("경로가 비어 있어요")
            path = _clean_path(raw_path)
            methods = _methods(_cell(cells, cols, "method"))
        except _Err as e:
            errors.append(f"{rnum}행: {e}")
            continue
        summary = _cell(cells, cols, "summary") or None
        tag = _cell(cells, cols, "tag")
        deprecated = _cell(cells, cols, "deprecated").lower() in _TRUE_WORDS
        for m in methods:
            op = SpecOperation(method=m, path=path, operation_id=None, tags=[tag] if tag else [],
                               summary=summary, deprecated=deprecated)
            ops.setdefault(op.key, op)
    if errors:
        more = f" 외 {len(errors) - _MAX_ERRORS}건" if len(errors) > _MAX_ERRORS else ""
        raise _spec_error("엔드포인트 목록에 고칠 곳이 있어요. " + " / ".join(errors[:_MAX_ERRORS]) + more)
    if not ops:
        raise _spec_error("엔드포인트 목록에 엔드포인트가 하나도 없어요.")
    return list(ops.values())


def load_endpoint_list(path) -> list[SpecOperation]:
    return parse_rows(_read_rows(Path(path)))


# ── 양식 파일 만들기 ───────────────────────────────────────────────────────────

TEMPLATE_HEADERS = ["endpoint", "method", "설명", "태그", "폐기"]
TEMPLATE_EXAMPLES = [
    ["/pay/orders/{id}", "GET/POST", "주문 조회·수정 (예시)", "주문", ""],
    ["/pay/old", "GET", "옛 결제 API (예시)", "결제", "Y"],
    ["/legacy/list.jsp", "", "메서드 구분 없는 옛 화면 (예시)", "화면", ""],
]
TEMPLATE_GUIDE = [
    "napi 엔드포인트 목록 작성 방법",
    "",
    "• 첫 번째 시트(엔드포인트)만 읽어요. 여러 시트는 지원하지 않으니 한 시트에 모두 적어 주세요.",
    "• 2~4행은 예시예요. 지우고 실제 API를 적어 주세요.",
    "• 1행 제목(endpoint, method …)은 그대로 두세요. 열 순서는 바꿔도 돼요.",
    "• endpoint (필수): /로 시작하는 경로. {id} 같은 경로 변수를 쓸 수 있고, http://호스트 부분은 떼고 읽어요.",
    "• method: GET, POST, PUT, DELETE, PATCH. 여러 개는 GET/POST 처럼 적어요. 비우거나 * 이면 모든 메서드로 봐요.",
    "• 설명, 태그 (선택): 결과 화면과 리포트에 그대로 보여요.",
    "• 폐기 (선택): Y 이면 폐기된 API로 보고, 호출이 없으면 \"영면\"으로 판정해요.",
    "• 같은 메서드·경로가 여러 번 나오면 하나로 합쳐요. HEAD, OPTIONS 는 판정에서 빼요.",
    "• CSV(쉼표·탭 구분)로 저장해서 넣어도 돼요.",
]


def write_template(path) -> Path:
    """엔드포인트 목록 양식 xlsx를 만든다. 셀 서식이 텍스트(@)라 경로·메서드가 숫자·날짜로 바뀌지 않는다."""
    return write_xlsx(path, [
        Sheet("엔드포인트", [list(TEMPLATE_HEADERS)] + [list(r) for r in TEMPLATE_EXAMPLES],
              [42, 16, 34, 14, 8]),
        Sheet("작성 방법", [[line] for line in TEMPLATE_GUIDE], [120], header=False),
    ])
