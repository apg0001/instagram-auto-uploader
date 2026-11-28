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


class CardLayoutError(RuntimeError):
    """글자가 카드 밖으로 잘림 — 이대로 올리면 안 된다."""


# 찍기 직전 자동 검사 (모든 템플릿 공통):
#  1) data-decor 요소(그림 등 장식)가 실제 글자 줄과 겹치면 장식을 숨긴다
#  2) 글자 줄이 카드 밖으로 나가면 잘린 글자 목록을 돌려준다 → CardLayoutError
# 요소 상자가 아니라 글자 노드의 줄 상자(Range.getClientRects)로 잰다 (여백·패딩은 겹침이 아님).
LAYOUT_CHECK = """() => {
  const W = innerWidth, H = innerHeight, lines = [];
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
  for (let n = walker.nextNode(); n; n = walker.nextNode()) {
    if (!n.textContent.trim() || n.parentElement.closest('[data-decor], script, style')) continue;
    const cs = getComputedStyle(n.parentElement);
    if (cs.display === 'none' || cs.visibility === 'hidden' || +cs.opacity === 0) continue;
    const rg = document.createRange(); rg.selectNodeContents(n);
    for (const r of rg.getClientRects()) if (r.width > 0 && r.height > 0) lines.push({r, t: n.textContent.trim().slice(0, 30)});
  }
  const hit = (a, b) => a.left < b.right - 2 && b.left < a.right - 2 && a.top < b.bottom - 2 && b.top < a.bottom - 2;
  const hidden = [];
  for (const d of document.querySelectorAll('[data-decor]')) {
    if (getComputedStyle(d).display === 'none') continue;
    const r = d.getBoundingClientRect();
    const over = lines.filter(l => hit(r, l.r));
    if (over.length) { d.style.display = 'none'; hidden.push(over[0].t); }
  }
  const clipped = lines.filter(l => l.r.right > W + 1 || l.r.bottom > H + 1 || l.r.left < -1 || l.r.top < -1).map(l => l.t);
  return {hidden, clipped};
}"""


# 원고의 `명령어` 를 코드 모양으로 (백틱이 글자 그대로 찍히지 않게). 줄 맞춤(fit) 전에 돌린다.
CODE_JS = """() => {
  const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT), nodes = [];
  for (let n = walker.nextNode(); n; n = walker.nextNode())
    if (/`[^`]+`/.test(n.textContent) && !n.parentElement.closest('script, style, code')) nodes.push(n);
  for (const n of nodes) {
    const frag = document.createDocumentFragment();
    n.textContent.split(/(`[^`]+`)/).forEach(part => {
      if (/^`[^`]+`$/.test(part)) {
        const c = document.createElement('code');
        c.textContent = part.slice(1, -1);
        c.style.cssText = 'font-family:ui-monospace,SFMono-Regular,Menlo,Consolas,monospace;font-size:.88em;'
          + 'padding:.08em .35em;border-radius:.3em;background:rgba(127,127,127,.16);word-break:break-all;';
        frag.appendChild(c);
      } else if (part) frag.appendChild(document.createTextNode(part));
    });
    n.replaceWith(frag);
  }
  return nodes.length;
}"""


def check_layout(page: Any, name: str, i: int) -> dict[str, list[str]]:
    res = page.evaluate(LAYOUT_CHECK)
    if res["hidden"]:
        log.warning("카드 %d(%s): 그림이 글자('%s')를 가려 그림을 숨김", i, name, res["hidden"][0])
    if res["clipped"]:
        raise CardLayoutError(f"카드 {i}({name}): 글자가 카드 밖으로 잘림 — {res['clipped'][:3]}")
    return res


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
            page.evaluate(CODE_JS)
            # 웹폰트가 다 뜬 뒤 다시 맞춘다 (글꼴에 따라 높이가 달라짐)
            zoom = page.evaluate("document.fonts.ready.then(() => { window.fit && window.fit(); return document.body.dataset.zoom; })")
            if zoom and float(zoom) <= 0.71:
                log.warning("카드 %s: 내용이 많아 70%%까지 줄였는데도 넘칠 수 있음", name)
            check_layout(page, name, i)
            png = Path(tmp) / f"{i}.png"
            page.screenshot(path=str(png), clip={"x": 0, "y": 0, "width": SIZE[0], "height": SIZE[1]})
            out = out_dir / f"{prefix}{i}.jpg"
            Image.open(png).convert("RGB").save(out, "JPEG", quality=93)
            paths.append(out)
        browser.close()
    return paths


def regress(data_dir: Path, out_dir: Path, n: int = 8) -> list[tuple[str, str]]:
    """최근 게시물을 지금 템플릿으로 다시 찍어 본다 (템플릿을 고친 뒤 확인용, 올리지는 않음).
    다시 찍을 수 있는 것: 웹 조사 카드뉴스(content.json + art.json), 증시(snapshot.json + analysis.json).
    반환: [(게시물 id, 'OK n장' | 'LAYOUT …' | 'ERR …')]."""
    import json

    from .. import custom, themes
    from ..market import report

    posts = sorted((d for d in (data_dir / "posts").iterdir() if d.is_dir()), key=lambda d: d.name, reverse=True)
    out = []
    for d in posts:
        if len(out) >= n:
            break
        try:
            if (d / "content.json").exists() and (d / "art.json").exists():
                topic = "project" if "-project-" in d.name else "tech"
                c = json.loads((d / "content.json").read_text())
                a = json.loads((d / "art.json").read_text())
                paths = render(custom.template_slides(c, a, None), out_dir / d.name, theme=themes.css(topic))
            elif (d / "snapshot.json").exists() and (d / "analysis.json").exists():
                paths = render(report.slides(json.loads((d / "snapshot.json").read_text()),
                                             json.loads((d / "analysis.json").read_text())),
                               out_dir / d.name, theme=themes.css("market"))
            else:
                continue
            out.append((d.name, f"OK {len(paths)}장"))
        except CardLayoutError as e:
            out.append((d.name, f"LAYOUT {e}"))
        except Exception as e:  # noqa: BLE001
            out.append((d.name, f"ERR {type(e).__name__}: {str(e)[:100]}"))
    return out

