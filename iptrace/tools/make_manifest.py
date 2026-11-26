"""배포 전에 실행: 소스 파일 해시와 함수 이름을 iptrace/manifest.py에 적는다.

    python tools/make_manifest.py

소스를 고칠 때마다 다시 실행한다(테스트 test_manifest_is_up_to_date가 잊으면 알려 준다).
"""
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from iptrace import __version__  # noqa: E402
from iptrace.core import integrity  # noqa: E402


def main():
    files, names = integrity.build_manifest(ROOT)
    text = integrity.render_manifest(files, names, __version__, datetime.now().strftime("%Y-%m-%d %H:%M"))
    (ROOT / integrity.MANIFEST_FILE).write_text(text, encoding="utf-8", newline="\n")
    print(f"{integrity.MANIFEST_FILE}: 파일 {len(files)}개, 모듈 {len(names)}개")


if __name__ == "__main__":
    main()
