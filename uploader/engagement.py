"""반응 모으기 — 최근 게시물의 좋아요·댓글 수를 노션에 적고, 댓글 중 콘텐츠 아이디어가 될 만한 것을
💡 아이디어 수신함(출처 '댓글')에 넣는다. 그다음은 workspace.process_ideas 가 시리즈로 정리한다.

config.yaml engagement.enabled 로 켠다. 게시물마다 한 번씩 묻는다:
  graph 모드 → 인스타 API 직접 / make 모드 → 웹훅 {"action": "engagement", "media_id": "179..."} (docs/make.md 5번)
응답: {"id": "179...", "like_count": 12, "comments_count": 3, "comments": [{"id": "180...", "text": "..."}]}
Make는 묻는 횟수만큼 작업 수를 쓰므로 하루 한 번, 최근 며칠(engagement.days)만.
댓글 쓴 사람 이름은 받지도 저장하지도 않는다 (글만).
"""
from __future__ import annotations

import json
import logging
import time
from typing import TYPE_CHECKING, Any

from .caption import call_claude, parse_json
from .notion import rich

if TYPE_CHECKING:
    from .pipeline import Pipeline

log = logging.getLogger("uploader")

SEEN_KEY = "engagement_seen_comments"
PICK_PROMPT = """개발자 개인 인스타그램 계정의 댓글들이다. 이 중에서 다음 게시물 주제가 될 만한 것만 골라라:
다뤄 달라는 요청, 궁금하다는 질문, 더 알고 싶다는 반응, 새 아이디어 제안. 인사·칭찬·이모지만·광고·스팸·비방은 버린다.
고른 것은 기획 담당이 읽을 한 줄 아이디어로 다듬는다 (댓글 쓴 사람이 누군지 알 수 있는 내용은 빼고).

# 댓글 (게시물 제목 | 댓글)
{comments}

# 출력 (JSON 하나만)
{{"ideas": [{{"idea": "한 줄 아이디어", "from": "근거가 된 댓글 요지 20자 이내"}}]}}"""


def recent_posted(pl: Pipeline, days: int) -> list:
    since = time.time() - days * 86400
    return [p for p in pl.db.by_status("게시됨") if p.ig_media_id and (p.decided_at or p.created_at) >= since]


def _seen(pl: Pipeline) -> list[str]:
    try:
        return json.loads(pl.db.kv_get(SEEN_KEY) or "[]")
    except ValueError:
        return []


def new_comments(media: list[dict[str, Any]], seen: set[str], titles: dict[str, str]) -> list[tuple[str, str, str]]:
    """(댓글 id, 게시물 제목, 글) — 처음 보는 것만."""
    out = []
    for m in media:
        for c in m.get("comments") or []:
            cid, text = str(c.get("id") or ""), str(c.get("text") or "").strip()
            if cid and text and cid not in seen:
                out.append((cid, titles.get(str(m.get("id")), ""), text[:300]))
    return out


def _ensure_props(pl: Pipeline) -> None:
    if pl.db.kv_get("engagement_props"):
        return
    db_id = pl.db.kv_get("notion_db_id")
    ds = pl.notion.database(db_id)["data_sources"][0]["id"]
    have = pl.notion.data_source(ds)["properties"]
    missing = {k: {"number": {}} for k in ("좋아요", "댓글 수") if k not in have}
    if missing:
        pl.notion.add_properties(db_id, missing)
    pl.db.kv_set("engagement_props", "1")


def collect(pl: Pipeline) -> dict[str, int]:
    cfg = pl.cfg.raw.get("engagement") or {}
    if not cfg.get("enabled"):
        return {}
    ig = pl._instagram()
    if ig is None:
        return {}
    posts = recent_posted(pl, int(cfg.get("days", 3)))
    if not posts:
        return {"posts": 0}
    by_id = {p.ig_media_id: p for p in posts}
    media = []
    for mid in by_id:
        try:
            m = ig.engagement(mid)
        except Exception as e:  # noqa: BLE001 — 경로가 아직 없거나 일시 오류: 다음 날 다시
            log.warning("반응 조회 실패 %s: %s", mid, e)
            break
        if isinstance(m, dict):
            media.append({**m, "id": str(m.get("id") or mid)})
    # 1) 좋아요·댓글 수 → 노션 게시물 표
    if pl.notion:
        _ensure_props(pl)
        for m in media:
            p = by_id.get(str(m.get("id")))
            if p and p.notion_page_id:
                props = {k: {"number": int(m[f])} for k, f in (("좋아요", "like_count"), ("댓글 수", "comments_count"))
                         if isinstance(m.get(f), (int, float))}
                if props:
                    pl.notion.set_props(p.notion_page_id, props)
    # 2) 새 댓글 → 아이디어
    seen = _seen(pl)
    fresh = new_comments(media, set(seen), {k: v.title for k, v in by_id.items()})
    added = 0
    ideas_db = pl.db.kv_get("notion_ideas_db_id")
    if fresh and ideas_db and pl.notion:
        listing = "\n".join(f"- {t} | {text}" for _, t, text in fresh)
        picked = parse_json(call_claude(PICK_PROMPT.format(comments=listing), pl.cfg.caption)).get("ideas") or []
        for it in picked[:10]:
            idea = str(it.get("idea") or "").strip()
            if not idea:
                continue
            pl.notion._req("POST", "/pages", json={"parent": {"database_id": ideas_db}, "properties": {
                "아이디어": {"title": rich(idea[:200])}, "출처": {"select": {"name": "댓글"}},
                "결과": {"rich_text": rich(f"댓글: {str(it.get('from') or '')[:60]}")}}})
            added += 1
    if fresh:
        pl.db.kv_set(SEEN_KEY, json.dumps((seen + [c for c, _, _ in fresh])[-3000:]))
    log.info("반응 모음: 게시물 %d, 새 댓글 %d, 아이디어 %d", len(media), len(fresh), added)
    return {"posts": len(media), "comments": len(fresh), "ideas": added}
