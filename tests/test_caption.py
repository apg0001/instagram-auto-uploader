import pytest

from uploader.caption import CaptionError, build_prompt, parse, validate

URL = "https://apg0001.github.io/lab-wars/"
SRC = {"title": "랩워즈", "tagline": "교수님에게서 연구실을 지켜라!", "play_url": URL, "facts": ["라인 디펜스"]}


def test_valid_caption_passes():
    assert validate(f"훅\n본문\n▶ 플레이: {URL}\n#랩워즈", URL) == []


def test_missing_play_link():
    assert "필수 링크 없음" in validate("훅 #랩워즈", URL)


@pytest.mark.parametrize("bad", ["github.com/apg0001/lab-wars", "레포 구경 오세요", "오픈소스로 공개", "소스 코드는"])
def test_repo_mentions_are_banned(bad):
    problems = validate(f"{bad}\n{URL}", URL)
    assert any(p.startswith("금지 표현") for p in problems)


def test_play_url_on_github_io_is_allowed():
    assert validate(f"플레이: {URL}", URL) == []


def test_too_long_and_too_many_hashtags():
    problems = validate(URL + "가" * 2300, URL)
    assert any("초과" in p for p in problems)
    assert any("해시태그" in p for p in validate(URL + " #a" * 31, URL))


def test_hashtag_limit_is_five_and_ignores_csharp():
    from uploader.caption import count_hashtags
    assert count_hashtags("C# 입문 #1위 아님\n#a #b #c") == 4
    assert not any("해시태그" in p for p in validate(URL + " C#\n#a #b #c #d #e", URL))
    assert any("해시태그" in p for p in validate(URL + "\n#a #b #c #d #e #f", URL))


def test_parse_extracts_json_from_noise():
    assert parse('앞말\n{"caption": "안녕"}\n뒷말') == "안녕"


def test_parse_errors():
    with pytest.raises(CaptionError):
        parse("JSON 없음")
    with pytest.raises(CaptionError):
        parse('{"text": "키가 다름"}')


def test_prompt_contains_rules_and_link():
    prompt = build_prompt(SRC, "REELS", "3-10 리뷰어 2의 습격", ["영상"], ["예전 캡션"])
    assert URL in prompt and "3-10" in prompt and "예전 캡션" in prompt
    assert "레포" in prompt  # 금지 규칙이 들어 있다
