from datetime import datetime
from zoneinfo import ZoneInfo

from uploader import workspace

KST = ZoneInfo("Asia/Seoul")


def rich(text):  # 노션이 돌려주는 모양 (plain_text 포함)
    return [{"type": "text", "text": {"content": text}, "plain_text": text}]


def row(**props):
    out = {}
    for k, v in props.items():
        if k in ("상태", "출처"):
            out[k] = {"type": "select", "select": {"name": v} if v else None}
        elif k == "예약":
            out[k] = {"type": "date", "date": {"start": v}}
        elif k in ("제목", "아이디어", "이름"):
            out[k] = {"type": "title", "title": rich(v)}
        else:
            out[k] = {"type": "rich_text", "rich_text": rich(v)}
    return {"properties": out}


def test_summary_lists_today_and_todo():
    now = datetime(2026, 11, 19, 10, 0, tzinfo=KST)
    posts = [
        row(제목="[A] 오늘 낮", 상태="승인", 예약="2026-11-19T12:00:00+09:00"),
        row(제목="[B] 오늘 밤", 상태="초안", 예약="2026-11-19T20:00:00+09:00"),
        row(제목="[C] 내일", 상태="승인", 예약="2026-11-20T12:00:00+09:00"),
        row(제목="[D] 어제 실패", 상태="실패"),
    ]
    plan = [row(이름="시리즈", 상태="검토 대기")]
    ideas = [row(아이디어="새거", 상태=""), row(아이디어="끝난 거", 상태="정리됨")]
    text = workspace.summary_text(now, posts, plan, ideas)
    assert "11/19(목)" in text
    assert text.index("12:00  A · 오늘 낮") < text.index("20:00  B · 오늘 밤")
    assert "[C]" not in text
    assert "확인할 초안 1개" in text and "실패·오류 1개" in text
    assert "검토 대기 시리즈 1개" in text and "아이디어 1개" in text


def test_summary_nothing_to_do():
    now = datetime(2026, 11, 19, 10, 0, tzinfo=KST)
    text = workspace.summary_text(now, [], [], [])
    assert "예약된 게시물 없음" in text and "할 일: 없음" in text


def test_add_candidate_keeps_rules_and_drops_oldest():
    rules = "규칙 본문"
    r = workspace.add_candidate(rules, "첫 후보")
    r = workspace.add_candidate(r, "둘째 후보")
    assert r.startswith("규칙 본문") and r.count(workspace.CANDIDATES) == 1
    assert r.endswith("- 첫 후보\n- 둘째 후보")
    long = workspace.add_candidate("x" * 1800, "a" * 60)
    long = workspace.add_candidate(long, "b" * 60)
    assert long.startswith("x" * 1800) and len(long) <= 1900 and "b" * 60 in long and "a" * 60 not in long


def test_short_title():
    assert workspace.short_title("[주 2회 신규 기술 브리핑] [기술 브리핑] Gemini 출시") == "주 2회 신규 기술 브리핑 · Gemini 출시"
    assert workspace.short_title("그냥 제목") == "그냥 제목"
    assert len(workspace.short_title("[a] " + "가" * 80)) == 48
