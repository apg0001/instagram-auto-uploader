"""Instagram API with Instagram Login 으로 게시한다.

컨테이너 생성 → 처리 완료(FINISHED) 대기 → media_publish → 퍼머링크 조회.
문서: https://developers.facebook.com/docs/instagram-platform/content-publishing
"""
from __future__ import annotations

import time
from typing import Any

import requests

GRAPH = "https://graph.instagram.com"


class InstagramError(RuntimeError):
    pass


class Instagram:
    def __init__(self, user_id: str, token: str, version: str = "v24.0"):
        self.user_id = user_id
        self.token = token
        self.base = f"{GRAPH}/{version}"

    def _call(self, method: str, path: str, **params: Any) -> dict:
        params["access_token"] = self.token
        r = requests.request(method, f"{self.base}/{path}", params=params if method == "GET" else None,
                             data=params if method != "GET" else None, timeout=60)
        data = r.json() if r.content else {}
        if r.status_code >= 400 or "error" in data:
            raise InstagramError(f"{method} {path} → {r.status_code}: {data.get('error', data)}")
        return data

    # --- 컨테이너 ---
    def _container(self, **params: Any) -> str:
        return self._call("POST", f"{self.user_id}/media", **params)["id"]

    def wait_ready(self, container_id: str, timeout: int = 900) -> None:
        deadline = time.time() + timeout
        while True:
            st = self._call("GET", container_id, fields="status_code,status")
            code = st.get("status_code")
            if code == "FINISHED":
                return
            if code in ("ERROR", "EXPIRED"):
                raise InstagramError(f"컨테이너 {container_id} 처리 실패: {st}")
            if time.time() > deadline:
                raise InstagramError(f"컨테이너 {container_id} 처리 시간 초과: {st}")
            time.sleep(10)

    def publish(self, kind: str, media: list[dict[str, str]], caption: str, cover_url: str | None = None) -> str:
        """media: [{"url": ..., "type": "image"|"video"}]. 게시된 미디어 id를 돌려준다."""
        if kind == "REELS":
            params: dict[str, Any] = {"media_type": "REELS", "video_url": media[0]["url"], "caption": caption,
                                      "share_to_feed": "true"}
            if cover_url:
                params["cover_url"] = cover_url
            cid = self._container(**params)
        elif kind == "IMAGE":
            cid = self._container(image_url=media[0]["url"], caption=caption)
        elif kind == "STORIES":
            m = media[0]
            key = "video_url" if m["type"] == "video" else "image_url"
            cid = self._container(media_type="STORIES", **{key: m["url"]})
        elif kind == "CAROUSEL":
            if not 2 <= len(media) <= 10:
                raise InstagramError(f"캐러셀은 2~10장이어야 함 (현재 {len(media)})")
            children = []
            for m in media:
                if m["type"] == "video":
                    children.append(self._container(media_type="VIDEO", video_url=m["url"], is_carousel_item="true"))
                else:
                    children.append(self._container(image_url=m["url"], is_carousel_item="true"))
            for c in children:
                self.wait_ready(c)
            cid = self._container(media_type="CAROUSEL", children=",".join(children), caption=caption)
        else:
            raise InstagramError(f"모르는 형식: {kind}")
        self.wait_ready(cid)
        return self._call("POST", f"{self.user_id}/media_publish", creation_id=cid)["id"]

    def permalink(self, media_id: str) -> str | None:
        return self._call("GET", media_id, fields="permalink").get("permalink")

    def publishing_limit(self) -> dict:
        return self._call("GET", f"{self.user_id}/content_publishing_limit", fields="quota_usage,config")

    def refresh_token(self) -> tuple[str, int]:
        """장기 토큰(60일)을 갱신한다. 발급 24시간 이후부터 가능. (새 토큰, 남은 초)"""
        r = requests.get(f"{GRAPH}/refresh_access_token",
                         params={"grant_type": "ig_refresh_token", "access_token": self.token}, timeout=30)
        data = r.json()
        if r.status_code >= 400 or "access_token" not in data:
            raise InstagramError(f"토큰 갱신 실패: {data}")
        self.token = data["access_token"]
        return self.token, int(data.get("expires_in", 0))
