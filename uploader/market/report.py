"""스냅샷 + 분석 → 카드 슬라이드 목록. 숫자는 스냅샷에서, 문장은 분석에서."""
from __future__ import annotations

import datetime as dt
from typing import Any

from ..cards.render import price
from .analysis import REPORT_KO, reasons_by_name

BRAND = "오늘의 증시"
FOOTNOTE = "데이터: 네이버 증권, Yahoo Finance  |  정보 제공 목적, 투자 권유 아님"
WEEKDAY = "월화수목금토일"


PHOTO_REPORTS = ("weekly", "monthly")
# 주간·월간 표지 사진 검색어 — 매주 돌아가며 (같은 사진이 이어지지 않게)
PHOTO_QUERIES = ("stock market chart", "trading screen", "financial district night", "candlestick chart",
                 "city skyline night", "stock ticker screen")


def photo_query(day: dt.date) -> str:
    return PHOTO_QUERIES[day.isocalendar()[1] % len(PHOTO_QUERIES)]


def _date_label(snap: dict) -> str:
    d = dt.date.fromisoformat(snap["date"])
    return f"{d.year}. {d.month}. {d.day}. ({WEEKDAY[d.weekday()]})"


def _common(snap: dict) -> dict[str, Any]:
    return {"brand": BRAND, "report_name": REPORT_KO[snap["report"]], "date_label": _date_label(snap), "footnote": FOOTNOTE}


def _tone(v: float) -> str:
    return "up" if v > 0 else "down" if v < 0 else "flat"


def _with_reason(items: list[dict], reasons: dict[str, dict], **extra) -> list[dict]:
    out = []
    for it in items:
        r = reasons.get(it["name"], {})
        out.append({**it, "reason": r.get("reason"), "source": r.get("source"), **extra})
    return out


def _stock_rows(items: list[dict], reasons: dict[str, dict]) -> list[dict]:
    rows = []
    for it in items:
        r = reasons.get(it["name"], {})
        tag = {"KOSPI": "코스피", "KOSDAQ": "코스닥"}.get(it.get("market", ""), "")
        rows.append({"name": it["name"], "tag": tag, "pct": it["pct"], "price_text": price(it["price"]),
                     "reason": r.get("reason"), "source": r.get("source")})
    return rows


def slides(snap: dict[str, Any], ana: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    c = _common(snap)
    rep = snap["report"]
    d = dt.date.fromisoformat(snap["date"])
    out: list[tuple[str, dict]] = []

    if rep == "close":
        kospi, kosdaq, fx = snap["kospi"], snap["kosdaq"], snap["usd_krw"]
        out.append(("market_cover", {**c, "kicker": f"{d.month}월 {d.day}일 장 마감", "headline": ana["headline"],
                                     "day_tone": _tone(kospi["pct"]), "summary": ana["summary"],
                                     "boards": [{"name": "코스피", "value": f"{kospi['price']:,.2f}", "pct": kospi["pct"]},
                                                {"name": "코스닥", "value": f"{kosdaq['price']:,.2f}", "pct": kosdaq["pct"]},
                                                {"name": "원/달러", "value": f"{fx['price']:,.1f}", "pct": fx["pct"]}],
                                     "spark": snap.get("kospi_intraday"), "spark_label": "코스피 장중 흐름 (5분)"}))
        sec_r = reasons_by_name(ana.get("sectors"))
        items = [{"name": s["name"], "pct": s["pct"], "note": " ".join(s.get("leaders", [])[:3])}
                 for s in snap["sectors_top"][:5] + snap["sectors_bottom"][:5]]
        b = snap["breadth"]
        out.append(("bars", {**c, "title": "업종별 등락", "subtitle": f"오른 종목 {b['rise']:,} · 내린 종목 {b['fall']:,}",
                             "items": _with_reason(items, sec_r), "gap_after": 5}))
        mv = reasons_by_name(ana.get("movers"))
        out.append(("rows", {**c, "title": "시가총액 상위 10", "subtitle": "코스피 대형주", "ranked": True,
                             "items": _stock_rows(snap["market_cap"], mv)}))
        out.append(("rows", {**c, "title": "급등 종목", "subtitle": "거래대금 30억 이상, 코스피·코스닥",
                             "items": _stock_rows(snap["gainers"], mv)}))
        out.append(("rows", {**c, "title": "급락 종목", "subtitle": "거래대금 30억 이상, 코스피·코스닥",
                             "items": _stock_rows(snap["losers"], mv)}))
        if ana.get("watch"):
            out.append(("notes", {**c, "title": "내일 눈여겨볼 종목", "accent": "ink",
                                  "items": [{"head": w["name"], "body": w["why"]} for w in ana["watch"]]}))

    elif rep == "premarket":
        fx = snap["usd_krw"]
        boards = [{"name": q["name"], "value": f"{q['price']:,.2f}", "pct": q["pct"]} for q in snap["us_indices"]]
        boards.append({"name": "원/달러", "value": f"{fx['price']:,.1f}", "pct": fx["pct"]})
        cols = 2 if len(boards) > 3 else len(boards)
        sp = next((q for q in snap["us_indices"] if q["code"] == ".INX"), snap["us_indices"][0])
        out.append(("market_cover", {**c, "kicker": f"{d.month}월 {d.day}일 장 시작 전", "headline": ana["headline"],
                                     "day_tone": _tone(sp["pct"]), "summary": ana["summary"], "boards": boards[:6],
                                     "cols": cols}))
        us_r = reasons_by_name(ana.get("us_sectors"))
        out.append(("bars", {**c, "title": "밤사이 미국 섹터", "subtitle": "S&P 500 섹터 ETF 등락",
                             "items": _with_reason([{"name": s["name"], "pct": s["pct"], "note": s["code"]}
                                                    for s in snap["us_sectors"]], us_r)}))
        bt = reasons_by_name(ana.get("us_bigtech"))
        out.append(("rows", {**c, "title": "미국 빅테크", "subtitle": "종가 기준 (달러)",
                             "items": [{"name": q["name"], "tag": q["code"], "pct": q["pct"], "price_text": f"${q['price']:,.2f}",
                                        "reason": bt.get(q["name"], {}).get("reason"), "source": bt.get(q["name"], {}).get("source")}
                                       for q in snap["us_bigtech"]]}))
        prev = dt.date.fromisoformat(snap["kr_prev_date"])
        out.append(("bars", {**c, "title": "전 거래일 국내 업종", "subtitle": f"{prev.month}월 {prev.day}일 기준 상위·하위 5",
                             "items": [{"name": s["name"], "pct": s["pct"]} for s in snap["kr_sectors_top"] + snap["kr_sectors_bottom"]],
                             "gap_after": len(snap["kr_sectors_top"])}))
        if ana.get("checkpoints"):
            out.append(("notes", {**c, "title": "오늘 체크포인트", "items": [{"head": t} for t in ana["checkpoints"]]}))

    else:  # weekly / monthly
        word = "이번 주" if rep == "weekly" else "이번 달"
        start = dt.date.fromisoformat(snap["period_start"])
        # 끝 날짜는 만든 날이 아니라 마지막 거래일 (주말에 만들어도 '11/16 – 11/20')
        last = snap["kospi_daily"][-1][0] if snap.get("kospi_daily") else None
        end = min(d, dt.date.fromisoformat(str(last)[:10])) if last else d
        idx = {q["name"]: q for q in snap["indices"]}
        boards = [{"name": n, "value": f"{idx[n]['price']:,.2f}", "pct": idx[n]["pct"]} for n in ("코스피", "코스닥", "나스닥") if n in idx]
        kospi_pct = idx.get("코스피", {"pct": 0})["pct"]
        out.append(("market_cover", {**c, "kicker": f"{start.month}/{start.day} – {end.month}/{end.day} {REPORT_KO[rep]}",
                                     "headline": ana["headline"], "day_tone": _tone(kospi_pct), "summary": ana["summary"],
                                     "boards": boards, "spark": [v for _, v in snap["kospi_daily"]],
                                     "spark_label": f"코스피 일별 종가 (최근 {len(snap['kospi_daily'])}거래일)"}))
        sec_r = reasons_by_name(ana.get("sectors"))
        out.append(("bars", {**c, "title": f"{word} 업종 수익률", "subtitle": "업종 대표 ETF 기준",
                             "items": _with_reason([{"name": s["name"], "pct": s["pct"]} for s in snap["sectors"]], sec_r)}))
        mv = reasons_by_name(ana.get("movers"))
        out.append(("rows", {**c, "title": f"{word} 대형주 강세", "subtitle": "코스피 시가총액 상위 30 중",
                             "items": _stock_rows(snap["caps_top"], mv)}))
        out.append(("rows", {**c, "title": f"{word} 대형주 약세", "subtitle": "코스피 시가총액 상위 30 중",
                             "items": _stock_rows(snap["caps_bottom"], mv)}))
        if ana.get("issues"):
            out.append(("notes", {**c, "title": f"{word} 시장을 움직인 이슈", "items": [{"head": t} for t in ana["issues"]]}))
        if ana.get("next"):
            nxt = "다음 주" if rep == "weekly" else "다음 달"
            out.append(("notes", {**c, "title": f"{nxt} 볼 일정", "items": [{"head": t} for t in ana["next"]]}))
    return out[:10]  # 캐러셀 최대 10장
