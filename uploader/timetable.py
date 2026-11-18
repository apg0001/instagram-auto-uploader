"""노션 '주간 타임테이블' — 무엇이 언제 올라가는지 시각 × 요일 표로 보여 준다.

데이터는 세 군데서 모은다 (손으로 고치지 않는다, plan 때마다 바뀌었으면 다시 쓴다):
  1) 증시 크론 (deploy/crontab 과 같은 값 — 크론을 바꾸면 MARKET 도 같이)
  2) 노션 콘텐츠 기획 표의 '사용'·'일시중지' 줄 (주기·요일·게시 시각)
  3) 기획 표 밖에서 예약된 단발 게시물 (앞으로 7일, 예: 랩워즈 소개 시리즈) — 📌
표는 행 = 게시 시각, 열 = 월~일. 첫 열 '시각'이 제목 속성.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from . import themes
from .notion import prop_text, rich

if TYPE_CHECKING:
    from .pipeline import Pipeline

log = logging.getLogger("uploader")

DAYS = ["월", "화", "수", "목", "금", "토", "일"]
WEEKDAYS = DAYS[:5]
EMOJI = {"game": "🎮", "project": "🟦", "market": "📈", "tech": "💜", "career": "🟢"}
# deploy/crontab 의 증시 게시 (자동 승인). 월말 16:40 월간 리포트는 하루 4개에 세지 않아 표 밖 안내로만.
MARKET = [("07:30", WEEKDAYS, "장 전 브리핑"), ("16:00", WEEKDAYS, "장 마감 시황"), ("09:00", ["토"], "주간 증시 리포트")]
NOTE = "📈 증시는 크론 자동 게시 · 월말 마지막 거래일 16:40 월간 리포트는 표에 없음 · ⏸ 일시중지 · 📌 단발 예약(7일 안)"
PROPS = {"시각": {"title": {}}, **{d: {"rich_text": {}} for d in DAYS}}


def _hm(text: str) -> str:
    try:
        h, m = (int(x) for x in text.strip().split(":")[:2])
        return f"{h:02d}:{m:02d}"
    except (ValueError, AttributeError):
        return "12:00"


def grid(plan_rows: list[dict], scheduled: list[tuple[datetime, str, str]], now: datetime) -> dict[str, dict[str, list[str]]]:
    """{'12:00': {'월': ['💜 기술 브리핑', ...], ...}}. scheduled: (예약 시각, 주제, 제목) 단발 게시물."""
    g: dict[str, dict[str, list[str]]] = {}

    def put(time: str, day: str, text: str) -> None:
        g.setdefault(time, {d: [] for d in DAYS})[day].append(text)

    for time, days, name in MARKET:
        for d in days:
            put(time, d, f"📈 {name}")
    for page in plan_rows:
        pr = page["properties"]
        status = prop_text(pr, "상태")
        if status not in ("사용", "일시중지"):
            continue
        cadence = prop_text(pr, "주기")
        topic = themes.from_ko(prop_text(pr, "주제")) or "tech"
        label = f"{EMOJI.get(topic, '💜')} {prop_text(pr, '이름')}"
        if status == "일시중지":
            label = f"⏸ {label}"
        nxt = prop_text(pr, "다음 게시")
        if cadence == "한 번":
            if status != "사용" or not nxt:
                continue
            at = datetime.fromisoformat(nxt)
            put(f"{at:%H:%M}", DAYS[at.weekday()], f"{label} ({at:%m/%d}만)")
            continue
        if cadence == "격주":
            label += " (격주)"
        elif cadence == "매월":
            label += " (매월 1일)"
        days = DAYS if cadence == "매일" else [d for d in prop_text(pr, "요일").split(",") if d in DAYS]
        time = _hm(prop_text(pr, "게시 시각"))
        for d in days:
            put(time, d, label)
            if prop_text(pr, "생성기") == "깃허브 트렌딩 릴스":  # 캐러셀은 게시 시각, 릴스는 8시간 뒤
                h = (int(time[:2]) + 8) % 24
                g[time][d][-1] = f"{label} 캐러셀"
                put(f"{h:02d}:{time[3:]}", d, f"{label} 릴스")
    for at, topic, title in scheduled:
        if now <= at < now + timedelta(days=7):
            put(f"{at:%H:%M}", DAYS[at.weekday()], f"📌 {EMOJI.get(topic, '💜')} {title} ({at:%m/%d})")
    return dict(sorted(g.items()))


def _scheduled(pl: Pipeline) -> list[tuple[datetime, str, str]]:
    """기획 표가 만들지 않은 예약 게시물 (기획·깃허브·증시는 표에 이미 있음)."""
    out = []
    for p in pl.db.by_status("초안", "승인"):
        if not p.scheduled_at or p.source.startswith(("plan-", "tech-github", "market")):
            continue
        at = datetime.fromtimestamp(p.scheduled_at, pl.tz)
        out.append((at, themes.topic_of(p.source), p.title.split("] ", 1)[-1]))
    return out


def ensure_db(pl: Pipeline, parent: str) -> str:
    db_id = pl.db.kv_get("notion_timetable_db_id")
    if db_id:
        return db_id
    pl.notion.append(parent, [{"object": "block", "type": "heading_2", "heading_2": {"rich_text": rich("🕒 주간 타임테이블")}},
                              {"object": "block", "type": "paragraph", "paragraph": {"rich_text": rich(NOTE)}}])
    db_id = pl.notion.create_db(parent, "주간 타임테이블", PROPS, icon="🕒")
    pl.db.kv_set("notion_timetable_db_id", db_id)
    try:  # 열 순서: 시각, 월~일 (줄바꿈 켜기)
        n = pl.notion
        ds = n.database(db_id)["data_sources"][0]["id"]
        pid = {k: v["id"] for k, v in n.data_source(ds)["properties"].items()}
        for v in n.views(db_id):
            n.update_view(v["id"], {"name": "이번 주", "sorts": [{"property": "시각", "direction": "ascending"}],
                                    "configuration": {"type": "table", "wrap_cells": True, "properties": [
                {"property_id": pid[k], "visible": True} for k in ["시각", *DAYS]]}})
    except RuntimeError as e:
        log.warning("타임테이블 보기 설정 실패: %s", e)
    return db_id


def refresh(pl: Pipeline, plan_rows: list[dict], force: bool = False) -> bool:
    """표가 바뀌었으면 노션 타임테이블을 다시 쓴다. 바꿨으면 True."""
    db_id = pl.db.kv_get("notion_timetable_db_id")
    if not pl.notion or not db_id:
        return False
    now = datetime.now(pl.tz)
    g = grid(plan_rows, _scheduled(pl), now)
    digest = hashlib.sha1(json.dumps(g, ensure_ascii=False, sort_keys=True).encode()).hexdigest()
    if not force and pl.db.kv_get("timetable_digest") == digest:
        return False
    n = pl.notion
    existing = {prop_text(r["properties"], "시각"): r["id"] for r in n.query(db_id)}
    for time, cells in g.items():
        props = {"시각": {"title": rich(time)}, **{d: {"rich_text": rich("\n".join(cells[d]))} for d in DAYS}}
        if time in existing:
            n.set_props(existing.pop(time), props)
        else:
            n._req("POST", "/pages", json={"parent": {"database_id": db_id}, "properties": props})
    for page_id in existing.values():  # 더 이상 쓰지 않는 시각
        n._req("PATCH", f"/pages/{page_id}", json={"archived": True})
    pl.db.kv_set("timetable_digest", digest)
    log.info("타임테이블 갱신 (%d개 시각)", len(g))
    return True
