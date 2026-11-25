"""카드뉴스 → 릴스. 이미 만든 캐러셀 카드(1080x1350)를 세로 화면(1080x1920)에 한 장씩 띄우고,
Claude가 장마다 쓴 내레이션을 타입캐스트로 읽는다. BGM·조립은 깃허브 릴스(github_reel)와 같다.

자막은 넣지 않는다 (카드 글씨가 곧 화면 내용). 길이는 40~70초를 목표로 한다.
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image, ImageDraw

from . import themes
from .caption import call_claude, parse_json
from .compose import band, font, text_block
from .github_reel import ANIM, H, Scene, W, _ease, assemble, validate_caption, voice

log = logging.getLogger("uploader")

SCRIPT_PROMPT = """너는 한국어 테크 인스타그램 릴스 내레이터다. 아래 카드뉴스를 카드 순서대로 넘기며 읽어 줄 내레이션을 쓴다.

# 카드뉴스 원고
{content}

# 카드 순서 (영상에 이 순서로 한 장씩 나온다)
{cards}

# 규칙
- 장마다 say 하나: 해요체, 1~2문장, 50자 안팎(표지는 30자 이내 훅). 화면 글씨를 그대로 읽지 말고 핵심만 말로 풀어서.
- 숫자·영어 이름은 소리 내 읽기 쉽게 (예: "GPT 6.2", "3배"). 원고에 없는 사실·숫자는 만들지 않는다.
- 표지 say: 스크롤을 멈추게 하는 한마디 (질문이나 놀라운 사실).
- outro_say: 마무리 한 문장 (저장·팔로우 유도는 가볍게).
- caption: 릴스 캡션. 첫 줄 검색 키워드가 들어간 훅 → 핵심 3~4줄 → 출처 한 줄 → 해시태그 딱 5개(검색 키워드는 본문 문장에). 900자 이내, 이모지 3개 이하.

# 출력 (JSON 하나만)
{{"say": ["1번 카드 내레이션", "2번 ..."], "outro_say": "...", "caption": "..."}}"""


@dataclass
class Result:
    reel: Path
    cover: Path
    caption: str


def card_lines(content: dict[str, Any], n_cards: int) -> list[str]:
    """영상에 나올 카드 설명 (shots '실제 화면' 장이 끼어 있으면 그 자리에 표시)."""
    slides = list(content.get("slides") or [])
    shots = sorted(content.get("shots") or [], key=lambda s: int(s.get("after", 99)))
    out: list[str] = []
    for i, s in enumerate(slides, 1):
        out.append(f"{s.get('type', 'points')}: {s.get('title', '')}")
        out += [f"실제 화면: {sh.get('head', '')} — {sh.get('caption', '')}" for sh in shots if int(sh.get("after", 0)) == i]
    return (out + ["(카드)"] * n_cards)[:n_cards]


def write_script(content: dict[str, Any], n_cards: int, cfg: dict[str, Any]) -> dict[str, Any]:
    cards = "\n".join(f"{i}. {c}" for i, c in enumerate(card_lines(content, n_cards), 1))
    body = {k: content.get(k) for k in ("title", "slides", "sources")}
    prompt = SCRIPT_PROMPT.format(content=json.dumps(body, ensure_ascii=False)[:12000], cards=cards)
    last: list[str] = []
    for _ in range(2):
        data = parse_json(call_claude(prompt, cfg))
        say = [str(x).strip() for x in data.get("say") or []]
        last = validate_caption(str(data.get("caption", "")))
        if len(say) < n_cards:
            last.append(f"say가 {len(say)}개 (카드 {n_cards}장)")
        if not last:
            return {**data, "say": say[:n_cards]}
        prompt += "\n\n# 직전 결과의 문제 (고쳐서 다시)\n- " + "\n- ".join(last)
    raise ValueError("릴스 대본 검증 실패: " + ", ".join(last))


def frame(card: Image.Image, t: dict, label: str, p: float, out: Path) -> Path:
    """세로 화면 한 장: 위 주제 띠·라벨, 가운데 카드(살짝 떠오르며 등장)."""
    img = Image.new("RGB", (W, H), t["bg"])
    d = ImageDraw.Draw(img)
    band(d, W, t["accent"])
    text_block(d, label, font(40), 150, t["sub"], 960, W)
    e = _ease(p)
    w = int(W * (0.94 + 0.04 * e))
    c = card.resize((w, int(card.height * w / card.width)), Image.LANCZOS)
    y = (H - c.height) // 2 + 60 + int((1 - e) * 80)
    if e < 1:
        c = Image.blend(Image.new("RGB", c.size, t["bg"]), c, 0.25 + 0.75 * e)
    img.paste(c, ((W - c.width) // 2, y))
    img.save(out, "JPEG", quality=90)
    return out


def build(content: dict[str, Any], cards: list[Path], out_dir: Path, cfg: dict[str, Any], label: str,
          topic: str = "tech", seed: int = 0) -> Result:
    out_dir.mkdir(parents=True, exist_ok=True)
    work = out_dir / "work"
    work.mkdir(exist_ok=True)
    t = themes.get(topic)
    script = write_script(content, len(cards), cfg)
    scenes: list[Scene] = []
    for i, (card, say) in enumerate(zip(cards, script["say"], strict=False)):
        im = Image.open(card).convert("RGB")
        frames = [frame(im, t, label, min(1.0, k / (ANIM - 1)), work / f"c{i}_{k:02d}.jpg") for k in range(ANIM)]
        scenes.append(Scene(frames, say))
    last = scenes[-1].frames[-1]
    scenes.append(Scene([last], script.get("outro_say") or "도움이 됐다면 저장해 두세요."))
    voice(scenes, work, "ko-KR-SunHiNeural", "+12%")
    reel = assemble(scenes, out_dir / "reel.mp4", work, seed)
    cover = out_dir / "cover.jpg"
    Image.open(scenes[0].frames[-1]).save(cover, "JPEG", quality=92)
    return Result(reel, cover, str(script["caption"]).strip())
