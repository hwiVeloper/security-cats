"""가짜 SSH 서버(paramiko)로 원격 수집 경로를 검증한다.

실제 WebtoB 서버 없이 접속 → 호스트 키 확인 → 파일 목록 → 로그 읽기 → 해시 흐름과
허용 목록 밖 명령이 오지 않는지를 확인한다.
"""
import hashlib
import os
import shlex
import socket
import threading

import paramiko
import pytest

from conftest import FIX, kst
from iptrace.core import engine, ioc
from iptrace.core.collector import AuthFailed, AuthGuard, CollectError, SshSource, check_allowed
from iptrace.core.models import CheckRequest

LOG = os.path.join(FIX, "access_sample.log")
REMOTE = "/webtob/log/access_20261021.log"


class FakeServer(paramiko.ServerInterface):
    grep_patterns = []
    sent_lines = []
    auth_attempts = []      # 서버가 받은 비밀번호 로그인 시도 (계정 잠김 방지 확인용)

    def __init__(self, commands):
        self.commands = commands
        self.event = threading.Event()

    def check_channel_request(self, kind, chanid):
        return paramiko.OPEN_SUCCEEDED if kind == "session" else paramiko.OPEN_FAILED_ADMINISTRATIVELY_PROHIBITED

    def check_auth_password(self, username, password):
        self.auth_attempts.append((username, password))
        return paramiko.AUTH_SUCCESSFUL if (username, password) == ("logviewer", "pw") else paramiko.AUTH_FAILED

    def get_allowed_auths(self, username):
        return "password"

    def check_channel_exec_request(self, channel, command):
        cmd = command.decode()
        self.commands.append(cmd)
        threading.Thread(target=self._run, args=(channel, cmd), daemon=True).start()
        return True

    def _run(self, ch, cmd):
        try:
            check_allowed(cmd)
            t = shlex.split(cmd)
            prog = [x for x in t if x in ("find", "zcat", "sha256sum", "ls", "true")][0]
            data = open(LOG, "rb").read()
            if prog == "find":
                ch.sendall(f"1792540800.0 {len(data)} {REMOTE}\n".encode())
            elif prog == "zcat" and "grep" in t:
                # 서버의 'zcat | grep -n -F -e ...' 흉내: 걸린 줄만 '줄번호:내용'으로 보낸다
                pats = [t[i + 1].encode() for i, x in enumerate(t) if x == "-e"]
                limit = int(t[t.index("-m") + 1]) if "-m" in t else None
                self.grep_patterns.append(pats)
                sent = 0
                for n, line in enumerate(data.splitlines(keepends=True), 1):
                    if any(p in line for p in pats):
                        if limit is not None and sent >= limit:
                            break
                        ch.sendall(f"{n}:".encode() + line)
                        sent += 1
                self.sent_lines.append(sent)
            elif prog == "zcat" and "head" in t:
                n = int(t[t.index("-n", t.index("head")) + 1])   # nice -n 19 가 아니라 head -n 의 값
                ch.sendall(b"".join(data.splitlines(keepends=True)[:n]))
            elif prog == "sha256sum":
                ch.sendall(f"{hashlib.sha256(data).hexdigest()}  {REMOTE}\n".encode())
            ch.send_exit_status(0)
        except Exception as e:  # noqa: BLE001
            ch.sendall_stderr(str(e).encode())
            ch.send_exit_status(1)
        finally:
            ch.close()


@pytest.fixture
def ssh_server():
    host_key = paramiko.RSAKey.generate(2048)
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    sock.listen(5)
    commands = []
    stop = threading.Event()

    def serve():
        sock.settimeout(0.3)
        while not stop.is_set():
            try:
                client, _ = sock.accept()
            except OSError:
                continue
            t = paramiko.Transport(client)
            t.add_server_key(host_key)
            t.start_server(server=FakeServer(commands))

    th = threading.Thread(target=serve, daemon=True)
    th.start()
    yield sock.getsockname()[1], commands
    stop.set()
    sock.close()


def server_cfg(port):
    return {"name": "web01", "host": "127.0.0.1", "port": port, "log_paths": ["/webtob/log/access*.log*"],
            "format": "test_xff", "user": "", "auth": "", "key_file": ""}


def test_ssh_collection_end_to_end(cfg, ssh_server, tmp_path):
    port, commands = ssh_server
    cfg["ssh"].update(user="logviewer", known_hosts=str(tmp_path / "known_hosts"))
    asked = []
    res = ioc.parse_text("203.0.113.45\n198.51.100.7")
    req = CheckRequest(iocs=res.entries, start=kst(2026, 7, 1), end=kst(2026, 10, 31),
                       servers=[server_cfg(port)], passwords={"web01": "pw"})
    result = engine.run_check(req, cfg, on_unknown_host=lambda *a: asked.append(a) or True)
    assert result.servers[0].ok, result.servers[0].reason
    s = {x.ioc_key: x for x in result.summaries}
    assert s["203.0.113.45"].requests == 3 and s["203.0.113.45"].grade == "CRITICAL"
    assert s["198.51.100.7"].c404 == 3
    f = result.servers[0].files[0]
    assert f.sha256 == "" and not any("sha256sum" in c for c in commands)  # 증적 해시는 기본으로 끔
    assert f.limit == "" and result.servers[0].state == "완료"
    assert len(asked) == 1 and asked[0][3].startswith("SHA256:")
    assert (tmp_path / "known_hosts").exists()
    for c in commands:
        check_allowed(c)
    assert any(c.startswith("timeout -k 10 180 nice -n 19 ionice -c3 find /webtob/log") for c in commands)
    # 로그 전체가 아니라 서버에서 거른 줄만 받았는지. 무거운 명령은 서버에서 시간 제한이 걸려 있는지
    reads = [c for c in commands if "zcat" in c]
    assert reads and all("| nice -n 19 grep -a -n -F -m 100000 " in c for c in reads)
    assert all(c.startswith("timeout -k 10 180 ") for c in reads)
    assert sorted(FakeServer.grep_patterns[-1]) == [b"198.51.100.7", b"203.0.113.45"]
    assert FakeServer.sent_lines[-1] == 8 and result.servers[0].candidate_lines == 8
    assert result.servers[0].server_filter and result.servers[0].files[0].matched == 8
    assert s["203.0.113.45"].evidence[0][2] == 7  # 원본 파일 기준 줄번호
    # 두 번째 접속은 known_hosts에 저장된 키로 묻지 않는다
    result2 = engine.run_check(req, cfg, on_unknown_host=lambda *a: asked.append(a) or True)
    assert result2.servers[0].ok and len(asked) == 1


def test_ssh_line_limit_and_evidence_hash(cfg, ssh_server, tmp_path):
    """파일당 최대 줄 수에 걸리면 '부분 분석'으로 표시하고, 증적 해시는 켰을 때만 계산한다."""
    port, commands = ssh_server
    cfg["ssh"].update(user="logviewer", known_hosts=str(tmp_path / "known_hosts"),
                      max_lines_per_file=3, evidence_hash=True)
    res = ioc.parse_text("203.0.113.45\n198.51.100.7")
    req = CheckRequest(iocs=res.entries, start=kst(2026, 7, 1), end=kst(2026, 10, 31),
                       servers=[server_cfg(port)], passwords={"web01": "pw"})
    result = engine.run_check(req, cfg, on_unknown_host=lambda *a: True)
    st = result.servers[0]
    assert st.ok and FakeServer.sent_lines[-1] == 3 and st.candidate_lines == 3
    assert st.files[0].limit == "줄 수 상한" and st.state == "부분 분석"
    assert "끝까지 못 본 파일 1개(줄 수 상한)" in st.detail_text()
    assert "끝까지 못 본 파일 1개" in result.coverage_text()
    assert st.files[0].sha256 == hashlib.sha256(open(LOG, "rb").read()).hexdigest()
    assert any(c.startswith("timeout -k 10 180 nice -n 19 ionice -c3 sha256sum") for c in commands)


def test_same_host_servers_run_one_at_a_time(cfg):
    """SSH 주소가 같은 항목(api, fe)은 한 묶음으로 차례로 점검한다."""
    def src(name, host):
        return SshSource({"name": name, "host": host}, {}), None
    groups = engine._group_by_host([src("web01-api", "10.2.0.11"), src("web02", "10.2.0.12"),
                                    src("web01-fe", " 10.2.0.11 ")])
    assert [[s.name for s, _ in g] for g in groups] == [["web01-api", "web01-fe"], ["web02"]]


def test_ssh_rejected_host_key_and_bad_password(cfg, ssh_server, tmp_path):
    port, _ = ssh_server
    ssh = {"user": "logviewer", "auth": "password", "connect_timeout": 5, "command_timeout": 30}
    src = SshSource(server_cfg(port), ssh, "pw", str(tmp_path / "kh"), on_unknown_host=lambda *a: False)
    with pytest.raises(CollectError):
        src.connect()
    src = SshSource(server_cfg(port), ssh, "wrong", str(tmp_path / "kh2"), on_unknown_host=lambda *a: True)
    with pytest.raises(CollectError, match="인증"):
        src.connect()


def test_wrong_password_is_sent_only_once(cfg, ssh_server, tmp_path):
    """같은 계정·틀린 비밀번호는 한 번만 서버에 보내고, 같은 점검의 다른 서버에는 보내지 않는다 (계정 잠김 방지)."""
    port, _ = ssh_server
    cfg["ssh"].update(user="logviewer", known_hosts=str(tmp_path / "known_hosts"), workers=2)
    FakeServer.auth_attempts.clear()
    res = ioc.parse_text("203.0.113.45")
    servers = [dict(server_cfg(port), name="web01"), dict(server_cfg(port), name="web02", host="localhost"),
               dict(server_cfg(port), name="web03", host="127.0.0.1")]
    req = CheckRequest(iocs=res.entries, start=kst(2026, 7, 1), end=kst(2026, 10, 31), servers=servers,
                       passwords={"web01": "wrong", "web02": "wrong", "web03": "wrong"})
    result = engine.run_check(req, cfg, on_unknown_host=lambda *a: True)
    assert [s.ok for s in result.servers] == [False, False, False]
    assert all("인증 실패" in s.reason for s in result.servers)
    assert len(FakeServer.auth_attempts) == 1, FakeServer.auth_attempts
    assert sum("시도하지 않았습니다" in s.reason for s in result.servers) == 2


def test_guard_and_empty_password(ssh_server, tmp_path):
    port, _ = ssh_server
    ssh = {"user": "logviewer", "auth": "password", "connect_timeout": 5, "command_timeout": 30}
    FakeServer.auth_attempts.clear()
    guard = AuthGuard()
    with pytest.raises(AuthFailed):
        SshSource(server_cfg(port), ssh, "wrong", str(tmp_path / "kh"), lambda *a: True, guard=guard).connect()
    with pytest.raises(AuthFailed, match="시도하지 않았습니다"):
        SshSource(server_cfg(port), ssh, "wrong", str(tmp_path / "kh"), lambda *a: True, guard=guard).connect()
    with pytest.raises(CollectError, match="비밀번호가 입력되지 않아"):
        SshSource(server_cfg(port), ssh, "", str(tmp_path / "kh"), lambda *a: True, guard=guard).connect()
    assert len(FakeServer.auth_attempts) == 1
    ok = SshSource(server_cfg(port), ssh, "pw", str(tmp_path / "kh"), lambda *a: True, guard=guard)
    ok.connect()   # 다른(맞는) 비밀번호는 그대로 시도한다
    ok.close()


def test_connection_test_checks_each_log_path(cfg, ssh_server, tmp_path):
    """연결 테스트는 로그 경로마다 파일 수와 그 경로 형식의 일치 여부를 따로 알려 준다."""
    from types import SimpleNamespace
    from iptrace.gui.settings_page import run_connection_test
    port, commands = ssh_server
    cfg["ssh"].update(user="logviewer", known_hosts=str(tmp_path / "known_hosts"))
    cfg["log_formats"]["webtob_error"] = "[%{%Y-%m-%dT%H:%M:%S}t] [CLIENT(%h)] %*"
    s = dict(server_cfg(port), logs=[{"path": "/webtob/log/access*.log*", "format": "test_xff"},
                                     {"path": "/webtob/log/error*.log*", "format": "webtob_error"}])
    main = SimpleNamespace(pw_for=lambda srv, c=None: "pw", mark_auth_failed=lambda *a: None, passwords={})
    rows = run_connection_test(None, None, lambda *a: True, [s], main, cfg)
    r = rows[0]
    assert r["ok"], r["text"]
    by_path = {p["path"]: p for p in r["paths"]}
    assert by_path["/webtob/log/access*.log*"]["fmt_ok"] == (5, 5)          # 접근 로그 형식이 맞음
    assert by_path["/webtob/log/error*.log*"]["fmt_ok"][0] == 0             # 같은 파일을 에러 로그 형식으로 보면 안 맞음
    assert "형식 안 맞는 경로 1개" in r["text"]
    assert sum(1 for c in commands if " find " in f" {c}" and "-path" in c) >= 2   # 경로마다 따로 찾음
