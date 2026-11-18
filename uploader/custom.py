"""요청형 게시물.

- topic: 주제를 주면 claude -p가 웹 검색으로 조사해 카드뉴스 원고를 쓰고, 카드로 만든다.
  design="template" 이면 우리 템플릿, design="free" 면 공개 디자인 스킬
  (prompts/anthropic-skills: frontend-design, canvas-design · Apache-2.0)을 지침으로 Claude가 HTML을 직접 디자인한다.
- post: 이미 만든 이미지·영상과 캡션으로 바로 초안을 만든다 (대화로 요청받아 Claude Code가 준비한 자료 등).
"""
from __future__ import annotations

import json
import shutil
import tempfile
from pathlib import Path
from typing import Any

from .caption import CaptionError, call_claude, parse_json
from .cards.render import FONTS, SIZE, render

ROOT = Path(__file__).resolve().parent.parent
SKILLS = ROOT / "prompts" / "anthropic-skills"

CONTENT_PROMPT = """너는 한국어 인스타그램 카드뉴스 에디터다. 아래 주제로 카드뉴스 원고를 만든다.

# 주제
{topic}

# 할 일
1. 웹 검색으로 최신·정확한 사실을 모은다. 출처가 확인된 내용만 쓴다. 불확실하면 빼거나 "확인되지 않음"이라고 쓴다.
2. 슬라이드 {n_min}~{n_max}장. 1장은 표지(한 줄 제목 + 부제), 마지막 장은 정리 또는 출처.
3. 한 장에 한 가지 메시지. 문장은 짧게, 한 항목 60자 이내.
4. 캡션: 첫 줄 훅 → 핵심 3~5줄 → 출처 표기 → 해시태그 4~5개(관련성 높은 것만). 1500자 이내. 이모지 3개 이하.

# 출력 (JSON 하나만)
{{"title": "게시물 제목(관리용)",
  "slides": [
    {{"type": "cover", "title": "표지 제목", "subtitle": "부제"}},
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


def template_slides(content: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    # 출처 전체는 캡션에 있으니 하단에는 짧게 (예전엔 첫 출처가 모든 장에 잘려 찍혔다)
    foot = "출처는 캡션에" if content.get("sources") else ""
    c = {"brand": content.get("brand", ""), "report_name": "", "date_label": "", "footnote": foot}
    out = []
    for s in content["slides"][:10]:
        t = s.get("type")
        if t == "cover":
            out.append(("notes", {**c, "title": s.get("title", ""), "lead": s.get("subtitle"), "items": []}))
        elif t == "stat":
            out.append(("notes", {**c, "title": s.get("title", ""), "items": [
                {"head": f"{s.get('value', '')} {s.get('label', '')}".strip(), "body": s.get("body")}]}))
        else:
            out.append(("notes", {**c, "title": s.get("title", ""), "lead": s.get("lead"), "items": s.get("items") or []}))
    return out


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
               theme: dict[str, str] | None = None) -> tuple[dict[str, Any], list[Path]]:
    content = research(topic, cfg)
    (out_dir.parent / "content.json").write_text(json.dumps(content, ensure_ascii=False, indent=1), encoding="utf-8")
    paths = free_design(content, cfg, out_dir) if design == "free" else render(template_slides(content), out_dir, theme=theme)
    return content, paths


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
