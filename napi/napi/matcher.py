"""집계된 로그 경로를 OpenAPI 경로 템플릿에 매칭한다."""
from __future__ import annotations

from dataclasses import dataclass, field

from napi.logformat import is_variable_segment
from napi.models import ANY_METHOD, AggRow, PathMapping, SpecOperation

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


_STAR = "{*}"


def _match_template(tpl: list[str], segs: list[str]) -> tuple[int, bool] | None:
    """일치하면 (리터럴 조각 수, 정규화 리터럴로 맞았는지), 아니면 None.

    - `{param}` 조각은 아무 조각과 일치한다 (로그의 `{*}` 포함).
    - 리터럴 조각은 같은 문자열과 일치한다. 그리고 원격 정규화 규칙
      (napi.logformat.is_variable_segment)이 그 리터럴을 `{*}`로 바꾸는 경우에는
      로그의 `{*}`와도 일치한다 (예: `retrieveAccountBalanceV2`, `2024`). 서버에서 이미
      `{*}`로 뭉개졌기 때문이다. 이것을 "정규화 리터럴 매칭"이라 부르고 두 번째 값이 True가 된다.
    - 정규화 리터럴 매칭은 우선순위용 리터럴 수에 넣지 않는다. `{*}` 행은 그 리터럴인지
      같은 자리의 `{param}`(또는 다른 정규화 리터럴)인지 알 수 없기 때문이다. 동률 처리는 match() 참고.
    """
    if len(tpl) != len(segs):
        return None
    literals = 0
    via_normalized = False
    for t, s in zip(tpl, segs):
        if _is_param(t):
            continue
        if t == s:
            literals += 1
        elif s == _STAR and is_variable_segment(t):
            via_normalized = True
        else:
            return None
    return literals, via_normalized


def match(
    server_rows: dict[str, list[AggRow]],
    ops: list[SpecOperation],
    mapping: PathMapping,
    exclude: list[str],
) -> MatchResult:
    """서버별 집계 행을 명세 오퍼레이션에 배정한다.

    한 행은 메서드가 같고 템플릿이 맞는 후보 중 리터럴 조각 수가 가장 많은 후보에 간다
    (`/users/me` 행은 `/users/{id}`가 아니라 `/users/me`로). 최고 리터럴 수가 같은 후보가
    여럿이면:

    - 동률 후보 중 하나라도 정규화 리터럴 매칭(로그 `{*}` ↔ is_variable_segment 리터럴)으로
      맞았다면, 그 행의 통계 전체를 동률 후보 **모두**에 기록한다. 서버에서 이미 뭉개진 `{*}`
      행은 어느 API의 호출인지 알 수 없는데, 하나만 고르면 정렬된 명세에서는 리터럴
      (`retrieveAccountBalanceV2`)이 늘 `{accountNo}`보다 앞서 살아 있는 API가 "잠듦"(삭제
      권고)으로 나온다. 거짓 "깨어 있음"이 거짓 "잠듦"보다 낫다.
    - 그렇지 않으면(평범한 `{param}`끼리의 동률 등) 기존처럼 스펙 순서상 먼저 나온 후보에만 기록한다.

    그래서 오퍼레이션별 calls 합계가 실제 로그 호출 수보다 클 수 있다 (의도된 동작).
    """
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
            tied: list[str] = []  # 최고 리터럴 수 후보들의 키, 스펙 순서
            best_lit = -1
            tied_normalized = False
            for op, tpl in templates:
                exact = op.method == r.method
                if not exact and op.method != ANY_METHOD:
                    continue
                m = _match_template(tpl, segs)
                if m is None:
                    continue
                lit, via_normalized = m
                # 같은 리터럴 수면 메서드가 정확히 같은 후보가 ANY(모든 메서드) 후보보다 우선한다
                lit = lit * 2 + exact
                if lit > best_lit:
                    tied, best_lit, tied_normalized = [op.key], lit, via_normalized
                elif lit == best_lit and op.key not in tied:
                    tied.append(op.key)
                    tied_normalized = tied_normalized or via_normalized
            if tied:
                for key in tied if tied_normalized else tied[:1]:
                    result.per_op[key].add(server, r)
            else:
                result.unmatched.setdefault((r.method, path), MatchedStats()).add(server, r)
    return result
