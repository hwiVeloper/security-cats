"""집계된 로그 경로를 OpenAPI 경로 템플릿에 매칭한다."""
from __future__ import annotations

from dataclasses import dataclass, field

from napi.models import AggRow, PathMapping, SpecOperation

_DROPPED_METHODS = {"HEAD", "OPTIONS"}


@dataclass
class MatchedStats:
    calls: int = 0
    first: str | None = None
    last: str | None = None
    by_status: dict[str, int] = field(default_factory=dict)
    by_server: dict[str, int] = field(default_factory=dict)

    def add(self, server: str, r: AggRow) -> None:
        self.calls += r.count
        self.first = r.first if self.first is None else min(self.first, r.first)
        self.last = r.last if self.last is None else max(self.last, r.last)
        self.by_status[r.status_class] = self.by_status.get(r.status_class, 0) + r.count
        self.by_server[server] = self.by_server.get(server, 0) + r.count


@dataclass
class MatchResult:
    per_op: dict[str, MatchedStats] = field(default_factory=dict)
    unmatched: dict[tuple[str, str], MatchedStats] = field(default_factory=dict)
    excluded_ops: set[str] = field(default_factory=set)


def _norm(path: str) -> str:
    if len(path) > 1:
        path = path.rstrip("/")
    return path or "/"


def apply_mapping(path: str, m: PathMapping) -> str:
    path = _norm(path)
    strip = m.strip_prefix.rstrip("/")
    if strip and (path == strip or path.startswith(strip + "/")):
        path = path[len(strip):] or "/"
    add = m.add_prefix.rstrip("/")
    if add:
        path = add + (path if path != "/" else "")
    return _norm(path)


def _segments(path: str) -> list[str]:
    return [s for s in path.split("/") if s]


def _seg_glob(pat: str, seg: str) -> bool:
    """세그먼트 안에서만 `*`가 임의 문자열과 일치한다."""
    parts = pat.split("*")
    if len(parts) == 1:
        return pat == seg
    if len(seg) < len(parts[0]) + len(parts[-1]):
        return False
    if not seg.startswith(parts[0]) or not seg.endswith(parts[-1]):
        return False
    pos = len(parts[0])
    end = len(seg) - len(parts[-1])
    for mid in parts[1:-1]:
        i = seg.find(mid, pos, end)
        if i < 0:
            return False
        pos = i + len(mid)
    return True


def _glob(pat: list[str], segs: list[str]) -> bool:
    if not pat:
        return not segs
    if pat[0] == "**":
        return any(_glob(pat[1:], segs[i:]) for i in range(len(segs) + 1))
    return bool(segs) and _seg_glob(pat[0], segs[0]) and _glob(pat[1:], segs[1:])


def _excluded(path: str, patterns: list[list[str]]) -> bool:
    segs = _segments(path)
    return any(_glob(p, segs) for p in patterns)


def _is_param(seg: str) -> bool:
    inner = seg[1:-1]
    return seg.startswith("{") and seg.endswith("}") and bool(inner) and "{" not in inner and "}" not in inner


def _match_template(tpl: list[str], segs: list[str]) -> int | None:
    """일치하면 리터럴 조각 수, 아니면 None."""
    if len(tpl) != len(segs):
        return None
    literals = 0
    for t, s in zip(tpl, segs):
        if _is_param(t):
            continue
        if t != s:
            return None
        literals += 1
    return literals


def match(
    server_rows: dict[str, list[AggRow]],
    ops: list[SpecOperation],
    mapping: PathMapping,
    exclude: list[str],
) -> MatchResult:
    patterns = [_segments(p) for p in exclude]
    result = MatchResult()
    templates: list[tuple[SpecOperation, list[str]]] = []
    for op in ops:
        if _excluded(_norm(op.path), patterns):
            result.excluded_ops.add(op.key)
            continue
        result.per_op[op.key] = MatchedStats()
        templates.append((op, _segments(_norm(op.path))))

    for server, rows in server_rows.items():
        for r in rows:
            if r.method.upper() in _DROPPED_METHODS:
                continue
            path = apply_mapping(r.path, mapping)
            if _excluded(path, patterns):
                continue
            segs = _segments(path)
            best: SpecOperation | None = None
            best_lit = -1
            for op, tpl in templates:
                if op.method != r.method:
                    continue
                lit = _match_template(tpl, segs)
                if lit is not None and lit > best_lit:  # 동률이면 스펙 순서상 먼저
                    best, best_lit = op, lit
            if best is not None:
                result.per_op[best.key].add(server, r)
            else:
                result.unmatched.setdefault((r.method, path), MatchedStats()).add(server, r)
    return result
