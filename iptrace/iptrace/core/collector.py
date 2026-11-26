"""로그 수집: 원격 서버(SSH)와 로컬 파일.

원격 서버 원칙
- 로그 전체를 PC로 받지 않는다. 서버에서 `zcat | grep -F`로 의심 IP 문자열이 든 줄만 골라 받는다.
  (로그 전체를 받는 명령은 아예 만들 수 없게 했다)
- 읽기 전용 명령만 실행한다. 명령은 build_command()에서만 만들고, check_allowed()로 다시 검사한다.
- 모든 인자는 shlex.quote로 감싼다. 서버 부하를 줄이려고 nice/ionice를 붙인다.
- 무거운 명령 앞에는 timeout을 붙여, PC가 끊기거나 점검을 중지해도 서버 작업이 정해진 시간 안에 끝나게 한다.
- grep -m으로 파일당 받는 줄 수에 상한을 둔다(넓은 대역을 넣어도 로그 전체가 오지 않게).
"""
import glob
import gzip
import hashlib
import os
import shlex
import threading
import time
from contextlib import contextmanager, nullcontext
from datetime import datetime

from .models import FileInfo

ALLOWED = {"find", "zcat", "grep", "head", "sha256sum", "ls", "nice", "ionice", "timeout", "true"}
WRAPPERS = ("nice", "ionice", "timeout")  # 앞에 붙는 명령. 이것만 단독으로 실행할 수는 없다
PIPE_RIGHT = {"grep", "head"}           # 파이프 뒤에 올 수 있는 명령
MAX_SAMPLE_LINES = 20                   # 형식 확인용으로 받는 줄 수 상한
BAD_TOKENS = (";", "&&", "||", ">", ">>", "<", "&", "`", "$(")
BAD_OPTIONS = ("-exec", "-execdir", "-delete", "-ok", "-okdir", "-fprintf", "-fprint", "-fls", "-fprint0")


class CollectError(Exception):
    pass


class HostKeyRejected(CollectError):
    pass


class AuthFailed(CollectError):
    """계정·비밀번호(또는 키)가 틀림"""


class AuthGuard:
    """같은 계정·비밀번호가 한 번 틀리면 다른 서버에도 다시 보내지 않는다 (여러 번 틀리면 계정이 잠기므로).

    계정이 여러 서버에 공통(LDAP 등)이면 서버마다 틀린 비밀번호를 보내는 순간 실패 횟수가 쌓인다.
    같은 계정·비밀번호의 로그인은 한 번에 하나씩만 하고, 틀린 것이 확인되면 나머지는 시도하지 않는다.
    """

    def __init__(self):
        self._lock = threading.Lock()
        self._locks = {}
        self.failed = set()

    @staticmethod
    def _key(user, password):
        return (user or "", hashlib.sha256(password.encode("utf-8")).hexdigest())

    @contextmanager
    def attempt(self, user, password):
        key = self._key(user, password)
        with self._lock:
            lk = self._locks.setdefault(key, threading.Lock())
        with lk:
            if key in self.failed:
                raise AuthFailed("인증 실패: 같은 계정·비밀번호가 다른 서버에서 틀려 접속을 시도하지 않았습니다 (계정 잠김 방지)")
            try:
                yield
            except AuthFailed:
                self.failed.add(key)
                raise


def build_command(kind: str, *, path: str = "", pattern: str = "", since: datetime = None,
                  patterns=None, lines: int = 5, use_ionice: bool = True, timeout: int = None,
                  max_count: int = None) -> str:
    q = shlex.quote
    # timeout: 서버에서 이 시간(초)이 지나면 명령을 끝낸다(TERM, 10초 뒤에도 남아 있으면 KILL)
    limit = f"timeout -k 10 {int(timeout)} " if timeout else ""
    low = limit + "nice -n 19 " + ("ionice -c3 " if use_ionice else "")
    if kind == "list":
        # path는 와일드카드가 들어간 전체 경로 (예: /data/webserver/APP/log/*_ssl/*.log*)
        base, depth = split_glob(path)
        cmd = (f"{low}find {q(base)} -mindepth {depth} -maxdepth {depth} -type f -path {q(path)}"
               + (f" -newermt {q(since.strftime('%Y-%m-%d %H:%M:%S'))}" if since else "")
               + f" -printf {q('%T@ %s %p' + chr(92) + 'n')}")
    elif kind == "grep":
        if not patterns:
            raise ValueError("서버 필터 문자열이 없습니다")
        es = " ".join(f"-e {q(p)}" for p in patterns)
        # -a: 바이너리로 보이는 줄도 텍스트로, -n: 원본 줄번호, -F: 고정 문자열(정규식 아님)
        # -m: 이 줄 수만큼 찾으면 grep이 끝나고, 앞의 zcat도 따라 끝난다
        m = f"-m {int(max_count)} " if max_count else ""
        cmd = f"{low}zcat -f -- {q(path)} | nice -n 19 grep -a -n -F {m}{es}"
    elif kind == "head":
        n = max(1, min(int(lines), MAX_SAMPLE_LINES))
        cmd = f"{low}zcat -f -- {q(path)} | head -n {n}"
    elif kind == "hash":
        cmd = f"{low}sha256sum -- {q(path)}"
    elif kind == "check_dir":
        cmd = f"ls -ld -- {q(path)}"
    elif kind == "ping":
        cmd = "true"
    else:
        raise ValueError(f"허용되지 않은 명령 종류: {kind}")
    check_allowed(cmd)
    return cmd


def split_glob(path: str):
    """'/data/webserver/APP/log/*_ssl/*.log' -> ('/data/webserver/APP/log', 2)

    와일드카드가 처음 나오는 폴더 앞까지를 find 시작 폴더로, 나머지 단계 수를 검색 깊이로 쓴다.
    와일드카드가 없으면 파일이 있는 폴더와 깊이 1.
    """
    parts = [p for p in path.split("/") if p]
    idx = next((i for i, p in enumerate(parts) if any(c in p for c in "*?[")), len(parts) - 1)
    idx = max(1, idx) if len(parts) > 1 else 0
    base = "/" + "/".join(parts[:idx])
    return base, len(parts) - idx


def _program(seg: list) -> str:
    i = 0
    while i < len(seg):
        t = seg[i]
        if t == "nice":
            i += 3
        elif t == "ionice":
            i += 2
        elif t == "timeout":
            i += 1
            while i < len(seg) and seg[i] in ("-k", "-s"):
                i += 2
            if i >= len(seg) or not seg[i].isdigit():
                raise ValueError("timeout 시간은 숫자여야 합니다")
            i += 1
        else:
            return t
    return ""


def check_allowed(cmd: str) -> None:
    """허용 목록의 읽기 전용 명령만, 파이프는 'zcat | grep/head' 한 번만 허용한다."""
    outside = _unquoted(cmd)
    if any(bad in outside for bad in BAD_TOKENS):
        raise ValueError("명령 연결·리다이렉션·치환은 허용되지 않습니다")
    tokens = shlex.split(cmd)
    segments = [[]]
    for t in tokens:
        if t == "|":
            segments.append([])
        else:
            segments[-1].append(t)
    if len(segments) > 2:
        raise ValueError("파이프는 한 번만 허용됩니다")
    for seg in segments:
        prog = _program(seg)
        if prog not in ALLOWED or prog in WRAPPERS:
            raise ValueError(f"허용되지 않은 명령: {prog or '(없음)'}")
        for t in seg:
            if t in BAD_OPTIONS:
                raise ValueError(f"허용되지 않은 옵션: {t}")
    if len(segments) == 2:
        if _program(segments[0]) != "zcat" or _program(segments[1]) not in PIPE_RIGHT:
            raise ValueError("허용되는 파이프는 'zcat | grep' 또는 'zcat | head' 뿐입니다")


def _unquoted(cmd: str) -> str:
    """작은따옴표 밖의 글자만 남긴다(치환 문자 검사용)."""
    out, inside = [], False
    for c in cmd:
        if c == "'":
            inside = not inside
            continue
        if not inside:
            out.append(c)
    return "".join(out)


# ---------------------------------------------------------------- 공통 인터페이스
class Source:
    name = ""
    server_filter = False     # True면 서버에서 걸러 준 줄만 받는다

    def connect(self):
        pass

    def close(self):
        pass

    def list_files(self, start: datetime) -> list:
        raise NotImplementedError

    def read_lines(self, info: FileInfo, on_bytes=None, patterns=None):
        """(원본 줄번호, 바이트 줄)을 차례로 돌려준다."""
        raise NotImplementedError


# ---------------------------------------------------------------- 로컬 파일 (이미 PC에 있는 로그)
class LocalSource(Source):
    def __init__(self, name: str, pattern: str):
        self.name = name
        self.pattern = pattern

    def list_files(self, start: datetime) -> list:
        files = []
        if any(c in self.pattern for c in "*?["):
            paths = sorted(p for p in set(glob.glob(self.pattern)) if os.path.isfile(p))
        else:
            paths = [self.pattern] if os.path.isfile(self.pattern) else []
        if not paths:
            raise CollectError(f"로그 파일이 없습니다: {self.pattern}")
        for p in paths:
            st = os.stat(p)
            # 수정 시각이 조회 시작보다 이전인 파일에는 기간 안의 로그가 없다
            if start and st.st_mtime < start.timestamp():
                continue
            files.append(FileInfo(server=self.name, path=p, size=st.st_size, mtime=st.st_mtime))
        return files

    def read_lines(self, info: FileInfo, on_bytes=None, patterns=None):
        n = 0
        with open(info.path, "rb") as raw:
            head = raw.read(2)
            raw.seek(0)
            data = gzip.GzipFile(fileobj=raw) if head == b"\x1f\x8b" else raw
            for n, line in enumerate(data, 1):
                if on_bytes and n % 20000 == 0:
                    on_bytes(raw.tell())
                yield n, line
        if on_bytes:
            on_bytes(info.size)
        info.lines = n
        info.sha256 = file_sha256(info.path)


def file_sha256(path) -> str:
    """증적용 해시. 큰 단위로 따로 읽는다."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


# ---------------------------------------------------------------- 원격 서버 (SSH)
class SshSource(Source):
    server_filter = True

    def __init__(self, server: dict, ssh: dict, password: str = None, known_hosts: str = None,
                 on_unknown_host=None, guard: AuthGuard = None):
        """on_unknown_host(server_name, host, key_type, fingerprint) -> bool (신뢰하면 True)
        guard: 여러 서버를 함께 점검할 때 같은 AuthGuard를 넘기면 틀린 비밀번호를 거듭 보내지 않는다."""
        self.guard = guard
        self.server = server
        self.name = server["name"]
        self.ssh = ssh
        self.password = password
        self.known_hosts = known_hosts
        self.on_unknown_host = on_unknown_host
        self.client = None

    @property
    def _ionice(self):
        return self.ssh.get("use_ionice", True)

    @property
    def _timeout(self) -> int:
        return int(self.ssh.get("server_timeout", 180))

    @property
    def _max_lines(self) -> int:
        return int(self.ssh.get("max_lines_per_file", 100000))

    def connect(self):
        try:
            import paramiko
        except ImportError as e:
            raise CollectError("SSH 모듈(paramiko)이 설치되지 않아 서버에 접속할 수 없습니다. "
                               "'pip install paramiko'로 설치하세요") from e

        source = self

        class AskPolicy(paramiko.MissingHostKeyPolicy):
            def missing_host_key(self, client, hostname, key):
                fp = "SHA256:" + _b64_sha256(key.asbytes())
                ok = bool(source.on_unknown_host and source.on_unknown_host(
                    source.name, hostname, key.get_name(), fp))
                if not ok:
                    raise HostKeyRejected(f"처음 보는 호스트 키를 신뢰하지 않아 접속하지 않았습니다 ({fp})")
                client.get_host_keys().add(hostname, key.get_name(), key)
                if source.known_hosts:
                    os.makedirs(os.path.dirname(source.known_hosts), exist_ok=True)
                    client.save_host_keys(source.known_hosts)

        c = paramiko.SSHClient()
        if self.known_hosts and os.path.exists(self.known_hosts):
            c.load_host_keys(self.known_hosts)
        c.set_missing_host_key_policy(AskPolicy())
        kw = dict(hostname=self.server["host"], port=int(self.server.get("port", 22)),
                  username=self.ssh.get("user"), timeout=float(self.ssh.get("connect_timeout", 10)),
                  allow_agent=False, look_for_keys=False)
        use_key = self.ssh.get("auth") == "key"
        if use_key:
            kw["key_filename"] = os.path.expanduser(self.ssh.get("key_file", ""))
        else:
            if not self.password:
                # 빈 비밀번호로 시도하면 실패 횟수만 쌓인다
                raise CollectError("비밀번호가 입력되지 않아 접속하지 않았습니다")
            kw["password"] = self.password
        # paramiko는 비밀번호를 한 번만 보낸다(키·에이전트 시도 안 함). 같은 계정·비밀번호는 guard로 한 번씩만 시도
        attempt = self.guard.attempt(self.ssh.get("user"), self.password) if (self.guard and not use_key) else nullcontext()
        with attempt:
            try:
                c.connect(**kw)
            except paramiko.BadHostKeyException as e:
                raise CollectError("호스트 키가 바뀌었습니다. 서버 담당자 확인 전에는 접속하지 않습니다") from e
            except paramiko.AuthenticationException as e:
                raise AuthFailed("인증 실패 (계정·비밀번호·키 확인)") from e
            except HostKeyRejected:
                raise
            except Exception as e:  # noqa: BLE001 - 네트워크 오류는 메시지로만 보고한다
                raise CollectError(f"접속 실패: {e}") from e
        self.client = c

    def close(self):
        if self.client:
            self.client.close()
            self.client = None

    def _exec(self, cmd: str):
        """명령을 실행하고 (채널, stdout, stderr)를 바이너리 모드로 돌려준다."""
        check_allowed(cmd)
        chan = self.client.get_transport().open_session()
        chan.settimeout(float(self.ssh.get("command_timeout", 600)))
        chan.exec_command(cmd)
        return chan, chan.makefile("rb"), chan.makefile_stderr("rb")

    def _run(self, cmd: str, ok_codes=(0,)) -> str:
        chan, out, err = self._exec(cmd)
        data = out.read().decode("utf-8", errors="replace")
        code = chan.recv_exit_status()
        if code not in ok_codes:
            msg = err.read().decode("utf-8", errors="replace").strip()
            raise CollectError(f"원격 명령 실패({code}): {msg[:200]}")
        return data

    @property
    def logs(self) -> list:
        """[(로그 경로, 형식 이름)] (경로마다 형식이 다를 수 있다)"""
        default = self.server.get("format", "")
        if self.server.get("logs"):
            return [(x["path"].strip(), x.get("format") or default) for x in self.server["logs"] if x.get("path", "").strip()]
        return [(p.strip(), default) for p in self.server.get("log_paths", []) if p.strip()]

    @property
    def log_paths(self) -> list:
        return [p for p, _ in self.logs]

    def check(self) -> str:
        """로그 경로마다 시작 폴더(와일드카드 앞)가 있고 읽을 수 있는지 확인한다."""
        bases = sorted({split_glob(p)[0] for p in self.log_paths})
        for b in bases:
            try:
                self._run(build_command("check_dir", path=b))
            except CollectError as e:
                raise CollectError(f"로그 폴더 '{b}'를 확인할 수 없습니다 ({e})") from e
        return "ok"

    def list_files_for(self, pattern: str, fmt: str, start: datetime = None) -> list:
        """로그 경로 하나에서 파일 목록 (각 파일에 그 경로의 형식 이름을 붙인다)."""
        cmd = build_command("list", path=pattern, since=start, use_ionice=self._ionice, timeout=self._timeout)
        files = []
        # 일부 하위 폴더를 읽을 권한이 없으면 find가 1을 돌려준다. 찾은 파일은 그대로 쓴다.
        for line in self._run(cmd, ok_codes=(0, 1)).splitlines():
            parts = line.split(" ", 2)
            if len(parts) == 3:
                files.append(FileInfo(server=self.name, path=parts[2], size=int(parts[1]), mtime=float(parts[0]),
                                      log_format=fmt))
        files.sort(key=lambda f: f.mtime)
        return files

    def list_files(self, start: datetime) -> list:
        files, seen = [], set()
        for pattern, fmt in self.logs:
            for f in self.list_files_for(pattern, fmt, start):
                if f.path not in seen:      # 두 경로에 같은 파일이 걸리면 먼저 적은 경로(형식)를 쓴다
                    seen.add(f.path)
                    files.append(f)
        files.sort(key=lambda f: f.mtime)
        return files

    def read_lines(self, info: FileInfo, on_bytes=None, patterns=None):
        """서버에서 grep으로 거른 줄만 받는다. 줄번호는 원본 파일 기준이다."""
        cmd = build_command("grep", path=info.path, patterns=patterns, use_ionice=self._ionice,
                            timeout=self._timeout, max_count=self._max_lines)
        started = time.monotonic()
        chan, out, err = self._exec(cmd)
        n = 0
        try:
            for raw in out:
                num, sep, line = raw.partition(b":")
                if not sep or not num.isdigit():
                    continue
                n += 1
                yield int(num), line
        except BaseException:
            # 중지·오류로 도중에 멈추면 채널을 닫는다 (서버 쪽 작업은 timeout으로 끝난다)
            chan.close()
            raise
        code = chan.recv_exit_status()
        msg = err.read().decode("utf-8", errors="replace").strip()
        if n >= self._max_lines:
            info.limit = "줄 수 상한"
        elif time.monotonic() - started >= self._timeout - 2:
            info.limit = "시간 제한"
        # 상한에 걸려 grep이 먼저 끝나면 zcat이 'Broken pipe'를 남길 수 있다. 이것은 오류가 아니다
        if info.limit and "Broken pipe" in msg:
            msg = ""
        # grep: 0 = 찾음, 1 = 없음, 2 이상 = 오류. zcat 오류는 stderr로만 나오므로 함께 확인한다
        if code not in (0, 1) or msg:
            raise CollectError(f"로그 읽기 실패: {msg[:200] or f'종료 코드 {code}'}")
        if on_bytes:
            on_bytes(info.size)
        info.lines = 0          # 서버에서 걸러 받으므로 전체 줄 수는 알 수 없다
        info.matched = n
        if self.ssh.get("evidence_hash", False):
            try:
                out_hash = self._run(build_command("hash", path=info.path, use_ionice=self._ionice,
                                                   timeout=self._timeout))
                info.sha256 = out_hash.split(" ", 1)[0]
            except CollectError:
                info.sha256 = ""    # 해시는 증적용이라 실패해도 점검 결과에는 영향이 없다

    def sample_lines(self, info: FileInfo, n: int = 5) -> list:
        """형식 확인용으로 파일 앞부분 몇 줄만 받는다(최대 MAX_SAMPLE_LINES)."""
        data = self._run(build_command("head", path=info.path, lines=n, use_ionice=self._ionice,
                                       timeout=self._timeout))
        return [x for x in data.splitlines() if x.strip()]


def _b64_sha256(data: bytes) -> str:
    import base64
    return base64.b64encode(hashlib.sha256(data).digest()).decode().rstrip("=")
