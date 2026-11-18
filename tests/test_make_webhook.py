import pytest
import requests

from uploader import make_webhook
from uploader.config import Config
from uploader.instagram import InstagramError
from uploader.make_webhook import MakeWebhook, payload

MEDIA = [{"url": "https://x/1.jpg", "type": "image"}, {"url": "https://x/2.mp4", "type": "video"}]


class Resp:
    def __init__(self, status=200, body=None, text=""):
        self.status_code, self._body, self.text = status, body, text

    def json(self):
        if self._body is None:
            raise ValueError
        return self._body


def test_payload_shapes_media():
    p = payload("CAROUSEL", MEDIA, "캡션", None, "abc")
    assert p["kind"] == "CAROUSEL" and p["post_id"] == "abc" and p["count"] == 2
    assert p["image_url"] == "https://x/1.jpg" and p["video_url"] == "https://x/2.mp4"
    assert [(i["type"], i["url"]) for i in p["media"]] == [("IMAGE", "https://x/1.jpg"), ("VIDEO", "https://x/2.mp4")]
    assert p["media"][0]["image_url"] == "https://x/1.jpg" and p["media"][1]["video_url"] == "https://x/2.mp4"
    assert p["media"][1]["media_type"] == "VIDEO"


def test_publish_returns_id_and_permalink(monkeypatch):
    sent = {}

    def post(url, json, timeout):
        sent.update(json)
        return Resp(body={"id": "179", "permalink": "https://instagram.com/p/X"})

    monkeypatch.setattr(make_webhook.requests, "post", post)
    hook = MakeWebhook("https://hook")
    assert hook.publish("CAROUSEL", MEDIA, "캡션", post_id="abc") == "179"
    assert hook.permalink("179") == "https://instagram.com/p/X"
    assert sent["caption"] == "캡션"


@pytest.mark.parametrize("resp", [Resp(body=None, text="Accepted"), Resp(status=500, text="boom")])
def test_publish_without_id_fails(monkeypatch, resp):
    monkeypatch.setattr(make_webhook.requests, "post", lambda *a, **k: resp)
    with pytest.raises(InstagramError):
        MakeWebhook("https://hook").publish("IMAGE", MEDIA[:1], "c")


def test_timeout_is_not_retried_silently(monkeypatch):
    def post(*a, **k):
        raise requests.Timeout

    monkeypatch.setattr(make_webhook.requests, "post", post)
    with pytest.raises(InstagramError, match="게시 여부"):
        MakeWebhook("https://hook").publish("IMAGE", MEDIA[:1], "c")


def test_ig_enabled_by_mode(monkeypatch):
    for k in ("IG_USER_ID", "IG_ACCESS_TOKEN", "MAKE_WEBHOOK_URL"):
        monkeypatch.delenv(k, raising=False)
    cfg = Config({"instagram": {"enabled": True, "mode": "make"}})
    assert not cfg.ig_enabled
    monkeypatch.setenv("MAKE_WEBHOOK_URL", "https://hook")
    assert cfg.ig_enabled
    assert not Config({"instagram": {"enabled": False, "mode": "make"}}).ig_enabled
    assert not Config({"instagram": {"enabled": True}}).ig_enabled  # graph 모드는 IG 토큰 필요
