"""원격 파이프라인(find | gzip | awk)을 로컬 bash로 돌려 Python twin과 비교한다.

출력은 항상 bytes로 다룬다 (줄바꿈 변환 없음).
"""
import gzip
import os
import re
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

from napi.logformat import FormatError, normalize_path, parse_line, status_class
from napi.models import CustomFormat, LogFormatDef, ServerConfig, Settings
from napi.remote.awk_program import awk_program
from napi.remote.command import build_pipeline, build_remote_command

FIXTURES = Path(__file__).parent.parent / "fixtures" / "logs"
FORMATS = {
    "webtob": "webtob_access.log",
    "nginx_combined": "nginx_access.log",
    "apache_common": "apache_common.log",
}
# 기본 제공 형식 5개 전부. combined 계열은 nginx fixture(.gz)를 같이 쓴다.
BUILTIN_FIXTURES = {**FORMATS, "apache_combined": "nginx_access.log",
                    "webtob_combined": "nginx_access.log"}
USER_A = '[%{%Y-%m-%dT%H:%M:%S}t] [CLIENT(%h)] "%r" %s'
USER_B = "%{%d/%b/%Y:%H:%M:%S}t %m %U ?%q %s"
USER_FORMATS = [LogFormatDef("user_a", USER_A), LogFormatDef("user_b", USER_B)]
USER_FIXTURES = {"user_a": "user_a.log", "user_b": "user_b.log"}
GZIPPED = "nginx_access.log"
TOKEN = "napi-scan-00c0ffee"
TAGS = (b"F\t", b"R\t", b"P\t", b"Q\t", b"T\t", b"E\t", b"D\t")


@pytest.fixture(scope="module")
def bash():
    exe = shutil.which("bash")
    if exe is None:
        pytest.skip("bash 없음")
    probe = subprocess.run(
        [exe, "-c", "command -v awk && command -v gzip && command -v sort && "
                    "command -v head && find --version"],
        capture_output=True)
    if probe.returncode != 0 or b"GNU findutils" not in probe.stdout:
        pytest.skip("awk/gzip/sort/head/GNU find 없음")
    return exe


@pytest.fixture
def logdir(tmp_path):
    """fixture 3개를 tmp에 복사하고 nginx 것은 .gz로 압축한다."""
    d = tmp_path.resolve() / "logs"
    d.mkdir()
    for name in FORMATS.values():
        data = (FIXTURES / name).read_bytes()
        if name == GZIPPED:
            (d / (name + ".gz")).write_bytes(gzip.compress(data))
        else:
            (d / name).write_bytes(data)
    return d


def posix(path: Path) -> str:
    """bash가 받는 절대 경로. Windows의 C:/x는 Git Bash(MSYS) 형식 /c/x로 바꾼다."""
    s = path.as_posix()
    if sys.platform == "win32" and re.match(r"[A-Za-z]:/", s):
        s = "/" + s[0].lower() + s[2:]
    return s


def server_for(directory: Path, pattern: str, fmt: str, custom=None) -> ServerConfig:
    return server_globs([posix(directory) + "/" + pattern], fmt, custom)


def server_globs(globs: list[str], fmt: str, custom=None) -> ServerConfig:
    return ServerConfig(name="t", host="h", user="u", log_globs=globs,
                        log_format=fmt, custom_format=custom)


def linux_like(script: str) -> str:
    """Git Bash(MSYS)의 gawk는 입력을 텍스트 모드로 열어 CR을 지운다. Linux gawk처럼
    바이트 그대로 읽도록 테스트에서만 BINMODE=3을 준다 (Linux gawk에는 영향 없음)."""
    if sys.platform == "win32" and "LC_ALL=C awk --posix " not in script:
        assert script.count("LC_ALL=C awk ") == 1
        script = script.replace("LC_ALL=C awk ", "LC_ALL=C awk -v BINMODE=3 ")
    return script


def bash_c(bash, script: str, *args: str, cwd=None) -> subprocess.CompletedProcess:
    """bash -c <script> <args...>. Windows에서 네이티브 프로세스가 MSYS bash에 넘기는
    인자는 약 8 KB에서 잘리므로(테스트 환경만의 한계, Linux는 128 KiB) 스크립트를 임시 파일에
    쓰고 bash -c '. <파일>' <args...>로 돌린다. source는 $0(토큰)을 바꾸지 않는다."""
    if sys.platform != "win32":
        return subprocess.run([bash, "-c", script, *args], capture_output=True,
                              timeout=120, cwd=cwd)
    fd, name = tempfile.mkstemp(suffix=".sh")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(script.encode("utf-8", "surrogateescape"))
        return subprocess.run([bash, "-c", ". " + shlex.quote(posix(Path(name).resolve())),
                               *args], capture_output=True, timeout=120, cwd=cwd)
    finally:
        os.unlink(name)


def run_script(bash, script: str, cwd=None) -> list[bytes]:
    r = bash_c(bash, linux_like(script), TOKEN, cwd=cwd)
    assert r.returncode == 0, r.stderr
    assert r.stdout.endswith(b"\n")
    return r.stdout[:-1].split(b"\n")


def run_pipeline(bash, server, settings=None, since=None, formats=(),
                 cwd=None) -> list[bytes]:
    return run_script(bash, build_pipeline(server, settings or Settings(), since, TOKEN,
                                           formats), cwd)


def is_gawk(bash) -> bool:
    return b"GNU Awk" in subprocess.run([bash, "-c", "awk --version"],
                                        capture_output=True).stdout


def run_pipeline_posix(bash, server, settings=None, since=None, formats=(),
                       cwd=None) -> list[bytes]:
    """gawk --posix로 돌린다. gawk 전용 문법이 있으면 여기서 실패한다."""
    script = build_pipeline(server, settings or Settings(), since, TOKEN, formats)
    assert script.count("LC_ALL=C awk ") == 1
    return run_script(bash, script.replace("LC_ALL=C awk ", "LC_ALL=C awk --posix "), cwd)


# ---- Python twin: parse_line + normalize_path + 같은 키 + 404 전용 분리 + since ----

def _records(data: bytes) -> list[bytes]:
    recs = data.split(b"\n")
    if recs[-1] == b"":
        recs.pop()
    return recs


def _b(s: str) -> bytes:
    return s.encode("utf-8", "surrogateescape")


def twin(files: list[tuple[str, bytes]], fmt, custom=None, since=None,
         max_rows=50000, top404=200, formats=()) -> list[bytes]:
    out = []
    agg = {}
    non404 = set()
    for path, data in sorted(files):
        recs = _records(data)
        pf = oor = 0
        dates = []
        for rec in recs:
            p = parse_line(rec.decode("utf-8", "surrogateescape"), fmt, custom, formats)
            if p is None:
                pf += 1
                continue
            dates.append(p.date)
            if since is not None and p.date < since:
                oor += 1
                continue
            status = "" if p.status is None else f"{p.status:03d}"  # 상태 미상 = 404 아님
            mp = (_b(p.method), _b(normalize_path(p.path)))
            key = mp + (_b(status_class(p.status)),)
            n, first, last = agg.get(key, (0, p.date, p.date))
            agg[key] = (n + 1, min(first, p.date), max(last, p.date))
            if status != "404":
                non404.add(mp)
        out.append(b"\t".join([b"F", _b(path),
                               _b(min(dates)) if dates else b"-",
                               _b(max(dates)) if dates else b"-",
                               b"%d" % len(recs), b"%d" % pf, b"%d" % oor]))
    if not files:
        out.insert(0, b"E\tno files matched")

    def line(tag, fields, n, first, last):
        return b"\t".join([tag, *fields, b"%d" % n, _b(first), _b(last)])

    def top(lines, keep):  # sort -t '\t' -kN,Nnr | head -n keep (LC_ALL=C)
        return sorted(lines, key=lambda x: (-x[0], x[1]))[:keep]

    rows = [(n, line(b"R", k, n, f, l)) for k, (n, f, l) in agg.items() if k[:2] in non404]
    if len(rows) > max_rows:
        out += [x for _, x in top(rows, max_rows)] + [b"T\t%d" % max_rows]
    else:
        out += [x for _, x in rows]
    probes = [(n, line(b"P", k[:2], n, f, l)) for k, (n, f, l) in agg.items()
              if k[:2] not in non404]
    kept = top(probes, top404)
    out += [x for _, x in kept]
    out.append(b"Q\t%d\t%d" % (len(probes) - len(kept),
                               sum(n for n, _ in probes) - sum(n for n, _ in kept)))
    out.append(b"D\tdone")
    return out


def twin_dir(directory: Path, pattern_prefix: str, fmt, **kw) -> list[bytes]:
    files = []
    for f in directory.iterdir():
        if f.name.startswith(pattern_prefix):
            data = f.read_bytes()
            if f.suffix == ".gz":
                data = gzip.decompress(data)
            files.append((posix(directory) + "/" + f.name, data))
    return twin(files, fmt, **kw)


def assert_same(actual: list[bytes], expected: list[bytes]):
    assert actual[-1] == b"D\tdone"
    assert sorted(actual) == sorted(expected)
    # P 줄은 count 내림차순 순서까지 같아야 한다
    assert [x for x in actual if x.startswith(b"P\t")] == \
        [x for x in expected if x.startswith(b"P\t")]


def f_lines(out):
    return [x.split(b"\t") for x in out if x.startswith(b"F\t")]


# ---- tests ----

@pytest.mark.parametrize("since", [None, "20260801", "20261118"])
@pytest.mark.parametrize("fmt", list(FORMATS))
def test_pipeline_matches_python_twin(bash, logdir, fmt, since):
    name = FORMATS[fmt]
    out = run_pipeline(bash, server_for(logdir, name + "*", fmt), since=since)
    expected = twin_dir(logdir, name, fmt, since=since)
    assert any(x.startswith(b"R\t") for x in expected)
    assert any(x.startswith(b"P\t") for x in expected)
    assert_same(out, expected)


@pytest.mark.parametrize("fmt", list(FORMATS))
def test_pipeline_counts_parse_fail(bash, logdir, fmt):
    out = run_pipeline(bash, server_for(logdir, FORMATS[fmt] + "*", fmt))
    stats = f_lines(out)
    assert len(stats) == 1
    assert int(stats[0][5]) == 2
    assert int(stats[0][4]) == len(_records((FIXTURES / FORMATS[fmt]).read_bytes()))


@pytest.mark.parametrize("fmt", list(FORMATS))
def test_pipeline_since_filters_lines(bash, logdir, fmt):
    since = "20260801"
    out = run_pipeline(bash, server_for(logdir, FORMATS[fmt] + "*", fmt), since=since)
    may_lines = [x for x in _records((FIXTURES / FORMATS[fmt]).read_bytes())
                 if b"/May/2026" in x]
    assert may_lines
    (stat,) = f_lines(out)
    assert int(stat[6]) == len(may_lines)
    assert stat[2] == b"20260520" and stat[3] == b"20261118"  # 파일의 실제 날짜 범위
    for x in out:
        if x[:2] in (b"R\t", b"P\t"):
            assert x.split(b"\t")[-2] >= since.encode()
    # since 없이 돌리면 그 줄들이 R에 들어간다
    full = run_pipeline(bash, server_for(logdir, FORMATS[fmt] + "*", fmt))
    assert any(x.startswith(b"R\t") and x.split(b"\t")[-2] == b"20260520" for x in full)


def test_pipeline_newermt_skips_old_files(bash, logdir):
    import os
    old = logdir / FORMATS["webtob"]
    os.utime(old, (1_600_000_000, 1_600_000_000))  # 2020년
    out = run_pipeline(bash, server_for(logdir, FORMATS["webtob"] + "*", "webtob"),
                       since="20260801")
    assert out == [b"E\tno files matched", b"Q\t0\t0", b"D\tdone"]


def test_pipeline_no_files(bash, tmp_path):
    empty = tmp_path.resolve() / "empty"
    empty.mkdir()
    for d in (empty, empty / "missing"):
        out = run_pipeline(bash, server_for(d, "access.log*", "webtob"))
        assert out == [b"E\tno files matched", b"Q\t0\t0", b"D\tdone"]


@pytest.mark.parametrize("fmt", list(FORMATS))
def test_pipeline_outputs_no_raw_lines(bash, logdir, fmt):
    out = run_pipeline(bash, server_for(logdir, FORMATS[fmt] + "*", fmt))
    for x in out:
        assert x[:2] in TAGS, x
    # F 줄의 파일 경로(tmp 경로)는 빼고 본다
    blob = b"\n".join(b"F" if x.startswith(b"F\t") else x for x in out)
    for secret in (b"token=abc", b"token", b"abc", b"550e8400", b"446655440000",
                   b"778899", b"9981", b"123", b"456", b"%ED", b"Mozilla",
                   b"HTTP/1.1", b"\\x16", b"+0900", b"frank", b"10.0.0.", b"192.168.",
                   b"203.0.113."):
        assert secret not in blob, secret


def test_pipeline_top404_and_rest(bash, tmp_path):
    d = tmp_path.resolve()
    lines = []
    for path, n in (("/a", 4), ("/b", 3), ("/c", 2), ("/d", 1)):
        lines += [f'1.1.1.1 - - [18/Nov/2026:10:00:00 +0900] "GET {path} HTTP/1.1" 404 9'] * n
    lines += ['1.1.1.1 - - [17/Nov/2026:10:00:00 +0900] "GET /mixed HTTP/1.1" 404 9',
              '1.1.1.1 - - [18/Nov/2026:10:00:00 +0900] "GET /mixed HTTP/1.1" 200 9',
              '1.1.1.1 - - [18/Nov/2026:10:00:00 +0900] "POST /a HTTP/1.1" 405 9']
    (d / "access.log").write_bytes(("\n".join(lines) + "\n").encode())
    settings = Settings(top_404=2)
    out = run_pipeline(bash, server_for(d, "access.log*", "webtob"), settings)
    assert [x for x in out if x[:2] in (b"P\t", b"Q\t")] == [
        b"P\tGET\t/a\t4\t20261118\t20261118",
        b"P\tGET\t/b\t3\t20261118\t20261118",
        b"Q\t2\t3",
    ]
    rows = sorted(x for x in out if x.startswith(b"R\t"))
    assert rows == [
        b"R\tGET\t/mixed\t2xx\t1\t20261118\t20261118",
        b"R\tGET\t/mixed\t4xx\t1\t20261117\t20261117",
        b"R\tPOST\t/a\t4xx\t1\t20261118\t20261118",
    ]
    assert_same(out, twin_dir(d, "access.log", "webtob", top404=2))
    zero = run_pipeline(bash, server_for(d, "access.log*", "webtob"), Settings(top_404=0))
    assert not any(x.startswith(b"P\t") for x in zero)
    assert b"Q\t4\t10" in zero


def test_pipeline_truncates_rows(bash, tmp_path):
    d = tmp_path.resolve()
    lines = []
    for path, n in (("/x", 3), ("/y", 2), ("/z", 1)):
        lines += [f'1.1.1.1 - - [18/Nov/2026:10:00:00 +0900] "GET {path} HTTP/1.1" 200 9'] * n
    (d / "access.log").write_bytes(("\n".join(lines) + "\n").encode())
    out = run_pipeline(bash, server_for(d, "access.log*", "webtob"), Settings(max_rows=2))
    assert sorted(x for x in out if x.startswith(b"R\t")) == [
        b"R\tGET\t/x\t2xx\t3\t20261118\t20261118",
        b"R\tGET\t/y\t2xx\t2\t20261118\t20261118",
    ]
    assert out[-3:] == [b"T\t2", b"Q\t0\t0", b"D\tdone"]
    assert_same(out, twin_dir(d, "access.log", "webtob", max_rows=2))
    exact = run_pipeline(bash, server_for(d, "access.log*", "webtob"), Settings(max_rows=3))
    assert not any(x.startswith(b"T\t") for x in exact)


EDGE_PATHS = [
    "/v1/users/123", "/v1/u/550E8400-E29B-41D4-A716-446655440000",
    "/v1/u/550e8400-e29b-41d4-a716-44665544000", "/v1/h/0123456789abcdef",
    "/v1/h/0123456789abcde", "/v1/p/010-1234-5678", "/v1/p/12-345", "/v1/p/12-34",
    "/v1/p/-123456",
    "/v1/m/a@b.com", "/v1/e/%ED%99%8D%EA%B8%B8", "/v1/e/%ED%99%8D",
    "/v1/e/%ED%99%8D%EA%B8%B8ab", "/v1/e/%ED%99%8D%EA%B8%B8a",
    "/v1/검색/%ED%99%8D", "/v1/검색/가나다라마바사", "/v1/t/abcdefghij0123456789",
    "/v1/t/abcdefghijklmnopqrst", "/v1/t/ab_cd-ef_gh-0123456789",
    "/v1/x;jsessionid=ABC123/y", "/v1//double///slash/", "/v1/q?x=1#frag",
    "/v1/f#frag?x", "/", "//", "/;x", "relative/path", "?only", "/v1/a;b;c/d",
    "/v1/\x01ctl", "/v1/tab\x7f", "/v1/fs\x1csep", "/v1/\x1c/{x}",
]


def test_pipeline_edge_paths_and_line_endings(bash, tmp_path):
    """정규화 경계값, CRLF, 마지막 줄 개행 없음, 깨진 UTF-8을 twin과 비교한다."""
    d = tmp_path.resolve()
    lines = [f'1.1.1.1 - - [18/Nov/2026:10:00:00 +0900] "GET {p} HTTP/1.1" 200 9'
             for p in EDGE_PATHS]
    lines += [
        '1.1.1.1 - - [18/Nov/2026:10:00:00 +0900] "  PUT   /sp   HTTP/1.1  "   201 9',
        '1.1.1.1 - - [18/Nov/2026:10:00:00 +0900] "GET /noproto" 304 9',
        '1.1.1.1 - - [18/Nov/2026:10:00:00 +0900] "get /lower HTTP/1.1" 200 9',
        '1.1.1.1 - - [18/Nov/2026:10:00:00 +0900] "GET /esc\\" HTTP/1.1" 200 9',
        '1.1.1.1 - - [18/xyz/2026:10:00:00 +0900] "GET /badmonth HTTP/1.1" 200 9',
        '1.1.1.1 - - [18/Nov/2026:10:00:00 +0900] "GET /st HTTP/1.1" 20 9',
        '1.1.1.1 - - [18/Nov/2026:10:00:00 +0900] "GET /st HTTP/1.1" 2000 9',
        '1.1.1.1 - - [18/Nov/2026:10:00:00 +0900] "GET /zero HTTP/1.1" 099 9',
        '1.1.1.1 - - [18/Nov/2026:10:00:00 +0900] "GET" 200 9',
        '1.1.1.1 - - [18/anF/2026:10:00:00 +0900] "GET /shifted-month HTTP/1.1" 200 9',
        '1.1.1.1 - - [18/nFe/2026:10:00:00 +0900] "GET /shifted-month HTTP/1.1" 200 9',
        '1.1.1.1 - - [18/Nov/2026:10:00:00 +0900] "GET /crlf-last HTTP/1.1" 201',
        '1.1.1.1 - - [18/Nov/2026:10:00:00 +0900] "GET /noquote HTTP/1.1 200 9',
        "",
        "   ",
    ]
    crlf = "\r\n".join(lines).encode("utf-8") + b"\r\n"
    crlf += b'1.1.1.1 - - [19/Nov/2026:10:00:00 +0900] "GET /bad/\xff\xfe/x HTTP/1.1" 200 9'
    (d / "access.log.1").write_bytes(crlf)  # 마지막 줄 개행 없음 -> 다음 파일 표시와 붙는다
    (d / "access.log.2").write_bytes(
        b'1.1.1.1 - - [20/Nov/2026:10:00:00 +0900] "GET /second HTTP/1.1" 500 9\r')
    (d / "access.log.3").write_bytes(b"")
    out = run_pipeline(bash, server_for(d, "access.log*", "webtob"))
    assert_same(out, twin_dir(d, "access.log", "webtob"))
    stats = {Path(x[1].decode()).name: x[2:] for x in f_lines(out)}
    assert stats["access.log.2"] == [b"20261120", b"20261120", b"1", b"0", b"0"]
    assert stats["access.log.3"] == [b"-", b"-", b"0", b"0", b"0"]


@pytest.mark.parametrize("custom,lines", [
    (CustomFormat(method_field=2, path_field=3, status_field=4, time_field=1,
                  time_format="iso8601"),
     ["2026-11-18T10:00:00+09:00 GET /v1/users/12?x=1 200",
      "2026-05-20T10:00:00+09:00\tPOST\t\"/v1/orders\"\t201",
      "2026-11-18 GET \"/v1/q 200",
      "2026-11-18 GET /v1/endq\" 200",
      "2026-11-18 GET \" 200",
      "2026-11-18 GET /v1/w/ 404",
      "2026/11/18 GET /v1/bad 200",
      "2026-11-18 GET /v1/short",
      "2026-11-18 GET /v1/crlf 302\r"]),
    (CustomFormat(method_field=4, path_field=5, status_field=6, time_field=2,
                  time_format="clf"),
     ["10.0.0.1 [18/Nov/2026:10:00:01 +0900] GET /v1/users/123?token=abc 200 5",
      "10.0.0.1 [20/May/2026:10:00:01 +0900] DELETE /v1/orders/9 204 5",
      "10.0.0.1 [20/Mai/2026:10:00:01 +0900] GET /v1/x 200 5",
      "10.0.0.1 x18/Nov/2026x GET /v1/y 200 5",
      "10.0.0.1 [20/May/2026:10:00:01 +0900] - 400 0"]),
])
def test_pipeline_custom_format_matches_twin(bash, tmp_path, custom, lines):
    d = tmp_path.resolve()
    (d / "app.log").write_bytes(("\n".join(lines) + "\n").encode())
    server = server_for(d, "app.log", "custom", custom)
    for since in (None, "20260801"):
        out = run_pipeline(bash, server, since=since)
        expected = twin_dir(d, "app.log", "custom", custom=custom, since=since)
        assert any(x.startswith(b"R\t") for x in expected)
        assert_same(out, expected)


def test_pipeline_runs_under_posix_awk(bash, logdir):
    """gawk --posix로 돌려도 결과가 같다 (gawk 전용 기능을 쓰지 않는다)."""
    version = subprocess.run([bash, "-c", "awk --version"], capture_output=True).stdout
    if b"GNU Awk" not in version:
        pytest.skip("gawk 아님")
    server = server_for(logdir, "*", "webtob")
    script = build_pipeline(server, Settings(top_404=1, max_rows=3), "20260801", TOKEN)
    assert script.count("LC_ALL=C awk ") == 1
    posix = run_script(bash, script.replace("LC_ALL=C awk ", "LC_ALL=C awk --posix "))
    assert posix == run_script(bash, script)


def test_pipeline_hostile_directory_name(bash, tmp_path):
    d = tmp_path.resolve() / "it's $(echo pwned) `x` a;b"
    d.mkdir()
    shutil.copy(FIXTURES / FORMATS["webtob"], d / "access.log")
    out = run_pipeline(bash, server_for(d, "access.log*", "webtob"))
    (stat,) = f_lines(out)
    assert stat[1].endswith(b"/it's $(echo pwned) `x` a;b/access.log")
    assert_same(out, twin_dir(d, "access.log", "webtob"))
    assert not (tmp_path / "pwned").exists()


def test_full_remote_command_runs_locally(bash, logdir):
    """timeout/nice/sh -c 껍데기까지 포함한 명령이 같은 결과를 낸다."""
    probe = subprocess.run([bash, "-c", "command -v timeout && command -v nice && command -v sh"],
                           capture_output=True)
    if probe.returncode != 0:
        pytest.skip("timeout/nice/sh 없음")
    server = server_for(logdir, FORMATS["webtob"] + "*", "webtob")
    cmd = build_remote_command(server, Settings(), None, TOKEN)
    r = bash_c(bash, linux_like(cmd))
    assert r.returncode == 0, r.stderr
    assert r.stdout[:-1].split(b"\n") == run_pipeline(bash, server)


LINE = b'1.1.1.1 - - [18/Nov/2026:10:00:00 +0900] "GET /v1/a HTTP/1.1" 200 9\n'
FMARK = b"\n\x01F" + TOKEN.encode() + b"\t"
EMARK = b"\n\x01E" + TOKEN.encode() + b"\t"


def run_awk(bash, stdin: bytes, mark: str | None = TOKEN, maxrows=10, top404=10) -> bytes:
    """awk 프로그램만 직접 돌린다 (셸 루프가 내는 표시 줄을 손으로 만든다)."""
    from napi.remote.awk_program import awk_program
    args = f"-v SINCE=0 -v MAXROWS={maxrows} -v MAXKEYS={4 * maxrows} -v TOP404={top404}"
    if mark is not None:
        args += f" -v MARK={mark}"
    script = linux_like(f'LC_ALL=C awk {args} "$1"')
    r = subprocess.run([bash, "-c", script, TOKEN, awk_program("webtob", None)],
                       input=stdin, capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr
    return r.stdout


def test_awk_program_markers_sanitizing_and_blank_lines(bash):
    """표시 줄의 제어 문자는 ?로, 표시 앞에 온 줄은 무시, 셸이 넣은 \n 하나만 빼고 빈 줄은 센다."""
    stdin = (LINE + FMARK + b"/logs/a\tb\x1bc\r\x7fd\n" + LINE
             + EMARK + b"decompress_failed\t/logs/a\x01x\n"
             + LINE  # 오류 표시 뒤, 다음 파일 표시 전의 줄은 어느 파일에도 세지 않는다
             + EMARK + b"bogus\t/x\n" + EMARK + b"nocode\n"
             + FMARK + b"/logs/empty\n"
             + FMARK + b"/logs/second\n" + b"junk\n\n\n"
             + FMARK + b"/logs/third\n" + LINE[:-1])
    assert run_awk(bash, stdin).split(b"\n") == [
        b"E\tdecompress_failed\t/logs/a?x",
        b"F\t/logs/a?b?c??d\t20261118\t20261118\t1\t0\t0",
        b"F\t/logs/empty\t-\t-\t0\t0\t0",
        b"F\t/logs/second\t-\t-\t3\t3\t0",
        b"F\t/logs/third\t20261118\t20261118\t1\t0\t0",
        b"R\tGET\t/v1/a\t2xx\t2\t20261118\t20261118",
        b"Q\t0\t0",
        b"D\tdone",
        b"",
    ]


def test_awk_program_without_mark_refuses(bash):
    """MARK 없이 돌리면 표시를 믿을 수 없으므로 D 없이 오류만 낸다."""
    assert run_awk(bash, FMARK + LINE, mark=None) == b"E\tbad marker\n"


def test_awk_program_ignores_forged_markers(bash):
    """토큰이 없거나 틀린 표시, 줄 중간의 표시는 그냥 로그 줄이다 (원문이 F로 새지 않는다)."""
    secret = b"/SECRET?token=abc&id=778899 HTTP/1.1\" 200 9 \"-\" \"Mozilla\""
    stdin = (FMARK + b"/logs/real\n"
             + b"\x01F\t" + secret + b"\n"
             + b"\x01Fnapi-scan-deadbeef\t" + secret + b"\n"
             + b"\x01E\tunreadable\t" + secret + b"\n"
             + b"x\x01F" + TOKEN.encode() + b"\t" + secret + b"\n"
             + LINE)
    out = run_awk(bash, stdin)
    assert out.split(b"\n") == [
        b"F\t/logs/real\t20261118\t20261118\t5\t4\t0",
        b"R\tGET\t/v1/a\t2xx\t1\t20261118\t20261118",
        b"Q\t0\t0",
        b"D\tdone",
        b"",
    ]


def test_pipeline_forged_marker_in_log_is_not_a_file(bash, tmp_path):
    d = tmp_path.resolve()
    head = b'1.1.1.1 - - [18/Nov/2026:10:00:00 +0900] '
    secret = b'/SECRET?token=abc&id=778899 HTTP/1.1" 200 9 "-" "Mozilla"'
    lines = [
        head + b'"GET /v1/x?x=\x01F\t' + secret,
        head + b'"GET /v1/y?x=\x01Fnapi-scan-deadbeef\t' + secret,
        b"\x01F\t" + secret,
        b"\x01Fnapi-scan-deadbeef\t" + secret,
        b"\x01Enapi-scan-deadbeef\tunreadable\t" + secret,
        head + b'"GET /v1/users/me HTTP/1.1" 200 9',
    ]
    (d / "access.log").write_bytes(b"\n".join(lines) + b"\n")
    shutil.copy(FIXTURES / FORMATS["webtob"], d / "access.log.1")
    out = run_pipeline(bash, server_for(d, "access.log*", "webtob"))
    stats = {Path(x[1].decode()).name: x[2:] for x in f_lines(out)}
    assert sorted(stats) == ["access.log", "access.log.1"]
    assert stats["access.log"] == [b"20261118", b"20261118", b"6", b"3", b"0"]
    assert stats["access.log.1"][2] == b"%d" % len(
        _records((FIXTURES / FORMATS["webtob"]).read_bytes()))
    blob = b"\n".join(b"F" if x.startswith(b"F\t") else x for x in out)
    for leaked in (b"SECRET", b"token", b"778899", b"Mozilla", b"deadbeef"):
        assert leaked not in blob, leaked
    assert not any(x.startswith(b"E\t") for x in out)
    assert_same(out, twin_dir(d, "access.log", "webtob"))


def test_pipeline_decompress_failed(bash, tmp_path):
    d = tmp_path.resolve()
    data = (FIXTURES / FORMATS["webtob"]).read_bytes()
    (d / "access.log").write_bytes(data)
    gz = gzip.compress(data)
    (d / "access.log.1.gz").write_bytes(gz[: len(gz) // 2])
    out = run_pipeline(bash, server_for(d, "access.log*", "webtob"))
    errors = [x for x in out if x.startswith(b"E\t")]
    assert len(errors) == 1
    assert errors[0].startswith(b"E\tdecompress_failed\t")
    assert errors[0].endswith(b"/access.log.1.gz")
    stats = {Path(x[1].decode()).name: x[2:] for x in f_lines(out)}
    assert sorted(stats) == ["access.log", "access.log.1.gz"]
    expected = twin([(posix(d) + "/access.log", data)], "webtob")
    # 멀쩡한 파일의 통계는 그대로다
    assert [x for x in expected if x.startswith(b"F\t")] == \
        [x for x in out if x.startswith(b"F\t") and x.split(b"\t")[1].endswith(b"/access.log")]
    assert out[-1] == b"D\tdone"


def test_pipeline_unreadable_file_branch(bash, tmp_path):
    """find가 내준 파일을 읽을 수 없으면 E unreadable을 내고 건너뛴다.
    (Windows에서는 chmod 000을 만들 수 없어 find를 셸 함수로 바꿔 없는 파일을 내게 한다.)"""
    d = tmp_path.resolve()
    shutil.copy(FIXTURES / FORMATS["webtob"], d / "access.log")
    real = posix(d) + "/access.log"
    script = build_pipeline(server_for(d, "access.log*", "webtob"), Settings(), None, TOKEN)
    fake_find = "find() { printf '%s\\n' /nonexistent/napi/access.log.9 " + shlex.quote(real) + "; }; "
    out = run_script(bash, fake_find + script)
    assert [x for x in out if x.startswith(b"E\t")] == [
        b"E\tunreadable\t/nonexistent/napi/access.log.9"]
    assert [x[1] for x in f_lines(out)] == [real.encode()]
    assert_same([x for x in out if not x.startswith(b"E\t")], twin_dir(d, "access.log", "webtob"))
    # 읽을 수 없는 파일만 있으면 "no files matched"가 아니다 (파일은 있었다)
    only = "find() { printf '%s\\n' /nonexistent/napi/access.log.9; }; "
    assert run_script(bash, only + script) == [
        b"E\tunreadable\t/nonexistent/napi/access.log.9", b"Q\t0\t0", b"D\tdone"]


@pytest.mark.skipif(sys.platform == "win32" or not hasattr(os, "geteuid") or os.geteuid() == 0,
                    reason="권한 없는 파일은 root가 아닌 Linux에서만 만들 수 있다")
def test_pipeline_unreadable_file_real(bash, tmp_path):
    d = tmp_path.resolve()
    f = d / "access.log"
    shutil.copy(FIXTURES / FORMATS["webtob"], f)
    f.chmod(0)
    try:
        out = run_pipeline(bash, server_for(d, "access.log*", "webtob"))
    finally:
        f.chmod(0o644)
    assert out == [b"E\tunreadable\t" + posix(f).encode(), b"Q\t0\t0", b"D\tdone"]


def test_pipeline_key_cap_truncates(bash, tmp_path):
    """키가 MAXKEYS(=4*max_rows)에 닿으면 새 키는 버리고 T를 낸다. 있던 키는 계속 센다."""
    d = tmp_path.resolve()

    def ln(path, status):
        return f'1.1.1.1 - - [18/Nov/2026:10:00:00 +0900] "GET {path} HTTP/1.1" {status} 9'
    lines = [ln("/r", 200)] + [ln(f"/p{i}", 404) for i in range(1, 10)]
    lines += [ln("/r", 200), ln("/p1", 404), ln("/late", 200)]
    (d / "access.log").write_bytes(("\n".join(lines) + "\n").encode())
    out = run_pipeline(bash, server_for(d, "access.log*", "webtob"), Settings(max_rows=2))
    assert [x for x in out if x[:2] in (b"R\t", b"T\t")] == [
        b"R\tGET\t/r\t2xx\t2\t20261118\t20261118", b"T\t2"]
    probes = [x for x in out if x.startswith(b"P\t")]
    assert probes == [b"P\tGET\t/p1\t2\t20261118\t20261118"] + [
        b"P\tGET\t/p%d\t1\t20261118\t20261118" % i for i in range(2, 8)]
    assert out[-2:] == [b"Q\t0\t0", b"D\tdone"]
    (stat,) = f_lines(out)
    assert stat[4:] == [b"13", b"0", b"0"]


# ---- Task 2: FormatSpec에서 생성한 awk parse() ----

def parity(bash, server, twin_files, fmt, formats=(), since=None, posix=True,
           cwd=None) -> list[bytes]:
    """awk 출력이 Python twin과 바이트 단위로 같은지 본다. posix=True면 gawk --posix도."""
    out = run_pipeline(bash, server, None, since, formats, cwd)
    assert_same(out, twin(twin_files, fmt, since=since, formats=formats))
    if posix and is_gawk(bash):
        assert run_pipeline_posix(bash, server, None, since, formats, cwd) == out
    return out


def write_log(d: Path, name: str, data: bytes) -> list[tuple[str, bytes]]:
    (d / name).write_bytes(data)
    return [(posix(d) + "/" + name, data)]


@pytest.mark.parametrize("since", [None, "20260801"])
@pytest.mark.parametrize("fmt", list(BUILTIN_FIXTURES))
def test_pipeline_parity_builtin_formats(bash, logdir, fmt, since):
    name = BUILTIN_FIXTURES[fmt]
    server = server_for(logdir, name + "*", fmt)
    out = run_pipeline(bash, server, since=since)
    expected = twin_dir(logdir, name, fmt, since=since)
    assert any(x.startswith(b"R\t") for x in expected)
    assert any(x.startswith(b"P\t") for x in expected)
    assert_same(out, expected)
    if is_gawk(bash):
        assert run_pipeline_posix(bash, server, since=since) == out


@pytest.mark.parametrize("since", [None, "20260801"])
@pytest.mark.parametrize("fmt", list(USER_FIXTURES))
def test_pipeline_parity_user_formats(bash, tmp_path, fmt, since):
    d = tmp_path.resolve()
    data = (FIXTURES / USER_FIXTURES[fmt]).read_bytes()
    files = write_log(d, "app.log", data)
    out = parity(bash, server_for(d, "app.log", fmt), files, fmt, USER_FORMATS, since)
    (stat,) = f_lines(out)
    assert int(stat[4]) == len(_records(data)) >= 10
    assert int(stat[5]) >= 2  # 깨진 줄
    assert stat[2] == b"20260520" and stat[3] == b"20261118"
    if since is not None:
        assert int(stat[6]) == 2  # 5월 줄
    rows = [x for x in out if x.startswith(b"R\t")]
    assert len(rows) >= 5 and any(x.startswith(b"P\t") for x in out)
    assert any(x.startswith(b"R\tGET\t/v1/users/{*}\t2xx\t") for x in rows)
    blob = b"\n".join(b"F" if x.startswith(b"F\t") else x for x in out)
    for secret in (b"token", b"abc", b"550e8400", b"9981", b"778899", b"10.0.0.",
                   b"CLIENT", b"HTTP/1.1", b"%ED", b"10:00", b"Nov", b"2026-"):
        assert secret not in blob, secret


def test_pipeline_no_status_format(bash, tmp_path):
    d = tmp_path.resolve()
    fmts = [LogFormatDef("ns", '%t "%r"')]
    data = (b'[18/Nov/2026:10:00:00 +0900] "GET /v1/users/1 HTTP/1.1"\n'
            b'[18/Nov/2026:10:00:01 +0900] "GET /v1/users/2 HTTP/1.1" 404 trailing\n'
            b'[17/Nov/2026:10:00:01 +0900] "POST /v1/orders HTTP/1.1"\r\n'
            b'[17/Nov/2026:10:00:01 +0900] "post /v1/orders HTTP/1.1"\n'
            b'garbage\n')
    files = write_log(d, "app.log", data)
    # CR이 든 입력은 --posix(BINMODE 없음)로 Windows에서 비교할 수 없어 기본 모드만 본다
    out = parity(bash, server_for(d, "app.log", "ns"), files, "ns", fmts, posix=False)
    assert sorted(x for x in out if x.startswith(b"R\t")) == [
        b"R\tGET\t/v1/users/{*}\t???\t2\t20261118\t20261118",
        b"R\tPOST\t/v1/orders\t???\t1\t20261117\t20261117",
    ]
    assert not any(x.startswith(b"P\t") for x in out)
    assert out[-2:] == [b"Q\t0\t0", b"D\tdone"]


# 리터럴에 " \ } ' 제어 문자, 한글, system("...")이 든 패턴 (필수 조건은 충족)
INJECT_TAIL = '\\"};system("touch pwned");{"\\ \'x\' 한'
INJECT_A = '%t "%r" ' + INJECT_TAIL + ' %s %{a"b\\c}i'
# 리터럴의 개행으로 awk 문장을 끊으려는 패턴 (R7: 제어 문자라 컴파일에서 거부된다)
INJECT_B = '%t "%r"\n}\nBEGIN { system("touch pwned2") }\n# %s'


def _strip_awk_strings(prog: str) -> str:
    return re.sub(r'"(?:[^"\\\n]|\\.)*"', '""', prog)


def test_pattern_cannot_inject_awk(bash, tmp_path):
    d = tmp_path.resolve()
    fmts = [LogFormatDef("inj", INJECT_A), LogFormatDef("inj2", INJECT_B)]
    prog = awk_program("inj", None, fmts)
    start = prog.index("function parse(")
    parse_fn = prog[start:prog.index("\n}\n", start)]
    assert parse_fn.isascii()  # 한글은 \ooo로 들어간다 (제어 문자는 _awk_str 테스트)
    assert "\\355\\225\\234" in parse_fn
    code = _strip_awk_strings(parse_fn)  # 문자열 리터럴을 지우면 패턴 글자는 남지 않는다
    for word in ("system", "pwned", "touch", "BEGIN", "a\"b", "#", "}\n"):
        assert word not in code, word
    whole = _strip_awk_strings(prog)
    assert "system" not in whole and whole.count("BEGIN") == 1
    with pytest.raises(FormatError, match="^형식에 쓸 수 없는 문자가 있어요$"):
        awk_program("inj2", None, fmts)

    lines = [
        '[18/Nov/2026:10:00:01 +0900] "GET /v1/a HTTP/1.1" ' + INJECT_TAIL + ' 200 z',
        '[18/Nov/2026:10:00:02 +0900] "GET /v1/a HTTP/1.1"  ' + INJECT_TAIL + '\t404',
        '[18/Nov/2026:10:00:03 +0900] "GET /v1/b HTTP/1.1" ' + INJECT_TAIL + ' 503 "x"',
        '[18/Nov/2026:10:00:04 +0900] "GET /v1/c HTTP/1.1" '
        + INJECT_TAIL.replace("한", "x") + ' 200',
        '[18/Nov/2026:10:00:05 +0900] "GET /v1/d HTTP/1.1" '
        + INJECT_TAIL.replace("\\", "/") + ' 200',
        '[18/Nov/2026:10:00:06 +0900] "GET /v1/e HTTP/1.1"',
    ]
    files = write_log(d, "app.log", ("\n".join(lines) + "\n").encode("utf-8"))
    out = parity(bash, server_for(d, "app.log", "inj"), files, "inj", fmts, cwd=d)
    assert sorted(x for x in out if x.startswith(b"R\t")) == [
        b"R\tGET\t/v1/a\t2xx\t1\t20261118\t20261118",
        b"R\tGET\t/v1/a\t4xx\t1\t20261118\t20261118",
        b"R\tGET\t/v1/b\t5xx\t1\t20261118\t20261118",
    ]
    assert not list(d.glob("pwned*"))
    assert not list(Path.cwd().glob("pwned*"))


EDGE_FORMATS = [
    # 빈 값 허용(lit 앞), 다음 lit 없음, %b 대소문자, 마지막 %s 뒤 꼬리
    ("%{%d/%b/%Y}t|%m|%U|%s", [
        "18/Nov/2026|GET|/a|200", "18/Nov/2026|GET||201", "18/nov/2026|GET|/a|200",
        "18/Nov/2026|GET|/a|200|x", "18/Nov/2026|GET|/a", "18/Nov/2026|GET|/a|",
        "18/Nov/2026||/a|200", "18/Nov/2026|GE T|/a|200", "1/Nov/2026|GET|/a|200",
        "18/Nov/2026|GET|/a|404"]),
    # 같은 날짜 항목 반복(나중 것이 이김), 레이아웃 리터럴과 H는 비교하지 않음
    ('%{%m %b %d %Y %H}t "%r"', [
        '11 Dec 05 2026 10 "GET /a HTTP/1.1"', '1x Dec 05 2026 10 "GET /a HTTP/1.1"',
        '11XDecX05X2026Xzz "GET /b HTTP/1.1"', '11 Dex 05 2026 10 "GET /a HTTP/1.1"',
        '11 Dec 05 2026 10"GET /a HTTP/1.1"', '11 Dec 05 2026 1']),
    # 멀티바이트 레이아웃과 리터럴
    ('%{%Y년%m월%d일}t "%r" %s', [
        '2026년11월18일 "GET /a HTTP/1.1" 200', '2026년11월18일  "GET /a HTTP/1.1"\t302',
        '2026-11-18 "GET /a HTTP/1.1" 200', '2026년11월18 "GET /a HTTP/1.1" 200']),
    # tfmt 폭이 글자 중간에서 끝남: 뒤 토큰이 있으면 실패
    ('%{%Y-%m-%dX}t "%r"', ['2026-11-18가 "GET /a HTTP/1.1"', '2026-11-18X"GET /a b"',
                            '2026-11-18X "GET /a b"']),
    # ... 마지막 필수 토큰이면 성공
    ('"%r" %{%Y-%m-%dX}t', ['"GET /a HTTP/1.1" 2026-11-18가', '"GET /a b" 2026-11-18',
                            '"GET /a b" 2026-11-1', '"GET /c b"\t2026-11-18Xtrailing',
                            '"GET /d b" 2026-11-18X']),  # 남은 바이트 = 폭
    ('%{%Y-%m-%d}t "%r"', ['2026-11-18 "GET /a b"', '2026-11-1', '2026-11-18',
                           'x2026-11-18 "GET /a b"']),
    # ws로 시작하는 패턴
    (' %t "%r"', [' [18/Nov/2026:10:00:00 +0900] "GET /a HTTP/1.1"',
                  '[18/Nov/2026:10:00:00 +0900] "GET /a HTTP/1.1"',
                  '\t \t[18/Nov/2026:10:00:00 +0900] "GET /b x"']),
    # %r이 마지막 토큰이면 줄 끝까지
    ('%t %s %r', ['[18/Nov/2026:10:00:01 +0900] 200 GET /a HTTP/1.1',
                  '[18/Nov/2026:10:00:01 +0900] 200   PUT   /b  ',
                  '[18/Nov/2026:10:00:01 +0900] 200 GET',
                  '[18/Nov/2026:10:00:01 +0900]  404\tGET /c',
                  '[18/Nov/2026:10:00:01 +0900] 200']),
    # 한글 리터럴 + %m %U
    ('%t 요청=%m 경로=%U 상태=%s', [
        '[18/Nov/2026:10:00:00 +0900] 요청=GET 경로=/v1/검색 상태=200',
        '[18/Nov/2026:10:00:00 +0900]\t요청=POST  경로=/v1/x\t상태=201',
        '[18/Nov/2026:10:00:00 +0900] 요청:GET 경로=/v1/검색 상태=200',
        '[18/Nov/2026:10:00:00 +0900] 요청=GET 경로= 상태=200']),
    # %t 경계: ] 위치, 짧은 값
    ('%t "%r"', ['[18/Nov/2026] "GET /a HTTP/1.1"', '[18/Nov]/2026:1 "GET /a HTTP/1.1"',
                 '[18/Nov/202] "GET /a HTTP/1.1"', '[18/Nov/2026:10:00:00 +0900 "GET /a b"',
                 '18/Nov/2026:10:00:00 +0900] "GET /a b"', '[18/Abc/2026:1] "GET /a b"',
                 '[18/Nov/2026:10:00:00 +0900]"GET /a b"']),
    # ws 앞 필드: 줄 끝이면 빈 값이라 실패
    ('%t "%r" %s %b', ['[18/Nov/2026:10:00:00 +0900] "GET /a b" ',
                       '[18/Nov/2026:10:00:00 +0900] "GET /a b" 200',
                       '[18/Nov/2026:10:00:00 +0900] "GET /a b" 200 ',
                       '[18/Nov/2026:10:00:00 +0900] "GET /a b"200 1']),
    # 리터럴의 역슬래시와 따옴표, 퍼센트
    ('%t \\"%r\\" 100%% %>s', ['[18/Nov/2026:10:00:00 +0900] \\"GET /a b\\" 100% 200',
                              '[18/Nov/2026:10:00:00 +0900] "GET /a b" 100% 200',
                              '[18/Nov/2026:10:00:00 +0900] \\"GET /a b\\" 100 200']),
    # %<s, 반복되는 %r (나중 것이 이김)
    ('%t "%r" "%r" %<s', ['[18/Nov/2026:10:00:00 +0900] "GET /a b" "POST /c d" 201',
                          '[18/Nov/2026:10:00:00 +0900] "GET /a b" "post /c d" 201']),
]


@pytest.mark.parametrize("pattern,lines", EDGE_FORMATS)
def test_pipeline_parity_edge_formats(bash, tmp_path, pattern, lines):
    d = tmp_path.resolve()
    fmts = [LogFormatDef("edge", pattern)]
    files = write_log(d, "app.log", ("\n".join(lines) + "\n").encode("utf-8"))
    out = parity(bash, server_for(d, "app.log", "edge"), files, "edge", fmts)
    (stat,) = f_lines(out)
    assert 0 < int(stat[5]) < len(lines)  # 성공과 실패가 모두 있다


def test_pipeline_parity_raw_bytes_and_crlf(bash, tmp_path):
    """깨진 UTF-8 바이트, 그 바이트를 지나가는 리터럴 검색, CRLF, 개행 없는 끝 줄."""
    d = tmp_path.resolve()
    fmts = [LogFormatDef("raw", '%t [%r]가 %s'), LogFormatDef("crlf", '%t "%r" %s')]
    data = (b'[18/Nov/2026:10:00:00 +0900] [GET /a\xff\xfe x]\xea\xb0]\xea\xb0\x80 200\n'
            b'[18/Nov/2026:10:00:00 +0900] [GET /b x]\xea\xb0\x80 404\r\n'
            b'[18/Nov/2026:10:00:00 +0900] [GET /c x]\xea\xb0 200\n'
            b'[18/Nov/2026:10:00:00 +0900] [GET /d\x80 x]\xea\xb0\x80 200\r\r\n'
            b'[18/Nov/2026:10:00:00 +0900] "GET /e x" 200\r\n'
            b'[18/Nov/2026:10:00:00 +0900] [GET /f x]\xea\xb0\x80 201')
    files = write_log(d, "app.log", data)
    for name in ("raw", "crlf"):
        out = parity(bash, server_for(d, "app.log", name), files, name, fmts, posix=False)
        assert any(x.startswith(b"R\t") for x in out)


# ---- Task 3: 로그 경로 여러 개 ----

def test_pipeline_multiple_globs(bash, tmp_path):
    """두 디렉터리의 glob 결과를 합쳐 집계한다. 두 glob에 걸린 파일은 한 번만 읽는다."""
    root = tmp_path.resolve()
    a, b = root / "a", root / "b"
    a.mkdir()
    b.mkdir()
    web = (FIXTURES / FORMATS["webtob"]).read_bytes()
    shutil.copy(FIXTURES / FORMATS["webtob"], a / "access.log")
    (b / "access.log.1.gz").write_bytes(gzip.compress(web[: len(web) // 2]))
    (b / "other.log").write_bytes(web)  # 어느 glob에도 걸리지 않는다
    globs = [posix(a) + "/access.log*", posix(b) + "/access.log*", posix(a) + "/access.*"]
    for since in (None, "20260801"):
        out = run_pipeline(bash, server_globs(globs, "webtob"), since=since)
        expected = twin([(posix(a) + "/access.log", web),
                         (posix(b) + "/access.log.1.gz", gzip.decompress(
                             (b / "access.log.1.gz").read_bytes()))], "webtob", since=since)
        assert any(x.startswith(b"R\t") for x in expected)
        assert_same(out, expected)
        assert [x[1] for x in f_lines(out)].count((posix(a) + "/access.log").encode()) == 1
        assert not any(x.startswith(b"E\t") for x in out)
    # gawk --posix로도 같다
    if is_gawk(bash):
        assert run_pipeline_posix(bash, server_globs(globs, "webtob")) == \
            run_pipeline(bash, server_globs(globs, "webtob"))


def test_pipeline_glob_empty(bash, tmp_path):
    root = tmp_path.resolve()
    shutil.copy(FIXTURES / FORMATS["webtob"], root / "access.log")
    full = posix(root) + "/access.log*"
    empty = posix(root) + "/missing*.log"
    gone = posix(root) + "/nodir/x*"
    out = run_pipeline(bash, server_globs([full, empty, gone], "webtob"))
    errors = [x for x in out if x.startswith(b"E\t")]
    assert errors == [b"E\tglob_empty\t" + empty.encode(), b"E\tglob_empty\t" + gone.encode()]
    assert_same([x for x in out if not x.startswith(b"E\t")], twin_dir(root, "access.log", "webtob"))
    assert any(x.startswith(b"R\t") for x in out)
    assert out[-1] == b"D\tdone"
    # 모두 비면 v1처럼 "no files matched"만 낸다 (glob_empty 없음)
    for globs in ([empty, gone], [empty]):
        assert run_pipeline(bash, server_globs(globs, "webtob")) == [
            b"E\tno files matched", b"Q\t0\t0", b"D\tdone"]
    if is_gawk(bash):
        assert run_pipeline_posix(bash, server_globs([full, empty], "webtob")) == \
            run_pipeline(bash, server_globs([full, empty], "webtob"))


def test_pipeline_glob_empty_cleans_control_chars(bash, tmp_path):
    """glob에 든 제어 문자는 다른 경로처럼 ?로 바뀌고, 개행으로 줄을 위조할 수 없다."""
    root = tmp_path.resolve()
    shutil.copy(FIXTURES / FORMATS["webtob"], root / "access.log")
    full = posix(root) + "/access.log*"
    weird = posix(root) + "/a\tb\x1bc*"
    forged = posix(root) + "/x\nR\tGET\t/forged\t2xx\t9\t20260101\t20260101\ny*"
    out = run_pipeline(bash, server_globs([full, weird, forged], "webtob"))
    assert [x for x in out if x.startswith(b"E\t")] == [
        b"E\tglob_empty\t" + (posix(root) + "/a?b?c*").encode(),
        b"E\tglob_empty\t" + (posix(root) + "/x").encode()]
    assert not any(b"forged" in x for x in out)
    assert_same([x for x in out if not x.startswith(b"E\t")], twin_dir(root, "access.log", "webtob"))


def test_awk_glob_empty_markers(bash):
    """awk: glob_empty는 파일로 세지 않는다. 파일(읽지 못한 것 포함)이 하나도 없으면
    "no files matched"만 내고 glob_empty는 내지 않는다."""
    ge = EMARK + b"glob_empty\t/x/b*\n"
    assert run_awk(bash, ge + EMARK + b"glob_empty\t/y/c*\n").split(b"\n") == [
        b"E\tno files matched", b"Q\t0\t0", b"D\tdone", b""]
    assert run_awk(bash, ge + EMARK + b"unreadable\t/x/a.log\n").split(b"\n") == [
        b"E\tglob_empty\t/x/b*", b"E\tunreadable\t/x/a.log", b"Q\t0\t0", b"D\tdone", b""]
    assert run_awk(bash, ge + FMARK + b"/x/a.log\n" + LINE).split(b"\n") == [
        b"E\tglob_empty\t/x/b*",
        b"F\t/x/a.log\t20261118\t20261118\t1\t0\t0",
        b"R\tGET\t/v1/a\t2xx\t1\t20261118\t20261118",
        b"Q\t0\t0", b"D\tdone", b""]
