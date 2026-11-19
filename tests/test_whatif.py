import pytest

from uploader import whatif


def months(start_y: int, n: int) -> list[str]:
    out = []
    y, m = start_y, 1
    for _ in range(n):
        out.append(f"{y}-{m:02d}")
        m += 1
        if m == 13:
            y, m = y + 1, 1
    return out


def test_won_format():
    assert whatif.won(4500) == "4,500원"
    assert whatif.won(19_170_000) == "1,917만 원"
    assert whatif.won(1_029_000_000) == "10억 2,900만 원"
    assert whatif.won(299_990_000) == "3억 원"
    assert whatif.won(100_000_000) == "1억 원"


def test_habit_invest_doubling_and_drawdown():
    ms = months(2020, 37)  # 2020-01 ~ 2023-01
    prices = {m: 10.0 for m in ms[:24]} | {m: 5.0 for m in ms[24:36]} | {ms[36]: 20.0}
    fx = {m: 1000.0 for m in ms}
    n = whatif.habit_invest(1000, prices, fx, "2020-01", ms[36])  # 마지막 달은 넣지 않음
    assert n.months == 36 and n.put == 1000 * 30 * 36 and n.units == 36 * 30
    shares = 30_000 / 1000 * (24 / 10 + 12 / 5)
    assert n.value == pytest.approx(shares * 20 * 1000)
    assert n.mdd == pytest.approx(-0.5) and n.mdd_year == "2022"
    assert [b["year"] for b in n.bars] == ["2020", "2021", "2022", "2023"]
    assert n.bars[-1]["label"] == whatif.won(n.value)
    p = whatif.placeholders(n, "엔비디아", "잔")
    assert p["years"] == "3" and p["mdd"] == "-50%" and p["units"] == "1,080잔"


def test_krw_asset_without_fx():
    ms = months(2020, 30)
    n = whatif.habit_invest(1000, {m: 100.0 for m in ms}, None, "2020-01", ms[-1])
    assert n.value == pytest.approx(n.put)


def spec(**over):
    s = {"ticker": "NVDA", "start": "2015-01", "daily_won": 4500, "unit": "잔", "item": "cup",
         "caption": "한 잔에 {per_unit}", "scenes": [
             {"visual": "spin", "item": "cup", "head": "너 오늘도 커피 샀지?", "say": "너 오늘도 커피 샀지?"},
             {"visual": "pile", "head": "{years}년이면", "big": "{put}", "say": "{years}년이면 {put}."},
             {"visual": "growth", "head": "{asset}", "say": "{mdd_year}년엔 {mdd}."},
             {"visual": "pile", "item": "coin", "big": "{value}", "say": "지금 {value}."},
             {"visual": "crash", "big": "{mdd}", "say": "버틸 수 있어?"},
             {"visual": "end", "head": "다음엔?", "say": "댓글 줘."}]}
    s.update(over)
    return s


def test_check_spec_ok_and_rejects_raw_numbers():
    assert whatif.check_spec(spec()) == []
    bad = spec()
    bad["scenes"][1]["say"] = "10년이면 1,917만 원."
    errs = whatif.check_spec(bad)
    assert any("숫자를 직접" in e for e in errs)
    assert whatif.check_spec(spec(ticker="DOGE"))
    assert whatif.check_spec(spec(caption="무려 10억"))


def test_fill_uses_numbers_and_rejects_unknown():
    p = {"years": "12", "put": "1,917만 원", "asset": "엔비디아", "mdd_year": "2022", "mdd": "-63%",
         "value": "10억 2,900만 원"}
    out = whatif.fill(spec(), p)
    assert out[1]["say"] == "12년이면 1,917만 원." and out[1]["item"] == "cup"
    assert out[3]["item"] == "coin"
    bad = spec()
    bad["scenes"][0]["say"] = "{몰라}"
    with pytest.raises(KeyError):
        whatif.fill(bad, p)
