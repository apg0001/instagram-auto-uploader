import json
import time
from types import SimpleNamespace

from uploader import film


class KV:
    def __init__(self):
        self.d = {}

    def kv_get(self, k):
        return self.d.get(k)

    def kv_set(self, k, v):
        self.d[k] = v


def fake_pl(tmp_path):
    calls = {"reel": [], "fail": []}
    pl = SimpleNamespace(db=KV(), cfg=SimpleNamespace(data_dir=tmp_path, caption={}, raw={}))
    pl.generate_slide_reel = lambda *a, **k: calls["reel"].append(a)
    pl.record_failure = lambda src, e: calls["fail"].append((src, str(e)))
    return pl, calls


def job(tmp_path, **kw):
    (tmp_path / "content.json").write_text(json.dumps({"title": "t"}), encoding="utf-8")
    base = {"name": "j", "local": str(tmp_path), "source": "plan-x", "title": "T", "theme": "tech",
            "reel_at": time.time() + 10 * 3600, "caption": "c", "cards": ["a.png"], "voice": {},
            "state": "running", "created": time.time(), "started": time.time()}
    return {**base, **kw}


def test_write_script_retries_until_valid(monkeypatch):
    replies = iter([
        json.dumps({"lines": [{"text": "하나"}], "caption": "x"}),
        json.dumps({"lines": [{"text": f"줄 {i}", "say": f"줄 {i}"} for i in range(8)], "caption": "훅\n#a #b #c #d #e"}),
    ])
    monkeypatch.setattr(film, "call_claude", lambda prompt, cfg: next(replies))
    out = film.write_script({"title": "t", "slides": []}, {})
    assert len(out["lines"]) == 8 and out["lines"][0]["say"] == "줄 0"


def test_film_prompt_mentions_premade_voice_and_sources():
    p = film.film_prompt({"title": "제목", "slides": [], "sources": [{"name": "공식 문서"}]}, 8)
    assert "/lemo-opuscar:lemo-opuscar" in p and "l8.wav" in p and "공식 문서" in p and "TTS" in p


def test_poll_keeps_running_job(tmp_path, monkeypatch):
    pl, calls = fake_pl(tmp_path)
    film.save(pl, [job(tmp_path)])
    monkeypatch.setattr(film, "check", lambda j: "running")
    film.poll(pl)
    assert len(film.load(pl)) == 1 and not calls["reel"]


def test_poll_falls_back_when_failed_or_late(tmp_path, monkeypatch):
    pl, calls = fake_pl(tmp_path)
    monkeypatch.setattr(film, "ssh", lambda *a, **k: "")
    film.save(pl, [job(tmp_path)])
    monkeypatch.setattr(film, "check", lambda j: "failed")
    film.poll(pl)
    assert film.load(pl) == [] and len(calls["reel"]) == 1 and calls["fail"][0][0] == "plan-x:film"

    film.save(pl, [job(tmp_path, reel_at=time.time() + 3600)])  # 게시 1시간 전인데 아직 제작 중
    monkeypatch.setattr(film, "check", lambda j: "running")
    film.poll(pl)
    assert film.load(pl) == [] and len(calls["reel"]) == 2


def test_poll_finishes_done_job_and_falls_back_on_bad_file(tmp_path, monkeypatch):
    pl, calls = fake_pl(tmp_path)
    done = []
    film.save(pl, [job(tmp_path)])
    monkeypatch.setattr(film, "check", lambda j: "done")
    monkeypatch.setattr(film, "finish", lambda p, j: done.append(j["name"]))
    film.poll(pl)
    assert done == ["j"] and film.load(pl) == [] and not calls["reel"]

    film.save(pl, [job(tmp_path)])

    def bad(p, j):
        raise film.FilmError("내레이션 파일이 바뀜")
    monkeypatch.setattr(film, "finish", bad)
    film.poll(pl)
    assert film.load(pl) == [] and len(calls["reel"]) == 1


def test_poll_launches_queued_job_when_idle(tmp_path, monkeypatch):
    pl, _ = fake_pl(tmp_path)
    film.save(pl, [job(tmp_path, state="queued")])
    monkeypatch.setattr(film, "busy", lambda: False)
    monkeypatch.setattr(film, "launch", lambda j: j.update(state="running", started=time.time()))
    film.poll(pl)
    assert film.load(pl)[0]["state"] == "running"


def test_run_ps1_passes_prompt_on_stdin():
    # 인자로 넘기면 PowerShell 5가 큰따옴표를 이스케이프하지 않아 인자가 쪼개진다
    assert "-p $prompt" not in film.RUN_PS1
    assert "$prompt | &" in film.RUN_PS1 and "UTF8Encoding" in film.RUN_PS1
