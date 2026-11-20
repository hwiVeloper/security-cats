from napi.remote.protocol import parse_remote_output

FULL = "\n".join([
    "F\t/var/log/a.log\t20250101\t20250301\t100\t2\t5",
    "F\t/var/log/b.log\t-\t-\t0\t0\t0",
    "R\tGET\t/v1/a\t2xx\t10\t20250101\t20250301",
    "R\tGET\t/v1/a\t5xx\t1\t20250102\t20250102",
    "R\tPOST\t/v1/b\t2xx\t3\t20250110\t20250120",
    "P\tGET\t/probe\t8\t20250201\t20250210",
    "Q\t4\t9",
    "D\tdone",
]) + "\n"


def test_parse_full():
    r = parse_remote_output("s1", FULL, "", 0, 1.5)
    assert r.ok and r.error is None and r.server == "s1" and r.elapsed_sec == 1.5
    assert len(r.files) == 2 and len(r.rows) == 3 and len(r.probe404) == 1
    assert r.files[0].path == "/var/log/a.log"
    assert (r.files[0].lines, r.files[0].parse_fail, r.files[0].out_of_range) == (100, 2, 5)
    assert r.files[1].min_date is None and r.files[1].max_date is None
    assert r.rows[1].status_class == "5xx" and r.rows[1].count == 1
    p = r.probe404[0]
    assert (p.method, p.path, p.status_class, p.count, p.first, p.last) == (
        "GET", "/probe", "4xx", 8, "20250201", "20250210")
    assert r.probe404_rest == (4, 9)
    assert r.oldest_date == "20250101" and r.newest_date == "20250301"
    assert not r.truncated and r.file_errors == []


def test_truncated_flag():
    r = parse_remote_output("s", "T\t50000\nD\tdone\n", "", 0, 0.0)
    assert r.ok and r.truncated


def test_missing_done_is_failure():
    r = parse_remote_output("s", "R\tGET\t/a\t2xx\t1\t20250101\t20250101\n", "", 0, 0.0)
    assert not r.ok and "집계가 끝까지 실행되지 않았어요" in r.error
    assert len(r.rows) == 1


def test_timeout_exit_124():
    r = parse_remote_output("s", FULL, "", 124, 0.0)
    assert not r.ok and "시간 초과" in r.error


def test_stderr_appended_only_when_failed_and_nonempty():
    r = parse_remote_output("s", "", "x" * 300, 1, 0.0)
    assert r.error.endswith(" — " + "x" * 200)
    r = parse_remote_output("s", "", "", 1, 0.0)
    assert " — " not in r.error
    r = parse_remote_output("s", "D\tdone\n", "warn", 0, 0.0)
    assert r.ok and r.error is None


def test_no_files():
    r = parse_remote_output("s", "E\tno files matched\nQ\t0\t0\nD\tdone\n", "", 0, 0.0)
    assert r.ok and r.rows == [] and r.files == [] and r.file_errors == []


def test_file_errors():
    out = "E\tunreadable\t/a.log\nE\tdecompress_failed\t/b.gz\nE\tweird\nD\tdone\n"
    r = parse_remote_output("s", out, "", 0, 0.0)
    assert r.ok
    assert r.file_errors == [("unreadable", "/a.log"), ("decompress_failed", "/b.gz"),
                             ("weird", "")]


def test_bad_marker():
    r = parse_remote_output("s", "E\tbad marker\n", "", 0, 0.0)
    assert not r.ok and "집계 프로그램 호출이 잘못됐어요" in r.error


def test_unknown_tag_ignored_but_never_kept():
    out = "X\tSECRET-LOG-LINE\nsecret raw line\nD\tdone\n"
    r = parse_remote_output("s", out, "", 0, 0.0)
    assert r.ok
    assert "SECRET" not in repr(r) and "secret" not in repr(r)


def test_malformed_lines_do_not_crash():
    for bad in ("R\tGET\t/a\t2xx\tabc\t1\t2", "R\tGET\t/a", "F\tp\t-\t-\t1\t2",
                "P\tGET\t/a\tx\t1\t2", "Q\t1", "Q\ta\tb"):
        r = parse_remote_output("s", bad + "\nD\tdone\n", "", 0, 0.0)
        assert not r.ok and "집계 결과 형식이 올바르지 않아요" in r.error


def test_split_on_newline_only_and_crlf_free():
    # U+2028 / \r inside a path must not split lines
    out = "F\t/a\u2028b\r.log\t20250101\t20250101\t1\t0\t0\nD\tdone\n"
    r = parse_remote_output("s", out, "", 0, 0.0)
    assert r.ok and r.files[0].path == "/a\u2028b\r.log"


def test_protocol_glob_empty():
    out = "E\tglob_empty\t/x/b*\nF\t/a/x.log\t20261101\t20261118\t3\t0\t0\nQ\t0\t0\nD\tdone\n"
    r = parse_remote_output("s", out, "", 0, 0.0)
    assert r.ok
    assert r.file_errors == [("glob_empty", "/x/b*")]
    assert len(r.files) == 1
