import base64
import hashlib
import re
import socket
import threading
import time

import paramiko
import pytest
from paramiko.ssh_exception import NoValidConnectionsError

from napi import collector as collector_mod
from napi.collector import Collector
from napi.models import LogFormatDef, ServerConfig, Settings
from napi.secrets import SessionSecrets

PW = "pw-Sup3rSecret"
OK_STDOUT = (b"F\t/log/access.log\t20260101\t20260110\t10\t0\t0\n"
             b"R\tGET\t/v1/x\t2xx\t10\t20260101\t20260110\n"
             b"Q\t0\t0\n"
             b"D\tdone\n")
TOKEN_RE = re.compile(r"napi-scan-[0-9a-f]+")
HOST_KEY = paramiko.RSAKey.generate(1024)


# ---------------------------------------------------------------- fake paramiko

class FakeChannel:
    """paramiko Channel 흉내.

    - 보통: `delay` 뒤에 stdout/stderr 데이터, EOF, exit status가 한꺼번에 준비된다.
    - `tail`: exit status는 `delay`에 준비되지만, 마지막 stdout 조각과 EOF는
      `tail_delay`만큼 늦게 온다 (OpenSSH sshd가 exit-status를 먼저 보내는 경우).
    - `hold`: 아무것도 오지 않는다 (끝나지 않는 원격 명령).
    - `stream`: stdout이 끝없이 계속 들어온다.
    """

    def __init__(self, stdout=b"", stderr=b"", exit_code=0, delay=0.0, hold=False,
                 tail=b"", tail_delay=0.0, stream=False):
        now = time.monotonic()
        self._out = stdout
        self._err = stderr
        self._tail = tail
        self._exit = exit_code
        self._ready_at = now + delay
        self._tail_at = now + delay + tail_delay
        self._hold = hold
        self._stream = stream
        self.closed = False

    def _arrived(self, at):
        return not self._hold and not self.closed and time.monotonic() >= at

    def _pump(self):
        if self._tail and self._arrived(self._tail_at):
            self._out, self._tail = self._out + self._tail, b""

    @property
    def eof_received(self):
        return not self._stream and self._arrived(self._tail_at)

    def recv_ready(self):
        if self._stream:
            return not self.closed
        self._pump()
        return self._arrived(self._ready_at) and bool(self._out)

    def recv(self, n):
        if self._stream:
            return b"x" * min(n, 16)  # 작게 받아 메모리를 아낀다
        data, self._out = self._out[:n], self._out[n:]
        return data

    def recv_stderr_ready(self):
        return not self._stream and self._arrived(self._ready_at) and bool(self._err)

    def recv_stderr(self, n):
        data, self._err = self._err[:n], self._err[n:]
        return data

    def exit_status_ready(self):
        return not self._stream and self._arrived(self._ready_at)

    def recv_exit_status(self):
        return self._exit

    def shutdown_write(self):
        pass

    def close(self):
        self.closed = True


class FakeStream:
    def __init__(self, channel):
        self.channel = channel


class World:
    """서버(host)별 동작과 공유 측정값."""

    def __init__(self, behaviors):
        self.behaviors = behaviors
        self.lock = threading.Lock()
        self.active = 0
        self.max_active = 0
        self.clients = []
        self.executing = threading.Event()

    def factory(self):
        c = FakeClient(self)
        with self.lock:
            self.clients.append(c)
        return c


class FakeClient:
    def __init__(self, world):
        self.world = world
        self.policy = None
        self.loaded = None
        self.host_keys = paramiko.HostKeys()
        self.connect_kwargs = None
        self.commands = []
        self.channels = []
        self.connected = False
        self.closed = False

    def set_missing_host_key_policy(self, policy):
        self.policy = policy

    def load_host_keys(self, filename):
        self.loaded = filename

    def get_host_keys(self):
        return self.host_keys

    def connect(self, hostname, port=22, **kwargs):
        self.connect_kwargs = dict(hostname=hostname, port=port, **kwargs)
        b = self.world.behaviors[hostname]
        if b.get("hostkey") is not None:
            name = hostname if port == 22 else f"[{hostname}]:{port}"
            self.policy.missing_host_key(self, name, b["hostkey"])
        if b.get("exc") is not None:
            raise b["exc"]
        with self.world.lock:
            self.world.active += 1
            self.world.max_active = max(self.world.max_active, self.world.active)
        self.connected = True

    def exec_command(self, command, timeout=None):
        self.commands.append(command)
        if len(self.commands) == 1:
            b = self.world.behaviors[self.connect_kwargs["hostname"]]
            ch = FakeChannel(b.get("stdout", OK_STDOUT), b.get("stderr", b""),
                             b.get("exit", 0), b.get("delay", 0.0), b.get("hold", False),
                             b.get("tail", b""), b.get("tail_delay", 0.0),
                             b.get("stream", False))
            self.world.executing.set()
        else:
            ch = FakeChannel()
        self.channels.append(ch)
        return FakeStream(ch), FakeStream(ch), FakeStream(ch)

    def close(self):
        if self.connected and not self.closed:
            with self.world.lock:
                self.world.active -= 1
        self.closed = True


# ---------------------------------------------------------------- helpers

def server(name, host, **kw):
    base = dict(name=name, host=host, user="app", log_globs=["/log/access.log*"])
    base.update(kw)
    return ServerConfig(**base)


def make(world, tmp_path, settings=None, prompt=None, secrets=None, formats=()):
    if secrets is None:
        secrets = SessionSecrets()
        for h in world.behaviors:
            secrets.set_password("app", h, 22, PW)
            secrets.set_password("app", h, 2222, PW)
    prompts = []

    def default_prompt(host, port, key_type, fp):
        prompts.append((host, port, key_type, fp))
        return False

    c = Collector(settings or Settings(), secrets, prompt or default_prompt,
                  tmp_path / "napi" / "known_hosts", client_factory=world.factory,
                  formats=formats)
    return c, prompts


class Progress:
    def __init__(self):
        self.calls = []
        self.lock = threading.Lock()

    def __call__(self, name, phase):
        with self.lock:
            self.calls.append((name, phase))

    def phases(self, name):
        return [p for n, p in self.calls if n == name]


def fingerprint(key):
    return "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")


# ---------------------------------------------------------------- tests

def test_collect_all_ok_preserves_order(tmp_path):
    world = World({"10.0.0.1": {"delay": 0.3}, "10.0.0.2": {}, "10.0.0.3": {}})
    c, _ = make(world, tmp_path)
    servers = [server("a", "10.0.0.1"), server("b", "10.0.0.2"), server("c", "10.0.0.3")]
    results = c.collect(servers, None, Progress(), threading.Event())
    assert [r.server for r in results] == ["a", "b", "c"]
    assert all(r.ok for r in results)
    assert results[0].rows[0].path == "/v1/x"
    assert all(cl.closed for cl in world.clients)
    for cl in world.clients:
        kw = cl.connect_kwargs
        assert kw["timeout"] == 15
        assert kw["allow_agent"] is False and kw["look_for_keys"] is False
        assert kw["username"] == "app" and kw["password"] == PW
        assert TOKEN_RE.search(cl.commands[0])
        assert cl.commands[0].startswith("timeout 3600 nice -n 19")


def test_one_server_auth_fail_others_ok(tmp_path):
    world = World({"10.0.0.1": {"exc": paramiko.AuthenticationException("denied")},
                   "10.0.0.2": {}})
    c, _ = make(world, tmp_path)
    results = c.collect([server("a", "10.0.0.1"), server("b", "10.0.0.2")], None,
                        Progress(), threading.Event())
    assert [r.ok for r in results] == [False, True]
    assert results[0].error == "인증 실패"
    assert all(cl.closed for cl in world.clients)


@pytest.mark.parametrize("exc, message", [
    (paramiko.BadHostKeyException("10.0.0.1", HOST_KEY, paramiko.RSAKey.generate(1024)),
     "호스트 키가 바뀌었어요 — 접속을 중단했어요"),
    (socket.timeout("timed out"), "접속할 수 없어요"),
    (NoValidConnectionsError({("10.0.0.1", 22): OSError("refused")}), "접속할 수 없어요"),
])
def test_connect_errors_map_to_korean(tmp_path, exc, message):
    world = World({"10.0.0.1": {"exc": exc}})
    c, _ = make(world, tmp_path)
    progress = Progress()
    [r] = c.collect([server("a", "10.0.0.1")], None, progress, threading.Event())
    assert not r.ok and r.error == message
    assert PW not in repr(r)
    assert progress.phases("a") == ["connecting", "failed"]


def test_parallel_limit(tmp_path):
    hosts = [f"10.0.0.{i}" for i in range(1, 5)]
    world = World({h: {"delay": 0.2} for h in hosts})
    c, _ = make(world, tmp_path, settings=Settings(max_parallel_servers=2))
    results = c.collect([server(f"s{i}", h) for i, h in enumerate(hosts)], None,
                        Progress(), threading.Event())
    assert all(r.ok for r in results)
    assert world.max_active == 2


def test_cancel_closes_and_marks_cancelled(tmp_path):
    world = World({"10.0.0.1": {"hold": True}})
    c, _ = make(world, tmp_path)
    cancel = threading.Event()
    progress = Progress()
    out = []
    t = threading.Thread(target=lambda: out.extend(
        c.collect([server("a", "10.0.0.1")], None, progress, cancel)))
    t.start()
    assert world.executing.wait(5)
    cancel.set()
    t.join(5)
    assert not t.is_alive()
    [r] = out
    assert not r.ok and r.error == "취소됨"
    [cl] = world.clients
    assert len(cl.commands) == 2
    token = TOKEN_RE.search(cl.commands[0]).group(0)
    assert cl.commands[1] == f"pkill -TERM -u app -f {token}"
    assert cl.channels[0].closed
    assert cl.closed
    assert progress.phases("a") == ["connecting", "aggregating", "cancelled"]


def test_cancel_before_start_does_not_connect(tmp_path):
    world = World({"10.0.0.1": {}})
    c, _ = make(world, tmp_path)
    cancel = threading.Event()
    cancel.set()
    progress = Progress()
    [r] = c.collect([server("a", "10.0.0.1")], None, progress, cancel)
    assert r.error == "취소됨"
    assert world.clients == []
    assert progress.phases("a") == ["cancelled"]


def test_client_wall_clock_timeout(tmp_path, monkeypatch):
    monkeypatch.setattr(collector_mod, "_CLIENT_GRACE_SEC", 0)
    world = World({"10.0.0.1": {"hold": True}})
    c, _ = make(world, tmp_path, settings=Settings(remote_timeout_sec=1))
    progress = Progress()
    [r] = c.collect([server("a", "10.0.0.1")], None, progress, threading.Event())
    assert not r.ok and r.error == "시간 초과"
    [cl] = world.clients
    assert cl.channels[0].closed and cl.closed
    assert cl.commands[1].startswith("pkill -TERM -u app -f napi-scan-")
    assert progress.phases("a")[-1] == "failed"


def test_host_key_rejected(tmp_path):
    world = World({"10.0.0.1": {"hostkey": HOST_KEY}})
    c, prompts = make(world, tmp_path)
    [r] = c.collect([server("a", "10.0.0.1", port=2222)], None, Progress(),
                    threading.Event())
    assert not r.ok
    assert r.error == "호스트 키를 확인하지 않아 접속하지 않았어요"
    assert prompts == [("10.0.0.1", 2222, "ssh-rsa", fingerprint(HOST_KEY))]
    assert not (tmp_path / "napi" / "known_hosts").exists()
    assert world.clients[0].commands == []


def test_host_key_accepted_is_saved(tmp_path):
    world = World({"10.0.0.1": {"hostkey": HOST_KEY}, "10.0.0.2": {"hostkey": HOST_KEY}})
    c, _ = make(world, tmp_path, prompt=lambda host, port, kt, fp: True)
    results = c.collect([server("a", "10.0.0.1", port=2222), server("b", "10.0.0.2")],
                        None, Progress(), threading.Event())
    assert all(r.ok for r in results)
    path = tmp_path / "napi" / "known_hosts"
    saved = paramiko.HostKeys(str(path))
    assert saved.lookup("[10.0.0.1]:2222")["ssh-rsa"] == HOST_KEY
    assert saved.lookup("10.0.0.2")["ssh-rsa"] == HOST_KEY
    assert world.clients[0].host_keys.lookup("[10.0.0.1]:2222") is not None


def test_existing_known_hosts_is_loaded(tmp_path):
    path = tmp_path / "napi" / "known_hosts"
    path.parent.mkdir()
    path.write_text("10.0.0.1 " + HOST_KEY.get_name() + " " + HOST_KEY.get_base64() + "\n")
    world = World({"10.0.0.1": {}})
    c, _ = make(world, tmp_path)
    c.collect([server("a", "10.0.0.1")], None, Progress(), threading.Event())
    assert world.clients[0].loaded == str(path)


def test_progress_phases(tmp_path):
    world = World({"10.0.0.1": {}, "10.0.0.2": {"stdout": b"Q\t0\t0\n"}})
    c, _ = make(world, tmp_path)
    progress = Progress()
    results = c.collect([server("a", "10.0.0.1"), server("b", "10.0.0.2")], None,
                        progress, threading.Event())
    assert progress.phases("a") == ["connecting", "aggregating", "done"]
    assert not results[1].ok
    assert progress.phases("b") == ["connecting", "aggregating", "failed"]


def test_large_stdout_with_large_stderr_is_read_fully(tmp_path):
    big_err = b"warn\n" * 50000
    big_out = b"R\tGET\t/v1/y\t2xx\t1\t20260101\t20260101\n" * 20000 + OK_STDOUT
    world = World({"10.0.0.1": {"stdout": big_out, "stderr": big_err}})
    c, _ = make(world, tmp_path)
    [r] = c.collect([server("a", "10.0.0.1")], None, Progress(), threading.Event())
    assert r.ok
    assert len(r.rows) == 20001


def test_bad_config_fails_without_connecting(tmp_path):
    world = World({"10.0.0.1": {}, "10.0.0.2": {}})
    c, _ = make(world, tmp_path)
    progress = Progress()
    results = c.collect([server("a", "10.0.0.1", log_globs=["relative/access.log"]),
                         server("b", "10.0.0.2")], None, progress, threading.Event())
    assert not results[0].ok and results[0].error.startswith("설정 오류: ")
    assert results[1].ok
    assert len(world.clients) == 1
    assert progress.phases("a") == ["failed"]


def test_collector_passes_formats(tmp_path):
    world = World({"10.0.0.1": {}})
    fmt = LogFormatDef("user_a", '[%{%Y-%m-%dT%H:%M:%S}t] [CLIENT(%h)] "%r" %s')
    c, _ = make(world, tmp_path, formats=[fmt])
    [r] = c.collect([server("a", "10.0.0.1", log_format="user_a")], None, Progress(),
                    threading.Event())
    assert r.ok
    [cl] = world.clients
    assert "CLIENT(" in cl.commands[0]


def test_missing_password_fails_without_connecting(tmp_path):
    world = World({"10.0.0.1": {}})
    c, _ = make(world, tmp_path, secrets=SessionSecrets())
    [r] = c.collect([server("a", "10.0.0.1")], None, Progress(), threading.Event())
    assert not r.ok and r.error == "비밀번호가 입력되지 않았어요"
    assert all(cl.connect_kwargs is None for cl in world.clients)


def test_key_auth_with_passphrase(tmp_path):
    key_path = tmp_path / "id_rsa"
    HOST_KEY.write_private_key_file(str(key_path), password="key-Secret-pp")
    secrets = SessionSecrets()
    secrets.set_passphrase(str(key_path), "key-Secret-pp")
    world = World({"10.0.0.1": {}})
    c, _ = make(world, tmp_path, secrets=secrets)
    [r] = c.collect([server("a", "10.0.0.1", auth="key", key_file=str(key_path))], None,
                    Progress(), threading.Event())
    assert r.ok
    kw = world.clients[0].connect_kwargs
    assert kw["pkey"] == HOST_KEY
    assert kw.get("password") is None


def test_key_auth_without_passphrase_needs_one(tmp_path):
    key_path = tmp_path / "id_rsa"
    HOST_KEY.write_private_key_file(str(key_path), password="key-Secret-pp")
    world = World({"10.0.0.1": {}})
    c, _ = make(world, tmp_path, secrets=SessionSecrets())
    progress = Progress()
    [r] = c.collect([server("a", "10.0.0.1", auth="key", key_file=str(key_path))], None,
                    progress, threading.Event())
    assert not r.ok and r.error == "키파일 암호가 필요해요"
    assert progress.phases("a")[-1] == "failed"


def test_key_auth_wrong_passphrase_hides_secret(tmp_path):
    key_path = tmp_path / "id_rsa"
    HOST_KEY.write_private_key_file(str(key_path), password="key-Secret-pp")
    secrets = SessionSecrets()
    secrets.set_passphrase(str(key_path), "wrong-Secret-pp")
    world = World({"10.0.0.1": {}})
    c, _ = make(world, tmp_path, secrets=secrets)
    [r] = c.collect([server("a", "10.0.0.1", auth="key", key_file=str(key_path))], None,
                    Progress(), threading.Event())
    assert not r.ok and r.error == "키파일 암호가 맞지 않거나 읽을 수 없는 키파일이에요"
    assert "Secret" not in repr(r)
    assert world.clients == []


def test_exit_status_before_last_stdout_chunk_still_ok(tmp_path):
    tail = b"D\tdone\n"
    assert OK_STDOUT.endswith(tail)
    head = OK_STDOUT[:-len(tail)]
    world = World({"10.0.0.1": {"stdout": head, "tail": tail, "tail_delay": 0.3}})
    c, _ = make(world, tmp_path)
    progress = Progress()
    [r] = c.collect([server("a", "10.0.0.1")], None, progress, threading.Event())
    assert r.ok, r.error
    assert progress.phases("a")[-1] == "done"


def test_continuous_output_does_not_block_cancel(tmp_path):
    world = World({"10.0.0.1": {"stream": True}})
    c, _ = make(world, tmp_path)
    cancel = threading.Event()
    out = []
    t = threading.Thread(target=lambda: out.extend(
        c.collect([server("a", "10.0.0.1")], None, Progress(), cancel)))
    t.start()
    assert world.executing.wait(5)
    time.sleep(0.1)
    cancel.set()
    t.join(5)
    assert not t.is_alive()
    [r] = out
    assert r.error == "취소됨"
    assert world.clients[0].commands[1].startswith("pkill -TERM -u app -f napi-scan-")


def test_continuous_output_does_not_block_wall_clock(tmp_path, monkeypatch):
    monkeypatch.setattr(collector_mod, "_CLIENT_GRACE_SEC", 0)
    world = World({"10.0.0.1": {"stream": True}})
    c, _ = make(world, tmp_path, settings=Settings(remote_timeout_sec=1))
    started = time.monotonic()
    [r] = c.collect([server("a", "10.0.0.1")], None, Progress(), threading.Event())
    assert r.error == "시간 초과"
    assert time.monotonic() - started < 5


def test_stderr_buffer_is_capped(tmp_path, monkeypatch):
    seen = {}
    real_parse = collector_mod.parse_remote_output

    def spy(server, stdout, stderr, exit_code, elapsed):
        seen["stderr_len"] = len(stderr)
        return real_parse(server, stdout, stderr, exit_code, elapsed)

    monkeypatch.setattr(collector_mod, "parse_remote_output", spy)
    world = World({"10.0.0.1": {"stderr": b"e" * (1024 * 1024)}})
    c, _ = make(world, tmp_path)
    [r] = c.collect([server("a", "10.0.0.1")], None, Progress(), threading.Event())
    assert r.ok
    assert seen["stderr_len"] == collector_mod._STDERR_CAP == 64 * 1024
