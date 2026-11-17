"""Agent Office 데모 화면(로그인 없이 보이는 합성 데이터)을 녹화·캡처한다.

로그인하지 않은 새 브라우저로 열기 때문에 실제 세션·PC 정보는 찍히지 않는다.
화면에 '합성 데이터(데모)' 배지가 함께 찍혀 데모임이 드러난다.
"""
from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from pathlib import Path

from playwright.async_api import async_playwright

VIEW = {"width": 1280, "height": 720}


@dataclass
class Capture:
    video: Path | None = None
    start: float = 0.0
    crop: tuple[int, int, int, int] = (0, 0, 1280, 720)   # 영상에서 잘라 쓸 영역 (x, y, w, h)
    office_shot: Path | None = None       # 사무실 전체
    zoom_shot: Path | None = None         # 캐릭터 확대 (머리 위 상태)
    list_shot: Path | None = None         # 활성 세션 목록
    shots: list[Path] = field(default_factory=list)


async def capture(url: str, out_dir: Path, seconds: float) -> Capture:
    out_dir.mkdir(parents=True, exist_ok=True)
    cap = Capture()
    async with async_playwright() as p:
        browser = await p.chromium.launch()
        ctx = await browser.new_context(viewport=VIEW, record_video_dir=str(out_dir / "raw"), record_video_size=VIEW,
                                        locale="ko-KR")
        page = await ctx.new_page()
        t0 = time.monotonic()
        await page.goto(url, wait_until="networkidle")
        await page.wait_for_timeout(2500)
        body = await page.evaluate("document.body.innerText")
        if "합성 데이터" not in body:
            raise RuntimeError("데모(합성 데이터) 화면이 아님 — 로그인 상태이거나 주소가 다름. 실제 데이터는 찍지 않는다.")
        box = await page.evaluate("(() => { const r = document.querySelector('canvas').getBoundingClientRect();"
                                  " return [r.x, r.y, r.width, r.height]; })()")
        x, y, w, h = (round(v) for v in box)
        cap.office_shot = out_dir / "office.png"
        await page.screenshot(path=cap.office_shot, clip={"x": x, "y": y - 40, "width": w, "height": h + 40})
        cap.list_shot = out_dir / "list.png"
        await page.screenshot(path=cap.list_shot, clip={"x": x, "y": y, "width": 290, "height": 340})

        # 자동 초점 + 확대 → 캐릭터와 머리 위 상태가 잘 보이게
        await page.mouse.move(x + w / 2, y + h / 2)
        for k in ["f", "+", "+"]:
            await page.keyboard.press(k)
            await page.wait_for_timeout(700)
        cap.start = time.monotonic() - t0
        cap.crop = (x, y - 40, w - (w % 2), h + 40 - ((h + 40) % 2))
        end = time.monotonic() + seconds + 2
        shot_at = time.monotonic() + seconds * 0.5
        while time.monotonic() < end:
            await page.wait_for_timeout(500)
            if cap.zoom_shot is None and time.monotonic() >= shot_at:
                cap.zoom_shot = out_dir / "zoom.png"
                await page.screenshot(path=cap.zoom_shot, clip={"x": x + 300, "y": y, "width": w - 300, "height": h})
        video = page.video
        await ctx.close()
        await browser.close()
        if video:
            cap.video = Path(await video.path())
    return cap


def run_capture(url: str, out_dir: Path, seconds: float) -> Capture:
    return asyncio.run(capture(url, out_dir, seconds))
