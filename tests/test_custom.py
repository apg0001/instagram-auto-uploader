from pathlib import Path

from uploader.caption import build_prompt, link_of, validate
from uploader.custom import media_kind, template_slides

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
    slides = template_slides(content)
    assert [n for n, _ in slides] == ["notes", "notes", "notes"]
    assert slides[0][1]["lead"] == "S"
    assert slides[2][1]["items"][0]["head"] == "42% 점유율"
    assert slides[0][1]["footnote"] == "출처는 캡션에"  # 잘린 첫 출처가 모든 장에 찍히지 않게
