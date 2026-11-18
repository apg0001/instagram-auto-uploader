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
    assert planner.next_run("매일", [], "아침", WED).hour == 10


def test_plan_sources_use_their_topic_theme():
    assert themes.topic_of("plan-career") == "career"
    assert themes.from_ko("테크·AI") == "tech"
