@echo off
chcp 65001 >nul
setlocal
cd /d "%~dp0"
rem GitLab Release에 올릴 zip과 릴리스 설명 만들기 (release\ 폴더). 먼저 build_exe.bat로 exe를 만든 뒤 실행
set PYTHONIOENCODING=utf-8

if exist ".venv\Scripts\python.exe" (
    .venv\Scripts\python tools\make_release.py
) else (
    where py >nul 2>nul && (py -3 tools\make_release.py) || (python tools\make_release.py)
)

echo.
pause
