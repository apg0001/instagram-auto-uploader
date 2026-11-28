from uploader.cards.render import eok, pct, sparkline, tone


def test_card_filters():
    assert pct(1.234) == "▲1.23%" and pct(-0.5) == "▼0.50%" and pct(0) == "0.00%"
    assert tone(2) == "up" and tone(-2) == "down" and tone(0) == "flat"
    assert eok(11148853) == "1,114조 8,853억" and eok(2492) == "2,492억"
    s = sparkline([1, 3, 2], w=100, h=50)
    assert len(s["points"].split()) == 3


def test_backticks_become_code():
    import pytest
    pw = pytest.importorskip("playwright.sync_api")
    from uploader.cards.render import CODE_JS

    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except pw.Error:
            pytest.skip("브라우저 없음 (CI)")
        pg = b.new_page()
        pg.set_content("<p>설치: `ollama launch openclaw` 한 줄, 그리고 `hermes`</p>")
        assert pg.evaluate(CODE_JS) == 1
        assert pg.eval_on_selector_all("code", "cs => cs.map(c => c.textContent)") == ["ollama launch openclaw", "hermes"]
        assert "`" not in pg.inner_text("p")
        b.close()
