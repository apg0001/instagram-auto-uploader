from datetime import datetime
from zoneinfo import ZoneInfo

from uploader.pipeline import parse_when

KST = ZoneInfo("Asia/Seoul")


def test_parse_when_date_and_datetime():
    assert parse_when(None, KST) is None
    assert parse_when("2026-11-18", KST) == datetime(2026, 11, 18, tzinfo=KST).timestamp()
    assert parse_when("2026-11-18T09:00:00.000+09:00", KST) == datetime(2026, 11, 18, 9, tzinfo=KST).timestamp()


def test_auto_approve_matches_market_sources():
    from uploader.config import Config
    from uploader.pipeline import Pipeline

    p = Pipeline.__new__(Pipeline)
    p.cfg = Config({"auto_approve": ["market"]})
    assert p.auto_approved("market-close") and p.auto_approved("market-weekly")
    assert not p.auto_approved("lab-wars") and not p.auto_approved("marketing")
    p.cfg = Config({})
    assert not p.auto_approved("market-close")


def test_auto_approve_star_means_everything():
    from uploader.config import Config
    from uploader.pipeline import Pipeline

    p = Pipeline.__new__(Pipeline)
    p.cfg = Config({"auto_approve": ["*"]})
    assert p.auto_approved("lab-wars") and p.auto_approved("plan-tech") and p.auto_approved("manual")
