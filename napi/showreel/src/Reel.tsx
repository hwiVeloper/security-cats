import React from "react";
import { AbsoluteFill, Html5Audio, Img, Sequence, interpolate, staticFile, useCurrentFrame, Easing } from "remotion";
import { Backdrop, C, CatDefs, FONT, Face, MONO, Napnyang, Pill, STATUSES, SceneFade, clamp, usePop } from "./parts";
import { HAS_MUSIC } from "./music";

// 장면 길이 (30fps). music.mp3는 약 100BPM(한 박 17.9프레임), 첫 박이 9.5프레임에 온다.
// 장면 경계를 박자(4·10·17·26·30·34번째 박)에 맞췄다.
const S = {
  intro: 81,
  problem: 108,
  how: 125,
  screens: 162,
  statuses: 71,
  outro: 73,
};
const at = (() => {
  let t = 0;
  const o: Record<keyof typeof S, number> = {} as never;
  (Object.keys(S) as (keyof typeof S)[]).forEach((k) => {
    o[k] = t;
    t += S[k];
  });
  return o;
})();
export const REEL_FRAMES = Object.values(S).reduce((a, b) => a + b, 0);

const ease = Easing.bezier(0.22, 1, 0.36, 1);

/* ───────── 1. 인트로 ───────── */
const Intro: React.FC = () => {
  const f = useCurrentFrame();
  const cat = usePop(0, 14);
  const letters = "napi".split("");
  const sub = usePop(26);
  const tag = usePop(36);
  return (
    <SceneFade dur={S.intro} fadeIn={0}>
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", flexDirection: "row", gap: 70 }}>
        <div style={{ transform: `scale(${0.6 + 0.4 * cat}) translateY(${(1 - cat) * 60}px)`, opacity: cat }}>
          <Napnyang width={620} />
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", fontSize: 230, fontWeight: 900, color: C.ink, letterSpacing: -6, lineHeight: 1 }}>
            {letters.map((l, i) => {
              const p = usePop(8 + i * 4, 9);
              return (
                <span key={i} style={{ display: "inline-block", transform: `translateY(${(1 - p) * 120}px) scale(${0.5 + 0.5 * p})`, opacity: Math.min(1, p * 1.5) }}>
                  {l}
                </span>
              );
            })}
          </div>
          <div style={{ fontSize: 56, fontWeight: 800, color: C.accent, opacity: sub, transform: `translateX(${(1 - sub) * -40}px)` }}>
            Nap + API
          </div>
          <div style={{ fontSize: 44, color: C.muted, opacity: tag, transform: `translateX(${(1 - tag) * -40}px)` }}>
            잠든 API를 찾아 주는 냅냥이
          </div>
        </div>
      </AbsoluteFill>
      {/* 화면 위로 살짝 떠오르는 효과 */}
      <AbsoluteFill style={{ background: C.bg, opacity: interpolate(f, [0, 10], [1, 0], clamp), pointerEvents: "none" }} />
    </SceneFade>
  );
};

/* ───────── 2. 문제 ───────── */
const ProblemCard: React.FC<{ icon: string; title: string; sub: string; delay: number }> = ({ icon, title, sub, delay }) => {
  const p = usePop(delay, 11);
  return (
    <div
      style={{
        width: 470,
        padding: "44px 40px",
        borderRadius: 36,
        background: "#fff",
        border: `3px solid ${C.line}`,
        boxShadow: "0 24px 60px rgba(90,61,43,.10)",
        transform: `translateY(${(1 - p) * 90}px) rotate(${(1 - p) * -6}deg)`,
        opacity: Math.min(1, p * 1.4),
      }}
    >
      <div style={{ fontSize: 84, lineHeight: 1 }}>{icon}</div>
      <div style={{ fontSize: 46, fontWeight: 900, color: C.ink, marginTop: 24 }}>{title}</div>
      <div style={{ fontSize: 30, color: C.muted, marginTop: 10 }}>{sub}</div>
    </div>
  );
};

const Problem: React.FC = () => {
  const f = useCurrentFrame();
  const head = usePop(0, 13);
  const bottom = usePop(54);
  return (
    <SceneFade dur={S.problem}>
      <AbsoluteFill style={{ alignItems: "center", paddingTop: 120 }}>
        <div style={{ opacity: head, transform: `translateY(${(1 - head) * 40}px)`, textAlign: "center" }}>
          <Pill size={32}>금융감독원 점검 대비</Pill>
          <div style={{ fontSize: 100, fontWeight: 900, color: C.ink, marginTop: 28, letterSpacing: -2 }}>
            안 쓰는 API, 어떻게 찾죠?
          </div>
        </div>
        <div style={{ display: "flex", gap: 44, marginTop: 70 }}>
          <ProblemCard icon="📘" title="API 수백 개" sub="Swagger · OpenAPI 명세" delay={9} />
          <ProblemCard icon="🗂️" title="1년 치 접속 로그" sub="Apache · nginx · WebtoB" delay={18} />
          <ProblemCard icon="🔒" title="로그는 반출 금지" sub="서버 밖으로 못 가져와요" delay={27} />
        </div>
        <div
          style={{
            marginTop: 60,
            fontSize: 40,
            color: C.muted,
            opacity: bottom,
            transform: `scale(${0.9 + 0.1 * bottom})`,
          }}
        >
          하나하나 대조하면… <b style={{ color: C.talk }}>며칠</b>이 걸려요 😵
        </div>
      </AbsoluteFill>
      <div style={{ position: "absolute", inset: 0, opacity: interpolate(f, [0, 1], [1, 1]) }} />
    </SceneFade>
  );
};

/* ───────── 3. 동작 방식 ───────── */
const SPEC_LINES = ["GET  /v1/payments/{id}", "POST /v1/cards", "GET  /v1/merchants", "PUT  /v1/notify/test", "…"];

const How: React.FC = () => {
  const f = useCurrentFrame();
  const title = usePop(0);
  const spec = usePop(8);
  const servers = [usePop(14), usePop(19), usePop(24)];
  const cat = usePop(4, 14);
  const caption = usePop(56);
  const done = usePop(84, 10);
  const CX = 960;
  const CY = 560;
  const srvY = [330, 560, 790];
  return (
    <SceneFade dur={S.how}>
      <AbsoluteFill>
        <div style={{ position: "absolute", top: 70, width: "100%", textAlign: "center", opacity: title, transform: `translateY(${(1 - title) * 30}px)` }}>
          <div style={{ fontSize: 72, fontWeight: 900, color: C.ink }}>napi는 이렇게 일해요</div>
        </div>

        {/* 연결선 + 움직이는 점 */}
        <svg width={1920} height={1080} style={{ position: "absolute", inset: 0 }}>
          {srvY.map((y, i) => {
            const prog = interpolate(f, [30 + i * 4, 44 + i * 4], [0, 1], clamp);
            const x1 = 1420;
            const x2 = CX + 170;
            return (
              <g key={i}>
                <line x1={x1} y1={y} x2={x1 - (x1 - x2) * prog} y2={y + (CY - y) * prog} stroke={C.line} strokeWidth={8} strokeLinecap="round" />
                {[0, 1, 2].map((k) => {
                  const t = ((f - 44 - k * 9 - i * 5) % 30) / 30;
                  if (f < 44 + k * 9 + i * 5) return null;
                  return <circle key={k} cx={x1 + (x2 - x1) * t} cy={y + (CY - y) * t} r={11} fill={C.purple} opacity={interpolate(t, [0, 0.15, 0.85, 1], [0, 1, 1, 0])} />;
                })}
              </g>
            );
          })}
          {(() => {
            const prog = interpolate(f, [26, 40], [0, 1], clamp);
            const x1 = 640;
            const x2 = CX - 170;
            return (
              <g>
                <line x1={x1} y1={CY} x2={x1 + (x2 - x1) * prog} y2={CY} stroke={C.line} strokeWidth={8} strokeLinecap="round" />
                {[0, 1].map((k) => {
                  if (f < 40 + k * 12) return null;
                  const t = ((f - 40 - k * 12) % 28) / 28;
                  return <circle key={k} cx={x1 + (x2 - x1) * t} cy={CY} r={11} fill={C.accent} opacity={interpolate(t, [0, 0.15, 0.85, 1], [0, 1, 1, 0])} />;
                })}
              </g>
            );
          })()}
        </svg>

        {/* 명세 카드 */}
        <div
          style={{
            position: "absolute",
            left: 110,
            top: CY - 210,
            width: 530,
            padding: 34,
            borderRadius: 30,
            background: "#fff",
            border: `3px solid ${C.line}`,
            boxShadow: "0 20px 50px rgba(90,61,43,.10)",
            opacity: spec,
            transform: `translateX(${(1 - spec) * -80}px)`,
          }}
        >
          <div style={{ fontSize: 34, fontWeight: 900, color: C.ink }}>📘 Swagger 명세</div>
          <div style={{ marginTop: 18, display: "flex", flexDirection: "column", gap: 8 }}>
            {SPEC_LINES.map((l, i) => (
              <div key={i} style={{ fontFamily: MONO, fontSize: 25, color: C.muted, opacity: interpolate(f, [14 + i * 3, 20 + i * 3], [0, 1], clamp) }}>
                {l}
              </div>
            ))}
          </div>
        </div>

        {/* 서버들 */}
        <div style={{ position: "absolute", left: 1420, top: 210, fontSize: 28, fontWeight: 800, color: C.purple, opacity: servers[0] }}>
          SSH · 읽기 전용 계정
        </div>
        {srvY.map((y, i) => {
          const p = servers[i];
          const bar = interpolate(f, [40 + i * 6, 90 + i * 4], [0, 1], { ...clamp, easing: ease });
          return (
            <div
              key={i}
              style={{
                position: "absolute",
                left: 1420,
                top: y - 80,
                width: 400,
                height: 160,
                borderRadius: 26,
                background: "#2f2420",
                color: "#f6efe8",
                padding: "22px 28px",
                boxSizing: "border-box",
                opacity: p,
                transform: `translateX(${(1 - p) * 80}px)`,
                boxShadow: "0 18px 40px rgba(47,36,32,.25)",
              }}
            >
              <div style={{ display: "flex", justifyContent: "space-between", fontSize: 30, fontWeight: 800 }}>
                <span>🖥️ WAS-0{i + 1}</span>
                <span style={{ fontSize: 24, color: bar >= 1 ? "#7be0a8" : "#c9b8ff" }}>{bar >= 1 ? "완료 ✓" : "집계 중"}</span>
              </div>
              <div style={{ fontFamily: MONO, fontSize: 22, color: "#bfae9f", marginTop: 12 }}>awk · gzip · 경로별 호출 수</div>
              <div style={{ marginTop: 14, height: 12, borderRadius: 6, background: "#4a3a33" }}>
                <div style={{ width: `${bar * 100}%`, height: "100%", borderRadius: 6, background: bar >= 1 ? "#7be0a8" : C.purple }} />
              </div>
            </div>
          );
        })}

        {/* 가운데 냅냥이 */}
        <div
          style={{
            position: "absolute",
            left: CX - 190,
            top: CY - 190,
            width: 380,
            height: 380,
            borderRadius: "50%",
            background: "#fff",
            border: `6px solid ${C.accentSoft}`,
            boxShadow: "0 24px 70px rgba(242,154,63,.25)",
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            transform: `scale(${cat})`,
          }}
        >
          <Napnyang width={300} scanning={f < 96} />
        </div>
        <div
          style={{
            position: "absolute",
            left: CX - 220,
            top: CY + 220,
            width: 440,
            textAlign: "center",
            transform: `scale(${done})`,
            opacity: done,
          }}
        >
          <Pill color={C.awake} bg="#e3f6ec" size={34}>
            ✓ API 161개 판정 완료
          </Pill>
        </div>

        <div
          style={{
            position: "absolute",
            bottom: 60,
            width: "100%",
            textAlign: "center",
            fontSize: 38,
            color: C.ink,
            opacity: caption,
            transform: `translateY(${(1 - caption) * 20}px)`,
          }}
        >
          집계는 <b style={{ color: C.purple }}>서버 안에서</b>, 가져오는 건 <b style={{ color: C.accent }}>경로별 호출 수</b>뿐이에요
        </div>
      </AbsoluteFill>
    </SceneFade>
  );
};

/* ───────── 4. 실제 화면 ───────── */
const SHOTS = [
  { img: "ui-scan.png", n: "01", title: "버튼 한 번으로 스캔", desc: "여러 서버를 동시에 집계하고\n진행률도 바로 보여 줘요", from: [1.0, 1.32], origin: "45% 5%" },
  { img: "ui-main.png", n: "02", title: "결과는 고양이로", desc: "API마다 잠듦 · 영면 같은\n상태를 붙여 줘요", from: [1.0, 1.12], origin: "60% 0%" },
  { img: "ui-probe.png", n: "03", title: "스캐너 요청은 따로", desc: "/.env, /wp-login.php 같은\n탐색 요청은 따로 모아요", from: [1.08, 1.22], origin: "35% 55%" },
];
const SHOT_LEN = S.screens / SHOTS.length;

const Screens: React.FC = () => {
  const f = useCurrentFrame();
  const win = usePop(0, 15);
  const idx = Math.min(SHOTS.length - 1, Math.floor(f / SHOT_LEN));
  const W = 1300;
  const H = (W * 1600) / 2560;
  return (
    <SceneFade dur={S.screens}>
      <AbsoluteFill>
        {/* 왼쪽 설명 */}
        {SHOTS.map((s, i) => {
          const local = f - i * SHOT_LEN;
          const op = interpolate(local, [0, 10, SHOT_LEN - 8, SHOT_LEN], [0, 1, 1, i === SHOTS.length - 1 ? 1 : 0], clamp);
          const y = interpolate(local, [0, 12], [40, 0], { ...clamp, easing: ease });
          return (
            <div key={i} style={{ position: "absolute", left: 90, top: 330, width: 460, opacity: op, transform: `translateY(${y}px)` }}>
              <div style={{ fontSize: 34, fontWeight: 900, color: C.accent, fontFamily: MONO }}>{s.n}</div>
              <div style={{ fontSize: 60, fontWeight: 900, color: C.ink, marginTop: 10, lineHeight: 1.2 }}>{s.title}</div>
              <div style={{ fontSize: 31, color: C.muted, marginTop: 24, lineHeight: 1.55, whiteSpace: "pre-line" }}>{s.desc}</div>
            </div>
          );
        })}
        {/* 진행 점 */}
        <div style={{ position: "absolute", left: 90, top: 760, display: "flex", gap: 14 }}>
          {SHOTS.map((_, i) => (
            <div key={i} style={{ width: i === idx ? 54 : 18, height: 18, borderRadius: 9, background: i === idx ? C.accent : C.line }} />
          ))}
        </div>

        {/* 앱 창 */}
        <div
          style={{
            position: "absolute",
            left: 560,
            top: (1080 - H - 48) / 2,
            width: W,
            borderRadius: 22,
            overflow: "hidden",
            background: "#fff",
            boxShadow: "0 40px 100px rgba(90,61,43,.22), 0 0 0 2px rgba(90,61,43,.08)",
            transform: `translateY(${(1 - win) * 120}px) scale(${0.92 + 0.08 * win})`,
            opacity: Math.min(1, win * 1.5),
          }}
        >
          <div style={{ height: 48, display: "flex", alignItems: "center", padding: "0 20px", gap: 12, background: "#f6efe8", fontSize: 22, color: C.ink }}>
            <div style={{ width: 28, height: 28 }}>
              <Face status="asleep" size={28} />
            </div>
            <span style={{ fontWeight: 700 }}>napi</span>
            <span style={{ marginLeft: "auto", letterSpacing: 26, color: C.muted }}>─ □ ✕</span>
          </div>
          <div style={{ position: "relative", width: W, height: H, overflow: "hidden" }}>
            {SHOTS.map((s, i) => {
              const local = f - i * SHOT_LEN;
              // 다음 화면이 오른쪽에서 밀고 들어온다 (겹쳐 비치지 않게)
              const x = i === 0 ? 0 : interpolate(local, [-8, 4], [W, 0], { ...clamp, easing: ease });
              if (local < -8 || local > SHOT_LEN + 8) return null;
              const z = interpolate(local, [0, SHOT_LEN + 6], s.from, { ...clamp, easing: Easing.inOut(Easing.quad) });
              return (
                <Img
                  key={i}
                  src={staticFile(s.img)}
                  style={{ position: "absolute", inset: 0, width: W, height: H, transform: `translateX(${x}px) scale(${z})`, transformOrigin: s.origin, boxShadow: "-30px 0 60px rgba(90,61,43,.18)" }}
                />
              );
            })}
          </div>
        </div>
      </AbsoluteFill>
    </SceneFade>
  );
};

/* ───────── 5. 일곱 가지 상태 ───────── */
const Statuses: React.FC = () => {
  const f = useCurrentFrame();
  const title = usePop(0);
  const badge = usePop(40, 9);
  return (
    <SceneFade dur={S.statuses}>
      <AbsoluteFill style={{ alignItems: "center", paddingTop: 170 }}>
        <div style={{ fontSize: 76, fontWeight: 900, color: C.ink, opacity: title, transform: `translateY(${(1 - title) * 30}px)` }}>
          API의 상태는 <span style={{ color: C.accent }}>일곱 가지</span>
        </div>
        <div style={{ display: "flex", gap: 22, marginTop: 90 }}>
          {STATUSES.map((s, i) => {
            const p = usePop(8 + i * 4, 10);
            const del = s.key === "asleep" || s.key === "rip";
            const wobble = del ? Math.sin(f / 4) * 2 * badge : 0;
            return (
              <div
                key={s.key}
                style={{
                  position: "relative",
                  width: 232,
                  padding: "34px 10px 30px",
                  borderRadius: 32,
                  background: "#fff",
                  border: `4px solid ${del && badge > 0.1 ? s.color : C.line}`,
                  textAlign: "center",
                  boxShadow: "0 18px 44px rgba(90,61,43,.10)",
                  transform: `translateY(${(1 - p) * 80}px) scale(${0.6 + 0.4 * p}) rotate(${wobble}deg)`,
                  opacity: Math.min(1, p * 1.5),
                }}
              >
                <div style={{ width: 120, height: 124, margin: "0 auto" }}>
                  <Face status={s.key} size={120} />
                </div>
                <div style={{ display: "inline-block", marginTop: 18, padding: "6px 18px", borderRadius: 999, background: s.soft, color: s.color, fontWeight: 900, fontSize: s.label.length > 4 ? 25 : 31 }}>
                  {s.label}
                </div>
                <div style={{ fontSize: 24, color: C.muted, marginTop: 14 }}>{s.desc}</div>
                {del && (
                  <div
                    style={{
                      position: "absolute",
                      top: -26,
                      right: -14,
                      transform: `scale(${badge}) rotate(8deg)`,
                      background: C.talk,
                      color: "#fff",
                      fontWeight: 900,
                      fontSize: 24,
                      padding: "8px 16px",
                      borderRadius: 14,
                      boxShadow: "0 8px 20px rgba(232,89,12,.35)",
                    }}
                  >
                    삭제 후보
                  </div>
                )}
              </div>
            );
          })}
        </div>
        <div style={{ marginTop: 90, fontSize: 42, color: C.ink, opacity: badge, transform: `translateY(${(1 - badge) * 20}px)` }}>
          <b style={{ color: C.purple }}>잠듦</b> · <b style={{ color: C.rip }}>영면</b>은 <b style={{ color: C.talk }}>삭제 후보</b>로 바로 골라 줘요
        </div>
      </AbsoluteFill>
    </SceneFade>
  );
};

/* ───────── 6. 리포트 + 엔딩 ───────── */
const Outro: React.FC = () => {
  const f = useCurrentFrame();
  const rep = usePop(0, 14);
  const chips = usePop(12);
  const endStart = 36;
  const end = usePop(endStart, 13);
  const repOut = interpolate(f, [endStart - 6, endStart + 6], [1, 0], clamp);
  return (
    <SceneFade dur={S.outro} fadeOut={0}>
      <AbsoluteFill style={{ opacity: repOut }}>
        <div style={{ position: "absolute", left: 110, top: 360, width: 560 }}>
          <div style={{ fontSize: 64, fontWeight: 900, color: C.ink, lineHeight: 1.2, opacity: rep }}>
            리포트로
            <br />
            바로 공유
          </div>
          <div style={{ display: "flex", gap: 16, marginTop: 34, opacity: chips, transform: `translateY(${(1 - chips) * 20}px)` }}>
            <Pill size={34}>HTML</Pill>
            <Pill size={34} color={C.purple} bg={C.purpleSoft}>
              PDF
            </Pill>
          </div>
        </div>
        <div
          style={{
            position: "absolute",
            left: 740,
            top: 130,
            width: 1100,
            borderRadius: 20,
            overflow: "hidden",
            boxShadow: "0 40px 100px rgba(90,61,43,.22)",
            transform: `perspective(1600px) rotateY(${-14 + 6 * rep}deg) rotateX(4deg) translateY(${(1 - rep) * 200}px)`,
            opacity: rep,
          }}
        >
          <Img src={staticFile("ui-report.png")} style={{ width: 1100, display: "block", transform: `translateY(${interpolate(f, [0, endStart], [0, -60], clamp)}px)` }} />
        </div>
      </AbsoluteFill>

      <AbsoluteFill style={{ alignItems: "center", justifyContent: "center", opacity: interpolate(f, [endStart - 4, endStart + 8], [0, 1], clamp) }}>
        <div style={{ transform: `scale(${0.7 + 0.3 * end})` }}>
          <Napnyang width={460} />
        </div>
        <div style={{ fontSize: 150, fontWeight: 900, color: C.ink, letterSpacing: -4, lineHeight: 1, marginTop: 10, transform: `translateY(${(1 - end) * 40}px)` }}>napi</div>
        <div style={{ fontSize: 46, color: C.muted, marginTop: 22, opacity: end }}>
          잠든 API, <b style={{ color: C.accent }}>냅냥이</b>가 찾아 드려요
        </div>
        <div style={{ fontSize: 26, color: C.muted, marginTop: 40, opacity: interpolate(f, [endStart + 16, endStart + 28], [0, 1], clamp) }}>
          Claude Code와 함께 만들었어요
        </div>
      </AbsoluteFill>
    </SceneFade>
  );
};

export const Reel: React.FC = () => {
  return (
    <AbsoluteFill style={{ fontFamily: FONT, background: C.bg, color: C.ink, overflow: "hidden" }}>
      <CatDefs />
      <Backdrop />
      <Sequence from={at.intro} durationInFrames={S.intro}>
        <Intro />
      </Sequence>
      <Sequence from={at.problem} durationInFrames={S.problem}>
        <Problem />
      </Sequence>
      <Sequence from={at.how} durationInFrames={S.how}>
        <How />
      </Sequence>
      <Sequence from={at.screens} durationInFrames={S.screens}>
        <Screens />
      </Sequence>
      <Sequence from={at.statuses} durationInFrames={S.statuses}>
        <Statuses />
      </Sequence>
      <Sequence from={at.outro} durationInFrames={S.outro}>
        <Outro />
      </Sequence>
      {HAS_MUSIC && (
        <Html5Audio
          src={staticFile("music.mp3")}
          volume={(f) => interpolate(f, [0, 8, REEL_FRAMES - 40, REEL_FRAMES], [0, 0.85, 0.85, 0], clamp)}
        />
      )}
    </AbsoluteFill>
  );
};
