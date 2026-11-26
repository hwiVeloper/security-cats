# 보안냥이 (security-cats)

보안 대응 기능을 고양이로 하나씩 붙여 가는 데스크톱 프로그램이에요. 보안 전문가 고양이 **보안냥이**가 본체이고, 기능마다 고양이가 한 마리씩 있어요.

| 고양이 | 하는 일 | 이관 전 프로그램 |
|---|---|---|
| 냅냥 | 안 쓰는(잠든) API 찾기 | `napi/` |
| 탐정냥 | 침해의심 IP 점검 | `iptrace/` |

## 지금 상태

**1단계(이사 준비)까지 끝났어요.** 두 프로그램을 이 저장소에 그대로 옮겨 놓은 상태예요.

- `napi/`: napi 전체(커밋 이력 포함). 냅냥이 될 코드.
- `iptrace/`: iptrace 기준본(v1.1.0, 메일 기능 포함). 탐정냥이 될 코드.

두 폴더는 이관용 원본이라, 본체(`seccats/`)로 다 옮기면 6단계에서 지워요. 단계는 설계 문서 9절을 보세요.

## 문서

- 설계: [docs/superpowers/specs/2026-11-26-security-cats-design.md](docs/superpowers/specs/2026-11-26-security-cats-design.md)
- 고양이·홈 화면 시안: [docs/superpowers/specs/2026-11-26-security-cats-mockups/](docs/superpowers/specs/2026-11-26-security-cats-mockups/) (브라우저로 열기)
- 단계별 구현 계획: [docs/superpowers/plans/](docs/superpowers/plans/)

## 개발 준비

Windows, Python 3.12. 저장소 루트에 가상환경 하나를 만들고 두 프로그램이 같이 써요.

```powershell
C:\Python312\python -m venv .venv
.venv\Scripts\python -m pip install "pywebview>=5" "paramiko>=3.4" "PyYAML>=6" requests "openpyxl>=3.1" pytest pyinstaller Pillow git-filter-repo
```

폐쇄망에서는 `--find-links <wheelhouse 폴더>` 를 붙여 미리 받아 둔 wheel 로 설치해요.

## 테스트

```powershell
cd napi; ..\.venv\Scripts\python -m pytest -q; cd ..
cd iptrace; ..\.venv\Scripts\python -m pytest -q; cd ..
.venv\Scripts\python -m pytest tools -q -p no:cacheprovider
```

## 빌드

```powershell
powershell -File napi\scripts\build.ps1
cd iptrace; ..\.venv\Scripts\python -m PyInstaller build\iptrace.spec --clean --noconfirm; cd ..
```

결과: `napi\dist\napi.exe`, `iptrace\dist\iptrace\iptrace.exe`·`iptrace-cli.exe`.

## 사내 정보

이 저장소는 공개 저장소예요.

- iptrace 의 메일 발송 주소·받는 사람·로그 경로 기본값은 **가짜 값**이에요(`https://internal.mailapi.com`, `security@foo.bar`, `/data/webserver/...`). 실제 값은 프로그램 설정 화면에서 넣어요.
- 실제 회사 정보(주소, 메일, 서버 경로 등)는 코드·문서·테스트·커밋 메시지 어디에도 커밋하지 않아요.
- push 하기 전에 검사해요. 치환표(원래 값 ↔ 가짜 값)는 저장소 밖 JSON 파일에 두고 넘겨요.

  ```powershell
  .venv\Scripts\python tools\scrub.py --map <저장소 밖 치환표.json> --check
  ```

  `clean` 이 나와야 push 해요.
