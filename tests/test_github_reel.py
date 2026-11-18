from datetime import date

import numpy as np
import pytest

from uploader import bgm, github_reel
from uploader.sources import github_trending as gt

HTML = """
<article class="Box-row"><h2><a href="/a/one">a / one</a></h2><p>First repo</p>
 <span itemprop="programmingLanguage">Python</span><a href="/a/one/stargazers">1,234</a>
 <span class="d-inline-block float-sm-right">300 stars this week</span></article>
<article class="Box-row"><h2><a href="/b/two">b / two</a></h2>
 <a href="/b/two/stargazers">99,000</a><span class="d-inline-block float-sm-right">2,500 stars this week</span></article>
"""


def test_parse_sorts_by_weekly_stars():
    repos = gt.parse(HTML)
    assert [r.name for r in repos] == ["b/two", "a/one"]
    assert repos[0].stars_week == 2500 and repos[0].stars_total == 99000 and repos[0].rank == 1
    assert repos[1].language == "Python" and repos[1].description == "First repo"


def test_snapshot_and_week_over_week_change(tmp_path):
    last = gt.parse(HTML)
    gt.save(last, tmp_path, date(2026, 11, 15))
    gt.save(last, tmp_path, date(2026, 11, 20))  # 너무 가까운 스냅샷은 지난주로 치지 않는다
    prev = gt.previous(tmp_path, date(2026, 11, 22))
    assert set(prev) == {"a/one", "b/two"}
    now = gt.Repo("a/one", "", None, stars_week=900, stars_total=2000, rank=1)
    c = gt.change(now, prev)
    assert c == {"new": False, "prev_rank": 2, "prev_week": 300, "diff_week": 600}
    assert gt.change(gt.Repo("c/new", "", None, 1, 1, 1), prev)["new"] is True
    assert gt.change(now, {})["new"] is None
    assert "▲1" in github_reel._change_text(c, 1)


def test_pick_skips_excluded_and_needs_five():
    repos = [gt.Repo(f"o/r{i}", "", None, 100 - i, 1000, i + 1) for i in range(7)]
    script = {"repos": [{"name": f"o/r{i}", "exclude": i == 1} for i in range(7)]}
    assert [r.name for r, _ in github_reel.pick(repos, script)] == ["o/r0", "o/r2", "o/r3", "o/r4", "o/r5"]
    with pytest.raises(RuntimeError):
        github_reel.pick(repos[:4], script)


def test_caption_hashtag_limit():
    assert github_reel.validate_caption("좋아요 " + " ".join(f"#t{i}" for i in range(5))) == []
    assert github_reel.validate_caption(" ".join(f"#t{i}" for i in range(12)))


def test_bgm_is_deterministic_and_varies_by_seed():
    a, b, c = bgm.compose(6, 1), bgm.compose(6, 1), bgm.compose(6, 2)
    assert len(a) == 6 * bgm.SR and np.max(np.abs(a)) <= 0.81
    assert np.array_equal(a, b) and not np.array_equal(a, c)
