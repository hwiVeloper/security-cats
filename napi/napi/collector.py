"""SSH로 서버마다 원격 집계 명령을 실행한다 (병렬 처리, 호스트 키 확인, 취소)."""
from __future__ import annotations

import base64
import hashlib
import os
import shlex
import threading
import time
from collections.abc import Callable, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import paramiko
from paramiko.hostkeys import HostKeyEntry

from napi.models import LogFormatDef, ServerConfig, ServerScanResult, Settings
from napi.remote.command import build_remote_command
from napi.remote.protocol import parse_remote_output
from napi.secrets import SessionSecrets

# (host, port, key_type, sha256_fingerprint) -> 수락 여부
HostKeyPrompt = Callable[[str, int, str, str], bool]
# (server_name, phase): "connecting" | "aggregating" | "done" | "failed" | "cancelled"
ProgressFn = Callable[[str, str], None]

_CONNECT_TIMEOUT_SEC = 15
_CLIENT_GRACE_SEC = 60  # 원격 timeout 뒤에도 끝나지 않으면 PC 쪽에서 끊는다
_POLL_SEC = 0.05
_PKILL_WAIT_SEC = 5
_CHUNK = 65536
_STDERR_CAP = 64 * 1024  # stderr는 앞부분만 둔다 (오류 문구에는 앞 200자만 쓴다)

_CANCELLED = "취소됨"
_TIMED_OUT = "시간 초과"

# 여러 서버를 병렬로 접속하므로 known_hosts 쓰기는 한 번에 하나씩.
_known_hosts_lock = threading.Lock()


class _Failure(Exception):
    """사용자에게 그대로 보여줄 한국어 오류 문구. 비밀 값은 절대 담지 않는다."""


class HostKeyRejected(paramiko.SSHException):
    """사용자가 처음 보는 호스트 키를 수락하지 않았다."""


def sha256_fingerprint(key: paramiko.PKey) -> str:
    """OpenSSH 형식 지문: SHA256:<base64, 패딩 없음>."""
    digest = hashlib.sha256(key.asbytes()).digest()
    return "SHA256:" + base64.b64encode(digest).decode("ascii").rstrip("=")


def _append_known_host(path: Path, hostname: str, key: paramiko.PKey) -> None:
    line = HostKeyEntry([hostname], key).to_line()
    with _known_hosts_lock:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            prefix = ""
            if path.exists() and path.stat().st_size > 0:
                with open(path, "rb") as f:
                    f.seek(-1, os.SEEK_END)
                    if f.read(1) != b"\n":
                        prefix = "\n"
            with open(path, "a", encoding="utf-8", newline="\n") as f:
                f.write(prefix + line)
        except OSError:
            # 저장하지 못해도 이번 접속은 수락된 키로 계속한다. 다음 접속 때 다시 묻는다.
            pass


class _PromptPolicy(paramiko.MissingHostKeyPolicy):
    def __init__(self, host: str, port: int, prompt: HostKeyPrompt, known_hosts: Path):
        self._host = host
        self._port = port
        self._prompt = prompt
        self._known_hosts = known_hosts

    def missing_host_key(self, client, hostname, key):
        if not self._prompt(self._host, self._port, key.get_name(), sha256_fingerprint(key)):
            raise HostKeyRejected(hostname)
        client.get_host_keys().add(hostname, key.get_name(), key)
        _append_known_host(self._known_hosts, hostname, key)


def _load_key(key_file: str, passphrase: str | None) -> paramiko.PKey:
    password = passphrase.encode("utf-8") if passphrase is not None else None
    try:
        return paramiko.PKey.from_path(key_file, password)
    except FileNotFoundError:
        raise _Failure("키파일을 찾을 수 없어요") from None
    except paramiko.PasswordRequiredException:
        raise _Failure("키파일 암호가 필요해요") from None
    except TypeError:
        # cryptography: 암호화된 키인데 암호를 주지 않았다.
        if passphrase is None:
            raise _Failure("키파일 암호가 필요해요") from None
        raise _Failure("키파일을 읽을 수 없어요") from None
    except Exception:
        if passphrase is not None:
            raise _Failure("키파일 암호가 맞지 않거나 읽을 수 없는 키파일이에요") from None
        raise _Failure("키파일을 읽을 수 없어요") from None


class Collector:
    def __init__(self, settings: Settings, secrets: SessionSecrets,
                 host_key_prompt: HostKeyPrompt, known_hosts: Path,
                 client_factory=paramiko.SSHClient, formats: Sequence[LogFormatDef] = ()):
        self._settings = settings
        self._secrets = secrets
        self._prompt = host_key_prompt
        self._known_hosts = Path(known_hosts)
        self._client_factory = client_factory
        self._formats = tuple(formats)

    def collect(self, servers: list[ServerConfig], since: str | None,
                progress: ProgressFn, cancel: threading.Event) -> list[ServerScanResult]:
        """서버마다 집계를 실행한다. 반환 순서는 입력 순서와 같다."""
        workers = max(1, int(self._settings.max_parallel_servers))
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(self._run_one, s, since, progress, cancel) for s in servers]
        results = []
        for server, future in zip(servers, futures):
            try:
                results.append(future.result())
            except Exception as e:
                results.append(ServerScanResult(server=server.name, ok=False,
                                                error=f"알 수 없는 오류 ({type(e).__name__})"))
        return results

    # ------------------------------------------------------------ per server

    def _run_one(self, server: ServerConfig, since: str | None, progress: ProgressFn,
                 cancel: threading.Event) -> ServerScanResult:
        name = server.name
        if cancel.is_set():
            progress(name, "cancelled")
            return ServerScanResult(server=name, ok=False, error=_CANCELLED)

        token = "napi-scan-" + os.urandom(4).hex()
        try:
            command = build_remote_command(server, self._settings, since, token,
                                           self._formats)
            auth = self._auth_kwargs(server)
        except ValueError as e:
            progress(name, "failed")
            return ServerScanResult(server=name, ok=False, error=f"설정 오류: {e}")
        except _Failure as e:
            progress(name, "failed")
            return ServerScanResult(server=name, ok=False, error=str(e))

        start = time.monotonic()
        progress(name, "connecting")
        client = self._client_factory()
        connected = False
        try:
            client.set_missing_host_key_policy(
                _PromptPolicy(server.host, server.port, self._prompt, self._known_hosts))
            if self._known_hosts.exists():
                client.load_host_keys(str(self._known_hosts))
            client.connect(hostname=server.host, port=server.port, username=server.user,
                           timeout=_CONNECT_TIMEOUT_SEC, allow_agent=False,
                           look_for_keys=False, **auth)
            connected = True
            if cancel.is_set():
                progress(name, "cancelled")
                return ServerScanResult(server=name, ok=False, error=_CANCELLED,
                                        elapsed_sec=time.monotonic() - start)
            progress(name, "aggregating")
            result = self._execute(client, server, command, token, cancel, start)
        except HostKeyRejected:
            error = "호스트 키를 확인하지 않아 접속하지 않았어요"
        except paramiko.BadHostKeyException:
            error = "호스트 키가 바뀌었어요 — 접속을 중단했어요"
        except paramiko.AuthenticationException:
            error = "인증 실패"
        except (OSError, EOFError, paramiko.SSHException) as e:
            if connected:
                error = "연결이 끊겼어요"
            elif isinstance(e, paramiko.SSHException):
                error = f"SSH 접속 오류 ({type(e).__name__})"
            else:  # socket.timeout, NoValidConnectionsError, 이름 해석 실패 등
                error = "접속할 수 없어요"
        else:
            if result.error == _CANCELLED:
                progress(name, "cancelled")
            else:
                progress(name, "done" if result.ok else "failed")
            return result
        finally:
            try:
                client.close()
            except Exception:
                pass
        progress(name, "failed")
        return ServerScanResult(server=name, ok=False, error=error,
                                elapsed_sec=time.monotonic() - start)

    def _auth_kwargs(self, server: ServerConfig) -> dict:
        if server.auth == "key":
            if not server.key_file:
                raise ValueError("키파일 경로가 없어요")
            return {"pkey": _load_key(server.key_file,
                                      self._secrets.get_passphrase(server.key_file))}
        password = self._secrets.get_password(server.user, server.host, server.port)
        if password is None:
            raise _Failure("비밀번호가 입력되지 않았어요")
        return {"password": password}

    def _execute(self, client, server: ServerConfig, command: str, token: str,
                 cancel: threading.Event, start: float) -> ServerScanResult:
        _, stdout_file, _ = client.exec_command(command)
        chan = stdout_file.channel
        chan.shutdown_write()
        deadline = time.monotonic() + self._settings.remote_timeout_sec + _CLIENT_GRACE_SEC
        out, err = bytearray(), bytearray()
        while True:
            if cancel.is_set() or time.monotonic() >= deadline:
                error = _CANCELLED if cancel.is_set() else _TIMED_OUT
                chan.close()
                self._kill_remote(client, server.user, token)
                return ServerScanResult(server=server.name, ok=False, error=error,
                                        elapsed_sec=time.monotonic() - start)
            # EOF를 버퍼보다 먼저 확인한다. EOF 뒤에는 새 데이터가 오지 않으므로,
            # 이번에 읽을 것이 없으면 버퍼가 정말로 비었다는 뜻이다.
            # (OpenSSH sshd는 exit-status를 마지막 파이프 데이터보다 먼저 보낼 수 있어서
            # exit_status_ready()만으로는 끝났다고 볼 수 없다.)
            eof = chan.eof_received or chan.closed
            got = False
            # stdout/stderr를 번갈아 읽어 한쪽 버퍼가 가득 차 멈추는 일이 없게 한다.
            if chan.recv_ready():
                out += chan.recv(_CHUNK)
                got = True
            if chan.recv_stderr_ready():
                data = chan.recv_stderr(_CHUNK)
                err += data[:max(0, _STDERR_CAP - len(err))]
                got = True
            if got:
                continue
            if eof and chan.exit_status_ready():
                break
            cancel.wait(_POLL_SEC)
        exit_code = chan.recv_exit_status()
        return parse_remote_output(server.name, out.decode("utf-8", errors="replace"),
                                   err.decode("utf-8", errors="replace"), exit_code,
                                   time.monotonic() - start)

    @staticmethod
    def _kill_remote(client, user: str, token: str) -> None:
        """취소·시간 초과 시 원격 파이프라인을 정리한다. 실패는 무시한다."""
        try:
            _, stdout_file, _ = client.exec_command(
                f"pkill -TERM -u {shlex.quote(user)} -f {token}", timeout=_PKILL_WAIT_SEC)
            chan = stdout_file.channel
            end = time.monotonic() + _PKILL_WAIT_SEC
            while not chan.exit_status_ready() and time.monotonic() < end:
                time.sleep(_POLL_SEC)
        except Exception:
            pass
