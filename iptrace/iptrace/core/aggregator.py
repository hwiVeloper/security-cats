"""IOC별 집계 (PLAN.md 5.7). 미접속 IOC도 반드시 포함한다."""
from collections import Counter

from .detector import judge
from .models import GRADE_RANK, IpSummary


def summarize(iocs: list, records: list, scan_404_threshold: int = 20) -> list:
    by_key = {}
    for r in records:
        by_key.setdefault(r.ioc_key, []).append(r)
    out = []
    for e in iocs:
        recs = by_key.get(e.key, [])
        s = IpSummary(ioc_key=e.key, original=e.original, is_range=e.is_range)
        if recs:
            s.requests = len(recs)
            for r in recs:
                st = r.status or 0
                if 200 <= st < 300:
                    s.c2xx += 1
                elif 300 <= st < 400:
                    s.c3xx += 1
                elif 400 <= st < 500:
                    s.c4xx += 1
                    s.c404 += st == 404
                elif st >= 500:
                    s.c5xx += 1
                    s.c500 += st == 500
            times = [r.time for r in recs if r.time]
            s.first_seen, s.last_seen = (min(times), max(times)) if times else (None, None)
            s.servers = sorted({r.server for r in recs})
            s.matched_ips = sorted({r.client_ip for r in recs})
            s.matched_fields = sorted({r.matched_field for r in recs})
            s.methods = dict(Counter(r.method for r in recs))
            urls = Counter(r.url for r in recs)
            s.top_urls = urls.most_common(10)
            s.unique_urls = len(urls)
            s.user_agents = [ua for ua, _ in Counter(r.user_agent for r in recs).most_common(5)]
            s.tag_counts = dict(Counter(t for r in recs for t in r.tags))
            s.server_counts = dict(Counter(r.server for r in recs))
            ordered = sorted(recs, key=lambda r: (r.time is None, r.time))
            s.evidence = [(r.server, r.file, r.line_no, r.time) for r in ordered[:3]]
            parsed = [r for r in recs if not r.unparsed]
            unparsed = len(recs) - len(parsed)
            if parsed:
                s.grade, s.reasons = judge(parsed, scan_404_threshold)
            else:
                s.grade, s.reasons = "LOW", []
            if unparsed:
                # 형식과 맞지 않는 줄은 요청·응답을 모르므로 판정 없이 원문 확인을 안내한다
                note = f"형식이 맞지 않는 줄에서 발견 {unparsed}건 (원문 확인 필요)"
                s.reasons = [x for x in s.reasons if x != "일반 접속"] + [note] if parsed else [note]
            if s.matched_fields == ["xff"]:
                s.reasons.append("XFF로만 매칭 (신뢰도 중)")
        out.append(s)
    out.sort(key=lambda x: (GRADE_RANK[x.grade], -x.requests, x.ioc_key))
    return out
