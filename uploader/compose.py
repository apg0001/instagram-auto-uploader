"""녹화·캡처본을 인스타그램 규격 미디어로 만든다.

- 릴스: 1080x1920 H.264/AAC MP4 (게임 화면을 가운데, 위아래에 제목·안내 문구)
- 캐러셀: 1080x1350(4:5) JPEG 여러 장
- 스토리: 1080x1920 JPEG
"""
from __future__ import annotations

import subprocess
from dataclasses import dataclass
from functools import cache
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

BG = (243, 235, 221)
INK = (40, 33, 45)
SUB = (110, 98, 112)
ACCENT = (255, 112, 67)
CARD = (255, 251, 244)

FONT_DIR = Path("/usr/share/fonts/opentype/noto")


@cache
def font(size: int, bold: bool = True) -> ImageFont.FreeTypeFont:
    path = FONT_DIR / ("NotoSansCJK-Bold.ttc" if bold else "NotoSansCJK-Regular.ttc")
    for i in range(12):  # .ttc 안에서 한국어(KR) 서체 번호를 찾는다
        try:
            f = ImageFont.truetype(str(path), size, index=i)
        except OSError:
            break
        if f.getname()[0] == "Noto Sans CJK KR":
            return f
    return ImageFont.truetype(str(path), size)


def wrap(draw: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont, width: int) -> list[str]:
    """띄어쓰기 단위로 줄바꿈하되, 한 단어가 너무 길면 글자 단위로 자른다."""
    lines: list[str] = []
    for para in text.split("\n"):
        cur = ""
        for word in para.split(" "):
            cand = f"{cur} {word}".strip()
            if draw.textlength(cand, font=f) <= width:
                cur = cand
                continue
            if cur:
                lines.append(cur)
            cur = ""
            for ch in word:
                if draw.textlength(cur + ch, font=f) > width:
                    lines.append(cur)
                    cur = ""
                cur += ch
        lines.append(cur)
    return lines


def text_block(draw: ImageDraw.ImageDraw, text: str, f: ImageFont.FreeTypeFont, y: int, fill, width: int,
               canvas_w: int, spacing: float = 1.3) -> int:
    """가운데 정렬로 쓰고 다음 y를 돌려준다."""
    for line in wrap(draw, text, f, width):
        w = draw.textlength(line, font=f)
        draw.text(((canvas_w - w) / 2, y), line, font=f, fill=fill)
        y += int(f.size * spacing)
    return y


def chip(draw: ImageDraw.ImageDraw, text: str, cx: int, y: int, size: int = 34) -> int:
    f = font(size)
    w = draw.textlength(text, font=f)
    pad_x, pad_y = 26, 12
    box = (cx - w / 2 - pad_x, y, cx + w / 2 + pad_x, y + size + pad_y * 2)
    draw.rounded_rectangle(box, radius=(size + pad_y * 2) // 2, fill=ACCENT)
    draw.text((cx - w / 2, y + pad_y - size * 0.12), text, font=f, fill="white")
    return int(box[3])


def framed(shot: Image.Image, width: int, radius: int = 28) -> Image.Image:
    """스크린샷을 둥근 모서리 + 그림자 카드로 만든다 (RGBA)."""
    h = round(shot.height * width / shot.width)
    img = shot.convert("RGB").resize((width, h), Image.LANCZOS)
    mask = Image.new("L", (width, h), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, width, h), radius=radius, fill=255)
    pad = 40
    out = Image.new("RGBA", (width + pad * 2, h + pad * 2), (0, 0, 0, 0))
    shadow = Image.new("L", out.size, 0)
    ImageDraw.Draw(shadow).rounded_rectangle((pad, pad + 12, pad + width, pad + h + 12), radius=radius, fill=90)
    shadow = shadow.filter(ImageFilter.GaussianBlur(18))
    out.paste((40, 30, 40, 255), (0, 0), shadow)
    out.paste(img, (pad, pad), mask)
    return out


# ---------------------------------------------------------------- 릴스

@dataclass
class ReelText:
    label: str        # 상단 칩 (예: "모바일 웹게임")
    title: str        # 큰 제목
    subtitle: str     # 한 줄 소개
    scene: str        # 영상 아래 장면 설명 (예: "3-10 리뷰어 2의 습격")
    cta: str          # 맨 아래 안내


VIDEO_Y = 640  # 1080x608 게임 화면이 놓이는 y


def reel_overlay(t: ReelText, out: Path, video_h: int = 608, colors: dict | None = None) -> Path:
    """video_h: 가운데 들어갈 영상 높이(폭 1080 기준). colors로 배경·글자색을 바꿀 수 있다."""
    c = {"bg": BG, "ink": INK, "sub": SUB, "card": CARD, **(colors or {})}
    W, H = 1080, 1920
    img = Image.new("RGB", (W, H), c["bg"])
    d = ImageDraw.Draw(img)
    y = chip(d, t.label, W // 2, 190)
    y = text_block(d, t.title, font(120), y + 36, c["ink"], 980, W, 1.32)
    text_block(d, t.subtitle, font(46, bold=False), y + 10, c["sub"], 960, W)
    # 영상 자리 (가장자리 그림자)
    d.rounded_rectangle((20, VIDEO_Y - 20, W - 20, VIDEO_Y + video_h + 20), radius=36, fill=c["card"])
    y = VIDEO_Y + video_h + 70
    y = text_block(d, t.scene, font(48), y, c["ink"], 960, W)
    # 하단 ~20%는 인스타 UI(캡션·버튼)에 가려지므로 안내 문구는 그 위에 둔다.
    text_block(d, t.cta, font(42, bold=False), y + 50, c["sub"], 960, W)
    img.save(out)
    return out


def run_ffmpeg(args: list[str]) -> None:
    proc = subprocess.run(["ffmpeg", "-hide_banner", "-loglevel", "error", "-y", *args], capture_output=True, text=True)
    if proc.returncode != 0:
        raise RuntimeError(f"ffmpeg 실패: {proc.stderr[-2000:]}")


def video_height(crop: tuple[int, int, int, int] | None) -> int:
    """폭 1080으로 맞췄을 때 영상 높이 (짝수)."""
    if not crop:
        return 608
    return int(round(1080 * crop[3] / crop[2] / 2) * 2)


def make_reel(raw_video: Path, start: float, seconds: float, overlay: Path, out: Path,
              crop: tuple[int, int, int, int] | None = None) -> Path:
    """원본 녹화에서 전투 구간을 잘라 세로 릴스로 만든다. 무음 오디오 트랙을 넣는다."""
    fade_out = max(0.0, seconds - 0.6)
    crop_filter = f"crop={crop[2]}:{crop[3]}:{crop[0]}:{crop[1]}," if crop else ""
    run_ffmpeg([
        "-ss", f"{start:.2f}", "-t", f"{seconds:.2f}", "-i", str(raw_video),
        "-loop", "1", "-i", str(overlay),
        "-f", "lavfi", "-i", "anullsrc=r=44100:cl=stereo",
        "-filter_complex",
        f"[0:v]fps=30,{crop_filter}scale=1080:{video_height(crop)}:flags=lanczos,setsar=1[g];"
        f"[1:v]fps=30,format=yuv420p[bg];"
        f"[bg][g]overlay=0:{VIDEO_Y}:shortest=1,fade=t=in:st=0:d=0.4,fade=t=out:st={fade_out:.2f}:d=0.6[v]",
        "-map", "[v]", "-map", "2:a", "-t", f"{seconds:.2f}",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p", "-profile:v", "high",
        "-r", "30", "-c:a", "aac", "-b:a", "128k", "-ar", "44100", "-movflags", "+faststart",
        str(out),
    ])
    return out


def video_cover(video: Path, at: float, out: Path) -> Path:
    run_ffmpeg(["-ss", f"{at:.2f}", "-i", str(video), "-frames:v", "1", "-q:v", "3", str(out)])
    return out


# ---------------------------------------------------------------- 이미지

def slide(shot: Path | None, heading: str, body: str, out: Path, footer: str, size=(1080, 1350)) -> Path:
    W, H = size
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    y = text_block(d, heading, font(70), 120, INK, 960, W, 1.2)
    if body:
        y = text_block(d, body, font(40, bold=False), y + 10, SUB, 940, W, 1.4)
    if shot:
        card = framed(Image.open(shot), 960)
        top = max(y + 10, (H - card.height) // 2 + 80)
        img.paste(card, ((W - card.width) // 2, min(top, H - card.height - 70)), card)
    f = font(32, bold=False)
    w = d.textlength(footer, font=f)
    d.text(((W - w) / 2, H - 80), footer, font=f, fill=SUB)
    img.save(out, "JPEG", quality=92)
    return out


def cta_slide(title: str, lines: list[str], url: str, out: Path, size=(1080, 1350)) -> Path:
    W, H = size
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    y = H // 2 - 260
    y = text_block(d, title, font(84), y, INK, 960, W, 1.2)
    for line in lines:
        y = text_block(d, line, font(44, bold=False), y + 16, SUB, 940, W)
    y = chip(d, "프로필 링크에서 바로 플레이", W // 2, y + 70, 40)
    text_block(d, url, font(34, bold=False), y + 30, SUB, 1000, W)
    img.save(out, "JPEG", quality=92)
    return out
