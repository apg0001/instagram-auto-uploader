"""Make(make.com) 웹훅으로 인스타에 게시한다 — Meta 개발자 계정 없이 쓰는 경로.

승인된 게시물을 JSON으로 웹훅에 보내면 Make 시나리오가 'Instagram for Business' 모듈로 게시하고,
'Webhook response' 모듈로 {"id": ..., "permalink": ...} 를 돌려준다. 설정은 docs/make.md 참고.
"""
from __future__ import annotations

from typing import Any

import requests

from .instagram import InstagramError


def payload(kind: str, media: list[dict[str, str]], caption: str, cover_url: str | None, post_id: str) -> dict[str, Any]:
    """Make에서 매핑하기 쉽게 평평한 필드와 배열을 함께 보낸다."""
    images = [m["url"] for m in media if m["type"] != "video"]
    videos = [m["url"] for m in media if m["type"] == "video"]
    return {
        "post_id": post_id,
        "kind": kind,                      # IMAGE | CAROUSEL | REELS | STORIES — 라우터 필터 기준
        "caption": caption,
        "image_url": images[0] if images else None,
        "video_url": videos[0] if videos else None,
        "cover_url": cover_url,
        "media": [_item(m) for m in media],
        "count": len(media),
    }


def _item(m: dict[str, str]) -> dict[str, str]:
    """캐러셀 항목. Make 'Files' 항목의 내부 키 이름을 몰라 흔한 이름을 모두 넣는다 (남는 키는 무시됨)."""
    video = m["type"] == "video"
    t = "VIDEO" if video else "IMAGE"
    return {"type": t, "media_type": t, "mediaType": t, "url": m["url"],
            ("video_url" if video else "image_url"): m["url"]}


class MakeWebhook:
    def __init__(self, url: str, timeout: int = 300):
        self.url = url
        self.timeout = timeout
        self._permalinks: dict[str, str | None] = {}

    def publish(self, kind: str, media: list[dict[str, str]], caption: str, cover_url: str | None = None,
                post_id: str = "") -> str:
        if kind == "CAROUSEL" and not 2 <= len(media) <= 10:
            raise InstagramError(f"캐러셀은 2~10장이어야 함 (현재 {len(media)})")
        try:
            r = requests.post(self.url, json=payload(kind, media, caption, cover_url, post_id), timeout=self.timeout)
        except requests.Timeout as e:
            # 게시됐는지 알 수 없다 — 재시도하면 중복 게시될 수 있으므로 실패로 남기고 사람이 확인한다.
            raise InstagramError("Make 응답 시간 초과 — 인스타에서 실제 게시 여부를 확인할 것 (자동 재시도 안 함)") from e
        if r.status_code >= 400:
            raise InstagramError(f"Make 웹훅 {r.status_code}: {r.text[:300]}")
        try:
            data = r.json()
        except ValueError:
            data = {}
        if not isinstance(data, dict) or not data.get("id"):
            # 'Accepted' 만 왔다 = Webhook response 모듈이 없음 → 결과를 모른 채 게시됐다고 볼 수 없다.
            raise InstagramError(f"Make 응답에 id 없음 (Webhook response 모듈 확인): {r.text[:200]}")
        media_id = str(data["id"])
        self._permalinks[media_id] = data.get("permalink")
        return media_id

    def permalink(self, media_id: str) -> str | None:
        return self._permalinks.get(media_id)

    def ask(self, action: str, data: dict[str, Any]) -> dict[str, Any]:
        """게시가 아닌 요청 (예: action="engagement" → 좋아요·댓글). 시나리오 라우터가 'action' 으로 나눈다.
        게시 요청에는 action 이 없으므로, 게시 경로 필터는 'action 이 비어 있음' 이어야 한다 (docs/make.md)."""
        r = requests.post(self.url, json={"action": action, **data}, timeout=self.timeout)
        if r.status_code >= 400:
            raise InstagramError(f"Make 웹훅 {r.status_code}: {r.text[:300]}")
        try:
            out = r.json()
        except ValueError as e:
            raise InstagramError(f"Make 응답이 JSON 아님 (Webhook response 확인): {r.text[:200]}") from e
        if not isinstance(out, dict):
            raise InstagramError(f"Make 응답 형식 이상: {r.text[:200]}")
        return out

    def engagement(self, media_id: str) -> dict[str, Any]:
        """게시물 하나의 좋아요·댓글 수와 댓글 글 (docs/make.md 5번 경로)."""
        return self.ask("engagement", {"media_id": media_id})

