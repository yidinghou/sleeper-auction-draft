"""The player pool and the draft log — the two panes in the bottom band."""

import json
import re
from pathlib import Path

import pytest

from draftsim.live_render import (
    _POOL_SHOWN,
    _esc,
    _log_price,
    render_log,
    render_page,
    render_pool,
)
from draftsim.live_state import SeatPick, reconstruct
from draftsim.sleeper import config_from_draft
from draftsim.valuation import Player, load_players, market_value

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
def midway(mock_config, pool, catalog):
    picks = sorted(_load("picks-mock"), key=lambda p: p["pick_no"])[:60]
    return reconstruct(picks, mock_config, pool, catalog=catalog)


def _pick(price: int, proj, points: float = 100.0, pick_no: int = 1) -> SeatPick:
    player = Player(
        id="p", name="Sam Test", pos="WR", team="NE", points=points, proj_dollar=proj
    )
    return SeatPick(pick_no=pick_no, slot=1, player=player, price=price)


# -- the pool -----------------------------------------------------------------


def test_the_pool_is_ordered_by_the_dollar_it_quotes(midway):
    html = render_pool(midway)
    shown = [int(m) for m in re.findall(r'class="ppr">\$(\d+)<', html)]
    assert shown == sorted(shown, reverse=True)


def test_the_pool_never_shows_a_player_who_is_gone(midway):
    # The one thing a pool pane must not do. Checked by identity rather than by
    # name: two different players really do share a short name in this pool --
    # Bijan Robinson is drafted while a "B. Robinson" of Atlanta is not -- so a
    # name search would fail on a board that is perfectly correct.
    taken = {pick.player.id for pick in midway.picks}
    assert not [p for p in midway.available if p.id in taken]

    expected = sorted(
        midway.available, key=lambda p: (-market_value(p), -p.points, p.name)
    )[:_POOL_SHOWN]
    # Escaped, because a full name can carry an apostrophe -- "D'Andre Swift"
    # ships as "D&#x27;Andre Swift", and the short name never had to.
    shown = re.findall(r"<b>(.*?)</b>", render_pool(midway))
    assert shown == [_esc(p.name) for p in expected]


def test_the_pool_is_a_shortlist_not_the_sheet(midway):
    assert len(midway.available) > _POOL_SHOWN
    assert render_pool(midway).count('class="prow"') == _POOL_SHOWN


def test_an_unpriced_player_still_gets_a_row():
    # Most of the sheet carries no $PROJ. Dropping them would hide bodies that
    # can still be nominated; they sort last and show a dash.
    priced = Player(id="a", name="A Rich", pos="WR", team="NE", points=200.0, proj_dollar=30)
    free = Player(id="b", name="B Free", pos="WR", team="NE", points=190.0)

    class _S:
        available = [free, priced]

    html = render_pool(_S())
    assert html.index(priced.name) < html.index(free.name)
    assert "—" in html


def test_an_empty_board_says_so():
    class _S:
        available = []

    assert "the board is empty" in render_pool(_S())


# -- the log ------------------------------------------------------------------


def test_the_log_runs_newest_first(midway):
    html = render_log(midway)
    numbers = [int(m) for m in re.findall(r'class="lno">#(\d+)<', html)]
    assert numbers == sorted(numbers, reverse=True)
    assert numbers[0] == max(p.pick_no for p in midway.picks)


def test_the_log_names_the_seat_that_bought(midway):
    newest = max(midway.picks, key=lambda p: p.pick_no)
    top = render_log(midway).split('class="lrow"')[1]
    assert f"S{newest.slot}" in top
    assert newest.player.name in top
    assert f"${newest.price}" in top


def test_the_log_says_what_position_went(midway):
    # The same pill the pool uses: what the room has been paying for running
    # backs is a question you answer by colour, not by reading fifteen names.
    newest = max(midway.picks, key=lambda p: p.pick_no)
    top = render_log(midway).split('class="lrow"')[1]
    assert f'class="badge"' in top
    assert f">{newest.player.pos}<" in top


@pytest.mark.parametrize(
    "price,proj,direction,shown",
    [
        (30, 20, "over", "+50%"),
        (10, 20, "under", "-50%"),
        (20, 20, "even", "+0%"),
    ],
)
def test_the_delta_says_which_way_the_room_paid(price, proj, direction, shown):
    cell = _log_price(_pick(price, proj))
    assert direction in cell and shown in cell


def test_a_pick_with_no_projection_shows_the_price_alone():
    # Comparing against a number that isn't there would be an invented delta.
    cell = _log_price(_pick(14, None))
    assert "$14" in cell and "muted" in cell
    assert "%" not in cell


def test_a_zero_projection_is_treated_as_no_projection():
    # Guards the division: $PROJ of 0 would be a divide-by-zero, not a 100% over.
    assert "%" not in _log_price(_pick(9, 0))


def test_an_empty_log_says_so():
    class _S:
        picks = []
        seats = {}  # who bought what, asked of a log with nothing in it

    assert "no picks yet" in render_log(_S())


# -- the page's own furniture -------------------------------------------------


def test_the_page_arrives_whole_with_its_stylesheet_and_client_inlined():
    # The shell, the stylesheet and the client are three files in `static/` now,
    # stitched together at request time. That buys real CSS and real JS at the
    # price of a new way to fail -- an asset that did not travel with the module
    # -- and this is the only thing that would catch it. One request still: the
    # page arrives with both inlined, nothing fetched after it but /api/state.
    page = render_page("123")
    assert "  .pcard { border" in page          # from board.css
    assert "function applyRows()" in page       # from board.js
    assert '<b id="draft">123</b>' in page      # the shell's one interpolation
    for token in ("/*__CSS__*/", "/*__JS__*/", "__DRAFT_ID__"):
        assert token not in page, token


def test_the_draft_id_is_escaped_into_the_shell():
    # It comes off the command line and lands in the band header, which names
    # the draft being read so a stale id cannot hide.
    assert '<b id="draft">&lt;b&gt;</b>' in render_page("<b>")


def test_the_live_band_header_carries_checkpoint_nav_controls():
    # Chrome, like the rest of the header -- it has to survive the fragment
    # swap, so it lives in the shell and not in a snapshot. And it belongs
    # with the rest of "what moment is this" (the pulse/subtitle line), not
    # in the roster grid's own toolbar.
    page = render_page("123")
    live_head = page.split("<h2>Live draft board</h2>")[1].split(
        '<div class="bandbody live2">'
    )[0]
    for control in ("navprev", "navslider", "navnext", "navlive", "navpos"):
        assert f'id="{control}"' in live_head
    menubar = page.split('<div class="menubar">')[1].split('<div id="rosters">')[0]
    for control in ("navprev", "navslider", "navnext", "navlive", "navpos"):
        assert f'id="{control}"' not in menubar
    assert "function applyNav(" in page   # from board.js
    assert ".navseg" in page              # from board.css


# -- the bands ----------------------------------------------------------------


def test_the_column_is_three_bands_and_the_chrome_is_in_the_shell():
    # Chrome in the shell, not the fragments: the fragments are replaced every
    # 2s and a control destroyed that often holds neither focus nor state.
    page = render_page("123")
    for band in ("live", "runs", "panes", "pool", "log"):
        assert f'data-band="{band}"' in page
    # No buttons: the header itself folds on double-click. Which means the only
    # thing telling you so is the hover state, so that rule has to exist.
    assert "caret" not in page.split("<body>")[1]
    assert ".band > .bandhd:hover" in page
    assert ".band > .bandhd { cursor: pointer" in page


def test_the_live_band_is_two_panels_across():
    # Both halves were wider than they needed to be and the band was taller than
    # it needed to be. Side by side it costs the height of the taller one, and
    # the bands under it grow into the difference.
    page = render_page("123")
    assert 'class="bandbody live2"' in page
    assert ".band-live > .bandbody.live2 { flex-direction: row" in page
    # The bid is the left two fifths, the chart the right three.
    assert page.index('id="block"') < page.index('id="ledger"')
    assert ".bidpane { flex: 2 1 0; }" in page
    assert ".moneypane { flex: 3 1 0; }" in page
    # Still the two swap targets, and still empty in the shell: the panel titles
    # are chrome and stay put, the fragments inside them are replaced.
    assert '<div id="block"></div>' in page
    assert '<div id="ledger"></div>' in page


def test_the_live_band_is_a_fifth_of_the_column_and_stays_there():
    # A share, not "whatever the content wants". Content-sized it grew to a
    # third of the screen answering a question you glance at -- so the band is
    # pinned and the one thing in it that can scale, the chart, takes what is
    # left. Which is why the bars are drawn in percentages: the server cannot
    # know the height and no longer needs to.
    page = render_page("123")
    assert ".band-live { flex: 0 0 20%" in page
    assert ".plot { position: relative; flex: 1" in page
    # The spend line moved out to the header to buy that height back.
    assert 'id="spend"' in page
    assert "s.spend_html" in page


def test_collapse_is_remembered_and_reapplied_after_every_swap():
    page = render_page("123")
    assert "draftsim.collapsed" in page
    assert "applyCollapsed();" in page
    # Applied before the first fetch too, so a folded band never flashes open
    # and a card never shows the wrong pane for a beat. Asserted as "before the
    # first tick" rather than as an exact run of three lines: pinning the
    # sequence meant every new apply pass added between them broke a test about
    # something else, which is how this one came to be checking a line that had
    # not existed for two commits.
    startup = page.rsplit("\ntick();", 1)[0]
    for call in ("applyCollapsed();", "applyPViews();", "applyFilters();"):
        assert f"\n{call}\n" in startup


def test_the_panes_are_swapped_with_everything_else():
    page = render_page("123")
    assert 'swapKeepingScroll("pool", s.pool_html)' in page
    assert 'swapKeepingScroll("log", s.log_html)' in page


def test_a_refresh_does_not_yank_you_back_to_the_top():
    # Both panes are deep enough to scroll now, and both are replaced wholesale
    # every 2s. Without this you cannot read past the first screen.
    page = render_page("123")
    body = page.split("function swapKeepingScroll")[1].split("}")[0]
    assert "el.scrollHeight - before" in body  # anchored to the growth, not the offset
    assert "wasAtTop" in body                  # a pane at rest stays pinned to newest
