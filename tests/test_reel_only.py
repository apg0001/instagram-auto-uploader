from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from uploader import planner
from uploader.pipeline import Pipeline


def make(film_on, film_fails=False):
    calls = []
    pl = SimpleNamespace(cfg=SimpleNamespace(raw={"film": {"enabled": film_on}}), calls=calls)
    pl.generate_slide_reel = lambda *a: calls.append(("slide", a[-1])) or "reel-post"
    pl.record_failure = lambda src, e: calls.append(("fail", src))
    return pl


def test_reel_from_cards_film_then_fallback(monkeypatch):
    from uploader import film

    started = []
    monkeypatch.setattr(film, "start", lambda *a: started.append(a[-1]))
    pl = make(True)
    assert Pipeline._reel_from_cards(pl, {}, [Path("1"), Path("2")], "plan-tech", "T", "tech", 100.0) is None
    assert started == [100.0] and pl.calls == []

    def boom(*a):
        raise film.FilmError("no host")
    monkeypatch.setattr(film, "start", boom)
    pl = make(True)
    assert Pipeline._reel_from_cards(pl, {}, [Path("1"), Path("2")], "plan-tech", "T", "tech", 100.0) == "reel-post"
    assert pl.calls == [("fail", "plan-tech:film"), ("slide", 100.0)]


def test_plan_row_reel_only(monkeypatch):
    got = {}
    pl = SimpleNamespace(tz=ZoneInfo("Asia/Seoul"), db=SimpleNamespace(titles=lambda p: []),
                         generate_topic=lambda brief, **k: got.update(k))
    txt = lambda v: {"type": "rich_text", "rich_text": [{"plain_text": v}]}  # noqa: E731
    pr = {"이름": {"type": "title", "title": [{"plain_text": "AI 공감 모먼트"}]}, "생성기": {"type": "select", "select": None},
          "주제": {"type": "select", "select": {"name": "테크"}}, "릴스": txt("대신"), "다듬은 규칙": txt("r")}
    at = datetime(2026, 12, 5, 12, 0, tzinfo=pl.tz)
    planner.generate_for(pl, pr, at)
    assert got["reel_only"] is True and got["reel_at"] is None and got["scheduled_at"] == at.timestamp()
