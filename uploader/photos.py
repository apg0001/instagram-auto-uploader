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


def download(url: str) -> bytes:
    """사진 받기. 연달아 받으면 429가 나서 잠깐 쉬었다가 다시 (최대 3번)."""
    import time

    for wait in (3, 8, 20, None):
        r = requests.get(url, timeout=30)
        if r.status_code != 429 or wait is None:
            r.raise_for_status()
            return r.content
        time.sleep(wait)
    raise AssertionError("unreachable")


def relevant(hit: dict, query: str) -> bool:
    """태그에 검색어 단어가 하나라도 있어야 한다 (색 필터를 켜면 엉뚱한 사진이 섞여 나온다)."""
    tags = str(hit.get("tags", "")).lower()
    return any(w in tags for w in query.lower().split() if len(w) > 2)


def search(query: str, key: str, colors: str | None = None, n: int = 4) -> list[dict]:
    """후보 n장: 주제 색(colors)에 맞는 사진을 앞에, 그다음 색 필터 없는 사진. 세로 사진 우선."""
    params = {"key": key, "q": query[:100], "image_type": "photo", "safesearch": "true", "per_page": 30,
              "order": "popular", "min_width": SIZE[0]}
    out: list[dict] = []
    for color in ([colors] if colors else []) + [None]:
        for orient in ("vertical", "horizontal"):  # 세로 사진이 표지에 잘 맞는다, 없으면 가로를 잘라 쓴다
            extra = {"orientation": orient, **({"colors": color} if color else {})}
            r = requests.get(API, params={**params, **extra}, timeout=20)
            r.raise_for_status()
            seen = {h["id"] for h in out}
            out += [h for h in r.json().get("hits", [])
                    if usable(h) and relevant(h, query) and h.get("id") not in seen][: n - len(out)]
            if len(out) >= n:
                return out
    return out


REVIEW_PROMPT = """인스타그램 카드뉴스 표지 배경으로 쓸 사진을 고른다. 주제: {topic}
아래 후보 사진 파일을 Read 도구로 모두 열어 보고, 조건을 모두 만족하는 것 중 가장 어울리는 한 장을 골라라.
- 사람이 전혀 없어야 한다 (얼굴·몸·실루엣·손 포함, 멀리 작게 있어도 안 됨)
- 로고·상표·브랜드 이름·읽히는 글자가 눈에 띄면 안 된다
- 주제와 자연스럽게 어울리는 실사 사진 (AI가 만든 티가 나면 안 됨)
- 위에 흰 글씨가 얹히므로 지나치게 복잡하거나 밝은 사진은 피한다
없으면 null.
{files}
출력: JSON 하나만 {{"pick": 번호 또는 null, "why": "한 줄"}}"""


def review(hits: list[dict], topic: str, cfg: dict, work: Path) -> dict | None:
    """Claude가 후보 사진(작은 미리보기)을 직접 보고 고른다. 태그만으로는 사람·로고를 못 걸러서."""
    from .caption import call_claude, parse_json

    work.mkdir(parents=True, exist_ok=True)
    files = []
    for i, h in enumerate(hits, 1):
        f = work / f"candidate-{i}.jpg"
        f.write_bytes(download(h.get("webformatURL") or h["largeImageURL"]))
        files.append(f"{i}. {f.resolve()}")
    run = {**cfg, "command": list(cfg["command"]) + ["--allowedTools", "Read", "--add-dir", str(work.resolve())]}
    data = parse_json(call_claude(REVIEW_PROMPT.format(topic=topic, files="\n".join(files)), run))
    pick = data.get("pick")
    log.info("표지 사진 고르기: %s (%s)", pick, data.get("why"))
    if isinstance(pick, int) and 1 <= pick <= len(hits):
        return hits[pick - 1]
    return None


def fit(raw: bytes, out: Path) -> Path:
    img = ImageOps.fit(Image.open(BytesIO(raw)).convert("RGB"), SIZE, Image.LANCZOS, centering=(0.5, 0.45))
    img.save(out, "JPEG", quality=90)
    return out


def find(query: str | None, out_dir: Path, colors: str | None = None, topic: str = "",
         cfg: dict | None = None) -> Photo | None:
    """query(영어 검색어)에 맞는 사진을 받아 out_dir/cover-photo.jpg 로. 실패하면 None.
    colors: 주제 색과 어울리는 Pixabay 색 필터 (themes 의 photo).
    cfg(Claude 설정)를 주면 Claude가 후보를 직접 보고 고른다 (사람·로고가 찍힌 사진 거르기)."""
    key = os.environ.get("PIXABAY_API_KEY")
    if not query or not key:
        return None
    try:
        hits = search(query, key, colors)
        if not hits:
            log.info("표지 사진 없음: %s", query)
            return None
        h = review(hits, topic or query, cfg, out_dir / "photo-candidates") if cfg else hits[0]
        if not h:
            log.info("표지 사진: 어울리는 후보 없음 (%s)", query)
            return None
        out_dir.mkdir(parents=True, exist_ok=True)
        path = fit(download(h["largeImageURL"]), out_dir / "cover-photo.jpg")
        return Photo(path, str(h.get("user") or "Pixabay"), str(h.get("pageURL") or "https://pixabay.com"))
    except Exception as e:  # noqa: BLE001 — 사진이 없어도 표지는 만든다
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
