"""생성 → 노션 초안 → 승인 → 게시 → 정리 흐름을 묶는다."""
from __future__ import annotations

import json
import logging
import os
import shutil
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from . import caption as captions
from . import compose, themes
from .config import Config
from .db import APPROVED, DB, DRAFT, FAILED, PUBLISHED, REJECTED, Post
from .instagram import Instagram
from .make_webhook import MakeWebhook
from .media_host import MediaHost
from .notion import Notion
from .sources import lab_wars, lab_wars_tour

log = logging.getLogger("uploader")

KIND_ROTATION = ["REELS", "CAROUSEL"]
FOOTER = "랩워즈 · Lab Wars"
GAME = themes.get("game")
MARKET_REPORTS = ("premarket", "close", "weekly", "monthly")


class Pipeline:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.db = DB(cfg.data_dir / "state.db")
        self.host = MediaHost(cfg.media_repo, cfg.media["pages_base"], cfg.media.get("pages_wait_seconds", 600))
        self.notion = Notion(os.environ["NOTION_TOKEN"], cfg.raw["notion"]["api_version"]) if cfg.notion_enabled else None
        self.tz = ZoneInfo(cfg.raw.get("timezone", "Asia/Seoul"))

    # ------------------------------------------------------------ 생성
    def _new_id(self, source: str, kind: str) -> tuple[str, Path]:
        pid = f"{datetime.now(self.tz):%Y%m%d-%H%M%S}-{source}-{kind.lower()}"
        work = self.cfg.data_dir / "posts" / pid
        (work / "out").mkdir(parents=True, exist_ok=True)
        return pid, work

    def _finalize(self, pid: str, source: str, kind: str, title: str, text: str, media: list[Path], types: list[str],
                  cover: Path | None = None, meta: dict | None = None, scheduled_at: float | None = None) -> Post:
        """미디어 업로드 → 초안 저장 → 노션 등록."""
        uploads = media + ([cover] if cover else [])
        urls = self.host.upload(pid, uploads)
        post = Post(
            id=pid, source=source, kind=kind, status=DRAFT, title=title, caption=text,
            media=[{"file": str(f), "url": u, "type": t} for f, u, t in zip(media, urls, types, strict=False)],
            meta={**(meta or {}), "cover_url": urls[-1] if cover else None},
        )
        post.scheduled_at = scheduled_at
        if self.auto_approved(source):
            post.status = APPROVED  # 노션에는 '승인'으로 올라가고 다음 sync에서 게시 (노션에서 반려로 바꾸면 취소)
        self.db.save(post)  # 노션이 실패해도 초안은 남긴다 (sync가 다시 올림)
        self.push_to_notion([post])
        log.info("%s 생성 완료 %s", "자동 승인" if post.status == APPROVED else "초안", pid)
        return post

    def auto_approved(self, source: str) -> bool:
        """config auto_approve 의 접두어로 시작하는 소스는 사람 승인 없이 게시한다. '*' 이면 전부."""
        prefixes = self.cfg.raw.get("auto_approve") or []
        return "*" in prefixes or any(source == a or source.startswith((a + ":", a + "-")) for a in prefixes)

    def generate(self, source: str = "lab-wars", kind: str | None = None, stage: str | None = None,
                 at: str | None = None) -> Post | None:
        if source.startswith("market"):
            report = source.split(":", 1)[1] if ":" in source else "close"
            return self.generate_market(report)
        when = parse_when(at, self.tz)
        if source == "agent-office":
            return self.generate_repo(source, kind, when)
        if source.startswith("lab-wars:"):
            return self.generate_tour(source.split(":", 1)[1], when)
        if source == "lab-wars":
            return self.generate_game(source, kind, stage, when)
        if source == "github-trending":
            return self.generate_github(when)
        raise ValueError(f"모르는 소스: {source} (lab-wars | agent-office | github-trending | market:premarket|close|weekly|monthly)")

    def generate_github(self, scheduled_at: float | None = None) -> Post:
        """주간 깃허브 인기 레포 Top 5: 릴스(릴스 탭, TTS + 합성 BGM) + 캐러셀(피드) 두 개를 만든다. 릴스 초안을 돌려준다.
        예약이 있으면 캐러셀은 그 시각, 릴스는 reel_offset_hours(기본 8) 뒤 — 같은 시각에 두 개가 몰리지 않게."""
        from . import github_reel
        from .sources import github_trending as gt

        src = self.cfg.raw.get("github_trending") or {}
        day = datetime.now(self.tz).date()
        pid, work = self._new_id("tech-github", "reels")
        log.info("생성 시작 %s", pid)
        repos = gt.fetch()
        snaps = self.cfg.data_dir / "github-trending"
        prev = gt.previous(snaps, day)
        gt.save(repos, snaps, day)
        cands = repos[:9]
        readmes = {r.name: gt.readme(r.name) for r in cands}
        metas = {r.name: gt.details(r.name) for r in cands}
        res = github_reel.build(repos, prev, readmes, metas, day, work / "out", self.cfg.caption,
                                voice_name=src.get("voice", "ko-KR-SunHiNeural"), rate=src.get("rate", "+15%"))
        meta = {"check": "none", "slides": res.desc, "topic_theme": "tech"}
        reel_at = scheduled_at + float(src.get("reel_offset_hours", 8)) * 3600 if scheduled_at else None
        reel = self._finalize(pid, "tech-github", "REELS", f"[깃허브 Top 5 · 릴스] {day:%m/%d}", res.reel_caption,
                              [res.reel], ["video"], res.cover, meta, reel_at)
        cid = pid.replace("-reels", "-carousel")
        (self.cfg.data_dir / "posts" / cid).mkdir(parents=True, exist_ok=True)
        self._finalize(cid, "tech-github", "CAROUSEL", f"[깃허브 Top 5 · 캐러셀] {day:%m/%d}", res.carousel_caption,
                       res.carousel, ["image"] * len(res.carousel), None, meta, scheduled_at)
        return reel

    def generate_game(self, source: str, kind: str | None, stage: str | None,
                      scheduled_at: float | None = None) -> Post:
        src = self.cfg.source(source)
        n = self.db.count_source(source)
        kind = kind or KIND_ROTATION[n % len(KIND_ROTATION)]
        stages = src["showcase_stages"]
        stage = stage or stages[n % len(stages)]
        pid, work = self._new_id(source, kind)
        out = work / "out"
        log.info("생성 시작 %s (스테이지 %s)", pid, stage)

        seconds = float(src.get("reel_seconds", 28))
        cap = lab_wars.run_capture(stage, work / "capture", seconds, src["play_url"], seed=n)
        scene = f"{stage} {cap.stage_title}".strip()

        media: list[Path] = []
        types: list[str] = []
        slides_desc: list[str] = []
        cover: Path | None = None
        if kind == "REELS":
            if not cap.video:
                raise RuntimeError("녹화 파일이 없음")
            ov = compose.reel_overlay(compose.ReelText(
                label="모바일 웹게임", title="랩워즈", subtitle=src["tagline"], scene=scene,
                cta="설치 없이 브라우저에서 바로 · 프로필 링크"), out / "overlay.png", theme=GAME)
            body = compose.make_reel(cap.video, cap.battle_start + 0.5, seconds, ov, out / "body.mp4")
            cover = compose.video_cover(body, min(seconds / 2, 12), out / "cover.jpg")
            reel = compose.prepend_cover(body, cover, out / "reel.mp4")
            media, types = [reel], ["video"]
            slides_desc = [f"{scene} 전투 플레이 영상 {int(seconds)}초"]
        elif kind == "CAROUSEL":
            shots = cap.battle_shots
            plan = [
                (shots[0] if shots else None, src["tagline"], "대학원생 라인 디펜스, 랩워즈"),
                (cap.story_shot, "스테이지마다 스토리 컷신", "학부생에서 포닥까지, 대학원생의 생존기"),
                (shots[1] if len(shots) > 1 else None, scene, "카페인을 모아 동료를 내보내고 잡무 군단을 밀어내라"),
                (cap.map_shot, "7챕터 70스테이지", "학부생 · 학연생 · 학석사 · 석사 · 석박 · 박사 · 포닥"),
            ]
            for i, (shot, head, body) in enumerate([p for p in plan if p[0]]):
                media.append(compose.slide(shot, head, body, out / f"{i + 1}.jpg", FOOTER, theme=GAME))
                slides_desc.append(f"{head} — {body}")
            media.append(compose.cta_slide("지금 바로 플레이", ["폰 가로 · 세로 모두 지원", "설치 없이 브라우저에서"],
                                           src["play_url"].removeprefix("https://").rstrip("/"),
                                           out / f"{len(media) + 1}.jpg", theme=GAME))
            slides_desc.append("마지막: 플레이 링크 안내")
            types = ["image"] * len(media)
        elif kind == "STORIES":
            shot = cap.battle_shots[0] if cap.battle_shots else cap.story_shot
            media = [compose.slide(shot, src["tagline"], f"{scene}\n프로필 링크에서 바로 플레이",
                                   out / "story.jpg", FOOTER, size=(1080, 1920), theme=GAME)]
            types = ["image"]
        else:
            raise ValueError(f"모르는 형식: {kind}")

        text = ""
        if kind != "STORIES":  # 스토리는 캡션이 없다
            text = captions.generate(src, self.cfg.caption, kind, scene, slides_desc, self.db.recent_captions(source))
        post = self._finalize(pid, source, kind, f"[{kind}] {scene}", text, media, types, cover,
                              {"stage": stage, "check": "link"}, scheduled_at)
        shutil.rmtree(work / "capture" / "raw", ignore_errors=True)  # 원본 녹화는 크니 지운다
        return post

    def generate_tour(self, feature: str, scheduled_at: float | None = None) -> Post:
        """랩워즈 기능 소개 시리즈 (overview 등). scheduled_at 이 있으면 그 시각 이후에 게시."""
        src = self.cfg.source("lab-wars")
        if feature == "side":
            return self.tour_side(src, scheduled_at)
        if feature in ("gear", "endless"):
            return self.tour_clips(src, feature, scheduled_at)
        if feature == "major":
            return self.tour_major(src, scheduled_at)
        if feature != "overview":
            raise ValueError(f"모르는 랩워즈 소개: {feature} (overview | side | gear | major | endless)")
        pid, work = self._new_id("lab-wars", "carousel")
        out = work / "out"
        log.info("생성 시작 %s (소개: %s)", pid, feature)
        s = lab_wars_tour.run_overview(src["play_url"], work / "capture")
        plan = [
            ("title", src["tagline"], "대학원생 라인 디펜스, 랩워즈. 설치 없이 브라우저에서 바로"),
            ("battle2", "카페인 모아 동료 출동", "학부생부터 박사까지, 진화하는 연구실 동료 12종"),
            ("story", "스테이지마다 스토리 컷신", "학부생에서 포닥까지 7챕터 70스테이지"),
            ("gacha", "장비 지원 뽑기", "입자 가속기 · 양자컴퓨터 · 토카막… ICT 장비 28종"),
            ("side", "외전 7편", "엔딩 뒤 이야기. 정전 · 마감 24시 · AI 복제, 편마다 특수 규칙"),
            ("major", "전공 모드", "41과목 이수체계도. 자료구조는 스택, 신호와시스템은 샘플링이 전투 규칙"),
            ("endless", "무한 모드 · 순위표", "끝없이 강해지는 적과 버그 군단"),
            ("wardrobe", "꾸미기 옷장", "논문 투고 심사 결과로 모자 · 옷 색 · 칭호"),
        ]
        media: list[Path] = []
        slides_desc: list[str] = []
        for key, head, body in plan:
            if key not in s:
                continue
            media.append(compose.slide(s[key], head, body, out / f"{len(media) + 1}.jpg", FOOTER, theme=GAME))
            slides_desc.append(f"{head} — {body}")
        media.append(compose.cta_slide("지금 바로 플레이", ["폰 가로 · 세로 모두 지원", "설치 없이 브라우저에서"],
                                       src["play_url"].removeprefix("https://").rstrip("/"),
                                       out / f"{len(media) + 1}.jpg", theme=GAME))
        slides_desc.append("마지막: 플레이 링크 안내")
        scene = "랩워즈 전체 소개 (업데이트 총정리: 장비 뽑기 · 외전 7편 · 전공 모드 · 무한 모드 · 꾸미기)"
        text = captions.generate(src, self.cfg.caption, "CAROUSEL", scene, slides_desc,
                                 self.db.recent_captions("lab-wars"))
        post = self._finalize(pid, "lab-wars", "CAROUSEL", "[소개] 랩워즈 전체 소개", text, media,
                              ["image"] * len(media), None, {"tour": feature, "check": "link"},
                              scheduled_at=scheduled_at)
        return post

    def tour_side(self, src: dict, scheduled_at: float | None) -> Post:
        """소개 시리즈: 외전 7편 릴스. 편마다 이름·특수 규칙을 바꿔 가며 전투 장면을 이어 붙이고 BGM을 깐다."""
        from . import bgm

        pid, work = self._new_id("lab-wars", "reels")
        out = work / "out"
        log.info("생성 시작 %s (소개: 외전)", pid)
        video, segs = lab_wars_tour.run_side(src["play_url"], work / "capture")
        if not video:
            raise RuntimeError("녹화 파일이 없음")
        clip, lead = 6.0, 7.0  # 전투 시작 7초 뒤(유닛이 깔린 뒤)부터 6초씩
        parts: list[Path] = []
        for i, sg in enumerate(segs):
            k = sg["node"][1:].split("-")[0]
            ov = compose.reel_overlay(compose.ReelText(
                label="외전 7편 공개", title="랩워즈 외전", subtitle="엔딩 뒤 이야기, 편마다 다른 특수 규칙",
                scene=f"외전 {k} · {sg['name']}\n{sg['rule']}", cta="설치 없이 브라우저에서 바로 · 프로필 링크"),
                out / f"overlay{i}.png", theme=GAME)
            parts.append(compose.make_reel(video, sg["start"] + lead, clip, ov, work / f"part{i}.mp4"))
        body = compose.concat(parts, work / "body_silent.mp4")
        total = clip * len(parts)
        music = bgm.write(total, int(datetime.now(self.tz).strftime("%Y%m%d")) + 7, work / "bgm.wav")
        body = compose.replace_audio(body, music, out / "body.mp4", volume=0.8)
        cover = compose.video_cover(body, clip * 2 + clip / 2, out / "cover.jpg")  # 마감 24시(타이머) 장면
        reel = compose.prepend_cover(body, cover, out / "reel.mp4")
        slides_desc = [f"외전 {sg['node'][1:].split('-')[0]} {sg['name']} — {sg['rule']} (전투 {int(clip)}초)" for sg in segs]
        scene = "랩워즈 외전 7편 소개 (엔딩 뒤 이야기, 편마다 특수 규칙. 영상에는 그중 5편)"
        text = captions.generate(src, self.cfg.caption, "REELS", scene, slides_desc, self.db.recent_captions("lab-wars"))
        post = self._finalize(pid, "lab-wars", "REELS", "[소개] 랩워즈 외전 7편", text, [reel], ["video"], cover,
                              {"tour": "side", "check": "link"}, scheduled_at=scheduled_at)
        shutil.rmtree(work / "capture" / "raw", ignore_errors=True)
        return post

    # 소개 시리즈 릴스: (녹화 구간 key, 시작에서 건너뛸 초, 최대 길이, 화면 문구)
    TOUR_CLIPS = {
        "gear": {
            "label": "장비 지원 뽑기", "title": "랩워즈 장비 뽑기", "subtitle": "입자 가속기 · 토카막 · 양자컴퓨터, ICT 장비 28종",
            "cover": "spark", "run": lab_wars_tour.run_gear,
            "clips": [("draw10", 0.6, 4.4, "10회 뽑기\n카드가 한 장씩 열린다"),
                      ("spark", 0.0, 5.0, "천장 100회\n레전더리를 직접 고른다"),
                      ("battle", 5.0, 9.0, "뽑은 장비로 출전\n입자 가속기 · 토카막 · 테슬라 코일")],
            "scene": "랩워즈 장비 뽑기 소개 (10회 뽑기 공개, 천장 100회 레전더리 직접 선택, 장비로 전투)",
        },
        "endless": {
            "label": "무한 모드 · 꾸미기", "title": "랩워즈 무한 모드", "subtitle": "끝나지 않는 잡무, 그리고 옷장",
            "cover": "endless", "run": lab_wars_tour.run_endless,
            "clips": [("endless_map", 0.0, 2.4, "무한 모드\n교수가 되어도 일은 끝나지 않는다"),
                      ("endless", 5.0, 8.0, "판마다 강해지는 적\n5판마다 보스 러시"),
                      ("review", 0.3, 3.0, "논문 투고 → 심사 결과\n억셉트면 레어 아이템"),
                      ("closet", 0.0, 3.6, "옷장\n모자 · 옷 색 · 칭호로 꾸미기")],
            "scene": "랩워즈 무한 모드와 꾸미기 소개 (무한 38판 전투, 논문 투고 심사 결과로 아이템, 옷장)",
        },
    }

    def tour_clips(self, src: dict, feature: str, scheduled_at: float | None) -> Post:
        """소개 시리즈 릴스: 녹화 구간을 이어 붙이고 구간마다 문구를 바꾼 뒤 BGM을 깐다."""
        from . import bgm

        spec = self.TOUR_CLIPS[feature]
        pid, work = self._new_id("lab-wars", "reels")
        out = work / "out"
        log.info("생성 시작 %s (소개: %s)", pid, feature)
        video, segs = spec["run"](src["play_url"], work / "capture")
        if not video:
            raise RuntimeError("녹화 파일이 없음")
        by_key = {sg["key"]: sg for sg in segs}
        parts: list[Path] = []
        desc: list[str] = []
        cover_at, total = None, 0.0
        for i, (key, skip, length, text) in enumerate(spec["clips"]):
            sg = by_key.get(key)
            if not sg:
                log.warning("구간 없음: %s", key)
                continue
            dur = max(1.5, min(length, sg["dur"] - skip))
            ov = compose.reel_overlay(compose.ReelText(
                label=spec["label"], title=spec["title"], subtitle=spec["subtitle"], scene=text,
                cta="설치 없이 브라우저에서 바로 · 프로필 링크"), out / f"overlay{i}.png", theme=GAME)
            parts.append(compose.make_reel(video, sg["start"] + skip, dur, ov, work / f"part{i}.mp4"))
            if key == spec["cover"]:
                cover_at = total + dur * 0.7
            total += dur
            desc.append(f"{text.replace(chr(10), ' — ')} ({dur:.0f}초)")
        if not parts:
            raise RuntimeError("이어 붙일 구간이 없음")
        body = compose.concat(parts, work / "body_silent.mp4")
        music = bgm.write(total, int(datetime.now(self.tz).strftime("%Y%m%d")) + len(feature), work / "bgm.wav")
        body = compose.replace_audio(body, music, out / "body.mp4", volume=0.8)
        cover = compose.video_cover(body, cover_at if cover_at is not None else total / 2, out / "cover.jpg")
        reel = compose.prepend_cover(body, cover, out / "reel.mp4")
        text = captions.generate(src, self.cfg.caption, "REELS", spec["scene"], desc, self.db.recent_captions("lab-wars"))
        post = self._finalize(pid, "lab-wars", "REELS", f"[소개] {spec['title']}", text, [reel], ["video"], cover,
                              {"tour": feature, "check": "link"}, scheduled_at=scheduled_at)
        shutil.rmtree(work / "capture" / "raw", ignore_errors=True)
        return post

    def tour_major(self, src: dict, scheduled_at: float | None) -> Post:
        """소개 시리즈: 전공 모드 캐러셀 (이수체계도 → 과목 규칙 → 과목별 전투 → 플레이 링크)."""
        pid, work = self._new_id("lab-wars", "carousel")
        out = work / "out"
        log.info("생성 시작 %s (소개: 전공 모드)", pid)
        s = lab_wars_tour.run_major(src["play_url"], work / "capture")
        plan = [("map", "전공 모드", "정보통신공학 4년, 41과목 이수체계도. 학점 4.5 만점에 도전"),
                ("info", "과목마다 교수님과 규칙", "수강 신청 전에 이번 과목의 특수 규칙을 확인")]
        plan += [(f"course{i}", name, rule) for i, (name, rule) in enumerate(lab_wars_tour.MAJOR_COURSES)]
        media: list[Path] = []
        desc: list[str] = []
        for key, head, body in plan:
            if key not in s:
                continue
            media.append(compose.slide(s[key], head, body, out / f"{len(media) + 1}.jpg", FOOTER, theme=GAME))
            desc.append(f"{head} — {body}")
        if len(media) < 3:
            raise RuntimeError(f"전공 모드 화면이 모자람: {list(s)}")
        media.append(compose.cta_slide("지금 바로 플레이", ["폰 가로 · 세로 모두 지원", "설치 없이 브라우저에서"],
                                       src["play_url"].removeprefix("https://").rstrip("/"),
                                       out / f"{len(media) + 1}.jpg", theme=GAME))
        desc.append("마지막: 플레이 링크 안내")
        scene = "랩워즈 전공 모드 소개 (정보통신공학 41과목, 과목마다 전공 지식이 전투 규칙: 샘플링·NaN·스택·전치)"
        text = captions.generate(src, self.cfg.caption, "CAROUSEL", scene, desc, self.db.recent_captions("lab-wars"))
        return self._finalize(pid, "lab-wars", "CAROUSEL", "[소개] 랩워즈 전공 모드", text, media,
                              ["image"] * len(media), None, {"tour": "major", "check": "link"},
                              scheduled_at=scheduled_at)

    def generate_repo(self, source: str, kind: str | None, scheduled_at: float | None = None) -> Post:
        """레포 소개: 데모 영상 릴스 또는 카드뉴스 캐러셀."""
        from .cards.render import render
        from .sources import agent_office

        src = self.cfg.source(source)
        n = self.db.count_source(source)
        kind = kind or KIND_ROTATION[n % len(KIND_ROTATION)]
        pid, work = self._new_id(source, kind)
        out = work / "out"
        seconds = float(src.get("reel_seconds", 22))
        cap = agent_office.run_capture(src["demo_url"], work / "capture", seconds)
        repo_short = src["repo_url"].removeprefix("https://")
        cover = None
        if kind == "REELS":
            if not cap.video:
                raise RuntimeError("녹화 파일이 없음")
            ov = compose.reel_overlay(compose.ReelText(
                label="오픈소스 · 셀프 호스팅", title=src["title"], subtitle=src["tagline"],
                scene="파일을 읽으면 도서관으로, 테스트를 돌리면 테스트 구역으로",
                cta=f"{repo_short}  ·  프로필 링크"), out / "overlay.png",
                video_h=compose.video_height(cap.crop), theme=themes.get("project"))
            body = compose.make_reel(cap.video, cap.start + 0.3, seconds, ov, out / "body.mp4", crop=cap.crop)
            cover = compose.video_cover(body, min(seconds / 2, 10), out / "cover.jpg")
            reel = compose.prepend_cover(body, cover, out / "reel.mp4")
            media, types = [reel], ["video"]
            slides_desc = [f"합성 데모 데이터로 돌아가는 사무실 화면 {int(seconds)}초 (캐릭터가 상태에 따라 구역을 이동)"]
        else:
            kind = "CAROUSEL"
            c = {"brand": src["title"], "report_name": "레포 소개", "date_label": "",
                 "footnote": f"{repo_short}  |  화면은 합성 데모 데이터", "theme": themes.css("project")}
            slides = [
                ("shot", {**c, "big": src["tagline"], "image": cap.office_shot.as_uri(),
                          "caption": "Claude Code·Codex 세션이 작은 캐릭터가 되어, 하는 일에 따라 사무실을 돌아다닙니다."}),
                ("notes", {**c, "title": "어디에 있는지 보면 무엇을 하는지 보여요", "items": [
                    {"head": "도서관", "body": "파일 읽기·코드 검색"}, {"head": "인터넷", "body": "웹 검색·웹 페이지 가져오기"},
                    {"head": "회의실", "body": "계획 세우기(할 일 목록)"}, {"head": "테스트 구역", "body": "테스트 실행"},
                    {"head": "업무석", "body": "코드 편집·생각 중·승인 대기"}, {"head": "휴게실 / 출입구", "body": "턴 종료 후 휴식, 세션 시작·종료"}]}),
                ("shot", {**c, "title": "머리 위에는 지금 상태만", "image": (cap.zoom_shot or cap.office_shot).as_uri(),
                          "caption": "읽는 중, 코딩 중, 테스트 중, 승인 대기… 프롬프트나 코드 내용은 보이지 않습니다."}),
                ("notes", {**c, "title": "보내는 건 상태뿐", "items": [
                    {"head": "서버로 가는 것", "body": "상태(읽는 중·테스트 중 등)와 세션·장치 ID 같은 메타데이터"},
                    {"head": "보내지 않는 것", "body": "프롬프트, 소스 코드, 파일 본문, 도구 입출력, 전체 명령, 로컬 경로"},
                    {"head": "끄고 켜기", "body": "작업 폴더 이름 전송도 PC별로 끌 수 있음"},
                    {"head": "안전하게", "body": "Hook은 실패해도 작업을 막지 않고, 기존 설정을 덮어쓰지 않음"}]}),
                ("notes", {**c, "title": "혼자도, 팀으로도", "items": [
                    {"head": "개인 사무실", "body": "내 여러 PC의 에이전트를 한 화면에서"},
                    {"head": "팀·통합 사무실", "body": "팀원 에이전트를 함께. 공유 범위는 각자 선택(모두 / 선택한 프로젝트만 / 안 함)"},
                    {"head": "어디서나", "body": "폰·데스크톱에 앱(PWA)으로 설치, 모니터에 전체 화면으로 걸어 두기"}]}),
                ("notes", {**c, "title": "직접 띄워 보기", "items": [
                    {"head": "1. 서버", "body": "Docker Compose로 셀프 호스팅",
                     "code": "docker compose up -d --wait\n→ 브라우저에서 /setup"},
                    {"head": "2. 각 PC에 Collector", "body": "Releases에서 Windows·macOS·Linux 설치 파일, 설정 마법사로 PC 등록"},
                    {"head": "3. 끝", "body": "에이전트를 쓰면 사무실에 캐릭터가 출근합니다"}]}),
                ("notes", {**c, "title": "GitHub에서 받아 가세요", "lead": "MIT 라이선스라 누구나 가져가 쓰고 고칠 수 있어요. 아직 0.1.0 베타라 검증한 조합과 미검증 범위는 README에 그대로 적어 두었어요.",
                           "items": [], "cta_label": "레포", "cta": repo_short}),
            ]
            media = render(slides, out)
            types = ["image"] * len(media)
            slides_desc = ["표지: 사무실 화면", "구역별 의미", "머리 위 상태", "개인정보 원칙", "개인·팀 사무실", "설치 3단계", "레포 링크"]
        text = captions.generate(src, self.cfg.caption, kind, "레포 소개", slides_desc, self.db.recent_captions(source))
        post = self._finalize(pid, source, kind, f"[{kind}] {src['title']} 소개", text, media, types, cover, {"check": "link"},
                              scheduled_at)
        shutil.rmtree(work / "capture" / "raw", ignore_errors=True)
        return post

    def generate_market(self, report: str, today=None) -> Post | None:
        from .cards.render import render
        from .market import analysis, data
        from .market import report as market_report

        if report not in MARKET_REPORTS:
            raise ValueError(f"모르는 리포트: {report}")
        now = datetime.now(self.tz)
        today = today or now.date()
        if report in ("premarket", "close") and not data.is_krx_trading_day(today):
            log.info("휴장일이라 %s 건너뜀 (%s)", report, today)
            return None
        if report == "monthly" and not data.is_last_trading_day_of_month(today):
            log.info("월말 마지막 거래일이 아니라 월간 리포트 건너뜀 (%s)", today)
            return None
        source = f"market-{report}"
        pid, work = self._new_id(source, "carousel")
        snap = data.snapshot(report, today)
        if report == "close" and not str(snap["kospi"].get("at", "")).startswith(today.isoformat()):
            log.info("오늘 체결 데이터가 없어 장 마감 리포트 건너뜀")
            return None
        (work / "snapshot.json").write_text(json.dumps(snap, ensure_ascii=False, indent=1), encoding="utf-8")
        ana = analysis.analyze(snap, self.cfg.caption)
        (work / "analysis.json").write_text(json.dumps(ana, ensure_ascii=False, indent=1), encoding="utf-8")
        media = render(market_report.slides(snap, ana), work / "out", theme=themes.css("market"))
        expires = market_expiry(report, now)
        title = f"[증시] {analysis.REPORT_KO[report]} {today:%m/%d} {ana['headline']}"
        return self._finalize(pid, source, "CAROUSEL", title, ana["caption"], media, ["image"] * len(media),
                              meta={"check": "market", "report": report, "expires_at": expires})

    def generate_topic(self, topic: str, design: str = "template", source: str = "topic",
                       topic_theme: str | None = None, title: str | None = None,
                       scheduled_at: float | None = None) -> Post:
        """요청형: 주제를 조사해 카드뉴스 초안을 만든다. 노션 기획 DB에서는 source=plan-<주제>."""
        from . import custom

        theme = topic_theme or themes.topic_of(source)
        pid, work = self._new_id(source, "carousel")
        content, media = custom.make_topic(topic, self.cfg.caption, work / "out", design, theme=themes.css(theme),
                                           kicker=title, topic_key=theme)
        kind = "CAROUSEL" if len(media) > 1 else "IMAGE"
        head = f"[{title}]" if title else "[주제]"
        return self._finalize(pid, source, kind, f"{head} {content.get('title', topic[:40])}", content["caption"], media,
                              ["image"] * len(media),
                              meta={"check": "none", "topic": topic, "design": design, "topic_theme": theme},
                              scheduled_at=scheduled_at)

    def post_manual(self, title: str, text: str, files: list[Path]) -> Post:
        """요청형: 준비된 이미지·영상과 캡션으로 바로 초안을 만든다."""
        from . import custom

        if not files:
            raise ValueError("올릴 파일이 없음")
        kind, types = custom.media_kind(files)
        if kind == "CAROUSEL" and len(files) > 10:
            raise ValueError("캐러셀은 최대 10장")
        pid, work = self._new_id("manual", kind)
        staged = custom.stage_files(files, work / "out")
        cover = None
        if kind == "REELS":
            cover = compose.video_cover(staged[0], 1.0, work / "out" / "cover.jpg")
        return self._finalize(pid, "manual", kind, f"[직접] {title}", text, staged, types, cover, {"check": "none"})

    # ------------------------------------------------------------ 승인·게시
    def _instagram(self) -> Instagram | MakeWebhook | None:
        if not self.cfg.ig_enabled:
            return None
        if self.cfg.ig_mode == "make":
            return MakeWebhook(os.environ["MAKE_WEBHOOK_URL"], int(self.cfg.instagram.get("make_timeout", 300)))
        token = self.db.kv_get("ig_token") or os.environ["IG_ACCESS_TOKEN"]
        return Instagram(os.environ["IG_USER_ID"], token, self.cfg.instagram.get("api_version", "v24.0"))

    def set_status(self, post: Post, status: str, *, error: str | None = None) -> None:
        post.status = status
        if status in (PUBLISHED, REJECTED):
            post.decided_at = time.time()
        post.error = error
        self.db.save(post)
        if self.notion and post.notion_page_id:
            self.notion.update(post.notion_page_id, status=status, permalink=post.permalink,
                               error=error if error is not None else "")

    def pull_decisions(self) -> None:
        """노션에서 사람이 바꾼 상태·캡션을 가져온다."""
        if not self.notion:
            return
        for p in self.db.by_status(DRAFT, APPROVED, FAILED):
            if not p.notion_page_id:
                continue
            info = self.notion.read(p.notion_page_id)
            changed = False
            if info["caption"] and info["caption"] != p.caption:
                p.caption, changed = info["caption"], True
            ts = parse_when(info["scheduled"], self.tz)
            if ts != p.scheduled_at:
                p.scheduled_at, changed = ts, True
            if info["status"] and info["status"] != p.status and info["status"] in (DRAFT, APPROVED, REJECTED):
                p.status, changed = info["status"], True
                if p.status == REJECTED:
                    p.decided_at = time.time()
            if changed:
                self.db.save(p)
                log.info("노션 반영 %s → %s", p.id, p.status)

    def publish_approved(self) -> None:
        ig = self._instagram()
        for p in self.db.by_status(APPROVED):
            if p.scheduled_at and p.scheduled_at > time.time():
                continue
            if not ig:
                log.info("승인됨 %s — 인스타 연동이 꺼져 있어 대기 (config instagram.enabled / .env 확인)", p.id)
                continue
            problems = self.check_caption(p)
            if problems:
                self.set_status(p, FAILED, error="캡션 검증 실패: " + ", ".join(problems))
                continue
            try:
                if isinstance(ig, MakeWebhook):
                    p.ig_media_id = ig.publish(p.kind, p.media, p.caption, p.meta.get("cover_url"), post_id=p.id)
                else:
                    p.ig_media_id = ig.publish(p.kind, p.media, p.caption, p.meta.get("cover_url"))
                p.permalink = ig.permalink(p.ig_media_id)
                self.set_status(p, PUBLISHED)
                log.info("게시 완료 %s %s", p.id, p.permalink)
            except Exception as e:  # noqa: BLE001 — 실패는 기록하고 다음 건으로
                log.exception("게시 실패 %s", p.id)
                self.set_status(p, FAILED, error=str(e))

    def check_caption(self, p: Post) -> list[str]:
        """게시 직전 검사 (노션에서 사람이 고친 캡션도 다시 확인)."""
        if p.kind == "STORIES":
            return []
        check = p.meta.get("check", "link")
        if check == "market":
            from .market.analysis import DISCLAIMER
            return [] if DISCLAIMER in p.caption else ["면책 문구가 빠짐"]
        if check == "link" and p.source in self.cfg.raw["sources"]:
            src = self.cfg.source(p.source)
            return captions.validate(p.caption, captions.link_of(src), allow_repo=src.get("mode") == "repo")
        return [] if p.caption.strip() else ["빈 캡션"]

    def expire_stale(self) -> None:
        """게시 기한(시황 등)이 지난 초안·승인 건은 반려로 돌린다."""
        now = time.time()
        for p in self.db.by_status(DRAFT, APPROVED):
            exp = p.meta.get("expires_at")
            if exp and now > exp:
                self.set_status(p, REJECTED, error="게시 기한이 지나 자동 반려")
                log.info("기한 지남 → 반려 %s", p.id)

    def push_to_notion(self, posts: list[Post] | None = None) -> int:
        """노션 페이지가 아직 없는 초안·승인 건을 노션에 올린다. 실패해도 다음 sync에서 다시 시도."""
        if not self.notion:
            return 0
        db_id = self.db.kv_get("notion_db_id")
        if not db_id:
            log.warning("노션 DB가 아직 없음 → `notion-setup` 먼저 실행")
            return 0
        pushed = 0
        for p in posts if posts is not None else self.db.by_status(DRAFT, APPROVED):
            if p.notion_page_id:
                continue
            try:
                preview = ([p.meta["cover_url"]] if p.meta.get("cover_url") else []) + [m["url"] for m in p.media]
                p.notion_page_id = self.notion.create_post(db_id, p, preview)
                self.db.save(p)
                pushed += 1
            except Exception:  # noqa: BLE001 — 노션 장애가 생성·게시를 막지 않게
                log.exception("노션 등록 실패 %s (다음 sync에서 재시도)", p.id)
        return pushed

    def sync(self) -> None:
        self.push_to_notion()
        self.pull_decisions()
        self.expire_stale()
        self.publish_approved()

    # ------------------------------------------------------------ 정리·토큰
    def cleanup(self) -> int:
        keep_days = self.cfg.media.get("retention_days", 7)
        cutoff = time.time() - keep_days * 86400
        expired = [p for p in self.db.by_status(PUBLISHED, REJECTED)
                   if not p.media_deleted and p.decided_at and p.decided_at < cutoff]
        removed = self.host.prune([p.id for p in expired])
        for p in expired:
            p.media_deleted = 1
            self.db.save(p)
            shutil.rmtree(self.cfg.data_dir / "posts" / p.id, ignore_errors=True)
        log.info("미디어 정리: %d건", removed)
        return removed

    def refresh_token(self) -> None:
        ig = self._instagram()
        if not ig:
            log.info("인스타 연동이 꺼져 있어 토큰 갱신 건너뜀")
            return
        if isinstance(ig, MakeWebhook):
            log.info("Make 모드 — 토큰은 Make가 관리하므로 갱신 건너뜀")
            return
        token, expires = ig.refresh_token()
        self.db.kv_set("ig_token", token)
        log.info("토큰 갱신 완료 (만료까지 %d일)", expires // 86400)

    def setup_notion(self, parent: str) -> str:
        if not self.notion:
            raise RuntimeError(".env 에 NOTION_TOKEN 이 없음")
        db_id = self.notion.create_database(parent)
        self.db.kv_set("notion_db_id", db_id)
        return db_id


def parse_when(value: str | None, tz: ZoneInfo) -> float | None:
    """노션 날짜('2026-11-18' 또는 '2026-11-18T09:00:00.000+09:00')를 타임스탬프로."""
    if not value:
        return None
    d = datetime.fromisoformat(value)
    if d.tzinfo is None:
        d = d.replace(tzinfo=tz)
    return d.timestamp()


def market_expiry(report: str, now: datetime) -> float:
    """시황은 때를 놓치면 의미가 없다. 이 시각까지 승인·게시되지 않으면 자동 반려."""
    if report == "premarket":
        t = now.replace(hour=9, minute=0, second=0, microsecond=0)
    elif report == "close":
        t = (now + timedelta(days=1)).replace(hour=8, minute=30, second=0, microsecond=0)
    elif report == "weekly":
        t = now + timedelta(days=2)
    else:
        t = now + timedelta(days=3)
    return t.timestamp()
