from PIL import Image

from uploader import compose, themes


def test_topic_of_maps_sources_and_config_override():
    assert themes.topic_of("lab-wars") == "game"
    assert themes.topic_of("lab-wars:side") == "game"
    assert themes.topic_of("market-close") == "market"
    assert themes.topic_of("agent-office") == "project"
    assert themes.topic_of("topic") == "tech"
    assert themes.topic_of("lab-wars", {"topic": "career"}) == "career"
    assert themes.topic_of("lab-wars", {"topic": "없는주제"}) == "game"


def test_css_has_card_variables():
    for name in themes.THEMES:
        v = themes.css(name)
        assert {"paper", "surface", "ink", "muted", "accent"} <= v.keys()
        assert all(x.startswith("#") and len(x) == 7 for x in v.values())
    assert themes.css("project")["up"] == "#F5C542"   # 어두운 바탕용 상승색
    assert "up" not in themes.css("market")           # 증시는 빨강·파랑 기본값 유지


def test_slide_uses_theme_colors(tmp_path):
    for name, t in themes.THEMES.items():
        out = compose.slide(None, "제목", "본문", tmp_path / f"{name}.jpg", "footer", theme=t)
        img = Image.open(out).convert("RGB")
        band = img.getpixel((540, 5))
        bg = img.getpixel((540, 600))
        assert all(abs(a - b) < 12 for a, b in zip(band, t["accent"], strict=True)), name
        assert all(abs(a - b) < 12 for a, b in zip(bg, t["bg"], strict=True)), name
