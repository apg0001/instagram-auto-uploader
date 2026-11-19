import time
from types import SimpleNamespace

from uploader import engagement
from uploader.db import Post


def test_new_comments_only_unseen_and_nonempty():
    media = [{"id": "m1", "comments": [{"id": "c1", "text": "MCP 다뤄 주세요"}, {"id": "c2", "text": "  "},
                                       {"id": "c3", "text": "좋아요"}]},
             {"id": "m2", "comments": None}]
    out = engagement.new_comments(media, {"c3"}, {"m1": "[클로드] MCP"})
    assert out == [("c1", "[클로드] MCP", "MCP 다뤄 주세요")]


class FakeDB:
    def __init__(self, posts):
        self.posts, self.kv = posts, {}

    def by_status(self, *s):
        return [p for p in self.posts if p.status in s]

    def kv_get(self, k):
        return self.kv.get(k)

    def kv_set(self, k, v):
        self.kv[k] = v


class FakeIG:
    def __init__(self):
        self.asked = []

    def engagement(self, mid):
        self.asked.append(mid)
        return {"id": mid, "like_count": 5, "comments_count": 1, "comments": [{"id": "c-" + mid, "text": "다음엔 이거"}]}


def test_collect_asks_recent_only_and_remembers_seen(monkeypatch):
    now = time.time()
    posts = [Post("a", "plan-tech", "CAROUSEL", "게시됨", "[A] 최근", "", [], ig_media_id="m1", decided_at=now - 3600),
             Post("b", "plan-tech", "CAROUSEL", "게시됨", "[B] 오래됨", "", [], ig_media_id="m2", decided_at=now - 10 * 86400),
             Post("c", "plan-tech", "CAROUSEL", "승인", "[C] 아직", "", [], ig_media_id=None)]
    ig = FakeIG()
    pl = SimpleNamespace(cfg=SimpleNamespace(raw={"engagement": {"enabled": True, "days": 3}}, caption={}),
                         db=FakeDB(posts), notion=None, _instagram=lambda: ig)
    r = engagement.collect(pl)
    assert ig.asked == ["m1"] and r["posts"] == 1 and r["comments"] == 1
    assert "c-m1" in pl.db.kv[engagement.SEEN_KEY]
    assert engagement.collect(pl)["comments"] == 0  # 두 번째엔 새 댓글 없음


def test_collect_off_by_default():
    pl = SimpleNamespace(cfg=SimpleNamespace(raw={}, caption={}))
    assert engagement.collect(pl) == {}
