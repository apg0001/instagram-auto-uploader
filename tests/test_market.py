import datetime as dt
from zoneinfo import ZoneInfo

from uploader.market.analysis import DISCLAIMER, stray_numbers, validate
from uploader.market.data import is_krx_trading_day, is_last_trading_day_of_month, last_trading_day
from uploader.pipeline import market_expiry

KST = ZoneInfo("Asia/Seoul")
SNAP = {
    "report": "close", "date": "2026-11-17",
    "kospi": {"name": "코스피", "price": 4999.59, "change": -100.91, "pct": -1.98},
    "gainers": [{"name": "계양전기", "price": 2935.0, "pct": 29.87}],
    "sectors_top": [{"name": "백화점과일반상점", "pct": 2.52}],
}


def ok_result(**over):
    r = {"headline": "코스피 5000선 하회", "summary": "코스피는 4,999.59로 1.98% 내렸습니다.",
         "sectors": [{"name": "백화점과일반상점", "reason": None, "source": None}],
         "movers": [{"name": "계양전기", "reason": "공급업체 선정", "source": "뉴스핌"}],
         "caption": f"코스피 4,999.59 (-1.98%)\n\n{DISCLAIMER}\n#주식"}
    r.update(over)
    return r


def test_valid_result_passes():
    assert validate(ok_result(), SNAP) == []


def test_missing_disclaimer_and_advice_words():
    problems = validate(ok_result(caption="계양전기 매수 추천"), SNAP)
    assert "캡션에 면책 문구 없음" in problems
    assert any(p.startswith("금지 표현") for p in problems)


def test_unknown_names_rejected():
    problems = validate(ok_result(movers=[{"name": "없는종목", "reason": None, "source": None}]), SNAP)
    assert any("없는종목" in p for p in problems)


def test_stray_numbers():
    assert stray_numbers("코스피 4,999.59, 100.91포인트, 11월 17일, 3배", SNAP) == []
    assert stray_numbers("코스피 5,012.33", SNAP) == ["5,012.33"]


def test_trading_calendar():
    assert not is_krx_trading_day(dt.date(2026, 11, 15))      # 일요일
    assert not is_krx_trading_day(dt.date(2026, 12, 25))      # 성탄절
    assert not is_krx_trading_day(dt.date(2026, 12, 31))      # 연말 휴장
    assert is_last_trading_day_of_month(dt.date(2026, 12, 30))
    assert not is_last_trading_day_of_month(dt.date(2026, 11, 17))
    assert last_trading_day(dt.date(2026, 11, 16)) == dt.date(2026, 11, 13)


def test_market_expiry():
    now = dt.datetime(2026, 11, 17, 7, 40, tzinfo=KST)
    assert market_expiry("premarket", now) == dt.datetime(2026, 11, 17, 9, 0, tzinfo=KST).timestamp()
    close = dt.datetime(2026, 11, 17, 16, 10, tzinfo=KST)
    assert market_expiry("close", close) == dt.datetime(2026, 11, 18, 8, 30, tzinfo=KST).timestamp()
