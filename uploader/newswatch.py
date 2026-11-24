"""AI·테크 속보 감시 — 큰 소식은 바로 짧은 속보 캐러셀로, 나머지는 브리핑·주간 뉴스 후보로.

매시간(cron) 공식 블로그·릴리스 피드·테크 뉴스·해커뉴스 상위 글을 훑어 새 글만 Claude에게 보인다.
- 점수 8 이상(새 대형 모델·주요 제품 출시, 클로드·클로드 코드 주요 기능, 대형 인수·사고 등) → 속보 캐러셀 생성,
  자동 승인 설정이면 바로 게시. 하루 한 편, 밤(23~07시)에는 아침 8:30 예약.
- 점수 6~7 → news_backlog 에 쌓아 '주 2회 신규 기술 브리핑'·'주간 AI 뉴스 정리' 원고 지침에 붙인다.
처음 실행할 때는 지금 떠 있는 글을 '본 것'으로만 기록한다 (옛날 글로 속보를 내지 않게).
"""
from __future__ import annotations

import hashlib
import html
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from typing import TYPE_CHECKING, Any

import requests

from .caption import call_claude, parse_json

if TYPE_CHECKING:
    from .pipeline import Pipeline

log = logging.getLogger("uploader")

UA = {"User-Agent": "Mozilla/5.0 (X11; Linux aarch64) news-watch/1"}
# (이름, 주소, 형식) — 형식: feed(RSS·Atom) | anthropic(뉴스 페이지) | hn(해커뉴스 상위)
FEEDS = [
    ("Anthropic", "https://www.anthropic.com/news", "anthropic"),
    ("Claude Code 릴리스", "https://github.com/anthropics/claude-code/releases.atom", "feed"),
    ("OpenAI", "https://openai.com/news/rss.xml", "feed"),
    ("Google AI", "https://blog.google/technology/ai/rss/", "feed"),
    ("Google DeepMind", "https://deepmind.google/blog/rss.xml", "feed"),
    ("NVIDIA", "https://blogs.nvidia.com/feed/", "feed"),
    ("Hugging Face", "https://huggingface.co/blog/feed.xml", "feed"),
    ("GitHub Changelog", "https://github.blog/changelog/feed/", "feed"),
    ("TechCrunch AI", "https://techcrunch.com/category/artificial-intelligence/feed/", "feed"),
    ("The Verge AI", "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml", "feed"),
    ("GeekNews", "https://news.hada.io/rss/news", "feed"),
    ("Hacker News", "https://hacker-news.firebaseio.com/v0/topstories.json", "hn"),
]
MAX_AGE_H = 36          # 이보다 오래된 글은 보지 않는다 (날짜가 있는 피드만)
HN_MIN_SCORE = 300
SEEN_KEEP = 4000
BREAKING_MIN = 8
BACKLOG_MIN = 6
SOURCE = "news-breaking"
SERIES_TITLE = "AI 속보"


def _id(link: str) -> str:
    return hashlib.sha1(link.strip().encode()).hexdigest()[:16]


def _when(text: str | None) -> float | None:
    if not text:
        return None
    text = text.strip()
    try:
        return parsedate_to_datetime(text).timestamp()
    except (TypeError, ValueError):
        pass
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except ValueError:
        return None


def parse_feed(xml: str, source: str, limit: int = 40) -> list[dict[str, Any]]:
    """RSS·Atom 공통. 네임스페이스는 떼고 본다."""
    root = ET.fromstring(xml)
    for el in root.iter():
        if isinstance(el.tag, str) and "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    out = []
    for it in [*root.iter("item"), *root.iter("entry")][:limit]:
        title = (it.findtext("title") or "").strip()
        link = (it.findtext("link") or "").strip()
        if not link:
            ln = it.find("link")
            link = (ln.get("href") if ln is not None else "") or ""
        when = _when(it.findtext("pubDate") or it.findtext("published") or it.findtext("updated"))
        if title and link:
            out.append({"id": _id(link), "source": source, "title": html.unescape(title)[:200], "link": link, "at": when})
    return out


ANTHROPIC_SKIP = {"announcements", "features", "partnerships", "product", "policy", "research", "societal-impacts"}


def parse_anthropic(page: str) -> list[dict[str, Any]]:
    out, seen = [], set()
    for href, inner in re.findall(r'<a[^>]+href="(/news/[a-z0-9\-]+)"[^>]*>(.*?)</a>', page, flags=re.S):
        slug = href.rsplit("/", 1)[1]
        if slug in ANTHROPIC_SKIP or href in seen:
            continue
        seen.add(href)
        text = re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", inner))).strip()
        text = re.sub(r"\b[A-Z][a-z]{2} \d{1,2}, \d{4}\b", "", text).strip(" ·") or slug.replace("-", " ")
        link = "https://www.anthropic.com" + href
        out.append({"id": _id(link), "source": "Anthropic", "title": text[:200], "link": link, "at": None})
    return out[:30]


def fetch_hn(n: int = 30) -> list[dict[str, Any]]:
    ids = requests.get("https://hacker-news.firebaseio.com/v0/topstories.json", timeout=20).json()[:n]
    out = []
    for i in ids:
        it = requests.get(f"https://hacker-news.firebaseio.com/v0/item/{i}.json", timeout=20).json() or {}
        if it.get("score", 0) >= HN_MIN_SCORE and it.get("title"):
            link = it.get("url") or f"https://news.ycombinator.com/item?id={i}"
            out.append({"id": _id(link), "source": f"Hacker News {it['score']}점", "title": it["title"][:200],
                        "link": link, "at": float(it.get("time") or 0) or None})
    return out


def collect(now: float) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for name, url, kind in FEEDS:
        try:
            if kind == "hn":
                got = fetch_hn()
            else:
                r = requests.get(url, headers=UA, timeout=25)
                r.raise_for_status()
                got = parse_anthropic(r.text) if kind == "anthropic" else parse_feed(r.text, name)
        except Exception as e:  # noqa: BLE001 — 피드 하나가 죽어도 나머지는 본다
            log.warning("속보 감시: %s 읽기 실패 (%s)", name, e)
            continue
        items += [it for it in got if not it["at"] or now - it["at"] <= MAX_AGE_H * 3600]
    uniq: dict[str, dict[str, Any]] = {}
    for it in items:
        uniq.setdefault(it["id"], it)
    return list(uniq.values())


JUDGE_PROMPT = """너는 한국어 AI·개발 인스타그램 계정의 뉴스 데스크다. 팔로워는 AI 도구를 쓰는 개발자·대학원생·직장인.
아래는 방금 새로 올라온 글 목록이다. 각 글이 이 계정에 얼마나 중요한지 0~10점으로 매겨라.
- 9~10: 다들 오늘 이야기할 소식. 새 프런티어 모델 출시(GPT·Claude·Gemini·Llama 등 주요 버전), 주요 AI 제품 정식 출시·큰 가격 변경,
  클로드·클로드 코드의 큰 새 기능, 대형 인수·상장·사고(대규모 장애·보안 사고).
- 8: 많은 사람이 바로 써 볼 만한 주요 기능 출시나 업계 판도에 영향 있는 발표.
- 6~7: 브리핑에 넣을 만한 소식 (작은 기능, 연구 결과, 오픈소스 공개).
- 0~5: 홍보·사례 소개·사소한 업데이트·의견 글·이미 다룬 소식의 재탕.
여러 글이 같은 소식이면 공식 발표 원문 하나에만 점수를 주고 나머지는 0점.

# 최근에 이미 속보로 다룬 것 (같은 소식이면 0점)
{covered}

# 새 글
{items}

출력: JSON 하나만
{{"scores": [{{"n": 번호, "score": 점수}}], "breaking": {{"n": 가장 중요한 글 번호, "topic": "속보 주제 한 줄(한국어, 무엇이 나왔나)"}} 또는 null}}
breaking 은 8점 이상인 글이 있을 때만."""


def judge(items: list[dict[str, Any]], covered: list[str], cfg: dict[str, Any]) -> dict[str, Any]:
    lines = "\n".join(f"{i}. [{it['source']}] {it['title']} — {it['link']}" for i, it in enumerate(items, 1))
    data = parse_json(call_claude(JUDGE_PROMPT.format(items=lines, covered="\n".join(f"- {c}" for c in covered) or "없음"), cfg))
    scores = {int(s["n"]): int(s.get("score", 0)) for s in data.get("scores") or [] if str(s.get("n", "")).isdigit()}
    return {"scores": scores, "breaking": data.get("breaking")}


BRIEF = """{topic}

AI 속보 카드뉴스. 공식 발표 원문({link})을 먼저 열어 읽고, 확인된 사실만 쓴다. 빠르게 읽히게 4~6장:
표지(무엇이 나왔나 한 줄) → 핵심 변화 3가지(숫자·비교·전과 뭐가 다른지) → 누가·언제·어떻게 쓸 수 있나(요금제·지역·가격, 확인된 것만)
→ 한 줄 의미(과장 없이). 발표 날짜를 쓰고, 루머·추측·확인 안 된 벤치마크는 넣지 않는다. 캡션 첫 줄은 '속보'를 쓰지 말고 무엇이 나왔는지로."""


def _load(pl: Pipeline, key: str, default: Any) -> Any:
    try:
        return json.loads(pl.db.kv_get(key) or "null") or default
    except ValueError:
        return default


def slot(pl: Pipeline, now: datetime) -> float | None:
    """속보 게시 시각. 밤이면 아침 8:30, 아니면 바로(None) — 단 앞뒤 45분 안에 다른 예약이 있으면 그 1시간 뒤."""
    if now.hour >= 23 or now.hour < 7:
        day = now if now.hour < 7 else now + timedelta(days=1)
        return day.replace(hour=8, minute=30, second=0, microsecond=0).timestamp()
    ready = now.timestamp() + 25 * 60  # 만드는 데 걸리는 시간
    taken = sorted(p.scheduled_at for p in pl.db.by_status("초안", "승인") if p.scheduled_at)
    for t in taken:
        if abs(t - ready) < 45 * 60:
            ready = t + 60 * 60
    return ready if ready > now.timestamp() + 30 * 60 else None


def run(pl: Pipeline, now: datetime | None = None) -> str:
    now = now or datetime.now(pl.tz)
    items = collect(now.timestamp())
    seen = _load(pl, "news_seen", [])
    fresh = [it for it in items if it["id"] not in set(seen)]
    pl.db.kv_set("news_seen", json.dumps((seen + [it["id"] for it in fresh])[-SEEN_KEEP:]))
    if not seen:
        return f"처음 실행: 지금 있는 글 {len(fresh)}개를 본 것으로 기록"
    if not fresh:
        return "새 글 없음"
    fresh = fresh[:60]
    covered = _load(pl, "news_breaking", [])
    res = judge(fresh, [c["topic"] for c in covered[-15:]], pl.cfg.caption)
    backlog = _load(pl, "news_backlog", [])
    week_ago = now.timestamp() - 8 * 86400
    backlog = [b for b in backlog if b["at"] > week_ago]
    for n, score in res["scores"].items():
        if BACKLOG_MIN <= score and 1 <= n <= len(fresh):
            it = fresh[n - 1]
            backlog.append({"title": it["title"], "link": it["link"], "source": it["source"], "score": score,
                            "at": now.timestamp()})
    pl.db.kv_set("news_backlog", json.dumps(backlog[-60:], ensure_ascii=False))
    b = res.get("breaking")
    if not b or not str(b.get("n", "")).isdigit() or not 1 <= int(b["n"]) <= len(fresh):
        return f"새 글 {len(fresh)}개, 속보 없음 (후보 {len(backlog)}개)"
    it, score = fresh[int(b["n"]) - 1], res["scores"].get(int(b["n"]), 0)
    if score < BREAKING_MIN:
        return f"새 글 {len(fresh)}개, 속보 기준 미달 ({score}점: {it['title']})"
    today = now.strftime("%Y-%m-%d")
    if any(c.get("day") == today for c in covered):
        return f"오늘 속보는 이미 냈음 — 후보로만: {it['title']}"
    topic = str(b.get("topic") or it["title"])
    covered.append({"day": today, "topic": topic, "link": it["link"], "score": score})
    pl.db.kv_set("news_breaking", json.dumps(covered[-40:], ensure_ascii=False))
    log.info("속보 감지 (%d점): %s — %s", score, topic, it["link"])
    post = pl.generate_topic(BRIEF.format(topic=topic, link=it["link"]), source=SOURCE, topic_theme="tech",
                             title=SERIES_TITLE, scheduled_at=slot(pl, now))
    return f"속보 생성 {post.id}: {topic}"


def backlog_text(pl: Pipeline, now: float | None = None, days: int = 7) -> str:
    """브리핑·주간 뉴스 원고 지침에 붙일 '이번 주 감시에서 잡힌 소식'."""
    now = now or time.time()
    items = [b for b in _load(pl, "news_backlog", []) if b["at"] > now - days * 86400]
    breaking = [c for c in _load(pl, "news_breaking", [])
                if datetime.strptime(c["day"], "%Y-%m-%d").timestamp() > now - days * 86400]
    if not items and not breaking:
        return ""
    lines = [f"- (속보로 이미 올림) {c['topic']} — {c['link']}" for c in breaking]
    lines += [f"- [{b['source']}] {b['title']} — {b['link']}" for b in sorted(items, key=lambda x: -x["score"])[:20]]
    return ("\n\n# 이번 주 속보 감시에서 잡힌 소식 (공식 원문으로 확인한 뒤 중요한 것은 빠뜨리지 말 것. "
            "속보로 이미 올린 것은 한 줄 요약만)\n" + "\n".join(lines))
