"""요청형 게시물.

- topic: 주제를 주면 claude -p가 웹 검색으로 조사해 카드뉴스 원고를 쓰고, 카드로 만든다.
  design="template" 이면 우리 템플릿, design="free" 면 공개 디자인 스킬
  (prompts/anthropic-skills: frontend-design, canvas-design · Apache-2.0)을 지침으로 Claude가 HTML을 직접 디자인한다.
- post: 이미 만든 이미지·영상과 캡션으로 바로 초안을 만든다 (대화로 요청받아 Claude Code가 준비한 자료 등).
"""
from __future__ import annotations

import json
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .caption import CaptionError, call_claude, parse_json
from .cards.render import FONTS, SIZE, render

log = logging.getLogger("uploader")
ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "prompts" / "anthropic-skills"

CONTENT_PROMPT = """너는 한국어 인스타그램 카드뉴스 에디터다. 아래 주제로 카드뉴스 원고를 만든다.

# 주제
{topic}

# 할 일
1. 웹 검색으로 최신·정확한 사실을 모은다. 출처가 확인된 내용만 쓴다. 불확실하면 빼거나 "확인되지 않음"이라고 쓴다.
2. 슬라이드 {n_min}~{n_max}장. 1장은 표지(한 줄 제목 + 부제), 마지막 장은 정리 또는 출처.
3. 한 장에 한 가지 메시지지만 내용은 꽉 채운다: points 장은 lead 한두 문장 + 항목 3~4개(머리말 + 구체적인 설명:
   숫자·예시·비교·"그래서 뭐가 달라지나"). 빈말·반복 금지. 한 항목 설명 80자 이내.
4. 캡션: 첫 줄 훅 → 핵심 3~5줄 → 출처 표기 → 해시태그 4~5개(관련성 높은 것만). 1500자 이내. 이모지 3개 이하.

# 출력 (JSON 하나만)
{{"title": "게시물 제목(관리용)",
  "slides": [
    {{"type": "cover", "title": "표지 제목", "subtitle": "부제 한 줄", "points": ["이 게시물에서 다룰 것 3~5개, 각 25자 이내"]}},
    {{"type": "points", "title": "슬라이드 제목", "lead": "한두 문장 또는 null", "items": [{{"head": "굵은 머리말", "body": "설명"}}]}},
    {{"type": "stat", "title": "슬라이드 제목", "value": "큰 숫자나 짧은 말", "label": "무엇의 값인지", "body": "설명"}}
  ],
  "sources": ["출처 언론사·기관명"],
  "caption": "..."}}"""

DESIGN_PROMPT = """아래 디자인 지침을 따르는 디자이너로서, 주어진 카드뉴스 원고를 인스타그램 캐러셀 슬라이드 HTML로 디자인하라.

# 디자인 지침 (공개 스킬, Apache-2.0)
<frontend-design>
{frontend}
</frontend-design>
<canvas-design>
{canvas}
</canvas-design>

# 제약
- 슬라이드마다 완결된 HTML 문서 하나. 크기는 정확히 {w}x{h}px (html, body 고정, overflow hidden). 스크롤·애니메이션 없음.
- 외부 네트워크 불가. 글꼴은 아래 @font-face만 쓴다 (Pretendard, 굵기 400~900):
{fontface}
- 이미지 파일 없음. 필요하면 CSS·인라인 SVG로 그린다.
- 원고의 사실·숫자·문장을 바꾸지 않는다 (줄바꿈·강조만 조정). 한국어는 word-break: keep-all.
- 모든 슬라이드가 같은 디자인 시스템(색·서체·여백)을 공유한다. 오른쪽 아래에 "n / 전체" 표시.
- 먼저 짧은 디자인 계획(색 4~6개, 서체 역할, 레이아웃)을 세우고, 주제에 맞지 않는 기본값이면 고친 뒤 코드를 쓴다.

# 원고
{content}

# 출력 (JSON 하나만, 설명 없이)
{{"plan": "디자인 계획 요약 3~5줄", "slides": ["<!doctype html>...", "..."]}}"""


def _fontface() -> str:
    weights = {400: "Regular", 500: "Medium", 600: "SemiBold", 700: "Bold", 800: "ExtraBold", 900: "Black"}
    return "\n".join(f'@font-face {{ font-family: "Pretendard"; font-weight: {w}; src: url("{FONTS.as_uri()}/Pretendard-{n}.woff2"); }}'
                     for w, n in weights.items())


def research(topic: str, cfg: dict[str, Any], n_min: int = 4, n_max: int = 8) -> dict[str, Any]:
    run = {**cfg, "command": list(cfg["command"]) + ["--allowedTools", "WebSearch", "WebFetch"],
           "timeout_seconds": max(int(cfg.get("timeout_seconds", 300)), 900)}
    data = parse_json(call_claude(CONTENT_PROMPT.format(topic=topic, n_min=n_min, n_max=n_max), run))
    if not data.get("slides") or not data.get("caption"):
        raise CaptionError("원고에 slides 또는 caption이 없음")
    return data


CIRCLED = "①②③④⑤⑥⑦⑧⑨⑩"


def split_teaser(text: str) -> tuple[str, list[str]]:
    """'① A ② B ③ C' 처럼 번호가 이어진 부제를 (앞말, [A, B, C])로 — 표지에서 한 줄씩 보이게."""
    import re

    parts = re.split(f"[{CIRCLED}]", text)
    if len(parts) < 3:
        return text, []
    return parts[0].strip(), [p.strip(" ,·") for p in parts[1:] if p.strip(" ,·")]


def template_slides(content: dict[str, Any], art: list[str | None] | None = None,
                    kicker: str | None = None) -> list[tuple[str, dict[str, Any]]]:
    # 출처 전체는 캡션에 있으니 하단에는 짧게 (예전엔 첫 출처가 모든 장에 잘려 찍혔다)
    foot = "출처는 캡션에" if content.get("sources") else ""
    c = {"brand": content.get("brand", ""), "report_name": "", "date_label": "", "footnote": foot}
    out = []
    for i, s in enumerate(content["slides"][:10]):
        t = s.get("type")
        a = art[i] if art and i < len(art) else None
        if t == "cover":
            sub, teaser = split_teaser(s.get("subtitle") or "")
            teaser = teaser or [str(x) for x in s.get("points") or []][:5]
            out.append(("story", {**c, "kind": "cover", "title": s.get("title", ""), "subtitle": sub,
                                  "teaser": teaser, "kicker": kicker, "art": a}))
        elif t == "stat":
            out.append(("story", {**c, "kind": "stat", "title": s.get("title", ""), "value": s.get("value", ""),
                                  "label": s.get("label"), "body": s.get("body"), "art": a}))
        else:
            out.append(("story", {**c, "kind": "points", "title": s.get("title", ""), "lead": s.get("lead"),
                                  "items": s.get("items") or [], "art": a}))
    return out


ART_PROMPT = """너는 인스타그램 카드뉴스 일러스트레이터다. 아래 카드뉴스의 각 장 아래쪽 빈 공간에 들어갈
플랫 벡터 일러스트를 장마다 하나씩 인라인 SVG로 그려라. 그 장의 내용에 맞는 구체적인 장면·사물이어야 한다
(예: 코딩 에이전트가 회사에 취직 → 사원증 단 로봇이 모니터 여러 대 앞에서 일함, 영상 속 속도 → 달리는 공과 속도계).

# 규칙
- 장마다 <svg viewBox="0 0 900 {h_cover}"> (표지) 또는 <svg viewBox="0 0 900 {h_body}"> (나머지) 하나.
- 플랫 스타일, 굵은 둥근 선, 큰 도형 위주. 장면 하나에 요소 4~10개. 너무 복잡하게 그리지 말 것.
- 색은 이 팔레트만: {palette}. 투명 배경(배경 사각형 금지).
- 글자(<text>) 금지, 실존 로고·인물 얼굴 금지.
- 쓸 수 있는 태그: svg g path rect circle ellipse line polyline polygon defs linearGradient radialGradient stop.
- 장 하나당 SVG 4,000자 이내.

# 카드뉴스 원고
{slides}

# 출력 (JSON 하나만)
{{"art": ["<svg ...>...</svg>", "..."]}}  (원고 장 수와 같은 개수, 순서대로)"""

_SVG_TAGS = {"svg", "g", "path", "rect", "circle", "ellipse", "line", "polyline", "polygon", "defs",
             "lineargradient", "radialgradient", "stop"}
_SVG_ATTRS = {"viewbox", "xmlns", "d", "x", "y", "x1", "y1", "x2", "y2", "cx", "cy", "r", "rx", "ry", "width", "height",
              "points", "fill", "stroke", "stroke-width", "stroke-linecap", "stroke-linejoin", "stroke-dasharray",
              "opacity", "fill-opacity", "stroke-opacity", "transform", "id", "offset", "stop-color", "stop-opacity",
              "gradientunits", "gradienttransform", "fill-rule", "preserveaspectratio"}


def clean_svg(svg: str) -> str | None:
    """모델이 쓴 SVG를 허용 목록으로 다시 만든다 (스크립트·외부 참조·이벤트 속성 제거). 못 읽으면 None."""
    import re
    import xml.etree.ElementTree as ET

    try:
        root = ET.fromstring(svg.strip())
    except ET.ParseError:
        return None

    def local(name: str) -> str:
        return name.rsplit("}", 1)[-1].lower()

    if local(root.tag) != "svg":
        return None

    def build(el: ET.Element) -> str:
        tag = local(el.tag)
        if tag not in _SVG_TAGS:
            return ""
        attrs = []
        for k, v in el.attrib.items():
            k = local(k)
            if k not in _SVG_ATTRS or "url(" in v and not re.fullmatch(r"url\(#[\w-]+\)", v.strip()):
                continue
            attrs.append(f'{k}="{v.replace(chr(34), "")}"')
        if tag == "svg":
            attrs = [a for a in attrs if not a.startswith(("width=", "height="))] + ['preserveAspectRatio="xMidYMid meet"']
        name = {"lineargradient": "linearGradient", "radialgradient": "radialGradient"}.get(tag, tag)
        name = name.replace("viewbox", "viewBox")
        attrs = [a.replace("viewbox=", "viewBox=").replace("gradientunits=", "gradientUnits=")
                 .replace("gradienttransform=", "gradientTransform=") for a in attrs]
        inner = "".join(build(c) for c in el)
        return f"<{name} {' '.join(attrs)}>{inner}</{name}>"

    out = build(root)
    return out if len(out) <= 12000 else None


def illustrate(content: dict[str, Any], cfg: dict[str, Any], theme: dict[str, str] | None) -> list[str | None]:
    """장마다 내용에 맞는 일러스트(SVG). 실패하면 빈 목록 — 일러스트 없이도 카드는 나온다."""
    t = theme or {}
    palette = ", ".join(f"{k} {v}" for k, v in t.items() if k in ("accent", "ink", "surface", "muted", "paper")) \
        or "accent #6D4AFF, ink #1E1A33, surface #FFFFFF, muted #686285"
    slides = json.dumps([{k: s.get(k) for k in ("type", "title", "subtitle", "lead", "items", "value", "label")}
                         for s in content["slides"][:10]], ensure_ascii=False)
    try:
        data = parse_json(call_claude(ART_PROMPT.format(h_cover=640, h_body=420, palette=palette, slides=slides),
                                      {**cfg, "timeout_seconds": max(int(cfg.get("timeout_seconds", 300)), 600)}))
    except Exception as e:  # noqa: BLE001
        log.warning("일러스트 생성 실패, 일러스트 없이 진행: %s", e)
        return []
    return [clean_svg(a) if isinstance(a, str) else None for a in data.get("art") or []]


def free_design(content: dict[str, Any], cfg: dict[str, Any], out_dir: Path) -> list[Path]:
    from PIL import Image
    from playwright.sync_api import sync_playwright

    prompt = DESIGN_PROMPT.format(
        frontend=(SKILLS / "frontend-design" / "SKILL.md").read_text(encoding="utf-8"),
        canvas=(SKILLS / "canvas-design" / "SKILL.md").read_text(encoding="utf-8"),
        w=SIZE[0], h=SIZE[1], fontface=_fontface(), content=json.dumps(content, ensure_ascii=False, indent=1))
    run = {**cfg, "timeout_seconds": max(int(cfg.get("timeout_seconds", 300)), 900)}
    data = parse_json(call_claude(prompt, run))
    htmls = [h for h in data.get("slides", []) if isinstance(h, str) and "<" in h][:10]
    if not htmls:
        raise CaptionError("디자인 결과에 슬라이드 HTML이 없음")
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "design-plan.txt").write_text(str(data.get("plan", "")), encoding="utf-8")
    paths = []
    with sync_playwright() as p, tempfile.TemporaryDirectory() as tmp:
        browser = p.chromium.launch()
        # 디자인 HTML은 모델이 쓴 것이므로 네트워크를 막고 렌더링만 한다
        ctx = browser.new_context(viewport={"width": SIZE[0], "height": SIZE[1]}, java_script_enabled=False)
        ctx.route("http*://**", lambda route: route.abort())
        page = ctx.new_page()
        for i, html in enumerate(htmls, 1):
            src = Path(tmp) / f"{i}.html"
            src.write_text(html, encoding="utf-8")
            page.goto(src.as_uri())
            page.wait_for_timeout(300)
            png = Path(tmp) / f"{i}.png"
            page.screenshot(path=str(png), clip={"x": 0, "y": 0, "width": SIZE[0], "height": SIZE[1]})
            out = out_dir / f"{i}.jpg"
            Image.open(png).convert("RGB").save(out, "JPEG", quality=93)
            paths.append(out)
        browser.close()
    return paths


def make_topic(topic: str, cfg: dict[str, Any], out_dir: Path, design: str = "template",
               theme: dict[str, str] | None = None, kicker: str | None = None) -> tuple[dict[str, Any], list[Path]]:
    content = research(topic, cfg)
    (out_dir.parent / "content.json").write_text(json.dumps(content, ensure_ascii=False, indent=1), encoding="utf-8")
    if design == "free":
        return content, free_design(content, cfg, out_dir)
    art = illustrate(content, cfg, theme)
    log.info("일러스트 %d/%d장", sum(1 for a in art if a), len(content["slides"]))
    (out_dir.parent / "art.json").write_text(json.dumps(art, ensure_ascii=False), encoding="utf-8")  # 다시 그릴 때 재사용
    return content, render(template_slides(content, art, kicker), out_dir, theme=theme)


def media_kind(files: list[Path]) -> tuple[str, list[str]]:
    types = ["video" if f.suffix.lower() in (".mp4", ".mov") else "image" for f in files]
    if len(files) == 1:
        return ("REELS" if types[0] == "video" else "IMAGE"), types
    return "CAROUSEL", types


def stage_files(files: list[Path], out_dir: Path) -> list[Path]:
    """입력 파일을 작업 폴더로 복사한다. 이미지는 인스타 규격(JPEG)으로 바꾼다."""
    from PIL import Image

    out_dir.mkdir(parents=True, exist_ok=True)
    staged = []
    for i, f in enumerate(files, 1):
        if f.suffix.lower() in (".jpg", ".jpeg"):
            dst = out_dir / f"{i}.jpg"
            shutil.copy2(f, dst)
        elif f.suffix.lower() in (".png", ".webp"):
            dst = out_dir / f"{i}.jpg"
            Image.open(f).convert("RGB").save(dst, "JPEG", quality=93)
        else:
            dst = out_dir / f"{i}{f.suffix.lower()}"
            shutil.copy2(f, dst)
        staged.append(dst)
    return staged
