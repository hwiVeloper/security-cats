# napi — 경로별 로그 형식 설계

- 작성일: 2026-11-20
- 상태: 초안 (승인 대기)
- 기반: `2026-11-20-napi-formats-paths-password-design.md` (main에 병합됨)

## 1. 왜

로그 형식은 서버 단위가 아니라 **경로마다** 다르다. 같은 서버에서도 `access.log`는 `webtob_combined`, `access_ssl.log`는 `webtob`처럼 형식이 갈린다. 그래서 서버 카드의 경로 한 줄마다 형식을 고를 수 있어야 한다.

## 2. 설계

### 2.1 모델과 설정 파일

```yaml
servers:
  - name: WAS-01
    log_paths:
      - glob: /webtob/log/access.log*
        log_format: webtob_combined
      - glob: /webtob/log/access_ssl.log*
        log_format: webtob
        # custom_format: {...}   # log_format: custom일 때만
```

- `ServerConfig.log_paths: list[LogPath]`
  - `LogPath(glob: str, log_format: str = "webtob", custom_format: CustomFormat | None = None)`
  - 서버 단위의 `log_globs`, `log_format`, `custom_format`은 없앤다.
- **호환:** 기존 형식(`log_glob` 하나 또는 `log_globs` 목록, 그리고 서버의 `log_format`/`custom_format`)을 읽으면, 각 경로가 서버의 형식을 물려받는다. 저장할 때는 `log_paths`만 쓴다.
- **검증:** 경로 규칙은 이전과 같다. 형식 이름 검사(기본 제공, 사용자 형식, custom)는 경로마다 한다.

### 2.2 서버 쪽 집계

awk 프로그램 하나에 서버가 쓰는 **형식마다 파싱 함수**(`parse_1`, `parse_2`, …)를 넣는다. 파일 표시 줄에 형식 번호를 실어서 파일마다 맞는 함수로 파싱한다.

- **표시 줄:** `\n\001F<token>\t<형식번호>\t<경로>`. 토큰으로 묶이고 줄 맨 앞에서만 인정하는 규칙은 이전과 같다.
- **파일 목록:** glob마다 `find`한 결과를 `<형식번호>\t<경로>`로 내보내고 `LC_ALL=C` 정렬로 합친다. 같은 파일이 두 경로에 걸리면 **목록에서 먼저 나온 경로의 형식**으로 한 번만 읽는다.
- **그대로 유지하는 것:** 정규화, 집계, 출력 프로토콜(F/R/P/Q/T/E/D), `glob_empty`, 읽기 전용, `timeout`/`nice`. 출력의 F 줄 형식도 바뀌지 않는다(형식 번호는 서버 안에서만 쓴다).
- **동등성 테스트:** 서로 다른 형식을 쓰는 경로 2개로 awk 출력과 Python 집계가 바이트 단위까지 같은지 확인한다.

### 2.3 화면

서버 카드의 경로 한 줄을 `[경로 입력칸] [로그 형식 ▾] [삭제]`로 바꾼다.

- 형식 드롭다운에는 기본 제공 형식, 내 형식, `직접 지정 (필드 번호)`가 나온다.
- `직접 지정`을 고르면 그 경로 아래에 필드 번호 칸이 열린다.
- "샘플 줄로 형식 테스트"는 경로마다 둔다.
- 서버 단위의 "로그 형식" 드롭다운은 없앤다.
- 새 경로를 추가하면 바로 위 경로의 형식을 기본값으로 쓴다.
- 형식 이름을 바꾸면 그 형식을 쓰는 모든 경로가 따라 바뀌고, 사용 중인 형식은 삭제할 수 없다. 둘 다 경로 기준으로 동작한다.

## 3. 범위

- **규모:** 작업 4개 정도다. ① 모델과 설정 ② awk와 셸(보안 핵심, 동등성 테스트) ③ API, 미리보기, 형식 테스트 연결 ④ 화면과 README. 작업마다 리뷰를 거친다.
- **함께 반영:** 브랜치 `fix/js-param-shadowing`의 "실행될 명령 보기 / 스캔 버튼이 실제 앱에서 동작하지 않던 버그" 수정도 이 작업과 같이 `main`에 반영한다.
