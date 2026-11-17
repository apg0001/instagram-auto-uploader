"""생성 → 노션 초안 → 승인 → 게시 → 정리 흐름을 묶는다."""
from __future__ import annotations

import logging
import os
import shutil
import time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from . import caption as captions
from . import compose
from .config import Config
from .db import APPROVED, DB, DRAFT, FAILED, PUBLISHED, REJECTED, Post
from .instagram import Instagram
from .media_host import MediaHost
from .notion import Notion
from .sources import lab_wars

log = logging.getLogger("uploader")

KIND_ROTATION = ["REELS", "CAROUSEL"]
FOOTER = "랩워즈 · Lab Wars"


class Pipeline:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.db = DB(cfg.data_dir / "state.db")
        self.host = MediaHost(cfg.media_repo, cfg.media["pages_base"], cfg.media.get("pages_wait_seconds", 600))
        self.notion = Notion(os.environ["NOTION_TOKEN"], cfg.raw["notion"]["api_version"]) if cfg.notion_enabled else None
        self.tz = ZoneInfo(cfg.raw.get("timezone", "Asia/Seoul"))

    # ------------------------------------------------------------ 생성
    def generate(self, source: str = "lab-wars", kind: str | None = None, stage: str | None = None) -> Post:
        if source != "lab-wars":
            raise ValueError(f"아직 없는 소스: {source}")
        src = self.cfg.source(source)
        n = self.db.count_source(source)
        kind = kind or KIND_ROTATION[n % len(KIND_ROTATION)]
        stages = src["showcase_stages"]
        stage = stage or stages[n % len(stages)]
        pid = f"{datetime.now(self.tz):%Y%m%d-%H%M%S}-{source}-{kind.lower()}"
        work = self.cfg.data_dir / "posts" / pid
        log.info("생성 시작 %s (스테이지 %s)", pid, stage)

        seconds = float(src.get("reel_seconds", 28))
        cap = lab_wars.run_capture(stage, work / "capture", seconds, src["play_url"], seed=n)
        scene = f"{stage} {cap.stage_title}".strip()

        out = work / "out"
        out.mkdir(parents=True, exist_ok=True)
        media: list[Path] = []
        types: list[str] = []
        slides_desc: list[str] = []
        cover: Path | None = None
        if kind == "REELS":
            if not cap.video:
                raise RuntimeError("녹화 파일이 없음")
            ov = compose.reel_overlay(compose.ReelText(
                label="모바일 웹게임", title="랩워즈", subtitle=src["tagline"], scene=scene,
                cta="설치 없이 브라우저에서 바로 · 프로필 링크"), out / "overlay.png")
            reel = compose.make_reel(cap.video, cap.battle_start + 0.5, seconds, ov, out / "reel.mp4")
            cover = compose.video_cover(reel, min(seconds / 2, 12), out / "cover.jpg")
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
                media.append(compose.slide(shot, head, body, out / f"{i + 1}.jpg", FOOTER))
                slides_desc.append(f"{head} — {body}")
            media.append(compose.cta_slide("지금 바로 플레이", ["모바일 가로 화면 추천", "설치 없이 브라우저에서"],
                                           src["play_url"].removeprefix("https://").rstrip("/"),
                                           out / f"{len(media) + 1}.jpg"))
            slides_desc.append("마지막: 플레이 링크 안내")
            types = ["image"] * len(media)
        elif kind == "STORIES":
            shot = cap.battle_shots[0] if cap.battle_shots else cap.story_shot
            media = [compose.slide(shot, src["tagline"], f"{scene}\n프로필 링크에서 바로 플레이",
                                   out / "story.jpg", FOOTER, size=(1080, 1920))]
            types = ["image"]
        else:
            raise ValueError(f"모르는 형식: {kind}")

        text = ""
        if kind != "STORIES":  # 스토리는 캡션이 없다
            text = captions.generate(src, self.cfg.caption, kind, scene, slides_desc,
                                     self.db.recent_captions(source))

        uploads = media + ([cover] if cover else [])
        urls = self.host.upload(pid, uploads)
        post = Post(
            id=pid, source=source, kind=kind, status=DRAFT, title=f"[{kind}] {scene}", caption=text,
            media=[{"file": str(f), "url": u, "type": t} for f, u, t in zip(media, urls, types, strict=False)],
            meta={"stage": stage, "cover_url": urls[-1] if cover else None},
        )
        if self.notion:
            db_id = self.db.kv_get("notion_db_id")
            if db_id:
                preview = ([post.meta["cover_url"]] if cover else []) + [m["url"] for m in post.media]
                post.notion_page_id = self.notion.create_post(db_id, post, preview)
            else:
                log.warning("노션 DB가 아직 없음 → `notion-setup` 먼저 실행")
        self.db.save(post)
        shutil.rmtree(work / "capture" / "raw", ignore_errors=True)  # 원본 녹화는 크니 지운다
        log.info("초안 생성 완료 %s", pid)
        return post

    # ------------------------------------------------------------ 승인·게시
    def _instagram(self) -> Instagram | None:
        if not self.cfg.ig_enabled:
            return None
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
            if p.kind != "STORIES":
                problems = captions.validate(p.caption, self.cfg.source(p.source)["play_url"])
                if problems:
                    self.set_status(p, FAILED, error="캡션 검증 실패: " + ", ".join(problems))
                    continue
            try:
                p.ig_media_id = ig.publish(p.kind, p.media, p.caption, p.meta.get("cover_url"))
                p.permalink = ig.permalink(p.ig_media_id)
                self.set_status(p, PUBLISHED)
                log.info("게시 완료 %s %s", p.id, p.permalink)
            except Exception as e:  # noqa: BLE001 — 실패는 기록하고 다음 건으로
                log.exception("게시 실패 %s", p.id)
                self.set_status(p, FAILED, error=str(e))

    def sync(self) -> None:
        self.pull_decisions()
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
