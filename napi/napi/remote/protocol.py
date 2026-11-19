"""원격 집계 stdout(탭 구분 프로토콜)을 ServerScanResult로 바꾼다."""
from __future__ import annotations

from napi.models import AggRow, FileStat, ServerScanResult

_BAD_FORMAT = "집계 결과 형식이 올바르지 않아요"
_STDERR_LIMIT = 200


def _date(s: str) -> str | None:
    return None if s == "-" else s


def parse_remote_output(server: str, stdout: str, stderr: str,
                        exit_code: int, elapsed_sec: float) -> ServerScanResult:
    res = ServerScanResult(server=server, ok=False, elapsed_sec=elapsed_sec)
    done = False
    bad_marker = False
    malformed = False
    lines = stdout.split("\n")
    if lines and lines[-1] == "":
        lines.pop()
    for line in lines:
        parts = line.split("\t")
        tag = parts[0]
        try:
            if tag == "F":
                if len(parts) != 7:
                    raise ValueError
                res.files.append(FileStat(parts[1], _date(parts[2]), _date(parts[3]),
                                          int(parts[4]), int(parts[5]), int(parts[6])))
            elif tag == "R":
                if len(parts) != 7:
                    raise ValueError
                res.rows.append(AggRow(parts[1], parts[2], parts[3], int(parts[4]),
                                       parts[5], parts[6]))
            elif tag == "P":
                if len(parts) != 6:
                    raise ValueError
                res.probe404.append(AggRow(parts[1], parts[2], "4xx", int(parts[3]),
                                           parts[4], parts[5]))
            elif tag == "Q":
                if len(parts) != 3:
                    raise ValueError
                res.probe404_rest = (int(parts[1]), int(parts[2]))
            elif tag == "T":
                res.truncated = True
            elif tag == "D":
                done = True
            elif tag == "E":
                msg = parts[1] if len(parts) > 1 else ""
                if msg == "no files matched":
                    pass
                elif msg == "bad marker":
                    bad_marker = True
                elif msg in ("unreadable", "decompress_failed") and len(parts) == 3:
                    res.file_errors.append((msg, parts[2]))
                else:
                    res.file_errors.append((msg, ""))
            # 알 수 없는 태그: 내용을 어디에도 담지 않고 버린다.
        except ValueError:
            malformed = True

    if exit_code == 124:
        error = "시간 초과"
    elif bad_marker:
        error = "집계 프로그램 호출이 잘못됐어요"
    elif malformed:
        error = _BAD_FORMAT
    elif not done:
        error = "집계가 끝까지 실행되지 않았어요"
    else:
        res.ok = True
        return res
    if stderr.strip():
        error += " — " + stderr[:_STDERR_LIMIT]
    res.error = error
    return res
