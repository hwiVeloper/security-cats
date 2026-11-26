"""점검 이력 (요약만 자동 저장).

점검할 때마다 조건·결과 요약·근거 위치(파일·줄번호)·검토 상태·메모를 SQLite에 남긴다.
로그 원문(요청 URL 등)은 저장하지 않는다. 원문은 서버에 그대로 있고, 근거 위치로 다시 찾을 수 있다.
"""
import json
import sqlite3
from datetime import datetime

from .. import __version__, paths

REVIEW_STATES = ("검토 전", "확인 완료", "조치 필요")

SCHEMA = """
CREATE TABLE IF NOT EXISTS checks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    started TEXT NOT NULL,
    finished TEXT,
    user TEXT,
    period_start TEXT,
    period_end TEXT,
    iocs TEXT,           -- JSON: ["203.0.113.45", ...]
    servers TEXT,        -- JSON: [{name, state, reason, files, size, matched, parse_errors, server_filter}]
    counts TEXT,         -- JSON: summary_counts()
    results TEXT,        -- JSON: IP별 요약 + 근거 위치 (원문 없음)
    coverage TEXT,
    rule_version TEXT,
    app_version TEXT,
    review TEXT DEFAULT '검토 전',
    memo TEXT DEFAULT '',
    reviewed_at TEXT
);
"""


def _db_path():
    return paths.BASE / "data" / "history.db"


def connect():
    p = _db_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(p)
    con.row_factory = sqlite3.Row
    con.executescript(SCHEMA)
    return con


def _t(d):
    return d.isoformat(timespec="seconds") if d else None


def _payload(result):
    servers = [{"name": s.name, "state": s.state, "reason": s.reason, "files": len(s.files),
                "size": s.scanned_bytes, "matched": s.matched_lines, "parse_errors": s.parse_errors,
                "server_filter": s.server_filter} for s in result.servers]
    rows = []
    for s in result.summaries:
        rows.append({"ip": s.ioc_key, "grade": s.grade if s.accessed else "NONE", "requests": s.requests,
                     "c2xx": s.c2xx, "c4xx": s.c4xx, "c404": s.c404, "c5xx": s.c5xx, "c500": s.c500,
                     "verdict": s.verdict, "servers": s.server_counts,
                     "first": _t(s.first_seen), "last": _t(s.last_seen),
                     "evidence": [[sv, f, ln, _t(t)] for sv, f, ln, t in s.evidence]})
    return servers, rows


def save_result(result, user: str = "") -> int:
    """점검 결과 요약을 저장하고 id를 돌려준다. 같은 결과(다시 점검 후 합침)면 갱신한다."""
    servers, rows = _payload(result)
    r = result.request
    vals = dict(started=_t(result.started), finished=_t(result.finished), user=user,
                period_start=_t(r.start), period_end=_t(r.end),
                iocs=json.dumps([e.key for e in r.iocs], ensure_ascii=False),
                servers=json.dumps(servers, ensure_ascii=False), counts=json.dumps(result.summary_counts(), ensure_ascii=False),
                results=json.dumps(rows, ensure_ascii=False), coverage=result.coverage_text(),
                rule_version=result.rule_version, app_version=__version__)
    with connect() as con:
        if result.history_id:
            sets = ", ".join(f"{k} = :{k}" for k in vals)
            con.execute(f"UPDATE checks SET {sets} WHERE id = :id", dict(vals, id=result.history_id))
        else:
            cols = ", ".join(vals)
            result.history_id = con.execute(
                f"INSERT INTO checks ({cols}) VALUES ({', '.join(':' + k for k in vals)})", vals).lastrowid
    return result.history_id


def list_checks(ip: str = "", limit: int = 200) -> list:
    with connect() as con:
        if ip:
            rows = con.execute("SELECT * FROM checks WHERE iocs LIKE ? ORDER BY id DESC LIMIT ?",
                               (f'%"{ip}%', limit)).fetchall()
        else:
            rows = con.execute("SELECT * FROM checks ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
    return [_decode(r) for r in rows]


def get(check_id: int):
    with connect() as con:
        r = con.execute("SELECT * FROM checks WHERE id = ?", (check_id,)).fetchone()
    return _decode(r) if r else None


def update_review(check_id: int, review: str, memo: str) -> None:
    if review not in REVIEW_STATES:
        raise ValueError(review)
    with connect() as con:
        con.execute("UPDATE checks SET review = ?, memo = ?, reviewed_at = ? WHERE id = ?",
                    (review, memo, datetime.now().isoformat(timespec="seconds"), check_id))


def delete(check_id: int) -> None:
    with connect() as con:
        con.execute("DELETE FROM checks WHERE id = ?", (check_id,))


def ip_history(ip: str) -> dict:
    """이 IP가 점검 대상이 된 횟수와 로그에서 발견된 점검."""
    checks = [c for c in list_checks(ip, 1000) if ip in c["iocs"]]
    hits = []
    for c in checks:
        for r in c["results"]:
            if r["ip"] == ip and r["requests"]:
                hits.append((c, r))
    return {"count": len(checks), "first": checks[-1]["started"] if checks else None,
            "last": checks[0]["started"] if checks else None, "hits": hits}


def _decode(r) -> dict:
    d = dict(r)
    for k in ("iocs", "servers", "counts", "results"):
        d[k] = json.loads(d[k] or "null") or ([] if k != "counts" else {})
    return d
