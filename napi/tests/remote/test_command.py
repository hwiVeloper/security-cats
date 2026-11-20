import re
import shlex

import pytest

from napi.logformat import BUILTIN_FORMATS, FormatError
from napi.models import CustomFormat, LogFormatDef, LogPath, ServerConfig, Settings
from napi.remote.awk_program import awk_program, awk_program_multi
from napi.remote.command import build_pipeline, build_remote_command, split_glob

TOKEN = "napi-scan-0a1b2c3d"
USER_FORMATS = [
    LogFormatDef("my", '[%{%Y-%m-%dT%H:%M:%S}t] [CLIENT(%h)] "%r" %s'),
    LogFormatDef("my_b", "%{%d/%b/%Y:%H:%M:%S}t %m %U ?%q %s"),
]
ALL_FORMATS = [*BUILTIN_FORMATS, "my", "my_b"]


def make_server(log_globs=("/webtob/log/access.log*",), log_format="webtob",
                custom_format=None, **kw):
    """경로마다 같은 형식 (log_globs 하나하나가 log_format/custom_format을 쓰는 LogPath)."""
    return ServerConfig(**{"name": "web1", "host": "10.0.0.1", "user": "app", **kw},
                        log_paths=[LogPath(g, log_format, custom_format) for g in log_globs])


def program_of(server, formats=()):
    return awk_program_multi([(p.log_format, p.custom_format) for p in server.log_paths],
                             formats)


def shell_portions(server, settings, since):
    """(원격 명령의 바깥 셸 부분, sh -c 본문의 셸 부분). awk 프로그램은 자리표시자로 바꾼다."""
    prog_q = shlex.quote(program_of(server))
    script = build_pipeline(server, settings, since, TOKEN)
    assert script.count(prog_q) == 1
    script_q = shlex.quote(script)
    cmd = build_remote_command(server, settings, since, TOKEN)
    assert cmd.count(script_q) == 1
    return cmd.replace(script_q, "SCRIPT"), script.replace(prog_q, "AWK_PROGRAM")


def test_split_glob():
    assert split_glob("/webtob/log/access.log*") == ("/webtob/log", "access.log*")
    assert split_glob("/access.log") == ("/", "access.log")


@pytest.mark.parametrize("log_glob", [
    "-delete/x", "access.log*", "./access.log", "", "/var/lo*g/x", "/var/lo?/x",
    "/var/[l]og/x", "/var/log/",
])
def test_split_glob_rejects_unsafe(log_glob):
    with pytest.raises(ValueError):
        split_glob(log_glob)
    with pytest.raises(ValueError):
        build_pipeline(make_server(log_globs=[log_glob]), Settings(), None, TOKEN)
    with pytest.raises(ValueError):  # 여러 개 중 하나만 잘못돼도 거부한다
        build_pipeline(make_server(log_globs=["/ok/a*", log_glob]), Settings(), None, TOKEN)


def test_command_quotes_values():
    server = make_server(log_globs=["/x/it's/a*.log"])
    cmd = build_remote_command(server, Settings(), None, TOKEN)
    assert "'\"'\"'" in cmd
    script = build_pipeline(server, Settings(), None, TOKEN)
    # 바깥 셸이 풀면 sh -c 본문이 그대로 한 인자로 넘어간다
    assert script in shlex.split(cmd)
    # 본문 안에서 find는 디렉터리와 패턴을 각각 한 인자로 받는다
    words = shlex.split(script)
    assert words[:3] == ["g1=$(find", "/x/it's", "-maxdepth"]
    assert words[words.index("-name") + 1] == "a*.log"


def test_command_quotes_shell_metacharacters():
    server = make_server(log_globs=["/x/$(id)`id`;rm -rf ~/a b*.log"])
    script = build_pipeline(server, Settings(), None, TOKEN)
    words = shlex.split(script)
    assert words[1] == "/x/$(id)`id`;rm -rf ~"
    assert words[words.index("-name") + 1] == "a b*.log"


def test_command_contains_safety_wrappers():
    cmd = build_remote_command(make_server(), Settings(), "20260522", TOKEN)
    assert "timeout 3600" in cmd
    assert "nice -n 19" in cmd
    assert "LC_ALL=C awk" in cmd
    assert "-newermt 2026-05-22" in cmd
    assert "-v SINCE=20260522" in cmd
    assert "-v MAXROWS=50000" in cmd
    assert "-v TOP404=200" in cmd
    assert "-v MAXKEYS=200000" in cmd
    assert '-v MARK="$0"' in cmd


def test_pipeline_markers_carry_token_from_dollar_zero():
    """파일 표시와 오류 표시는 토큰("$0")을 담고, awk도 같은 "$0"을 MARK로 받는다."""
    script = build_pipeline(make_server(), Settings(), None, TOKEN)
    assert TOKEN not in script  # 토큰은 실행할 때 $0으로만 들어온다
    assert r"""printf '\n\001F%s\t%s\t%s\n' "$0" "$n" "$f";""" in script
    assert r"""if [ ! -r "$f" ]; then printf '\n\001E%s\tunreadable\t%s\n' "$0" "$f"; continue; fi;""" in script
    assert r"""gzip -dcf -- "$f" 2>/dev/null || printf '\n\001E%s\tdecompress_failed\t%s\n' "$0" "$f";""" in script
    assert script.count('-v MARK="$0"') == 1


def test_command_shape_passes_token_as_dollar_zero():
    """R2: timeout <sec> nice -n 19 [ionice -c3] sh -c '<script>' <token>"""
    settings = Settings(remote_timeout_sec=120)
    server = make_server()
    cmd = build_remote_command(server, settings, None, TOKEN)
    script = build_pipeline(server, settings, None, TOKEN)
    assert cmd == (
        "timeout 120 nice -n 19 "
        "$(command -v ionice >/dev/null 2>/dev/null && echo ionice -c3) "
        f"sh -c {shlex.quote(script)} {TOKEN}")
    assert "NAPI_TOKEN" not in cmd


def test_command_without_since_has_no_newermt():
    cmd = build_remote_command(make_server(), Settings(), None, TOKEN)
    assert "-newermt" not in cmd
    assert "-v SINCE=0" in cmd


@pytest.mark.parametrize("globs", [
    ["/webtob/log/access.log*"],
    ["/webtob/log/access.log*", "/x/it's/a b*.log", "/x/$(id)`id`;rm -rf ~/c>d<e*"],
])
@pytest.mark.parametrize("since", [None, "20260522"])
@pytest.mark.parametrize("fmt,custom", [
    ("webtob", None),
    ("custom", CustomFormat(method_field=2, path_field=3, status_field=4,
                            time_field=1, time_format="iso8601")),
])
def test_command_is_read_only(since, fmt, custom, globs):
    server = make_server(log_format=fmt, custom_format=custom, log_globs=globs)
    for text in shell_portions(server, Settings(), since):
        for g in globs:  # 따옴표로 감싼 사용자 값은 검사 대상에서 뺀다
            for value in (g, *split_glob(g)):
                text = text.replace(shlex.quote(value), "Q")
        redirections = re.findall(r"[0-9]*>+&?[^\s;|&)]*", text)
        assert redirections, text
        assert set(redirections) <= {">/dev/null", "2>/dev/null"}, text
        assert ">>" not in text
        assert "<" not in text
        for word in ("rm", "mv", "tee", "cp", "dd", "touch", "mkdir", "ln",
                     "chmod", "truncate", "sed", "perl", "python"):
            assert not re.search(rf"(^|[\s;|&(]){word}(\s|$)", text), (word, text)


@pytest.mark.parametrize("fmt,custom", [
    *((f, None) for f in ALL_FORMATS),
    ("custom", CustomFormat(method_field=2, path_field=3, status_field=4,
                            time_field=1, time_format="clf")),
])
def test_awk_program_writes_no_files(fmt, custom):
    prog = awk_program(fmt, custom, USER_FORMATS)
    assert not re.search(r"print[f]?[^\n;{}]*>", prog)
    assert ">>" not in prog
    assert "system(" not in prog
    assert "getline" not in prog
    # 외부 명령은 집계 결과를 정렬하는 sort | head 뿐이다
    assert set(re.findall(r'"(sort [^"]*)"', prog)) == {
        r"sort -t '\t' -k5,5nr 2>/dev/null | head -n ",
        r"sort -t '\t' -k4,4nr 2>/dev/null | head -n ",
    }


@pytest.mark.parametrize("fmt", ALL_FORMATS)
def test_awk_program_has_no_gawk_extensions(fmt):
    prog = awk_program(fmt, None, USER_FORMATS)
    for ext in ("gensub", "asort", "strftime", "systime", "FPAT", "IGNORECASE",
                "PROCINFO", "BEGINFILE", "ENDFILE", "**", "@include"):
        assert ext not in prog, ext
    assert not re.search(r"\{[0-9]+,?[0-9]*\}", prog.replace("{*}", "")), "interval expr"


def test_awk_program_rejects_unknown_format():
    with pytest.raises(ValueError):
        awk_program("iis", None)
    with pytest.raises(ValueError):
        awk_program("custom", None)
    with pytest.raises(ValueError):
        awk_program("custom", CustomFormat(method_field=0, path_field=3, status_field=4,
                                           time_field=1, time_format="clf"))


@pytest.mark.parametrize("token", ["", "napi-scan-XYZ", "napi-scan-0a1b2c3d; rm -rf /",
                                   "napi-scan-0a1b2c3"])
def test_command_rejects_bad_token(token):
    with pytest.raises(ValueError):
        build_remote_command(make_server(), Settings(), None, token)
    with pytest.raises(ValueError):
        build_pipeline(make_server(), Settings(), None, token)


@pytest.mark.parametrize("since", ["2026-05-22", "2026052", "20260522;", ""])
def test_command_rejects_bad_since(since):
    with pytest.raises(ValueError):
        build_pipeline(make_server(), Settings(), since, TOKEN)


def test_command_rejects_non_integer_settings():
    with pytest.raises(ValueError):
        build_pipeline(make_server(), Settings(max_rows="1; rm -rf /"), None, TOKEN)
    with pytest.raises(ValueError):
        build_remote_command(make_server(), Settings(remote_timeout_sec=-1), None, TOKEN)


def test_command_rejects_zero_max_rows():
    """MAXKEYS = 4 × max_rows이므로 max_rows=0이면 어떤 키도 셀 수 없다."""
    with pytest.raises(ValueError):
        build_pipeline(make_server(), Settings(max_rows=0), None, TOKEN)
    assert "MAXKEYS=4 " in build_pipeline(make_server(), Settings(max_rows=1), None, TOKEN)


def test_awk_program_has_no_clf_parser():
    """v1 CLF 전용 파서는 없어졌다. 기본 제공 형식도 생성한 parse()를 쓴다."""
    import napi.remote.awk_program as mod
    assert not hasattr(mod, "_PARSE_CLF")
    for fmt in BUILTIN_FORMATS:
        prog = awk_program(fmt, None)
        assert "match(line, /\\[" not in prog
        assert prog.count("function parse(") == 1
    assert awk_program("apache_common", None) == awk_program("apache_common", None, USER_FORMATS)


def test_command_passes_formats():
    server = make_server(log_format="my")
    prog_q = shlex.quote(awk_program("my", None, USER_FORMATS))
    assert prog_q == shlex.quote(program_of(server, USER_FORMATS))
    script = build_pipeline(server, Settings(), None, TOKEN, USER_FORMATS)
    assert script.count(prog_q) == 1
    cmd = build_remote_command(server, Settings(), None, TOKEN, formats=USER_FORMATS)
    assert cmd.count(shlex.quote(script)) == 1
    assert "[CLIENT(" in prog_q
    # 이름을 모르면 FormatError (ValueError)
    for bad_server, fmts in ((make_server(log_format="nope"), USER_FORMATS),
                             (server, ())):
        with pytest.raises(FormatError, match="모르는 로그 형식이에요"):
            build_remote_command(bad_server, Settings(), None, TOKEN, formats=fmts)
        with pytest.raises(ValueError):
            build_pipeline(bad_server, Settings(), None, TOKEN, fmts)
    # 기본 제공 형식 이름이 사용자 형식보다 먼저다
    shadow = [LogFormatDef("webtob", '%t "%r"')]
    assert awk_program("webtob", None, shadow) == awk_program("webtob", None)


@pytest.mark.parametrize("since", [None, "20260522"])
def test_command_with_user_format_is_read_only(since):
    server = make_server(log_format="my")
    prog_q = shlex.quote(awk_program("my", None, USER_FORMATS))
    script = build_pipeline(server, Settings(), since, TOKEN, USER_FORMATS)
    text = script.replace(prog_q, "AWK_PROGRAM")
    assert set(re.findall(r"[0-9]*>+&?[^\s;|&)]*", text)) <= {">/dev/null", "2>/dev/null"}


def test_awk_string_literal_escaping():
    """패턴 리터럴은 ASCII awk 문자열로만 들어간다: \\ " 는 역슬래시로, 제어 문자와
    비ASCII 바이트, @(자리표시자 문자)는 3자리 8진수로."""
    from napi.remote.awk_program import _awk_str
    assert _awk_str('a"b\\c') == r'"a\"b\\c"'
    assert _awk_str("x\x01\n\t\x7f\x00") == r'"x\001\012\011\177\000"'
    assert _awk_str("가") == r'"\352\260\200"'
    assert _awk_str("@PARSE@ / }{ ' $1") == r'''"\100PARSE\100 / }{ ' $1"'''




# ---- Task 3: 로그 경로 여러 개 ----

def test_pipeline_multiple_globs_structure():
    globs = ["/a/x/access.log*", "/b/it's/err*.log", "/a/x/access.log*"]
    script = build_pipeline(make_server(log_globs=globs), Settings(), "20260522", TOKEN)
    # 중복 glob은 한 번만 find한다
    assert script.count("find ") == 2
    for directory, pattern in (("/a/x", "access.log*"), ("/b/it's", "err*.log")):
        cmd = f"find {shlex.quote(directory)} -maxdepth 1 -type f -name {shlex.quote(pattern)}"
        assert script.count(cmd + " -newermt 2026-05-22 2>/dev/null") == 1
    # 비어 있는 glob은 토큰 마커로 알린다 (glob은 따옴표로 감싼다)
    for g in ("/a/x/access.log*", "/b/it's/err*.log"):
        assert script.count(r"""printf '\n\001E%s\tglob_empty\t%s\n' "$0" """
                            + shlex.quote(g) + ";") == 1
    # 목록 줄마다 경로 번호를 붙인다 (중복 glob 3번은 find하지 않으므로 번호도 없다)
    assert r"""printf '1/%s\n' "$f"; done;""" in script
    assert r"""printf '2/%s\n' "$f"; done;""" in script
    assert "printf '3/" not in script and "$g3" not in script
    # 경로, 번호 순으로 정렬해 같은 경로의 첫 줄만 파일 루프가 읽는다
    # sort는 원격 세션의 로캘이 아니라 바이트 순서로 (LANG=ko_KR.UTF-8 등에서 이름이 합쳐지지 않게)
    assert ("| LC_ALL=C sort -t/ -k2 -k1,1n | { p=; while IFS= read -r l; do "
            'n=${l%%/*}; f=${l#*/}; [ "$f" = "$p" ] && continue; p=$f; ') in script
    assert "| sort |" not in script
    assert "sort -u" not in script


def test_pipeline_requires_a_glob():
    with pytest.raises(ValueError, match="로그 경로를 하나 이상"):
        build_pipeline(make_server(log_globs=[]), Settings(), None, TOKEN)
