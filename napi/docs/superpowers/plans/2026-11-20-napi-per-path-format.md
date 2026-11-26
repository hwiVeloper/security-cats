# napi — 경로별 로그 형식 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 로그 형식을 서버 단위가 아니라 로그 경로(glob)마다 지정한다.

**Architecture:**
- **모델:** `ServerConfig.log_paths: list[LogPath]`로 바꾼다.
- **서버 쪽 집계:** awk 프로그램 하나에 형식별 파싱 함수를 넣는다. 셸 파일 목록이 파일마다 형식 번호를 실어 보내고, awk는 파일 표시 줄의 번호로 파싱 함수를 고른다.
- **화면:** 경로 줄마다 형식 드롭다운을 둔다.

**Tech Stack:** 기존과 같다.

**Spec:** `docs/superpowers/specs/2026-11-20-napi-per-path-format-design.md` (기반: `2026-11-20-napi-formats-paths-password-design.md`, `2026-11-19-napi-design.md`)

## Global Constraints

- **v1/확장 보안 원칙 유지:**
  - 원본 로그가 PC로 넘어오지 않는다. 출력은 F/R/P/Q/T/E/D 줄만 낸다.
  - 원격 명령은 읽기 전용이다. `timeout`, `nice`, `ionice`로 감싼다.
  - 마커는 토큰에 묶여 있고 줄 맨 앞에서만 인정한다.
  - 모든 사용자 값은 `shlex.quote`로 감싼다.
  - 사용자 패턴은 `_awk_str` 문자열 리터럴로만 awk에 들어간다.
  - 비밀값은 저장하지 않는다.
- **FORMAT PARSE CONTRACT**와 R4~R7, 그리고 최종 리뷰에서 고친 규칙(`%U` 첫 토큰, 월/일 범위)은 그대로 유지한다.
- **원격 출력 F 줄 형식은 바뀌지 않는다:** `F\tpath\tmin\tmax\tlines\tpf\toor`. 형식 번호는 서버 안에서만 쓴다.
- **같은 파일 중복:** 한 파일이 경로 두 개 이상에 걸리면, `log_paths` 목록에서 먼저 나온 경로의 형식으로 **한 번만** 읽는다(결정적이어야 함).
- **파일 목록 정렬:** 항상 `LC_ALL=C`로 한다.
- **환경:**
  - 테스트: `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/Scripts/python -m pytest -k "not with_edge"`
  - msedge.exe, napi.exe, `python -m napi`는 실행 금지다.
  - `.txt` 파일을 만들지 않는다.
- **pywebview 제약:** 공개 Api 메서드의 매개변수 이름에 JS 전역 이름(`window` 등)을 쓰지 않는다. `test_js_safe_param_names`가 이를 막는다.
- 사용자에게 보이는 문구는 모두 한국어다.

## Review Focus

1. **v1/확장 설정 파일 호환:** `log_glob` 하나, 또는 `log_globs`와 서버 `log_format`/`custom_format` 조합이 각 경로에 형식을 물려준 채로 로드된다. 저장하면 `log_paths`로 바뀐다. → Task 1
2. **두 경로, 다른 형식:** 각 파일이 자기 형식으로 파싱되고, awk 출력과 Python 집계가 바이트 단위까지 같다. → Task 1
3. **같은 파일이 두 경로에 걸림:** 첫 경로의 형식으로 한 번만 읽힌다(F 줄 1개). → Task 1
4. **경로 하나의 형식만 잘못됨:** 모르는 이름이거나 컴파일 오류인 경우, 그 서버만 "설정 오류: …"로 실패하고 다른 서버는 계속 스캔된다. → Task 1, Task 2
5. **UI:** 경로를 추가하면 위 경로의 형식을 물려받는다. 형식 이름 변경과 사용 중 판정은 경로 기준으로 동작한다. → Task 3

---

### Task 1: 모델, 설정, 원격 파이프라인 (백엔드 핵심)

**Files:**
- Modify: `napi/models.py`, `napi/config.py`, `napi/remote/command.py`, `napi/remote/awk_program.py`, 그리고 `log_globs`, `server.log_format`, `server.custom_format`을 쓰는 모든 Python 코드와 테스트(`napi/api.py`, `napi/collector.py` 포함 — grep으로 찾는다)
- Test: `tests/test_config.py`, `tests/remote/test_command.py`, `tests/remote/test_awk_pipeline.py`, 그리고 영향받는 테스트

**Interfaces:**
- Produces:
  - `@dataclass LogPath(glob: str, log_format: str = "webtob", custom_format: CustomFormat | None = None)`
  - `ServerConfig` 필드는 `name, host, user, log_paths: list[LogPath], port=22, auth="password", key_file=None`이다. `log_globs`, `log_format`, `custom_format` 필드는 삭제한다.
  - `awk_program_multi(paths: Sequence[tuple[str, CustomFormat | None]], formats: Sequence[LogFormatDef] = ()) -> str`
    - `(형식 이름, custom)` 목록을 받아 `parse_1..parse_N`과 디스패처를 생성한다.
    - 같은 (이름, custom) 조합은 함수 하나를 공유해도 된다.
    - 기존 `awk_program(fmt, custom, formats)`은 경로가 하나뿐인 경우의 래퍼로 남겨도 된다.
  - `build_pipeline` / `build_remote_command` 시그니처는 그대로다. 내부에서 `server.log_paths`를 쓴다.

설정 파일 규칙:
- 로드할 때 `log_paths: [{glob, log_format?, custom_format?}]`를 읽는다. 문자열 하나만 있으면 glob으로 보고, 서버의 기본 형식을 쓴다.
- 레거시 키(`log_glob`, `log_globs`, 서버 `log_format`, 서버 `custom_format`)가 있으면 `log_paths`로 변환한다. 각 경로는 서버 형식을 물려받는다.
- `log_paths`와 레거시 경로 키가 함께 있으면 ConfigError다.
- 저장할 때는 `log_paths`만 쓴다.
- 검증은 경로마다 한다(v1/확장 규칙 그대로). 형식 오류 메시지에는 glob을 포함한다: `"{서버 라벨}: 경로 {glob}: 모르는 로그 형식이에요: {name}"` 등.

원격 규칙:
- **파일 목록:** glob마다 `find` 결과 앞에 경로 번호(1부터, `log_paths` 순서)를 붙인다. 구분자는 `/`를 쓴다(`<n>/<절대경로>` — 번호가 숫자뿐이라 첫 `/`에서 잘라도 모호하지 않다).
- **중복 제거:** `LC_ALL=C`로 정렬하되, 같은 경로(파일)가 여러 번 나오면 번호가 가장 작은 줄만 남긴다. 결정적이어야 하며 임시 파일을 만들지 않는다.
- **파일 표시 줄:** `\n\001F<token>\t<n>\t<path>`. awk는 `n`으로 파싱 함수를 고른다. n이 모르는 값이면 그 파일의 줄은 모두 parse_fail로 센다.
- **E 표시 줄**(`unreadable`, `decompress_failed`, `glob_empty`)과 `glob_empty` 규칙은 그대로다. 경로 번호는 F 표시에만 붙인다.
- **동등성 테스트 기준:** Python 쪽은 파일마다 해당 경로의 형식으로 `parse_line`을 해서 집계한다.

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_config_legacy_to_log_paths`: v1 YAML(`log_glob` + `log_format: webtob`)과 확장 YAML(`log_globs` 2개 + `log_format: custom` + `custom_format`)을 로드하면 각 `LogPath`가 형식을 물려받는다. 저장 결과에 `log_paths`만 있다.
  - `test_config_log_paths_roundtrip_and_validate`: 경로별로 다른 형식을 쓴 설정이 왕복된다. 경로 하나가 모르는 형식이면 해당 glob을 포함한 오류가 나온다.
  - `test_pipeline_per_path_formats_parity`: 경로 A는 webtob(`webtob_access.log`), 경로 B는 사용자 형식 a(`user_a.log`)다. 서로 다른 디렉터리에 두고, awk 출력과 Python 집계가 같아야 한다. `gawk --posix`로도 같아야 한다.
  - `test_pipeline_same_file_two_paths_first_wins`: 같은 파일이 경로 1(webtob)과 경로 2(user_a)에 걸리면 F 줄 1개이고, webtob 기준 집계와 같다. 경로 순서를 바꾸면 user_a 기준이 된다.
  - `test_pipeline_unknown_path_index_is_parse_fail`: 디스패처가 모르는 번호를 받으면 parse_fail로 센다(awk 단위 테스트).
  - `test_build_remote_command_bad_format_on_one_path`: 한 경로의 형식이 모르는 이름이면 `ValueError`(FormatError)다.
  - 기존 R3 읽기 전용 테스트와 마커 위조 방지 테스트가 경로 번호가 붙은 F 표시에서도 통과한다. 로그 줄에 `\001F<token>\t1\t/x`를 넣어도 표시로 인정되지 않는다.
- [ ] **Step 2:** FAIL을 확인한다.
- [ ] **Step 3:** 구현한다. `server.log_format`/`log_globs`를 쓰던 모든 Python 코드를 바꾸고, `collector`의 오류 처리가 경로 형식 오류에서도 "설정 오류: …"가 되도록 한다.
- [ ] **Step 4:** 전체 스위트를 통과시킨다. 손으로 변형 3개 이상(번호 디스패치, 중복 제거 우선순위, 표시 파싱)을 넣어 보고 테스트가 잡는지 보고서에 적는다.
- [ ] **Step 5:** Commit (논리 단위로 나눠도 된다).

---

### Task 2: API 연결 — 미리보기, 형식 테스트, 상태

**Files:**
- Modify: `napi/api.py` (Task 1에서 이미 컴파일만 맞췄다면 여기서 기능을 정리한다), `napi/logformat.py`는 필요할 때만
- Test: `tests/test_api.py`

**Interfaces:**
- Consumes: `LogPath`, `ServerConfig.log_paths`
- Produces:
  - `preview_commands(service, period)`: 기존과 같다. 경로별 형식이 반영된 명령을 돌려준다.
  - `test_log_line(fmt, custom, line, formats=None)`: 기존과 같다. 화면이 경로마다 호출한다.
  - `get_state()`의 config dict에서 서버는 `log_paths`(목록, 각 `{glob, log_format, custom_format}`)를 갖는다.
  - 공개 메서드 집합(20개)과 매개변수 이름 규칙은 그대로다.

- [ ] **Step 1:** 실패하는 테스트를 작성한다.
  - `test_preview_uses_per_path_formats`: 경로 2개 중 하나가 사용자 형식이면, 미리보기 명령에 그 형식의 리터럴(예: `CLIENT(`)이 들어 있다.
  - `test_get_state_exposes_log_paths`
  - `test_scan_one_server_bad_path_format_others_continue`: 가짜 collector로 확인한다. 실제 Collector 경로에서 설정 오류가 한 서버로 한정되는지 본다.
- [ ] **Step 2:** FAIL → 구현 → PASS, 그다음 전체 스위트.
- [ ] **Step 3:** Commit.

---

### Task 3: 설정 화면 — 경로 줄마다 형식, README

**Files:**
- Modify: `napi/ui/js/settings.js`, `napi/ui/js/formats.js`, `napi/ui/js/mock-api.js`, `napi/ui/css/app.css`, `README.md`

**Interfaces:**
- Consumes: `get_state`(servers의 `log_paths`), `save_config`, `test_log_line`, `preview_commands`

화면 동작:
- **경로 한 줄:** `[경로 입력칸] [로그 형식 ▾] [삭제]`. 드롭다운에는 기본 제공 5개, 내 형식, `직접 지정 (필드 번호)`가 나온다.
- **직접 지정:** 고르면 그 줄 아래에 필드 번호 칸이 열린다. 기존 custom UI를 줄 단위로 옮기고, "새로 설정한다면 '로그 형식' 메뉴에서 형식을 추가해 쓰는 걸 권장해요" 안내 문구도 유지한다.
- **샘플 줄 테스트:** 경로마다 "샘플 줄로 형식 테스트" 버튼을 둔다.
- **서버 단위 형식 제거:** 서버 단위의 "로그 형식" 드롭다운을 없앤다.
- **경로 추가:** "＋ 경로 추가"는 바로 위 경로의 형식(과 custom)을 복사한다. 경로가 하나도 없으면 `webtob`를 쓴다.
- **형식 관리 연동(formats.js):** 사용 중 판정(`사용 중인 서버: …`)과 이름 바꾸기(`settle`의 원자적 매핑)를 경로 단위로 바꾼다. 툴팁 표기는 서버명 기준을 유지하되 중복 없이 보여 준다.
- **오류 표시:** 서버의 검증 오류 중 glob이 들어 있는 것은 해당 줄 옆에 표시한다.
- **mock-api.js:** `log_paths` 구조로 바꾼다.
- **README:** 경로별 형식을 설명하고, 같은 파일이 두 경로에 걸리면 위쪽 경로의 형식으로 한 번만 읽는다는 것도 적는다.

- [ ] **Step 1:** 구현한다(DOM은 `textContent`/`createElement`만 쓴다).
- [ ] **Step 2:** 정적 API 검사, DOM-shim 하네스(Electron node), 전체 pytest를 돌린다.
- [ ] **Step 3: 컨트롤러 브라우저 확인.** mock 화면(`:8765/index.html?mock=1`)과 실제 백엔드 다리(`:8767`)에서 확인한다. 보고서에 클릭 경로를 적는다.
- [ ] **Step 4:** Commit.

## 실행 순서

Task 1 → 2 → 3. 브랜치 `feat/per-path-format`은 `fix/js-param-shadowing`(78b544b)에서 시작한다.
