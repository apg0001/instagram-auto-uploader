from datetime import datetime
from zoneinfo import ZoneInfo

from uploader import planner, slide_reel

KST = ZoneInfo("Asia/Seoul")


def test_reel_time_next_slot_within_48h():
    thu_noon = datetime(2026, 11, 26, 12, 0, tzinfo=KST)  # 목
    mon_noon = datetime(2026, 11, 30, 12, 0, tzinfo=KST)  # 월
    sun_4pm = datetime(2026, 11, 29, 16, 0, tzinfo=KST)   # 일
    assert datetime.fromtimestamp(planner.reel_time("목 18:00", thu_noon), KST) == datetime(2026, 11, 26, 18, 0, tzinfo=KST)
    assert planner.reel_time("목 18:00", mon_noon) is None  # 월요일 편은 릴스 없음
    assert datetime.fromtimestamp(planner.reel_time("월 18:00", sun_4pm), KST) == datetime(2026, 11, 30, 18, 0, tzinfo=KST)
    assert planner.reel_time("", thu_noon) is None and planner.reel_time("아무거나", thu_noon) is None


def test_card_lines_puts_shots_after_their_slide():
    content = {"slides": [{"type": "cover", "title": "표지"}, {"type": "points", "title": "둘"}, {"type": "points", "title": "셋"}],
               "shots": [{"head": "공식 문서", "caption": "여기", "after": 2}]}
    assert slide_reel.card_lines(content, 4) == ["cover: 표지", "points: 둘", "실제 화면: 공식 문서 — 여기", "points: 셋"]
