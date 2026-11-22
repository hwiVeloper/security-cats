"""pywebview js_api 브리지. UI(JS)가 window.pywebview.api.<메서드>로 부른다.

모든 공개 메서드는 JSON으로 바꿀 수 있는 값을 돌려주고 예외를 밖으로 던지지 않는다.
예외는 {"ok": False, "error": "<한국어>"}가 된다. 비밀번호·passphrase는 어떤 반환값에도
넣지 않는다. 예상하지 못한 예외는 예외 종류 이름만 알린다 (메시지에 비밀이 섞일 수 있다).
pywebview는 "_"로 시작하지 않는 속성을 모두 JS에 노출하므로 내부 상태는 전부 "_"로 시작한다.
"""
from __future__ import annotations

import functools
import importlib
import inspect
import threading
import uuid
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from napi import paths
from napi.classifier import DEFAULT_PROBE_PATTERNS, window_bounds
from napi.collector import Collector
from napi.config import ConfigError, _from_dict, load_config, save_config, validate_config
from napi.logformat import test_format as _test_format
from napi.logformat import test_sample_line
from napi.models import (
    AppConfig, CustomFormat, LogFormatDef, Note, ServerConfig, ServiceConfig,
)
from napi.remote.command import build_remote_command
from napi.scan import ScanError, run_scan
from napi.secrets import SessionSecrets
from napi.spec import SpecError, load_spec
from napi.store import load_result, refresh_notes, result_to_dict, safe_name, save_result

WINDOWS = ("90", "180", "365", "all")
PREVIEW_TOKEN = "napi-scan-00000000"
HOSTKEY_TIMEOUT_SEC = 300.0

_SCAN_NOT_FOUND = "스캔을 찾을 수 없어요"


class _UserError(Exception):
    """사용자에게 그대로 보여줄 한국어 문구."""


def _safe(method):
    """예외를 {"ok": False, "error": ...}로 바꾼다. 시그니처는 pywebview가 읽으므로 유지한다."""
    @functools.wraps(method)
    def wrapper(self, *args, **kwargs):
        try:
            return method(self, *args, **kwargs)
        except (_UserError, ConfigError, SpecError) as e:
            return {"ok": False, "error": str(e)}
        except Exception as e:
            return {"ok": False, "error": f"처리 중 오류가 발생했어요 ({type(e).__name__})"}
    wrapper.__signature__ = inspect.signature(method)
    return wrapper


@dataclass
class _Prompt:
    id: str
    host: str
    port: int
    key_type: str
    fingerprint: str
    event: threading.Event = field(default_factory=threading.Event)
    accept: bool = False


@dataclass
class _Scan:
    id: str
    service: str
    servers: dict[str, str]
    phase: str = "spec"
    error: str | None = None
    cancel: threading.Event = field(default_factory=threading.Event)
    prompts: list[_Prompt] = field(default_factory=list)  # FIFO
    lock: threading.Lock = field(default_factory=threading.Lock)

    def reject_all(self) -> None:
        with self.lock:
            pending, self.prompts = self.prompts, []
        for p in pending:
            p.accept = False
            p.event.set()


def _webview_open_file() -> str | None:
    import webview
    picked = webview.windows[0].create_file_dialog(
        webview.FileDialog.OPEN, file_types=("YAML 설정 파일 (*.yaml;*.yml)", "모든 파일 (*.*)"))
    return _first(picked)


def _webview_save_file(default_name: str, kind: str) -> str | None:
    import webview
    types = {"html": ("HTML 파일 (*.html)",), "pdf": ("PDF 파일 (*.pdf)",)}[kind]
    picked = webview.windows[0].create_file_dialog(
        webview.FileDialog.SAVE, save_filename=default_name, file_types=types)
    return _first(picked)


def _webview_open_key_file() -> str | None:
    import webview
    picked = webview.windows[0].create_file_dialog(
        webview.FileDialog.OPEN, file_types=("모든 파일 (*.*)",))
    return _first(picked)


def _first(picked) -> str | None:
    if not picked:
        return None
    if isinstance(picked, (list, tuple)):
        return str(picked[0])
    return str(picked)


def _window(window) -> str:
    w = str(window)
    if w not in WINDOWS:
        raise _UserError("분석 기간이 올바르지 않아요")
    return w


def _custom(d: dict | None) -> CustomFormat | None:
    if not d:
        return None
    try:
        return CustomFormat(
            method_field=int(d["method_field"]), path_field=int(d["path_field"]),
            status_field=int(d["status_field"]), time_field=int(d["time_field"]),
            time_format=d["time_format"])
    except (KeyError, TypeError, ValueError):
        raise _UserError("사용자 정의 형식이 올바르지 않아요") from None


def _draft_formats(items) -> list[LogFormatDef]:
    """설정 화면의 저장 전 형식 목록 ([{name, pattern}, ...]) -> LogFormatDef."""
    if not isinstance(items, list):
        raise _UserError("형식 정의가 올바르지 않아요")
    out = []
    for f in items:
        if not (isinstance(f, dict) and isinstance(f.get("name"), str)
                and isinstance(f.get("pattern"), str)):
            raise _UserError("형식 정의가 올바르지 않아요")
        out.append(LogFormatDef(name=f["name"], pattern=f["pattern"]))
    return out


class Api:
    def __init__(self, config_path: Path | None = None, known_hosts: Path | None = None,
                 results_dir: Path | None = None, secrets: SessionSecrets | None = None,
                 collector_factory: Callable = Collector, spec_loader: Callable = load_spec,
                 open_file: Callable[[], str | None] = _webview_open_file,
                 save_file: Callable[[str, str], str | None] = _webview_save_file,
                 open_key_file: Callable[[], str | None] = _webview_open_key_file,
                 exporter=None, today: Callable[[], date] = date.today,
                 hostkey_timeout: float = HOSTKEY_TIMEOUT_SEC):
        self._config_path = Path(config_path) if config_path else paths.config_path()
        self._known_hosts = Path(known_hosts) if known_hosts else None
        self._results_dir = Path(results_dir) if results_dir else None
        self._secrets = secrets if secrets is not None else SessionSecrets()
        self._collector_factory = collector_factory
        self._spec_loader = spec_loader
        self._open_file = open_file
        self._save_file = save_file
        self._open_key_file = open_key_file
        self._exporter = exporter
        self._today = today
        self._hostkey_timeout = hostkey_timeout
        self._lock = threading.RLock()  # 설정 읽기-수정-쓰기, 스캔 목록
        self._scans: dict[str, _Scan] = {}
        self._active: dict[str, str] = {}  # service -> scan_id

    # ---------------------------------------------------------------- helpers

    def _load(self) -> AppConfig:
        return load_config(self._config_path)

    @staticmethod
    def _service(cfg: AppConfig, name: str) -> ServiceConfig:
        for svc in cfg.services:
            if svc.name == name:
                return svc
        raise _UserError(f"서비스를 찾을 수 없어요: {name}")

    @staticmethod
    def _server(svc: ServiceConfig, name: str) -> ServerConfig:
        for server in svc.servers:
            if server.name == name:
                return server
        raise _UserError(f"서버를 찾을 수 없어요: {name}")

    def _scan(self, scan_id: str) -> _Scan:
        with self._lock:
            scan = self._scans.get(scan_id)
        if scan is None:
            raise _UserError(_SCAN_NOT_FOUND)
        return scan

    # ----------------------------------------------------------------- config

    @_safe
    def get_state(self) -> dict:
        try:
            cfg = self._load()
            error = None
        except ConfigError as e:
            cfg, error = AppConfig(services=[]), str(e)
        results = {}
        for svc in cfg.services:
            r = load_result(svc.name, self._results_dir)
            results[svc.name] = (None if r is None
                                 else {"sleeping": r.sleeping, "scanned_at": r.scanned_at})
        return {"config": asdict(cfg), "config_path": str(self._config_path),
                "results": results, "error": error,
                "default_probe_patterns": list(DEFAULT_PROBE_PATTERNS)}

    @_safe
    def save_config(self, config: dict) -> dict:
        try:
            cfg = _from_dict(config)
        except ConfigError as e:
            return {"ok": False, "errors": [str(e)]}
        errors = validate_config(cfg)
        if errors:
            return {"ok": False, "errors": errors}
        with self._lock:
            save_config(cfg, self._config_path)
        return {"ok": True, "errors": []}

    @_safe
    def open_config_dialog(self) -> dict:
        picked = self._open_file()
        if not picked:
            return {"ok": False, "path": str(self._config_path)}
        path = Path(picked)
        load_config(path)  # 읽을 수 없는 파일이면 ConfigError → 경로를 바꾸지 않는다
        with self._lock:
            self._config_path = path
        return {"ok": True, "path": str(path)}

    @_safe
    def test_log_line(self, fmt: str, custom: dict | None, line: str,
                      formats: list[dict] | None = None) -> dict:
        """formats가 있으면 (설정 화면의 저장 전 초안) 그것을, 없으면 설정 파일의 형식을 쓴다."""
        if formats is not None:
            defs = _draft_formats(formats)
        else:
            try:
                defs = self._load().log_formats
            except ConfigError:
                defs = []  # 설정 파일이 깨져도 기본 제공 형식은 시험할 수 있게 한다
        return test_sample_line(line, fmt, _custom(custom), defs)

    @_safe
    def test_format(self, pattern: str, line: str) -> dict:
        return _test_format(pattern, line)

    @_safe
    def preview_commands(self, service: str, period: str) -> dict:
        window = _window(period)  # 매개변수를 window로 지으면 안 된다 (start_scan 주석 참고)
        cfg = self._load()
        svc = self._service(cfg, service)
        since, _ = window_bounds(window, self._today())
        out = {}
        for server in svc.servers:
            try:
                out[server.name] = build_remote_command(server, cfg.settings, since,
                                                        PREVIEW_TOKEN, cfg.log_formats)
            except ValueError as e:
                out[server.name] = f"설정 오류: {e}"
        return out

    # ---------------------------------------------------------------- secrets

    @_safe
    def missing_secrets(self, service: str) -> list:
        svc = self._service(self._load(), service)
        return [{"server": s.name, "user": s.user, "host": s.host, "port": s.port,
                 "auth": s.auth} for s in self._secrets.missing_for(svc.servers)]

    @_safe
    def set_password(self, user: str, host: str, port: int, password: str,
                     apply_to_service: str | None = None) -> dict:
        targets = [(user, host, int(port))]
        if apply_to_service:
            svc = self._service(self._load(), apply_to_service)
            targets += [(s.user, s.host, s.port) for s in svc.servers if s.auth == "password"]
        for u, h, p in targets:
            self._secrets.set_password(u, h, p, password)
        return {"ok": True}

    @_safe
    def set_passphrase(self, key_file: str, passphrase: str) -> dict:
        self._secrets.set_passphrase(key_file, passphrase)
        return {"ok": True}

    @_safe
    def secret_status(self, service: str) -> dict:
        """서버명 -> "set" | "missing" | "n/a". 값은 절대 돌려주지 않는다.
        키파일 서버는 passphrase가 없으면 "n/a"다 (암호 없는 키일 수 있다)."""
        svc = self._service(self._load(), service)
        out = {}
        for s in svc.servers:
            if s.auth == "key":
                out[s.name] = ("set" if s.key_file and self._secrets.has_passphrase(s.key_file)
                               else "n/a")
            else:
                out[s.name] = ("set" if self._secrets.has_password(s.user, s.host, s.port)
                               else "missing")
        return out

    @_safe
    def clear_secret(self, service: str, server: str) -> dict:
        """해당 서버의 비밀번호 또는 키파일 암호를 메모리에서 지운다."""
        s = self._server(self._service(self._load(), service), server)
        if s.auth == "key":
            if s.key_file:
                self._secrets.clear_passphrase(s.key_file)
        else:
            self._secrets.clear_password(s.user, s.host, s.port)
        return {"ok": True}

    @_safe
    def pick_key_file(self) -> dict:
        """키파일 경로만 고른다. 파일은 열거나 읽지 않는다."""
        picked = self._open_key_file()
        if not picked:
            return {"ok": False, "path": None}
        return {"ok": True, "path": str(picked)}

    # ------------------------------------------------------------------- scan

    @_safe
    def start_scan(self, service: str, period: str) -> dict:
        # 매개변수 이름은 pywebview가 JS 함수 인자 이름으로 그대로 쓴다. "window"로 지으면
        # 생성된 JS 함수 안의 전역 window를 가려서 호출 자체가 실패한다 (test_js_safe_param_names).
        window = _window(period)
        cfg = self._load()
        svc = self._service(cfg, service)
        with self._lock:
            if service in self._active:
                raise _UserError("이미 스캔 중이에요")
            scan = _Scan(id=uuid.uuid4().hex, service=service,
                         servers={s.name: "waiting" for s in svc.servers})
            self._scans[scan.id] = scan
            self._active[service] = scan.id
        thread = threading.Thread(target=self._run_scan, args=(scan, svc, cfg, window),
                                  name=f"napi-scan-{scan.id[:8]}", daemon=True)
        thread.start()
        return {"scan_id": scan.id}

    def _run_scan(self, scan: _Scan, svc: ServiceConfig, cfg: AppConfig, window: str) -> None:
        def progress(server: str, phase: str) -> None:
            with scan.lock:
                if server == "*":
                    scan.phase = "spec"
                else:
                    scan.phase = "collect"
                    scan.servers[server] = phase

        def prompt(host: str, port: int, key_type: str, fingerprint: str) -> bool:
            return self._ask_hostkey(scan, host, port, key_type, fingerprint)

        phase, error = "failed", None
        try:
            known_hosts = self._known_hosts or paths.known_hosts_path()
            collector = self._collector_factory(cfg.settings, self._secrets, prompt, known_hosts,
                                                formats=cfg.log_formats)
            result = run_scan(svc, cfg.settings, window, collector, progress, scan.cancel,
                              today=self._today(), spec_loader=self._spec_loader)
            if scan.cancel.is_set():
                phase = "cancelled"  # 취소된 스캔은 저장하지 않는다 (이전 결과 유지)
            else:
                with self._lock:
                    # 스캔 중에 저장된 메모를 반영한다.
                    try:
                        result = refresh_notes(
                            result, self._service(self._load(), svc.name).notes)
                    except (ConfigError, _UserError):
                        pass
                    save_result(result, self._results_dir)
                phase = "done"
        except (SpecError, ScanError) as e:
            error = str(e)  # 저장하지 않으므로 이전 결과가 남는다
        except Exception as e:
            error = f"스캔 중 오류가 발생했어요 ({type(e).__name__})"
        finally:
            scan.reject_all()
            with scan.lock:
                scan.phase, scan.error = phase, error
            with self._lock:
                if self._active.get(scan.service) == scan.id:
                    del self._active[scan.service]

    def _ask_hostkey(self, scan: _Scan, host: str, port: int, key_type: str,
                     fingerprint: str) -> bool:
        """수집 작업 스레드에서 불린다. UI가 answer_hostkey로 답할 때까지 기다린다."""
        p = _Prompt(id=uuid.uuid4().hex[:12], host=host, port=int(port), key_type=key_type,
                    fingerprint=fingerprint)
        with scan.lock:
            if scan.cancel.is_set():
                return False
            scan.prompts.append(p)
        answered = p.event.wait(self._hostkey_timeout)
        with scan.lock:
            if p in scan.prompts:
                scan.prompts.remove(p)
        return p.accept if answered else False

    @_safe
    def get_scan_progress(self, scan_id: str) -> dict:
        scan = self._scan(scan_id)
        with scan.lock:
            pending = None
            if scan.prompts:
                p = scan.prompts[0]
                pending = {"prompt_id": p.id, "host": p.host, "port": p.port,
                           "key_type": p.key_type, "fingerprint": p.fingerprint}
            return {"phase": scan.phase, "servers": dict(scan.servers),
                    "hostkey_pending": pending, "error": scan.error}

    @_safe
    def answer_hostkey(self, scan_id: str, accept: bool, prompt_id: str | None = None) -> dict:
        scan = self._scan(scan_id)
        with scan.lock:
            match = [p for p in scan.prompts if prompt_id is None or p.id == prompt_id]
            if not match:
                raise _UserError("확인할 호스트 키가 없어요")
            p = match[0]
            scan.prompts.remove(p)
            p.accept = bool(accept)
        p.event.set()
        return {"ok": True}

    def _shutdown(self) -> None:
        """창이 닫힐 때 부른다 (JS에는 노출하지 않음). 진행 중인 스캔을 모두 취소하고
        기다리는 호스트 키 확인을 거절해서, 수집 스레드가 곧바로 끝나게 한다."""
        with self._lock:
            scans = [self._scans[i] for i in self._active.values()]
        for scan in scans:
            scan.cancel.set()
            scan.reject_all()

    @_safe
    def cancel_scan(self, scan_id: str) -> dict:
        scan = self._scan(scan_id)
        scan.cancel.set()
        scan.reject_all()
        return {"ok": True}

    # ----------------------------------------------------------- result/notes

    @_safe
    def get_result(self, service: str) -> dict | None:
        r = load_result(service, self._results_dir)
        return result_to_dict(r) if r is not None else None

    def _update_notes(self, service: str, change: Callable[[dict[str, Note]], None]) -> None:
        with self._lock:
            cfg = self._load()
            svc = self._service(cfg, service)
            change(svc.notes)
            save_config(cfg, self._config_path)
            r = load_result(service, self._results_dir)
            if r is not None:
                save_result(refresh_notes(r, svc.notes), self._results_dir)

    @_safe
    def save_note(self, service: str, op_key: str, reason: str, author: str) -> dict:
        reason = (reason or "").strip()
        if not reason:
            raise _UserError("유지 사유를 입력해 주세요")
        note = Note(reason=reason, author=(author or "").strip(),
                    date=self._today().isoformat())
        self._update_notes(service, lambda notes: notes.__setitem__(op_key, note))
        return {"ok": True}

    @_safe
    def delete_note(self, service: str, op_key: str) -> dict:
        self._update_notes(service, lambda notes: notes.pop(op_key, None))
        return {"ok": True}

    # ----------------------------------------------------------------- export

    @_safe
    def export(self, service: str, kind: str) -> dict:
        if kind not in ("html", "pdf"):
            raise _UserError("내보내기 형식이 올바르지 않아요")
        r = load_result(service, self._results_dir)
        if r is None:
            raise _UserError("아직 스캔 결과가 없어요")
        default_name = f"napi_{safe_name(service)}_{self._today():%Y%m%d}.{kind}"
        picked = self._save_file(default_name, kind)
        if not picked:
            return {"ok": False, "path": None}
        exporter = self._exporter or importlib.import_module("napi.report")
        report_error = getattr(exporter, "ReportError", None)
        export_fn = exporter.export_html if kind == "html" else exporter.export_pdf
        try:
            export_fn(r, Path(picked))
        except Exception as e:
            if report_error is not None and isinstance(e, report_error):
                raise _UserError(str(e)) from None
            raise
        return {"ok": True, "path": str(picked)}
