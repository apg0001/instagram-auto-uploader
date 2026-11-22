from pathlib import Path

from uploader import custom, photos


def hit(tags, w=4000, h=6000):
    return {"tags": tags, "imageWidth": w, "imageHeight": h}


def test_usable_skips_people_logos_and_small():
    assert photos.usable(hit("server room, data center"))
    assert not photos.usable(hit("woman, laptop, office"))
    assert not photos.usable(hit("apple logo, phone"))
    assert not photos.usable(hit("businessman, chart"))
    assert not photos.usable(hit("night sky", w=800))


def test_credit_goes_before_hashtags():
    p = photos.Photo(Path("x.jpg"), "Apke", "https://pixabay.com/x")
    cap = "훅\n본문\n\n#AI #테크"
    out = photos.add_credit(cap, p)
    assert out == "훅\n본문\n표지 사진: Apke / Pixabay\n\n#AI #테크"
    assert photos.add_credit("해시태그 없음", p).endswith("\n\n표지 사진: Apke / Pixabay")


def test_photo_only_on_cover_and_replaces_art():
    p = photos.Photo(Path("/tmp/c.jpg"), "A", "u")
    content = {"slides": [{"type": "cover", "title": "t", "subtitle": "s"},
                          {"type": "points", "title": "p", "items": []}]}
    slides = custom.template_slides(content, ["<svg/>", "<svg/>"], None, p)
    assert slides[0][1]["photo"].startswith("file://") and slides[0][1]["art"] is None
    assert "photo" not in slides[1][1] and slides[1][1]["art"] == "<svg/>"
    assert custom.template_slides(content)[0][1]["photo"] is None


def test_find_without_key_or_query(monkeypatch, tmp_path):
    monkeypatch.delenv("PIXABAY_API_KEY", raising=False)
    assert photos.find("server room", tmp_path) is None
    monkeypatch.setenv("PIXABAY_API_KEY", "k")
    assert photos.find(None, tmp_path) is None
