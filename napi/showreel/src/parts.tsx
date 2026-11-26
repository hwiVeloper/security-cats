import React from "react";
import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { CAT } from "./catSvgs";

export const C = {
  bg: "#fbf7f2",
  ink: "#3a2a20",
  muted: "#8a7a6e",
  line: "#efe5da",
  accent: "#f29a3f",
  accentSoft: "#fff1e0",
  purple: "#7b6cf6",
  purpleSoft: "#efedff",
  outline: "#5a3d2b",
  awake: "#22a06b",
  drowsy: "#d9a400",
  rip: "#8b8f98",
  talk: "#e8590c",
  ghost: "#0ea5b7",
  probe: "#8a7a6e",
};

export const FONT = '"Malgun Gothic", "Apple SD Gothic Neo", sans-serif';
export const MONO = 'Consolas, "Cascadia Mono", monospace';

export const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

/** 0→1 스프링 (delay 프레임 뒤 시작) */
export const usePop = (delay = 0, damping = 12) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  return spring({ frame: frame - delay, fps, config: { damping, stiffness: 140, mass: 0.7 } });
};

/** 화면 전체에서 한 번만 넣는 고양이 공용 defs + 애니메이션 끄기 CSS */
export const CatDefs: React.FC = () => (
  <>
    <style>{`
      .napnyang .eyes-open, .napnyang .magnifier, .napnyang .zs { display: none; }
      .napnyang.scanning .eyes-open, .napnyang.scanning .magnifier { display: inline; }
      .napnyang.scanning .eyes-sleep, .napnyang.scanning .snot { display: none; }
      .napnyang, .napi-face { width: 100%; height: 100%; display: block; overflow: visible; }
    `}</style>
    <div style={{ position: "absolute", width: 0, height: 0 }} dangerouslySetInnerHTML={{ __html: CAT.defs }} />
  </>
);

/** 방석 위 냅냥이. 숨쉬기·꼬리·콧물방울을 프레임으로 움직인다 */
export const Napnyang: React.FC<{ width: number; scanning?: boolean; zs?: boolean; style?: React.CSSProperties }> = ({
  width,
  scanning = false,
  zs = true,
  style,
}) => {
  const f = useCurrentFrame();
  const breath = 1 + 0.03 * Math.sin(f / 11);
  const tail = 7 * Math.sin(f / 8);
  const bubble = 0.75 + 0.35 * (0.5 + 0.5 * Math.sin(f / 11));
  let svg: string = CAT.full
    .replace('<g class="breathe">', `<g class="breathe" transform="translate(150 175) scale(1 ${breath}) translate(-150 -175)">`)
    .replace('<g class="tail">', `<g class="tail" transform="rotate(${tail} 198 150)">`)
    .replace('<g class="snot">', `<g class="snot" transform="translate(124 128) scale(${bubble}) translate(-124 -128)">`);
  if (scanning) {
    const bob = 3 * Math.sin(f / 5);
    svg = svg
      .replace('class="napnyang"', 'class="napnyang scanning"')
      .replace('<g class="magnifier">', `<g class="magnifier" transform="translate(${bob} ${-Math.abs(bob)})">`);
  }
  const height = (width * 190) / 230;
  return (
    <div style={{ position: "relative", width, height, ...style }}>
      <div style={{ width, height }} dangerouslySetInnerHTML={{ __html: svg }} />
      {zs && !scanning && <Zzz x={width * 0.6} y={height * 0.18} size={width * 0.13} />}
    </div>
  );
};

/** 위로 떠오르며 사라지는 Z 세 개 */
export const Zzz: React.FC<{ x: number; y: number; size: number }> = ({ x, y, size }) => {
  const f = useCurrentFrame();
  const period = 54;
  return (
    <>
      {[0, 1, 2].map((i) => {
        const t = ((f + i * (period / 3)) % period) / period;
        const op = interpolate(t, [0, 0.2, 0.75, 1], [0, 1, 1, 0]);
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: x + t * size * 1.6,
              top: y - t * size * 2.2,
              fontFamily: '"Arial Rounded MT Bold", Arial, sans-serif',
              fontWeight: 900,
              fontSize: size * (0.7 + t * 0.6),
              color: C.purple,
              opacity: op,
              transform: `rotate(${-12 + t * 20}deg)`,
            }}
          >
            Z
          </div>
        );
      })}
    </>
  );
};

export const Face: React.FC<{ status: keyof typeof CAT.faces; size: number }> = ({ status, size }) => (
  <div style={{ width: size, height: size }} dangerouslySetInnerHTML={{ __html: CAT.faces[status] }} />
);

export const STATUSES: { key: keyof typeof CAT.faces; label: string; color: string; soft: string; desc: string }[] = [
  { key: "awake", label: "깨어있음", color: C.awake, soft: "#e3f6ec", desc: "지금도 쓰여요" },
  { key: "drowsy", label: "꾸벅꾸벅", color: C.drowsy, soft: "#fdf5d6", desc: "요즘 뜸해요" },
  { key: "asleep", label: "잠듦", color: C.purple, soft: C.purpleSoft, desc: "아무도 안 불러요" },
  { key: "rip", label: "영면", color: C.rip, soft: "#eeeff1", desc: "은퇴한 API" },
  { key: "sleeptalk", label: "잠꼬대", color: C.talk, soft: "#fdebe0", desc: "은퇴했는데 불려요" },
  { key: "ghost", label: "유령", color: C.ghost, soft: "#ddf4f7", desc: "명세에 없어요" },
  { key: "probe", label: "외부 탐색 시도", color: C.probe, soft: "#f2ede8", desc: "스캐너 · 봇 요청" },
];

/** 부드러운 크림색 배경 + 은은한 점 무늬 */
export const Backdrop: React.FC<{ tint?: string }> = ({ tint = "#fdeee0" }) => {
  const f = useCurrentFrame();
  return (
    <div
      style={{
        position: "absolute",
        inset: 0,
        background: `radial-gradient(1200px 800px at ${20 + 5 * Math.sin(f / 60)}% 10%, ${tint} 0%, rgba(255,255,255,0) 60%),
          radial-gradient(1000px 700px at 90% 100%, #f1ecff 0%, rgba(255,255,255,0) 60%), ${C.bg}`,
      }}
    >
      <div
        style={{
          position: "absolute",
          inset: 0,
          opacity: 0.35,
          backgroundImage: `radial-gradient(${C.line} 2px, transparent 2px)`,
          backgroundSize: "36px 36px",
          backgroundPosition: `${f * 0.4}px ${f * 0.2}px`,
        }}
      />
    </div>
  );
};

/** 장면 시작·끝에 페이드 */
export const SceneFade: React.FC<{ dur: number; children: React.ReactNode; fadeIn?: number; fadeOut?: number }> = ({
  dur,
  children,
  fadeIn = 8,
  fadeOut = 8,
}) => {
  const f = useCurrentFrame();
  const op = Math.min(
    fadeIn ? interpolate(f, [0, fadeIn], [0, 1], clamp) : 1,
    fadeOut ? interpolate(f, [dur - fadeOut, dur], [1, 0], clamp) : 1,
  );
  return <div style={{ position: "absolute", inset: 0, opacity: op }}>{children}</div>;
};

export const Pill: React.FC<{ children: React.ReactNode; color?: string; bg?: string; size?: number; style?: React.CSSProperties }> = ({
  children,
  color = C.accent,
  bg = C.accentSoft,
  size = 30,
  style,
}) => (
  <div
    style={{
      display: "inline-flex",
      alignItems: "center",
      gap: 10,
      padding: `${size * 0.35}px ${size * 0.8}px`,
      borderRadius: 999,
      background: bg,
      color,
      fontWeight: 800,
      fontSize: size,
      ...style,
    }}
  >
    {children}
  </div>
);
