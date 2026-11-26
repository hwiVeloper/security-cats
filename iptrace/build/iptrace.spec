# -*- mode: python ; coding: utf-8 -*-
# PyInstaller 빌드 설정 (PLAN.md 9장)
#   pyinstaller build\iptrace.spec --clean --noconfirm
# 결과: dist\iptrace\ 폴더 (iptrace.exe = GUI, iptrace-cli.exe = CLI, _internal\ 공유)
import os

ROOT = os.path.abspath(os.path.join(SPECPATH, ".."))
VERSION = os.path.join(SPECPATH, "version_info.py")

a = Analysis(
    [os.path.join(SPECPATH, "entry.py")],
    pathex=[ROOT],
    datas=[
        # 실행에 필요한 데이터는 공격 규칙 하나뿐이다 (config.json은 첫 실행 때 기본값으로 만들어진다)
        (os.path.join(ROOT, "rules", "signatures.json"), "rules"),
    ],
    hiddenimports=[],
    excludes=["pytest", "PyInstaller", "setuptools", "pip"],
    noarchive=False,
)
# 패키지 정보 중 .txt(top_level.txt, entry_points.txt)는 실행에 쓰지 않는다. 문서 보안(DRM)이 .txt를 암호화해
# exe 폴더·배포 zip에 깨진 파일이 섞이므로 넣지 않는다 (버전 정보가 든 METADATA는 그대로 둔다)
a.datas = [d for d in a.datas if not (".dist-info" in d[0].replace("\\", "/") and d[0].lower().endswith(".txt"))]
pyz = PYZ(a.pure)

gui = EXE(pyz, a.scripts, [], exclude_binaries=True, name="iptrace", console=False,
          upx=False, version=VERSION)
cli = EXE(pyz, a.scripts, [], exclude_binaries=True, name="iptrace-cli", console=True,
          upx=False, version=VERSION)

coll = COLLECT(gui, cli, a.binaries, a.datas, name="iptrace", upx=False)
