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
    p = photos.Photo(Path("cover-photo.jpg"), "Apke", "https://pixabay.com/x")
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


def test_relevant_needs_query_word_in_tags():
    assert photos.relevant(hit("stock market, finance"), "stock market chart")
    assert not photos.relevant(hit("machine, tractor"), "stock market chart")


def test_search_puts_theme_colors_first(monkeypatch):
    calls = []

    class R:
        def __init__(self, hits):
            self.hits = hits

        def raise_for_status(self):
            pass

        def json(self):
            return {"hits": self.hits}

    def fake_get(url, params, timeout):
        color, orient = params.get("colors"), params["orientation"]
        calls.append((color, orient))
        base = 100 if color else 200
        hits = [{**hit("server room"), "id": base + j} for j in range(2)] if orient == "vertical" else []
        hits.append({**hit("tractor"), "id": base + 9})  # 검색어와 상관없는 사진은 빠진다
        return R(hits)

    monkeypatch.setattr(photos.requests, "get", fake_get)
    got = photos.search("server room", "k", "blue,lilac", n=3)
    assert [h["id"] for h in got] == [100, 101, 200]
    assert calls == [("blue,lilac", "vertical"), ("blue,lilac", "horizontal"), (None, "vertical")]


def test_inner_photo_slots_skip_cover_last_and_neighbors():
    s = [{"type": "cover", "photo": "a"}, {"photo": "b"}, {"photo": "c"}, {}, {"photo": "d"}, {"photo": "e"}, {"photo": "f"}]
    assert custom.inner_photo_slots(s) == [1, 4]  # 2는 1 바로 뒤, 6은 마지막 장, 최대 2장
    assert custom.inner_photo_slots([{"type": "cover"}, {}, {}]) == []


def test_inner_photo_replaces_art_on_that_slide_only():
    content = {"slides": [{"type": "cover", "title": "t"}, {"type": "points", "title": "a"},
                          {"type": "stat", "title": "b", "value": "3배"}, {"type": "points", "title": "끝"}]}
    ph = photos.Photo(Path("slide3-photo.jpg"), "Apke", "https://pixabay.com/x", 7)
    out = custom.template_slides(content, ["<svg/>"] * 4, inner={2: ph})
    assert out[2][1]["photo"].endswith("slide3-photo.jpg") and out[2][1]["art"] is None
    assert "Apke" in out[2][1]["photo_credit"]
    assert out[1][1]["art"] == "<svg/>" and not out[1][1].get("photo")


def test_several_credits_in_one_line():
    a = photos.Photo(Path("cover-photo.jpg"), "Apke", "p")
    b = photos.Photo(Path("slide3-photo.jpg"), "Kim", "p")
    cap = photos.add_credit("훅\n\n#AI", [a, b, a])
    assert "사진: Apke, Kim / Pixabay\n\n#AI" in cap
    assert photos.add_credit("훅", [b]).endswith("\n\n사진: Kim / Pixabay")
