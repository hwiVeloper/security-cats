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

# 로딩 화면: 부트로더가 exe 압축 해제(수 초) 전에 바로 띄우고, napi/__main__.py 의 splash()가
# 단계별 진행률을 갱신한 뒤 창이 다 그려지면(window.events.loaded) 닫는다.
# 이미지는 scripts/make_splash.py 로 만든다 (assets/splash.png).
#
# 진행 막대: 부트로더는 파일을 하나 풀 때마다 status_text 에 파일 이름을 넣는다.
# 파일 이름 대신 진행률을 보여주도록 기본 Tcl 스크립트 끝에 처리기를 덧붙인다.
#   - 파일 이름이 들어오면: 푼 파일 수 / 전체 파일 수 → 0~75%
#   - "@<퍼센트>|<문구>" 가 들어오면 (splash()): 해당 퍼센트·문구 표시
# 막대는 줄어들지 않는다(최대값 유지). 좌표는 scripts/make_splash.py 의 BAR_* / TEXT_Y 와 맞춘다.
# NAPI_SPLASH_DEBUG 환경 변수에 파일 경로를 주면 받은 메시지·오류를 기록한다.
SPLASH_PROGRESS_TCL_BODY = r"""
trace remove variable status_text write [list canvas_text_update .root.canvas vartext]
set np_total __TOTAL__
set np_count 0
set np_pct 0
set np_label "프로그램 파일을 준비하는 중…"
.root.canvas create rectangle 40 212 520 218 -fill #EFE5DA -outline "" -tags np_track
.root.canvas create rectangle 40 212 40 218 -fill #F29A3F -outline "" -tags np_bar
.root.canvas create text 520 246 -fill #B8621A -font myFont -anchor se -tags np_pctx
proc np_render {} {
    global np_pct np_label
    set x [expr {40 + int(480 * $np_pct / 100.0)}]
    .root.canvas coords np_bar 40 212 $x 218
    .root.canvas itemconfigure vartext -text $np_label
    .root.canvas itemconfigure np_pctx -text "${np_pct}%"
}
proc np_update {args} {
    global status_text np_total np_count np_pct np_label env
    set p $np_pct
    if {[string index $status_text 0] eq "@"} {
        set i [string first "|" $status_text]
        if {$i > 1} {
            set p [string range $status_text 1 [expr {$i - 1}]]
            set np_label [string range $status_text [expr {$i + 1}] end]
        }
        if {[info exists env(NAPI_SPLASH_DEBUG)]} {
            catch {set f [open $env(NAPI_SPLASH_DEBUG) a]; fconfigure $f -encoding utf-8; puts $f "$np_count/$np_total $status_text"; close $f}
        }
    } else {
        incr np_count
        set p [expr {min(75, int(75.0 * $np_count / $np_total))}]
    }
    if {[string is integer -strict $p] && $p > $np_pct} {
        set np_pct [expr {min($p, 100)}]
    }
    np_render
}
trace add variable status_text write np_update
np_render
"""
# 오류가 나면 원래 동작(문구 표시)으로 되돌리고, 진단용 환경 변수가 있으면 오류 내용을 기록한다.
SPLASH_PROGRESS_TCL = (
    "\nif {[catch {" + SPLASH_PROGRESS_TCL_BODY + "} np_err]} {\n"
    "    catch {trace add variable status_text write [list canvas_text_update .root.canvas vartext]}\n"
    "    if {[info exists env(NAPI_SPLASH_DEBUG)]} {\n"
    "        catch {set f [open $env(NAPI_SPLASH_DEBUG) a]; puts $f \"ERROR: $::errorInfo\"; close $f}\n"
    "    }\n"
    "}\n"
)


class ProgressSplash(Splash):
    """파일 이름 대신 진행 막대(0~100%)를 표시하는 로딩 화면."""

    def assemble(self):
        # 부트로더는 압축을 푸는 모든 파일(스플래시용 Tcl/Tk 포함)마다 진행 알림을 보낸다.
        total = len(a.binaries) + len(a.datas)
        script = self.generate_script() + SPLASH_PROGRESS_TCL.replace("__TOTAL__", str(max(1, total)))
        # PyInstaller(SplashWriter)는 스크립트 길이를 '문자 수'로 기록하고 내용은 UTF-8 바이트로 쓴다.
        # 한글(3바이트)이 있으면 부트로더가 스크립트 끝부분을 잘라 실행하므로, 비 ASCII 문자를
        # Tcl \uXXXX 이스케이프로 바꿔 순수 ASCII 로 만든다 (Tcl 이 실행 시 한글로 해석).
        self.script = "".join(c if ord(c) < 128 else "\\u%04x" % ord(c) for c in script)
        with open(self.script_name, "w", encoding="utf-8") as f:  # 실제 실행되는 스크립트를 build/ 에 남김
            f.write(self.script)
        super().assemble()


# 스플래시는 tkinter 와 별개로 Tcl/Tk 를 직접 챙겨 넣으므로 excludes 의 "tkinter" 와 함께 쓸 수 있다.
splash = ProgressSplash(
    "assets/splash.png",
    binaries=a.binaries,
    datas=a.datas,
    text_pos=(40, 246),       # 진행 막대 아래, 왼쪽 아래 기준 (진행 단계 문구)
    text_size=10,
    # PyInstaller 가 Tcl 스크립트에 따옴표 없이 넣으므로 공백 있는 글꼴명은 {} 로 감싸야 한다
    text_font="{Malgun Gothic}",
    text_color="#8A7A6E",
    text_default="프로그램 파일을 준비하는 중…",
    always_on_top=True,       # 다른 창이 활성화돼도 로딩 중임을 계속 보이게 (창이 뜨면 닫힘)
)

if ONEDIR:
    exe = EXE(pyz, a.scripts, splash, [], exclude_binaries=True, name="napi",
              console=False, icon="assets/napi.ico")
    coll = COLLECT(exe, splash.binaries, a.binaries, a.datas, name="napi")
else:
    exe = EXE(pyz, a.scripts, splash, splash.binaries, a.binaries, a.datas, [], name="napi",
              console=False, icon="assets/napi.ico")
