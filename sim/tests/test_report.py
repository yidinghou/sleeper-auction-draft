from draftsim.config import DraftConfig
from draftsim.engine import DraftResult, ManagerState, Pick
from draftsim.report import render_html
from draftsim.valuation import Player


def _result():
    config = DraftConfig(teams=2, budget=50, roster_slots=("QB", "WR", "BN"))
    allen = Player(id="allen", name="Josh Allen", pos="QB", team="BUF", points=400.0, bye=7)
    chase = Player(id="chase", name="Ja'Marr Chase", pos="WR", team="CIN", points=350.0, bye=10)
    lamb = Player(id="lamb", name="CeeDee Lamb", pos="WR", team="DAL", points=300.0, bye=7)
    managers = {
        "M00": ManagerState("M00", budget=8, roster=[allen, chase]),
        "M01": ManagerState("M01", budget=48, roster=[lamb]),
    }
    picks = (
        Pick(0, "M00", allen, "M00", 40),
        Pick(1, "M00", chase, "M00", 2),
        Pick(2, "M01", lamb, "M01", 2),
    )
    return DraftResult(picks=picks, managers=managers, config=config)


def test_render_html_is_a_nonempty_full_page():
    html = render_html(_result(), seed=1)
    assert html.startswith("<!doctype html>")
    assert len(html) > 500


def test_render_html_shows_every_manager_player_and_price():
    html = render_html(_result(), seed=1)
    for manager_id in ("M00", "M01"):
        assert manager_id in html
    assert "Josh Allen" in html
    assert "Ja&#x27;Marr Chase" in html  # name is HTML-escaped
    assert "$40" in html  # a price paid


def test_render_html_is_deterministic():
    a = render_html(_result(), seed=1)
    b = render_html(_result(), seed=1)
    assert a == b
