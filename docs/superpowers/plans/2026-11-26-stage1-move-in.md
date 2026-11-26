# 1단계 "이사 준비" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 빈 저장소 `security-cats` 에 napi 를 커밋 이력째 `napi/` 로, iptrace 를 사내 정보를 가짜 값으로 바꾼 뒤 `iptrace/` 로 넣고, 두 프로그램이 각자 테스트·빌드되는 상태를 만든다.

**Architecture:** napi 는 임시 복제본을 `git filter-repo --to-subdirectory-filter napi` 로 다시 쓴 뒤 `--allow-unrelated-histories` 로 합친다(이력의 파일 경로가 처음부터 `napi/...` 로 보인다). iptrace 는 git 이력이 없으므로, 복사 → 저장소 밖 치환표로 사내 정보 바꾸기 → 해시 목록 다시 만들기 → 첫 커밋 순서로 넣는다. 가상환경은 저장소 루트 `.venv` 하나를 두 프로그램이 같이 쓴다.

**Tech Stack:** Git for Windows(bash), Python 3.12(`C:\Python312`), git-filter-repo, pytest, PyInstaller.

**Spec:** `docs/superpowers/specs/2026-11-26-security-cats-design.md` (9절 1단계, 3절 제약)

## Global Constraints

- 저장소: `E:\workspace_claude\security-cats` (원격 `github.com/hwiVeloper/security-cats`, **공개**). 작업 브랜치 `stage1/move-in` 은 `docs/design` 브랜치(설계 문서·이 계획서 커밋)에서 만든다.
- napi 원본: `E:\workspace_claude\napi` 의 `main` (원격과 같음, `ba36adc`). 추적하지 않던 `wheelhouse/`, `wheelhouse.7z`, `showcase/`, `.claude/`, `.superpowers/`, `iptrace/` 는 가져오지 않는다.
- iptrace 원본(기준본, 동결): `E:\workspace_claude\napi\iptrace`. **원본은 읽기만 한다.**
- 사내 정보는 iptrace 를 **첫 커밋하기 전에** 바꾼다(설계 9절). **원래 값은 저장소 어디에도(문서·코드·테스트·커밋 메시지) 적지 않는다.** 치환표와 금지어는 저장소 밖 `E:\workspace_claude\security-cats-scrub-map.json` 에만 있다. 형식: `{"replacements": [[원래, 바꿀 값], ...], "forbidden": [금지어, ...]}` — 위에서부터 순서대로 바꾼다. 바꿀 값은 메일 API `https://internal.mailapi.com`, 보안팀 메일 `security@foo.bar`, 로그 경로 `/data/webserver/APP/...`·`/data/webserver/WEB/...`·`/data/webserver/...`.
  - 로그 형식 이름(`webtob_default`, `webtob_error`, `webtob_combined` 등)과 제품 이름 "WebtoB" 는 바꾸지 않는다.
- 커밋 전과 push 전에 `tools/scrub.py --check` 로 저장소 전체(문서 포함)와 커밋 메시지를 검사해 금지어가 하나도 나오지 않아야 한다.
- 실행 중 생기는 파일은 커밋하지 않는다: `config/config.json`, `config/known_hosts`, `data/`, `logs/`, `out/`, `release/`, `dist/`, `build/_work/`, `build/iptrace/`, `build/_backup/`, `codex_demo/`, `반입/`.
- 텍스트 파일에 `.txt` 확장자를 새로 만들지 않는다(DRM).
- 개발 세션에서 `napi.exe`, `iptrace.exe`, Edge/WebView2 를 실행하지 않는다. 빌드만 한다.
- 커밋 메시지 끝: `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`. 원격 push 와 `main` 병합은 사용자가 승인한 뒤에만.

## Review Focus

1. **치환이 놓친 곳**: `.py` 말고 `.md`, `.json`, `.bat`, `.spec`, `.ini`, `.log`, `.list` 에 남은 사내 정보, 대소문자가 다른 표기 → `tools/scrub.py --check` 가 추적 대상 파일 전부를 대소문자 무시로 검사한다(Task 3 테스트).
2. **UTF-8 이 아닌 텍스트 파일**: 치환 스크립트가 못 읽는 파일을 조용히 건너뛰면 사내 정보가 남는다 → 못 읽은 파일 목록을 출력하고 종료 코드 1 로 멈춘다(Task 3 테스트).
3. **실행 데이터가 커밋되는 것**: iptrace 원본 폴더에는 실제 서버 정보가 든 `config/config.json`, `data/history.db`, `logs/` 가 있다 → Task 3 에서 `git ls-files` 로 없는지 확인한다.
4. **줄바꿈 변환**: autocrlf 로 줄바꿈이 바뀌어 napi 의 "명령 문자열 정확히 비교" 테스트가 깨질 수 있다 → Task 2 는 합친 뒤의 작업 트리에서 napi 테스트 전체를 다시 돌린다.
5. **스크린샷에 남은 옛 경로**: PNG 는 글자 검색이 안 된다 → Task 4 에서 다시 만든 이미지 2장을 눈으로 확인하고, 못 만들면 저장소에 넣지 않는다.

---

### Task 1: 작업 브랜치와 루트 가상환경

**Files:**
- Create: `.gitignore`
- Create: `.venv/` (커밋 안 함)

**Interfaces:**
- Produces: 저장소 루트 `.venv` — Python 3.12, 두 프로그램의 의존성(napi: pywebview, paramiko, PyYAML, requests / iptrace: openpyxl, paramiko / 개발: pytest, pyinstaller, Pillow) + `git-filter-repo`. 이후 모든 명령은 `.venv/Scripts/python` 으로 실행.

- [ ] **Step 1: 브랜치 만들기**

Run: `cd /e/workspace_claude/security-cats && git switch -c stage1/move-in docs/design`
Expected: `Switched to a new branch 'stage1/move-in'`

- [ ] **Step 2: 루트 `.gitignore` 쓰기**

내용(이 줄들만): `.venv/`, `__pycache__/`, `*.pyc`, `.pytest_cache/`, `.superpowers/`, `.claude/`, `wheelhouse/`, `*scrub-map*.json`

- [ ] **Step 3: 가상환경 만들고 설치**

Run:
```bash
/c/Python312/python -m venv .venv
.venv/Scripts/python -m pip install --find-links /e/workspace_claude/napi/wheelhouse "pywebview>=5" "paramiko>=3.4" "PyYAML>=6" requests "openpyxl>=3.1" pytest pyinstaller Pillow git-filter-repo
```
openpyxl·git-filter-repo 는 wheelhouse 에 없으므로 인터넷에서 받는다.

- [ ] **Step 4: 설치 확인**

Run: `.venv/Scripts/python -c "import webview, paramiko, yaml, requests, openpyxl, pytest, PyInstaller, tkinter; print('ok')" && .venv/Scripts/git-filter-repo --version`
Expected: `ok` 와 버전 문자열

- [ ] **Step 5: Commit**

```bash
git add .gitignore
git commit -m "chore: 루트 .gitignore"
```

---

### Task 2: napi 를 이력째 `napi/` 로 들이기

**Files:**
- Create: `napi/**` (napi 저장소의 추적 파일 전부, 이력 포함)
- Modify: `napi/scripts/build.ps1` (가상환경 위치)

**Interfaces:**
- Consumes: Task 1 의 `.venv`
- Produces: `napi/` 아래에서 `../.venv/Scripts/python -m pytest` 로 napi 테스트 전부 통과, `powershell -File napi/scripts/build.ps1` 로 `napi/dist/napi.exe` 생성.

- [ ] **Step 1: 임시 복제본을 `napi/` 아래로 다시 쓰기** (임시 폴더는 `/e/workspace_claude/_napi-import`, 저장소 밖)

Run:
```bash
rm -rf /e/workspace_claude/_napi-import
git clone --no-local /e/workspace_claude/napi /e/workspace_claude/_napi-import
cd /e/workspace_claude/_napi-import && /e/workspace_claude/security-cats/.venv/Scripts/git-filter-repo --to-subdirectory-filter napi --force
git log --oneline -1 && git ls-files | head -3
```
Expected: 마지막 커밋 제목이 `feat: 스캔 결과를 엑셀(xlsx) 리포트로 내보내기`, 파일 경로가 `napi/` 로 시작

- [ ] **Step 2: security-cats 로 합치고 임시 폴더 지우기**

Run:
```bash
cd /e/workspace_claude/security-cats && git pull --no-rebase --allow-unrelated-histories --no-edit /e/workspace_claude/_napi-import main
rm -rf /e/workspace_claude/_napi-import
```
Expected: 병합 커밋 생성, 충돌 없음

- [ ] **Step 3: 들어온 파일·이력 확인**

Run:
```bash
diff <(git -C /e/workspace_claude/napi ls-files | sed 's|^|napi/|' | sort) <(git ls-files napi | sort) && echo SAME
git log --oneline -- napi/napi/api.py | wc -l
ls napi/wheelhouse napi/showcase napi/iptrace 2>&1 | grep -c "No such file"
```
Expected: `SAME`, 이력 줄 수 10 이상, 마지막 줄 `3`

- [ ] **Step 4: napi 테스트 (합친 작업 트리에서)**

Run: `cd napi && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ../.venv/Scripts/python -m pytest -q 2>&1 | tail -1`
Expected: `737 passed, 2 skipped`

- [ ] **Step 5: `build.ps1` 가 루트 가상환경도 찾게 고치기**

`napi/scripts/build.ps1` 에서 `& .venv\Scripts\pyinstaller` 를, napi 폴더에 `.venv` 가 있으면 그것을, 없으면 `..\.venv` 를 쓰게 바꾼다. 둘 다 없으면 `throw "가상환경(.venv)을 찾지 못했어요"`.

- [ ] **Step 6: napi 빌드 확인**

Run: `powershell -NoProfile -ExecutionPolicy Bypass -File napi/scripts/build.ps1 2>&1 | tail -1`
Expected: `dist\napi.exe: 18.x MB` (실행은 하지 않는다). `git status -s napi` 에 `dist/`·`build/` 가 안 보여야 한다(napi 의 `.gitignore` 가 막음).

- [ ] **Step 7: Commit**

```bash
git add napi/scripts/build.ps1
git commit -m "build(napi): 저장소 루트 .venv 로도 빌드"
```

---

### Task 3: iptrace 를 사내 정보를 바꿔서 `iptrace/` 로 들이기

**Files:**
- Create: `tools/scrub.py` (범용 치환·검사 스크립트. 원래 값은 담지 않는다)
- Test: `tools/test_scrub.py` (가짜 치환표로만 테스트)
- Create: `iptrace/**` (원본에서 복사, 스크린샷 제외)
- Modify (치환 결과): 치환표에 걸리는 `iptrace/` 아래 파일 전부 (예상: `iptrace/core/config.py`, `README.md`, `CHANGELOG.md`, `tests/test_mailer.py`, `config/config.example.json`, `iptrace/core/collector.py`, `iptrace/gui/settings_page.py`, `PLAN.md`, `tools/capture_screens.py`, 일부 테스트)
- Regenerate: `iptrace/iptrace/manifest.py`

**Interfaces:**
- Consumes: Task 1 의 `.venv`
- Produces (`tools/scrub.py`):
  - `TEXT_SUFFIXES: frozenset[str]` = `.py .md .json .bat .spec .ini .in .log .list .cfg .toml .yaml .yml .html .css .js .mjs .ts .tsx .csv .ps1 .gitignore .gitattributes`
  - `load_map(path: Path) -> tuple[list[tuple[str, str]], list[str]]` — (치환 목록, 금지어)
  - `scrub_tree(root: Path, replacements: list[tuple[str, str]]) -> tuple[list[Path], list[Path]]` — (바꾼 파일, 못 읽은 파일). 텍스트 확장자만, 위에서부터 순서대로 `str.replace`. 줄바꿈·BOM 은 원래 바이트 그대로 유지.
  - `find_forbidden(files: list[Path], forbidden: list[str]) -> list[tuple[Path, int, str]]` — (파일, 줄 번호, 걸린 금지어 원문). 대소문자 무시. 텍스트 확장자만.
  - 명령행:
    - `python tools/scrub.py --map <json> <폴더>` — 폴더를 치환한 뒤 그 폴더를 검사. 못 읽은 파일이나 남은 금지어가 있으면 목록을 출력하고 종료 코드 1, 없으면 바꾼 파일 목록을 출력하고 0.
    - `python tools/scrub.py --map <json> --check` — 저장소 루트에서 실행. `git ls-files -co --exclude-standard` 의 파일 전부 + `git log --all --format=%B`(커밋 메시지) 를 검사. 걸린 것이 있으면 위치를 출력하고 1, 없으면 `clean` 을 출력하고 0. **걸린 금지어를 출력할 때는 금지어 자체 대신 치환표의 순번(`#3`)만 쓴다**(검사 출력이 로그나 이슈에 붙어도 원래 값이 새지 않게).

- [ ] **Step 1: 치환 스크립트 테스트 쓰기** (`tools/test_scrub.py`, 가짜 치환표만)

```python
REPL = [("https://mail.real.example", "https://internal.mailapi.com"),
        ("sec@real.example", "security@foo.bar"),
        ("/data/websrv/AAA/", "/data/webserver/APP/"),
        ("/data/websrv/", "/data/webserver/")]
FORBID = ["real.example", "/AAA/", "/data/websrv"]

def test_replaces_in_order_and_keeps_crlf(tmp_path):
    f = tmp_path / "a.py"
    f.write_bytes(b'U="https://mail.real.example"\r\nT="sec@real.example"\r\n'
                  b'P="/data/websrv/AAA/log/*.log*"\r\nR="/data/websrv/log"\r\nF="webtob_default"\r\n')
    changed, unreadable = scrub_tree(tmp_path, REPL)
    assert changed == [f] and unreadable == []
    assert f.read_bytes() == (b'U="https://internal.mailapi.com"\r\nT="security@foo.bar"\r\n'
                              b'P="/data/webserver/APP/log/*.log*"\r\nR="/data/webserver/log"\r\nF="webtob_default"\r\n')

def test_reports_unreadable_and_skips_binary(tmp_path):
    (tmp_path / "cp949.md").write_bytes("보안팀 real.example".encode("cp949"))
    (tmp_path / "img.png").write_bytes(b"\x89PNG\r\n\x1a\nreal.example")
    changed, unreadable = scrub_tree(tmp_path, REPL)
    assert unreadable == [tmp_path / "cp949.md"] and changed == []

def test_find_forbidden_is_case_insensitive(tmp_path):
    f = tmp_path / "x.json"
    f.write_text('{"a": 1}\n{"to": "a@REAL.Example"}', encoding="utf-8")
    assert find_forbidden([f], FORBID) == [(f, 2, "real.example")]

def test_load_map(tmp_path):
    m = tmp_path / "m.json"
    m.write_text('{"replacements": [["a", "b"]], "forbidden": ["a"]}', encoding="utf-8")
    assert load_map(m) == ([("a", "b")], ["a"])
```

- [ ] **Step 2: 실패 확인**

Run: `.venv/Scripts/python -m pytest tools/test_scrub.py -q -p no:cacheprovider`
Expected: FAIL (import 오류)

- [ ] **Step 3: `tools/scrub.py` 구현** (Interfaces 의 이름·시그니처·명령행 그대로)

- [ ] **Step 4: 통과 확인**

Run: `.venv/Scripts/python -m pytest tools/test_scrub.py -q -p no:cacheprovider`
Expected: `4 passed`

- [ ] **Step 5: 원본 기준 테스트 수 재기** (원본 폴더는 바꾸지 않는다)

Run: `cd /e/workspace_claude/napi/iptrace && /e/workspace_claude/security-cats/.venv/Scripts/python -m pytest -q 2>&1 | tail -1`
Expected: 통과/건너뜀 수를 적어 둔다(이후 비교 기준). 실패가 있으면 그 목록도 적어 두고 사용자에게 알린다.

- [ ] **Step 6: 복사 (실행 데이터·빌드 결과·스크린샷 제외)**

`shutil.copytree(원본, "iptrace", ignore=...)` 로 복사한다. 빼는 것:
- 어디에 있든 이 이름의 폴더: `.venv`, `dist`, `dist_new`, `release`, `logs`, `data`, `out`, `codex_demo`, `반입`, `__pycache__`, `.pytest_cache`, `screens`
- `build/` 바로 아래의 `iptrace`, `_work`, `_backup` 폴더
- `config/config.json`, `config/known_hosts`, `*.pyc`

Run (복사 후): `ls iptrace/config iptrace/build`
Expected: `iptrace/config` 에 `config.example.json` 은 있고 `config.json`·`known_hosts` 는 없다. `iptrace/build` 에는 `entry.py`, `iptrace.spec`, `version_info.py` 만.

- [ ] **Step 7: 치환 실행**

Run: `.venv/Scripts/python tools/scrub.py --map /e/workspace_claude/security-cats-scrub-map.json iptrace; echo "exit=$?"`
Expected: 바꾼 파일 목록과 `exit=0`. 1 이면 멈추고 사용자에게 보고(못 읽은 파일이나 남은 금지어 위치).

- [ ] **Step 8: 해시 목록 다시 만들기**

Run: `cd iptrace && ../.venv/Scripts/python tools/make_manifest.py && ../.venv/Scripts/python -m iptrace doctor; echo "exit=$?"`
Expected: doctor 가 문제 없음으로 끝나고 `exit=0`

- [ ] **Step 9: iptrace 테스트**

Run: `cd iptrace && ../.venv/Scripts/python -m pytest -q 2>&1 | tail -1`
Expected: Step 5 와 같은 통과/건너뜀 수

- [ ] **Step 10: iptrace 빌드 확인** (실행은 하지 않는다)

Run: `cd iptrace && ../.venv/Scripts/python -m PyInstaller build/iptrace.spec --clean --noconfirm 2>&1 | tail -1 && ls dist/iptrace/iptrace.exe dist/iptrace/iptrace-cli.exe`
Expected: 두 exe 가 있다. `git status -s iptrace` 에 `dist/`·`build/iptrace/`·`build/_work/` 가 안 보인다.

- [ ] **Step 11: 커밋 전 검사**

Run:
```bash
git add tools iptrace
git ls-files iptrace | grep -E -i "config/config\.json|known_hosts|\.db$|^iptrace/(logs|out|data|release|dist)/|\.png$" ; echo "runtime-files: $?"
.venv/Scripts/python tools/scrub.py --map /e/workspace_claude/security-cats-scrub-map.json --check
```
Expected: `runtime-files: 1` (찾은 것 없음), 그리고 `clean`

- [ ] **Step 12: Commit** (메시지에 원래 값을 쓰지 않는다)

```bash
git commit -m "feat: iptrace 들이기 (사내 정보는 가짜 값으로)"
```

---

### Task 4: iptrace 스크린샷 다시 만들기

**Files:**
- Create: `iptrace/screens/*.png` (다시 만든 것만)

**Interfaces:**
- Consumes: Task 3 의 치환된 `iptrace/tools/capture_screens.py`(경로가 `/data/webserver/...` 로 바뀐 상태)
- Produces: 새 경로가 찍힌 스크린샷 20장, 또는 "저장소에 스크린샷 없음" 결정 (README 에 원본 위치 안내)

- [ ] **Step 1: 캡처 실행** (tkinter 창이 잠깐 떴다 사라진다. Edge·exe 가 아니므로 개발 세션에서 실행해도 된다)

Run: `cd iptrace && mkdir -p screens && ../.venv/Scripts/python tools/capture_screens.py 2>&1 | tail -3; ls screens/*.png | wc -l`
Expected: `captured ...` 줄들과 `20`

- [ ] **Step 2: 눈으로 확인** — `screens/06_서버설정_서버.png`, `screens/03_로그점검_결과.png` 를 열어 로그 경로가 `/data/webserver/APP/...` 로 보이고, 치환표의 원래 폴더 이름이 보이지 않는지 본다.

- [ ] **Step 3: Commit** (캡처가 됐을 때만)

```bash
git add iptrace/screens
git commit -m "docs(iptrace): 가짜 경로로 스크린샷 다시 만들기"
```

캡처가 안 되면(오류, 장수 부족, 옛 경로가 보임): `iptrace/screens/` 를 지우고 커밋하지 않는다. Task 5 README 에 "기능 기준 스크린샷은 저장소에 없고 원본 폴더에 있다"고 적는다.

---

### Task 5: 루트 README 와 마무리 검사

**Files:**
- Create: `README.md`

**Interfaces:**
- Consumes: Task 2·3·4 결과

- [ ] **Step 1: `README.md` 쓰기** — 들어갈 내용:
  - 한 줄 소개: "보안냥이 — 보안 대응 기능을 고양이로 하나씩 붙여 가는 데스크톱 프로그램"
  - 지금 상태: 1단계(이사 준비) 끝. `napi/`(냅냥이 될 napi), `iptrace/`(탐정냥이 될 iptrace)는 이관용 원본이고 6단계에서 지운다.
  - 문서: 설계 `docs/superpowers/specs/2026-11-26-security-cats-design.md`, 시안 폴더, 단계 계획 `docs/superpowers/plans/`.
  - 개발 준비: Task 1 Step 3 의 가상환경 명령.
  - 테스트: `cd napi && ../.venv/Scripts/python -m pytest -q`, `cd iptrace && ../.venv/Scripts/python -m pytest -q`, `.venv/Scripts/python -m pytest tools -q`
  - 빌드: `powershell -File napi/scripts/build.ps1`, `cd iptrace && ../.venv/Scripts/python -m PyInstaller build/iptrace.spec --clean --noconfirm`
  - 사내 정보: iptrace 의 메일 주소·로그 경로 기본값은 가짜 값이다. 실제 값은 설정 화면에서 넣는다. 공개 저장소라 실제 값은 커밋하지 않으며, push 전 `tools/scrub.py --check` 로 검사한다(치환표는 저장소 밖에 둔다).
  - (Task 4 가 실패했을 때만) 스크린샷 안내.

- [ ] **Step 2: 마지막 전체 검사**

Run:
```bash
git add README.md
.venv/Scripts/python tools/scrub.py --map /e/workspace_claude/security-cats-scrub-map.json --check
(cd napi && PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 ../.venv/Scripts/python -m pytest -q 2>&1 | tail -1)
(cd iptrace && ../.venv/Scripts/python -m pytest -q 2>&1 | tail -1)
.venv/Scripts/python -m pytest tools -q -p no:cacheprovider 2>&1 | tail -1
git status -s
```
Expected: `clean`, napi `737 passed, 2 skipped`, iptrace 는 Task 3 Step 5 와 같은 수, tools `4 passed`, `git status` 에는 스테이징된 `README.md` 만.

- [ ] **Step 3: Commit**

```bash
git commit -m "docs: 저장소 README (1단계 이사 준비)"
```

- [ ] **Step 4: 사용자에게 보고하고 승인받기** — `stage1/move-in` 을 `main` 으로 만들어 push 할지 묻는다. 승인되면 push 직전에 Step 2 의 `--check` 를 한 번 더 돌린 뒤 `git switch -c main stage1/move-in && git push -u origin main` (`main` 은 아직 커밋이 없다).
