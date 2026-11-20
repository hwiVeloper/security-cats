# -*- mode: python ; coding: utf-8 -*-
# 빌드: powershell -File scripts/build.ps1  (-OneDir 로 폴더형 빌드)
import os

from PyInstaller.utils.hooks import collect_data_files, collect_submodules

ONEDIR = os.environ.get("NAPI_ONEDIR") == "1"

datas = [("napi/ui", "napi/ui")]
datas += collect_data_files("webview")  # pywebview의 js/ 와 WebView2 로더 dll

# DRM이 entry_points.txt를 암호화해서 hook 자동 탐색이 불가능하다 (scripts/pyi_shim 참고).
# 자동 탐색 대신 필요한 hook 디렉터리를 직접 지정한다.
import _pyinstaller_hooks_contrib
import webview

hookspath = _pyinstaller_hooks_contrib.get_hook_dirs() + [
    os.path.join(os.path.dirname(webview.__file__), "__pyinstaller")]

hiddenimports = (
    collect_submodules("webview.platforms")
    + ["napi.report",  # api.py가 importlib.import_module로 불러서 정적 분석에 안 잡힌다
       "clr", "clr_loader", "pythonnet", "bottle", "proxy_tools"]
)

a = Analysis(
    ["scripts/napi_launcher.py"],
    pathex=["."],
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=hookspath,
    # setuptools는 DRM이 암호화한 .txt 데이터 때문에 import가 실패하고 앱도 쓰지 않는다.
    excludes=["tkinter", "pytest", "setuptools", "pkg_resources", "_distutils_hack"],
)
pyz = PYZ(a.pure)

if ONEDIR:
    exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name="napi",
              console=False, icon="assets/napi.ico")
    coll = COLLECT(exe, a.binaries, a.datas, name="napi")
else:
    exe = EXE(pyz, a.scripts, a.binaries, a.datas, [], name="napi",
              console=False, icon="assets/napi.ico")
