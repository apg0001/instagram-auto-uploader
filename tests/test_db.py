from uploader.db import APPROVED, DB, DRAFT, Post


def test_db_roundtrip(tmp_path):
    db = DB(tmp_path / "s.db")
    p = Post(id="20261117-1-lab-wars-reels", source="lab-wars", kind="REELS", status=DRAFT, title="t",
             caption="캡션", media=[{"file": "a.mp4", "url": "https://x/a.mp4", "type": "video"}],
             meta={"cover_url": "https://x/c.jpg"})
    db.save(p)
    got = db.get(p.id)
    assert got == p
    got.status = APPROVED
    db.save(got)
    assert [x.id for x in db.by_status(APPROVED)] == [p.id]
    assert db.find("20261117-1").id == p.id
    assert db.recent_captions("lab-wars") == ["캡션"]
    db.kv_set("k", "v")
    assert db.kv_get("k") == "v"
