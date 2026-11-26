"""공격 탐지와 IP별 위험도 판정 (PLAN.md 5.6).

1. 레코드마다 시그니처로 태그를 붙인다 (SQLI, XSS, TRAVERSAL, RCE, JAVA, WEBSHELL, SENSITIVE, SCANNER, METHOD, UPLOAD)
2. IP(IOC)별로 레코드를 모아 등급을 정한다. 200으로 성공한 공격을 가장 위험하게 본다.
"""
import json
import re
from urllib.parse import unquote_plus

from .models import GRADE_RANK

ATTACK_TAGS = ("SQLI", "XSS", "TRAVERSAL", "RCE", "JAVA", "WEBSHELL")
SEVERE_TAGS = ("SQLI", "RCE", "JAVA")
TAG_LABELS = {
    "SQLI": "SQLi", "XSS": "XSS", "TRAVERSAL": "경로 조작", "RCE": "명령 실행", "JAVA": "Java 취약점",
    "WEBSHELL": "웹셸", "SENSITIVE": "민감파일", "SCANNER": "스캐너", "METHOD": "비정상 메서드",
    "UPLOAD": "업로드", "UNPARSED": "해석 실패",
}


class Signatures:
    def __init__(self, data: dict, upload_paths=()):
        self.url = {tag: [re.compile(p, re.I) for p in pats] for tag, pats in data.get("url", {}).items()}
        self.scanner_ua = [s.lower() for s in data.get("scanner_ua", [])]
        self.methods = {m.upper() for m in data.get("methods", [])}
        self.webshell_ext = tuple(e.lower() for e in data.get("webshell_ext", [".jsp", ".jspx"]))
        self.upload_paths = tuple(p.lower().rstrip("/") for p in upload_paths)
        self.version = str(data.get("version", "미지정"))

    @classmethod
    def load(cls, path, upload_paths=()):
        with open(path, encoding="utf-8-sig") as f:
            return cls(json.load(f), upload_paths)

    @property
    def rule_count(self) -> int:
        return sum(len(v) for v in self.url.values()) + len(self.scanner_ua) + len(self.methods)

    def in_upload_path(self, path: str) -> bool:
        p = path.lower()
        return any(p.startswith(u + "/") or ("/" + u.strip("/") + "/") in p for u in self.upload_paths)

    def is_script(self, path: str) -> bool:
        return path.lower().endswith(self.webshell_ext)

    def tag(self, rec) -> list:
        target = rec.path + ("?" + rec.query if rec.query else "")
        decoded = unquote_plus(unquote_plus(target)).lower()
        tags = [tag for tag, pats in self.url.items() if any(p.search(decoded) for p in pats)]
        path_l = unquote_plus(rec.path).lower()
        if self.in_upload_path(path_l) and self.is_script(path_l) and "WEBSHELL" not in tags:
            tags.append("WEBSHELL")
        if rec.method.upper() == "POST" and ("upload" in path_l or self.in_upload_path(path_l)) \
                and not self.is_script(path_l):
            tags.append("UPLOAD")
        ua = rec.user_agent.lower()
        # UA 필드가 있는데 '-'(헤더 없음)이면 도구일 가능성이 높다. 형식에 UA가 없으면 ''라서 검사하지 않는다.
        if ua == "-" or (ua and any(s in ua for s in self.scanner_ua)):
            tags.append("SCANNER")
        if rec.method.upper() in self.methods:
            tags.append("METHOD")
        return tags


def _ok(rec) -> bool:
    return rec.status is not None and 200 <= rec.status < 300


def judge(records: list, scan_404_threshold: int = 20):
    """한 IOC에 매칭된 레코드 목록으로 (등급, [판정 근거])를 정한다."""
    if not records:
        return "NONE", []
    recs = sorted(records, key=lambda r: (r.time is None, r.time))
    found = []  # (grade, reason)

    def add(grade, reason):
        if reason not in [r for _, r in found]:
            found.append((grade, reason))

    # 웹셸 업로드: 업로드 POST 2xx 이후 같은 IP가 스크립트 파일에 2xx
    upload_times = [r.time for r in recs if "UPLOAD" in r.tags and _ok(r)]
    if upload_times:
        first = min(t for t in upload_times if t) if any(upload_times) else None
        for r in recs:
            if "WEBSHELL" in r.tags and _ok(r) and (first is None or r.time is None or r.time >= first):
                add("CRITICAL", "웹셸 업로드 의심 (업로드 후 스크립트 파일 200)")
                break
    # 웹셸 동작: 웹셸 경로 2xx가 반복
    shell_paths = {}
    for r in recs:
        if "WEBSHELL" in r.tags and _ok(r):
            shell_paths[r.path] = shell_paths.get(r.path, 0) + 1
    if any(c >= 2 for c in shell_paths.values()):
        add("CRITICAL", "웹셸 동작 의심 (같은 경로 반복 200)")
    elif shell_paths and not upload_times:
        add("HIGH", "웹셸 의심 경로 200")
    # 공격 성공 의심
    for tag in SEVERE_TAGS:
        if any(tag in r.tags and _ok(r) for r in recs):
            add("CRITICAL", f"{TAG_LABELS[tag]} 공격 200 응답 (성공 의심)")
    for tag in ("XSS", "TRAVERSAL"):
        if any(tag in r.tags and _ok(r) for r in recs):
            add("HIGH", f"{TAG_LABELS[tag]} 공격 200 응답 (성공 가능)")
    if any("SENSITIVE" in r.tags and _ok(r) and r.bytes > 0 for r in recs):
        add("HIGH", "민감 파일 200 응답 (정보 노출 의심)")
    # 시도·탐색
    attack_fail = [r for r in recs if any(t in r.tags for t in ATTACK_TAGS) and not _ok(r)]
    if attack_fail:
        kinds = sorted({TAG_LABELS[t] for r in attack_fail for t in r.tags if t in ATTACK_TAGS})
        codes = sorted({str(r.status) for r in attack_fail if r.status})
        add("MEDIUM", f"{'·'.join(kinds)} 시도 ({'/'.join(codes)})")
    n5xx = sum(1 for r in recs if r.status and r.status >= 500)
    if n5xx:
        add("MEDIUM", f"서버 오류 유발 (5xx {n5xx}건)")
    n404 = sum(1 for r in recs if r.status == 404)
    if n404 >= scan_404_threshold:
        add("MEDIUM", f"경로 스캔 (404 {n404}건)")
    if any("SCANNER" in r.tags for r in recs):
        uas = sorted({r.user_agent for r in recs if "SCANNER" in r.tags})[:2]
        add("MEDIUM", "스캐너 도구 (" + ", ".join(u or "UA 없음" for u in uas) + ")")
    if any("METHOD" in r.tags for r in recs):
        add("MEDIUM", "비정상 메서드 (" + ", ".join(sorted({r.method for r in recs if "METHOD" in r.tags})) + ")")
    if not found:
        return "LOW", ["일반 접속"]
    found.sort(key=lambda x: GRADE_RANK[x[0]])
    return found[0][0], [r for _, r in found]
