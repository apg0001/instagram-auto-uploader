from pathlib import Path

from uploader import shots


def test_public_url_rejects_non_https_and_private():
    assert not shots.public_url("http://example.com")
    assert not shots.public_url("https://localhost/x")
    assert not shots.public_url("https://127.0.0.1/")
    assert not shots.public_url("file:///etc/passwd")
    assert not shots.public_url("")


def test_insert_places_shots_after_given_slides(monkeypatch, tmp_path):
    media = [Path(f"{i}.jpg") for i in range(1, 6)]
    monkeypatch.setattr(shots, "capture", lambda urls, out: [Path(f"p{i}.png") for i in range(len(urls))])
    monkeypatch.setattr(shots, "slide", lambda imgs, head, cap, topic, out, source="": out)
    content = {"shots": [{"url": "https://a.dev/x", "head": "A", "after": 2},
                         {"url": "https://b.dev/y", "head": "B", "after": 4},
                         {"url": "https://c.dev/z", "head": "셋째는 버림"}]}
    out = shots.insert(content, media, tmp_path, "tech")
    names = [p.name for p in out]
    assert names == ["1.jpg", "2.jpg", "shot0.jpg", "3.jpg", "4.jpg", "shot1.jpg", "5.jpg"]


def test_insert_skips_failed_and_respects_ten(monkeypatch, tmp_path):
    media = [Path(f"{i}.jpg") for i in range(1, 10)]
    monkeypatch.setattr(shots, "capture", lambda urls, out: [None for _ in urls])
    out = shots.insert({"shots": [{"url": "https://a.dev"}]}, media, tmp_path, "tech")
    assert out == media
    full = [Path(f"{i}.jpg") for i in range(10)]
    assert shots.insert({"shots": [{"url": "https://a.dev"}]}, full, tmp_path, "tech") == full
