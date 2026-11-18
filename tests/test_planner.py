from datetime import datetime
from zoneinfo import ZoneInfo

from uploader import planner, themes

KST = ZoneInfo("Asia/Seoul")
WED = datetime(2026, 11, 18, 12, 0, tzinfo=KST)  # 수요일


def test_weekly_picks_next_listed_day_and_time():
    assert planner.next_run("매주", ["일"], "10:00", WED) == datetime(2026, 11, 22, 10, 0, tzinfo=KST)
    assert planner.next_run("매주", ["수"], "19:00", WED) == datetime(2026, 11, 18, 19, 0, tzinfo=KST)
    assert planner.next_run("매주", ["수"], "09:00", WED) == datetime(2026, 11, 25, 9, 0, tzinfo=KST)


def test_daily_monthly_once():
    assert planner.next_run("매일", [], "08:30", WED) == datetime(2026, 11, 19, 8, 30, tzinfo=KST)
    assert planner.next_run("매월", [], "10:00", WED) == datetime(2026, 12, 1, 10, 0, tzinfo=KST)
    assert planner.next_run("한 번", [], "13:00", WED) == datetime(2026, 11, 18, 13, 0, tzinfo=KST)
    assert planner.next_run("한 번", [], "13:00", WED, last=WED) is None


def test_biweekly_skips_a_week_after_last_run():
    last = datetime(2026, 11, 15, 10, 0, tzinfo=KST)  # 일요일에 만들었음
    assert planner.next_run("격주", ["일"], "10:00", WED, last) == datetime(2026, 11, 29, 10, 0, tzinfo=KST)


def test_bad_time_falls_back_to_default():
    assert planner.next_run("매일", [], "아침", WED).hour == 12


def test_plan_sources_use_their_topic_theme():
    assert themes.topic_of("plan-career") == "career"
    assert themes.from_ko("테크·AI") == "tech"


def test_once_respects_weekday():
    assert planner.next_run("한 번", ["일"], "20:00", WED) == datetime(2026, 11, 22, 20, 0, tzinfo=KST)


# ---- run_due: 게시 시각보다 먼저 만들고 예약, 횟수·이어서

class FakeNotion:
    def __init__(self):
        self.sets = []

    def set_props(self, pid, props):
        self.sets.append((pid, props))


class FakeDB:
    def titles(self, prefix, n=40):
        return [f"{prefix} PER이란?", f"{prefix} 공매도란?"]


class FakePL:
    def __init__(self):
        from types import SimpleNamespace
        self.notion, self.db, self.tz = FakeNotion(), FakeDB(), KST
        self.cfg = SimpleNamespace(raw={})
        self.calls = []

    def generate_topic(self, brief, **kw):
        self.calls.append((brief, kw))


def _page(name, **props):
    def txt(v):
        return {"type": "rich_text", "rich_text": [{"plain_text": v, "text": {"content": v}}]}
    p = {"이름": {"type": "title", "title": [{"plain_text": name, "text": {"content": name}}]},
         "상태": {"type": "select", "select": {"name": "사용"}}}
    for k, v in props.items():
        if k in ("주기", "주제", "생성기"):
            p[k] = {"type": "select", "select": {"name": v}}
        elif k == "요일":
            p[k] = {"type": "multi_select", "multi_select": [{"name": d} for d in v.split(",")]}
        elif k == "다음 게시":
            p[k] = {"type": "date", "date": {"start": v}}
        elif k in ("횟수", "만든 횟수"):
            p[k] = {"type": "number", "number": v}
        else:
            p[k] = txt(v)
    return {"id": name, "properties": p}


def test_run_due_waits_until_lead_window():
    pl = FakePL()
    page = _page("용어", 주기="매주", 요일="금", **{"게시 시각": "12:00", "다음 게시": "2026-11-20T12:00:00+09:00"})
    assert not planner.run_due(pl, page, datetime(2026, 11, 20, 6, 59, tzinfo=KST))
    assert pl.calls == []


def test_run_due_schedules_at_slot_and_skips_covered_topics():
    pl = FakePL()
    page = _page("용어", 주기="매주", 요일="금", 주제="증시", **{"게시 시각": "12:00",
                                                         "다음 게시": "2026-11-20T12:00:00+09:00", "다듬은 규칙": "용어 하나씩"})
    assert planner.run_due(pl, page, datetime(2026, 11, 20, 7, 5, tzinfo=KST))
    brief, kw = pl.calls[0]
    assert kw["scheduled_at"] == datetime(2026, 11, 20, 12, 0, tzinfo=KST).timestamp()
    assert kw["topic_theme"] == "market"
    assert "이미 다룬 것" in brief and "- PER이란?" in brief
    props = pl.notion.sets[-1][1]
    assert props["다음 게시"]["date"]["start"].startswith("2026-11-27T12:00")
    assert props["만든 횟수"] == {"number": 1}


def test_run_due_finishes_after_count_and_starts_follow_up():
    pl = FakePL()
    page = _page("증시 용어", 주기="매주", 요일="금", 횟수=3, **{"만든 횟수": 2, "게시 시각": "12:00",
                                                          "다음 게시": "2026-11-20T12:00:00+09:00", "이어서": "AI 용어"})
    follow = _page("AI 용어", 주기="매주", 요일="금")
    assert planner.run_due(pl, page, datetime(2026, 11, 20, 8, 0, tzinfo=KST), [page, follow])
    by_id = dict(pl.notion.sets)
    assert by_id["증시 용어"]["상태"]["select"]["name"] == "완료"
    assert by_id["AI 용어"]["상태"]["select"]["name"] == "사용"
