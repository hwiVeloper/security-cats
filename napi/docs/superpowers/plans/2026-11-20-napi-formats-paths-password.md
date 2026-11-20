# napi 확장 — 사용자 정의 로그 형식 · 로그 경로 여러 개 · 설정 화면 비밀번호 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 사용자가 Apache LogFormat 문법으로 로그 형식을 정의할 수 있게 한다. 서버마다 로그 경로를 여러 개 지정할 수 있게 한다. 설정 화면에서 비밀번호, 키파일 암호, 키파일 경로를 입력할 수 있게 한다(비밀값은 세션에만 둔다).

**Architecture:**
- `napi/logformat.py`에 형식 컴파일러(`compile_format`)를 둔다. 형식 문자열을 토큰 목록(`FormatSpec`)으로 바꾸고, Python 기준 파서와 awk 생성기가 같은 FormatSpec을 해석한다.
- 기본 제공 CLF 형식도 이 엔진으로 옮긴다.
- 사용자 형식은 설정 파일의 `log_formats`에 두고, Collector와 Api를 거쳐 원격 명령 생성까지 전달한다.

**Tech Stack:** 기존과 같다 (Python 3.12, pywebview, paramiko, PyYAML, POSIX awk, 바닐라 JS).

**Spec:** `docs/superpowers/specs/2026-11-20-napi-formats-paths-password-design.md` (기반: `docs/superpowers/specs/2026-11-19-napi-design.md`)

## Global Constraints

- **v1 보안 원칙 유지:**
  - 원본 로그 줄이 PC로 넘어오지 않는다. 원격 출력은 F/R/P/Q/T/E/D 프로토콜 줄만 낸다.
  - 비밀번호와 passphrase는 디스크, 결과 JSON, API 반환값, DOM 속성, 로그 어디에도 남지 않는다.
  - 원격 명령은 읽기 전용이다. `timeout`, `nice -n 19`, `ionice -c3`(있을 때)로 감싼다.
- **awk 제약:** POSIX awk만 쓴다(gawk 확장 금지, 정규식 반복 `{n}` 금지). 파싱은 `index`, `substr`, `length`로 한다. 마커는 토큰에 묶여 있고 줄 맨 앞에서만 인정한다(v1 R9). 정규화 규칙과 "AWK PARITY CONTRACT"는 v1 그대로다.
- **기본 제공 형식 (이름 → 패턴, 수정 불가):**
  - `apache_common` = `%h %l %u %t "%r" %>s %b`
  - `apache_combined` = `%h %l %u %t "%r" %>s %b "%{Referer}i" "%{User-Agent}i"`
  - `nginx_combined` = `apache_combined`와 같다
  - `webtob` = `%h %l %u %t "%r" %s %b`
  - `webtob_combined` = `%h %l %u %t "%r" %s %b "%{Referer}i" "%{User-Agent}i"`
- **형식 필수 조건:** `%r`이 있거나 `%m`과 `%U`가 함께 있어야 한다. 시각은 `%t` 또는 `%Y`, `%m`, `%d`를 모두 포함한 `%{…}t`여야 한다. 위반하면 `FormatError("API 호출을 셀 수 없는 형식이에요 (요청/시각 필요)")`.
- **상태코드 없는 형식:** 상태 클래스는 `"???"`로 집계하고 404가 아닌 것으로 취급한다. 화면 라벨은 "상태 미상".
- **v1 호환:**
  - 설정 파일의 `log_glob: x`는 `log_globs: [x]`로 읽고, 저장할 때는 `log_globs`만 쓴다.
  - `log_format: custom` + `custom_format`(필드 번호)은 계속 동작한다.
- **환경:**
  - 테스트는 `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/Scripts/python -m pytest -k "not with_edge"`로 돌린다.
  - msedge.exe, napi.exe, `python -m napi`는 절대 실행하지 않는다(세션이 종료된다).
  - `.txt` 파일을 만들지 않는다. 일반 텍스트 파일이 필요하면 확장자 없이 만든다.
- 사용자에게 보이는 문구는 모두 한국어다.

## Review Focus

1. **잘못된 형식 패턴:** 필드가 붙어 있음(`%h%l`), 요청 없음(`%*`만), 날짜 없음, 닫히지 않은 `%{`. 저장하거나 테스트할 때 한국어 오류를 내고, 앱이 죽지 않는다. → Task 1 테스트
2. **형식과 맞지 않는 로그 줄:** 리터럴 불일치, 줄이 중간에 끝남. 이런 줄은 `parse_fail`로 세고, 출력에 원문이 섞이지 않는다. → Task 1, Task 2 테스트
3. **v1 설정 파일:** `log_glob` 한 개와 `custom` 형식을 쓰던 파일이 그대로 로드된다. 저장하면 `log_globs`로 바뀌고, 명령 생성도 정상이다. → Task 3 테스트
4. **일부 경로만 비어 있음:** 비어 있는 glob은 신뢰도 사유로 표시되고 나머지 경로는 정상 집계된다. 모든 glob이 비면 `E\tno files matched`가 나온다. → Task 3 테스트
5. **설정 화면에서 넣은 비밀번호:** 설정 파일, `get_state`, `secret_status`, 결과 JSON 어디에도 값이 나오지 않는다. → Task 4 테스트

---

## File Structure

```
napi/models.py              LogFormatDef, AppConfig.log_formats, ServerConfig.log_globs (Task 1, 3)
napi/logformat.py           BUILTIN_FORMATS, compile_format, FormatSpec, FormatError, resolve_format, 파서 (Task 1)
napi/config.py              log_formats 로드/저장/검증, log_glob→log_globs 호환 (Task 1, 3)
napi/remote/awk_program.py  FormatSpec → awk 파싱 코드 생성 (Task 2)
napi/remote/command.py      formats 전달, glob 여러 개 find, glob_empty 마커 (Task 2, 3)
napi/remote/protocol.py     E glob_empty 파싱 (Task 3)
napi/classifier.py          glob_empty 신뢰도 사유 (Task 3)
napi/collector.py           Collector(formats=...) (Task 4)
napi/secrets.py             has_/clear_ 메서드 (Task 4)
napi/api.py                 formats 전달, test_log_line 확장, test_format, secret_status, clear_secret, pick_key_file (Task 4)
napi/ui/js/settings.js      형식 관리, 경로 목록, 비밀번호/키파일 칸 (Task 5)
napi/ui/js/mock-api.js      새 API 목업 (Task 5)
napi/ui/css/app.css         필요한 스타일 (Task 5)
README.md                   사용법 갱신 (Task 5)
```

---

### Task 1: 형식 컴파일러와 Python 기준 파서, 설정 파일의 log_formats

**Files:**
- Modify: `napi/models.py`, `napi/logformat.py`, `napi/config.py`
- Test: `tests/test_logformat.py`, `tests/test_format_compile.py` (new), `tests/test_config.py`

**Interfaces:**
- Produces (`napi/models.py`):
  - `@dataclass LogFormatDef(name: str, pattern: str)`
  - `AppConfig.log_formats: list[LogFormatDef] = field(default_factory=list)`
- Produces (`napi/logformat.py`):
  - `BUILTIN_FORMATS: dict[str, str]` (Global Constraints의 5개)
  - `class FormatError(ValueError)` (한국어 메시지)
  - `@dataclass(frozen=True) Token(kind: Literal["lit","field"], text: str, directive: str = "", time_layout: tuple[tuple[str,int,int], ...] = ())`
    - lit이면 `text`가 리터럴이다.
    - field이면 `directive`가 `h l u t tfmt r m U q H s b B D T i o skip` 중 하나다.
    - `time_layout`은 `%{…}t`에서 `(Y|m|d|b, 시작 오프셋, 길이)` 목록이다.
  - `@dataclass(frozen=True) FormatSpec(tokens: tuple[Token, ...], has_status: bool)`
  - `compile_format(pattern: str) -> FormatSpec`. 오류는 `FormatError`.
  - `resolve_format(name: str, user_formats: Sequence[LogFormatDef] = (), custom: CustomFormat | None = None) -> FormatSpec | CustomFormat`
    - `"custom"`이면 custom을 돌려준다.
    - 그 밖에는 기본 제공 형식에서 먼저 찾고, 없으면 사용자 형식에서 찾는다.
    - 모르는 이름이면 `FormatError("모르는 로그 형식이에요: <name>")`.
  - `parse_line(line, fmt: str, custom=None, formats: Sequence[LogFormatDef] = ()) -> ParsedLine | None`. 기존 인자 순서를 유지하고 `formats`만 추가한다.
  - `parse_with_spec(line: str, spec: FormatSpec) -> ParsedLine | None`
  - `ParsedLine.status`는 `int | None`이다(None = 상태 미상).
  - `test_sample_line(line, fmt, custom, formats=()) -> dict`. `status_class` 키를 추가한다(`"2xx"`, …, `"???"`).
  - `test_format(pattern: str, line: str) -> dict`. 반환 키는 `ok`, `error`, `method`, `path`, `normalized`, `status`, `status_class`, `date`이다. 컴파일 오류도 `ok:false`와 `error`로 돌려준다.
  - `PRESETS`는 `BUILTIN_FORMATS`의 키와 표시명(`"WebtoB (combined)"` 등)에 `"custom": "직접 지정 (필드 번호)"`를 더한 것이다.

컴파일 규칙:
- `%` 다음에 `>`를 허용한다(`%>s`). `%{…}x`는 중괄호 안을 인자로 받는다. `%%`는 리터럴 `%`다.
- 알 수 없는 지시자와 `%*`는 `skip`이다. `%{X}i`, `%{X}o`, `%b`, `%B`, `%D`, `%T`, `%h`, `%l`, `%u`, `%q`, `%H`는 값을 버린다.
- 필드 두 개가 리터럴 없이 붙으면 오류다: `"필드 사이에 구분 문자가 필요해요: %h%l"`.
- 닫히지 않은 `%{` → `"닫히지 않은 %{ 가 있어요"`.
- `%{…}t` 안에서 지원하는 것: `%Y`(4), `%m`(2), `%d`(2), `%H`, `%M`, `%S`(각 2), `%b`(3, 월 이름), `%z`(5), `%y`(2). 나머지 문자는 리터럴로, 폭은 1이다. 이 중 `%Y`, `%m`(또는 `%b`), `%d`가 모두 있어야 날짜로 인정한다. 그 밖의 strftime 지시자는 `"시각 형식에서 지원하지 않는 지시자예요: %X"`.
- 필수 조건은 Global Constraints를 따른다.

파싱 알고리즘(Python과 awk가 같아야 하는 계약 — 모듈 docstring "FORMAT PARSE CONTRACT"에 적을 것):
- **줄 정리:** 줄 끝의 `\n` 하나, 그다음 `\r` 하나를 제거한다(v1과 같음). 커서는 1부터 시작한다.
- **lit 토큰:** 커서 위치의 부분 문자열이 리터럴과 정확히 같아야 하고, 같으면 커서를 리터럴 길이만큼 옮긴다. 다르면 실패.
- **field 토큰(`t` 제외):**
  - 다음 토큰이 lit이면, 커서부터 그 리터럴이 처음 나오는 위치 직전까지가 값이다. 리터럴이 없으면 실패.
  - 다음 토큰이 없으면 줄 끝까지가 값이다.
- **`t` 필드:** 커서 위치 문자가 `[`이어야 하고, 첫 `]`까지(포함)가 값이다. 날짜는 v1 `clfdate` 규칙으로 뽑는다.
- **`tfmt` 필드:** 값의 길이가 layout 전체 폭과 같아야 한다. 날짜는 오프셋으로 뽑아 `YYYYMMDD`로 만들고, 월 이름은 v1 월 맵을 쓴다.
- **`r` 필드:** v1 규칙대로 토큰화한다(앞뒤 공백 trim, `[ \t]+`로 분할, 토큰 2개 이상, 메서드는 `^[A-Z]+$`).
- **`m`과 `U` 필드:** 메서드는 `^[A-Z]+$`이어야 한다. `%U`는 경로이고, `%q`가 있으면 버린다(정규화가 쿼리를 지운다).
- **`s` 필드:** `^[0-9]{3}$`. 형식에 `%s`가 없으면 status는 None이다.
- **실패 처리:** 하나라도 실패하면 None을 돌려준다. 마지막 토큰을 소비한 뒤 남은 글자는 무시한다.
- 기본 제공 5개 형식도 이 엔진으로 파싱한다. 기존 CLF 전용 파서는 삭제한다.

- [ ] **Step 1: 실패하는 테스트 작성** `tests/test_format_compile.py`
  - `test_builtin_patterns_compile`: 5개 모두 컴파일되고, `has_status`가 True다.
  - `test_compile_errors` (parametrize): 각 경우 `FormatError`가 나고 메시지에 해당 한국어 문구가 들어 있다.
    - `"%h%l %t \"%r\""` → `"구분 문자"`
    - `"[%{%Y-%m-%dT%H:%M:%S}t] [CLIENT(%h)] %*"` → `"API 호출을 셀 수 없는"`
    - `"%h \"%r\" %s"` (시각 없음) → `"API 호출을 셀 수 없는"`
    - `"%h %{%Y-%m-%d %t"` → `"닫히지 않은"`
    - `"%{%Y-%j}t \"%r\""` → `"지원하지 않는"`
  - `test_user_format_with_brackets`: 패턴 `[%{%Y-%m-%dT%H:%M:%S}t] [CLIENT(%h)] "%r" %s`, 줄 `[2026-11-18T10:00:01] [CLIENT(10.0.0.1)] "GET /v1/users/123?x=1 HTTP/1.1" 200` → `ParsedLine("GET", "/v1/users/123?x=1", 200, "20261118")`
  - `test_method_and_U`: 패턴 `%{%d/%b/%Y:%H:%M:%S}t %m %U ?%q %s`, 줄 `18/Nov/2026:10:00:01 GET /v1/a ?x=1 404` → method GET, path `/v1/a`, status 404, date `20261118`
  - `test_adjacent_U_q_is_error`: 패턴 `%t %m %U%q` → `FormatError`("구분 문자")
  - `test_no_status_format`: 패턴 `%t "%r"` → `has_status` False, 파싱 결과 status None, `test_format(...)["status_class"] == "???"`
  - `test_literal_mismatch_is_none`: webtob 형식에 `[CLIENT(...)` 줄 → None
  - `test_resolve_format`: 기본 제공 형식 → FormatSpec, 사용자 형식 → FormatSpec, `"custom"` → CustomFormat, 모르는 이름 → FormatError
- [ ] **Step 2:** 기존 `tests/test_logformat.py`의 v1 기대값은 바꾸지 않는다. CLF 테스트가 엔진 전환 뒤에도 통과해야 한다.
  - 엔진이 엄격해서 v1 기대값과 달라지는 경우는 고치지 말고 보고서에 사례를 적는다.
  - 예: 요청줄 따옴표 뒤 필드 사이 공백이 두 칸인 줄. `%s` 앞 리터럴 `" "` 한 칸 때문에 실패할 수 있다.
- [ ] **Step 3: 설정 테스트 추가** `tests/test_config.py`
  - `test_log_formats_roundtrip`: `log_formats` 2개를 저장한 뒤 로드하면 `==`이다.
  - `test_validate_log_formats`: 다음 각 경우에 오류 메시지가 나온다.
    - 기본 제공 이름과 같은 이름 → `"기본 제공 형식 이름"`
    - 중복 이름 → `"중복"`
    - 컴파일 오류 → 형식 이름과 오류 문구
    - 서버의 `log_format`이 기본 제공 형식, `custom`, 사용자 형식 어디에도 없음 → `"모르는 로그 형식"`
- [ ] **Step 4:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/Scripts/python -m pytest tests/test_format_compile.py tests/test_logformat.py tests/test_config.py -k "not with_edge"` → FAIL을 확인한다.
- [ ] **Step 5:** 구현한다(models, logformat, config). `test_sample_line`과 Api의 기존 호출이 깨지지 않게 한다.
- [ ] **Step 6:** 같은 명령 → PASS, 그다음 전체 스위트 → PASS. 단, awk 쪽 동등성 테스트는 Task 2 전까지 기존 CLF awk를 쓰므로 그대로 통과해야 한다.
- [ ] **Step 7:** Commit `feat: LogFormat-style format compiler and user-defined formats in config`

---

### Task 2: FormatSpec → awk 생성기와 동등성 테스트

**Files:**
- Modify: `napi/remote/awk_program.py`, `napi/remote/command.py`
- Test: `tests/remote/test_awk_pipeline.py`, `tests/remote/test_command.py`

**Interfaces:**
- Consumes: `compile_format`, `resolve_format`, `FormatSpec`, `Token`, `LogFormatDef`, `parse_line(..., formats=)` (Task 1)
- Produces:
  - `awk_program(fmt: str, custom: CustomFormat | None, formats: Sequence[LogFormatDef] = ()) -> str`. FormatSpec이면 생성 코드를, CustomFormat이면 기존 `_PARSE_CUSTOM`을 쓴다.
  - `build_pipeline(server, settings, since, token, formats: Sequence[LogFormatDef] = ())`
  - `build_remote_command(server, settings, since, token, formats: Sequence[LogFormatDef] = ())`

규칙:
- 생성되는 awk의 `parse()`는 FORMAT PARSE CONTRACT를 그대로 따른다.
  - 커서 변수와 `substr`/`index`로 구현하고, 토큰마다 코드를 펼친다.
  - lit 문자열은 awk 문자열 리터럴로 안전하게 이스케이프한다(`\\`, `"`, 제어문자 → `\ooo`).
  - 결과는 `P_M`(메서드), `P_U`(경로), `P_S`(상태 또는 `""`), `P_D`(날짜)이고, v1 `_COMMON`이 기대하는 변수 이름을 맞춘다. 상태가 `""`이면 상태 클래스는 `"???"`다.
- 사용자 형식 패턴은 awk 소스에 리터럴로만 들어간다. 패턴 문자열로 awk 코드를 주입할 수 없다는 것을 테스트로 고정한다.
- 기존 `_PARSE_CLF`와 CLF 전용 정규식은 삭제한다.

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_pipeline_parity_builtin_formats`: 기존 v1 동등성 테스트를 5개 기본 제공 형식 전부로 확장한다. 기존 픽스처를 재사용하고, `webtob_combined`는 nginx 픽스처를 쓴다.
  - `test_pipeline_parity_user_formats`: 사용자 형식 2개로 동등성을 확인한다. awk R/P/Q 출력과 Python 집계가 바이트 단위로 같아야 한다.
    - (a) `[%{%Y-%m-%dT%H:%M:%S}t] [CLIENT(%h)] "%r" %s`
    - (b) `%{%d/%b/%Y:%H:%M:%S}t %m %U ?%q %s`
    - 각각 새 픽스처 `tests/fixtures/logs/user_a.log`, `user_b.log`를 만든다. 정상 줄 8줄 이상, 깨진 줄 2줄 이상, 날짜 2개 이상을 넣는다(LF, UTF-8).
  - `test_pipeline_no_status_format`: `%t "%r"` 형식 → R 줄의 상태 클래스가 `???`
  - `test_pattern_cannot_inject_awk`: 리터럴에 `"`, `\`, `}`, `system("x")`가 들어간 패턴(필수 조건은 충족)을 쓴다. 생성된 프로그램이 `gawk --posix`로 문법 검사를 통과하고, 동등성도 유지된다.
  - `test_command_passes_formats`: `build_remote_command(..., formats=[LogFormatDef("my", ...)])`에서 서버 `log_format="my"`이면 명령이 생성된다. 모르는 이름이면 `ValueError`(FormatError)가 난다.
- [ ] **Step 2:** `PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 .venv/Scripts/python -m pytest tests/remote -k "not with_edge"` → FAIL을 확인한다.
- [ ] **Step 3:** 구현한다.
- [ ] **Step 4:** 같은 명령 → PASS, 전체 스위트 → PASS. 손으로 변형 3개 이상(커서 이동 off-by-one, lit 비교, tfmt 오프셋)을 넣어 보고 테스트가 잡는지 확인해서 보고서에 적는다.
- [ ] **Step 5:** Commit `feat: generate awk parser from compiled LogFormat spec`

---

### Task 3: 로그 경로 여러 개

**Files:**
- Modify: `napi/models.py`, `napi/config.py`, `napi/remote/command.py`, `napi/remote/awk_program.py`, `napi/remote/protocol.py`, `napi/classifier.py`
- Test: `tests/test_config.py`, `tests/remote/test_command.py`, `tests/remote/test_awk_pipeline.py`, `tests/remote/test_protocol.py`, `tests/test_classifier.py`

**Interfaces:**
- Produces:
  - `ServerConfig.log_globs: list[str]`. 기존 `log_glob` 필드를 대체한다. 필드 순서는 `name, host, user, log_globs, port, auth, key_file, log_format, custom_format`.
  - `split_glob`은 그대로다.
  - 프로토콜에 E 코드를 추가한다: `E\tglob_empty\t<glob>`. 일부 glob만 비었을 때 낸다. 모두 비면 기존 `E\tno files matched`만 낸다.
  - `ServerScanResult.file_errors`에 `("glob_empty", glob)`을 담는다.
  - 신뢰도 사유: `"<서버명>의 경로 <glob>에서 파일을 찾지 못했어요"`.

명령 구조:
- glob마다 `find <dir> -maxdepth 1 -type f -name <pattern> [-newermt …]`를 만든다.
- 비어 있는 glob을 알리기 위해 셸에서 glob마다 결과 수를 센다. 0이면 토큰 마커 `printf '\n\001E%s\tglob_empty\t%s\n' "$0" <quoted-glob>`를 낸다.
- 전체 파일 목록은 `sort -u`로 중복을 제거한 뒤 v1 파일 루프에 넣는다.
- 모든 사용자 값은 `shlex.quote`로 감싼다. 읽기 전용 테스트(R3)는 계속 통과해야 한다.

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_config_reads_legacy_log_glob`: v1 YAML(`log_glob: /x/a.log*`)을 로드하면 `log_globs == ["/x/a.log*"]`이다. 저장하면 파일에 `log_globs`가 있고 `log_glob:`는 없다.
  - `test_validate_log_globs`: 빈 목록 → `"로그 경로를 하나 이상"`. 각 glob에 v1 규칙(절대 경로, 디렉터리 부분 와일드카드 금지)을 적용하고, 메시지에 해당 glob을 넣는다.
  - `test_pipeline_multiple_globs`: tmp에 디렉터리 두 개를 만들고 각각 파일을 넣는다. 두 glob에서 나온 결과가 합쳐지고 Python 집계와 같아야 한다. 같은 파일이 두 glob에 동시에 걸리면 한 번만 읽는다(F 줄 1개).
  - `test_pipeline_glob_empty`: glob 하나는 파일 있음, 하나는 없음 → `E\tglob_empty\t<glob>`, R 줄 정상, `D`. 둘 다 없음 → `E\tno files matched`(glob_empty 없음).
  - `test_protocol_glob_empty`: `file_errors`에 `("glob_empty", "/x/b*")`가 담긴다.
  - `test_reliability_glob_empty`: 사유 문구가 정확히 나오고, level이 `"low"`다.
- [ ] **Step 2:** 해당 테스트 → FAIL을 확인한다.
- [ ] **Step 3:** 구현한다. `log_glob`을 쓰던 곳(command, config, mock은 Task 5)을 모두 바꾼다.
- [ ] **Step 4:** 해당 테스트, 그다음 전체 스위트 → PASS.
- [ ] **Step 5:** Commit `feat: multiple log paths per server`

---

### Task 4: 백엔드 연결 — 형식 전달, 형식 테스트, 세션 비밀 관리, 키파일 선택

**Files:**
- Modify: `napi/collector.py`, `napi/secrets.py`, `napi/api.py`
- Test: `tests/test_collector.py`, `tests/test_secrets.py`, `tests/test_api.py`

**Interfaces:**
- Consumes: `build_remote_command(..., formats=)` (Task 2), `test_format`, `test_sample_line(..., formats=)` (Task 1), `LogFormatDef`
- Produces:
  - `Collector.__init__(..., client_factory=paramiko.SSHClient, formats: Sequence[LogFormatDef] = ())`. formats는 build_remote_command로 넘긴다.
  - `SessionSecrets`:
    - `has_password(user, host, port) -> bool`
    - `has_passphrase(key_file) -> bool`
    - `clear_password(user, host, port) -> None`
    - `clear_passphrase(key_file) -> None`
  - `Api`의 변경과 추가:
    - `start_scan`과 `preview_commands`는 `cfg.log_formats`를 전달한다. `collector_factory(cfg.settings, secrets, prompt, known_hosts, formats=cfg.log_formats)` — 테스트용 가짜 factory도 `formats` 키워드를 받게 고친다.
    - `test_log_line(fmt: str, custom: dict | None, line: str, formats: list[dict] | None = None) -> dict`. formats가 주어지면(설정 화면의 저장 전 초안) 그것을 쓰고, 없으면 현재 설정 파일의 log_formats를 쓴다.
    - `test_format(pattern: str, line: str) -> dict`
    - `secret_status(service: str) -> dict[str, str]`. 서버명별로 `"set"`, `"missing"`, `"n/a"` 중 하나를 돌려준다. password 서버는 비밀번호가 있는지, key 서버는 passphrase가 있으면 `"set"`, 없으면 `"n/a"`다(암호 없는 키일 수 있음). **값은 절대 반환하지 않는다.**
    - `clear_secret(service: str, server: str) -> {"ok"}`. 해당 서버의 password 또는 passphrase를 메모리에서 지운다.
    - `pick_key_file() -> {"ok", "path"}`. 주입 가능한 `open_key_file: Callable[[], str | None]`을 쓴다. 기본값은 `create_file_dialog(OPEN, file_types=("모든 파일 (*.*)",))`이다. 고른 경로만 돌려주고, 파일 내용은 읽지 않는다.
  - 공개 메서드 목록 테스트(`test_no_secret_in_any_return`의 16개 집합)를 19개(+ `test_format`, `secret_status`, `clear_secret`, `pick_key_file`)로 갱신한다. 비밀이 새지 않는지 확인하는 검사도 새 메서드까지 적용한다.

- [ ] **Step 1: 실패하는 테스트 작성**
  - `test_collector_passes_formats`: 가짜 client가 받은 명령에 사용자 형식 리터럴(예: `CLIENT(`)이 들어 있다.
  - `test_secrets_has_and_clear`: set → has True → clear → has False, get → None.
  - `test_api_secret_status_and_clear`: password 서버 2대 중 1대만 set → `{"A": "set", "B": "missing"}`. key 서버 → `"n/a"`, passphrase를 set하면 `"set"`. clear_secret 후 `"missing"`.
  - `test_api_test_format`: 정상 패턴과 줄 → ok, `normalized` 포함. 컴파일 오류 → `ok:false`, 한국어 error.
  - `test_api_test_log_line_with_draft_formats`: 저장되지 않은 초안 formats에 정의된 이름으로 테스트하면 ok다.
  - `test_api_pick_key_file`: 주입된 대화상자가 경로를 돌려주면 `{"ok": True, "path": p}`, None이면 `{"ok": False, "path": None}`. 파일을 열지 않는다(존재하지 않는 경로로도 통과).
  - `test_no_secret_in_any_return` 갱신: `set_password`와 `set_passphrase` 뒤에 새 메서드를 포함한 모든 공개 메서드의 반환값과 디스크 파일에 비밀번호 문자열이 없다.
- [ ] **Step 2:** 해당 테스트 → FAIL을 확인한다.
- [ ] **Step 3:** 구현한다.
- [ ] **Step 4:** 해당 테스트, 그다음 전체 스위트 → PASS.
- [ ] **Step 5:** Commit `feat: pass user formats to scans; session secret status, clear, key file picker`

---

### Task 5: 설정 화면 UI — 형식 관리, 경로 목록, 비밀번호 / 키파일, README

**Files:**
- Modify: `napi/ui/js/settings.js`, `napi/ui/js/mock-api.js`, `napi/ui/css/app.css`, `README.md`
- (필요하면) Create: `napi/ui/js/formats.js` — 형식 관리 화면을 분리한다. settings.js가 이미 700줄을 넘으므로 분리를 권장한다.

**Interfaces:**
- Consumes (Task 4 API): `get_state`, `save_config`, `test_log_line(fmt, custom, line, formats)`, `test_format(pattern, line)`, `set_password`, `set_passphrase`, `secret_status`, `clear_secret`, `pick_key_file`

화면 동작:
- **형식 관리 (설정 모달 안의 "로그 형식" 섹션 또는 탭):**
  - 기본 제공 형식 5개는 읽기 전용으로 보여 준다(이름과 패턴).
  - 사용자 형식은 이름과 패턴으로 추가, 수정, 삭제한다. 각 형식에 "샘플 줄로 테스트" 기능을 두고 `test_format`을 호출한다.
  - 문법 도움말 한 줄: `"%h %l %u %t \"%r\" %s %b 처럼 WebtoB/Apache LogFormat 문법을 그대로 적어요. %r(또는 %m+%U)과 날짜가 꼭 있어야 해요"`
  - 서버가 쓰는 형식은 삭제 버튼이 비활성이다. 툴팁으로 `"사용 중인 서버: A, B"`를 보여 준다.
  - 형식 정의는 설정 저장 시 `log_formats`로 함께 저장한다(v1 notes 보존 로직은 그대로 유지).
- **서버 카드:**
  - **로그 형식 드롭다운:** 기본 제공 형식 + 사용자 형식 + `직접 지정 (필드 번호)`. 기존 custom UI는 유지한다.
  - **로그 경로 목록:** 경로 입력칸 N개, "＋ 경로 추가", 경로별 "삭제"(마지막 1개는 비활성). 검증 오류는 해당 경로 옆에 표시한다.
  - **인증이 비밀번호면:** `비밀번호` 칸(`type=password`, `autocomplete=off`), 안내 `"저장되지 않아요. 프로그램을 끄면 사라져요"`, 상태 표시 `"이번 실행 동안 입력됨 ✓"` + `지우기` 버튼(`secret_status`, `clear_secret`).
  - **인증이 키파일이면:** 경로 칸 + `키파일 불러오기…` 버튼(`pick_key_file`로 경로 칸을 채움), `키파일 암호(있으면)` 칸(`type=password`), 같은 상태 표시와 지우기.
- **저장 시 순서:**
  1. `save_config`가 성공한다.
  2. 값이 입력된 비밀번호 칸만 `set_password(user, host, port, value, null)`, 암호 칸만 `set_passphrase(key_file, value)`로 보낸다.
  3. 보낸 즉시 칸을 비우고 JS 참조를 버린다. 비밀값을 설정 dict, DOM 속성, localStorage, 콘솔에 넣지 않는다.
  4. 모달을 다시 열 때 `secret_status`로 상태를 표시한다.
- **mock-api.js:** 새 API를 모두 흉내 낸다. 비밀은 "설정됨" 플래그만 둔다. `test_format`은 간단한 JS 구현이면 충분하다.
- **README.md:** 형식 정의 문법, 경로 여러 개, 설정 화면의 비밀번호와 키파일 사용법을 갱신한다. `webtob_error`처럼 요청 정보가 없는 형식은 등록할 수 없는 이유도 적는다.

- [ ] **Step 1:** 구현한다(DOM 텍스트는 `textContent`/`createElement`만 쓴다).
- [ ] **Step 2: 정적 검사.** JS의 `api.<name>` 호출이 모두 `napi/api.py` 공개 메서드와 mock에 존재하는지 확인한다(Task 13 방식). 전체 pytest도 → PASS.
- [ ] **Step 3: 컨트롤러 브라우저 확인** (`http://localhost:8765/index.html?mock=1`, `.claude/launch.json`의 `napi-ui-mock`). 보고서에 클릭 경로를 적는다.
  1. 형식 추가 → 테스트 성공/실패 → 저장 → 서버 드롭다운에 나타남
  2. 사용 중인 형식은 삭제 불가
  3. 경로 추가/삭제, 잘못된 경로 오류 표시
  4. 비밀번호 입력 → 저장 → 칸 비워짐 + "입력됨 ✓" → 스캔 시 비밀번호 창 안 뜸 → 지우기 후 스캔하면 다시 뜸
  5. 키파일 불러오기로 경로 채워짐
  6. 콘솔 에러 0건
- [ ] **Step 4:** Commit `feat: settings UI for custom formats, multiple log paths, session passwords and key file picker`

---

## 실행 순서

Task 1 → 2 → 3 → 4 → 5. 브랜치는 `feat/formats-paths-password`이고, 설계 문서와 이 계획은 브랜치의 첫 커밋으로 넣는다.
