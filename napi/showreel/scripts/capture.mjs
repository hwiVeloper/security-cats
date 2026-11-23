// napi UI(mock)를 고해상도로 캡처해 public/ui-*.png 로 저장한다.
// 사용: node scripts/capture.mjs  (Remotion이 받은 chrome-headless-shell 사용)
import http from "node:http";
import fs from "node:fs";
import path from "node:path";
import puppeteer from "puppeteer-core";

const ROOT = path.resolve("../napi/ui");
const OUT = path.resolve("public");
const CHROME = path.resolve(
  "node_modules/.remotion/chrome-headless-shell/win64/chrome-headless-shell-win64/chrome-headless-shell.exe",
);
const TYPES = { ".html": "text/html; charset=utf-8", ".js": "text/javascript; charset=utf-8", ".css": "text/css; charset=utf-8", ".svg": "image/svg+xml", ".png": "image/png" };

const server = http.createServer((req, res) => {
  const p = path.join(ROOT, decodeURIComponent(new URL(req.url, "http://x").pathname));
  if (!p.startsWith(ROOT) || !fs.existsSync(p) || fs.statSync(p).isDirectory()) { res.writeHead(404); res.end(); return; }
  res.writeHead(200, { "content-type": TYPES[path.extname(p)] || "application/octet-stream" });
  fs.createReadStream(p).pipe(res);
});
await new Promise((r) => server.listen(8790, "127.0.0.1", r));

const browser = await puppeteer.launch({ executablePath: CHROME, headless: "shell", args: ["--no-sandbox", "--lang=ko-KR"] });
const page = await browser.newPage();
await page.setViewport({ width: 1280, height: 800, deviceScaleFactor: 2 });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const shot = async (name) => { await page.screenshot({ path: path.join(OUT, `ui-${name}.png`) }); console.log("saved", name); };
const clickText = async (sel, text) => {
  const ok = await page.evaluate((sel, text) => {
    const el = [...document.querySelectorAll(sel)].find((e) => e.textContent.trim().includes(text) && e.offsetParent !== null);
    if (el) el.click();
    return !!el;
  }, sel, text);
  if (!ok) console.log("NOT FOUND", sel, text);
  return ok;
};

const step = process.argv[2] || "all";
await page.goto("http://127.0.0.1:8790/index.html?mock=1");
await sleep(300);
if (step === "all" || step === "boot") await shot("boot");
await sleep(3000);
await shot("main");

await clickText("button", "상태 설명");
await sleep(600);
await shot("help");
await page.keyboard.press("Escape");
await sleep(400);

await clickText("button, [role=tab]", "외부 탐색 시도");
await sleep(600);
await shot("probe");

await clickText("button, [role=tab]", "잠듦");
await sleep(500);
await shot("asleep");

await page.evaluate(() => [...document.querySelectorAll("button")].find((b) => b.textContent.trim() === "▶ 스캔").click());
let n = 0;
for (let i = 0; i < 30; i++) {
  await sleep(500);
  const dlg = await page.evaluate(() => {
    const d = [...document.querySelectorAll(".modal, [role=dialog]")].find((x) => x.offsetParent !== null);
    return d ? d.innerText.slice(0, 120) : null;
  });
  if (dlg) {
    console.log("DIALOG", JSON.stringify(dlg));
    await shot(`dlg${n}`);
    const pw = await page.$(".modal input[type=password], [role=dialog] input[type=password]");
    if (pw) { await pw.type("demo-pass"); }
    await sleep(300);
    await shot(`dlg${n}b`);
    await page.evaluate(() => {
      const d = [...document.querySelectorAll(".modal, [role=dialog]")].find((x) => x.offsetParent !== null);
      const bs = [...d.querySelectorAll("button")].filter((b) => !/취소|✕|닫기/.test(b.textContent) && !b.disabled);
      bs[bs.length - 1].click();
    });
    n++;
    continue;
  }
  await shot(`scan${i}`);
}
// 리포트: mock 결과를 같은 페이지에서 renderReport로 그린다
await page.goto("http://127.0.0.1:8790/index.html?mock=1");
await sleep(1500);
await page.evaluate(async () => {
  const r = await window.pywebview.api.get_result("라운드페이 API");
  document.body.innerHTML = '<div class="report" id="rep"></div>';
  window.NapiCat.ensureDefs(document);
  window.NapiRender.renderReport(document.getElementById("rep"), r, {});
});
await sleep(800);
await shot("report");

await browser.close();
server.close();
