"""원격 서버에서 실행할 셸 명령을 조립한다.

모양 (Global Constraints, Ruling R2):
    timeout <sec> nice -n 19 $(ionice가 있으면 ionice -c3) sh -c '<pipeline>' <token>
취소 토큰은 sh -c의 $0으로 넘겨 timeout과 sh의 argv에 보이게 한다 (pkill -f <token>).
명령은 읽기 전용이다. 쓰는 리다이렉션은 >/dev/null, 2>/dev/null 뿐이다.
사용자 값은 모두 shlex.quote로 감싸거나 정수·형식 검사를 거친다.
"""
from __future__ import annotations

import re
import shlex

from napi.models import ServerConfig, Settings
from napi.remote.awk_program import awk_program

_TOKEN_RE = re.compile(r"napi-scan-[0-9a-f]{8}")
_SINCE_RE = re.compile(r"[0-9]{8}")

_IONICE = "$(command -v ionice >/dev/null 2>/dev/null && echo ionice -c3)"
_FILE_LOOP = (r'while IFS= read -r f; do printf "\001F\t%s\n" "$f"; '
              r'gzip -dcf -- "$f" 2>/dev/null; done')


def split_glob(log_glob: str) -> tuple[str, str]:
    """"/webtob/log/access.log*" -> ("/webtob/log", "access.log*")."""
    directory, _, pattern = log_glob.rpartition("/")
    return (directory or "/"), pattern


def _int(name: str, value: object, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name}은(는) {minimum} 이상의 정수여야 해요: {value!r}")
    return value


def _check_since(since: str | None) -> None:
    if since is not None and not _SINCE_RE.fullmatch(since):
        raise ValueError(f"since는 YYYYMMDD 형식이어야 해요: {since!r}")


def build_pipeline(server: ServerConfig, settings: Settings, since: str | None) -> str:
    """sh -c로 실행할 본문 (find | 파일별 gzip -dcf | awk)."""
    _check_since(since)
    max_rows = _int("max_rows", settings.max_rows, 0)
    top_404 = _int("top_404", settings.top_404, 0)
    program = awk_program(server.log_format, server.custom_format)
    directory, pattern = split_glob(server.log_glob)

    find = ["find", shlex.quote(directory), "-maxdepth", "1", "-type", "f",
            "-name", shlex.quote(pattern)]
    if since is not None:
        find += ["-newermt", f"{since[:4]}-{since[4:6]}-{since[6:]}"]
    awk = ["LC_ALL=C", "awk", "-v", f"SINCE={since or 0}", "-v", f"MAXROWS={max_rows}",
           "-v", f"TOP404={top_404}", shlex.quote(program)]
    return " | ".join([" ".join(find) + " 2>/dev/null", "sort", _FILE_LOOP, " ".join(awk)])


def build_remote_command(server: ServerConfig, settings: Settings, since: str | None,
                         token: str) -> str:
    """SSH exec로 보낼 명령 전문. token은 취소용 식별자 (napi-scan-<hex8>)."""
    if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
        raise ValueError(f"취소 토큰 형식이 아니에요: {token!r}")
    timeout = _int("remote_timeout_sec", settings.remote_timeout_sec, 1)
    script = build_pipeline(server, settings, since)
    return (f"timeout {timeout} nice -n 19 {_IONICE} "
            f"sh -c {shlex.quote(script)} {shlex.quote(token)}")
