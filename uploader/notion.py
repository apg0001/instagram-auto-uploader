"""노션 데이터베이스를 게시물 목록·승인 화면으로 쓴다.

흐름: 초안 생성 → 노션에 '초안' 페이지 → 사람이 미리보기·캡션 수정 후 상태를 '승인'(또는 '반려')으로
→ 동기화가 승인 건을 게시하고 '게시됨' + 인스타 링크로 갱신.
"""
from __future__ import annotations

import re
from typing import Any

import requests

from . import themes
from .db import APPROVED, DRAFT, FAILED, PUBLISHED, REJECTED, Post

API = "https://api.notion.com/v1"
STATUS_COLORS = {DRAFT: "yellow", APPROVED: "blue", PUBLISHED: "green", REJECTED: "gray", FAILED: "red"}
KIND_COLORS = {"REELS": "purple", "CAROUSEL": "orange", "IMAGE": "brown", "STORIES": "pink"}
VIEWS_VERSION = "2026-03-11"  # 보기(view) API는 이 버전부터
TOPIC_OPTIONS = {t["ko"]: t["notion"] for t in themes.THEMES.values()}
CHUNK = 2000  # rich_text 한 덩어리 최대 길이


def page_id_from(url_or_id: str) -> str:
    m = re.search(r"([0-9a-f]{32})(?:\?|$)", url_or_id.replace("-", ""))
    if not m:
        raise ValueError(f"노션 페이지 id를 찾지 못함: {url_or_id}")
    return m.group(1)


def rich(text: str) -> list[dict]:
    return [{"type": "text", "text": {"content": text[i:i + CHUNK]}} for i in range(0, len(text), CHUNK)] or []


def plain(rt: list[dict]) -> str:
    return "".join(t.get("plain_text", "") for t in rt)


def iso_kst(ts: float) -> str:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    return datetime.fromtimestamp(ts, ZoneInfo("Asia/Seoul")).isoformat(timespec="minutes")


class Notion:
    def __init__(self, token: str, version: str):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Notion-Version": version,
                               "Content-Type": "application/json"})

    def _req(self, method: str, path: str, version: str | None = None, **kw) -> dict:
        headers = {"Notion-Version": version} if version else None
        r = self.s.request(method, f"{API}{path}", timeout=30, headers=headers, **kw)
        if r.status_code >= 400:
            raise RuntimeError(f"Notion {method} {path} → {r.status_code}: {r.text[:500]}")
        return r.json()

    # --- 설정 ---
    def create_database(self, parent_page: str, title: str = "인스타그램 게시물") -> str:
        def select(colors: dict[str, str]) -> dict:
            return {"select": {"options": [{"name": k, "color": v} for k, v in colors.items()]}}

        body = {
            "parent": {"type": "page_id", "page_id": page_id_from(parent_page)},
            "title": rich(title),
            "is_inline": True,
            "properties": {
                "제목": {"title": {}},
                "상태": select(STATUS_COLORS),
                "형식": select(KIND_COLORS),
                "소스": {"select": {}},
                "주제": select(TOPIC_OPTIONS),
                "캡션": {"rich_text": {}},
                "미디어": {"files": {}},
                "예약": {"date": {}},
                "인스타 링크": {"url": {}},
                "오류": {"rich_text": {}},
                "ID": {"rich_text": {}},
                "생성": {"created_time": {}},
            },
        }
        return self._req("POST", "/databases", json=body)["id"]

    # --- 게시물 ---
    def create_post(self, db_id: str, p: Post, preview_urls: list[str]) -> str:
        files = [{"name": f"{i + 1}_{u.rsplit('/', 1)[-1]}"[:100], "type": "external", "external": {"url": u}}
                 for i, u in enumerate(preview_urls)]
        children: list[dict] = [{
            "object": "block", "type": "callout",
            "callout": {"rich_text": rich("미리보기와 캡션을 확인하고 '상태'를 승인 또는 반려로 바꾸세요. "
                                          "캡션은 이 페이지의 '캡션' 속성을 고치면 그대로 게시됩니다."),
                        "icon": {"type": "emoji", "emoji": "✅"}},
        }]
        for m in p.media:
            kind = "video" if m["type"] == "video" else "image"
            children.append({"object": "block", "type": kind, kind: {"type": "external", "external": {"url": m["url"]}}})
        body = {
            "parent": {"database_id": db_id},
            "properties": {
                "제목": {"title": rich(p.title)},
                "상태": {"select": {"name": p.status}},
                "형식": {"select": {"name": p.kind}},
                "소스": {"select": {"name": p.source}},
                "주제": {"select": {"name": themes.ko(p.meta.get("topic_theme") or themes.topic_of(p.source))}},
                "캡션": {"rich_text": rich(p.caption)},
                "미디어": {"files": files},
                "ID": {"rich_text": rich(p.id)},
                **({"예약": {"date": {"start": iso_kst(p.scheduled_at)}}} if p.scheduled_at else {}),
            },
            "children": children,
        }
        return self._req("POST", "/pages", json=body)["id"]

    def read(self, page_id: str) -> dict[str, Any]:
        props = self._req("GET", f"/pages/{page_id}")["properties"]
        sched = (props.get("예약", {}).get("date") or {}).get("start")
        return {
            "status": (props["상태"].get("select") or {}).get("name"),
            "caption": plain(props["캡션"]["rich_text"]),
            "scheduled": sched,
        }

    def update(self, page_id: str, *, status: str | None = None, permalink: str | None = None,
               error: str | None = None, scheduled: str | None = None) -> None:
        props: dict[str, Any] = {}
        if scheduled:
            props["예약"] = {"date": {"start": scheduled}}
        if status:
            props["상태"] = {"select": {"name": status}}
        if permalink:
            props["인스타 링크"] = {"url": permalink}
        if error is not None:
            props["오류"] = {"rich_text": rich(error[:1900])}
        self._req("PATCH", f"/pages/{page_id}", json={"properties": props})

    # --- 범용 (대시보드·기획 DB) ---
    def query(self, db_id: str, flt: dict | None = None) -> list[dict]:
        out, cursor = [], None
        while True:
            body: dict[str, Any] = {"page_size": 100, **({"filter": flt} if flt else {})}
            if cursor:
                body["start_cursor"] = cursor
            r = self._req("POST", f"/databases/{db_id}/query", json=body)
            out += r["results"]
            if not r.get("has_more"):
                return out
            cursor = r["next_cursor"]

    def set_props(self, page_id: str, props: dict[str, Any]) -> None:
        self._req("PATCH", f"/pages/{page_id}", json={"properties": props})

    def add_properties(self, db_id: str, props: dict[str, Any]) -> None:
        self._req("PATCH", f"/databases/{db_id}", json={"properties": props})

    def create_db(self, parent_page: str, title: str, props: dict[str, Any], icon: str | None = None) -> str:
        body = {"parent": {"type": "page_id", "page_id": parent_page}, "title": rich(title), "is_inline": True,
                "properties": props, **({"icon": {"type": "emoji", "emoji": icon}} if icon else {})}
        return self._req("POST", "/databases", json=body)["id"]

    def database(self, db_id: str) -> dict:
        return self._req("GET", f"/databases/{db_id}", version=VIEWS_VERSION)

    def data_source(self, ds_id: str) -> dict:
        return self._req("GET", f"/data_sources/{ds_id}", version=VIEWS_VERSION)

    def views(self, db_id: str) -> list[dict]:
        r = self._req("GET", "/views", version=VIEWS_VERSION, params={"database_id": db_id})
        return [self._req("GET", f"/views/{v['id']}", version=VIEWS_VERSION) for v in r["results"]]

    def create_view(self, body: dict) -> dict:
        return self._req("POST", "/views", version=VIEWS_VERSION, json=body)

    def update_view(self, view_id: str, body: dict) -> dict:
        return self._req("PATCH", f"/views/{view_id}", version=VIEWS_VERSION, json=body)

    def children(self, block_id: str) -> list[dict]:
        return self._req("GET", f"/blocks/{block_id}/children", params={"page_size": 100})["results"]

    def append(self, block_id: str, blocks: list[dict], after: str | None = None) -> list[dict]:
        body: dict[str, Any] = {"children": blocks, **({"after": after} if after else {})}
        return self._req("PATCH", f"/blocks/{block_id}/children", json=body)["results"]


def prop_text(props: dict, name: str) -> str:
    """페이지 속성 값을 글자로 (title·rich_text·select·multi_select·date)."""
    v = props.get(name) or {}
    t = v.get("type")
    if t in ("title", "rich_text"):
        return plain(v[t])
    if t == "select":
        return (v["select"] or {}).get("name") or ""
    if t == "multi_select":
        return ",".join(o["name"] for o in v["multi_select"])
    if t == "date":
        return (v["date"] or {}).get("start") or ""
    return ""
