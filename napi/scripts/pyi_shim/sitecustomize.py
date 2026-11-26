"""빌드 전용 shim (PYTHONPATH로만 로드된다, 앱에는 포함되지 않는다).

회사 DRM이 일부 dist-info의 entry_points.txt를 암호화해서 importlib.metadata가
UnicodeDecodeError를 낸다. 이 파일들을 '읽을 수 없는 메타데이터'로 보고 건너뛰게 해서
PyInstaller의 hook 탐색이 죽지 않게 한다. DRM 자체는 건드리지 않는다.
"""
import importlib.metadata as _md

_orig = _md.PathDistribution.read_text


def _read_text(self, filename):
    try:
        return _orig(self, filename)
    except UnicodeDecodeError:
        return None


_md.PathDistribution.read_text = _read_text
