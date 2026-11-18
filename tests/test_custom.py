from pathlib import Path

from uploader.caption import build_prompt, link_of, validate
from uploader.custom import clean_svg, media_kind, template_slides

REPO = {"mode": "repo", "title": "Agent Office", "hashtag": "AgentOffice", "tagline": "픽셀 사무실",
        "repo_url": "https://github.com/apg0001/Agent-Office", "facts": ["셀프 호스팅"]}


def test_repo_mode_allows_repo_and_requires_link():
    assert link_of(REPO) == REPO["repo_url"]
    cap = f"오픈소스로 공개했어요\n▶ GitHub: {REPO['repo_url']}"
    assert validate(cap, link_of(REPO), allow_repo=True) == []
    assert "필수 링크 없음" in validate("오픈소스", link_of(REPO), allow_repo=True)
    prompt = build_prompt(REPO, "CAROUSEL", "레포 소개", ["표지"], [])
    assert "GitHub" in prompt and "베타" in prompt


def test_media_kind():
    assert media_kind([Path("a.jpg")]) == ("IMAGE", ["image"])
    assert media_kind([Path("a.mp4")]) == ("REELS", ["video"])
    assert media_kind([Path("a.jpg"), Path("b.png")])[0] == "CAROUSEL"


def test_template_slides_maps_types():
    content = {"slides": [{"type": "cover", "title": "T", "subtitle": "S"},
                          {"type": "points", "title": "P", "items": [{"head": "h", "body": "b"}]},
                          {"type": "stat", "title": "N", "value": "42%", "label": "점유율", "body": "설명"}],
               "sources": ["연합뉴스"]}
    slides = template_slides(content, ["<svg/>", None], kicker="주간 논문 픽")
    assert [n for n, _ in slides] == ["story", "story", "story"]
    assert [c["kind"] for _, c in slides] == ["cover", "points", "stat"]
    assert slides[0][1]["subtitle"] == "S" and slides[0][1]["kicker"] == "주간 논문 픽"
    assert slides[0][1]["art"] == "<svg/>" and slides[1][1]["art"] is None and slides[2][1]["art"] is None
    assert slides[2][1]["value"] == "42%"
    assert slides[0][1]["footnote"] == "출처는 캡션에"  # 잘린 첫 출처가 모든 장에 찍히지 않게


def test_clean_svg_keeps_shapes_and_drops_unsafe_parts():
    raw = ('<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 900 420" width="900" onload="alert(1)">'
           '<script>alert(1)</script><defs><linearGradient id="g"><stop offset="0" stop-color="#fff"/></linearGradient></defs>'
           '<rect x="1" y="2" width="3" height="4" fill="url(#g)"/><circle cx="1" cy="1" r="2" fill="url(http://x)"/>'
           '<image href="http://x/a.png"/><text>hi</text><path d="M0 0L1 1" stroke="#000"/></svg>')
    out = clean_svg(raw)
    assert out.startswith("<svg") and 'viewBox="0 0 900 420"' in out and "linearGradient" in out
    assert "script" not in out and "onload" not in out and "http://x" not in out
    assert "<image" not in out and "<text" not in out and 'fill="url(#g)"' in out and "<path" in out
    assert clean_svg("<div>not svg</div>") is None and clean_svg("<svg><broken") is None


def test_cover_numbered_subtitle_becomes_lines():
    from uploader.custom import split_teaser

    assert split_teaser("이번 주 ① 코딩 에이전트 ② 블렌더 영상 ③ 속도 읽는 모델") == (
        "이번 주", ["코딩 에이전트", "블렌더 영상", "속도 읽는 모델"])
    assert split_teaser("그냥 부제") == ("그냥 부제", [])
    s = template_slides({"slides": [{"type": "cover", "title": "T", "subtitle": "", "points": ["a", "b"]}]})
    assert s[0][1]["teaser"] == ["a", "b"]
