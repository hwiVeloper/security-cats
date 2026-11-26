"""소스 파일 버전 확인 (doctor).

소스를 손으로 한 파일씩 옮기다 보면 일부만 바뀌거나 중간까지만 붙여넣어질 수 있다.
배포할 때 tools/make_manifest.py로 파일마다 해시와 함수 이름을 iptrace/manifest.py에 적어 두고,
doctor가 지금 파일과 비교한다.

- 해시는 줄바꿈(CRLF/LF), 줄 끝 공백, BOM 차이를 무시한다(복사 과정에서 바뀌기 쉬우므로).
- 함수 이름 확인은 모듈을 실제로 불러와서 하므로 exe에서도 동작한다.
"""
import ast
import hashlib
import importlib
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent      # 소스 실행 시 프로젝트 루트
MANIFEST_FILE = "iptrace/manifest.py"
EXTRA_FILES = ("build/entry.py", "build/iptrace.spec", "build/version_info.py")


def source_files(root: Path = ROOT) -> list:
    """배포본에 넣는 소스 파일 (프로젝트 루트 기준 상대 경로, / 구분)."""
    files = sorted(p.relative_to(root).as_posix() for p in (root / "iptrace").rglob("*.py")
                   if "__pycache__" not in p.parts)
    files = [f for f in files if f != MANIFEST_FILE]
    return files + [f for f in EXTRA_FILES if (root / f).exists()]


def text_hash(data: bytes) -> str:
    """줄바꿈·줄 끝 공백·BOM 차이를 무시한 해시(앞 16자리)."""
    text = data.decode("utf-8-sig", errors="replace")
    norm = "\n".join(line.rstrip() for line in text.splitlines()).strip("\n")
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()[:16]


def module_name(rel: str) -> str:
    """'iptrace/core/report.py' -> 'iptrace.core.report', 'iptrace/gui/__init__.py' -> 'iptrace.gui'"""
    mod = rel[:-3].replace("/", ".")
    return mod[: -len(".__init__")] if mod.endswith(".__init__") else mod


def public_names(source: str) -> list:
    """모듈 맨 위에 정의된 공개 이름(함수·클래스·상수). 다른 모듈이 가져다 쓰는 것들."""
    names = set()
    for node in ast.parse(source).body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names.add(node.name)
        elif isinstance(node, ast.Assign):
            names.update(t.id for t in node.targets if isinstance(t, ast.Name))
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names.add(node.target.id)
    return sorted(n for n in names if not n.startswith("_"))


def build_manifest(root: Path = ROOT) -> tuple:
    """(파일별 해시, 모듈별 공개 이름)"""
    files, names = {}, {}
    for rel in source_files(root):
        data = (root / rel).read_bytes()
        files[rel] = text_hash(data)
        # __main__은 불러오면 프로그램이 실행되므로 함수 확인에서 뺀다
        if rel.startswith("iptrace/") and rel.endswith(".py") and not rel.endswith("__main__.py"):
            names[module_name(rel)] = public_names(data.decode("utf-8-sig"))
    return files, names


def load_manifest():
    """배포본 목록을 불러온다. 없으면 None (manifest.py를 옮기지 않은 경우)."""
    try:
        from .. import manifest
    except ImportError:
        return None
    return manifest


def check_files(manifest, root: Path = ROOT) -> dict:
    """소스 실행일 때만: 배포본과 다른 파일, 없는 파일, 배포본에 없는 새 파일."""
    changed, missing = [], []
    for rel, h in manifest.FILES.items():
        p = root / rel
        if not p.exists():
            missing.append(rel)
        elif text_hash(p.read_bytes()) != h:
            changed.append(rel)
    extra = [f for f in source_files(root) if f not in manifest.FILES]
    return {"changed": changed, "missing": missing, "extra": extra}


def check_names(manifest) -> dict:
    """모듈을 불러와 배포본에 있던 함수·클래스가 모두 있는지 본다. {모듈: [없는 이름] 또는 '불러오기 실패: ...'}"""
    problems = {}
    for mod, names in manifest.NAMES.items():
        try:
            m = importlib.import_module(mod)
        except BaseException as e:  # noqa: BLE001 - 진단이므로 모든 오류(SystemExit 포함)를 보고한다
            problems[mod] = f"불러오기 실패: {type(e).__name__}: {e}"
            continue
        lost = [n for n in names if not hasattr(m, n)]
        if lost:
            problems[mod] = lost
    return problems


def render_manifest(files: dict, names: dict, version: str, created: str) -> str:
    """iptrace/manifest.py 내용"""
    out = ['"""배포본 소스 목록 (tools/make_manifest.py가 만든다. 직접 고치지 않는다).',
           "",
           "iptrace-cli doctor가 지금 파일과 비교해 손으로 옮기다 빠지거나 섞인 파일을 찾는다.",
           '"""',
           f"VERSION = {version!r}",
           f"CREATED = {created!r}",
           "",
           "FILES = {"]
    out += [f"    {k!r}: {v!r}," for k, v in files.items()]
    out += ["}", "", "NAMES = {"]
    for k, v in names.items():
        out.append(f"    {k!r}: {v!r},")
    out += ["}", ""]
    return "\n".join(out)
