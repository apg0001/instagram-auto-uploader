"""노션 작업 공간 — 홈은 '오늘 요약'만, 표는 하위 페이지에 하나씩.

🏠 홈 (notion-setup 때 고른 페이지)
 ├ 📌 오늘 요약 (콜아웃) + 🗓 이번 주 올라갈 것 (표) — plan 때마다 바뀌었으면 다시 씀
 ├ 📥 게시물          — 게시물 DB. 2주 지난 게시됨·반려는 '보관'에 체크돼 🗄 보관함 보기로만 보인다
 ├ 🧭 시리즈 기획      — 콘텐츠 기획 DB + 쓰는 법
 ├ 💡 아이디어 수신함   — 한 줄 적으면 Claude가 기존 시리즈의 다음 회차 후보로 넣거나 새 시리즈 요청을 만든다
 ├ 🕒 주간 타임테이블
 └ 📖 운영 설명서
organize() 는 여러 번 실행해도 안전하다 (만든 페이지 id는 kv `notion_page_<key>`).
"""
from __future__ import annotations

import hashlib
import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from .caption import call_claude, parse_json
from .notion import VIEWS_VERSION, prop_text, rich

if TYPE_CHECKING:
    from .pipeline import Pipeline

log = logging.getLogger("uploader")

ARCHIVE_DAYS = 14
PAGES = [("posts", "📥", "게시물"), ("plan", "🧭", "시리즈 기획"), ("whatif", "🎬", "만약에 주제"),
         ("ideas", "💡", "아이디어 수신함"), ("timetable", "🕒", "주간 타임테이블"), ("guide", "📖", "운영 설명서")]
DB_OF = {"posts": "notion_db_id", "plan": "notion_plan_db_id", "whatif": "notion_whatif_db_id",
         "ideas": "notion_ideas_db_id", "timetable": "notion_timetable_db_id"}
# 보기 이름 통일: 이모지 + 이름 (옛 이름 → 새 이름)
VIEW_NAMES = {"전체": "📋 전체", "상태별": "🗂 상태별", "최근": "📋 최근", "이번 주": "🗓 이번 주", "Default view": "📋 전체"}

IDEA_NEW, IDEA_DONE, IDEA_HOLD = "새 아이디어", "정리됨", "보류"
IDEA_PROPS = {
    "아이디어": {"title": {}},
    "상태": {"select": {"options": [{"name": IDEA_NEW, "color": "yellow"}, {"name": IDEA_DONE, "color": "green"},
                                   {"name": IDEA_HOLD, "color": "gray"}]}},
    "출처": {"select": {"options": [{"name": "직접", "color": "blue"}, {"name": "댓글", "color": "purple"}]}},
    "결과": {"rich_text": {}},
    "생성": {"created_time": {}},
}
CANDIDATES = "# 다음 회차 후보 (아이디어 수신함)"

INTRO = {
    "posts": "자동 승인이라 손댈 필요 없어요. 올라가면 안 되는 게시물만 상태를 '반려'로 바꾸세요.\n"
             "📥 확인할 것: 초안·실패만 · 🗓 주간 예약: 올라갈 순서 · 🗄 보관함: 2주 지난 게시됨·반려",
    "plan": "줄 하나 = 시리즈 하나. 새 줄의 '메모'에 대충 적고 상태를 '새 요청'으로 → 몇 분 안에 Claude가 이름·주기·시각·"
            "'다듬은 규칙'을 채워 '검토 대기'로 → 괜찮으면 '사용'. '다음 게시' 5시간 전에 만들어 그 시각에 자동 게시.\n"
            "'횟수'를 채우면 끝나고 '이어서'에 적은 줄이 시작돼요. 멈추려면 '일시중지'. "
            "프로젝트 소개처럼 가끔 올릴 것은 '일시중지'로 두었다가 올리고 싶을 때 '사용'으로.",
    "ideas": "떠오른 걸 '아이디어' 칸에 한 줄만 적으세요 (상태는 비워 둬도 됨). 몇 분 안에 Claude가 읽고\n"
             "• 기존 시리즈에 맞으면 → 그 시리즈 '다듬은 규칙'의 다음 회차 후보로\n"
             "• 새 시리즈감이면 → 🧭 시리즈 기획에 '새 요청' 줄을 만들어요 (거기서 검토 후 '사용')\n"
             "• 애매하면 '보류'와 이유를 '결과'에 남겨요.",
    "whatif": "'만약에 3D 릴스'가 매주 여기서 하나씩 골라요: 데이터 계산과 이야기를 번갈아, ⭐추천 먼저, 번호 순.\n"
              "원칙: 상상하라고 하지 말고 직접 보여 준다 — 규칙을 먼저 정하고, 그 안에서 단계별로 커지다 결말까지 (열린 결말 금지).\n"
              "유형: A 불가능한 현실 · B 본 적 없는 세계 · C 존재 공포 · 데이터 계산 · 역사 바꾸기 · 과학 결말 · 사고실험. "
              "결말을 보여 주기 어려운 주제는 '기각', 빼 두려면 '보류', 새 주제는 줄 추가.",
    "timetable": "증시는 크론 자동 게시 · 월말 마지막 거래일 16:40 월간 리포트는 표에 없음 · ⏸ 일시중지 · 📌 단발 예약(7일 안). "
                 "시리즈 기획이 바뀌면 저절로 다시 그려져요.",
}
GUIDE = [
    ("heading_2", "매일 저절로 일어나는 일"),
    ("bulleted_list_item", "증시: 평일 07:30 장 전 브리핑, 16:00 장 마감 시황, 토 09:00 주간 리포트, 월말 16:40 월간 리포트."),
    ("bulleted_list_item", "시리즈: 🧭 시리즈 기획의 '사용' 줄이 게시 5시간 전에 만들어져 그 시각에 올라가요 (자동 승인)."),
    ("bulleted_list_item", "게시 칸: 평일 12:00·20:00, 주말 09·12·16·20시. 하루 4개 (월간 리포트 제외)."),
    ("bulleted_list_item", "2주 지난 게시됨·반려 게시물은 📥 게시물의 🗄 보관함으로 옮겨져요."),
    ("heading_2", "내가 하는 일"),
    ("bulleted_list_item", "아이디어가 떠오르면 💡 아이디어 수신함에 한 줄."),
    ("bulleted_list_item", "만약에 릴스 주제는 🎬 만약에 주제에서 ⭐추천·보류·기각으로 고르기 (매주 토 20:00 자동)."),
    ("bulleted_list_item", "'검토 대기' 시리즈를 읽고 '사용' 또는 고쳐서 '새 요청'."),
    ("bulleted_list_item", "올라가면 안 될 게시물은 '반려'. 오류가 보이면 Claude Code에 말하기."),
    ("heading_2", "콘텐츠 규칙"),
    ("bulleted_list_item", "사실은 확인된 출처만, 카드 마지막·캡션에 출처. 증시는 투자 권유 금지 + 면책 문구."),
    ("bulleted_list_item", "계정 주인 실명 쓰지 않기. 랩워즈는 레포·소스 언급 없이 플레이 링크만."),
    ("bulleted_list_item", "뻔한 것보다 잘 알려지지 않았지만 바로 써먹을 수 있는 것. 가능하면 실제 화면으로 보여 주기."),
    ("heading_2", "Make 실행 기록 (게시가 실패했을 때 여기서 원인 확인)"),
]

IDEA_PROMPT = """너는 개발자 개인 인스타그램 계정의 콘텐츠 기획 담당이다. 계정 주인이 아이디어 수신함에 한 줄을 적었다.
지금 돌고 있는 시리즈 목록을 보고 이 아이디어를 어디에 넣을지 정해라.

# 아이디어 (출처: {source})
{idea}

# 시리즈 목록 (이름 | 상태 | 주제 | 규칙 앞부분)
{series}

# 판단 기준
- 기존 시리즈의 규칙 범위에 자연스럽게 들어가면 "series" (상태가 '사용' 또는 '일시중지'인 것만). 다음 회차에 다룰 한 줄 후보로 다듬어 note에.
- 어느 시리즈에도 안 맞고 계정에 어울리는 새 기획감이면 "new". memo는 기획 담당이 읽을 메모(무엇을, 누구에게, 어떤 구성으로, 얼마나 자주).
- 계정 규칙(출처 확인, 투자 권유 금지, 실명·비방 금지)에 어긋나거나 너무 모호하면 "hold"와 이유.

# 출력 (JSON 하나만)
{{"action": "series|new|hold", "series": "시리즈 이름(series일 때 목록의 이름 그대로)", "note": "다음 회차 후보 한 줄",
  "memo": "새 시리즈 메모(new일 때)", "reason": "한 줄 이유"}}"""


# ---------------------------------------------------------------- 구조

def _page(pl: Pipeline, key: str, home: str) -> tuple[str, bool]:
    """하위 페이지 id (없으면 만듦). 새로 만들었으면 True."""
    pid = pl.db.kv_get(f"notion_page_{key}")
    if pid:
        return pid, False
    emoji, title = next((e, t) for k, e, t in PAGES if k == key)
    pid = pl.notion._req("POST", "/pages", json={"parent": {"page_id": home}, "icon": {"type": "emoji", "emoji": emoji},
                                                 "properties": {"title": {"title": rich(title)}}})["id"]
    pl.db.kv_set(f"notion_page_{key}", pid)
    return pid, True


def _callout(text: str, emoji: str = "💬", color: str = "gray_background") -> dict:
    return {"object": "block", "type": "callout",
            "callout": {"rich_text": rich(text), "icon": {"type": "emoji", "emoji": emoji}, "color": color}}


def _block(kind: str, text: str) -> dict:
    return {"object": "block", "type": kind, kind: {"rich_text": rich(text)}}


def _move_db(pl: Pipeline, db_id: str, page: str) -> bool:
    db = pl.notion.database(db_id)
    if db["parent"].get("page_id", "").replace("-", "") == page.replace("-", ""):
        return False
    pl.notion._req("PATCH", f"/databases/{db_id}", version=VIEWS_VERSION,
                   json={"parent": {"type": "page_id", "page_id": page}})
    return True


def ensure_ideas_db(pl: Pipeline, page: str) -> str:
    db_id = pl.db.kv_get("notion_ideas_db_id")
    if db_id:
        return db_id
    db_id = pl.notion.create_db(page, "아이디어 수신함", IDEA_PROPS, icon="💡")
    pl.db.kv_set("notion_ideas_db_id", db_id)
    try:
        n = pl.notion
        ds = n.database(db_id)["data_sources"][0]["id"]
        p = {k: v["id"] for k, v in n.data_source(ds)["properties"].items()}
        for v in n.views(db_id):
            n.update_view(v["id"], {"name": "최근", "sorts": [{"property": "생성", "direction": "descending"}],
                                    "configuration": {"type": "table", "wrap_cells": True, "properties": [
                                        {"property_id": p[k], "visible": True} for k in ("아이디어", "상태", "결과", "출처")]}})
    except RuntimeError as e:
        log.warning("아이디어 보기 설정 실패: %s", e)
    return db_id


def _posts_views(pl: Pipeline) -> list[str]:
    """게시물 DB: '보관' 체크박스, 보관함 보기, 겹치는 보기 정리."""
    n, db_id = pl.notion, pl.db.kv_get("notion_db_id")
    done: list[str] = []
    ds = n.database(db_id)["data_sources"][0]["id"]
    props = n.data_source(ds)["properties"]
    if "보관" not in props:
        n.add_properties(db_id, {"보관": {"checkbox": {}}})
        props = n.data_source(ds)["properties"]
        done.append("게시물 DB에 '보관' 추가")
    if "업로드" not in props:  # 인스타에 실제로 올라간 시각 — 이미 올라간 것도 채운다
        n.add_properties(db_id, {"업로드": {"date": {}}})
        props = n.data_source(ds)["properties"]
        filled = 0
        for post in pl.db.by_status("게시됨"):
            if post.notion_page_id and post.decided_at:
                n.update(post.notion_page_id, posted=post.decided_at)
                filled += 1
        done.append(f"게시물 DB에 '업로드' 추가 ({filled}개 채움)")
    p = {k: v["id"] for k, v in props.items()}
    live = {"property": "보관", "checkbox": {"equals": False}}
    have = {v["name"]: v for v in n.views(db_id)}
    want = {
        "📋 전체": {"filter": live, "configuration": {"type": "table", "wrap_cells": True, "properties": [
            {"property_id": p[k], "visible": True} for k in ("제목", "상태", "주제", "형식", "예약", "업로드", "인스타 링크")] + [
            {"property_id": p[k], "visible": False} for k in ("캡션", "미디어", "ID", "소스", "오류", "보관") if k in p]}},
        "📥 확인할 것": {"filter": {"or": [{"property": "상태", "select": {"equals": s}} for s in ("초안", "실패")]}},
        "🖼 게시됨": {"filter": {"and": [{"property": "상태", "select": {"equals": "게시됨"}}, live]},
                    "sorts": [{"property": "업로드", "direction": "descending"}],
                    "configuration": {"type": "gallery", "cover": {"type": "property", "property_id": p["미디어"]},
                                      "cover_size": "medium", "cover_aspect": "cover", "properties": [
                                          {"property_id": p[k], "visible": True} for k in ("제목", "업로드", "형식")]}},
    }
    for name, body in want.items():
        if name in have:
            n.update_view(have[name]["id"], body)
    for name in ("🗂 주제별", "🗑 반려"):  # 보관함·전체와 겹친다
        if name in have:
            try:
                n._req("DELETE", f"/views/{have[name]['id']}", version=VIEWS_VERSION)
                done.append(f"보기 {name} 정리")
            except RuntimeError as e:
                log.warning("보기 삭제 실패 %s: %s", name, e)
    if "🗄 보관함" not in have:
        n.create_view({"database_id": db_id, "data_source_id": ds, "name": "🗄 보관함", "type": "table",
                       "filter": {"property": "보관", "checkbox": {"equals": True}},
                       "sorts": [{"property": "생성", "direction": "descending"}],
                       "configuration": {"type": "table", "properties": [
                           {"property_id": p[k], "visible": True} for k in ("제목", "상태", "주제", "형식", "인스타 링크")] + [
                           {"property_id": p[k], "visible": False} for k in ("캡션", "미디어", "ID", "소스", "오류", "예약")]}})
        done.append("보기 🗄 보관함")
    return done


def _whatif_views(pl: Pipeline, db_id: str) -> list[str]:
    n, done = pl.notion, []
    ds = n.database(db_id)["data_sources"][0]["id"]
    p = {k: v["id"] for k, v in n.data_source(ds)["properties"].items()}
    if not all(k in p for k in ("주제", "유형", "상태", "추천", "번호")):
        return done
    cols = {"type": "table", "wrap_cells": True, "properties": [
        {"property_id": p[k], "visible": True} for k in ("주제", "유형", "상태", "추천", "현실성", "메모", "번호") if k in p]}
    have = {v["name"]: v for v in n.views(db_id)}
    if "📋 전체" in have:
        n.update_view(have["📋 전체"]["id"], {"sorts": [{"property": "번호", "direction": "ascending"}], "configuration": cols})
    specs = [{"name": "⭐ 다음 후보", "type": "table", "filter": {"property": "상태", "select": {"equals": "대기"}},
              "sorts": [{"property": "추천", "direction": "descending"}, {"property": "번호", "direction": "ascending"}],
              "configuration": cols},
             {"name": "🗂 유형별", "type": "board", "filter": {"property": "상태", "select": {"does_not_equal": "기각"}},
              "configuration": {"type": "board", "group_by": {"type": "select", "property_id": p["유형"],
                                                               "sort": {"type": "manual"}, "hide_empty_groups": True}}}]
    for spec in specs:
        if spec["name"] not in have:
            try:
                n.create_view({"database_id": db_id, "data_source_id": ds, **spec})
                done.append(f"보기 {spec['name']}")
            except RuntimeError as e:
                log.warning("보기 %s 실패: %s", spec["name"], e)
    return done


def _unify(pl: Pipeline) -> list[str]:
    """하위 페이지 = 💬 안내 + 표 하나. 표 이름·아이콘은 페이지와 같게, 보기 이름은 이모지 + 이름."""
    n, done = pl.notion, []
    for key, emoji, title in PAGES:
        db_id = pl.db.kv_get(DB_OF.get(key, ""))
        if not db_id:
            continue
        db = n.database(db_id)
        if prop_title(db) != title or (db.get("icon") or {}).get("emoji") != emoji:
            n._req("PATCH", f"/databases/{db_id}", version=VIEWS_VERSION,
                   json={"title": rich(title), "icon": {"type": "emoji", "emoji": emoji}})
            done.append(f"표 이름·아이콘 → {emoji} {title}")
        for v in n.views(db_id):
            if v["name"] in VIEW_NAMES:
                n.update_view(v["id"], {"name": VIEW_NAMES[v["name"]]})
        page = pl.db.kv_get(f"notion_page_{key}")
        intro_seen = False
        for b in n.children(page):  # 안내 콜아웃 하나 + 표만 남긴다
            if b["type"] == "child_database":
                continue
            if b["type"] == "callout" and not intro_seen:
                intro_seen = True
                n._req("PATCH", f"/blocks/{b['id']}", json={"callout": {
                    "rich_text": rich(INTRO[key]), "icon": {"type": "emoji", "emoji": "💬"}, "color": "gray_background"}})
                continue
            n._req("DELETE", f"/blocks/{b['id']}")
    wid = pl.db.kv_get("notion_whatif_db_id")
    if wid:
        done += _whatif_views(pl, wid)
    return done


def _order_home(pl: Pipeline, home: str) -> list[str]:
    """홈의 하위 페이지를 PAGES 순서로. 다시 넣으면 맨 뒤에 붙으므로, 처음 어긋난 곳부터 뒤를 잠깐 뺐다 넣는다."""
    n = pl.notion
    want = [pl.db.kv_get(f"notion_page_{k}") for k, _, _ in PAGES]
    have = [b["id"] for b in n.children(home) if b["type"] == "child_page" and b["id"] in want]
    order = [p for p in want if p in have]
    if have == order:
        return []
    first = next(i for i, (a, b) in enumerate(zip(have, order, strict=False)) if a != b)
    park = order[0] if first > 0 else order[-1]  # 잠깐 맡겨 둘 곳 (옮기는 대상이 아닌 페이지)
    for pid in order[first:]:
        if pid == park:
            continue
        for parent in (park, home):
            n._req("POST", f"/pages/{pid}/move", version=VIEWS_VERSION,
                   json={"parent": {"type": "page_id", "page_id": parent}})
    return ["홈 하위 페이지 순서 정리"]


def prop_title(db: dict) -> str:
    return "".join(x.get("plain_text") or x.get("text", {}).get("content", "") for x in db.get("title") or [])


def organize(pl: Pipeline) -> list[str]:
    """홈을 요약 + 하위 페이지로 정리한다. 반환: 한 일 목록."""
    n = pl.notion
    home = pl.db.kv_get("notion_home_page_id")
    if not home:
        home = n.database(pl.db.kv_get("notion_db_id"))["parent"]["page_id"]
        pl.db.kv_set("notion_home_page_id", home)
    done: list[str] = []
    page = n._req("GET", f"/pages/{home}")
    if not prop_text(page["properties"], "title"):
        n._req("PATCH", f"/pages/{home}", json={"icon": {"type": "emoji", "emoji": "🏠"},
                                                "properties": {"title": {"title": rich("인스타그램 운영")}}})
    # 1) 홈의 옛 안내 블록은 지우고 (Make 기록 링크는 설명서로 옮김), 요약 콜아웃을 맨 위에
    bookmarks = []
    summary = pl.db.kv_get("notion_home_summary_block")
    for b in n.children(home):
        if b["type"] not in ("heading_2", "paragraph", "callout", "bookmark") or b["id"] == summary:
            continue  # 하위 페이지·이번 주 표(heading_3·table)는 그대로
        if b["type"] == "bookmark":
            bookmarks.append(b["bookmark"]["url"])
        n._req("DELETE", f"/blocks/{b['id']}")
    if not summary:
        summary = n.append(home, [_callout("오늘 요약을 준비 중이에요.", "📌", "blue_background")])[0]["id"]
        pl.db.kv_set("notion_home_summary_block", summary)
        done.append("홈 요약")
    # 2) 하위 페이지 + 소개 + 표 옮기기 (소개를 먼저 써야 표가 그 아래에 온다)
    for key, _, title in PAGES:
        pid, new = _page(pl, key, home)
        if new:
            if key == "guide":
                n.append(pid, [_block(k, t) for k, t in GUIDE] +
                         [{"object": "block", "type": "bookmark", "bookmark": {"url": u}} for u in bookmarks])
            else:
                n.append(pid, [_callout(INTRO[key])])
            done.append(f"페이지 {title}")
        if key == "ideas":
            ensure_ideas_db(pl, pid)
        db_id = pl.db.kv_get(DB_OF.get(key, ""))
        if db_id and _move_db(pl, db_id, pid):
            done.append(f"{title} 표 → 하위 페이지")
    done += _posts_views(pl)
    done += _unify(pl)
    done += _order_home(pl, home)
    refresh_home(pl, force=True)
    return done


# ---------------------------------------------------------------- 매번 (plan 크론)

def archive_old(pl: Pipeline, now: datetime) -> int:
    """2주 지난 게시됨·반려 게시물에 '보관' 체크."""
    db_id = pl.db.kv_get("notion_db_id")
    before = (now - timedelta(days=ARCHIVE_DAYS)).isoformat(timespec="minutes")
    flt = {"and": [{"property": "보관", "checkbox": {"equals": False}},
                   {"or": [{"property": "상태", "select": {"equals": s}} for s in ("게시됨", "반려")]},
                   {"timestamp": "created_time", "created_time": {"before": before}}]}
    rows = pl.notion.query(db_id, flt)
    for r in rows:
        pl.notion.set_props(r["id"], {"보관": {"checkbox": True}})
    if rows:
        log.info("보관함으로 %d개", len(rows))
    return len(rows)


def short_title(title: str, n: int = 48) -> str:
    """'[시리즈] [소제목] 내용' → '시리즈 · 내용' (홈 요약 한 줄용)."""
    series = title[1:title.index("]")] if title.startswith("[") and "]" in title else ""
    rest = title.rsplit("] ", 1)[-1]
    out = f"{series} · {rest}" if series and rest != title else title
    return out if len(out) <= n else out[: n - 1] + "…"


SOURCE_KO = {"market:premarket": "장 전 브리핑", "market:close": "장 마감 시황", "market:weekly": "주간 증시 리포트",
             "market:monthly": "월간 증시 리포트", "github-trending": "깃허브 Top 5", "whatif": "만약에 릴스",
             "news": "AI 속보 감시"}


def recent_failures(raw: str | None, now: datetime, hours: int = 24) -> list[str]:
    """kv generate_failures → 최근 실패 한 줄씩 ('11/21 09:00 주간 증시 리포트: 이유')."""
    import json

    try:
        items = json.loads(raw or "[]")
    except ValueError:
        return []
    out = []
    for it in items:
        at = datetime.fromtimestamp(float(it.get("at", 0)), now.tzinfo)
        if now - at <= timedelta(hours=hours):
            name = SOURCE_KO.get(it.get("source", ""), it.get("source", ""))
            out.append(f"{at:%m/%d %H:%M} {name}: {str(it.get('error', ''))[:80]}")
    return out


def summary_text(now: datetime, posts: list[dict], plan_rows: list[dict], ideas: list[dict],
                 failures: list[str] | None = None) -> str:
    """홈 콜아웃 글. posts: 게시물 DB 행(보관 안 된 것)."""
    today = now.date()
    days = "월화수목금토일"
    up, check, failed = [], 0, 0
    for r in posts:
        pr = r["properties"]
        st = prop_text(pr, "상태")
        if st == "초안":
            check += 1
        elif st == "실패":
            failed += 1
        at = prop_text(pr, "예약")
        if st in ("초안", "승인") and at:
            dt = datetime.fromisoformat(at)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=now.tzinfo)
            dt = dt.astimezone(now.tzinfo)
            if dt.date() == today and dt >= now - timedelta(minutes=30):
                up.append((dt, short_title(prop_text(pr, "제목"))))
    review = sum(1 for r in plan_rows if prop_text(r["properties"], "상태") == "검토 대기")
    plan_err = sum(1 for r in plan_rows if prop_text(r["properties"], "상태") == "오류")
    new_ideas = sum(1 for r in ideas if prop_text(r["properties"], "상태") in ("", IDEA_NEW))
    lines = [f"{today:%m/%d}({days[today.weekday()]}) 오늘 남은 게시"]
    lines += [f"  {dt:%H:%M}  {t}" for dt, t in sorted(up)] or ["  (예약된 게시물 없음 — 증시는 크론이 따로 올려요)"]
    todo = []
    if check:
        todo.append(f"📥 확인할 초안 {check}개")
    if failed or plan_err:
        todo.append(f"⚠️ 실패·오류 {failed + plan_err}개")
    if review:
        todo.append(f"🧭 검토 대기 시리즈 {review}개")
    if new_ideas:
        todo.append(f"💡 정리 중인 아이디어 {new_ideas}개")
    lines.append("할 일: " + (" · ".join(todo) if todo else "없음 👍"))
    for f in failures or []:
        lines.append(f"⚠️ 생성 실패 {f}")
    return "\n".join(lines)


def _dt(text: str, tz) -> datetime | None:
    if not text:
        return None
    dt = datetime.fromisoformat(text)
    return (dt if dt.tzinfo else dt.replace(tzinfo=tz)).astimezone(tz)


def week_rows(now: datetime, posts: list[dict], plan_rows: list[dict]) -> list[tuple[datetime, str, str]]:
    """오늘~일요일에 올라갈 것: (시각, 무엇, 상태). 만들어진 예약 게시물 + 앞으로 만들 시리즈 회차 + 증시 크론."""
    from . import planner, timetable

    end = (now + timedelta(days=6 - now.weekday())).replace(hour=23, minute=59, second=59, microsecond=0)
    out: list[tuple[datetime, str, str]] = []
    taken: set[str] = set()
    for r in posts:
        pr = r["properties"]
        st, at = prop_text(pr, "상태"), _dt(prop_text(pr, "예약"), now.tzinfo)
        if st in ("초안", "승인") and at and now - timedelta(minutes=5) <= at <= end:
            out.append((at, short_title(prop_text(pr, "제목"), 60), "✅ 준비됨" if st == "승인" else "📝 초안"))
            taken.add(f"{at:%m%d%H%M}")
    for r in plan_rows:
        pr = r["properties"]
        if prop_text(pr, "상태") != "사용":
            continue
        name, cadence = prop_text(pr, "이름"), prop_text(pr, "주기") or "한 번"
        days, time_text = prop_text(pr, "요일").split(","), prop_text(pr, "게시 시각")
        at = _dt(prop_text(pr, "다음 게시"), now.tzinfo) or planner.next_run(cadence, days, time_text, now)
        hops = 0
        while at and at <= end and hops < 14:
            reel = prop_text(pr, "생성기") == "깃허브 트렌딩 릴스"
            for t, label in ((at, f"{name} 캐러셀" if reel else name),) + (((at + timedelta(hours=8), f"{name} 릴스"),) if reel else ()):
                if t >= now - timedelta(minutes=5) and t <= end and f"{t:%m%d%H%M}" not in taken:
                    out.append((t, label, "⏳ 만들 예정"))
            if cadence == "한 번":
                break
            at = planner.next_run(cadence, days, time_text, at, at)
            hops += 1
    day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    while day <= end:
        for hm, wdays, name in timetable.MARKET:
            h, m = (int(x) for x in hm.split(":"))
            t = day.replace(hour=h, minute=m)
            if DAYS_KO[t.weekday()] in wdays and t >= now - timedelta(minutes=5):
                out.append((t, f"📈 {name}", "🤖 자동"))
        day += timedelta(days=1)
    return sorted(out, key=lambda x: (x[0], x[1]))


DAYS_KO = "월화수목금토일"


def week_table(rows: list[tuple[datetime, str, str]]) -> dict:
    def cell(t: str) -> list[dict]:
        return rich(t)

    head = {"type": "table_row", "table_row": {"cells": [cell("날짜"), cell("시각"), cell("무엇"), cell("상태")]}}
    body = []
    prev = ""
    for t, what, st in rows:
        d = f"{t:%m/%d}({DAYS_KO[t.weekday()]})"
        body.append({"type": "table_row", "table_row": {"cells": [cell(d if d != prev else ""), cell(f"{t:%H:%M}"),
                                                                    cell(what), cell(st)]}})
        prev = d
    if not body:
        body = [{"type": "table_row", "table_row": {"cells": [cell(""), cell(""), cell("이번 주에 남은 게시 없음"), cell("")]}}]
    return {"object": "block", "type": "table",
            "table": {"table_width": 4, "has_column_header": True, "has_row_header": False, "children": [head, *body]}}


def refresh_home(pl: Pipeline, plan_rows: list[dict] | None = None, force: bool = False) -> bool:
    n, block = pl.notion, pl.db.kv_get("notion_home_summary_block")
    if not n or not block:
        return False
    now = datetime.now(pl.tz)
    posts = n.query(pl.db.kv_get("notion_db_id"), {"property": "보관", "checkbox": {"equals": False}})
    if plan_rows is None:
        plan_rows = n.query(pl.db.kv_get("notion_plan_db_id"))
    ideas_db = pl.db.kv_get("notion_ideas_db_id")
    ideas = n.query(ideas_db) if ideas_db else []
    text = summary_text(now, posts, plan_rows, ideas, recent_failures(pl.db.kv_get("generate_failures"), now))
    week = week_rows(now, posts, plan_rows)
    digest = hashlib.sha1((text + repr([(f"{t:%m%d%H%M}", w, s) for t, w, s in week])).encode()).hexdigest()
    if not force and pl.db.kv_get("home_digest") == digest:
        return False
    n._req("PATCH", f"/blocks/{block}", json={"callout": {"rich_text": rich(text)}})
    # 이번 주 표: 지우고 요약 바로 아래에 다시 (행 수가 바뀌므로 통째로)
    old = pl.db.kv_get("notion_home_week_block")
    if old:
        try:
            n._req("DELETE", f"/blocks/{old}")
        except RuntimeError:
            pass
    else:
        n.append(pl.db.kv_get("notion_home_page_id"), [_block("heading_3", "🗓 이번 주 올라갈 것")], after=block)
    head = next((b["id"] for b in n.children(pl.db.kv_get("notion_home_page_id"))
                 if b["type"] == "heading_3"), block)
    new = n.append(pl.db.kv_get("notion_home_page_id"), [week_table(week)], after=head)[0]["id"]
    pl.db.kv_set("notion_home_week_block", new)
    pl.db.kv_set("home_digest", digest)
    return True


def _series_table(plan_rows: list[dict]) -> str:
    out = []
    for r in plan_rows:
        pr = r["properties"]
        st = prop_text(pr, "상태")
        if st in ("사용", "일시중지", "검토 대기"):
            rules = prop_text(pr, "다듬은 규칙").split(CANDIDATES)[0].replace("\n", " ")[:160]
            out.append(f"- {prop_text(pr, '이름')} | {st} | {prop_text(pr, '주제')} | {rules}")
    return "\n".join(out)


def add_candidate(rules: str, note: str) -> str:
    """시리즈 규칙 끝 '다음 회차 후보' 목록에 한 줄 추가 (노션 글자 수 한도 안에서)."""
    base, _, tail = rules.partition(CANDIDATES)
    items = [x for x in tail.strip().splitlines() if x.strip()] + [f"- {note}"]
    while items:
        out = f"{base.rstrip()}\n\n{CANDIDATES}\n" + "\n".join(items)
        if len(out) <= 1900:
            return out
        items.pop(0)  # 넘치면 오래된 후보부터 뺀다 (규칙 본문은 그대로)
    return base[:1900]


def process_ideas(pl: Pipeline, plan_rows: list[dict]) -> int:
    """새 아이디어(상태 비어 있음 포함)를 하나씩 정리한다. 처리한 수."""
    ideas_db = pl.db.kv_get("notion_ideas_db_id")
    if not pl.notion or not ideas_db:
        return 0
    n, plan_id = pl.notion, pl.db.kv_get("notion_plan_db_id")
    count = 0
    for r in n.query(ideas_db):
        pr = r["properties"]
        idea = prop_text(pr, "아이디어").strip()
        if prop_text(pr, "상태") not in ("", IDEA_NEW) or not idea:
            continue
        try:
            d = parse_json(call_claude(IDEA_PROMPT.format(idea=idea, source=prop_text(pr, "출처") or "직접",
                                                          series=_series_table(plan_rows)), pl.cfg.caption))
        except Exception as e:  # noqa: BLE001 — 다음 크론에 다시
            log.warning("아이디어 정리 실패: %s", e)
            continue
        action = d.get("action")
        target = next((p for p in plan_rows if prop_text(p["properties"], "이름") == d.get("series")), None)
        if action == "series" and target and d.get("note"):
            rules = add_candidate(prop_text(target["properties"], "다듬은 규칙"), str(d["note"]))
            n.set_props(target["id"], {"다듬은 규칙": {"rich_text": rich(rules)}})
            target["properties"]["다듬은 규칙"] = {"type": "rich_text", "rich_text": rich(rules)}
            result, status = f"→ '{d['series']}' 다음 회차 후보: {d['note']}", IDEA_DONE
        elif action in ("new", "series") and (d.get("memo") or idea):
            memo = f"{d.get('memo') or idea}\n(아이디어 수신함: {idea})"
            n._req("POST", "/pages", json={"parent": {"database_id": plan_id}, "properties": {
                "이름": {"title": rich(idea[:60])}, "상태": {"select": {"name": "새 요청"}},
                "메모": {"rich_text": rich(memo[:1900])}}})
            result, status = "→ 🧭 시리즈 기획에 새 요청으로 (검토 대기 후 '사용')", IDEA_DONE
        else:
            result, status = f"보류: {d.get('reason') or '판단 못 함'}", IDEA_HOLD
        n.set_props(r["id"], {"상태": {"select": {"name": status}}, "결과": {"rich_text": rich(result[:1900])},
                              **({} if prop_text(pr, "출처") else {"출처": {"select": {"name": "직접"}}})})
        log.info("아이디어 정리: %s %s", idea[:30], result[:60])
        count += 1
    return count
