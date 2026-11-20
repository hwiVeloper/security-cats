from pathlib import Path

import pytest

from napi.logformat import (
    PRESETS,
    ParsedLine,
    normalize_path,
    parse_line,
    test_sample_line as run_sample_line,
)
from napi.models import CustomFormat

FIXTURES = Path(__file__).parent / "fixtures" / "logs"
ALL_CLF = ["apache_common", "apache_combined", "nginx_combined", "webtob"]


def test_presets_display_names():
    assert PRESETS == {
        "apache_common": "Apache (common)",
        "apache_combined": "Apache (combined)",
        "nginx_combined": "nginx (combined)",
        "webtob": "WebtoB",
        "webtob_combined": "WebtoB (combined)",
        "custom": "직접 지정 (필드 번호)",
    }


def test_parse_clf():
    line = ('10.0.0.1 - - [18/Nov/2026:10:00:01 +0900] '
            '"GET /v1/users/123?token=abc HTTP/1.1" 200 512')
    assert parse_line(line, "webtob") == ParsedLine(
        "GET", "/v1/users/123?token=abc", 200, "20261118")


def test_parse_combined_with_quotes_in_ua():
    line = ('1.2.3.4 - - [20/May/2026:07:30:00 +0900] "POST /v1/a HTTP/1.1" 201 9 '
            '"-" "Mozilla/5.0 (X11; Linux) \\"Quoted\\" x"')
    assert parse_line(line, "nginx_combined") == ParsedLine(
        "POST", "/v1/a", 201, "20260520")


@pytest.mark.parametrize("line", [
    '10.0.0.9 - - [18/Nov/2026:10:05:00 +0900] "-" 400 0',
    '10.0.0.9 - - [18/Nov/2026:10:05:01 +0900] "\\x16\\x03\\x01\\x02\\x00" 400',
])
def test_broken_lines_return_none(line):
    assert parse_line(line, "webtob") is None


@pytest.mark.parametrize("line", [
    "",
    "garbage",
    '1.1.1.1 - - [32/Foo/2026:10:00:00 +0900] "GET /a HTTP/1.1" 200 1',
    '1.1.1.1 - - [18/Nov/2026:10:00:00 +0900] "get /a HTTP/1.1" 200 1',
    '1.1.1.1 - - [18/Nov/2026:10:00:00 +0900] "GET /a HTTP/1.1" 20 1',
    '1.1.1.1 - - [18/Nov/2026:10:00:00 +0900] "GET /a HTTP/1.1"',
])
def test_malformed_lines_return_none(line):
    assert parse_line(line, "apache_common") is None


@pytest.mark.parametrize("name,fmt", [
    ("webtob_access.log", "webtob"),
    ("apache_common.log", "apache_common"),
    ("nginx_access.log", "nginx_combined"),
])
def test_fixtures_parse(name, fmt):
    raw = (FIXTURES / name).read_bytes()
    assert b"\r" not in raw and not raw.startswith(b"\xef\xbb\xbf")
    lines = raw.decode("utf-8").split("\n")
    assert lines[-1] == ""
    parsed = [parse_line(ln, fmt) for ln in lines[:-1]]
    assert sum(p is None for p in parsed) == 2
    good = [p for p in parsed if p]
    assert {p.date for p in good} >= {"20260520", "20261118"}
    paths = [p.path for p in good]
    assert "/v1/users/123?token=abc" in paths
    assert "/wp-login.php" in paths
    assert any(p.method == "HEAD" and p.path == "/health" for p in good)
    assert len(paths) != len(set(paths))  # repeated calls


@pytest.mark.parametrize("path,expected", [
    ("/v1/users/123?x=1", "/v1/users/{*}"),
    ("/v1/users/me/", "/v1/users/me"),
    ("//v1//a;jsessionid=XYZ/b", "/v1/a/b"),
    ("/v1/u/550e8400-e29b-41d4-a716-446655440000", "/v1/u/{*}"),
    ("/v1/t/abcdef0123456789", "/v1/t/{*}"),
    ("/v1/p/010-1234-5678", "/v1/p/{*}"),
    ("/v1/m/a@b.com", "/v1/m/{*}"),
    ("/v1/k/AbC123xyz456DEF789ghi0", "/v1/k/{*}"),
    ("/v1/merchants/legacy-info", "/v1/merchants/legacy-info"),
    ("/v1/검색/%ED%99%8D%EA%B8%B8%EB%8F%99%ED%99%8D", "/v1/검색/{*}"),
    ("/", "/"),
    ("", "/"),
    ("/a/b#frag", "/a/b"),
    ("/v1/short%20x", "/v1/short%20x"),
    ("/v1/abcdefghijklmnopqrst", "/v1/abcdefghijklmnopqrst"),  # no digit
    ("/v1/한글한글한글한글한글한글한글한글한글한글한글한글", "/v1/한글한글한글한글한글한글한글한글한글한글한글한글"),
    ("/v1/١٢٣", "/v1/١٢٣"),  # non-ASCII digits are not digits
    ("/v1/x/;a=b/y", "/v1/x/y"),
    ("/v1/검색검색검%20a", "/v1/검색검색검%20a"),  # 19 bytes (9 chars): unchanged
    ("/v1/검색검색검%20ab", "/v1/{*}"),  # 20 bytes
    ("/v1/검색검색검색%20", "/v1/{*}"),  # 11 chars but 21 bytes
])
def test_normalize_cases(path, expected):
    assert normalize_path(path) == expected


def test_custom_iso():
    fmt = CustomFormat(method_field=3, path_field=4, status_field=5,
                       time_field=1, time_format="iso8601")
    line = "2026-11-18T10:00:00 host GET /v1/a 200"
    assert parse_line(line, "custom", fmt) == ParsedLine(
        "GET", "/v1/a", 200, "20261118")


def test_custom_clf_with_quoted_path():
    fmt = CustomFormat(method_field=2, path_field=3, status_field=4,
                       time_field=1, time_format="clf")
    line = '[18/Nov/2026:10:00:00 GET "/v1/a?x=1" 200'
    assert parse_line(line, "custom", fmt) == ParsedLine(
        "GET", "/v1/a?x=1", 200, "20261118")


def test_custom_missing_format_or_field_returns_none():
    assert parse_line("a b c", "custom", None) is None
    fmt = CustomFormat(9, 9, 9, 9, "iso8601")
    assert parse_line("a b c", "custom", fmt) is None


def test_unknown_format_returns_none():
    assert parse_line("x", "bogus") is None


def test_sample_line_ok():
    line = ('10.0.0.1 - - [18/Nov/2026:10:00:01 +0900] '
            '"GET /v1/users/123?token=abc HTTP/1.1" 200 512')
    res = run_sample_line(line, "webtob", None)
    assert res == {
        "ok": True, "method": "GET", "path": "/v1/users/123?token=abc",
        "normalized": "/v1/users/{*}", "status": 200, "status_class": "2xx",
        "date": "20261118", "error": None,
    }


def test_sample_line_reports_error():
    res = run_sample_line('x "-" 400 0', "webtob", None)
    assert res["ok"] is False
    assert res["error"] == "이 형식으로 읽을 수 없는 줄이에요"


def test_custom_unicode_space_does_not_split():
    fmt = CustomFormat(method_field=2, path_field=3, status_field=4,
                       time_field=1, time_format="iso8601")
    line = "2026-11-18T10:00:00\tGET\t/v1/a\u3000b  200"
    res = parse_line(line, "custom", fmt)
    assert res == ParsedLine("GET", "/v1/a\u3000b", 200, "20261118")


def test_crlf_line_parses():
    line = ('10.0.0.1 - - [18/Nov/2026:10:00:01 +0900] "GET /a HTTP/1.1" 200\r\n')
    assert parse_line(line, "webtob") == ParsedLine("GET", "/a", 200, "20261118")


@pytest.mark.parametrize("req", ['GET  /a HTTP/1.1', ' GET /a HTTP/1.1', 'GET\t/a  '])
def test_request_line_awk_style_tokenizing(req):
    line = f'1.1.1.1 - - [18/Nov/2026:10:00:01 +0900] "{req}" 200 1'
    assert parse_line(line, "webtob") == ParsedLine("GET", "/a", 200, "20261118")
