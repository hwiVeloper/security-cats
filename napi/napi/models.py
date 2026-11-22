"""napi 전체에서 쓰는 dataclass/Enum."""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Literal


@dataclass
class PathMapping:
    strip_prefix: str = ""
    add_prefix: str = ""


@dataclass
class Thresholds:
    awake_days: int = 30
    min_coverage_days: int = 90


@dataclass
class SpecSource:
    type: str = "openapi"
    source: Literal["url", "file"] = "url"
    location: str = ""


@dataclass
class CustomFormat:
    """필드 번호는 awk $N 기준 (1부터)."""
    method_field: int
    path_field: int
    status_field: int
    time_field: int
    time_format: Literal["clf", "iso8601"]


@dataclass
class LogFormatDef:
    """사용자 정의 로그 형식. pattern은 Apache LogFormat 문법."""
    name: str
    pattern: str


@dataclass
class LogPath:
    """로그 경로 하나와 그 경로의 파일을 읽을 형식.
    glob은 절대 경로 + 파일 이름 패턴, log_format은 형식 이름 ("custom"이면 custom_format)."""
    glob: str
    log_format: str = "webtob"
    custom_format: CustomFormat | None = None


@dataclass
class ServerConfig:
    name: str
    host: str
    user: str
    # 한 파일이 경로 두 개 이상에 걸리면 목록에서 먼저 나온 경로의 형식으로 한 번만 읽는다
    log_paths: list[LogPath]
    port: int = 22
    auth: Literal["password", "key"] = "password"
    key_file: str | None = None


@dataclass
class Note:
    reason: str
    author: str
    date: str


@dataclass
class ServiceConfig:
    name: str
    spec: SpecSource
    servers: list[ServerConfig]
    path_mapping: PathMapping = field(default_factory=PathMapping)
    thresholds: Thresholds = field(default_factory=Thresholds)
    exclude_paths: list[str] = field(default_factory=list)
    notes: dict[str, Note] = field(default_factory=dict)  # 키: "GET /v1/x"


@dataclass
class Settings:
    max_parallel_servers: int = 2
    default_window: str = "365"
    remote_timeout_sec: int = 3600
    max_rows: int = 50000
    top_404: int = 200


@dataclass
class AppConfig:
    services: list[ServiceConfig]
    settings: Settings = field(default_factory=Settings)
    version: int = 1
    log_formats: list[LogFormatDef] = field(default_factory=list)


@dataclass
class SpecOperation:
    method: str
    path: str
    operation_id: str | None
    tags: list[str]
    summary: str | None
    deprecated: bool

    @property
    def key(self) -> str:
        return f"{self.method} {self.path}"


@dataclass
class AggRow:
    method: str
    path: str
    status_class: str
    count: int
    first: str
    last: str


@dataclass
class FileStat:
    path: str
    min_date: str | None
    max_date: str | None
    lines: int
    parse_fail: int
    out_of_range: int


@dataclass
class ServerScanResult:
    server: str
    ok: bool
    error: str | None = None
    rows: list[AggRow] = field(default_factory=list)
    probe404: list[AggRow] = field(default_factory=list)
    probe404_rest: tuple[int, int] = (0, 0)
    files: list[FileStat] = field(default_factory=list)
    truncated: bool = False
    elapsed_sec: float = 0.0
    # (코드, 경로): unreadable / decompress_failed 등 원격 측 파일 오류,
    # ("glob_empty", glob): 다른 glob에는 파일이 있는데 이 glob만 비었음
    file_errors: list[tuple[str, str]] = field(default_factory=list)

    @property
    def oldest_date(self) -> str | None:
        dates = [f.min_date for f in self.files if f.min_date is not None]
        return min(dates) if dates else None

    @property
    def newest_date(self) -> str | None:
        dates = [f.max_date for f in self.files if f.max_date is not None]
        return max(dates) if dates else None

    @property
    def total_lines(self) -> int:
        return sum(f.lines for f in self.files)

    @property
    def total_parse_fail(self) -> int:
        return sum(f.parse_fail for f in self.files)


class Status(str, Enum):
    AWAKE = "awake"
    DROWSY = "drowsy"
    ASLEEP = "asleep"
    RIP = "rip"
    SLEEPTALK = "sleeptalk"
    GHOST = "ghost"
    PROBE = "probe"


@dataclass
class ApiResult:
    method: str
    path: str
    status: Status
    calls: int
    first: str | None
    last: str | None
    by_status: dict[str, int]
    by_server: dict[str, int]
    in_spec: bool
    deprecated: bool = False
    tags: list[str] = field(default_factory=list)
    summary: str | None = None
    operation_id: str | None = None
    note: Note | None = None


@dataclass
class Reliability:
    level: Literal["ok", "low"]
    coverage_days: int
    reasons: list[str]


@dataclass
class ServerSummary:
    name: str
    ok: bool
    error: str | None
    oldest: str | None
    newest: str | None
    elapsed_sec: float
    truncated: bool
    # 읽은 로그 줄 수와 그중 형식이 맞지 않아 건너뛴 줄 수 (예전 결과 파일에는 없어서 기본 0)
    lines: int = 0
    parse_fail: int = 0


@dataclass
class ServiceResult:
    service: str
    scanned_at: str
    window: str
    since: str | None
    until: str
    spec_location: str
    summary: str
    apis: list[ApiResult]
    counts: dict[str, int]
    sleeping: int
    sleeping_noted: int
    reliability: Reliability
    servers: list[ServerSummary]
    probe_rest: dict[str, int]
    # 판정에 쓴 기준 (예전 결과 파일에는 없어서 기본값)
    awake_days: int = 30
    min_coverage_days: int = 90
