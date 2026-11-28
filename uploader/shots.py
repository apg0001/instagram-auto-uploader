"""'실제 화면' 슬라이드 — 글로만 설명하지 않고 공개 웹페이지(공식 문서·깃허브 레포·릴리스 노트)를
직접 열어 찍은 화면을 카드 사이에 끼운다. 원고(JSON)의 "shots": [{url, head, caption, after}] 를 쓴다.

로그인 없이 열리는 https 공개 페이지만 (사설 IP·localhost 금지). 못 찍으면 그 장만 건너뛴다.
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from . import themes

log = logging.getLogger("uploader")

W, H = 1080, 1350
MAX_SHOTS = 2
FONTS = ["/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc", "/usr/share/fonts/truetype/noto/NotoSansCJK-Bold.ttc"]


def public_url(url: str) -> bool:
    """https 이고 공인 주소로만 풀리는 호스트인가."""
    u = urlparse(url or "")
    if u.scheme != "https" or not u.hostname:
        return False
    try:
        addrs = {a[4][0] for a in socket.getaddrinfo(u.hostname, 443)}
    except OSError:
        return False
    return bool(addrs) and all(ipaddress.ip_address(a).is_global for a in addrs)


BLOCK_WORDS = ("just a moment", "verify you are human", "checking your browser", "attention required", "access denied",
               "enable javascript", "are you a robot", "captcha", "사람인지 확인", "보안 확인", "403 forbidden", "404 not found",
               "page not found", "페이지를 찾을 수 없")


def blocked(title: str, text: str) -> str | None:
    """봇 차단·오류 페이지면 이유. 본문 글이 너무 적어도 (빈 화면·로딩만) 버린다."""
    low = f"{title}\n{text[:3000]}".lower()
    for w in BLOCK_WORDS:
        if w in low:
            return f"차단·오류 화면 '{w}'"
    if len(" ".join(text.split())) < 200:
        return "본문 글이 거의 없음"
    return None


def blank(png: Path) -> str | None:
    """찍힌 화면이 거의 단색이면 (흰 화면·로딩 중) 이유."""
    from PIL import Image, ImageStat

    st = ImageStat.Stat(Image.open(png).convert("L").resize((270, 400)))
    return "거의 빈 화면" if st.stddev[0] < 12 else None


def capture(urls: list[str], out_dir: Path) -> list[Path | None]:
    """페이지 윗부분을 휴대폰 폭(540px, 2배율)으로 찍는다. 실패한 것은 None."""
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    res: list[Path | None] = []
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 540, "height": 820}, device_scale_factor=2, locale="ko-KR",
                                  color_scheme="light")
        for i, url in enumerate(urls):
            if not public_url(url):
                log.warning("실제 화면 건너뜀 (공개 https 아님): %s", url)
                res.append(None)
                continue
            page = ctx.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=30000)
                try:
                    page.wait_for_load_state("networkidle", timeout=8000)
                except Exception:  # noqa: BLE001 — 광고·분석 요청이 끝없이 이어지는 페이지
                    pass
                page.wait_for_timeout(1500)  # 쿠키 배너는 조금 늦게 뜬다
                page.evaluate("""() => {  // 떠 있는 쿠키·동의·앱 설치 배너 숨기기
                  const bad = /cookie|쿠키|consent|동의/i;
                  for (const el of document.querySelectorAll('body *')) {
                    const s = getComputedStyle(el);
                    if (s.position !== 'fixed' && s.position !== 'sticky') continue;
                    if (el.getBoundingClientRect().top > innerHeight * 0.4 || bad.test(el.innerText || ''))
                      el.style.display = 'none';
                  }
                }""")
                why = blocked(page.title(), page.evaluate("() => (document.body && document.body.innerText) || ''"))
                f = out_dir / f"page{i}.png"
                page.screenshot(path=str(f))
                why = why or blank(f)
                if why:
                    log.warning("실제 화면 버림 (%s): %s", why, url)
                    res.append(None)
                else:
                    res.append(f)
            except Exception as e:  # noqa: BLE001
                log.warning("실제 화면 실패 %s: %s", url, e)
                res.append(None)
            finally:
                page.close()
        browser.close()
    return res


def _font(size: int):
    from PIL import ImageFont

    for f in FONTS:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def _wrap(draw, text: str, font, width: int, lines: int) -> list[str]:
    out, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) > width:
            out.append(cur)
            cur = ch.lstrip()
            if len(out) == lines:
                break
        else:
            cur += ch
    if len(out) < lines and cur:
        out.append(cur)
    return out


def slide(images: list[Path], head: str, caption: str, topic: str, out: Path, source: str = "") -> Path:
    """주제 색 배경에 화면(1~2장)을 둥근 모서리·그림자로 얹은 1080x1350 카드."""
    from PIL import Image, ImageDraw, ImageFilter

    t = themes.get(topic)
    im = Image.new("RGB", (W, H), t["bg"])
    d = ImageDraw.Draw(im)
    d.ellipse((W - 400, -260, W + 220, 360), fill=tuple(int(a * .9 + b * .1) for a, b in zip(t["bg"], t["accent"], strict=True)))
    f_k, f_h, f_s = _font(26), _font(60), _font(32)
    d.rounded_rectangle((80, 80, 80 + d.textlength("실제 화면", font=f_k) + 40, 128), 24, fill=t["accent"])
    d.text((100, 84), "실제 화면", font=f_k, fill=t["bg"])
    y = 160
    for line in _wrap(d, head, f_h, W - 160, 2):
        d.text((80, y), line, font=f_h, fill=t["ink"])
        y += 76
    for line in _wrap(d, caption, f_s, W - 160, 2):
        d.text((80, y + 6), line, font=f_s, fill=t["sub"])
        y += 46
    top, bottom = y + 40, H - (110 if source else 70)
    shots = [Image.open(f).convert("RGB") for f in images]
    gap = 36
    avail_w = (W - 160 - gap * (len(shots) - 1)) // len(shots)

    def fit(s):  # 칸 폭에 맞추고, 너무 길면 윗부분만
        r = min(1.0, avail_w / s.width)
        s = s.resize((int(s.width * r), int(s.height * r)), Image.LANCZOS)
        return s.crop((0, 0, s.width, min(s.height, bottom - top)))

    shots = [fit(s) for s in shots]
    x = (W - (sum(s.width for s in shots) + gap * (len(shots) - 1))) // 2
    for s in shots:
        mask = Image.new("L", s.size, 0)
        ImageDraw.Draw(mask).rounded_rectangle((0, 0, *s.size), 26, fill=255)
        shadow = Image.new("L", (W, H), 0)
        ImageDraw.Draw(shadow).rounded_rectangle((x + 6, top + 14, x + s.width + 6, top + s.height + 14), 26, fill=120)
        im.paste((0, 0, 0), (0, 0), shadow.filter(ImageFilter.GaussianBlur(18)))
        im.paste(s, (x, top), mask)
        x += s.width + gap
    if source:
        d.text((80, H - 80), source[:70], font=_font(24), fill=t["sub"])
    im.save(out, quality=93)
    return out


def insert(content: dict[str, Any], media: list[Path], out_dir: Path, topic: str) -> list[Path]:
    """원고의 shots 를 찍어 해당 장 뒤에 끼운다 (캐러셀 최대 10장)."""
    specs = [s for s in content.get("shots") or [] if isinstance(s, dict) and s.get("url")][:MAX_SHOTS]
    room = 10 - len(media)
    if not specs or room <= 0:
        return media
    specs = specs[:room]
    pics = capture([s["url"] for s in specs], out_dir / "pages")
    made = []
    for i, (s, pic) in enumerate(zip(specs, pics, strict=True)):
        if not pic:
            continue
        host = urlparse(s["url"]).hostname or ""
        f = slide([pic], str(s.get("head") or "직접 열어 본 화면").replace("`", ""), str(s.get("caption") or "").replace("`", ""), topic,
                  out_dir / f"shot{i}.jpg", source=host + urlparse(s["url"]).path[:50])
        try:
            after = int(s.get("after") or len(media))
        except (TypeError, ValueError):
            after = len(media)
        made.append((max(1, min(after, len(media))), f))
    out = list(media)
    for after, f in sorted(made, key=lambda x: -x[0]):  # 뒤에서부터 끼워야 번호가 안 밀린다
        out.insert(after, f)
    log.info("실제 화면 %d장", len(made))
    return out
