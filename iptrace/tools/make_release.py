"""GitLab Release에 올릴 파일 만들기 (make_release.bat가 부른다).

    python tools\\make_release.py

dist\\iptrace의 exe를 zip으로 묶고, 릴리스 설명(SHA256·설치 방법·변경 내역)을 만든다.
- zip에는 iptrace.exe, iptrace-cli.exe, _internal\\ 과 '처음_읽어주세요.md'만 넣는다.
  config·data·logs·rules·out(서버 정보·점검 이력·보고서)은 넣지 않는다.
- 소스가 exe보다 새로우면 멈춘다 (build_exe.bat를 먼저 실행).
결과: release\\IPtrace-<버전>-win64.zip, release\\IPtrace-<버전>-릴리스설명.md
"""
import hashlib
import re
import sys
import time
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DIST = ROOT / "dist" / "iptrace"
OUT = ROOT / "release"
FILES = ("iptrace.exe", "iptrace-cli.exe")


def fail(text):
    print(f"\n[실패] {text}", flush=True)
    sys.exit(1)


def version() -> str:
    m = re.search(r'__version__\s*=\s*"([^"]+)"', (ROOT / "iptrace" / "__init__.py").read_text(encoding="utf-8"))
    return m.group(1)


def sha256(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def changes(ver: str) -> str:
    """CHANGELOG.md에서 이 버전 부분만 꺼낸다."""
    p = ROOT / "CHANGELOG.md"
    if not p.exists():
        return "(CHANGELOG.md 없음)"
    m = re.search(rf"^## v?{re.escape(ver)}\s*$(.*?)(?=^## |\Z)", p.read_text(encoding="utf-8"), re.M | re.S)
    if not m:
        return f"(CHANGELOG.md에 v{ver} 항목 없음)"
    return re.sub(r"^### ", "#### ", m.group(1).strip(), flags=re.M)   # '### 바뀐 것' 아래 단계로


GUIDE = """# IPtrace {ver}

침해의심 IP가 서버 로그에 남았는지 확인하는 점검 도구입니다. 서버 원본 로그는 PC로 내려받지 않습니다.

## 설치
1. 이 폴더를 통째로 쓰기 가능한 위치에 둡니다 (예: `D:\\iptrace\\`). `C:\\Program Files`는 안 됩니다.
2. `iptrace.exe`를 실행합니다. 처음에는 [서버 설정] 탭이 열립니다.
   - **서비스 계정은 절대 사용하지 마세요.** 인프라팀을 통해 개인 계정으로 로그에 접근할 수 있도록 권한을 받아 쓰세요.
3. 백신/EDR이 막으면 릴리스 설명의 SHA256으로 예외 등록을 요청하세요.

## 새 버전으로 바꿀 때
`iptrace.exe`, `iptrace-cli.exe`, `_internal\\`만 덮어쓰세요. `config\\`(서버 설정), `data\\`(점검 이력)는 그대로 남습니다.

## 안 켜지거나 이상할 때
명령 프롬프트에서 이 폴더로 이동한 뒤 실행합니다.
```
iptrace-cli.exe doctor
```
"""


def main():
    ver = version()
    for f in FILES:
        if not (DIST / f).exists():
            fail(f"{DIST}\\{f}가 없습니다. build_exe.bat를 먼저 실행하세요.")
    built = min((DIST / f).stat().st_mtime for f in FILES)
    sources = [p for p in (ROOT / "iptrace").rglob("*.py")] + [ROOT / "rules" / "signatures.json"]
    newer = sorted(str(p.relative_to(ROOT)) for p in sources if p.stat().st_mtime > built + 1)
    if newer:
        fail(f"exe보다 새로 고친 소스가 있습니다 ({', '.join(newer[:3])}{' 외' if len(newer) > 3 else ''}). "
             "build_exe.bat를 먼저 실행하세요.")

    enc = [p for p in DIST.rglob("*") if p.is_file() and p.read_bytes()[:15] == b"<DOCUMENT SAFER"
           and not set(p.relative_to(DIST).parts[:1]) & {"config", "data", "logs", "rules", "out"}]
    if enc:
        fail(f"exe 폴더에 문서 보안(DRM)으로 암호화된 파일이 {len(enc)}개 있습니다 "
             f"(예: {enc[0].relative_to(DIST)}). build_exe.bat로 다시 빌드한 뒤 바로 실행하세요.")

    OUT.mkdir(exist_ok=True)
    name = f"IPtrace-{ver}-win64"
    zpath = OUT / f"{name}.zip"
    print(f"[1/3] {zpath.name} 만들기", flush=True)
    n = 0
    try:
        zf = zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED, compresslevel=9)
    except PermissionError:
        fail(f"{zpath.name}이 다른 프로그램에서 열려 있습니다 (업로드 중이거나 압축 프로그램). 닫고 다시 실행하세요.")
    with zf as z:
        for f in FILES:
            z.write(DIST / f, f"{name}/{f}")
            n += 1
        for p in sorted((DIST / "_internal").rglob("*")):
            if p.is_file():
                z.write(p, f"{name}/{p.relative_to(DIST).as_posix()}")
                n += 1
        z.writestr(f"{name}/처음_읽어주세요.md", GUIDE.format(ver=ver))
    with zipfile.ZipFile(zpath) as z:
        bad = z.testzip()
        tops = {x.split("/")[1] for x in z.namelist()}
    if bad or tops != {"iptrace.exe", "iptrace-cli.exe", "_internal", "처음_읽어주세요.md"}:
        fail(f"zip 확인 실패 ({bad or sorted(tops)})")
    print(f"      파일 {n + 1}개, {zpath.stat().st_size / 1e6:.1f}MB", flush=True)

    print("[2/3] 릴리스 설명 만들기", flush=True)
    rows = [(zpath.name, sha256(zpath))] + [(f, sha256(DIST / f)) for f in FILES]
    notes = OUT / f"{name.replace('-win64', '')}-릴리스설명.md"
    notes.write_text(
        f"## IPtrace {ver} ({time.strftime('%Y-%m-%d')})\n\n"
        f"Windows 64비트용입니다. `{zpath.name}`을 받아 압축을 풀고 `iptrace.exe`를 실행하세요 "
        "(Python 설치 필요 없음). 자세한 내용은 zip 안의 `처음_읽어주세요.md`를 보세요.\n\n"
        "### SHA256 (백신/EDR 예외 등록용)\n\n| 파일 | SHA256 |\n|---|---|\n"
        + "".join(f"| `{f}` | `{h}` |\n" for f, h in rows)
        + "\n### 설치\n"
          "1. 압축을 쓰기 가능한 위치에 풉니다 (예: `D:\\iptrace\\`). `C:\\Program Files`는 안 됩니다.\n"
          "2. `iptrace.exe` 실행 → [서버 설정] 탭에서 서버를 등록하고 [연결 테스트]\n"
          "   - **서비스 계정은 절대 사용하지 마세요.** 인프라팀을 통해 개인 계정으로 로그에 접근할 수 있도록 "
          "권한을 받아 쓰세요.\n"
          "3. 새 버전으로 바꿀 때는 `iptrace.exe`, `iptrace-cli.exe`, `_internal\\`만 덮어쓰면 설정·이력이 그대로 남습니다.\n\n"
        + f"### 바뀐 것\n\n{changes(ver)}\n",
        encoding="utf-8")

    print("[3/3] 확인", flush=True)
    for p in (zpath, notes):
        with open(p, "rb") as f:
            if f.read(15) == b"<DOCUMENT SAFER":
                fail(f"{p.name}이 문서 보안(DRM)으로 암호화됐습니다. 그대로 올리면 받는 사람이 열 수 없습니다.")
    print(f"\n완료: {OUT}")
    for f, h in rows:
        print(f"  {f}: {h}")
    print("\nGitLab: 배포 → 릴리스 → 새 릴리스 → 태그 v" + ver + " → 설명에 릴리스설명.md 내용을 붙여넣고 zip을 끌어다 놓기")


if __name__ == "__main__":
    main()
