"""큰 주제별 카드 색. 피드에서 색만 봐도 무슨 게시물인지 알 수 있게 주제마다 한 벌씩 고정한다.

- bg/ink/sub/card: PIL로 그리는 카드·릴스 오버레이 (RGB)
- accent: 상단 띠·칩 색
- css: HTML 카드 템플릿(base.html.j2)의 CSS 변수
"""
from __future__ import annotations

from typing import Any


def _hex(rgb: tuple[int, int, int]) -> str:
    return "#{:02X}{:02X}{:02X}".format(*rgb)


THEMES: dict[str, dict[str, Any]] = {
    # 게임(랩워즈): 따뜻한 크림 + 주황
    "game": {"ko": "게임", "notion": "orange", "label": "GAME", "bg": (243, 235, 221), "ink": (40, 33, 45), "sub": (110, 98, 112),
             "card": (255, 251, 244), "accent": (255, 112, 67)},
    # 내가 만든 프로젝트·오픈소스(Agent Office 등): 남색 + 노랑 (제품 화면 색)
    "project": {"ko": "프로젝트", "notion": "blue", "label": "PROJECT", "bg": (28, 31, 51), "ink": (242, 240, 230), "sub": (154, 160, 191),
                "card": (38, 42, 68), "accent": (245, 197, 66)},
    # 증시: 차가운 회색 종이 + 먹색 (상승 빨강·하락 파랑과 겹치지 않게 무채색)
    "market": {"ko": "증시", "notion": "gray", "label": "MARKET", "bg": (238, 240, 243), "ink": (32, 36, 43), "sub": (110, 119, 135),
               "card": (255, 255, 255), "accent": (32, 36, 43)},
    # 테크·AI 소식: 연보라 + 보라
    "tech": {"ko": "테크·AI", "notion": "purple", "label": "TECH · AI", "bg": (240, 237, 252), "ink": (30, 26, 51), "sub": (104, 98, 133),
             "card": (255, 255, 255), "accent": (109, 74, 255)},
    # 개발자 커리어(취업·연봉·채용): 민트 + 초록
    "career": {"ko": "커리어", "notion": "green", "label": "CAREER", "bg": (232, 245, 239), "ink": (22, 42, 35), "sub": (92, 117, 107),
               "card": (255, 255, 255), "accent": (16, 163, 127)},
}

# 소스 이름(앞부분) → 주제. config의 소스에 topic: 을 적으면 그쪽이 우선.
SOURCE_TOPIC = {"lab-wars": "game", "agent-office": "project", "market": "market", "topic": "tech",
                "tech": "tech", "career": "career"}


def ko(topic: str) -> str:
    return get(topic)["ko"]


def from_ko(name: str | None) -> str | None:
    return next((k for k, t in THEMES.items() if t["ko"] == name), None)


def topic_of(source: str, src_cfg: dict[str, Any] | None = None) -> str:
    if src_cfg and src_cfg.get("topic") in THEMES:
        return src_cfg["topic"]
    if source.startswith("plan-") and source[5:] in THEMES:  # 노션 기획 DB에서 만든 게시물
        return source[5:]
    for prefix, topic in SOURCE_TOPIC.items():
        if source == prefix or source.startswith((prefix + ":", prefix + "-")):
            return topic
    return "tech"


def get(topic: str) -> dict[str, Any]:
    return THEMES.get(topic, THEMES["tech"])


def css(topic: str) -> dict[str, str]:
    """HTML 카드용 CSS 변수. 증시는 상승·하락 색을 기본(빨강·파랑) 그대로 둔다."""
    t = get(topic)
    v = {"paper": _hex(t["bg"]), "surface": _hex(t["card"]), "ink": _hex(t["ink"]), "muted": _hex(t["sub"]),
         "accent": _hex(t["accent"])}
    if topic == "project":  # 어두운 바탕에서 빨강·파랑은 안 보여서 제품 화면 색을 쓴다
        v.update({"rule": "#3A3F63", "up": "#F5C542", "down": "#7FA7FF"})
    return v
