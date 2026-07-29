from draftsim.auction import Bid
from draftsim.config import DraftConfig
from draftsim.engine import DraftResult, ManagerState, Pick
from draftsim.report import render_html
from draftsim.valuation import Player


def _result():
    config = DraftConfig(teams=2, budget=50, roster_slots=("QB", "WR", "BN"))
    allen = Player(
        id="allen", name="Josh Allen", pos="QB", team="BUF", points=400.0,
        proj_dollar=32, bye=7,
    )
    chase = Player(
        id="chase", name="Ja'Marr Chase", pos="WR", team="CIN", points=350.0,
        proj_dollar=40, bye=10,
    )
    lamb = Player(
        id="lamb", name="CeeDee Lamb", pos="WR", team="DAL", points=300.0,
        proj_dollar=2, bye=7,
    )
    managers = {
        "M00": ManagerState("M00", budget=8, roster=[allen, chase]),
        "M01": ManagerState("M01", budget=48, roster=[lamb]),
    }
    picks = (
        Pick(0, "M00", allen, "M00", 40, (Bid("M00", 40, 0), Bid("M01", 31, 1))),
        Pick(1, "M00", chase, "M00", 2, (Bid("M00", 2, 0),)),
        Pick(2, "M01", lamb, "M01", 2),  # no bid log recorded
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


def test_roster_row_puts_team_and_bye_in_the_name_tooltip():
    html = render_html(_result(), seed=1)
    # Team + bye are a hover tooltip on the name, not a visible column.
    assert 'title="BUF · BYE 7"' in html


def test_render_html_is_deterministic():
    a = render_html(_result(), seed=1)
    b = render_html(_result(), seed=1)
    assert a == b


def test_position_table_covers_every_drafted_position():
    html = render_html(_result(), seed=1)
    assert 'id="positions"' in html
    for pos in ("QB", "WR"):
        assert f"<th>{pos}</th>" in html
    # Starter points per manager: M00 starts both its players, M01 only Lamb.
    assert ">750<" in html  # 400 + 350
    assert ">300<" in html


def test_timeline_shows_nominator_and_winner():
    html = render_html(_result(), seed=1)
    assert 'id="timeline"' in html
    assert "#0" in html
    assert "M00 nom" in html
    assert "M00</span>" in html


def test_timeline_compares_price_against_sleeper_proj():
    html = render_html(_result(), seed=1)
    # Allen went for $40 against a $32 projection: 25% over.
    assert '<span class="proj">$32</span>' in html
    assert '<span class="delta over">+25%</span>' in html
    # Chase went for $2 against $40: 95% under.
    assert '<span class="delta under">-95%</span>' in html


def test_price_spread_summarises_the_gap_against_proj():
    html = render_html(_result(), seed=1)
    assert "priced picks" in html
    assert "median" in html


def test_players_without_a_proj_have_nothing_to_compare():
    config = DraftConfig(teams=1, budget=50, roster_slots=("QB",))
    nobody = Player(id="x", name="No Proj", pos="QB", team="BUF", points=100.0)
    result = DraftResult(
        picks=(Pick(0, "M00", nobody, "M00", 7),),
        managers={"M00": ManagerState("M00", budget=43, roster=[nobody])},
        config=config,
    )
    html = render_html(result, seed=1)
    assert '<span class="proj muted">—</span>' in html
    assert "No drafted player carried a $PROJ" in html


def test_timeline_renders_a_pick_with_no_bid_log():
    html = render_html(_result(), seed=1)
    assert "no bid log recorded" in html


def test_timeline_lists_seats_that_sat_out():
    html = render_html(_result(), seed=1)
    # Only M00 bid on Ja'Marr Chase, so M01 sat that round out.
    assert "sat out (1): M01" in html


def test_dead_weight_and_unspent_budget_are_flagged():
    config = DraftConfig(teams=1, budget=50, roster_slots=("QB", "BN"))
    ghost = Player(id="ghost", name="Blake Bell", pos="TE", team="", points=0.0)
    allen = Player(id="allen", name="Josh Allen", pos="QB", team="BUF", points=400.0)
    result = DraftResult(
        picks=(Pick(0, "M00", allen, "M00", 1), Pick(1, "M00", ghost, "M00", 1)),
        managers={"M00": ManagerState("M00", budget=48, roster=[allen, ghost])},
        config=config,
    )
    html = render_html(result, seed=1)
    assert "1 dead" in html  # the teamless, 0-point player
    assert 'class="warn"' in html  # $48 left on the table
