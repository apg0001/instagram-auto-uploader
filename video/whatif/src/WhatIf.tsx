// "만약에" 3D 릴스 — 장면 길이·문구·숫자는 모두 props(파이썬이 데이터로 계산)에서 온다.
// 장면마다 visual 로 3D 화면을 고른다: spin(물체 회전) · pile(쌓이는 더미) · growth(연도별 막대) · rise · crash · end
// 물체(item)는 coin | cup. 규칙(remotion 3d 스킬): 3D는 <ThreeCanvas>, 모든 움직임은 useCurrentFrame()으로만.
import { loadFont } from "@remotion/fonts";
import { ThreeCanvas } from "@remotion/three";
import React, { useMemo } from "react";
import * as THREE from "three";
import {
  AbsoluteFill,
  CalculateMetadataFunction,
  Easing,
  Sequence,
  interpolate,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";

for (const [w, f] of [
  [600, "SemiBold"],
  [700, "Bold"],
  [800, "ExtraBold"],
  [900, "Black"],
] as const) {
  loadFont({ family: "Pretendard", url: staticFile(`fonts/Pretendard-${f}.woff2`), weight: String(w) });
}

export type Item = "coin" | "cup";
export type Visual = "spin" | "pile" | "growth" | "rise" | "crash" | "end";

export type Scene = {
  key: string;
  visual: Visual;
  item?: Item;
  frames: number;
  head: string;
  sub?: string;
  big?: string;
  tag?: string; // 큰 숫자 위 작은 꼬리표 (예: "가정")
};

export type Bar = { year: string; log: number; label: string; down?: boolean };

export type WhatIfProps = {
  scenes: Scene[];
  bars: Bar[];
  footer: string;
  accent: string;
};

const BG = "#14112A";
const GOLD = "#F5B700";
const RED = "#FF5A5F";
const ease = Easing.bezier(0.22, 1, 0.36, 1);
const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

export const calcWhatIf: CalculateMetadataFunction<WhatIfProps> = ({ props }) => ({
  durationInFrames: props.scenes.reduce((a, s) => a + s.frames, 0),
});

// ---------------------------------------------------------------- 3D 부품

function coinTexture(): THREE.CanvasTexture {
  const c = document.createElement("canvas");
  c.width = c.height = 256;
  const g = c.getContext("2d")!;
  g.fillStyle = GOLD;
  g.fillRect(0, 0, 256, 256);
  g.strokeStyle = "#C98F00";
  g.lineWidth = 14;
  g.beginPath();
  g.arc(128, 128, 104, 0, Math.PI * 2);
  g.stroke();
  g.translate(128, 128);
  g.rotate(-Math.PI / 2);
  g.translate(-128, -128);
  g.fillStyle = "#A87400";
  g.font = "bold 150px sans-serif";
  g.textAlign = "center";
  g.textBaseline = "middle";
  g.fillText("$", 128, 136);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  return t;
}

type ObjProps = { position?: [number, number, number]; rotation?: [number, number, number]; scale?: number };

const Coin: React.FC<ObjProps> = ({ position = [0, 0, 0], rotation = [0, 0, 0], scale = 1 }) => {
  const face = useMemo(() => coinTexture(), []);
  return (
    <mesh position={position} rotation={rotation} scale={scale}>
      <cylinderGeometry args={[1, 1, 0.16, 48]} />
      <meshStandardMaterial attach="material-0" color="#D9A200" metalness={0.35} roughness={0.35} />
      <meshStandardMaterial attach="material-1" map={face} metalness={0.25} roughness={0.45} />
      <meshStandardMaterial attach="material-2" map={face} metalness={0.25} roughness={0.45} />
    </mesh>
  );
};

/** 테이크아웃 커피잔: 흰 컵 + 갈색 슬리브 + 뚜껑 (세워진 상태, 높이 약 2.4) */
const Cup: React.FC<ObjProps> = ({ position = [0, 0, 0], rotation = [0, 0, 0], scale = 1 }) => (
  <group position={position} rotation={rotation} scale={scale}>
    <mesh>
      <cylinderGeometry args={[0.85, 0.62, 2.1, 40]} />
      <meshStandardMaterial color="#F4F1EC" roughness={0.55} />
    </mesh>
    <mesh position={[0, 0.05, 0]}>
      <cylinderGeometry args={[0.8, 0.7, 0.75, 40]} />
      <meshStandardMaterial color="#8B5A3C" roughness={0.8} />
    </mesh>
    <mesh position={[0, 1.13, 0]}>
      <cylinderGeometry args={[0.92, 0.92, 0.18, 40]} />
      <meshStandardMaterial color="#FFFFFF" roughness={0.4} />
    </mesh>
    <mesh position={[0, 1.27, 0]}>
      <cylinderGeometry args={[0.55, 0.85, 0.14, 40]} />
      <meshStandardMaterial color="#FFFFFF" roughness={0.4} />
    </mesh>
  </group>
);

const Obj: React.FC<ObjProps & { item: Item }> = ({ item, ...p }) => (item === "cup" ? <Cup {...p} /> : <Coin {...p} />);

/** 시드 고정 난수 (렌더할 때마다 같은 더미) */
function rand(i: number): number {
  const x = Math.sin(i * 12.9898) * 43758.5453;
  return x - Math.floor(x);
}

const Pile: React.FC<{ progress: number; item: Item }> = ({ progress, item }) => {
  const max = item === "cup" ? 70 : 140;
  const n = Math.round(interpolate(progress, [0, 1], [1, max], clamp));
  const out = [];
  for (let i = 0; i < n; i++) {
    const a = rand(i) * Math.PI * 2;
    const r = Math.sqrt(rand(i + 999)) * 2.6;
    if (item === "cup") {
      const layer = Math.floor(i / 14);
      out.push(
        <Cup key={i} scale={0.42}
             position={[Math.cos(a) * r * (1 - layer * 0.15), -1.6 + layer * 0.55 + rand(i + 7) * 0.1, Math.sin(a) * r * 0.6]}
             rotation={[(rand(i + 3) - 0.5) * 0.6, rand(i + 5) * 6, (rand(i + 11) - 0.5) * 0.6]} />,
      );
    } else {
      const layer = Math.floor(i / 22);
      out.push(
        <Coin key={i} scale={0.42}
              position={[Math.cos(a) * r * (1 - layer * 0.12), -1.6 + layer * 0.075 + rand(i + 7) * 0.05, Math.sin(a) * r * 0.6]}
              rotation={[(rand(i + 3) - 0.5) * 0.5, rand(i + 5) * 6, (rand(i + 11) - 0.5) * 0.5]} />,
      );
    }
  }
  return <group>{out}</group>;
};

const Lights: React.FC = () => (
  <>
    <ambientLight intensity={0.9} />
    <directionalLight position={[4, 6, 5]} intensity={1.4} />
    <directionalLight position={[-5, 2, -3]} intensity={0.5} color="#9C88FF" />
  </>
);

const Arrow: React.FC<{ up: boolean; p: number }> = ({ up, p }) => (
  <group position={[0, up ? -2.6 + p * 0.6 : -0.2 - p * 0.4, 0.5]} rotation={[0, 0, up ? 0 : Math.PI]}
         scale={[1, Math.max(0.001, p), 1]}>
    <mesh position={[0, 1.1, 0]}>
      <boxGeometry args={[0.55, 2.2, 0.3]} />
      <meshStandardMaterial color={up ? "#3DDC84" : RED} roughness={0.4} />
    </mesh>
    <mesh position={[0, 2.75, 0]}>
      <coneGeometry args={[0.95, 1.2, 3]} />
      <meshStandardMaterial color={up ? "#3DDC84" : RED} roughness={0.4} />
    </mesh>
  </group>
);

// ---------------------------------------------------------------- 글자

const Text: React.FC<{ s: Scene; accent: string; top?: number }> = ({ s, accent, top = 230 }) => {
  const f = useCurrentFrame();
  const y = (d: number) => interpolate(f, [d, d + 14], [40, 0], { ...clamp, easing: ease });
  const o = (d: number) => interpolate(f, [d, d + 14], [0, 1], clamp);
  return (
    <div style={{ position: "absolute", left: 80, right: 80, top, fontFamily: "Pretendard", color: "#fff" }}>
      <div style={{ fontSize: 76, fontWeight: 900, lineHeight: 1.18, whiteSpace: "pre-line", wordBreak: "keep-all",
                    transform: `translateY(${y(0)}px)`, opacity: o(0) }}>{s.head}</div>
      {s.big ? (
        <div style={{ marginTop: 30, transform: `translateY(${y(8)}px)`, opacity: o(8) }}>
          {s.tag ? (
            <span style={{ display: "inline-block", fontSize: 30, fontWeight: 800, color: BG, background: "#C9C2EE",
                           borderRadius: 999, padding: "4px 16px", marginBottom: 8 }}>{s.tag}</span>
          ) : null}
          <div style={{ fontSize: 132, fontWeight: 900, color: accent, letterSpacing: -4, whiteSpace: "pre-line",
                        lineHeight: 1.05 }}>{s.big}</div>
        </div>
      ) : null}
      {s.sub ? (
        <div style={{ marginTop: 26, fontSize: 40, fontWeight: 700, color: "#C9C2EE", lineHeight: 1.45, whiteSpace: "pre-line",
                      wordBreak: "keep-all", transform: `translateY(${y(16)}px)`, opacity: o(16) }}>{s.sub}</div>
      ) : null}
    </div>
  );
};

// ---------------------------------------------------------------- 장면

const SpinScene: React.FC<{ s: Scene; accent: string }> = ({ s, accent }) => {
  const f = useCurrentFrame();
  const { width, height } = useVideoConfig();
  const pop = interpolate(f, [0, 22], [0.2, 1], { ...clamp, easing: Easing.spring({ damping: 12 }) });
  const item = s.item ?? "coin";
  return (
    <AbsoluteFill>
      <ThreeCanvas width={width} height={height} camera={{ position: [0, 0, 9], fov: 40 }}>
        <Lights />
        {item === "cup" ? (
          <Cup position={[0, -0.9, 0]} rotation={[0.25, f * 0.05, 0]} scale={0.95 * pop} />
        ) : (
          <Coin position={[0, -2.4, 0]} rotation={[Math.PI / 2 - 0.3, 0, f * 0.07]} scale={1.45 * pop} />
        )}
      </ThreeCanvas>
      <Text s={s} accent={accent} />
    </AbsoluteFill>
  );
};

const PileScene: React.FC<{ s: Scene; accent: string }> = ({ s, accent }) => {
  const f = useCurrentFrame();
  const { width, height, durationInFrames } = useVideoConfig();
  const p = interpolate(f, [6, durationInFrames - 10], [0, 1], { ...clamp, easing: ease });
  return (
    <AbsoluteFill>
      <ThreeCanvas width={width} height={height} camera={{ position: [0, 1.6 - p * 0.6, 8.5], fov: 42 }}>
        <Lights />
        <group rotation={[0.18, f * 0.004, 0]} position={[0, -1.2, 0]}>
          <Pile progress={p} item={s.item ?? "coin"} />
        </group>
      </ThreeCanvas>
      <Text s={s} accent={accent} />
    </AbsoluteFill>
  );
};

const GrowthScene: React.FC<{ s: Scene; accent: string; bars: Bar[] }> = ({ s, accent, bars }) => {
  const f = useCurrentFrame();
  const { width, height, durationInFrames } = useVideoConfig();
  const per = (durationInFrames - 20) / bars.length;
  const maxLog = Math.max(...bars.map((b) => b.log));
  const minLog = Math.min(...bars.map((b) => b.log));
  const shown = Math.min(bars.length - 1, Math.floor(f / per));
  const camX = interpolate(f, [0, durationInFrames], [-1.2, 1.2], clamp);
  const gap = Math.min(0.6, 6.5 / bars.length);
  return (
    <AbsoluteFill>
      <ThreeCanvas width={width} height={height} camera={{ position: [camX, 1.5, 19], fov: 42 }}>
        <Lights />
        <group position={[0, -6.2, 0]} rotation={[0, -0.2, 0]}>
          {bars.map((b, i) => {
            const grow = interpolate(f, [i * per, i * per + per * 1.6], [0, 1], { ...clamp, easing: ease });
            const h = 0.25 + ((b.log - minLog) / Math.max(0.001, maxLog - minLog)) * 8.5;
            const x = (i - (bars.length - 1) / 2) * gap;
            return (
              <mesh key={b.year} position={[x, (h * grow) / 2, 0]} scale={[1, Math.max(0.001, grow), 1]}>
                <boxGeometry args={[gap * 0.76, h, gap * 0.76]} />
                <meshStandardMaterial color={b.down ? RED : i === shown ? GOLD : accent} metalness={0.2} roughness={0.5} />
              </mesh>
            );
          })}
        </group>
      </ThreeCanvas>
      <Text s={s} accent={accent} />
      <div style={{ position: "absolute", left: 80, right: 80, top: 560, fontFamily: "Pretendard", color: "#fff" }}>
        <div style={{ fontSize: 88, fontWeight: 900, color: bars[shown].down ? RED : GOLD }}>{bars[shown].year}</div>
        <div style={{ fontSize: 50, fontWeight: 800, marginTop: 6 }}>{bars[shown].label}</div>
      </div>
    </AbsoluteFill>
  );
};

/** 금화(또는 컵)가 솟구치며 상승 화살표 / 떨어지며 하락 화살표 */
const MoveScene: React.FC<{ s: Scene; accent: string; up: boolean }> = ({ s, accent, up }) => {
  const f = useCurrentFrame();
  const { width, height } = useVideoConfig();
  const arrow = interpolate(f, [6, 30], [0, 1], { ...clamp, easing: ease });
  const item = s.item ?? "coin";
  return (
    <AbsoluteFill>
      <ThreeCanvas width={width} height={height} camera={{ position: [0, 0, 10], fov: 40 }}>
        <Lights />
        {Array.from({ length: 16 }, (_, i) => {
          const speed = 0.05 + rand(i + 40) * 0.05;
          const t = (f * speed + rand(i + 20) * 7) % 7.5;
          const y = up ? -5 + t : 0.6 - t * 0.8; // 하락은 글자(위쪽) 아래에서만
          return (
            <Obj key={i} item={item} scale={(item === "cup" ? 0.32 : 0.42) + rand(i + 60) * 0.2}
                 position={[(rand(i + 80) - 0.5) * 5.6, y, -1 - rand(i + 90) * 2]}
                 rotation={[Math.PI / 2 - 0.4, f * 0.08 + i, (rand(i) - 0.5) * 0.8]} />
          );
        })}
        <Arrow up={up} p={arrow} />
      </ThreeCanvas>
      <Text s={s} accent={accent} top={260} />
    </AbsoluteFill>
  );
};

/** 마무리: 가운데 큰 물체가 돌고 작은 것들이 둘레를 돈다 */
const EndScene: React.FC<{ s: Scene; accent: string }> = ({ s, accent }) => {
  const f = useCurrentFrame();
  const { width, height } = useVideoConfig();
  const pop = interpolate(f, [0, 20], [0.3, 1], { ...clamp, easing: Easing.spring({ damping: 12 }) });
  const item = s.item ?? "coin";
  return (
    <AbsoluteFill>
      <ThreeCanvas width={width} height={height} camera={{ position: [0, 0, 10], fov: 40 }}>
        <Lights />
        {item === "cup" ? (
          <Cup position={[0, -1.3 + Math.sin(f * 0.08) * 0.12, 0]} rotation={[0.2, f * 0.05, 0]} scale={0.95 * pop} />
        ) : (
          <Coin position={[0, -1.3 + Math.sin(f * 0.08) * 0.12, 0]} rotation={[Math.PI / 2 - 0.25, 0, f * 0.09]} scale={1.7 * pop} />
        )}
        {[0, 1, 2, 3, 4, 5].map((i) => {
          const a = (i / 6) * Math.PI * 2 + f * 0.025;
          return <Coin key={i} scale={0.38} position={[Math.cos(a) * 3.1, -1.3 + Math.sin(a) * 1.1, Math.sin(a) * 1.5 - 0.5]}
                       rotation={[Math.PI / 2, 0, f * 0.07 + i]} />;
        })}
      </ThreeCanvas>
      <Text s={s} accent={accent} top={230} />
    </AbsoluteFill>
  );
};

export const WhatIf: React.FC<WhatIfProps> = ({ scenes, bars, footer, accent }) => {
  let from = 0;
  return (
    <AbsoluteFill style={{ background: `radial-gradient(circle at 50% 30%, #2A2160 0%, ${BG} 70%)` }}>
      {scenes.map((s) => {
        const at = from;
        from += s.frames;
        const body =
          s.visual === "spin" ? <SpinScene s={s} accent={accent} />
          : s.visual === "pile" ? <PileScene s={s} accent={accent} />
          : s.visual === "growth" ? <GrowthScene s={s} accent={accent} bars={bars} />
          : s.visual === "rise" ? <MoveScene s={s} accent={accent} up />
          : s.visual === "crash" ? <MoveScene s={s} accent={accent} up={false} />
          : <EndScene s={s} accent={accent} />;
        return (
          <Sequence key={s.key} from={at} durationInFrames={s.frames}>
            {body}
          </Sequence>
        );
      })}
      <div style={{ position: "absolute", left: 0, right: 0, top: 70, textAlign: "center", fontFamily: "Pretendard",
                    fontSize: 34, fontWeight: 800, color: accent, letterSpacing: 2 }}>만약에 · WHAT IF</div>
      <div style={{ position: "absolute", left: 60, right: 60, bottom: 90, textAlign: "center", fontFamily: "Pretendard",
                    fontSize: 26, fontWeight: 600, color: "#8E86B8", whiteSpace: "pre-line" }}>{footer}</div>
    </AbsoluteFill>
  );
};
