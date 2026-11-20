"""원격 서버에서 실행할 셸 명령을 조립한다.

모양 (Global Constraints, Ruling R2):
    timeout <sec> nice -n 19 $(ionice가 있으면 ionice -c3) sh -c '<pipeline>' <token>
취소 토큰은 sh -c의 $0으로 넘겨 timeout과 sh의 argv에 보이게 한다 (pkill -f <token>).
pipeline 안에서도 "$0"(토큰)을 파일 표시 줄과 awk의 MARK로 쓴다. 로그 내용은 토큰을
알 수 없으므로 표시 줄을 위조할 수 없다.
명령은 읽기 전용이다. 쓰는 리다이렉션은 >/dev/null, 2>/dev/null 뿐이다.
사용자 값은 모두 shlex.quote로 감싸거나 정수·형식 검사를 거친다.
"""
from __future__ import annotations

import re
import shlex
from collections.abc import Sequence

from napi.models import LogFormatDef, ServerConfig, Settings
from napi.remote.awk_program import awk_program

_TOKEN_RE = re.compile(r"napi-scan-[0-9a-f]{8}")
_SINCE_RE = re.compile(r"[0-9]{8}")
_GLOB_CHARS = "*?["

_IONICE = "$(command -v ionice >/dev/null 2>/dev/null && echo ionice -c3)"
# 표시 줄 앞의 \n은 마지막 줄에 개행이 없는 파일과 표시가 붙지 않게 한다 (awk가 하나를 무시).
_FILE_LOOP = (
    "while IFS= read -r f; do "
    r"""if [ ! -r "$f" ]; then printf '\n\001E%s\tunreadable\t%s\n' "$0" "$f"; continue; fi; """
    r"""printf '\n\001F%s\t%s\n' "$0" "$f"; """
    r"""gzip -dcf -- "$f" 2>/dev/null || printf '\n\001E%s\tdecompress_failed\t%s\n' "$0" "$f"; """
    "done")


def split_glob(log_glob: str) -> tuple[str, str]:
    """"/webtob/log/access.log*" -> ("/webtob/log", "access.log*").

    절대 경로만 받고, 디렉터리 부분에는 glob 문자를 허용하지 않는다. 그래서 find에
    넘기는 디렉터리는 항상 "/"로 시작하고 옵션(-delete 등)으로 읽힐 수 없다.
    """
    if not isinstance(log_glob, str) or not log_glob.startswith("/"):
        raise ValueError(f"로그 경로는 /로 시작하는 절대 경로여야 해요: {log_glob!r}")
    directory, _, pattern = log_glob.rpartition("/")
    directory = directory or "/"
    if any(ch in directory for ch in _GLOB_CHARS):
        raise ValueError(f"디렉터리 부분에는 * ? [ 를 쓸 수 없어요: {log_glob!r}")
    if pattern == "":
        raise ValueError(f"파일 이름 패턴이 비어 있어요: {log_glob!r}")
    return directory, pattern


def _int(name: str, value: object, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"{name}은(는) {minimum} 이상의 정수여야 해요: {value!r}")
    return value


def _check_since(since: str | None) -> None:
    if since is not None and not _SINCE_RE.fullmatch(since):
        raise ValueError(f"since는 YYYYMMDD 형식이어야 해요: {since!r}")


def _check_token(token: str) -> None:
    if not isinstance(token, str) or not _TOKEN_RE.fullmatch(token):
        raise ValueError(f"취소 토큰 형식이 아니에요: {token!r}")


def build_pipeline(server: ServerConfig, settings: Settings, since: str | None,
                   token: str, formats: Sequence[LogFormatDef] = ()) -> str:
    """sh -c로 실행할 본문 (find | 파일별 gzip -dcf | awk).

    본문은 토큰을 "$0"으로 읽는다. 반드시 `sh -c <본문> <token>`으로 실행해야 한다.
    formats는 사용자 정의 형식 목록이다. server.log_format을 모르면 FormatError(ValueError).
    """
    _check_token(token)
    _check_since(since)
    max_rows = _int("max_rows", settings.max_rows, 1)  # MAXKEYS = 4 × max_rows ≥ 4
    top_404 = _int("top_404", settings.top_404, 0)
    program = awk_program(server.log_format, server.custom_format, formats)
    directory, pattern = split_glob(server.log_glob)

    find = ["find", shlex.quote(directory), "-maxdepth", "1", "-type", "f",
            "-name", shlex.quote(pattern)]
    if since is not None:
        find += ["-newermt", f"{since[:4]}-{since[4:6]}-{since[6:]}"]
    awk = ["LC_ALL=C", "awk", "-v", f"SINCE={since or 0}", "-v", f"MAXROWS={max_rows}",
           "-v", f"MAXKEYS={4 * max_rows}", "-v", f"TOP404={top_404}",
           "-v", 'MARK="$0"', shlex.quote(program)]
    return " | ".join([" ".join(find) + " 2>/dev/null", "sort", _FILE_LOOP, " ".join(awk)])


def build_remote_command(server: ServerConfig, settings: Settings, since: str | None,
                         token: str, formats: Sequence[LogFormatDef] = ()) -> str:
    """SSH exec로 보낼 명령 전문. token은 취소용 식별자 (napi-scan-<hex8>)."""
    timeout = _int("remote_timeout_sec", settings.remote_timeout_sec, 1)
    script = build_pipeline(server, settings, since, token, formats)
    return (f"timeout {timeout} nice -n 19 {_IONICE} "
            f"sh -c {shlex.quote(script)} {shlex.quote(token)}")
