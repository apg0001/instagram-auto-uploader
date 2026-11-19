import { Composition } from "remotion";
import { WhatIf, WhatIfProps, calcWhatIf } from "./WhatIf";

const sample: WhatIfProps = {
  scenes: [
    { key: "hook", visual: "spin", item: "cup", frames: 75, head: "너 오늘도\n커피 샀지?" },
    { key: "pile", visual: "pile", item: "cup", frames: 90, head: "10년이면", big: "1,917만 원" },
    { key: "growth", visual: "growth", frames: 150, head: "엔비디아에 넣었다면" },
    { key: "crash", visual: "crash", frames: 90, head: "2022년", big: "-63%" },
    { key: "end", visual: "end", item: "cup", frames: 75, head: "다음 만약에는?" },
  ],
  bars: Array.from({ length: 12 }, (_, i) => ({ year: String(2015 + i), log: i * 0.3, label: "₩", down: i === 7 })),
  footer: "주가: Yahoo Finance · 환율: FRED",
  accent: "#8C6CFF",
};

export const RemotionRoot: React.FC = () => (
  <Composition
    id="WhatIf"
    component={WhatIf}
    durationInFrames={900}
    fps={30}
    width={1080}
    height={1920}
    defaultProps={sample}
    calculateMetadata={calcWhatIf}
  />
);
