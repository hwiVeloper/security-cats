@echo off
chcp 65001 >nul
cd /d "%~dp0"
set PYTHONIOENCODING=utf-8
rem 실행 환경 점검 (doctor): 빠진 파일, 설정 오류, DRM 암호화, 규칙·이력 파일을 확인한다
rem   만든 exe가 있으면 dist\iptrace\iptrace-cli.exe 로, 없으면 소스(python)로 점검

if exist "dist\iptrace\iptrace-cli.exe" (
    echo [exe 점검] dist\iptrace
    "dist\iptrace\iptrace-cli.exe" doctor
) else (
    echo [소스 점검]
    python -m iptrace doctor
)

echo.
pause
