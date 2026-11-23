# napi 쇼릴 (Remotion)

napi를 소개하는 약 24초짜리 가로형(1920×1080, 30fps) 영상이에요. 슬라이드에 넣는 용도로 만들었어요.

## 구성 (장면 경계를 music.mp3 박자 약 100BPM에 맞춤)

| 구간 | 장면 |
|---|---|
| 0.0–2.7초 | 인트로: 낮잠 자는 냅냥이와 napi 로고 |
| 2.7–6.3초 | 문제: 안 쓰는 API를 어떻게 찾을지 |
| 6.3–10.5초 | 동작 방식: SSH로 서버 안에서 집계하고 결과만 가져옴 |
| 10.5–15.9초 | 실제 화면: 스캔 → 결과 → 외부 탐색 시도 |
| 15.9–20.0초 | 일곱 가지 상태와 삭제 후보 |
| 20.0–24.3초 | 리포트(HTML·PDF)와 엔딩 |

## 다시 만들기

이 PC에는 Node.js가 설치되어 있지 않아요. 그래서 `.tools/`에 압축 해제한 Node.js를 써요. npm 저장소의 `node-win-x64`, `npm` 패키지에서 받았고, git에는 올리지 않아요.

```bash
export PATH="$PWD/.tools/node/package/bin:$PATH"
node_modules/.bin/remotion studio                      # 미리보기
node_modules/.bin/remotion render NapiReel out/napi-showreel.mp4 --crf=18 --pixel-format=yuv420p
```

### 업로드용 단일 HTML

동영상 파일을 올릴 수 없는 곳에서는 영상을 넣은 HTML 파일 하나를 올리면 돼요. 페이지를 열면 무음으로 자동 재생되고, "소리 켜기" 버튼을 누르면 소리가 켜져요.

```bash
node_modules/.bin/remotion render NapiReel out/napi-showreel-web.mp4 --crf=27 --audio-bitrate=128k --pixel-format=yuv420p
node scripts/make-html.mjs            # → out/napi-showreel.html (약 5MB)
```

## 그 밖에

- **앱 화면 다시 찍기:** `node scripts/capture.mjs`를 실행하면 napi mock UI를 띄워서 `public/ui-*.png`를 2배 해상도로 다시 찍어요.
- **음악:** `public/music.mp3`예요. git에는 올리지 않아요. 바꾸면 `src/Reel.tsx`의 장면 길이(`S`)를 새 박자에 맞춰 조정해 주세요. 음악 없이 만들려면 `src/music.ts`의 `HAS_MUSIC`를 `false`로 바꾸면 돼요.
- **고양이 그림:** `src/catSvgs.ts`예요. `napi/ui/js/cat.js`에서 뽑아낸 그림이라 직접 고치지 말고 cat.js를 고치세요.
