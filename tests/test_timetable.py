from datetime import datetime
from zoneinfo import ZoneInfo

from uploader import timetable

KST = ZoneInfo("Asia/Seoul")
NOW = datetime(2026, 11, 18, 18, 0, tzinfo=KST)


def _row(name, status="사용", **kw):
    p = {"이름": {"type": "title", "title": [{"plain_text": name}]},
         "상태": {"type": "select", "select": {"name": status}}}
    for k, v in kw.items():
        if k in ("주기", "주제", "생성기"):
            p[k] = {"type": "select", "select": {"name": v}}
        elif k == "요일":
            p[k] = {"type": "multi_select", "multi_select": [{"name": d} for d in v.split(",")]}
        elif k == "다음":
            p["다음 게시"] = {"type": "date", "date": {"start": v}}
        else:
            p[k.replace("_", " ")] = {"type": "rich_text", "rich_text": [{"plain_text": v}]}
    return {"properties": p}


def test_grid_merges_market_plan_and_one_offs():
    rows = [
        _row("기술 브리핑", 주기="매주", 요일="월,목", 주제="테크·AI", 게시_시각="12:00"),
        _row("만약에 3D", "일시중지", 주기="매주", 요일="수", 주제="테크·AI", 게시_시각="20:00"),
        _row("깃허브 Top 5", 주기="매주", 요일="일", 주제="테크·AI", 게시_시각="12:00", 생성기="깃허브 트렌딩 릴스"),
        _row("용어 5분", 주기="한 번", 주제="증시", 게시_시각="12:00", 다음="2026-11-20T12:00:00+09:00"),
        _row("끝난 것", "완료", 주기="매주", 요일="화", 게시_시각="12:00"),
    ]
    g = timetable.grid(rows, [(datetime(2026, 11, 19, 20, 0, tzinfo=KST), "game", "랩워즈 외전 7편")], NOW)
    assert list(g) == sorted(g)
    assert g["07:30"]["월"] == ["📈 장 전 브리핑"] and g["07:30"]["토"] == []
    assert g["09:00"]["토"] == ["📈 주간 증시 리포트"]
    assert "💜 기술 브리핑" in g["12:00"]["목"]
    assert g["20:00"]["수"] == ["⏸ 💜 만약에 3D"]
    assert "💜 깃허브 Top 5 캐러셀" in g["12:00"]["일"] and "💜 깃허브 Top 5 릴스" in g["20:00"]["일"]
    assert "📈 용어 5분 (11/20만)" in g["12:00"]["금"]
    assert g["12:00"]["화"] == []
    assert g["20:00"]["목"] == ["📌 🎮 랩워즈 외전 7편 (11/19)"]
