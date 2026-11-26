"""엔진·화면·보고서가 함께 쓰는 데이터 구조."""
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

GRADES = ("CRITICAL", "HIGH", "MEDIUM", "LOW", "NONE")
GRADE_RANK = {g: i for i, g in enumerate(GRADES)}  # 숫자가 작을수록 위험
NOT_FOUND = "조회 범위 내 미발견"   # "미접속"이라고 단정하지 않는다 (못 본 서버·구간이 있을 수 있음)


@dataclass
class IocEntry:
    network: object            # ipaddress.IPv4Network / IPv6Network
    original: str              # 사용자가 적은 원문 토큰
    line_no: int
    count: int = 1             # 같은 IP를 몇 번 적었는지

    @property
    def key(self) -> str:
        n = self.network
        return str(n.network_address) if n.num_addresses == 1 else str(n)

    @property
    def is_range(self) -> bool:
        return self.network.num_addresses > 1


@dataclass
class IocParseResult:
    entries: list = field(default_factory=list)       # [IocEntry]
    duplicates: int = 0
    excluded: list = field(default_factory=list)      # [(line_no, value, reason)]
    errors: list = field(default_factory=list)        # [(line_no, line_text, bad_token)]
    warnings: list = field(default_factory=list)      # [str]

    @property
    def error_lines(self) -> list:
        return sorted({e[0] for e in self.errors})


@dataclass
class FileInfo:
    server: str
    path: str
    size: int
    mtime: float
    sha256: str = ""
    lines: int = 0               # 전체 줄 수 (로컬만. 서버 필터를 쓰면 알 수 없어 0)
    matched: int = 0             # 서버에서 걸러 받은 줄 수
    first_time: Optional[datetime] = None
    limit: str = ""              # 끝까지 확인하지 못한 이유 (줄 수 상한 / 시간 제한). 비어 있으면 끝까지 확인
    log_format: str = ""         # 이 파일을 찾은 로그 경로의 형식 이름 (비어 있으면 서버 기본 형식)


@dataclass
class LogRecord:
    server: str
    file: str
    line_no: int
    raw: str
    remote_ip: str
    xff: list
    time: Optional[datetime]
    method: str
    path: str
    query: str
    protocol: str
    status: Optional[int]
    bytes: int
    referer: str
    user_agent: str
    ioc_key: str = ""            # 어느 IOC에 매칭됐는지
    client_ip: str = ""          # 실제로 매칭된 IP
    matched_field: str = ""      # remote / xff
    tags: list = field(default_factory=list)

    @property
    def url(self) -> str:
        return self.path + ("?" + self.query if self.query else "")

    @property
    def unparsed(self) -> bool:
        """로그 형식과 맞지 않아 IP·시간만 찾아 넣은 기록"""
        return "UNPARSED" in self.tags


@dataclass
class ServerStatus:
    name: str
    ok: bool = True
    reason: str = ""
    files: list = field(default_factory=list)   # [FileInfo]
    matched_lines: int = 0
    total_lines: int = 0         # 로컬만. 서버 필터를 쓰면 0
    candidate_lines: int = 0     # 서버에서 걸러 받은(또는 로컬에서 필터에 걸린) 줄 수
    parse_errors: int = 0
    parse_errors_ip: int = 0     # 해석 실패 줄 중 의심 IP 문자열이 든 줄 (놓친 기록일 수 있음)
    unparsed_matched: int = 0    # 해석 실패 줄 중 의심 IP와 정확히 맞아 결과에 '해석 실패'로 넣은 줄
    parse_error_lines: list = field(default_factory=list)   # [(파일, 줄번호, 원문, 의심 IP 포함 여부)] 최대 50줄
    server_filter: bool = False
    oldest_time: Optional[datetime] = None

    @property
    def scanned_bytes(self) -> int:
        return sum(f.size for f in self.files)

    @property
    def limited_files(self) -> list:
        """줄 수 상한·시간 제한 때문에 끝까지 확인하지 못한 파일"""
        return [f for f in self.files if f.limit]

    @property
    def state(self) -> str:
        """완료 / 부분 분석(해석 실패 또는 끝까지 못 본 파일 있음) / 연결 실패"""
        if not self.ok:
            return "연결 실패" if any(k in self.reason for k in ("접속", "인증", "호스트 키")) else "수집 실패"
        return "부분 분석" if self.parse_errors or self.limited_files else "완료"

    def detail_text(self) -> str:
        """서버 상태 한 줄 설명 (화면·보고서 공용)"""
        if not self.ok:
            return f"{self.state}: {self.reason}"
        t = f"{self.state} · 파일 {len(self.files)}개 · 매칭 {self.matched_lines}줄"
        if self.parse_errors:
            t += f" · 해석 실패 {self.parse_errors}줄"
        lim = self.limited_files
        if lim:
            reasons = ", ".join(sorted({f.limit for f in lim}))
            t += f" · 끝까지 못 본 파일 {len(lim)}개({reasons})"
        return t


@dataclass
class IpSummary:
    ioc_key: str
    original: str
    is_range: bool
    requests: int = 0
    c2xx: int = 0
    c3xx: int = 0
    c4xx: int = 0
    c404: int = 0
    c5xx: int = 0
    c500: int = 0
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None
    servers: list = field(default_factory=list)
    matched_ips: list = field(default_factory=list)
    matched_fields: list = field(default_factory=list)
    methods: dict = field(default_factory=dict)
    top_urls: list = field(default_factory=list)       # [(url, count)]
    unique_urls: int = 0
    user_agents: list = field(default_factory=list)
    tag_counts: dict = field(default_factory=dict)
    grade: str = "NONE"
    reasons: list = field(default_factory=list)
    server_counts: dict = field(default_factory=dict)   # {서버: 요청 수}
    evidence: list = field(default_factory=list)        # [(서버, 파일, 줄번호, 시각)] 처음 몇 건

    @property
    def accessed(self) -> bool:
        return self.requests > 0

    @property
    def confidence(self) -> str:
        return "중 (XFF로만 매칭)" if self.matched_fields == ["xff"] else ""

    @property
    def verdict(self) -> str:
        return " · ".join(self.reasons) if self.reasons else (NOT_FOUND if not self.accessed else "단순 접속")


@dataclass
class CheckRequest:
    iocs: list                       # [IocEntry]
    start: datetime
    end: datetime
    servers: list = field(default_factory=list)       # [서버 설정 dict] (원격)
    local_logs: list = field(default_factory=list)    # [(server_name, glob)] (로컬)
    log_format: str = "webtob_combined"               # 로컬 로그 형식 이름 또는 Format 문자열
    passwords: dict = field(default_factory=dict)     # {server_name: password}  메모리 전용
    ioc_text_hash: str = ""


@dataclass
class CheckResult:
    request: CheckRequest
    started: datetime
    finished: Optional[datetime] = None
    servers: list = field(default_factory=list)       # [ServerStatus]
    records: list = field(default_factory=list)       # [LogRecord]
    summaries: list = field(default_factory=list)     # [IpSummary]
    parse_errors: int = 0
    parse_error_samples: list = field(default_factory=list)
    saved_path: str = ""
    rule_version: str = ""
    history_id: Optional[int] = None
    ai_notes: dict = field(default_factory=dict)      # {ioc_key: AiNote}  [AI 분석]을 누른 IP만

    @property
    def failed_servers(self) -> list:
        return [s for s in self.servers if not s.ok]

    def coverage_text(self) -> str:
        """분석 범위 한 줄 요약: 어디까지 봤고 무엇을 못 봤는지."""
        ok = [s for s in self.servers if s.ok]
        files = sum(len(s.files) for s in ok)
        size = sum(s.scanned_bytes for s in ok)
        parts = [f"서버 {len(ok)}/{len(self.servers)}", f"로그 파일 {files}개({_size(size)})"]
        if any(s.server_filter for s in ok):
            parts.append("서버에서 의심 IP 줄만 걸러 분석")
        parts.append(f"해석 실패 {self.parse_errors}줄")
        limited = sum(len(s.limited_files) for s in ok)
        if limited:
            parts.append(f"끝까지 못 본 파일 {limited}개")
        missing = [s.name for s in self.servers if not s.ok]
        if missing:
            parts.append(f"{', '.join(missing)} 미확인")
        return " · ".join(parts)

    def summary_counts(self) -> dict:
        s = self.summaries
        return {
            "iocs": len(s),
            "accessed": sum(1 for x in s if x.accessed),
            "not_accessed": sum(1 for x in s if not x.accessed),
            "with_404": sum(1 for x in s if x.c404),
            "with_500": sum(1 for x in s if x.c500),
            "danger": sum(1 for x in s if x.grade in ("CRITICAL", "HIGH")),
            "servers_ok": sum(1 for x in self.servers if x.ok),
            "servers": len(self.servers),
            "grades": {g: sum(1 for x in s if x.grade == g) for g in GRADES},
        }


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.0f}{unit}" if unit == "B" else f"{n:.1f}{unit}"
        n /= 1024
    return str(n)
