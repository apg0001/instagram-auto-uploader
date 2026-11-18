"""HTML(Jinja2) 카드를 Playwright로 1080x1350 이미지로 찍는다.

디자인 원칙은 prompts/anthropic-skills/frontend-design/SKILL.md (Apache-2.0)를 따른다.
토큰: 국내 시세 관습(상승 빨강 ▲ / 하락 파랑 ▼), Pretendard 한 가족, 고정폭 숫자,
과감함은 표지 한 곳(그날 시장 방향 색)에만.
"""
from __future__ import annotations

import logging
import tempfile
from pathlib import Path
from typing import Any

from jinja2 import Environment, FileSystemLoader, select_autoescape
from PIL import Image

log = logging.getLogger("uploader")

ROOT = Path(__file__).resolve().parents[2]
TEMPLATES = Path(__file__).parent / "templates"
FONTS = ROOT / "assets" / "fonts"
SIZE = (1080, 1350)


def pct(v: float | None, digits: int = 2) -> str:
    if v is None:
        return "-"
    arrow = "▲" if v > 0 else "▼" if v < 0 else ""
    return f"{arrow}{abs(v):.{digits}f}%"


def tone(v: float | None) -> str:
    if v is None or abs(v) < 1e-9:
        return "flat"
    return "up" if v > 0 else "down"


def num(v: float | None, digits: int = 2) -> str:
    if v is None:
        return "-"
    return f"{v:,.{digits}f}" if digits else f"{v:,.0f}"


def price(v: float | None) -> str:
    if v is None:
        return "-"
    return f"{v:,.0f}" if v >= 1000 else f"{v:,.2f}"


def eok(v: float | None) -> str:
    """억원 → '1,114조 8,853억' 같은 표기."""
    if not v:
        return "-"
    jo, rest = divmod(int(round(v)), 10000)
    return f"{jo:,}조 {rest:,}억" if jo else f"{rest:,}억"


def sparkline(values: list[float], w: int = 900, h: int = 180, pad: int = 6) -> dict[str, Any]:
    """SVG polyline 좌표와 기준선(첫 값) y."""
    if len(values) < 2:
        return {"points": "", "base_y": h / 2, "w": w, "h": h, "last": (w, h / 2)}
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1
    pts = [(pad + i * (w - 2 * pad) / (len(values) - 1), pad + (hi - v) * (h - 2 * pad) / span) for i, v in enumerate(values)]
    base = pad + (hi - values[0]) * (h - 2 * pad) / span
    return {"points": " ".join(f"{x:.1f},{y:.1f}" for x, y in pts), "base_y": base, "w": w, "h": h, "last": pts[-1],
            "area": f"{pad},{h} " + " ".join(f"{x:.1f},{y:.1f}" for x, y in pts) + f" {w - pad},{h}"}


def env() -> Environment:
    e = Environment(loader=FileSystemLoader(TEMPLATES), autoescape=select_autoescape(["html", "j2"]))
    e.filters.update(pct=pct, tone=tone, num=num, price=price, eok=eok)
    e.globals.update(fonts=FONTS.as_uri(), sparkline=sparkline)
    return e


def render(slides: list[tuple[str, dict[str, Any]]], out_dir: Path, prefix: str = "",
           theme: dict[str, str] | None = None) -> list[Path]:
    """slides: [(템플릿 이름, 컨텍스트)]. JPEG 경로 목록을 돌려준다 (인스타 이미지는 JPEG만).
    theme: 주제 색 CSS 변수(themes.css). 슬라이드 컨텍스트에 theme 이 있으면 그쪽이 우선."""
    from playwright.sync_api import sync_playwright

    out_dir.mkdir(parents=True, exist_ok=True)
    e = env()
    paths: list[Path] = []
    with sync_playwright() as p, tempfile.TemporaryDirectory() as tmp:
        browser = p.chromium.launch()
        page = browser.new_page(viewport={"width": SIZE[0], "height": SIZE[1]}, device_scale_factor=1)
        for i, (name, ctx) in enumerate(slides, 1):
            html = e.get_template(f"{name}.html.j2").render(page_no=i, page_total=len(slides), **{"theme": theme, **ctx})
            src = Path(tmp) / f"{i}.html"
            src.write_text(html, encoding="utf-8")
            page.goto(src.as_uri())
            # 웹폰트가 다 뜬 뒤 다시 맞춘다 (글꼴에 따라 높이가 달라짐)
            zoom = page.evaluate("document.fonts.ready.then(() => { window.fit && window.fit(); return document.body.dataset.zoom; })")
            if zoom and float(zoom) <= 0.71:
                log.warning("카드 %s: 내용이 많아 70%%까지 줄였는데도 넘칠 수 있음", name)
            png = Path(tmp) / f"{i}.png"
            page.screenshot(path=str(png), clip={"x": 0, "y": 0, "width": SIZE[0], "height": SIZE[1]})
            out = out_dir / f"{prefix}{i}.jpg"
            Image.open(png).convert("RGB").save(out, "JPEG", quality=93)
            paths.append(out)
        browser.close()
    return paths
