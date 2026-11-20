# napi (냅냥)

Swagger/OpenAPI 명세와 서버의 접근 로그를 비교해서 **사용되지 않는(잠자는) API**를 찾아 주는 Windows 데스크톱 도구입니다.
낮잠 자는 고양이 냅냥이 안내합니다.

## 설치 및 실행

### exe로 쓰기 (권장)

`dist\napi.exe` 하나만 있으면 됩니다. Python은 필요 없고, 더블클릭하면 창이 열립니다.
Windows 10/11 에는 보통 WebView2 런타임이 이미 들어 있습니다. 없으면 실행 시 안내 창이 뜨니
안내된 주소에서 설치해 주세요.

### 소스에서 실행

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
.venv\Scripts\python -m napi
```

Python 3.12 이상이 필요합니다.

## 설정 파일 공유

설정은 `%APPDATA%\napi\napi-config.yaml` 에 저장됩니다 (설정 화면에서 저장하면 생깁니다).
서비스, 서버 주소, 로그 경로, 로그 형식, 판정 기준(임계값), 제외 경로, 메모가 들어 있습니다.

- 팀원과 공유하려면 이 파일을 그대로 전달하고, 받는 사람은 같은 위치에 넣으면 됩니다.
- 파일에 **비밀번호는 들어 있지 않습니다.** 공유해도 안전하지만, 서버 주소와 계정 이름은 들어 있으니 사내에서만 돌려 주세요.
- 비밀번호는 접속할 때 입력하며, 그 세션 동안 메모리에만 있습니다.
- `NAPI_HOME` 환경 변수를 지정하면 `%APPDATA%\napi` 대신 그 폴더를 씁니다.

## 보안 원칙

- **원본 로그를 가져오지 않습니다.** 로그 집계는 서버에서 awk로 수행하고, 경로별 호출 수 같은 집계 결과만 내려받습니다. 로그 원문은 서버 밖으로 나가지 않습니다.
- **비밀번호를 저장하지 않습니다.** 설정 파일, 결과 파일, 로그 어디에도 남기지 않습니다.
- **읽기 전용 명령만 실행합니다.** 서버에서는 파일 조회와 집계 명령만 쓰며, 서버의 파일을 만들거나 바꾸거나 지우지 않습니다.
- SSH 호스트 키는 처음 접속할 때 확인을 받아 `%APPDATA%\napi\known_hosts` 에 기록합니다.

## 로그 형식 맞추는 법

설정 화면에서 서비스별로 로그 형식을 고릅니다.

| 형식 | 설명 |
| --- | --- |
| Apache (common) | `%h %l %u %t "%r" %>s %b` |
| Apache (combined) | common 뒤에 Referer, User-Agent 추가 |
| nginx (combined) | nginx 기본 `combined` |
| WebtoB | WebtoB 접근 로그 |
| 직접 지정 | 공백(스페이스/탭)으로 나눈 **N번째 필드**를 지정 |

**직접 지정**에서는 메서드, 경로, 상태 코드, 시간이 각각 몇 번째 필드(1부터)인지 입력합니다.
경로 필드의 양끝 따옴표는 자동으로 벗겨지고, 시간은 `yyyy-mm-dd` 로 시작하는 ISO8601 형식이어야 합니다.
형식이 맞지 않는 줄은 건너뛰며, 결과 화면에 건너뛴 줄 수가 표시됩니다.

경로는 비교 전에 정규화됩니다. 쿼리(`?`, `#`)와 `;세션` 부분을 떼고, 숫자·UUID·긴 해시처럼 보이는 조각은 `{*}` 로 바꿔서
`/users/{id}` 같은 명세 경로와 맞춥니다.

## 빌드 방법

```powershell
powershell -File scripts\build.ps1            # 단일 exe: dist\napi.exe
powershell -File scripts\build.ps1 -OneDir    # 폴더형: dist\napi\napi.exe
```

- 개발 의존성(PyInstaller, Pillow)은 `.venv` 에 설치되어 있어야 합니다: `.venv\Scripts\pip install -e ".[dev]"`
- 아이콘은 `assets\napi.svg` 를 Pillow로 다시 그려서 만듭니다: `.venv\Scripts\python scripts\make_icon.py` (`assets\napi.ico`).
- `scripts\pyi_shim` 은 빌드할 때만 쓰는 보조 파일입니다. 아래 환경 메모를 보세요.

## 테스트

```powershell
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
.venv\Scripts\python -m pytest
```

### 환경 메모: `.txt` 파일을 암호화하는 PC

문서 보안(DRM) 에이전트가 `*.txt` 파일을 암호화하는 PC에서는 패키지의 `entry_points.txt` 를 읽지 못해
pytest 플러그인 자동 로드가 실패합니다. 그래서 테스트는 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1` 로 실행합니다.
같은 이유로 PyInstaller 빌드에서도 hook 자동 탐색과 setuptools import가 실패하므로,
`napi.spec` 에서 hook 디렉터리를 직접 지정하고 setuptools를 제외했으며 `scripts\build.ps1` 이 `scripts\pyi_shim` 을 PYTHONPATH에 넣습니다.
이 프로젝트는 `.txt` 파일을 만들지 않습니다. 텍스트 파일이 필요하면 확장자 없이 만드세요.

### 실제 Edge로 PDF 만들기 테스트

PDF 내보내기는 Edge를 headless로 실행합니다. 이 테스트는 기본으로 건너뛰고, 직접 확인할 때만 켭니다.

```powershell
$env:NAPI_RUN_EDGE_TESTS = "1"
$env:PYTEST_DISABLE_PLUGIN_AUTOLOAD = "1"
.venv\Scripts\python -m pytest tests\test_report.py
```
