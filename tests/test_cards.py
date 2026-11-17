from uploader.cards.render import eok, pct, sparkline, tone


def test_card_filters():
    assert pct(1.234) == "▲1.23%" and pct(-0.5) == "▼0.50%" and pct(0) == "0.00%"
    assert tone(2) == "up" and tone(-2) == "down" and tone(0) == "flat"
    assert eok(11148853) == "1,114조 8,853억" and eok(2492) == "2,492억"
    s = sparkline([1, 3, 2], w=100, h=50)
    assert len(s["points"].split()) == 3
