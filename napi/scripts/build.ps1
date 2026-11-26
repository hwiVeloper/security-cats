# napi 빌드. 기본은 단일 exe(dist\napi.exe), -OneDir 는 폴더형(dist\napi\napi.exe).
param([switch]$OneDir)

$ErrorActionPreference = "Stop"
Set-Location (Split-Path -Parent $PSScriptRoot)

$env:NAPI_ONEDIR = if ($OneDir) { "1" } else { "0" }
$env:PYTHONPATH = Join-Path $PSScriptRoot "pyi_shim"  # DRM 암호화 메타데이터 우회 (shim 설명 참고)
# napi 폴더의 .venv 를 먼저 쓰고, 없으면 저장소 루트(security-cats)의 .venv 를 쓴다
$venv = @(".venv", "..\.venv") | Where-Object { Test-Path "$_\Scripts\pyinstaller.exe" } | Select-Object -First 1
if (-not $venv) { throw "가상환경(.venv)을 찾지 못했어요" }
& "$venv\Scripts\pyinstaller" napi.spec --noconfirm
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed (exit $LASTEXITCODE)" }

if ($OneDir) {
    $dir = "dist\napi"
    $size = (Get-ChildItem $dir -Recurse -File | Measure-Object Length -Sum).Sum
    "{0}: {1:N1} MB" -f $dir, ($size / 1MB)
} else {
    "dist\napi.exe: {0:N1} MB" -f ((Get-Item dist\napi.exe).Length / 1MB)
}
