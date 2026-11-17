"""시장 데이터 수집. 숫자는 전부 여기서 온 값만 쓴다 (LLM이 숫자를 만들지 않게).

- 국내: 네이버 증권 모바일 API (지수, 업종 79개, 상승·하락 상위, 시가총액 상위)
- 해외·기간 수익률: Yahoo Finance (yfinance)
- 거래일: holidays 의 KRX(XKRX) 휴장일
"""
from __future__ import annotations

import datetime as dt
import logging
import warnings
from dataclasses import asdict, dataclass
from zoneinfo import ZoneInfo

import holidays
import requests

warnings.filterwarnings("ignore")
log = logging.getLogger("uploader")
KST = ZoneInfo("Asia/Seoul")
H = {"User-Agent": "Mozilla/5.0"}
M = "https://m.stock.naver.com/api"
W = "https://api.stock.naver.com"

US_INDICES = [(".INX", "S&P 500"), (".IXIC", "나스닥"), (".DJI", "다우"), (".SOX", "필라델피아 반도체")]
US_SECTORS = [("XLK", "기술"), ("XLC", "커뮤니케이션"), ("XLY", "임의소비재"), ("XLF", "금융"), ("XLV", "헬스케어"),
              ("XLI", "산업재"), ("XLE", "에너지"), ("XLB", "소재"), ("XLP", "필수소비재"), ("XLU", "유틸리티"),
              ("XLRE", "부동산")]
US_BIGTECH = [("NVDA", "엔비디아"), ("AAPL", "애플"), ("MSFT", "마이크로소프트"), ("GOOGL", "알파벳"),
              ("AMZN", "아마존"), ("META", "메타"), ("AVGO", "브로드컴"), ("TSLA", "테슬라")]
# 기간(주간·월간) 업종 동향은 업종 대표 ETF로 본다
KR_SECTOR_ETFS = [("091160", "반도체"), ("305720", "2차전지"), ("091180", "자동차"), ("466920", "조선"),
                  ("463250", "방산·우주"), ("244580", "바이오"), ("143860", "헬스케어"), ("091170", "은행"),
                  ("102970", "증권"), ("117460", "에너지화학"), ("117680", "철강"), ("102960", "기계장비"),
                  ("117700", "건설"), ("266360", "K콘텐츠"), ("157490", "소프트웨어"), ("228790", "화장품"),
                  ("140710", "운송")]


@dataclass
class Quote:
    name: str
    code: str
    price: float
    pct: float
    value_eok: float = 0.0       # 거래대금(억원)
    market: str = ""
    cap_eok: float = 0.0         # 시가총액(억원)


def _num(s) -> float:
    if s is None:
        return 0.0
    try:
        return float(str(s).replace(",", "").replace("%", ""))
    except ValueError:
        return 0.0


def _get(url: str, **params) -> dict | list:
    r = requests.get(url, params=params, headers=H, timeout=20)
    r.raise_for_status()
    return r.json()


# ------------------------------------------------------------------ 거래일

def krx_holidays(year: int):
    return holidays.financial_holidays("XKRX", years=year)


def is_krx_trading_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in krx_holidays(d.year)


def is_last_trading_day_of_month(d: dt.date) -> bool:
    if not is_krx_trading_day(d):
        return False
    n = d + dt.timedelta(days=1)
    while n.month == d.month:
        if is_krx_trading_day(n):
            return False
        n += dt.timedelta(days=1)
    return True


def last_trading_day(before: dt.date) -> dt.date:
    d = before - dt.timedelta(days=1)
    while not is_krx_trading_day(d):
        d -= dt.timedelta(days=1)
    return d


# ------------------------------------------------------------------ 국내

def kr_index(code: str) -> dict:
    """code: KOSPI | KOSDAQ"""
    j = _get(f"{M}/index/{code}/basic")
    return {"name": j["stockName"], "price": _num(j["closePrice"]), "change": _num(j["compareToPreviousClosePrice"]),
            "pct": _num(j["fluctuationsRatio"]), "status": j.get("marketStatus"), "at": j.get("localTradedAt")}


def kr_index_history(code: str, days: int) -> list[tuple[str, float]]:
    """최근 거래일 종가 (오래된 것부터)."""
    rows = _get(f"{M}/index/{code}/price", pageSize=days, page=1)
    return [(r["localTradedAt"], _num(r["closePrice"])) for r in reversed(rows)]


def kr_intraday(code: str) -> list[float]:
    """오늘 5분 봉 종가 (yfinance). 실패하면 빈 목록."""
    import yfinance as yf
    sym = {"KOSPI": "^KS11", "KOSDAQ": "^KQ11"}[code]
    try:
        df = yf.download(sym, period="1d", interval="5m", progress=False)
        return [float(x) for x in df["Close"].squeeze().dropna().tolist()]
    except Exception:  # noqa: BLE001
        log.warning("장중 데이터 실패: %s", code)
        return []


def industries() -> list[dict]:
    j = _get(f"{M}/stocks/industry", page=1, pageSize=100)
    out = [{"name": g["name"], "pct": _num(g["changeRate"]), "rise": g["riseCount"], "fall": g["fallCount"],
            "total": g["totalCount"], "no": g["no"]} for g in j["groups"]]
    return sorted(out, key=lambda x: x["pct"], reverse=True)


def industry_leaders(no: int, n: int = 3) -> list[str]:
    j = _get(f"{M}/stocks/industry/{no}", page=1, pageSize=n)
    return [s["stockName"] for s in j["stocks"]]


def _quotes(kind: str, market: str, n: int) -> list[Quote]:
    j = _get(f"{M}/stocks/{kind}/{market}", page=1, pageSize=n)
    return [Quote(name=s["stockName"], code=s["itemCode"], price=_num(s["closePrice"]), pct=_num(s["fluctuationsRatio"]),
                  value_eok=_num(s.get("accumulatedTradingValue")) / 100, market=market,
                  cap_eok=_num(s.get("marketValue"))) for s in j["stocks"]]


def movers(direction: str, n: int = 5, min_value_eok: float = 30) -> list[Quote]:
    """코스피+코스닥 통합 급등(up)·급락(down) 상위. 거래대금이 너무 작은 종목은 뺀다."""
    pool = _quotes(direction, "KOSPI", 40) + _quotes(direction, "KOSDAQ", 40)
    pool = [q for q in pool if q.value_eok >= min_value_eok]
    return sorted(pool, key=lambda q: q.pct, reverse=(direction == "up"))[:n]


def market_cap_top(n: int = 10) -> list[Quote]:
    return _quotes("marketValue", "KOSPI", n)


def usd_krw() -> dict:
    j = _get("https://m.stock.naver.com/front-api/marketIndex/productDetail", category="exchange",
             reutersCode="FX_USDKRW")["result"]
    return {"price": _num(j.get("closePrice")), "change": _num(j.get("fluctuations")), "pct": _num(j.get("fluctuationsRatio"))}


# ------------------------------------------------------------------ 해외·기간 (yfinance)

def _yf_changes(symbols: list[str], start: dt.date | None = None) -> dict[str, tuple[float, float]]:
    """심볼 → (마지막 종가, 등락률%). start가 없으면 직전 거래일 대비, 있으면 start 이전 마지막 종가 대비."""
    import yfinance as yf
    period = "10d" if start is None else None
    kw = {"period": period} if period else {"start": (start - dt.timedelta(days=10)).isoformat()}
    df = yf.download(symbols, progress=False, auto_adjust=False, **kw)["Close"]
    out = {}
    for s in symbols:
        col = df[s].dropna() if s in df else df.dropna()
        if len(col) < 2:
            continue
        last = float(col.iloc[-1])
        if start is None:
            base = float(col.iloc[-2])
        else:
            before = col[col.index.date < start]
            if before.empty:
                continue
            base = float(before.iloc[-1])
        out[s] = (last, (last / base - 1) * 100)
    return out


def us_indices() -> list[Quote]:
    out = []
    for code, name in US_INDICES:
        try:
            j = _get(f"{W}/index/{code}/basic")
            out.append(Quote(name=name, code=code, price=_num(j["closePrice"]), pct=_num(j["fluctuationsRatio"])))
        except Exception:  # noqa: BLE001
            log.warning("미국 지수 실패: %s", code)
    return out


def us_sectors() -> list[Quote]:
    ch = _yf_changes([s for s, _ in US_SECTORS])
    out = [Quote(name=n, code=s, price=ch[s][0], pct=ch[s][1]) for s, n in US_SECTORS if s in ch]
    return sorted(out, key=lambda q: q.pct, reverse=True)


def us_bigtech() -> list[Quote]:
    ch = _yf_changes([s for s, _ in US_BIGTECH])
    return [Quote(name=n, code=s, price=ch[s][0], pct=ch[s][1]) for s, n in US_BIGTECH if s in ch]


def kr_sector_etfs(start: dt.date) -> list[Quote]:
    syms = [f"{c}.KS" for c, _ in KR_SECTOR_ETFS]
    ch = _yf_changes(syms, start)
    out = [Quote(name=n, code=c, price=ch[f"{c}.KS"][0], pct=ch[f"{c}.KS"][1]) for c, n in KR_SECTOR_ETFS if f"{c}.KS" in ch]
    return sorted(out, key=lambda q: q.pct, reverse=True)


def large_cap_period(start: dt.date, n: int = 30) -> list[Quote]:
    caps = market_cap_top(n)
    ch = _yf_changes([f"{q.code}.KS" for q in caps], start)
    out = [Quote(name=q.name, code=q.code, price=ch[f"{q.code}.KS"][0], pct=ch[f"{q.code}.KS"][1], cap_eok=q.cap_eok)
           for q in caps if f"{q.code}.KS" in ch]
    return sorted(out, key=lambda q: q.pct, reverse=True)


def index_period(start: dt.date) -> list[Quote]:
    syms = {"^KS11": "코스피", "^KQ11": "코스닥", "^GSPC": "S&P 500", "^IXIC": "나스닥"}
    ch = _yf_changes(list(syms), start)
    return [Quote(name=n, code=s, price=ch[s][0], pct=ch[s][1]) for s, n in syms.items() if s in ch]


# ------------------------------------------------------------------ 스냅샷

def q(x: Quote) -> dict:
    return asdict(x)


def snapshot(report: str, today: dt.date) -> dict:
    """report: premarket | close | weekly | monthly"""
    snap: dict = {"report": report, "date": today.isoformat(), "fetched_at": dt.datetime.now(KST).isoformat(timespec="minutes")}
    if report == "premarket":
        snap["us_indices"] = [q(x) for x in us_indices()]
        snap["us_sectors"] = [q(x) for x in us_sectors()]
        snap["us_bigtech"] = [q(x) for x in us_bigtech()]
        snap["usd_krw"] = usd_krw()
        snap["kr_prev_date"] = last_trading_day(today).isoformat()
        inds = industries()  # 장 시작 전에는 직전 거래일 기준
        snap["kr_sectors_top"], snap["kr_sectors_bottom"] = inds[:5], inds[-5:][::-1]
    elif report == "close":
        snap["kospi"], snap["kosdaq"] = kr_index("KOSPI"), kr_index("KOSDAQ")
        snap["kospi_intraday"] = kr_intraday("KOSPI")
        snap["usd_krw"] = usd_krw()
        inds = industries()
        snap["breadth"] = {"rise": sum(i["rise"] for i in inds), "fall": sum(i["fall"] for i in inds)}
        top, bottom = inds[:6], inds[-6:][::-1]
        for i in top + bottom:
            try:
                i["leaders"] = industry_leaders(i["no"])
            except Exception:  # noqa: BLE001
                i["leaders"] = []
        snap["sectors_top"], snap["sectors_bottom"] = top, bottom
        snap["market_cap"] = [q(x) for x in market_cap_top(10)]
        snap["gainers"] = [q(x) for x in movers("up")]
        snap["losers"] = [q(x) for x in movers("down")]
    elif report in ("weekly", "monthly"):
        # 주간: 이번 주 월요일부터 (토요일 아침에 실행). 월간: 이번 달 1일부터 (월말 마지막 거래일 장 마감 후 실행)
        start = today - dt.timedelta(days=today.weekday()) if report == "weekly" else today.replace(day=1)
        snap["period_start"] = start.isoformat()
        snap["indices"] = [q(x) for x in index_period(start)]
        snap["sectors"] = [q(x) for x in kr_sector_etfs(start)]
        caps = large_cap_period(start)
        snap["caps_top"], snap["caps_bottom"] = [q(x) for x in caps[:5]], [q(x) for x in caps[-5:][::-1]]
        snap["kospi_daily"] = kr_index_history("KOSPI", 25 if report == "monthly" else 6)
    else:
        raise ValueError(report)
    return snap
