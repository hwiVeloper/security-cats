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
    assert [(t.kind, t.directive if t.kind == "field" else t.text) for t in spec.tokens] == [
        ("field", "h"), ("ws", " "), ("field", "l"), ("ws", " "), ("field", "u"),
        ("ws", " "), ("field", "t"), ("ws", " "), ("lit", '"'), ("field", "r"), ("lit", '"'),
        ("ws", " "), ("field", "s"), ("ws", " "), ("field", "b"),
    ]
    assert spec.last_essential == 12  # %s


def test_compile_splits_literals_into_exact_and_ws():
    spec = compile_format('[%{%Y-%m-%d}t]  \t[CLIENT(%h)] "%r" x %s')
    assert [(t.kind, t.text) for t in spec.tokens if t.kind != "field"] == [
        ("lit", "["), ("lit", "]"), ("ws", "  \t"), ("lit", "[CLIENT("), ("lit", ")]"),
        ("ws", " "), ("lit", '"'), ("lit", '"'), ("ws", " "), ("lit", "x"), ("ws", " "),
    ]


def test_last_essential():
    combined = compile_format(BUILTIN_FORMATS["apache_combined"])
    assert combined.tokens[combined.last_essential].directive == "s"
    spec = compile_format('%s %t %m %U ?%q %h')
    assert spec.tokens[spec.last_essential].directive == "U"
    spec = compile_format('"%r" [%t] %b')
    assert spec.tokens[spec.last_essential].directive == "t"


def test_compile_directive_mapping():
    spec = compile_format('%t "%r" %>s %{Referer}i %{X}o %B %D %T %q %H %* %Z %%x')
    fields = [t.directive for t in spec.tokens if t.kind == "field"]
    assert fields == ["t", "r", "s", "i", "o", "B", "D", "T", "q", "H", "skip", "skip"]
    assert spec.tokens[-2:] == (Token("ws", " "), Token("lit", "%x"))


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


# ---- 판정 R4: 공백 리터럴은 유연하게, 필수 토큰을 다 읽으면 성공 ----

WEBTOB_HEAD = '1.1.1.1 - - [18/Nov/2026:10:00:01 +0900] '


@pytest.mark.parametrize("line", [
    '1.1.1.1  -\t-   [18/Nov/2026:10:00:01 +0900]  "GET /a HTTP/1.1"   200  1',
    WEBTOB_HEAD + '"GET /a HTTP/1.1"\t200\t1',
    WEBTOB_HEAD + '"  GET   /a   HTTP/1.1  "   200 1',
])
def test_ws_literal_matches_any_run_of_spaces_and_tabs(line):
    assert parse_line(line, "webtob") == ParsedLine("GET", "/a", 200, "20261118")


def test_field_before_ws_ends_at_space_or_tab_and_must_be_non_empty():
    spec = compile_format('%h %m %U %t %s')
    assert parse_with_spec("1.1.1.1\tGET /a?x=1\t[18/Nov/2026:10:00:01] 200", spec) == \
        ParsedLine("GET", "/a?x=1", 200, "20261118")
    # 줄 앞 공백은 패턴이 공백으로 시작하지 않으면 건너뛰지 않는다 (%h가 빈 값 -> 실패)
    assert parse_with_spec(" 1.1.1.1 GET /a [18/Nov/2026:10:00:01] 200", spec) is None
    assert parse_line(" " + WEBTOB_HEAD + '"GET /a HTTP/1.1" 200 1', "webtob") is None


def test_pattern_starting_with_ws_requires_leading_ws():
    spec = compile_format(' %t "%r" %s')
    line = '[18/Nov/2026:10:00:01 +0900] "GET /a HTTP/1.1" 200'
    assert parse_with_spec(line, spec) is None
    assert parse_with_spec(" \t " + line, spec) == ParsedLine("GET", "/a", 200, "20261118")


def test_field_before_exact_may_be_empty_and_needs_the_literal():
    spec = compile_format('%t "%r" <%h> %s')
    ok = '[18/Nov/2026:10:00:01 +0900] "GET /a HTTP/1.1" <> 200'
    assert parse_with_spec(ok, spec) == ParsedLine("GET", "/a", 200, "20261118")
    assert parse_with_spec(ok.replace(">", ")"), spec) is None


@pytest.mark.parametrize("fmt", ["apache_combined", "nginx_combined", "webtob_combined"])
@pytest.mark.parametrize("tail", ["", " 512", " 512 garbage", ' 512 "-"', " - x\"y"])
def test_tokens_after_last_essential_are_not_checked(fmt, tail):
    line = WEBTOB_HEAD + '"GET /v1/a HTTP/1.1" 200' + tail
    assert parse_line(line, fmt) == ParsedLine("GET", "/v1/a", 200, "20261118")
    assert parse_line(line + "\r\n", fmt) == ParsedLine("GET", "/v1/a", 200, "20261118")


def test_webtob_without_bytes_field_parses():
    assert parse_line(WEBTOB_HEAD + '"GET /a HTTP/1.1" 200', "webtob") == \
        ParsedLine("GET", "/a", 200, "20261118")


@pytest.mark.parametrize("line", [
    '1.1.1.1 - - 18/Nov/2026:10:00:01 +0900 "GET /a HTTP/1.1" 200 1',    # [ 없음
    '1.1.1.1 - - [18/Nov/2026:10:00:01 +0900]"GET /a HTTP/1.1" 200 1',   # 공백 없음
    '1.1.1.1 - - [18/Nov/2026:10:00:01 +0900] GET /a HTTP/1.1 200 1',    # 따옴표 없음
    '1.1.1.1 - - [18/Nov/2026:10:00:01 +0900] "GET /a HTTP/1.1"200 1',   # 상태 앞 공백 없음
    '1.1.1.1 - [18/Nov/2026:10:00:01 +0900] "GET /a HTTP/1.1" 200 1',    # 필드 하나 빠짐
])
def test_mismatch_before_status_is_none(line):
    assert parse_line(line, "webtob") is None
    assert parse_line(line, "apache_combined") is None


def test_user_format_mismatch_before_last_essential_is_none():
    spec = compile_format(USER_A)
    good = '[2026-11-18T10:00:01] [CLIENT(h)] "GET /a HTTP/1.1" 200'
    assert parse_with_spec(good, spec) == ParsedLine("GET", "/a", 200, "20261118")
    assert parse_with_spec(good.replace("[CLIENT(", "[CLIENT "), spec) is None
    assert parse_with_spec(good.replace(")]", ")"), spec) is None


def test_early_success_without_status_format():
    spec = compile_format('%t "%r" %h [%{%d/%b/%Y}t]')
    assert spec.has_status is False
    assert spec.tokens[spec.last_essential].directive == "tfmt"
    line = '[18/Nov/2026:10:00:01 +0900] "GET /a HTTP/1.1" h [19/Nov/2026]'
    assert parse_with_spec(line, spec) == ParsedLine("GET", "/a", None, "20261119")
    assert parse_with_spec(line.replace("[19", "(19"), spec) is None
    # 날짜 없는 시각 필드는 필수가 아니다
    spec = compile_format('%t "%r" %{%H:%M}t')
    line = '[18/Nov/2026:10:00:01 +0900] "GET /a HTTP/1.1" bogus'
    assert parse_with_spec(line, spec) == ParsedLine("GET", "/a", None, "20261118")


# ---- 판정 R5: %{...}t는 커서부터 정확히 time_width 바이트를 먹는다 ----

SPACE_TIME = "%{%Y-%m-%d %H:%M:%S}t"


@pytest.mark.parametrize("line", [
    '2026-11-18 10:00:01 1.1.1.1 "GET /a HTTP/1.1" 200',
    '2026-11-18 10:00:01 \t 1.1.1.1 "GET /a HTTP/1.1" 200',
])
def test_space_containing_time_followed_by_ws(line):
    spec = compile_format(SPACE_TIME + ' %h "%r" %s')
    assert parse_with_spec(line, spec) == ParsedLine("GET", "/a", 200, "20261118")


def test_space_containing_time_followed_by_exact_literal():
    spec = compile_format("[" + SPACE_TIME + '] "%r" %s')
    assert parse_with_spec('[2026-11-18 10:00:01] "GET /a HTTP/1.1" 200', spec) == \
        ParsedLine("GET", "/a", 200, "20261118")
    # 폭보다 짧은 값: 19바이트를 먹으면 "]"까지 들어가고 다음 리터럴 "]"가 맞지 않는다
    assert parse_with_spec('[2026-11-18 10:00:1] "GET /a HTTP/1.1" 200', spec) is None
    # 폭보다 긴 값
    assert parse_with_spec('[2026-11-18 10:00:011] "GET /a HTTP/1.1" 200', spec) is None


@pytest.mark.parametrize("pattern,line", [
    (SPACE_TIME + ' "%r"', "2026-11-18 10:0"),                      # 줄이 폭보다 짧음
    ('"%r" ' + SPACE_TIME, '"GET /a HTTP/1.1" 2026-11-18 10:00'),   # 마지막 필수 토큰이 짧음
    ('"%r" ' + SPACE_TIME, '"GET /a HTTP/1.1" '),
])
def test_line_too_short_for_time_width_is_none(pattern, line):
    assert parse_with_spec(line, compile_format(pattern)) is None


def test_time_width_counts_utf8_bytes():
    spec = compile_format('%{%Y년%m월%d일}t "%r"')
    assert spec.tokens[0].time_width == 4 + 3 + 2 + 3 + 2 + 3
    assert parse_with_spec('2026년11월18일 "GET /a HTTP/1.1"', spec) == \
        ParsedLine("GET", "/a", None, "20261118")


def test_time_width_ending_inside_a_character():
    # 11바이트째가 "가"(3바이트)의 첫 바이트다. awk처럼 바이트로 자른다.
    # 마지막 필수 토큰이면 그대로 성공 (레이아웃의 리터럴 X는 비교하지 않는다)
    last = compile_format('"%r" %{%Y-%m-%dX}t')
    assert parse_with_spec('"GET /a HTTP/1.1" 2026-11-18가', last) == \
        ParsedLine("GET", "/a", None, "20261118")
    # 뒤에 토큰이 남아 있으면 커서가 글자 중간이라 다음 토큰이 맞을 수 없다
    first = compile_format('%{%Y-%m-%dX}t "%r"')
    assert parse_with_spec('2026-11-18가 "GET /a HTTP/1.1"', first) is None


# ---- 판정 R6 ----

@pytest.mark.parametrize("pattern", [
    '%t "%r" \ud800',
    '%t "%r" %{\udfff}i',
    '%{%Y-%m-%d\udc80}t "%r"',   # surrogateescape 범위도 형식에는 쓸 수 없다
])
def test_lone_surrogate_in_pattern_is_format_error(pattern):
    with pytest.raises(FormatError, match="^형식에 쓸 수 없는 문자가 있어요$"):
        compile_format(pattern)
    res = run_test_format(pattern, '[18/Nov/2026:10:00:01 +0900] "GET /a HTTP/1.1"')
    assert res["ok"] is False and res["error"] == "형식에 쓸 수 없는 문자가 있어요"


@pytest.mark.parametrize("line", [
    '1.1.1.1 - - [18/Nov/2026:10:00:01 +0900] "GET /a\ud800 HTTP/1.1" 200 1',
    '1.1.1.1 - - [18/Nov/2026:10:00:01 +0900] "GET /a HTTP/1.1" 200 1 \udfff',
])
def test_lone_surrogate_in_line_is_none(line):
    assert parse_line(line, "webtob") is None
    assert parse_with_spec(line, compile_format(BUILTIN_FORMATS["webtob"])) is None
    custom = CustomFormat(method_field=6, path_field=7, status_field=9, time_field=4,
                          time_format="clf")
    assert parse_line(line, "custom", custom) is None
    res = run_sample_line(line, "webtob", None)
    assert res["ok"] is False and res["error"] == "이 형식으로 읽을 수 없는 줄이에요"
    assert run_sample_line(line, "custom", custom)["ok"] is False
    assert run_test_format(BUILTIN_FORMATS["webtob"], line)["ok"] is False
    # tfmt 폭 계산 경로 (UTF-8 바이트로 자른다)
    spec = compile_format('%{%Y-%m-%d}t "%r"')
    assert parse_with_spec('2026-11-18 "GET /a HTTP/1.1" \ud800', spec) is None
    assert parse_with_spec('\ud8002026-11-18 "GET /a HTTP/1.1"', spec) is None


def test_surrogateescape_bytes_in_line_still_parse():
    """원격 로그의 깨진 UTF-8 바이트(U+DC80..U+DCFF로 읽힘)는 awk처럼 그대로 읽는다."""
    line = b'1.1.1.1 - - [18/Nov/2026:10:00:01 +0900] "GET /a\xff HTTP/1.1" 200 1'
    parsed = parse_line(line.decode("utf-8", "surrogateescape"), "webtob")
    assert parsed is not None and parsed.path == "/a\udcff"


@pytest.mark.parametrize("pattern", ['%t %r %s', '%t %r\t%s', '%t %>r  %s', '%t x%r y'])
def test_request_followed_by_whitespace_is_error(pattern):
    with pytest.raises(FormatError, match=r'^%r은 따옴표로 감싸 주세요 \(예: "%r"\)$'):
        compile_format(pattern)


def test_request_followed_by_other_literal_or_end_is_ok():
    assert compile_format('%t "%r"').last_essential == 3
    assert compile_format('%t [%r]').tokens[-2].directive == "r"
    spec = compile_format('%t %s %r')  # %r이 마지막이면 줄 끝까지
    assert parse_with_spec('[18/Nov/2026:10:00:01 +0900] 200 GET /a HTTP/1.1', spec) == \
        ParsedLine("GET", "/a", 200, "20261118")


def test_status_with_less_than_modifier():
    spec = compile_format('%h %l %u %t "%r" %<s %b')
    assert spec.has_status
    assert [t.directive for t in spec.tokens if t.kind == "field"] == \
        ["h", "l", "u", "t", "r", "s", "b"]
    assert spec.tokens[spec.last_essential].text == "%<s"
    line = '1.1.1.1 - - [18/Nov/2026:10:00:01 +0900] "GET /a HTTP/1.1" 404 1'
    assert parse_with_spec(line, spec) == ParsedLine("GET", "/a", 404, "20261118")


# ---- 판정 R7: 탭을 뺀 C0 제어 문자는 형식에 쓸 수 없다 ----

@pytest.mark.parametrize("pattern", [
    '%t "%r" \x00',
    '%t "%r"\n%s',
    '%t "%r" \x01x',
    '%t\r"%r"',
    '%t "%r" %{a\x1fb}i',
    '%{%Y-%m-%d\x1b}t "%r"',
])
def test_control_chars_in_pattern_are_format_error(pattern):
    with pytest.raises(FormatError, match="^형식에 쓸 수 없는 문자가 있어요$"):
        compile_format(pattern)
    res = run_test_format(pattern, '[18/Nov/2026:10:00:01 +0900] "GET /a HTTP/1.1"')
    assert res["ok"] is False and res["error"] == "형식에 쓸 수 없는 문자가 있어요"


def test_tab_in_pattern_is_allowed():
    spec = compile_format('%t\t"%r"\t%s')
    assert parse_with_spec('[18/Nov/2026:10:00:01 +0900]\t"GET /a HTTP/1.1"\t200', spec) == \
        ParsedLine("GET", "/a", 200, "20261118")


# ---- 최종 리뷰 C1: %U 값은 첫 [ \t] 토큰만 경로로 쓴다 (꼬리 원문이 새지 않게) ----

COMBINED_TAIL = ' HTTP/1.1" 200 512 "https://ref.example/secret?k=1" "Mozilla/5.0 (X11)"'


def test_trailing_U_takes_first_token_only():
    spec = compile_format('%t %m %U')
    line = '[18/Nov/2026:10:00:01 +0900] GET /v1/a?x=1' + COMBINED_TAIL
    assert parse_with_spec(line, spec) == ParsedLine("GET", "/v1/a?x=1", None, "20261118")


def test_quoted_U_with_tab_takes_first_token_only():
    spec = compile_format('%t %m "%U" %s')
    line = '[18/Nov/2026:10:00:01 +0900] GET "/v1/a\tleak me" 200'
    assert parse_with_spec(line, spec) == ParsedLine("GET", "/v1/a", 200, "20261118")
    line = '[18/Nov/2026:10:00:01 +0900] GET " \t/v1/b  x" 200'  # 앞 공백은 건너뛴다
    assert parse_with_spec(line, spec) == ParsedLine("GET", "/v1/b", 200, "20261118")


@pytest.mark.parametrize("line", [
    '[18/Nov/2026:10:00:01 +0900] GET "" 200',
    '[18/Nov/2026:10:00:01 +0900] GET " \t " 200',
])
def test_U_without_token_fails(line):
    assert parse_with_spec(line, compile_format('%t %m "%U" %s')) is None


def test_trailing_U_without_token_fails():
    spec = compile_format('%t %m %U')
    assert parse_with_spec('[18/Nov/2026:10:00:01 +0900] GET \t ', spec) is None


# ---- 최종 리뷰 I2: 범위를 벗어난 월(01-12)/일(01-31)은 그 줄만 실패 ----

@pytest.mark.parametrize("day,ok", [
    ("00", False), ("01", True), ("31", True), ("32", False), ("99", False)])
def test_clf_time_day_range(day, ok):
    line = f'1.1.1.1 - - [{day}/Nov/2026:10:00:01 +0900] "GET /a HTTP/1.1" 200 1'
    p = parse_line(line, "webtob")
    assert (p is not None) == ok
    if ok:
        assert p.date == "202611" + day


@pytest.mark.parametrize("ymd,ok", [
    ("2026-00-18", False), ("2026-01-18", True), ("2026-12-18", True), ("2026-13-18", False),
    ("2026-99-18", False), ("2026-11-00", False), ("2026-11-01", True), ("2026-11-31", True),
    ("2026-11-32", False), ("2026-11-99", False)])
def test_tfmt_month_day_range(ymd, ok):
    spec = compile_format('%{%Y-%m-%d}t "%r"')
    p = parse_with_spec(ymd + ' "GET /a HTTP/1.1"', spec)
    assert (p is not None) == ok
    if ok:
        assert p.date == ymd.replace("-", "")


def test_tfmt_day_range_with_month_name():
    spec = compile_format('%{%d/%b/%Y}t "%r"')
    assert parse_with_spec('00/Nov/2026 "GET /a b"', spec) is None
    assert parse_with_spec('32/Nov/2026 "GET /a b"', spec) is None
    assert parse_with_spec('31/Dec/2026 "GET /a b"', spec).date == "20261231"


@pytest.mark.parametrize("time_s,ok", [
    ("2026-13-01T10:00:00", False), ("2026-00-01", False), ("2026-11-00", False),
    ("2026-11-32", False), ("2026-12-31T00:00:00", True), ("2026-01-01", True)])
def test_custom_iso_month_day_range(time_s, ok):
    c = CustomFormat(method_field=2, path_field=3, status_field=4, time_field=1,
                     time_format="iso8601")
    p = parse_line(f"{time_s} GET /a 200", "custom", c)
    assert (p is not None) == ok


@pytest.mark.parametrize("day,ok", [("00", False), ("32", False), ("31", True)])
def test_custom_clf_day_range(day, ok):
    c = CustomFormat(method_field=4, path_field=5, status_field=6, time_field=2,
                     time_format="clf")
    p = parse_line(f"1.1.1.1 [{day}/Nov/2026:10:00:01 +0900] GET /a 200", "custom", c)
    assert (p is not None) == ok
