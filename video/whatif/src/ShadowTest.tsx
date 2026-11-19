// 시험: "그림자가 독립적으로 행동한다면?" — 로봇은 멈추는데 바닥 그림자는 혼자 걷고, 손을 흔든다.
import { ThreeCanvas } from "@remotion/three";
import React from "react";
import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from "remotion";
import { Robot, useRobot } from "./Character";

const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

const Caption: React.FC<{ text: string; from: number; to: number }> = ({ text, from, to }) => {
  const f = useCurrentFrame();
  const o = interpolate(f, [from, from + 10, to - 8, to], [0, 1, 1, 0], clamp);
  return (
    <div style={{ position: "absolute", left: 80, right: 80, top: 250, fontFamily: "Pretendard", fontSize: 72,
                  fontWeight: 900, color: "#fff", lineHeight: 1.2, whiteSpace: "pre-line", opacity: o,
                  textShadow: "0 4px 24px rgba(0,0,0,.6)" }}>{text}</div>
  );
};

export const ShadowTest: React.FC = () => {
  const f = useCurrentFrame();
  const { width, height, fps } = useVideoConfig();
  const gltf = useRobot();
  const t = f / fps;
  // 로봇: 0~3초 걸어 옴 → 멈춤(Idle) → 8초부터 뒤돌아봄(No)
  const walkZ = interpolate(t, [0, 3], [-3, 0.5], clamp);
  const me = t < 3 ? "Walking" : t < 8 ? "Idle" : "No";
  // 그림자: 4.2초까지 혼자 더 걷다가 멈추고, 5.5초에 혼자 손을 흔든다
  const shadowMove = t < 4.2 ? "Walking" : t < 5.5 ? "Idle" : "Wave";
  const shadowZ = interpolate(t, [0, 4.2], [-3, 0.6], clamp);
  const shadowX = interpolate(t, [2.4, 4.2], [0, 0.45], clamp); // 나에게서 떨어져 오른쪽으로
  const shadowT = shadowMove === "Wave" ? t - 5.5 : t; // 한 번 하는 동작은 시작 시점부터
  const meT = me === "No" ? t - 8 : t;
  const lampFlicker = t > 5.4 && t < 5.9 ? 0.35 + Math.abs(Math.sin(f * 2.3)) * 0.5 : 1;
  const camZ = interpolate(t, [0, 10], [12, 9.5], clamp);
  return (
    <AbsoluteFill style={{ background: "radial-gradient(circle at 50% 35%, #1E2240 0%, #07080F 75%)" }}>
      {gltf ? (
        <ThreeCanvas width={width} height={height} shadows camera={{ position: [0, 6, camZ], fov: 40 }}
                     onCreated={({ camera }) => camera.lookAt(0, 0.6, 0)}>
          <ambientLight intensity={0.22} />
          <spotLight position={[-2.2, 11, -3.2]} angle={0.6} penumbra={0.5} intensity={260 * lampFlicker} color="#FFE7B0"
                     castShadow shadow-mapSize={[2048, 2048]} shadow-bias={-0.0005} />
          <mesh rotation={[-Math.PI / 2, 0, 0]} receiveShadow>
            <planeGeometry args={[40, 40]} />
            <meshStandardMaterial color="#5A5662" roughness={0.95} />
          </mesh>
          {/* 가로등 (뒤 왼쪽) */}
          <mesh position={[-2.2, 5.5, -3.2]}>
            <cylinderGeometry args={[0.07, 0.09, 11, 12]} />
            <meshStandardMaterial color="#3A3D4A" />
          </mesh>
          <mesh position={[-2.2, 11.05, -3.2]}>
            <sphereGeometry args={[0.3, 16, 16]} />
            <meshBasicMaterial color={lampFlicker < 1 ? "#8A7A50" : "#FFF1C8"} />
          </mesh>
          {/* 보이는 나: 그림자를 만들지 않는다 */}
          <Robot gltf={gltf} move={me} t={meT} position={[0, 0, walkZ]} rotation={[0, t < 8 ? 0 : Math.PI * 0.15, 0]} scale={0.55} />
          {/* 안 보이는 그림자 주인: 따로 걷고 손을 흔든다 → 바닥에는 진짜 그림자만 */}
          <Robot gltf={gltf} move={shadowMove} t={shadowT} ghost position={[shadowX, 0, shadowZ]} rotation={[0, shadowMove === "Wave" ? -0.5 : 0, 0]} scale={0.55} />
        </ThreeCanvas>
      ) : null}
      <Caption text={"만약 네 그림자가\n따로 움직인다면?"} from={0} to={95} />
      <Caption text={"넌 멈췄는데\n그림자는 계속 걸어"} from={100} to={170} />
      <Caption text={"그리고…\n손을 흔들었어"} from={175} to={240} />
      <Caption text={"누구한테?"} from={245} to={300} />
      <div style={{ position: "absolute", left: 0, right: 0, top: 70, textAlign: "center", fontFamily: "Pretendard",
                    fontSize: 34, fontWeight: 800, color: "#8C6CFF", letterSpacing: 2 }}>만약에 · WHAT IF</div>
      <div style={{ position: "absolute", left: 0, right: 0, bottom: 120, textAlign: "center", fontFamily: "Pretendard",
                    fontSize: 30, fontWeight: 800, color: "#C9C2EE" }}>허구 · 상상 실험</div>
    </AbsoluteFill>
  );
};
