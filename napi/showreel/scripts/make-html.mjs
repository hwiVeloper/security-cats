// 압축본 영상을 base64로 넣은 단일 HTML을 만든다. 파일 하나만 올려도 바로 재생된다.
// 사용: node scripts/make-html.mjs [입력 mp4] [출력 html]
import fs from "node:fs";

const src = process.argv[2] || "out/napi-showreel-web.mp4";
const out = process.argv[3] || "out/napi-showreel.html";
const tpl = fs.readFileSync(new URL("./template.html", import.meta.url), "utf8");
const b64 = fs.readFileSync(src).toString("base64");
fs.writeFileSync(out, tpl.replace("__VIDEO_BASE64__", b64));
console.log(out, (fs.statSync(out).size / 1048576).toFixed(1) + "MB");
