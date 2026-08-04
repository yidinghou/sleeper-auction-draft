"""Rebuilding league standings from a pick feed, and rendering them."""

import json
import math
from pathlib import Path

import pytest

from draftsim.config import BENCH, DraftConfig
from draftsim.live_render import (
    _NEED_SKIP,
    _short_name,
    render_nomination,
    render_page,
    render_rosters,
)
from draftsim.live_state import (
    DRAFT_TARGETS,
    contenders,
    position_summary,
    reconstruct,
    seat_value_of,
    spend_by_position,
)
from draftsim.roster import starters
from draftsim.sleeper import Nomination, config_from_draft
from draftsim.valuation import Player, load_players

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str):
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture(scope="module")
def pool():
    return load_players()


@pytest.fixture(scope="module")
def catalog():
    return load_players(free_agents=True)


@pytest.fixture(scope="module")
def mock_config():
    return config_from_draft(_load("draft-mock"))


@pytest.fixture(scope="module")
def finished(mock_config, pool, catalog):
    return reconstruct(_load("picks-mock"), mock_config, pool, catalog=catalog)


@pytest.fixture(scope="module")
def midway(mock_config, pool, catalog):
    """The same mock rewound to pick 60 -- the state the board exists for."""
    picks = sorted(_load("picks-mock"), key=lambda p: p["pick_no"])[:60]
    return reconstruct(picks, mock_config, pool, catalog=catalog)


# -- a finished draft is the strongest end-state assertion --------------------


def test_every_seat_ends_full_and_solvent(finished, mock_config):
    assert len(finished.seats) == mock_config.teams
    for seat in finished.seats.values():
        assert seat.filled == mock_config.roster_size
        assert seat.open_slots == 0
        assert seat.budget_left >= 0
        assert seat.max_bid == 0  # no room left to bid into


def test_league_spend_never_exceeds_the_pooled_budget(finished, mock_config):
    spent = sum(seat.spent for seat in finished.seats.values())
    assert 0 < spent <= mock_config.budget * mock_config.teams
    assert spent == sum(pick.price for pick in finished.picks)


def test_every_pick_resolves_to_a_projection(finished):
    # The full sheet, not the draftable pool, is what picks resolve against --
    # seats really do spend $1 on unsigned free agents.
    assert finished.unknown_player_ids == []
    assert all(pick.player.name for pick in finished.picks)


def test_drafted_players_leave_the_available_pool(finished, pool):
    assert len(finished.available) == len(pool) - len(
        {pick.player.id for pick in finished.picks} & {p.id for p in pool}
    )
    drafted = {pick.player.id for pick in finished.picks}
    assert not any(p.id in drafted for p in finished.available)


# -- mid-draft is where the numbers have to be right -------------------------


def test_midway_seats_have_money_and_room(midway, mock_config):
    assert sum(s.filled for s in midway.seats.values()) == 60
    assert any(s.open_slots > 0 for s in midway.seats.values())
    for seat in midway.seats.values():
        assert seat.budget_left == mock_config.budget - seat.spent


def test_max_bid_reserves_a_dollar_for_every_other_open_slot(midway):
    for seat in midway.seats.values():
        assert seat.max_bid == max(0, seat.budget_left - (seat.open_slots - 1))
        assert seat.max_bid <= seat.budget_left


def test_needs_shrink_as_a_seat_fills(midway, finished):
    def outstanding(state):
        return sum(sum(s.needs.values()) for s in state.seats.values())

    assert outstanding(midway) > outstanding(finished) == 0


def test_spend_by_position_totals_the_feed(midway):
    by_pos = spend_by_position(midway)
    assert sum(by_pos.values()) == sum(p.price for p in midway.picks)


# -- valuation wiring --------------------------------------------------------


def test_a_seat_that_cannot_start_a_player_values_them_at_zero(midway):
    seat = next(iter(midway.seats.values()))
    # A kicker has no startable slot in this lineup, so no seat can ever gain
    # from one -- the check that "value" means lineup gain, not raw points.
    kicker = Player(id="k", name="Kicker", pos="K", team="KC", points=200.0)
    assert seat_value_of(midway, seat, kicker) == 0.0


def test_a_strong_player_is_worth_points_to_a_seat_with_room(midway):
    star = Player(id="star", name="Star", pos="WR", team="KC", points=400.0)
    seat = next(s for s in midway.seats.values() if s.needs.get("WR", 0) > 0)
    assert seat_value_of(midway, seat, star) > 0.0


def test_contenders_are_seats_that_can_both_pay_and_play(midway):
    star = Player(id="star", name="Star", pos="WR", team="KC", points=400.0)
    shortlist = contenders(midway, star)
    assert shortlist
    assert all(s.max_bid > 0 for s in shortlist)
    reaches = [s.max_bid for s in shortlist]
    assert reaches == sorted(reaches, reverse=True)


def test_nobody_contends_for_a_player_nobody_can_start(midway):
    kicker = Player(id="k", name="Kicker", pos="K", team="KC", points=200.0)
    assert contenders(midway, kicker) == []


def test_a_finished_league_has_no_contenders(finished):
    star = Player(id="star", name="Star", pos="WR", team="KC", points=400.0)
    assert contenders(finished, star) == []


# -- feed shapes that would otherwise fail silently --------------------------


def test_an_empty_feed_is_a_full_board_of_untouched_seats(mock_config, pool):
    state = reconstruct([], mock_config, pool)
    assert len(state.seats) == mock_config.teams
    for seat in state.seats.values():
        assert seat.spent == 0
        assert seat.budget_left == mock_config.budget
        assert seat.max_bid == mock_config.budget - (mock_config.roster_size - 1)


def test_a_pick_from_an_impossible_seat_is_an_error(pool):
    config = DraftConfig(teams=2, budget=50, roster_slots=("QB", "WR", "BN"))
    picks = [{"pick_no": 1, "draft_slot": 9, "player_id": "4984", "metadata": {}}]
    with pytest.raises(ValueError, match="outside 1..2"):
        reconstruct(picks, config, pool)


def test_an_unknown_player_still_costs_money_and_a_slot(mock_config, pool):
    picks = [
        {
            "pick_no": 1,
            "draft_slot": 1,
            "player_id": "999999",
            "metadata": {
                "first_name": "Ghost",
                "last_name": "Player",
                "position": "WR",
                "team": "KC",
                "amount": "23",
            },
        }
    ]
    state = reconstruct(picks, mock_config, pool)
    seat = state.seats[1]
    assert state.unknown_player_ids == ["999999"]
    assert seat.spent == 23
    assert seat.filled == 1
    assert seat.roster[0].name == "Ghost Player"
    assert seat.roster[0].points == 0.0


def test_a_pick_with_no_price_is_free_not_a_crash(mock_config, pool):
    picks = [
        {"pick_no": 1, "draft_slot": 1, "player_id": "4984", "metadata": {}},
    ]
    state = reconstruct(picks, mock_config, pool)
    assert state.seats[1].spent == 0
    assert state.seats[1].filled == 1


# -- the per-seat summary ----------------------------------------------------


def test_summary_targets_are_the_draft_plan_not_the_slot_count(midway):
    # The pane answers "should this seat buy another receiver?", which is a
    # draft target, not a legality check -- so it deliberately parts company
    # with seat.needs, which counts what makes a lineup legal.
    for seat in midway.seats.values():
        for line in position_summary(seat, midway.config):
            if line.pos in DRAFT_TARGETS:
                assert line.want == DRAFT_TARGETS[line.pos]


def test_a_position_with_no_target_falls_back_to_the_slot_count(midway):
    # DEF has no entry in the plan. It must still report a target rather than
    # silently reading as "wants none" beside the positions that have one.
    counts = midway.config.starter_counts()
    for seat in midway.seats.values():
        for line in position_summary(seat, midway.config):
            if line.pos not in DRAFT_TARGETS:
                assert line.want == counts.get(line.pos, 0)


def test_targets_stay_fractional_all_the_way_to_the_line(midway):
    # Rounding 2.5 up to 3 is what made a seat holding two and a flex read as
    # short; the half has to survive into the PositionLine.
    assert any(t % 1 for t in DRAFT_TARGETS.values())
    for seat in midway.seats.values():
        lines = {line.pos: line for line in position_summary(seat, midway.config)}
        for pos, target in DRAFT_TARGETS.items():
            if pos in lines:
                assert lines[pos].want == pytest.approx(target)


def test_summary_points_add_up_to_the_starting_lineup(midway):
    for seat in midway.seats.values():
        lineup = [p for p in starters(seat.roster, midway.config) if p is not None]
        total = sum(line.starter_points for line in position_summary(seat, midway.config))
        assert total == pytest.approx(sum(p.points for p in lineup))


def test_summary_counts_bench_depth_as_have_but_never_as_need(finished):
    seat = finished.seats[1]
    lines = {line.pos: line for line in position_summary(seat, finished.config)}
    for pos, line in lines.items():
        assert line.have == sum(1 for p in seat.roster if p.pos == pos)
    # A finished roster is deeper than its target at some position, and depth
    # must never read as a hole.
    assert any(line.have > line.want for line in lines.values())
    assert all(line.need == 0 for line in lines.values() if line.have >= line.want)
    # A full roster can still miss the plan -- sixteen bodies bought without a
    # third quarterback is a legal lineup and a missed target, and the pane is
    # the thing that says so. Legality is seat.needs' job, and it is zero here.
    assert sum(seat.needs.values()) == 0


def test_a_position_nobody_starts_or_owns_is_left_out(midway):
    # The default lineup has no kicker slot, and the plan names no kicker
    # target, so K is noise until someone drafts one -- at which point the body
    # has to show up somewhere.
    for seat in midway.seats.values():
        positions = {line.pos for line in position_summary(seat, midway.config)}
        has_kicker = any(p.pos == "K" for p in seat.roster)
        assert ("K" in positions) == has_kicker


def test_an_empty_roster_summarizes_as_all_holes(mock_config, pool):
    state = reconstruct([], mock_config, pool)
    lines = position_summary(state.seats[1], mock_config)
    assert [(l.pos, l.have, l.want) for l in lines] == [
        ("QB", 0, 3.0), ("RB", 0, 2.5), ("WR", 0, 3.5), ("TE", 0, 1.0),
        ("DEF", 0, 1.0),
    ]
    assert all(line.starter_points == 0.0 for line in lines)
    assert all(line.need == line.want for line in lines)


# -- rendering ---------------------------------------------------------------


def test_nomination_strip_names_the_player_and_the_bid(midway):
    star = Player(
        id="star", name="Star Guy", pos="WR", team="KC", points=400.0,
        proj_dollar=44,
    )
    nom = Nomination(
        player_id="star", nominating_slot=3, high_bid=17, offering_slot=8
    )
    html = render_nomination(midway, nom, star)
    assert "Star Guy" in html
    assert "$17" in html
    assert "$44" in html  # the crowd's price, to bid against
    assert "seat 8" in html


def test_nomination_strip_is_idle_between_lots(midway):
    nom = Nomination(
        player_id=None, nominating_slot=None, high_bid=None, offering_slot=None
    )
    html = render_nomination(midway, nom, None)
    assert "Nothing nominated" in html


def test_nomination_strip_survives_a_player_off_the_sheet(midway):
    nom = Nomination(
        player_id="99999", nominating_slot=1, high_bid=3, offering_slot=1
    )
    html = render_nomination(midway, nom, None)
    assert "not in projections" in html
    assert "$3" in html


def test_page_shell_is_a_full_html_document():
    page = render_page("123")
    assert page.startswith("<!doctype html>")
    assert "/api/state" in page
    assert "123" in page


# -- roster cards ------------------------------------------------------------


def test_every_seat_gets_a_roster_card(midway):
    html = render_rosters(midway)
    for slot in midway.seats:
        assert f'data-roster="{slot}"' in html


def test_cards_are_in_seat_order_not_reach_order(midway):
    # The table sorts by reach; these are read to look a seat up, and a grid
    # that reshuffles on every bid is useless for that.
    html = render_rosters(midway)
    order = [int(c.split('"')[0]) for c in html.split('data-roster="')[1:]]
    assert order == sorted(midway.seats)


def test_a_card_shows_each_player_with_price_points_and_bye(midway):
    seat = next(s for s in midway.seats.values() if s.roster)
    card = _card(midway, seat.slot)
    for pick in seat.picks:
        assert pick.player.name.replace("'", "&#x27;") in card
        assert f"${pick.price}" in card
        if pick.player.bye:
            assert f">{pick.player.bye}<" in card


def test_a_card_leads_with_money_and_reach(midway):
    # The header answers "can this seat outbid me" and nothing else: what is
    # left, and the most of it that can go on one player.
    seat = next(s for s in midway.seats.values() if s.roster)
    header = _card(midway, seat.slot).split("</header>")[0]
    assert f'class="big">${seat.budget_left}<' in header
    assert f"<b>${seat.max_bid}</b>" in header


def test_the_header_carries_no_points_or_slot_counts(midway):
    # Both moved into the NEED pane, where there is room to label them. They
    # were read occasionally and competed with the two numbers read constantly.
    for slot in midway.seats:
        header = _card(midway, slot).split("</header>")[0]
        assert "pts" not in header
        assert "open" not in header


def test_the_budget_bar_splits_spendable_from_reserved(midway):
    # A dollar per unfilled slot is in the account but already owed; a seat with
    # $80 and eight holes is not the threat its balance says it is.
    for seat in midway.seats.values():
        bar = _card(midway, seat.slot).split('class="budget"')[1].split("</div>")[0]
        widths = [float(w.split("%")[0]) for w in bar.split("width:")[1:]]
        assert len(widths) == 2
        assert sum(widths) <= 100.0 + 1e-6
        held = max(0, seat.open_slots - 1)
        assert widths[1] == pytest.approx(100 * held / midway.config.budget, abs=0.1)


def test_a_seat_out_of_the_market_is_flagged(mock_config, pool):
    # A max bid that cannot win anyone is a seat you have stopped bidding
    # against, and the card says so rather than leaving you to notice.
    state = reconstruct([], mock_config, pool)
    rich = render_rosters(state)
    assert 'class="card broke"' not in rich
    for seat in state.seats.values():
        seat.max_bid = 3
    assert render_rosters(state).count('class="card broke"') == mock_config.teams


def test_unfilled_starter_slots_are_shown(midway):
    seat = next(s for s in midway.seats.values() if s.open_slots > 6)
    card = _card(midway, seat.slot)
    # A hole at a starting slot is the point of the card, so it stays visible.
    assert 'class="ln open"' in card


def test_empty_rosters_still_render_their_shape(mock_config, pool):
    state = reconstruct([], mock_config, pool)
    html = render_rosters(state)
    assert html.count("data-roster=") == mock_config.teams
    # Every seat shows the full lineup shape, all of it empty.
    assert html.count('class="ln open"') == mock_config.teams * len(
        [s for s in mock_config.roster_slots if s != "BN"]
    )


def test_a_seat_with_no_bench_says_so_rather_than_showing_a_blank_pane(
    mock_config, pool
):
    state = reconstruct([], mock_config, pool)
    assert render_rosters(state).count('class="empty"') == mock_config.teams


def test_rows_carry_position_colour_and_price(midway):
    from draftsim.theme import POS_COLOR_LIGHT

    seat = next(s for s in midway.seats.values() if s.roster)
    card = _card(midway, seat.slot)
    # Position reads as the row's own tint, not a badge -- so no badge markup,
    # but every drafted player's colour must be present to mix that tint from.
    assert 'class="badge"' not in card
    for pick in seat.picks:
        assert f"--pos:{POS_COLOR_LIGHT[pick.player.pos]}" in card
        assert f"${pick.price}" in card


def test_the_row_is_tinted_and_the_slot_label_is_not(midway):
    # The colour is the field the name sits on. A coloured chip put the loudest
    # thing in the row right beside the thing you actually read.
    page = render_page("123")
    assert "background: color-mix(in srgb, var(--pos, #7c90a0) 26%, #fff)" in page
    label = page.split(".ms {")[1].split("}")[0]
    assert "var(--pos" not in label  # same grey on every row


def test_an_empty_slot_reads_as_absence_not_as_a_position(midway):
    # A hole is flat grey: tinting it would say "receiver" about a seat that
    # has no receiver.
    page = render_page("123")
    assert ".ln.open { background: #f4f4f4; }" in page


def test_one_player_per_line(midway):
    # The old card paired two players to a row; a row is one player now, so the
    # lineup pane has exactly one line per starting slot.
    seat = next(s for s in midway.seats.values() if s.roster)
    lineup = _pane(midway, seat.slot, "lineup")
    starter_slots = len(midway.config.starter_slots)
    assert lineup.count('<div class="ln') == starter_slots + 1  # + column labels


def test_bench_is_its_own_pane_labelled_by_real_position(finished):
    # In a pane of its own nothing else says what these players are, so the chip
    # is the player's position rather than a fungible BN.
    seat = finished.seats[1]
    bench = _pane(finished, 1, "bench")
    starting = {id(p) for p in starters(seat.roster, finished.config) if p}
    depth = [p for p in seat.roster if id(p) not in starting]
    assert bench.count('<div class="ln bench"') == len(depth)
    assert f">{BENCH}</i>" not in bench
    for player in depth:
        assert f'class="ms">{player.pos}<' in bench


def test_bench_rows_keep_their_position_tint_but_sit_back():
    page = render_page("123")
    # Depth is worth seeing -- it keeps the position tint, since in a pane of
    # its own nothing else says what these bodies are -- but a step back from
    # the lineup. The old "hidden until maximized" behaviour must not come back.
    assert ".ln.bench { opacity: 0.75; }" in page
    assert ".ln.bench { display: none" not in page.replace("{{", "{")


def test_board_controls_sit_in_a_menu_bar_over_the_grid():
    page = render_page("123")
    # The controls belong to the board, so they live above it rather than across
    # the aisle in the state column -- and they stay reachable when the board
    # takes the whole viewport.
    bar = page.index('class="menubar"')
    assert page.index('class="board"') < bar < page.index('id="rosters"')
    assert bar < page.index('id="max"') < page.index('id="rosters"')
    assert bar < page.index('id="reorder"') < page.index('id="rosters"')
    # The grid takes what the bar leaves rather than a flat 100% of the column,
    # which would have pushed the bottom row of cards off screen.
    assert "#rosters { flex: 1; min-height: 0; }" in page


def test_the_grid_is_four_across_three_down_in_both_sizes():
    # Twelve seats either way; a row per starter wants height, so the card is
    # wider than it is tall. Maximizing keeps the shape -- a card must be where
    # it was, only bigger -- and all twelve still fit one screen.
    page = render_page("123")
    grid = page.split(".grid {")[1].split("}")[0]
    maxed = page.split("body.maxed .grid {")[1].split("}")[0]
    assert "repeat(4, minmax(0, 1fr))" in grid
    assert "repeat(4, minmax(0, 1fr))" in maxed
    assert "height: 100%" in maxed  # rows share the viewport, nothing scrolls off


def test_lineup_rows_share_the_card_rather_than_a_fixed_height():
    # A fixed row height had to be tuned to one viewport: tall enough to fill a
    # big screen, it scrolled a short one. The rows flex instead, so the same
    # card fills a 1000px window and still fits an 820px one.
    page = render_page("123")
    assert ".pane.lineup .ln { flex: 1 1 auto; }" in page


def test_the_live_board_is_light_and_the_report_stays_dark():
    # The board sits open beside Sleeper's dark app for three hours; looking
    # unlike it is the point. The report is a different surface and keeps the
    # dark palette, so the two must not silently re-converge on one theme.
    from draftsim import theme

    page = render_page("123")
    assert theme.BASE_CSS_LIGHT in page
    assert theme.BASE_CSS not in page
    assert f"background: {theme.L_PAGE}" in page
    assert theme.BG not in page  # no stray dark-palette page background


def test_the_current_pane_stays_legible_when_maximized():
    # The pressed segment is dark-on-light; a blanket recolour in the overlay
    # would paint its label the same colour as the pill under it.
    page = render_page("123")
    seg = page.split('.seg button[aria-pressed="true"] {')[1].split("}")[0]
    assert "#fff" in seg and "#1a1a1a" in seg  # dark fill, light label


def test_page_splits_state_left_from_rosters_right():
    page = render_page("123")
    assert 'id="max"' in page
    assert "body.maxed" in page
    # The nomination strip and controls live in the left column; the roster
    # grid gets its own column so it can take the full viewport height.
    assert 'class="side"' in page
    assert 'class="board"' in page
    assert page.index('class="side"') < page.index('class="board"')


# -- the three panes ---------------------------------------------------------


def _card(state, slot):
    card = render_rosters(state).split('data-roster="%d"' % slot)[1]
    return card.split("</section>")[0]


def _pane(state, slot, pane):
    """One pane's markup. The pane name appears twice in a card -- on the button
    that picks it and on the pane itself -- so match the pane, not the first hit.
    """
    chunks = _card(state, slot).split('<div class="pane')[1:]
    return next(c for c in chunks if f'data-pane="{pane}"' in c)


def test_every_card_carries_all_three_panes_and_the_control_between_them(midway):
    html = render_rosters(midway)
    teams = len(midway.seats)
    for pane in ("lineup", "bench", "need"):
        # Once as the button that picks it, once as the pane itself.
        assert html.count(f'data-pane="{pane}"') == teams * 2
    assert html.count('class="seg"') == teams


def test_the_panes_ship_together_so_switching_needs_no_fetch(midway):
    # Three panes in one card is what makes switching free: no fetch, so two
    # panes can never show different moments of the draft.
    seat = next(s for s in midway.seats.values() if s.roster)
    card = _card(midway, seat.slot)
    assert 'class="pane lineup"' in card
    assert 'class="pane need"' in card
    for pick in seat.picks:
        assert pick.player.name.replace("'", "&#x27;") in card


def _needed(state, seat):
    """The rows a seat's NEED pane actually shows."""
    return [
        line
        for line in position_summary(seat, state.config)
        if line.pos not in _NEED_SKIP
    ]


def test_need_shows_each_positions_target_and_points(midway):
    seat = next(s for s in midway.seats.values() if s.roster)
    need = _pane(midway, seat.slot, "need")
    for line in _needed(midway, seat):
        want = f"{line.want:g}"  # 2.5 stays 2.5; 3.0 prints as 3
        assert f">{line.have}<i>/{want}</i>" in need
        if line.starter_points:
            assert f">{line.starter_points:.0f}<" in need


def test_need_flags_only_the_positions_still_short(midway):
    for seat in midway.seats.values():
        need = _pane(midway, seat.slot, "need")
        short = [line for line in _needed(midway, seat) if line.need]
        assert need.count('class="nrow short"') == len(short)


def test_need_leaves_out_the_rows_that_are_never_a_decision(midway):
    # One defense and one kicker, bought once and never decided at the podium:
    # rows that cost space without changing a bid. The lineup pane still has them.
    for seat in midway.seats.values():
        need = _pane(midway, seat.slot, "need")
        assert ">DEF</i>" not in need
        assert ">K</i>" not in need
        assert need.count('class="nrow') == len(_needed(midway, seat))


def test_the_lineup_pane_still_shows_what_need_drops(finished):
    # Dropping them from NEED must not lose them: a full roster's defense is
    # still there in the pane that lists every slot.
    assert ">DEF</i>" in _pane(finished, 1, "lineup")


def test_pips_draw_the_target_at_its_true_length(midway):
    # One pip per whole starter wanted, plus a half-width pip for a fractional
    # target -- which is the thing a percentage bar could not say.
    for seat in midway.seats.values():
        need = _pane(midway, seat.slot, "need")
        for line in _needed(midway, seat):
            row = need.split(f'>{line.pos}</i>')[1].split("</div>")[0]
            whole = int(line.want)
            assert row.count('class="pip"') + row.count('class="pip on"') == whole
            assert row.count("pip half") == (1 if line.want > whole else 0)


def test_pips_fill_to_what_a_seat_owns_and_no_further(midway):
    for seat in midway.seats.values():
        need = _pane(midway, seat.slot, "need")
        for line in _needed(midway, seat):
            row = need.split(f'>{line.pos}</i>')[1].split("</div>")[0]
            filled = row.count('class="pip on"') + row.count('class="pip half on"')
            assert filled == min(line.have, math.ceil(line.want))


def test_surplus_shows_as_extra_pips_outside_the_run(finished):
    # A fourth running back is depth, not a fault. The old fill bar capped at
    # 100% and drew it as "done"; here it sits past the target, outlined.
    seat = finished.seats[1]
    need = _pane(finished, 1, "need")
    for line in _needed(finished, seat):
        row = need.split(f'>{line.pos}</i>')[1].split("</div>")[0]
        assert row.count("pip extra") == max(0, line.have - math.ceil(line.want))
    assert "pip extra" in need  # a finished roster is deep somewhere


def test_the_need_pane_ends_with_the_spending_pace(midway):
    # $37 across three slots and $37 across twelve are different seats, and the
    # balance alone does not say which one you are looking at.
    for seat in midway.seats.values():
        pace = _pane(midway, seat.slot, "need").split('class="pace"')[1]
        assert f"<b>{seat.open_slots}</b> slots left" in pace
        if seat.open_slots:
            rate = round(seat.budget_left / seat.open_slots, 1)
            assert f"<b>${rate:g}</b> / slot" in pace


def test_a_full_seat_reports_no_pace_rather_than_dividing_by_zero(finished):
    pace = _pane(finished, 1, "need").split('class="pace"')[1]
    assert "<b>0</b> slots left" in pace
    assert "$0" in pace


def test_pane_state_is_client_side_and_survives_a_refresh():
    page = render_page("123")
    # Lineup is what the board loads on; a card with no stored choice shows it.
    assert ".card .pane.lineup { display: flex;" in page.replace("{{", "{")
    assert '.card.view-need .pane.need' in page
    # The cards are replaced every tick, so the choice must be re-applied after
    # each swap and the handler must be delegated, not per-button.
    assert "applyViews()" in page
    assert 'getElementById("rosters").addEventListener("click"' in page
    assert "draftsim.views" in page
    # The pressed segment is re-marked too, since the buttons are new markup.
    assert 'b.setAttribute("aria-pressed"' in page


# -- dragging cards into a different order -----------------------------------


def test_cards_are_draggable(midway):
    html = render_rosters(midway)
    assert html.count('draggable="true"') == len(midway.seats)


def test_dragging_does_not_change_what_the_server_sends(midway):
    # Order is presentational -- CSS `order` over markup that stays in seat
    # order, so the server has no idea the board was rearranged and seat order
    # is always recoverable.
    page = render_page("123")
    assert "card.style.order" in page
    assert "draftsim.order" in page
    order = [int(c.split('"')[0]) for c in render_rosters(midway).split('data-roster="')[1:]]
    assert order == sorted(midway.seats)


def test_a_drop_inserts_and_shifts_the_rest_down():
    page = render_page("123")
    # Remove then re-insert at the target's index: that is what makes the rest
    # shift down instead of the two cards swapping places.
    assert "order.splice(order.indexOf(dragging), 1)" in page
    assert "order.splice(order.indexOf(target), 0, dragging)" in page


def test_the_refresh_holds_still_while_a_card_is_in_hand():
    # The board polls every 2s; swapping #rosters mid-drag would delete the
    # element being dragged and the drop would land nowhere.
    page = render_page("123")
    assert "if (dragging === null) {" in page
    assert "rosters.innerHTML = s.rosters_html;" in page


def test_a_saved_order_is_reconciled_against_the_seats_on_the_board():
    page = render_page("123")
    # A stored order from a different league size must not hide a seat.
    assert "function normalizeOrder()" in page
    assert "if (!order.includes(slot)) order.push(slot);" in page


def test_there_is_a_way_back_to_seat_order():
    page = render_page("123")
    assert 'id="reorder"' in page
    assert 'localStorage.removeItem("draftsim.order")' in page


# -- compact names -----------------------------------------------------------


def _P(name, pos="WR"):
    return Player(id=name, name=name, pos=pos, team="KC", points=0.0)


def test_short_name_is_initial_plus_surname():
    assert _short_name(_P("Joe Burrow", "QB")) == "J. Burrow"
    assert _short_name(_P("Ja'Marr Chase")) == "J. Chase"


def test_short_name_keeps_compound_surnames_whole():
    # "A. Brown" would be the wrong player entirely.
    assert _short_name(_P("Amon-Ra St. Brown")) == "A. St. Brown"
    assert _short_name(_P("Jaxon Smith-Njigba")) == "J. Smith-Njigba"


def test_a_defense_gets_its_nickname_not_an_initial():
    # Defenses are named for their city, where an initial reads as nonsense --
    # "K. City Chiefs" helps nobody.
    assert _short_name(_P("Kansas City Chiefs", "DEF")) == "Chiefs"
    assert _short_name(_P("New York Giants", "DEF")) == "Giants"


def test_a_one_word_name_is_left_alone():
    assert _short_name(_P("Cher")) == "Cher"


def test_cards_carry_both_name_forms_so_maximizing_needs_no_refetch(midway):
    seat = next(s for s in midway.seats.values() if s.roster)
    card = render_rosters(midway).split('data-roster="%d"' % seat.slot)[1]
    card = card.split("</section>")[0]
    player = seat.picks[0].player
    assert f'class="mn">{_short_name(player)}<' in card
    assert f'class="mnf">{_esc_name(player.name)}<' in card


def _esc_name(name):
    import html as _h

    return _h.escape(name)


def test_cards_label_their_numeric_columns(midway):
    # The labels ship in every card but only show when maximized, where there
    # is room for the bye and points columns they name.
    card = render_rosters(midway).split('data-roster="1"')[1]
    assert ">BYE<" in card
    assert ">PTS<" in card
    assert 'class="ln colhead"' in card


def test_the_overlay_has_its_own_way_out():
    # Maximize sits in the left column, which the overlay covers -- without a
    # close control inside it, Esc is the only exit and nothing says so.
    page = render_page("123")
    assert 'id="close"' in page
    assert "body.maxed .closebtn" in page
    assert 'getElementById("close")' in page
    assert 'aria-label="Minimize"' in page


def test_the_page_hides_column_labels_until_maximized():
    page = render_page("123")
    assert "body.maxed .colhead" in page
    # Tabular figures are what make the numbers read as columns.
    assert "tabular-nums" in page
