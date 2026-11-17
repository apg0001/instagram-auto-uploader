"""노션 데이터베이스를 게시물 목록·승인 화면으로 쓴다.

흐름: 초안 생성 → 노션에 '초안' 페이지 → 사람이 미리보기·캡션 수정 후 상태를 '승인'(또는 '반려')으로
→ 동기화가 승인 건을 게시하고 '게시됨' + 인스타 링크로 갱신.
"""
from __future__ import annotations

import re
from typing import Any

import requests

from .db import APPROVED, DRAFT, FAILED, PUBLISHED, REJECTED, Post

API = "https://api.notion.com/v1"
STATUS_COLORS = {DRAFT: "yellow", APPROVED: "blue", PUBLISHED: "green", REJECTED: "gray", FAILED: "red"}
KIND_COLORS = {"REELS": "purple", "CAROUSEL": "orange", "IMAGE": "brown", "STORIES": "pink"}
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


class Notion:
    def __init__(self, token: str, version: str):
        self.s = requests.Session()
        self.s.headers.update({"Authorization": f"Bearer {token}", "Notion-Version": version,
                               "Content-Type": "application/json"})

    def _req(self, method: str, path: str, **kw) -> dict:
        r = self.s.request(method, f"{API}{path}", timeout=30, **kw)
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
                "캡션": {"rich_text": rich(p.caption)},
                "미디어": {"files": files},
                "ID": {"rich_text": rich(p.id)},
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
               error: str | None = None) -> None:
        props: dict[str, Any] = {}
        if status:
            props["상태"] = {"select": {"name": status}}
        if permalink:
            props["인스타 링크"] = {"url": permalink}
        if error is not None:
            props["오류"] = {"rich_text": rich(error[:1900])}
        self._req("PATCH", f"/pages/{page_id}", json={"properties": props})
