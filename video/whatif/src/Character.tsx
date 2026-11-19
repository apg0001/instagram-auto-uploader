// 캐릭터 애니메이션 — CC0 로봇(RobotExpressive, 14개 동작)을 불러와 프레임마다 동작·시간을 정한다.
// remotion 규칙: useFrame 대신 useCurrentFrame → mixer 시간을 직접 맞춘다 (렌더할 때마다 같은 결과).
import React, { useEffect, useMemo, useState } from "react";
import * as THREE from "three";
import { GLTF, GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import * as SkeletonUtils from "three/examples/jsm/utils/SkeletonUtils.js";
import { continueRender, delayRender, staticFile } from "remotion";

export type Move =
  | "Idle" | "Walking" | "Running" | "Wave" | "No" | "Yes" | "Death" | "Sitting" | "Standing"
  | "Jump" | "Dance" | "Punch" | "ThumbsUp" | "WalkJump";

let cache: Promise<GLTF> | null = null;
function loadRobot(): Promise<GLTF> {
  cache ??= new GLTFLoader().loadAsync(staticFile("models/RobotExpressive.glb"));
  return cache;
}

export function useRobot(): GLTF | null {
  const [gltf, setGltf] = useState<GLTF | null>(null);
  const [handle] = useState(() => delayRender("로봇 모델 불러오기"));
  useEffect(() => {
    loadRobot().then((g) => {
      setGltf(g);
      continueRender(handle);
    });
  }, [handle]);
  return gltf;
}

/** 로봇 한 개. move 동작을 t초 지점으로. silhouette 면 새까만 그림자 재질. */
export const Robot: React.FC<{
  gltf: GLTF;
  move: Move;
  t: number;
  position?: [number, number, number];
  rotation?: [number, number, number];
  scale?: number | [number, number, number];
  silhouette?: boolean;
  ghost?: boolean; // 안 보이고 그림자만 드리운다 (그림자가 따로 움직이는 연출)
  castShadow?: boolean;
  color?: string;
}> = ({ gltf, move, t, position = [0, 0, 0], rotation = [0, 0, 0], scale = 1, silhouette, ghost, castShadow, color }) => {
  const { scene, mixer } = useMemo(() => {
    const s = SkeletonUtils.clone(gltf.scene) as THREE.Object3D;
    s.traverse((o) => {
      const m = o as THREE.Mesh;
      if (m.isMesh) {
        m.castShadow = Boolean(castShadow || ghost);
        if (ghost) {
          m.material = new THREE.MeshBasicMaterial({ colorWrite: false, depthWrite: false });
        } else if (silhouette) {
          m.material = new THREE.MeshBasicMaterial({ color: "#05040A", transparent: true, opacity: 0.88 });
        } else if (color && (m.material as THREE.MeshStandardMaterial).name === "Main") {
          m.material = (m.material as THREE.MeshStandardMaterial).clone();
          (m.material as THREE.MeshStandardMaterial).color = new THREE.Color(color);
        }
      }
    });
    return { scene: s, mixer: new THREE.AnimationMixer(s) };
  }, [gltf, silhouette, ghost, castShadow, color]);
  const clip = gltf.animations.find((a) => a.name === move) ?? gltf.animations[0];
  mixer.stopAllAction();
  const action = mixer.clipAction(clip);
  const once = ["Death", "Sitting", "Standing", "Jump", "No", "Yes", "Wave", "Punch", "ThumbsUp"].includes(move);
  action.setLoop(once ? THREE.LoopOnce : THREE.LoopRepeat, Infinity);
  action.clampWhenFinished = true;
  action.play();
  action.time = once ? Math.min(t, clip.duration - 0.001) : t % clip.duration;
  mixer.update(0);
  return <primitive object={scene} position={position} rotation={rotation} scale={scale} />;
};
