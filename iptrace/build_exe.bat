@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
rem exe 다시 만들기: 소스 점검(doctor) 후 PyInstaller 빌드 -> dist\iptrace\
rem 명령 프롬프트에서 직접 치는 것과 같은 python 을 쓴다 (PyInstaller·paramiko·openpyxl이 설치돼 있어야 함)
rem 주의: 빌드하면 dist\iptrace\ 폴더를 통째로 새로 만든다. 그 안에서 쓰던 config\·data\는 지워지니 exe는 다른 폴더에 복사해 쓴다

python -m iptrace doctor
if errorlevel 1 (
    echo.
    echo [주의] 점검에서 문제가 나왔습니다. 위 내용을 확인하세요.
    echo        그래도 빌드하려면 아무 키나 누르고, 멈추려면 이 창을 닫으세요.
    pause >nul
)

python -m PyInstaller build\iptrace.spec --clean --noconfirm

echo.
pause
