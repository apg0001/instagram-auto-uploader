from datetime import datetime
from zoneinfo import ZoneInfo

from uploader.pipeline import parse_when

KST = ZoneInfo("Asia/Seoul")


def test_parse_when_date_and_datetime():
    assert parse_when(None, KST) is None
    assert parse_when("2026-11-18", KST) == datetime(2026, 11, 18, tzinfo=KST).timestamp()
    assert parse_when("2026-11-18T09:00:00.000+09:00", KST) == datetime(2026, 11, 18, 9, tzinfo=KST).timestamp()
