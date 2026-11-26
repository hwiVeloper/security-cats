"""doctor의 소스 파일 버전 확인 (손으로 옮기다 빠지거나 섞인 파일 찾기)."""
import os
import shutil
import subprocess
import sys

from iptrace import manifest
from iptrace.core import integrity


def test_manifest_is_up_to_date():
    """소스를 고쳤으면 'python tools/make_manifest.py'로 배포본 목록을 다시 만들어야 한다."""
    files, names = integrity.build_manifest()
    assert files == manifest.FILES, "tools/make_manifest.py를 다시 실행하세요"
    assert names == manifest.NAMES, "tools/make_manifest.py를 다시 실행하세요"
    assert "reply_text" in manifest.NAMES["iptrace.core.report"]


def test_hash_ignores_line_endings_and_trailing_spaces():
    a = b"def f():\n    return 1\n"
    assert integrity.text_hash(a) == integrity.text_hash(b"\xef\xbb\xbfdef f():  \r\n    return 1\r\n\r\n")
    assert integrity.text_hash(a) != integrity.text_hash(b"def f():\n    return 2\n")


def _doctor(root):
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    p = subprocess.run([sys.executable, "-m", "iptrace", "-c", str(root / "c.json"), "doctor"], cwd=root, env=env,
                       capture_output=True, text=True, encoding="utf-8", timeout=180)
    return p.returncode, p.stdout


def test_doctor_finds_half_copied_file(tmp_path):
    """report.py를 중간까지만 옮긴 경우(실제 사례: reply_text 없음)를 찾아내고, 줄바꿈만 바뀐 파일은 같다고 본다."""
    root = integrity.ROOT
    for d in ("iptrace", "build", "rules"):
        shutil.copytree(root / d, tmp_path / d, ignore=shutil.ignore_patterns("__pycache__", "_work"))
    rep = tmp_path / "iptrace" / "core" / "report.py"
    lines = rep.read_text(encoding="utf-8").splitlines(keepends=True)
    cut = next(i for i, ln in enumerate(lines) if ln.startswith("def reply_text"))
    rep.write_text("".join(lines[:cut]), encoding="utf-8")         # reply_text 앞에서 잘림
    cli = tmp_path / "iptrace" / "cli.py"
    lf = cli.read_bytes().replace(b"\r\n", b"\n")
    cli.write_bytes(lf if lf != cli.read_bytes() else lf.replace(b"\n", b"\r\n"))  # 줄바꿈만 바뀜 -> 문제 아님

    code, out = _doctor(tmp_path)
    assert code == 2, out
    ver = next(x for x in out.splitlines() if "소스 파일 버전" in x)
    assert ver.startswith("  [주의]") and "iptrace/core/report.py" in ver and "cli.py" not in ver
    mod = next(x for x in out.splitlines() if "모듈 내용" in x)
    assert mod.startswith("  [문제]") and "iptrace.core.report" in mod and "reply_text" in mod

    (tmp_path / "iptrace" / "manifest.py").unlink()                # 배포본 목록을 안 옮긴 경우
    code, out = _doctor(tmp_path)
    assert "[주의] 소스 파일 버전: 배포본 목록(iptrace/manifest.py)이 없어" in out
