from datetime import datetime
from zoneinfo import ZoneInfo

from uploader import workspace

KST = ZoneInfo("Asia/Seoul")


def rich(text):  # 노션이 돌려주는 모양 (plain_text 포함)
    return [{"type": "text", "text": {"content": text}, "plain_text": text}]


def row(**props):
    out = {}
    for k, v in props.items():
        if k in ("상태", "출처", "주기", "생성기"):
            out[k] = {"type": "select", "select": {"name": v} if v else None}
        elif k == "요일":
            out[k] = {"type": "multi_select", "multi_select": [{"name": d} for d in v.split(",")]}
        elif k in ("예약", "다음 게시"):
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


def test_week_rows_mix_posts_plan_and_market():
    now = datetime(2026, 11, 20, 10, 0, tzinfo=KST)  # 금
    posts = [row(제목="[랩워즈 소개] 장비 뽑기", 상태="승인", 예약="2026-11-20T20:00:00+09:00"),
             row(제목="[X] 다음 주", 상태="승인", 예약="2026-11-23T12:00:00+09:00")]
    plan = [row(이름="증시 용어 한 장", 상태="사용", 주기="매주", 요일="금", **{"게시 시각": "12:00"},
                **{"다음 게시": "2026-11-20T12:00:00+09:00"}),
            row(이름="AI 공감 모먼트", 상태="사용", 주기="매주", 요일="토", **{"게시 시각": "12:00"}),
            row(이름="쉬는 줄", 상태="일시중지", 주기="매주", 요일="토", **{"게시 시각": "16:00"})]
    rows = workspace.week_rows(now, posts, plan)
    got = [(f"{t:%a %H:%M}", w, s) for t, w, s in rows]
    assert ("Fri 12:00", "증시 용어 한 장", "⏳ 만들 예정") in got
    assert ("Fri 16:00", "📈 장 마감 시황", "🤖 자동") in got
    assert ("Fri 20:00", "랩워즈 소개 · 장비 뽑기", "✅ 준비됨") in got
    assert ("Sat 09:00", "📈 주간 증시 리포트", "🤖 자동") in got
    assert ("Sat 12:00", "AI 공감 모먼트", "⏳ 만들 예정") in got
    assert not any("다음 주" in w or "쉬는" in w or "07:30" in t for t, w, _ in got)
    assert [r[0] for r in rows] == sorted(r[0] for r in rows)
    table = workspace.week_table(rows)
    assert table["table"]["table_width"] == 4 and len(table["table"]["children"]) == len(rows) + 1
