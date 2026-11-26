"""명령줄 도구 (PLAN.md 7장). GUI와 같은 core.engine을 쓴다.

종료 코드: 0 접속 IP 없음 / 1 접속 IP 있음 / 2 실행 오류 / 3 일부 서버 수집 실패
"""
import argparse
import getpass
import os
import sys
from datetime import datetime

from . import APP_NAME, __version__, paths
from .core import config as cfgmod
from .core import audit, engine, history, ioc, report
from .core.collector import CollectError, SshSource
from .core.models import CheckRequest

EXIT_NONE, EXIT_FOUND, EXIT_ERROR, EXIT_PARTIAL = 0, 1, 2, 3


def _parse_dt(s: str, end=False) -> datetime:
    try:
        return engine.parse_when(s, end=end)
    except ValueError:
        raise argparse.ArgumentTypeError(f"날짜 형식이 아닙니다: {s} (예: 2026-10-21 또는 '2026-10-21 13:00')") from None


def _read_iocs(args, cfg):
    text = ""
    if args.input == "-":
        text = sys.stdin.read()
    elif args.input:
        text = ioc.read_file(args.input)
    if args.ip:
        text += "\n" + "\n".join(args.ip)
    res = ioc.parse_text(text, cfg["general"]["own_networks"], include_private=args.include_private)
    for ln, line, tok in res.errors:
        print(f"[입력 오류] {ln}번째 줄: '{tok}'는 IP 형식이 아닙니다 (점검에서 빠짐)", file=sys.stderr)
    for ln, v, why in res.excluded:
        print(f"[제외] {ln}번째 줄 {v}: {why}", file=sys.stderr)
    for w in res.warnings:
        print(f"[경고] {w}", file=sys.stderr)
    return res


def _period(args, cfg):
    limit = not args.local_logs      # 서버를 읽을 때만 최대 7일 (로컬 로그는 서버 부하가 없다)
    if args.today:
        return engine.period_range("today")
    if args.yesterday:
        return engine.period_range("yesterday")
    if args.date_from or args.date_to:
        if not (args.date_from and args.date_to):
            raise SystemExit("--from 과 --to 를 함께 지정하세요")
        return engine.period_range("custom", start=_parse_dt(args.date_from), end=_parse_dt(args.date_to, end=True),
                                   limit=limit)
    if args.days:
        return engine.period_range(str(args.days), limit=limit)
    return engine.period_range(str(cfg["general"]["default_period"]), limit=limit)


def _local_logs(values):
    out = []
    for v in values or []:
        name, sep, pattern = v.partition("=")
        is_named = sep and name and not any(c in name for c in r"\/:*?")
        out.append((name, pattern) if is_named else ("local", v))
    return out


def _ask_host(server, host, key_type, fp):
    print(f"[호스트 키] {server}({host})의 키가 known_hosts에 없습니다: {key_type} {fp}", file=sys.stderr)
    print("  GUI의 연결 테스트나 'servers test --trust'로 먼저 등록하세요.", file=sys.stderr)
    return False


def cmd_check(args, cfg) -> int:
    res = _read_iocs(args, cfg)
    if not res.entries:
        print("점검할 IP가 없습니다.", file=sys.stderr)
        return EXIT_ERROR
    try:
        start, end = _period(args, cfg)
    except ValueError as e:
        print(f"[오류] {e}", file=sys.stderr)
        return EXIT_ERROR
    local = _local_logs(args.local_logs)
    servers = []
    if not local:
        names = [n.strip() for n in args.servers.split(",")] if args.servers else [s["name"] for s in cfg["servers"]]
        try:
            servers = [cfgmod.server_by_name(cfg, n) for n in names]
        except KeyError as e:
            print(f"설정에 없는 서버: {e}", file=sys.stderr)
            return EXIT_ERROR
        if not servers:
            print("점검할 서버가 없습니다. config.json에 서버를 등록하거나 --local-logs를 쓰세요.", file=sys.stderr)
            return EXIT_ERROR
    passwords, by_key = {}, {}
    for s in servers:
        ssh = cfgmod.effective_ssh(cfg, s)
        if ssh["auth"] != "key":
            key = cfgmod.password_key(cfg, s)   # 계정@주소:포트 마다 한 번씩 묻는다
            if key not in by_key:
                by_key[key] = getpass.getpass(f"SSH 비밀번호 ({key}): ")
            passwords[s["name"]] = by_key[key]
    req = CheckRequest(iocs=res.entries, start=start, end=end, servers=servers, local_logs=local,
                              log_format=args.log_format, passwords=passwords)

    def on_event(e):
        if args.verbose and e.kind in ("server_started", "server_done", "server_failed"):
            print(f"  {e.message}", file=sys.stderr)

    result = engine.run_check(req, cfg, on_event, on_unknown_host=_ask_host)
    if not args.no_history:
        history.save_result(result, audit._user())
    print(report.console_summary(result))
    if args.output:
        out = args.output
        fmt = args.format or ("csv" if out.lower().endswith(".csv") else "xlsx" if out.lower().endswith(".xlsx")
                              else "html")
        if fmt == "csv":
            for p in report.save_csv(result, out, args.mask, res):
                print(f"저장: {p}")
        else:
            if not out.lower().endswith((".html", ".htm", ".xlsx")):
                out += "." + fmt
            if fmt == "xlsx" and not report.has_xlsx():
                print("[안내] openpyxl이 설치되지 않아 엑셀 대신 CSV로 저장합니다.", file=sys.stderr)
            kind, saved = report.save(result, out, args.mask, res)
            for p in saved if kind == "csv" else [saved]:
                print(f"저장: {p}")
        audit.log_save(out)
    if args.reply:
        print()
        print(report.reply_text(result))
    if result.failed_servers:
        return EXIT_PARTIAL
    return EXIT_FOUND if result.summary_counts()["accessed"] else EXIT_NONE


def cmd_ioc_normalize(args, cfg) -> int:
    res = _read_iocs(args, cfg)
    text = ioc.normalized_list(res)
    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(text + "\n")
    else:
        print(text)
    print(ioc.summary_text(res), file=sys.stderr)
    return EXIT_NONE if not res.errors else EXIT_ERROR


def cmd_servers_test(args, cfg) -> int:
    names = [n.strip() for n in args.servers.split(",")] if args.servers else [s["name"] for s in cfg["servers"]]
    known = str(paths.resolve(cfg["ssh"]["known_hosts"]))
    pw = None
    failed = 0
    for n in names:
        s = cfgmod.server_by_name(cfg, n)
        ssh = cfgmod.effective_ssh(cfg, s)
        if ssh["auth"] != "key" and pw is None:
            pw = getpass.getpass(f"SSH 비밀번호 ({ssh['user']}): ")

        def trust(server, host, key_type, fp):
            print(f"[호스트 키] {server}({host}) {key_type} {fp}")
            return bool(args.trust) and input("서버 담당자에게 확인한 지문이 맞습니까? (yes/no) ").strip() == "yes"

        src = SshSource(s, ssh, pw, known, trust)
        try:
            src.connect()
            src.check()
            files = src.list_files(None)
            print(f"[성공] {n}: 접속 OK, 로그 폴더 OK, 파일 {len(files)}개")
        except CollectError as e:
            failed += 1
            print(f"[실패] {n}: {e}")
        finally:
            src.close()
    return EXIT_PARTIAL if failed else EXIT_NONE


def cmd_doctor(args) -> int:
    """실행 환경 점검: exe가 안 켜질 때 원인을 찾는다. 설정 파일이 깨져 있어도 동작해야 하므로 먼저 실행한다."""
    import importlib
    import platform

    results = []   # [(상태, 항목, 내용)] 상태: 정상 / 주의 / 문제

    class Warn(Exception):
        """동작은 하지만 확인이 필요한 경우"""

    def check(name, fn):
        try:
            info = fn()
            results.append(("정상", name, info or ""))
        except Warn as e:
            results.append(("주의", name, str(e)))
        except BaseException as e:  # noqa: BLE001 - 진단이므로 모든 오류를 보고한다
            results.append(("문제", name, f"{type(e).__name__}: {e}"))

    def writable():
        paths.BASE.mkdir(parents=True, exist_ok=True)
        probe = paths.BASE / ".write_test"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
        return str(paths.BASE)

    def tk_check():
        import tkinter
        r = tkinter.Tk()
        r.withdraw()
        v = r.tk.call("info", "patchlevel")
        r.destroy()
        return f"Tcl/Tk {v}"

    def module(name):
        return lambda: getattr(importlib.import_module(name), "__version__", "설치됨")

    def rules():
        from .core.detector import Signatures
        paths.ensure_runtime_files()
        s = Signatures.load(paths.RULES_DIR / "signatures.json")
        return f"규칙 {s.rule_count}개 · 버전 {s.version}"

    def config():
        c = cfgmod.load(args.config)
        errs = cfgmod.validate(c)
        if errs:
            raise ValueError("; ".join(errs[:3]))
        return f"서버 {len(c['servers'])}대"

    def history_db():
        history.list_checks(limit=1)
        return str(history._db_path())

    def drm():
        # exe면 _internal 폴더, 소스 실행이면 설치된 패키지 폴더(site-packages)를 본다
        if getattr(sys, "frozen", False):
            root = str(paths.bundled_dir())
        else:
            import sysconfig
            root = sysconfig.get_paths()["purelib"]
        bad = []
        for dirpath, _, files in os.walk(root):
            for f in files:
                p = os.path.join(dirpath, f)
                try:
                    with open(p, "rb") as fh:
                        if fh.read(15) == b"<DOCUMENT SAFER":
                            bad.append(os.path.relpath(p, root))
                except OSError:
                    continue
        if bad:
            raise RuntimeError(f"DRM으로 암호화된 파일 {len(bad)}개 (예: {', '.join(bad[:3])})")
        return f"{root} 안에 암호화된 파일 없음"

    from .core import integrity
    manifest = integrity.load_manifest()

    def source_version():
        # 소스로 실행할 때만: 파일마다 배포본과 같은지 (손으로 옮기다 빠지거나 섞인 파일 찾기)
        if manifest is None:
            raise Warn(f"배포본 목록({integrity.MANIFEST_FILE})이 없어 확인할 수 없습니다. 이 파일도 함께 옮기세요")
        base = f"배포본 {manifest.VERSION} ({manifest.CREATED})"
        if getattr(sys, "frozen", False):
            return f"exe 안의 소스는 빌드할 때의 것. 빌드 전에 소스 폴더에서 'python -m iptrace doctor'로 확인하세요 · {base}"
        r = integrity.check_files(manifest)
        msgs = []
        if r["missing"]:
            msgs.append(f"없는 파일 {len(r['missing'])}개: {', '.join(r['missing'])}")
        if r["changed"]:
            msgs.append(f"배포본과 다른 파일 {len(r['changed'])}개: {', '.join(r['changed'])}")
        if r["extra"]:
            msgs.append(f"배포본에 없는 파일 {len(r['extra'])}개: {', '.join(r['extra'])}")
        if r["missing"]:
            raise RuntimeError(" / ".join(msgs))
        if msgs:
            raise Warn(" / ".join(msgs) + " (일부러 고친 파일이 아니면 개발 PC의 파일로 다시 옮기세요)")
        return f"파일 {len(manifest.FILES)}개 모두 {base}과 같음"

    def module_contents():
        # 소스·exe 모두: 다른 파일이 쓰는 함수가 빠진 모듈이 없는지 (예: report.py에 reply_text 없음)
        if manifest is None:
            raise Warn("배포본 목록이 없어 확인할 수 없습니다")
        bad = integrity.check_names(manifest)
        if bad:
            raise RuntimeError(" / ".join(f"{m}: {v if isinstance(v, str) else '없는 이름 ' + ', '.join(v)}"
                                          for m, v in bad.items()))
        return f"모듈 {len(manifest.NAMES)}개 모두 필요한 함수가 있음"

    check("실행 위치 쓰기 권한", writable)
    check("Python", lambda: f"{platform.python_version()} ({platform.architecture()[0]}, {'exe' if getattr(sys, 'frozen', False) else '소스'})")
    check("화면(tkinter)", tk_check)
    check("SSH(paramiko)", module("paramiko"))
    check("엑셀 저장(openpyxl) - 없으면 CSV로 저장", module("openpyxl"))
    check("공격 규칙 파일", rules)
    check("설정 파일", config)
    check("점검 이력 DB", history_db)
    check("문서 보안(DRM) 암호화 여부", drm)
    check("소스 파일 버전", source_version)
    check("모듈 내용(함수 빠짐)", module_contents)
    print(f"{APP_NAME} {__version__} 실행 환경 점검")
    for status, name, info in results:
        print(f"  [{status}] {name}: {info}")
    bad = [r for r in results if r[0] == "문제" and "openpyxl" not in r[1]]
    warn = [r for r in results if r[0] == "주의"]
    tail = f" (주의 {len(warn)}건)" if warn else ""
    print(f"\n문제 없음{tail}" if not bad else f"\n문제 {len(bad)}건{tail}. 위 [문제] 줄의 내용을 확인하세요.")
    return EXIT_NONE if not bad else EXIT_ERROR


def build_parser():
    p = argparse.ArgumentParser(prog="iptrace-cli", description=f"{APP_NAME} {__version__} - 침해의심 IP 로그 점검")
    p.add_argument("-c", "--config", default=None, help="설정 파일 (기본: config/config.json)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def ioc_args(sp):
        sp.add_argument("-i", "--input", help="IP 목록 파일(txt/csv). '-'이면 표준입력")
        sp.add_argument("--ip", action="append", help="IP 직접 지정 (쉼표 구분 가능, 여러 번 사용 가능)")
        sp.add_argument("--include-private", action="store_true", help="사설·자사 대역도 점검에 포함")

    c = sub.add_parser("check", help="침해의심 IP를 서버 로그와 대조")
    ioc_args(c)
    g = c.add_mutually_exclusive_group()
    g.add_argument("--today", action="store_true", help="오늘 00:00 ~ 지금")
    g.add_argument("--yesterday", action="store_true", help="어제 하루")
    g.add_argument("--days", type=int, help="오늘 포함 최근 N일 (서버 점검은 최대 7일)")
    c.add_argument("--from", dest="date_from", help="시작 (YYYY-MM-DD [HH:MM])")
    c.add_argument("--to", dest="date_to", help="끝 (YYYY-MM-DD [HH:MM])")
    c.add_argument("--servers", help="점검할 서버 이름 (쉼표 구분, 기본: 전체)")
    c.add_argument("--local-logs", action="append", help="로컬 로그 파일 [서버명=]경로패턴 (여러 번 사용 가능)")
    c.add_argument("--log-format", default="webtob_combined", help="로컬 로그 형식 이름 또는 Format 문자열")
    c.add_argument("-o", "--output", help="결과 저장 경로 (html·xlsx 파일 또는 csv 폴더). 지정하지 않으면 저장하지 않음")
    c.add_argument("--format", choices=["html", "xlsx", "csv"], default=None,
                   help="저장 형식. 생략하면 -o 확장자로 정하고, 확장자가 없으면 html")
    c.add_argument("--mask", action="store_true", help="쿼리스트링 값 가리기")
    c.add_argument("--reply", action="store_true", help="회신 요약 문구 출력")
    c.add_argument("--no-history", action="store_true", help="점검 이력에 요약을 남기지 않음")
    c.add_argument("-v", "--verbose", action="store_true")
    c.set_defaults(func=cmd_check)

    i = sub.add_parser("ioc", help="IP 목록 도구")
    isub = i.add_subparsers(dest="ioc_cmd", required=True)
    n = isub.add_parser("normalize", help="IP 목록 정리 (중복 제거, 오류 표시)")
    ioc_args(n)
    n.add_argument("-o", "--output")
    n.set_defaults(func=cmd_ioc_normalize)

    d = sub.add_parser("doctor", help="실행 환경 점검 (exe가 안 켜질 때)")
    d.set_defaults(func=None)

    s = sub.add_parser("servers", help="서버 도구")
    ssub = s.add_subparsers(dest="srv_cmd", required=True)
    t = ssub.add_parser("test", help="서버 연결과 로그 경로 점검")
    t.add_argument("--servers")
    t.add_argument("--trust", action="store_true", help="처음 보는 호스트 키를 확인 후 등록")
    t.set_defaults(func=cmd_servers_test)
    return p


def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")  # 콘솔 코드페이지에 없는 글자 때문에 죽지 않게 한다
    args = build_parser().parse_args(argv)
    if getattr(args, "ip", None):
        args.ip = [x for v in args.ip for x in v.split(",")]
    if args.cmd == "doctor":
        return cmd_doctor(args)
    try:
        paths.ensure_runtime_files()
        cfg = cfgmod.load(args.config)
        errs = cfgmod.validate(cfg)
        if errs:
            for e in errs:
                print(f"[설정 오류] {e}", file=sys.stderr)
            return EXIT_ERROR
        return args.func(args, cfg)
    except KeyboardInterrupt:
        print("중단했습니다.", file=sys.stderr)
        return EXIT_ERROR
    except (cfgmod.ConfigError, ValueError, OSError) as e:
        print(f"[오류] {e}", file=sys.stderr)
        return EXIT_ERROR


if __name__ == "__main__":
    sys.exit(main())
