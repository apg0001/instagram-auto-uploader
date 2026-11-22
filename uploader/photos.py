"""캐러셀 표지 배경 사진 — Pixabay(무료·상업 이용 가능, 출처 표기는 선택이지만 늘 표기한다).

.env PIXABAY_API_KEY 가 있어야 한다. 키가 없거나 맞는 사진이 없으면 None → 사진 없는 기존 표지.
사람 얼굴·로고·상표는 Pixabay 라이선스가 보장하지 않으므로 태그로 거른다.
받은 사진은 1080x1350으로 잘라 저장한다 (핫링크하지 않음).
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image, ImageOps

log = logging.getLogger("uploader")

API = "https://pixabay.com/api/"
SIZE = (1080, 1350)
# 태그에 있으면 쓰지 않는다: 사람(초상권), 로고·브랜드(상표), AI 생성 이미지
AVOID = {"person", "people", "man", "woman", "men", "women", "girl", "boy", "child", "children", "kid", "face",
         "portrait", "model", "baby", "human", "selfie", "crowd", "logo", "brand", "trademark", "ai generated",
         "ai-generated", "generated", "midjourney"}


@dataclass
class Photo:
    path: Path
    author: str
    page: str

    @property
    def credit(self) -> str:
        return f"사진: {self.author} / Pixabay"


def usable(hit: dict) -> bool:
    tags = {t.strip().lower() for t in str(hit.get("tags", "")).split(",")}
    words = {w for t in tags for w in t.split()}
    if tags & AVOID or any(w in t for t in tags for w in ("person", "people", "logo")) \
            or any(w.endswith(("man", "men")) for w in words):  # businessman, women …
        return False
    return int(hit.get("imageWidth", 0)) >= SIZE[0] and int(hit.get("imageHeight", 0)) >= 900


def search(query: str, key: str) -> list[dict]:
    params = {"key": key, "q": query[:100], "image_type": "photo", "safesearch": "true", "per_page": 30,
              "order": "popular", "min_width": SIZE[0]}
    hits: list[dict] = []
    for orient in ("vertical", "horizontal"):  # 세로 사진이 표지에 잘 맞는다, 없으면 가로를 잘라 쓴다
        r = requests.get(API, params={**params, "orientation": orient}, timeout=20)
        r.raise_for_status()
        hits += [h for h in r.json().get("hits", []) if usable(h)]
        if hits:
            break
    return hits


def fit(raw: bytes, out: Path) -> Path:
    img = ImageOps.fit(Image.open(BytesIO(raw)).convert("RGB"), SIZE, Image.LANCZOS, centering=(0.5, 0.45))
    img.save(out, "JPEG", quality=90)
    return out


def find(query: str | None, out_dir: Path) -> Photo | None:
    """query(영어 검색어)에 맞는 사진을 받아 out_dir/cover-photo.jpg 로. 실패하면 None."""
    key = os.environ.get("PIXABAY_API_KEY")
    if not query or not key:
        return None
    try:
        hits = search(query, key)
        if not hits:
            log.info("표지 사진 없음: %s", query)
            return None
        h = hits[0]
        r = requests.get(h["largeImageURL"], timeout=30)
        r.raise_for_status()
        out_dir.mkdir(parents=True, exist_ok=True)
        path = fit(r.content, out_dir / "cover-photo.jpg")
        return Photo(path, str(h.get("user") or "Pixabay"), str(h.get("pageURL") or "https://pixabay.com"))
    except (requests.RequestException, OSError, ValueError) as e:
        log.warning("표지 사진 실패(%s): %s", query, e)
        return None


def add_credit(caption: str, photo: Photo) -> str:
    """캡션의 해시태그 줄 바로 앞에 사진 출처를 넣는다."""
    line = f"표지 {photo.credit}"  # 캡션 이모지 수 제한이 있어 글로만
    lines = caption.rstrip().split("\n")
    i = len(lines)
    while i > 0 and lines[i - 1].strip().startswith("#"):
        i -= 1
    if i == len(lines):
        return f"{caption.rstrip()}\n\n{line}"
    return "\n".join(lines[:i]).rstrip() + f"\n{line}\n\n" + "\n".join(lines[i:])
