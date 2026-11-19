"""원격 파이프라인(find | gzip | awk)을 로컬 bash로 돌려 Python twin과 비교한다.

출력은 항상 bytes로 다룬다 (줄바꿈 변환 없음).
"""
import gzip
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from napi.logformat import normalize_path, parse_line
from napi.models import CustomFormat, ServerConfig, Settings
from napi.remote.command import build_pipeline, build_remote_command

FIXTURES = Path(__file__).parent.parent / "fixtures" / "logs"
FORMATS = {
    "webtob": "webtob_access.log",
    "nginx_combined": "nginx_access.log",
    "apache_common": "apache_common.log",
}
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


def server_for(directory: Path, pattern: str, fmt: str, custom=None) -> ServerConfig:
    return ServerConfig(name="t", host="h", user="u",
                        log_glob=directory.as_posix() + "/" + pattern,
                        log_format=fmt, custom_format=custom)


def linux_like(script: str) -> str:
    """Git Bash(MSYS)의 gawk는 입력을 텍스트 모드로 열어 CR을 지운다. Linux gawk처럼
    바이트 그대로 읽도록 테스트에서만 BINMODE=3을 준다 (Linux gawk에는 영향 없음)."""
    if sys.platform == "win32" and "LC_ALL=C awk --posix " not in script:
        assert script.count("LC_ALL=C awk ") == 1
        script = script.replace("LC_ALL=C awk ", "LC_ALL=C awk -v BINMODE=3 ")
    return script


def run_script(bash, script: str) -> list[bytes]:
    r = subprocess.run([bash, "-c", linux_like(script), TOKEN], capture_output=True,
                       timeout=120)
    assert r.returncode == 0, r.stderr
    assert r.stdout.endswith(b"\n")
    return r.stdout[:-1].split(b"\n")


def run_pipeline(bash, server, settings=None, since=None) -> list[bytes]:
    return run_script(bash, build_pipeline(server, settings or Settings(), since))


# ---- Python twin: parse_line + normalize_path + 같은 키 + 404 전용 분리 + since ----

def _records(data: bytes) -> list[bytes]:
    recs = data.split(b"\n")
    if recs[-1] == b"":
        recs.pop()
    return recs


def _b(s: str) -> bytes:
    return s.encode("utf-8", "surrogateescape")


def twin(files: list[tuple[str, bytes]], fmt, custom=None, since=None,
         max_rows=50000, top404=200) -> list[bytes]:
    out = []
    agg = {}
    non404 = set()
    for path, data in sorted(files):
        recs = _records(data)
        pf = oor = 0
        dates = []
        for rec in recs:
            p = parse_line(rec.decode("utf-8", "surrogateescape"), fmt, custom)
            if p is None:
                pf += 1
                continue
            dates.append(p.date)
            if since is not None and p.date < since:
                oor += 1
                continue
            status = f"{p.status:03d}"
            mp = (_b(p.method), _b(normalize_path(p.path)))
            key = mp + (_b(status[0] + "xx"),)
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
            files.append((directory.as_posix() + "/" + f.name, data))
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
    "/v1/\x01ctl", "/v1/tab\x7f",
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
    script = build_pipeline(server, Settings(top_404=1, max_rows=3), "20260801")
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
    r = subprocess.run([bash, "-c", linux_like(cmd)], capture_output=True, timeout=120)
    assert r.returncode == 0, r.stderr
    assert r.stdout[:-1].split(b"\n") == run_pipeline(bash, server)


def test_awk_program_sanitizes_marker_path_and_ignores_unmarked_input(bash):
    """파일 표시 줄의 제어 문자는 ?로 바뀌고, 표시 앞에 온 줄은 어디에도 세지 않는다.
    (Windows 파일 이름에는 제어 문자를 넣을 수 없어 awk에 직접 입력을 준다.)"""
    from napi.remote.awk_program import awk_program
    line = b'1.1.1.1 - - [18/Nov/2026:10:00:00 +0900] "GET /v1/a HTTP/1.1" 200 9\n'
    stdin = (line + b"\x01F\t/logs/a\tb\x1bc\r\x7fd\n" + line +
             b"\x01F\t/logs/second\n" + b"junk\n")
    script = linux_like('LC_ALL=C awk -v SINCE=0 -v MAXROWS=10 -v TOP404=10 "$1"')
    r = subprocess.run([bash, "-c", script, TOKEN, awk_program("webtob", None)],
                       input=stdin, capture_output=True, timeout=60)
    assert r.returncode == 0, r.stderr
    assert r.stdout.split(b"\n") == [
        b"F\t/logs/a?b?c??d\t20261118\t20261118\t1\t0\t0",
        b"F\t/logs/second\t-\t-\t1\t1\t0",
        b"R\tGET\t/v1/a\t2xx\t1\t20261118\t20261118",
        b"Q\t0\t0",
        b"D\tdone",
        b"",
    ]
