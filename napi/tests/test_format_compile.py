import pytest

from napi.logformat import (
    BUILTIN_FORMATS,
    PRESETS,
    FormatError,
    FormatSpec,
    ParsedLine,
    Token,
    compile_format,
    parse_line,
    parse_with_spec,
    resolve_format,
    test_format as run_test_format,
    test_sample_line as run_sample_line,
)
from napi.models import CustomFormat, LogFormatDef

USER_A = '[%{%Y-%m-%dT%H:%M:%S}t] [CLIENT(%h)] "%r" %s'
USER_B = "%{%d/%b/%Y:%H:%M:%S}t %m %U ?%q %s"


def test_builtin_formats_values():
    assert BUILTIN_FORMATS == {
        "apache_common": '%h %l %u %t "%r" %>s %b',
        "apache_combined": '%h %l %u %t "%r" %>s %b "%{Referer}i" "%{User-Agent}i"',
        "nginx_combined": '%h %l %u %t "%r" %>s %b "%{Referer}i" "%{User-Agent}i"',
        "webtob": '%h %l %u %t "%r" %s %b',
        "webtob_combined": '%h %l %u %t "%r" %s %b "%{Referer}i" "%{User-Agent}i"',
    }
    assert list(PRESETS) == [*BUILTIN_FORMATS, "custom"]
    assert PRESETS["webtob_combined"] == "WebtoB (combined)"
    assert PRESETS["custom"] == "직접 지정 (필드 번호)"


def test_builtin_patterns_compile():
    for name, pattern in BUILTIN_FORMATS.items():
        spec = compile_format(pattern)
        assert isinstance(spec, FormatSpec), name
        assert spec.has_status, name


def test_compile_tokens_webtob():
    spec = compile_format(BUILTIN_FORMATS["webtob"])
    assert [(t.kind, t.text if t.kind == "lit" else t.directive) for t in spec.tokens] == [
        ("field", "h"), ("lit", " "), ("field", "l"), ("lit", " "), ("field", "u"),
        ("lit", " "), ("field", "t"), ("lit", ' "'), ("field", "r"), ("lit", '" '),
        ("field", "s"), ("lit", " "), ("field", "b"),
    ]


def test_compile_directive_mapping():
    spec = compile_format('%t "%r" %>s %{Referer}i %{X}o %B %D %T %q %H %* %Z %%x')
    fields = [t.directive for t in spec.tokens if t.kind == "field"]
    assert fields == ["t", "r", "s", "i", "o", "B", "D", "T", "q", "H", "skip", "skip"]
    assert spec.tokens[-1] == Token("lit", " %x")


def test_compile_time_layout():
    spec = compile_format('[%{%Y-%m-%dT%H:%M:%S %z}t] "%r"')
    (tok,) = [t for t in spec.tokens if t.kind == "field" and t.directive == "tfmt"]
    assert tok.time_layout == (("Y", 0, 4), ("m", 5, 2), ("d", 8, 2))
    assert tok.time_width == 25
    spec = compile_format('%{%d/%b/%Y:%H:%M:%S}t "%r"')
    tok = spec.tokens[0]
    assert tok.time_layout == (("d", 0, 2), ("b", 3, 3), ("Y", 7, 4))
    assert tok.time_width == 20


@pytest.mark.parametrize("pattern,phrase", [
    ('%h%l %t "%r"', "구분 문자"),
    ('[%{%Y-%m-%dT%H:%M:%S}t] [CLIENT(%h)] %*', "API 호출을 셀 수 없는"),
    ('%h "%r" %s', "API 호출을 셀 수 없는"),
    ('%h %{%Y-%m-%d %t', "닫히지 않은"),
    ('%{%Y-%j}t "%r"', "지원하지 않는"),
])
def test_compile_errors(pattern, phrase):
    with pytest.raises(FormatError) as e:
        compile_format(pattern)
    assert phrase in str(e.value)


def test_compile_error_messages_exact():
    with pytest.raises(FormatError, match="^필드 사이에 구분 문자가 필요해요: %h%l$"):
        compile_format('%h%l %t "%r"')
    with pytest.raises(FormatError, match="^닫히지 않은 %{ 가 있어요$"):
        compile_format('%h %{%Y-%m-%d %t')
    with pytest.raises(FormatError, match="^시각 형식에서 지원하지 않는 지시자예요: %j$"):
        compile_format('%{%Y-%j}t "%r"')
    with pytest.raises(FormatError,
                       match=r"^API 호출을 셀 수 없는 형식이에요 \(요청/시각 필요\)$"):
        compile_format("")


@pytest.mark.parametrize("pattern", [
    '%{%Y-%m}t "%r"',          # 날짜에 일이 없음
    '%{%y-%m-%d}t "%r"',       # 2자리 연도는 날짜로 인정하지 않음
    "%t %m %s",                # %U 없음
    "%t %U %s",                # %m 없음
])
def test_compile_requirements(pattern):
    with pytest.raises(FormatError, match="API 호출을 셀 수 없는"):
        compile_format(pattern)


def test_user_format_with_brackets():
    spec = compile_format(USER_A)
    line = '[2026-11-18T10:00:01] [CLIENT(10.0.0.1)] "GET /v1/users/123?x=1 HTTP/1.1" 200'
    assert parse_with_spec(line, spec) == ParsedLine("GET", "/v1/users/123?x=1", 200, "20261118")


def test_method_and_U():
    spec = compile_format(USER_B)
    p = parse_with_spec("18/Nov/2026:10:00:01 GET /v1/a ?x=1 404", spec)
    assert (p.method, p.path, p.status, p.date) == ("GET", "/v1/a", 404, "20261118")


def test_adjacent_U_q_is_error():
    with pytest.raises(FormatError, match="구분 문자"):
        compile_format("%t %m %U%q")


def test_no_status_format():
    spec = compile_format('%t "%r"')
    assert spec.has_status is False
    line = '[18/Nov/2026:10:00:01 +0900] "GET /v1/a HTTP/1.1"'
    assert parse_with_spec(line, spec) == ParsedLine("GET", "/v1/a", None, "20261118")
    res = run_test_format('%t "%r"', line)
    assert res["ok"] is True and res["status"] is None
    assert res["status_class"] == "???"


def test_literal_mismatch_is_none():
    line = '[2026-11-18T10:00:01] [CLIENT(10.0.0.1)] "GET /v1/a HTTP/1.1" 200'
    assert parse_line(line, "webtob") is None


@pytest.mark.parametrize("line", [
    "[2026-11-18T10:00:01] [CLIENT(10.0.0.1)] ",                         # 줄이 중간에 끝남
    '[2026-11-18T10:00:01] [CLIENT(10.0.0.1)] "GET /v1/a HTTP/1.1"',      # %s 앞 리터럴 없음
    '[2026-11-18T10:00:01] [CLIENT(10.0.0.1)] "GET /v1/a HTTP/1.1" 20',   # 상태 2자리
    '[2026-11-18T10:00:01] [CLIENT(10.0.0.1)] "get /v1/a HTTP/1.1" 200',  # 소문자 메서드
    '[2026-11-18T10:00:01] [CLIENT(10.0.0.1)] "GET" 200',                 # 요청 토큰 1개
    '[2026-11-18T10:00:1] [CLIENT(10.0.0.1)] "GET /v1/a HTTP/1.1" 200',   # 시각 폭이 다름
    '[2026-Nov-18T10:00:1] [CLIENT(10.0.0.1)] "GET /v1/a HTTP/1.1" 200',  # 폭은 맞지만 월이 숫자가 아님
    '[2026-11-1xT10:00:01] [CLIENT(10.0.0.1)] "GET /v1/a HTTP/1.1" 200',  # 일이 숫자가 아님
    ' [2026-11-18T10:00:01] [CLIENT(10.0.0.1)] "GET /v1/a HTTP/1.1" 200', # 첫 리터럴 위치 다름
])
def test_user_format_bad_lines_are_none(line):
    assert parse_with_spec(line, compile_format(USER_A)) is None


def test_trailing_text_after_last_token_is_ignored():
    spec = compile_format('%t "%r"')  # 마지막 토큰이 리터럴 '"'
    line = '[18/Nov/2026:10:00:01 +0900] "GET /a HTTP/1.1" 200 extra "stuff"'
    assert parse_with_spec(line, spec) == ParsedLine("GET", "/a", None, "20261118")


def test_last_field_takes_rest_of_line():
    spec = compile_format('%t "%r" %s')
    line = '[18/Nov/2026:10:00:01 +0900] "GET /a HTTP/1.1" 200 512'
    assert parse_with_spec(line, spec) is None  # "200 512"는 3자리 숫자가 아님
    assert parse_with_spec(line[:-4], spec) == ParsedLine("GET", "/a", 200, "20261118")


def test_line_endings_stripped_once():
    spec = compile_format(USER_A)
    line = '[2026-11-18T10:00:01] [CLIENT(h)] "GET /a HTTP/1.1" 200'
    assert parse_with_spec(line + "\r\n", spec) == ParsedLine("GET", "/a", 200, "20261118")
    assert parse_with_spec(line + "\r", spec) == ParsedLine("GET", "/a", 200, "20261118")
    assert parse_with_spec(line + "\n\r\n", spec) is None  # "\n" 하나와 "\r" 하나만 지운다


def test_t_field_rules():
    spec = compile_format('%h %t "%r" %s')
    ok = '1.1.1.1 [18/Nov/2026:10:00:01 +0900] "GET /a HTTP/1.1" 200'
    assert parse_with_spec(ok, spec) == ParsedLine("GET", "/a", 200, "20261118")
    for bad in ('1.1.1.1 18/Nov/2026:10:00:01 "GET /a HTTP/1.1" 200',      # [ 없음
                '1.1.1.1 [18/Nov/2026:10:00:01 +0900 "GET /a HTTP/1.1" 200',  # ] 없음
                '1.1.1.1 [18/nov/2026:10:00:01 +0900] "GET /a HTTP/1.1" 200',  # 월 대소문자
                '1.1.1.1 [1/Nov/2026:10:00:01 +0900] "GET /a HTTP/1.1" 200'):
        assert parse_with_spec(bad, spec) is None, bad


def test_resolve_format():
    assert isinstance(resolve_format("webtob"), FormatSpec)
    assert resolve_format("webtob_combined") == compile_format(BUILTIN_FORMATS["webtob_combined"])
    user = [LogFormatDef("my_a", USER_A)]
    assert resolve_format("my_a", user) == compile_format(USER_A)
    custom = CustomFormat(2, 3, 4, 1, "iso8601")
    assert resolve_format("custom", user, custom) is custom
    with pytest.raises(FormatError, match="^모르는 로그 형식이에요: nope$"):
        resolve_format("nope", user)
    # 기본 제공 형식이 사용자 형식보다 먼저다
    assert resolve_format("webtob", [LogFormatDef("webtob", USER_A)]) == \
        compile_format(BUILTIN_FORMATS["webtob"])


def test_parse_line_with_user_formats():
    user = [LogFormatDef("my_b", USER_B)]
    assert parse_line("18/Nov/2026:10:00:01 GET /v1/a ?x=1 404\n", "my_b", None, user) == \
        ParsedLine("GET", "/v1/a", 404, "20261118")
    assert parse_line("18/Nov/2026:10:00:01 GET /v1/a ?x=1 404", "my_b") is None
    assert parse_line("x", "bad", None, [LogFormatDef("bad", "%h")]) is None


def test_webtob_combined():
    line = ('1.2.3.4 - - [20/May/2026:07:30:00 +0900] "POST /v1/a HTTP/1.1" 201 9 '
            '"-" "Mozilla/5.0 (X11; Linux) \\"Quoted\\" x"')
    assert parse_line(line, "webtob_combined") == ParsedLine("POST", "/v1/a", 201, "20260520")


def test_sample_line_status_class():
    line = '1.1.1.1 - - [18/Nov/2026:10:00:01 +0900] "GET /a HTTP/1.1" 404 1'
    assert run_sample_line(line, "webtob", None)["status_class"] == "4xx"
    user = [LogFormatDef("ns", '%t "%r"')]
    res = run_sample_line('[18/Nov/2026:10:00:01 +0900] "GET /a HTTP/1.1"', "ns", None, user)
    assert res["ok"] is True and res["status"] is None and res["status_class"] == "???"
    bad = run_sample_line("garbage", "webtob", None)
    assert bad["ok"] is False and bad["status_class"] is None


def test_test_format_results():
    line = '[2026-11-18T10:00:01] [CLIENT(10.0.0.1)] "GET /v1/users/123?x=1 HTTP/1.1" 200'
    assert run_test_format(USER_A, line) == {
        "ok": True, "error": None, "method": "GET", "path": "/v1/users/123?x=1",
        "normalized": "/v1/users/{*}", "status": 200, "status_class": "2xx",
        "date": "20261118",
    }
    res = run_test_format("%h%l", line)
    assert res["ok"] is False and "구분 문자" in res["error"]
    assert {k: v for k, v in res.items() if k not in ("ok", "error")} == {
        "method": None, "path": None, "normalized": None, "status": None,
        "status_class": None, "date": None}
    res = run_test_format(USER_A, "garbage")
    assert res["ok"] is False and res["error"] == "이 형식으로 읽을 수 없는 줄이에요"
