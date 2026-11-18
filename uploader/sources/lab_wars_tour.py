"""랩워즈 기능 소개용 캡처 (v5 저장 데이터로 모든 콘텐츠를 열어 둔 쇼케이스 프로필).

화면 이동은 DOM 버튼(클래스 선택자)으로 한다. 게임에는 자동화용 URL 파라미터가 없으므로
모든 상태는 localStorage 저장 데이터로 만든다. 전투 시드는 Date.now() 라 녹화마다 다르다.
"""
from __future__ import annotations

import asyncio
import json
import random
import re
import time
from contextlib import asynccontextmanager
from pathlib import Path

from playwright.async_api import Page, async_playwright

from .lab_wars import _follow_front

VIEW = {"width": 1280, "height": 720}

BASIC = ["grunt", "keyboard", "coder", "shield", "chemist", "allnighter", "labchief", "drone", "gpu",
         "claudy", "codexy", "jaeminai"]
# 전투 초반부터 화면이 차도록 싼 유닛을 앞에, 장비(레전더리)를 뒤에 둔다. 1~9 단축키 순서.
SHOW_LOADOUT = ["grunt", "keyboard", "coder", "claudy", "labchief", "tesla", "rack", "accelerator", "tokamak"]


def scene_ids() -> list[str]:
    main = [f"{c}-{i}" for c in range(1, 8) for i in range(1, 11)]
    ids = [x for c in range(1, 8) for x in (f"ch{c}-intro", f"ch{c}-boss", *( [f"ch{c}-clear"] if c < 7 else []))]
    ids += ["ending"] + [f"st-{s}" for s in main if not s.endswith("-10")]
    ids += [x for n in range(1, 8) for x in (f"side{n}-intro", *(f"st-S{n}-{i}" for i in range(1, 5)),
                                              f"side{n}-boss", f"side{n}-clear")]
    return ids + ["guide:tutorial", "guide:squad", "guide:major"]


def showcase_save(unseen: tuple[str, ...] = ()) -> dict:
    """모든 챕터·외전 클리어, 전공 모드 진행 중, 장비·꾸미기 보유. unseen 의 장면은 다시 재생된다."""
    main = [f"{c}-{i}" for c in range(1, 8) for i in range(1, 11)]
    side = [f"S{s}-{i}" for s in range(1, 8) for i in range(1, 6)]
    units = {u: {"level": 15, "tier": 3} for u in BASIC}
    units.update({
        "accelerator": {"level": 20, "tier": 3}, "tokamak": {"level": 18, "tier": 3}, "cluster": {"level": 17, "tier": 3},
        "quantum": {"level": 10, "tier": 2}, "npu": {"level": 5, "tier": 1}, "tesla": {"level": 17, "tier": 3},
        "cooler": {"level": 8, "tier": 2}, "rack": {"level": 16, "tier": 3}, "printer3d": {"level": 5, "tier": 1},
        "emp": {"level": 10, "tier": 2}, "ups": {"level": 16, "tier": 3}, "breadboard": {"level": 12, "tier": 3},
        "iron": {"level": 9, "tier": 2}, "siggen": {"level": 5, "tier": 1},
    })
    return {
        "v": 5, "money": 999999, "stars": {s: 3 for s in main + side}, "units": units,
        "subs": ["claudy", "codexy", "jaeminai"],
        "facilities": {"remodel": 10, "coffee": 10, "tumbler": 8, "meditate": 8, "planner": 8, "printer": 10,
                       "printerRange": 12},
        "endless": 37, "seen": [s for s in scene_ids() if s not in unseen],
        "gear": {"tickets": 500, "spark": 95, "draws": 395, "owned": {
            "accelerator": 6, "tokamak": 4, "cluster": 3, "quantum": 2, "npu": 1, "cooler": 2, "printer3d": 1,
            "tesla": 3, "tower": 1, "robotarm": 1, "rack": 2, "reflow": 1, "emp": 1, "fiber": 1, "iron": 2,
            "scope": 1, "firewall": 1, "breadboard": 3, "multimeter": 1, "arduino": 1, "logic": 1, "ups": 2,
            "pi": 1, "siggen": 1, "router": 1}},
        "loadout": SHOW_LOADOUT,
        "presets": [SHOW_LOADOUT, BASIC[:9], []],
        "cosmetics": {
            "owned": ["crown", "halo", "catears", "headphones", "sunglasses", "velvet", "beanie", "c-holo", "c-gold",
                      "c-mint", "c-navy", "c-strawberry", "t-accept", "t-reviewer2", "t-minor"],
            "outfits": {"hero": {"hat": "crown", "color": "c-holo"}, "grunt": {"hat": "catears", "color": "c-mint"},
                        "coder": {"hat": "sunglasses", "color": "c-navy"}, "claudy": {"hat": "halo"},
                        "labchief": {"hat": "velvet", "color": "c-gold"}},
            "title": "t-accept", "avatar": "", "pity": 3, "draws": 120},
        "grades": {"ict": "A+", "progbasic": "A0", "dataprog": "B+", "adventure": "A+", "iceprog": "A0", "aiprog": "B0",
                   "webprog": "A+", "ds": "A+", "icemath": "B+", "linalg": "A0", "probstat": "C+", "arch": "A0",
                   "oop": "B+", "algo": "A+", "randproc": "B0", "sigsys": "A+", "embedded": "A0", "datacomm": "B+",
                   "ml": "A0", "commtheory": "F"},
        "updatedAt": int(time.time() * 1000),
    }


INIT = """
(() => {
  if (sessionStorage.getItem('ig-seeded')) return;
  sessionStorage.setItem('ig-seeded', '1');
  localStorage.setItem('mw:profiles', JSON.stringify([{id:'showcase', name:'대학원생', kind:'guest', createdAt: Date.now()}]));
  localStorage.setItem('mw:session', 'showcase');
  localStorage.setItem('mw:save:showcase', %s);
  localStorage.setItem('mw:patchSeen', '99.0.0');
  localStorage.setItem('mw:lang', 'ko');
  localStorage.setItem('mw:mapMode', 'story');
  localStorage.setItem('mw:settings', JSON.stringify({speed: %d}));
})();
"""


@asynccontextmanager
async def session(url: str, record_dir: Path | None = None, unseen: tuple[str, ...] = (), view: dict | None = None,
                  speed: int = 2):
    view = view or VIEW
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        kw = {"record_video_dir": str(record_dir), "record_video_size": view} if record_dir else {}
        ctx = await browser.new_context(viewport=view, device_scale_factor=1, locale="ko-KR",
                                        timezone_id="Asia/Seoul", **kw)
        await ctx.add_init_script(INIT % (json.dumps(json.dumps(showcase_save(unseen))), speed))
        page = await ctx.new_page()
        page.t0 = time.monotonic()  # type: ignore[attr-defined]
        await page.goto(url, wait_until="networkidle")
        await page.wait_for_timeout(1500)
        try:
            yield page
        finally:
            video = page.video
            await ctx.close()
            await browser.close()
            page.video_path = Path(await video.path()) if video else None  # type: ignore[attr-defined]


async def click(page: Page, selector: str, text: str | None = None, wait: int = 900) -> None:
    loc = page.locator(f"{selector}:visible")
    if text:
        loc = loc.filter(has_text=text)
    await loc.first.click()
    await page.wait_for_timeout(wait)


async def close_overlays(page: Page) -> None:
    for _ in range(5):
        btn = page.locator(".overlay:visible button").filter(has_text="닫기")
        if await btn.count():
            await btn.first.click()
            await page.wait_for_timeout(500)
        else:
            return


async def to_map(page: Page) -> None:
    for _ in range(20):
        if await page.locator("button.chapter-tab:visible").count():
            return
        b = page.locator("button:visible").filter(has_text="연구실로")
        if await b.count():
            await b.first.click()
        await page.wait_for_timeout(500)
    raise RuntimeError("지도 화면에 들어가지 못함")


async def skip_cutscenes(page: Page, shot: Path | None = None, lines: int = 2) -> Path | None:
    """컷신이 있으면 몇 줄 넘겨 한 장 찍고(shot) 건너뛴다."""
    taken = None
    if shot and await page.locator("button.cut-skip:visible").count():
        for _ in range(lines):
            await page.mouse.click(640, 300)
            await page.wait_for_timeout(1300)
        await page.screenshot(path=shot)
        taken = shot
    for _ in range(12):
        skip = page.locator("button.cut-skip:visible")
        if not await skip.count():
            break
        await skip.first.click()
        await page.wait_for_timeout(1000)
    return taken


async def battle(page: Page, seconds: float, shots: list[Path] | None = None, seed: int = 0,
                 keys: str = "123456789", skill_rate: float = 0.12) -> float:
    """전투를 seconds 동안 봇으로 진행한다. 전투 시작 시각(녹화 기준 초)을 돌려준다."""
    rng = random.Random(seed)
    cam: dict = {}
    start = time.monotonic() - page.t0  # type: ignore[attr-defined]
    end = time.monotonic() + seconds + 3
    shots = shots or []
    next_shot, taken = time.monotonic() + seconds * 0.3, 0
    while time.monotonic() < end:
        # 학회 경품 추첨 카드가 뜨면 고른다
        card = page.locator(".draft-card:visible")
        if await card.count():
            await page.wait_for_timeout(1700)
            await page.keyboard.press(rng.choice("123"))
            await page.wait_for_timeout(400)
        await page.keyboard.press(rng.choice(keys))
        if rng.random() < 0.25:
            await page.keyboard.press("q")
        if rng.random() < skill_rate:
            await page.keyboard.press(rng.choice("wert"))
            await page.mouse.click(rng.randint(820, 1150), rng.randint(400, 500))
        for _ in range(3):  # 미니맵 점으로 전선 따라가기
            await _follow_front(page, cam)
            await page.wait_for_timeout(180)
        if taken < len(shots) and time.monotonic() >= next_shot:
            await page.screenshot(path=shots[taken])
            taken += 1
            next_shot += seconds * 0.22
        if await page.locator(".panel.result:visible").count():
            break
    return start


async def leave_battle(page: Page) -> None:
    """전투 중이면 일시정지 → 후퇴, 결과 화면이면 지도로."""
    for _ in range(6):
        if await page.locator("button.chapter-tab:visible").count():
            return
        for text in ("지도로", "후퇴하기", "후퇴", "확인"):
            b = page.locator("button:visible").filter(has_text=text)
            if await b.count():
                await b.first.click()
                await page.wait_for_timeout(1200)
                break
        else:
            await page.keyboard.press("Escape")
            await page.wait_for_timeout(700)


async def open_node(page: Page, key: str) -> None:
    n = page.locator("button.stage-node:visible").filter(has=page.locator(".node-id", has_text=key))
    await n.first.click()
    await page.wait_for_timeout(800)


async def gacha_mix(page: Page) -> None:
    """뽑기 난수를 섞어 둔다: 대부분 평범하게, 가끔 레전더리 (같은 장비만 10개 나오지 않게)."""
    await page.evaluate("""(() => { let i = 0; const r = Math.random;
      Math.random = () => { i++; return (i % 9 === 1) ? 0.012 : r(); }; })()""")


async def chapter_tab(page: Page, name: str) -> None:
    await page.locator("button.chapter-tab:visible").filter(has_text=re.compile(f"^\\s*{name}\\s*$")).first.click()
    await page.wait_for_timeout(900)


async def overview(url: str, out: Path) -> dict[str, Path]:
    """전체 소개 캐러셀용 화면들: 타이틀·스토리 컷신·전투·지도·장비 뽑기·외전·전공·무한·옷장."""
    out.mkdir(parents=True, exist_ok=True)
    s: dict[str, Path] = {k: out / f"{k}.png" for k in
                          ("title", "story", "battle1", "battle2", "map", "gacha", "side", "major", "endless", "wardrobe")}
    async with session(url, unseen=("st-4-7",)) as page:
        await page.screenshot(path=s["title"])
        await to_map(page)
        await page.screenshot(path=s["endless"])          # 다 깬 상태라 무한 탭이 열려 있다
        await chapter_tab(page, "석사")
        await page.screenshot(path=s["map"])
        await open_node(page, "4-7")
        await click(page, "button.btn.primary", "출전", wait=2500)
        await skip_cutscenes(page, s["story"], lines=1)
        await battle(page, 30, [s["battle1"], s["battle2"]], seed=7)
        await leave_battle(page)
        await chapter_tab(page, "외전")
        await page.screenshot(path=s["side"])
        await click(page, ".mode-switch button.mode-btn", "전공", wait=1200)
        await page.screenshot(path=s["major"])
        await click(page, ".mode-switch button.mode-btn", "스토리", wait=1000)
        await click(page, "button.gacha-btn", wait=1200)
        await gacha_mix(page)
        await click(page, "button.submit-10", wait=5000)
        box = await page.locator(".ship-overlay .panel:visible, .ship-overlay > *:visible").first.bounding_box()
        clip = ({"x": box["x"] - 12, "y": box["y"] - 12, "width": box["width"] + 24, "height": box["height"] + 24}
                if box else None)
        await page.screenshot(path=s["gacha"], clip=clip)
        ok = page.locator(".overlay:visible .btn.primary").filter(has_text="확인")
        if await ok.count():
            await ok.first.click()
            await page.wait_for_timeout(800)
        await page.locator(".lab-tabs:visible button").nth(2).click()
        await page.wait_for_timeout(1000)
        await page.screenshot(path=s["wardrobe"])
    return {k: v for k, v in s.items() if v.exists()}


def run_overview(url: str, out: Path) -> dict[str, Path]:
    return asyncio.run(overview(url, out))
