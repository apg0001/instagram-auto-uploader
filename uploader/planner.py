"""노션 '콘텐츠 기획' DB — 사람이 주제·주기를 대충 적으면 Claude가 다듬어 정기 게시물로 돌린다.

흐름 (행 하나 = 정기 시리즈 하나 또는 한 번짜리 주제):
  새 요청  : 사람이 '메모'에 대충 쓴다 (예: "깃허브 인기 레포 top10 매주 일요일 아침")
  → 검토 대기 : Claude가 이름·주제·주기·요일·시각·'다듬은 규칙'(조사·작성 지침)을 채우고, 걱정되는 점은 '확인할 점'에
  → 사용      : 사람이 읽어 보고 상태를 바꾸면, '다음 생성' 시각마다 초안을 만들어 게시물 DB에 올린다 (게시는 늘 사람 승인)
  일시중지 / 완료(한 번짜리 생성 후) / 오류
메모를 고쳐 다시 다듬고 싶으면 상태를 '새 요청'으로 되돌리면 된다.

setup_dashboard 는 게시물 DB에 '주제' 속성과 보기(확인할 것·예약 달력·게시됨·주제별·반려)를 만들고,
같은 페이지에 기획 DB와 사용법을 붙인다. 여러 번 실행해도 이미 있는 것은 건너뛴다.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from . import themes
from .caption import call_claude, parse_json
from .notion import TOPIC_OPTIONS, prop_text, rich

if TYPE_CHECKING:
    from .pipeline import Pipeline

log = logging.getLogger("uploader")

NEW, REVIEW, ACTIVE, PAUSED, DONE, ERROR = "새 요청", "검토 대기", "사용", "일시중지", "완료", "오류"
PLAN_STATUS = {NEW: "yellow", REVIEW: "orange", ACTIVE: "green", PAUSED: "gray", DONE: "blue", ERROR: "red"}
CADENCES = ["한 번", "매일", "매주", "격주", "매월"]
DAYS = ["월", "화", "수", "목", "금", "토", "일"]
DEFAULT_TIME = "10:00"

REFINE_PROMPT = """너는 개발자 개인 인스타그램 계정의 콘텐츠 기획 담당이다. 계정 주인이 노션에 대충 적은 메모를
정기 카드뉴스 시리즈의 기획으로 다듬어라. 실제 원고는 나중에 다른 Claude가 이 '규칙'만 보고 웹 검색으로 조사해 쓴다.

# 계정
- 주인: 대학원생 개발자. 직접 만든 게임(랩워즈)·도구(Agent Office), 증시 데이터 요약, 테크·AI·개발자 커리어 소식.
- 주제 분류: 게임 / 프로젝트 / 증시 / 테크·AI / 커리어

# 계정 규칙 (기획에 반드시 반영)
- 사실은 출처가 확인된 것만. 공식 발표·공개 통계·API 데이터 우선, 마지막 장에 출처.
- 투자 권유·종목 추천·매수/매도 표현 금지 (증시는 정보 제공 + 면책 문구).
- 개인 실명·사생활, 확인 안 된 루머, 비방 금지. 뉴스 사진 등 남의 이미지 쓰지 않음(카드는 직접 디자인).
- 해시태그 4~5개, 첫 줄에 검색 키워드가 담긴 훅.
- 카드 4~8장, 한 장에 한 메시지.

# 메모
이름: {name}
주제(비어 있으면 네가 고름): {topic}
주기·요일·시각(비어 있으면 메모에서 읽거나 적당히): {cadence} / {days} / {time}
메모:
{memo}

# 출력 (JSON 하나만)
{{"name": "짧은 시리즈 이름(예: 주간 깃허브 인기 레포 Top 10)",
  "topic": "게임|프로젝트|증시|테크·AI|커리어",
  "cadence": "한 번|매일|매주|격주|매월",
  "days": ["일"],
  "time": "HH:MM (한국 시간, 사람들이 많이 보는 시간대 고려)",
  "rules": "원고 작성 지침. 다룰 범위·기간, 데이터를 어디서 어떻게 모을지(구체적 사이트·API·기준), 장별 구성, 톤, 피할 것. 10줄 안팎.",
  "concerns": ["사람이 확인해야 할 점 (규칙 위반 소지, 데이터 출처가 불확실, 메모가 모호 등). 없으면 빈 배열"]}}"""


# ---------------------------------------------------------------- 일정 계산

def _hm(text: str) -> tuple[int, int]:
    try:
        h, m = (int(x) for x in (text or DEFAULT_TIME).strip().split(":")[:2])
        if 0 <= h < 24 and 0 <= m < 60:
            return h, m
    except ValueError:
        pass
    return _hm(DEFAULT_TIME)


def next_run(cadence: str, days: list[str], time_text: str, after: datetime, last: datetime | None = None) -> datetime | None:
    """after 이후 첫 생성 시각. 한 번짜리는 아직 안 만들었으면 다음 시각, 만들었으면 None.
    매주·격주는 요일(없으면 after의 요일), 매월은 1일. 격주는 마지막 생성 후 13일 이상 지난 날부터."""
    h, m = _hm(time_text)
    if cadence == "한 번" and last:
        return None
    wd = sorted({DAYS.index(d) for d in days if d in DAYS}) or [after.weekday()]
    t = after.replace(hour=h, minute=m, second=0, microsecond=0)
    for i in range(0, 62):
        c = t + timedelta(days=i)
        if c <= after:
            continue
        if cadence in ("한 번", "매일"):
            return c
        if cadence == "매월":
            if c.day == 1:
                return c
            continue
        if c.weekday() not in wd:
            continue
        if cadence == "격주" and last and (c - last) < timedelta(days=13):
            continue
        return c
    return None


# ---------------------------------------------------------------- 노션 스키마

def _select(opts: dict[str, str] | list[str]) -> dict:
    items = opts.items() if isinstance(opts, dict) else ((o, "default") for o in opts)
    return {"select": {"options": [{"name": k, "color": c} for k, c in items]}}


PLAN_PROPS = {
    "이름": {"title": {}},
    "상태": _select(PLAN_STATUS),
    "메모": {"rich_text": {}},
    "주제": _select(TOPIC_OPTIONS),
    "주기": _select(CADENCES),
    "요일": {"multi_select": {"options": [{"name": d} for d in DAYS]}},
    "시각": {"rich_text": {}},
    "다듬은 규칙": {"rich_text": {}},
    "확인할 점": {"rich_text": {}},
    "다음 생성": {"date": {}},
    "마지막 생성": {"date": {}},
    "오류": {"rich_text": {}},
}

GUIDE = [
    ("heading_2", "🗓 콘텐츠 기획"),
    ("callout", "① 아래 표에 새 줄을 만들고 '메모'에 하고 싶은 걸 대충 적으세요 (예: 깃허브 인기 레포 top10 매주 일요일 아침). "
                "상태는 '새 요청'.\n② 몇 분 안에 Claude가 이름·주제·주기·시각·'다듬은 규칙'을 채우고 '검토 대기'로 바꿉니다. "
                "'확인할 점'이 있으면 읽어 보세요.\n③ 괜찮으면 상태를 '사용'으로. '다음 생성' 시각마다 초안이 위 게시물 표에 올라옵니다 "
                "(게시는 늘 승인 후). 메모를 고쳐 다시 다듬으려면 '새 요청'으로, 멈추려면 '일시중지'."),
]


def _block(kind: str, text: str) -> dict:
    if kind == "callout":
        return {"object": "block", "type": "callout",
                "callout": {"rich_text": rich(text), "icon": {"type": "emoji", "emoji": "✍️"}, "color": "gray_background"}}
    return {"object": "block", "type": kind, kind: {"rich_text": rich(text)}}


def _view_specs(p: dict[str, str]) -> list[dict]:
    """게시물 DB 보기. p: 속성 이름 → id."""
    status = "상태"
    return [
        {"name": "📥 확인할 것", "type": "table",
         "filter": {"or": [{"property": status, "select": {"equals": s}} for s in ("초안", "승인", "실패")]},
         "sorts": [{"property": "생성", "direction": "descending"}],
         "configuration": {"type": "table", "wrap_cells": True, "properties": [
             {"property_id": p[k], "visible": True} for k in ("제목", "상태", "주제", "형식", "예약", "오류")] + [
             {"property_id": p[k], "visible": False} for k in ("캡션", "미디어", "ID", "소스", "인스타 링크")]}},
        {"name": "📅 예약 달력", "type": "calendar", "configuration": {"type": "calendar", "date_property_id": p["예약"]}},
        {"name": "🖼 게시됨", "type": "gallery", "filter": {"property": status, "select": {"equals": "게시됨"}},
         "sorts": [{"property": "생성", "direction": "descending"}],
         "configuration": {"type": "gallery", "cover": {"type": "property", "property_id": p["미디어"]},
                           "cover_size": "medium", "cover_aspect": "cover"}},
        {"name": "🗂 주제별", "type": "board",
         "filter": {"property": status, "select": {"does_not_equal": "반려"}},
         "configuration": {"type": "board", "group_by": {"type": "select", "property_id": p["주제"],
                                                          "sort": {"type": "manual"}, "hide_empty_groups": True},
                           "cover": {"type": "property", "property_id": p["미디어"]}, "cover_size": "small"}},
        {"name": "🗑 반려", "type": "table", "filter": {"property": status, "select": {"equals": "반려"}},
         "sorts": [{"property": "생성", "direction": "descending"}]},
    ]


def setup_dashboard(pl: Pipeline) -> list[str]:
    n = pl.notion
    if not n:
        raise RuntimeError("NOTION_TOKEN 이 없음")
    db_id = pl.db.kv_get("notion_db_id")
    if not db_id:
        raise RuntimeError("게시물 DB가 없음 → `notion-setup` 먼저")
    done: list[str] = []
    db = n.database(db_id)
    ds_id = db["data_sources"][0]["id"]
    parent = db["parent"]["page_id"]

    # 1) 게시물 DB에 '주제' 속성 + 기존 행 채우기
    props = n.data_source(ds_id)["properties"]
    if "주제" not in props:
        n.add_properties(db_id, {"주제": _select(TOPIC_OPTIONS)})
        props = n.data_source(ds_id)["properties"]
        done.append("게시물 DB에 '주제' 속성 추가")
    filled = 0
    for page in n.query(db_id):
        if prop_text(page["properties"], "주제"):
            continue
        src = prop_text(page["properties"], "소스")
        n.set_props(page["id"], {"주제": {"select": {"name": themes.ko(themes.topic_of(src))}}})
        filled += 1
    if filled:
        done.append(f"기존 게시물 {filled}개 주제 채움")

    # 2) 보기
    pid = {k: v["id"] for k, v in props.items()}
    have = {v["name"]: v for v in n.views(db_id)}
    default = have.get("Default view")
    if default:
        n.update_view(default["id"], {"name": "📋 전체", "sorts": [{"property": "생성", "direction": "descending"}]})
        done.append("기본 보기 → 📋 전체 (최신순)")
    for spec in _view_specs(pid):
        if spec["name"] in have:
            continue
        try:
            n.create_view({"database_id": db_id, "data_source_id": ds_id, **spec})
            done.append(f"보기 {spec['name']}")
        except RuntimeError as e:  # 보기 하나가 실패해도 나머지는 만든다
            log.warning("보기 %s 실패: %s", spec["name"], e)
            done.append(f"보기 {spec['name']} 실패 (로그 확인)")

    # 3) 기획 DB + 사용법 (페이지 맨 아래)
    if not pl.db.kv_get("notion_plan_db_id"):
        n.append(parent, [_block(k, t) for k, t in GUIDE])
        plan_id = n.create_db(parent, "콘텐츠 기획", PLAN_PROPS, icon="🗓")
        pl.db.kv_set("notion_plan_db_id", plan_id)
        done.append("콘텐츠 기획 DB + 사용법")
        _plan_views(pl, plan_id)
    return done


def _plan_views(pl: Pipeline, plan_id: str) -> None:
    n = pl.notion
    db = n.database(plan_id)
    ds_id = db["data_sources"][0]["id"]
    p = {k: v["id"] for k, v in n.data_source(ds_id)["properties"].items()}
    for v in n.views(plan_id):
        n.update_view(v["id"], {"name": "전체", "configuration": {"type": "table", "wrap_cells": True, "properties": [
            {"property_id": p[k], "visible": True} for k in
            ("이름", "상태", "메모", "주제", "주기", "요일", "시각", "다음 생성", "확인할 점", "다듬은 규칙", "마지막 생성", "오류")]}})
    try:
        n.create_view({"database_id": plan_id, "data_source_id": ds_id, "name": "상태별", "type": "board",
                       "configuration": {"type": "board", "group_by": {"type": "select", "property_id": p["상태"],
                                                                        "sort": {"type": "manual"}}}})
    except RuntimeError as e:
        log.warning("기획 보기 실패: %s", e)


# ---------------------------------------------------------------- 실행

def _date(dt: datetime | None) -> dict:
    return {"date": {"start": dt.isoformat(timespec="minutes")} if dt else None}


def refine(pl: Pipeline, page: dict) -> None:
    pr = page["properties"]
    memo = prop_text(pr, "메모") or prop_text(pr, "이름")
    if not memo.strip():
        pl.notion.set_props(page["id"], {"상태": {"select": {"name": ERROR}}, "오류": {"rich_text": rich("메모가 비어 있음")}})
        return
    prompt = REFINE_PROMPT.format(name=prop_text(pr, "이름"), topic=prop_text(pr, "주제"), cadence=prop_text(pr, "주기"),
                                  days=prop_text(pr, "요일"), time=prop_text(pr, "시각"), memo=memo)
    d = parse_json(call_claude(prompt, pl.cfg.caption))
    topic = d.get("topic") if d.get("topic") in TOPIC_OPTIONS else "테크·AI"
    cadence = d.get("cadence") if d.get("cadence") in CADENCES else "한 번"
    days = [x for x in d.get("days") or [] if x in DAYS]
    hh, mm = _hm(d.get("time") or "")
    time_text = f"{hh:02d}:{mm:02d}"
    nxt = next_run(cadence, days, time_text, datetime.now(pl.tz))
    concerns = "\n".join(f"• {c}" for c in d.get("concerns") or [])
    pl.notion.set_props(page["id"], {
        "이름": {"title": rich(str(d.get("name") or memo[:40]))},
        "상태": {"select": {"name": REVIEW}},
        "주제": {"select": {"name": topic}},
        "주기": {"select": {"name": cadence}},
        "요일": {"multi_select": [{"name": x} for x in days]},
        "시각": {"rich_text": rich(time_text)},
        "다듬은 규칙": {"rich_text": rich(str(d.get("rules") or "")[:1900])},
        "확인할 점": {"rich_text": rich(concerns or "없음")},
        "다음 생성": _date(nxt),
        "오류": {"rich_text": []},
    })
    log.info("기획 다듬음: %s", d.get("name"))


def _parse_dt(text: str, pl: Pipeline) -> datetime | None:
    if not text:
        return None
    dt = datetime.fromisoformat(text)
    if len(text) == 10:  # 날짜만 → 시각은 기본값
        dt = dt.replace(hour=_hm(DEFAULT_TIME)[0])
    return dt if dt.tzinfo else dt.replace(tzinfo=pl.tz)


def run_due(pl: Pipeline, page: dict, now: datetime) -> bool:
    """'사용' 행의 생성 시각이 지났으면 초안을 하나 만든다. 만들었으면 True."""
    pr = page["properties"]
    cadence, days, time_text = prop_text(pr, "주기") or "한 번", prop_text(pr, "요일").split(","), prop_text(pr, "시각")
    last = _parse_dt(prop_text(pr, "마지막 생성"), pl)
    due = _parse_dt(prop_text(pr, "다음 생성"), pl)
    if due is None:
        due = next_run(cadence, days, time_text, now - timedelta(minutes=1), last)
        if due is None:
            pl.notion.set_props(page["id"], {"상태": {"select": {"name": DONE}}})
            return False
        pl.notion.set_props(page["id"], {"다음 생성": _date(due)})
    if due > now:
        return False
    name = prop_text(pr, "이름")
    brief = f"{name}\n\n{prop_text(pr, '다듬은 규칙') or prop_text(pr, '메모')}"
    topic = themes.from_ko(prop_text(pr, "주제")) or "tech"
    try:
        pl.generate_topic(brief, source=f"plan-{topic}", topic_theme=topic, title=name)
    except Exception as e:  # noqa: BLE001 — 실패해도 다음 주기는 계속
        log.exception("기획 생성 실패 %s", name)
        pl.notion.set_props(page["id"], {"오류": {"rich_text": rich(f"{now:%m/%d %H:%M} 생성 실패: {e}"[:1900])}})
    nxt = next_run(cadence, days, time_text, now, now)
    pl.notion.set_props(page["id"], {
        "마지막 생성": _date(now), "다음 생성": _date(nxt),
        **({"상태": {"select": {"name": DONE}}} if nxt is None else {}),
    })
    return True


def run(pl: Pipeline) -> None:
    """기획 DB 처리: 새 요청은 다듬고, 때가 된 '사용' 행은 초안을 만든다 (한 번에 최대 1개 — 파이 전력 한계)."""
    plan_id = pl.db.kv_get("notion_plan_db_id")
    if not pl.notion or not plan_id:
        return
    rows = pl.notion.query(plan_id)
    for page in rows:
        if prop_text(page["properties"], "상태") == NEW:
            try:
                refine(pl, page)
            except Exception as e:  # noqa: BLE001
                log.exception("기획 다듬기 실패")
                pl.notion.set_props(page["id"], {"상태": {"select": {"name": ERROR}},
                                                 "오류": {"rich_text": rich(str(e)[:1900])}})
    now = datetime.now(pl.tz)
    for page in rows:
        if prop_text(page["properties"], "상태") == ACTIVE and run_due(pl, page, now):
            break
