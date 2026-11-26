"""공개 저장소에 사내 정보가 들어가지 않게 바꾸고 검사하는 범용 스크립트.

원래 값(치환표)은 이 저장소에 두지 않는다. 저장소 밖 JSON 파일을 --map 으로 넘긴다.
    {"replacements": [["원래 값", "바꿀 값"], ...], "forbidden": ["금지어", ...]}

    python tools/scrub.py --map <json> <폴더>     폴더 안 텍스트 파일을 바꾼 뒤 검사
    python tools/scrub.py --map <json> --check    저장소에 올라갈 파일 전부와 커밋 메시지를 검사

검사 결과에는 금지어 자체 대신 순번(#n)만 출력한다(출력이 어딘가에 붙어도 원래 값이 새지 않게).
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

TEXT_SUFFIXES = frozenset(
    ".py .md .json .bat .spec .ini .in .log .list .cfg .toml .yaml .yml .html .css .js .mjs .ts .tsx "
    ".csv .ps1 .gitignore .gitattributes".split())
_SKIP_DIRS = frozenset({".git", ".venv", "__pycache__", "node_modules"})
_BOM = b"\xef\xbb\xbf"


def _is_text(path: Path) -> bool:
    return path.suffix.lower() in TEXT_SUFFIXES or path.name.lower() in TEXT_SUFFIXES


def _text_files(root: Path) -> list[Path]:
    out = []
    for p in sorted(root.rglob("*")):
        if p.is_file() and _is_text(p) and not (set(p.relative_to(root).parts[:-1]) & _SKIP_DIRS):
            out.append(p)
    return out


def load_map(path: Path) -> tuple[list[tuple[str, str]], list[str]]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [(a, b) for a, b in data["replacements"]], list(data["forbidden"])


def scrub_tree(root: Path, replacements: list[tuple[str, str]]) -> tuple[list[Path], list[Path]]:
    """텍스트 파일을 위에서부터 차례로 바꾼다. 줄바꿈과 BOM 은 그대로 둔다."""
    changed: list[Path] = []
    unreadable: list[Path] = []
    for p in _text_files(Path(root)):
        raw = p.read_bytes()
        bom = raw.startswith(_BOM)
        try:
            text = raw[len(_BOM) if bom else 0:].decode("utf-8")
        except UnicodeDecodeError:
            unreadable.append(p)
            continue
        new = text
        for old, repl in replacements:
            new = new.replace(old, repl)
        if new != text:
            p.write_bytes((_BOM if bom else b"") + new.encode("utf-8"))
            changed.append(p)
    return changed, unreadable


def _hits_in_text(text: str, forbidden: list[str]) -> list[tuple[int, str]]:
    lowered = [w.lower() for w in forbidden]
    hits = []
    for n, line in enumerate(text.splitlines(), 1):
        low = line.lower()
        for word, lw in zip(forbidden, lowered):
            if lw in low:
                hits.append((n, word))
    return hits


def find_forbidden(files: list[Path], forbidden: list[str]) -> list[tuple[Path, int, str]]:
    """텍스트 파일에서 금지어를 대소문자 무시로 찾는다. 못 읽는 바이트는 대체 문자로 읽는다."""
    out = []
    for p in files:
        if not _is_text(p) or not p.is_file():
            continue
        text = p.read_bytes().decode("utf-8", errors="replace")
        out.extend((p, n, w) for n, w in _hits_in_text(text, forbidden))
    return out


def _git(*args: str) -> str:
    return subprocess.run(["git", *args], check=True, capture_output=True).stdout.decode("utf-8", "replace")


def _report(hits, forbidden, label) -> None:
    for where, n, word in hits:
        print(f"{label(where)}:{n} 금지어 #{forbidden.index(word) + 1}")


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--map", required=True, type=Path)
    ap.add_argument("--check", action="store_true")
    ap.add_argument("folder", nargs="?", type=Path)
    args = ap.parse_args(argv)
    replacements, forbidden = load_map(args.map)

    if args.check:
        files = [Path(f) for f in _git("ls-files", "-co", "--exclude-standard").splitlines() if f]
        hits = find_forbidden(files, forbidden)
        msg_hits = _hits_in_text(_git("log", "--all", "--format=%B"), forbidden)
        _report(hits, forbidden, str)
        _report([("커밋 메시지", n, w) for n, w in msg_hits], forbidden, str)
        if hits or msg_hits:
            return 1
        print("clean")
        return 0

    if args.folder is None:
        ap.error("바꿀 폴더를 주거나 --check 를 쓰세요")
    changed, unreadable = scrub_tree(args.folder, replacements)
    for p in changed:
        print(f"바꿈: {p}")
    for p in unreadable:
        print(f"못 읽음(UTF-8 아님): {p}")
    hits = find_forbidden(_text_files(args.folder), forbidden)
    _report(hits, forbidden, str)
    return 1 if unreadable or hits else 0


if __name__ == "__main__":
    sys.exit(main())
