"""랩워즈를 헤드리스 브라우저로 플레이하며 녹화·캡처한다.

- 쇼케이스용 저장 데이터(전 스테이지 개방, 유닛 전부 박사 진화)를 localStorage에 미리 넣는다.
- 고른 스테이지에 출전 → 스토리 컷신 한 장 캡처 후 건너뛰기 → 간단한 봇이 유닛·스킬을 쓴다.
- 카메라는 미니맵의 아군/적 점 위치를 읽어 전선을 따라간다.
"""
from __future__ import annotations

import asyncio
import json
import random
import time
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import Page, async_playwright

CHAPTERS = ["학부생", "학연생", "학석사", "석사", "석박", "박사", "포닥"]
UNIT_IDS = ["grunt", "keyboard", "coder", "shield", "chemist", "allnighter", "labchief", "drone", "gpu",
            "claudy", "codexy", "jaeminai"]
UNIT_KEYS = list("1234567890-=")
SKILL_KEYS = list("wert")
VIEW = {"width": 1280, "height": 720}


def showcase_save() -> dict:
    stars = {f"{c}-{i}": 3 for c in range(1, 8) for i in range(1, 11)}
    return {
        "v": 4,
        "money": 50000,
        "stars": stars,
        "units": {u: {"level": 15, "tier": 3} for u in UNIT_IDS},
        "subs": ["claudy", "codexy", "jaeminai"],
        "facilities": {k: 99 for k in ["remodel", "coffee", "tumbler", "meditate", "planner", "printer", "printerRange"]},
        "endless": 0,
        "seen": [],
        "updatedAt": int(time.time() * 1000),
    }


INIT_SCRIPT = """
(() => {
  if (sessionStorage.getItem('ig-seeded')) return;
  sessionStorage.setItem('ig-seeded', '1');
  const prof = [{id: 'showcase', name: '대학원생', kind: 'guest', createdAt: Date.now()}];
  localStorage.setItem('mw:profiles', JSON.stringify(prof));
  localStorage.setItem('mw:session', 'showcase');
  localStorage.setItem('mw:save:showcase', %s);
})();
"""


@dataclass
class Capture:
    stage: str
    stage_title: str = ""
    video: Path | None = None          # 전투 구간만 잘라낼 원본 녹화(webm)
    battle_start: float = 0.0          # 녹화 시작 기준 전투 시작 시각(초)
    story_shot: Path | None = None
    map_shot: Path | None = None
    battle_shots: list[Path] = field(default_factory=list)


async def _visible_buttons(page: Page) -> list[str]:
    return await page.evaluate(
        "[...document.querySelectorAll('button')].filter(b => b.offsetParent).map(b => b.innerText.trim())"
    )


async def _enter_stage(page: Page, stage: str) -> str:
    """지도에서 스테이지를 골라 출전 직전까지 간다. 스테이지 이름을 돌려준다."""
    # 타이틀: 저장된 세션이 있으면 바로 지도로 가거나 '이어하기'류 버튼이 나온다.
    for _ in range(20):
        buttons = await _visible_buttons(page)
        if any("연구실 강화" in b for b in buttons):
            break
        target = next((b for b in buttons if "연구실로" in b or "이어하기" in b), None)
        if target:
            await page.get_by_role("button", name=target).first.click()
        await page.wait_for_timeout(500)
    else:
        raise RuntimeError("스테이지 지도 화면에 들어가지 못함")
    chapter = int(stage.split("-")[0])
    click_js = """([kind, key]) => {
      const bs = [...document.querySelectorAll('button')].filter(b => b.offsetParent);
      const b = kind === 'chapter'
        ? bs.find(b => b.innerText.trim() === key)
        : bs.find(b => b.innerText.split('\\n').map(s => s.trim()).includes(key));
      if (!b) return null;
      b.click();
      return b.innerText.split('\\n').map(s => s.trim()).filter(Boolean);
    }"""
    if not await page.evaluate(click_js, ["chapter", CHAPTERS[chapter - 1]]):
        raise RuntimeError(f"챕터 탭을 찾지 못함: {CHAPTERS[chapter - 1]}")
    await page.wait_for_timeout(600)
    lines = await page.evaluate(click_js, ["stage", stage])
    if not lines:
        raise RuntimeError(f"스테이지 버튼을 찾지 못함: {stage}")
    await page.wait_for_timeout(800)
    # 버튼 글자: [아이콘?, '3-10', '리뷰어 2의 습격', '★★★']
    after = lines[lines.index(stage) + 1:] if stage in lines else []
    return next((x for x in after if not set(x) <= set("★☆")), "")


async def _follow_front(page: Page, state: dict) -> None:
    """미니맵 점으로 전선 위치를 구해 카메라를 부드럽게 옮긴다."""
    info = await page.evaluate(
        """() => {
          const pos = (sel) => [...document.querySelectorAll(sel)].map(e => parseFloat(e.style.left));
          const r = document.querySelector('.minimap')?.getBoundingClientRect();
          const v = document.querySelector('.mini-view');
          return {ally: pos('.mini-dots i.ally'), enemy: pos('.mini-dots i.enemy'),
                  boss: pos('.mini-dots i.boss'),
                  rect: r ? [r.left, r.top, r.width, r.height] : null,
                  view: v ? parseFloat(v.style.width) : 40};
        }"""
    )
    if not info["rect"]:
        return
    ally, enemy = info["ally"], info["enemy"]
    if ally and enemy:
        target = (max(ally) + min(enemy)) / 2
    elif ally:
        target = max(ally)
    elif enemy:
        target = min(enemy)
    else:
        return
    if info["boss"]:
        target = (target + min(info["boss"])) / 2
    cur = state.get("cam", target)
    cur += (target - cur) * 0.35          # 한 번에 확 넘어가지 않게
    state["cam"] = cur
    left, top, width, height = info["rect"]
    await page.evaluate(
        """([x, y]) => {
          const m = document.querySelector('.minimap');
          m.dispatchEvent(new PointerEvent('pointerdown', {clientX: x, clientY: y, bubbles: true}));
        }""",
        [left + width * cur / 100, top + height / 2],
    )


async def _bot_step(page: Page, rng: random.Random) -> None:
    keys = UNIT_KEYS[:]
    rng.shuffle(keys)
    for k in keys[:4]:
        await page.keyboard.press(k)
    await page.keyboard.press("q")
    if rng.random() < 0.15:
        k = rng.choice(SKILL_KEYS)
        await page.keyboard.press(k)
        # 폭격류는 지점 지정이 필요하다. 화면 오른쪽(적 쪽)을 찍는다.
        await page.mouse.click(rng.randint(820, 1150), rng.randint(430, 520))


async def capture(stage: str, out_dir: Path, seconds: float, url: str, seed: int | None = None) -> Capture:
    out_dir.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    cap = Capture(stage=stage)
    async with async_playwright() as p:
        browser = await p.chromium.launch(args=["--autoplay-policy=no-user-gesture-required"])
        ctx = await browser.new_context(
            viewport=VIEW, device_scale_factor=1, record_video_dir=str(out_dir / "raw"), record_video_size=VIEW,
            locale="ko-KR", timezone_id="Asia/Seoul",
        )
        await ctx.add_init_script(INIT_SCRIPT % json.dumps(json.dumps(showcase_save())))
        page = await ctx.new_page()
        t0 = time.monotonic()
        await page.goto(url, wait_until="networkidle")
        await page.wait_for_timeout(1500)
        cap.stage_title = await _enter_stage(page, stage)
        cap.map_shot = out_dir / "map.png"
        await page.screenshot(path=cap.map_shot)
        await page.locator("button:visible:has-text('출전')").first.click()
        await page.wait_for_timeout(2500)

        # 스토리 컷신: 대사 몇 줄 넘긴 뒤 한 장 찍고 건너뛴다.
        if await page.locator("button.cut-skip:visible").count():
            for _ in range(2):
                await page.mouse.click(640, 300)
                await page.wait_for_timeout(1300)
            cap.story_shot = out_dir / "story.png"
            await page.screenshot(path=cap.story_shot)
        for _ in range(10):
            skip = page.locator("button.cut-skip:visible")
            if not await skip.count():
                break
            await skip.first.click()
            await page.wait_for_timeout(1200)

        # 전투
        cap.battle_start = time.monotonic() - t0
        state: dict = {}
        end = time.monotonic() + seconds + 4
        next_shot = time.monotonic() + seconds * 0.35
        while time.monotonic() < end:
            await _bot_step(page, rng)
            for _ in range(3):
                await _follow_front(page, state)
                await page.wait_for_timeout(180)
            if time.monotonic() >= next_shot and len(cap.battle_shots) < 3:
                shot = out_dir / f"battle{len(cap.battle_shots) + 1}.png"
                await page.screenshot(path=shot)
                cap.battle_shots.append(shot)
                next_shot += seconds * 0.25
            if any(t.startswith(("승리", "패배", "다시")) for t in await _visible_buttons(page)):
                break
        video = page.video
        await ctx.close()
        await browser.close()
        if video:
            cap.video = Path(await video.path())
    return cap


def run_capture(stage: str, out_dir: Path, seconds: float, url: str, seed: int | None = None) -> Capture:
    return asyncio.run(capture(stage, out_dir, seconds, url, seed))
