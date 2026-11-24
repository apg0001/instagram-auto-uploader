import json
from datetime import datetime
from zoneinfo import ZoneInfo

from uploader import newswatch

KST = ZoneInfo("Asia/Seoul")

RSS = """<?xml version="1.0"?><rss><channel>
<item><title>GPT-7 출시</title><link>https://openai.com/index/gpt-7</link><pubDate>Tue, 24 Nov 2026 01:00:00 GMT</pubDate></item>
<item><title>옛날 글</title><link>https://openai.com/index/old</link><pubDate>Mon, 02 Nov 2026 01:00:00 GMT</pubDate></item>
</channel></rss>"""
ATOM = """<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>v3.0.0</title>
<link rel="alternate" href="https://github.com/anthropics/claude-code/releases/tag/v3.0.0"/>
<updated>2026-11-24T02:00:00Z</updated></entry></feed>"""


def test_parse_rss_and_atom():
    r = newswatch.parse_feed(RSS, "OpenAI")
    assert r[0]["title"] == "GPT-7 출시" and r[0]["link"].endswith("gpt-7") and r[0]["at"] > 0
    a = newswatch.parse_feed(ATOM, "Claude Code 릴리스")
    assert a[0]["link"].endswith("v3.0.0") and a[0]["at"]


def test_parse_anthropic_skips_categories_and_dates():
    page = ('<a href="/news/announcements">Announcements Nov 9, 2026</a>'
            '<a class="x" href="/news/claude-6"><h3>Introducing Claude 6</h3> <span>Nov 24, 2026</span></a>'
            '<a href="/news/claude-6">dup</a>')
    got = newswatch.parse_anthropic(page)
    assert [g["title"] for g in got] == ["Introducing Claude 6"]
    assert got[0]["link"] == "https://www.anthropic.com/news/claude-6"


class FakeDB:
    def __init__(self):
        self.kv = {}

    def kv_get(self, k):
        return self.kv.get(k)

    def kv_set(self, k, v):
        self.kv[k] = v

    def by_status(self, *s):
        return []


class FakePL:
    tz = KST

    class cfg:
        caption = {}

    def __init__(self):
        self.db = FakeDB()
        self.made = []

    def generate_topic(self, brief, **kw):
        self.made.append((brief, kw))

        class P:
            id = "p1"
        return P()


def item(i, title):
    return {"id": f"id{i}", "source": "OpenAI", "title": title, "link": f"https://x/{i}", "at": None}


def test_run_first_seeds_then_breaks_once_a_day(monkeypatch):
    pl, now = FakePL(), datetime(2026, 11, 24, 10, 0, tzinfo=KST)
    feed = [item(1, "옛 글")]
    monkeypatch.setattr(newswatch, "collect", lambda t: feed)
    assert "처음 실행" in newswatch.run(pl, now) and not pl.made

    feed.extend([item(2, "GPT-7 출시"), item(3, "작은 기능")])
    monkeypatch.setattr(newswatch, "judge", lambda items, covered, cfg: {
        "scores": {1: 9, 2: 6}, "breaking": {"n": 1, "topic": "GPT-7 출시"}})
    assert "속보 생성" in newswatch.run(pl, now)
    brief, kw = pl.made[0]
    assert "https://x/2" in brief and kw["source"] == "news-breaking" and kw["title"] == "AI 속보"
    assert [b["title"] for b in json.loads(pl.db.kv["news_backlog"])] == ["GPT-7 출시", "작은 기능"]

    feed.append(item(4, "Claude 7 출시"))
    monkeypatch.setattr(newswatch, "judge", lambda items, covered, cfg: {
        "scores": {1: 10}, "breaking": {"n": 1, "topic": "Claude 7"}})
    assert "이미 냈음" in newswatch.run(pl, now) and len(pl.made) == 1
    text = newswatch.backlog_text(pl, now.timestamp())
    assert "(속보로 이미 올림) GPT-7 출시" in text and "Claude 7 출시" in text


def test_slot_night_goes_to_morning():
    pl = FakePL()
    t = newswatch.slot(pl, datetime(2026, 11, 24, 23, 30, tzinfo=KST))
    assert datetime.fromtimestamp(t, KST) == datetime(2026, 11, 25, 8, 30, tzinfo=KST)
    assert newswatch.slot(pl, datetime(2026, 11, 24, 14, 0, tzinfo=KST)) is None
