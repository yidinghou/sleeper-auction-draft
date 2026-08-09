from draftsim.agents import build_field
from draftsim.auction import MIN_BID
from draftsim.config import DraftConfig
from draftsim.engine import invariant_violations, run_draft
from draftsim.roster import is_lineup_legal, starters
from draftsim.valuation import Player, load_players

# Load the real pool once; every test drafts from it.
PLAYERS = load_players()


def _run(seed: int, teams: int = 12):
    config = DraftConfig(teams=teams)
    agents = build_field(teams, seed=seed)
    return run_draft(agents, config, PLAYERS), config


# --- determinism -----------------------------------------------------------


def test_same_seed_is_byte_identical():
    a, _ = _run(seed=1)
    b, _ = _run(seed=1)
    assert a.picks == b.picks
    assert {k: (v.budget, tuple(p.id for p in v.roster)) for k, v in a.managers.items()} == {
        k: (v.budget, tuple(p.id for p in v.roster)) for k, v in b.managers.items()
    }


def test_different_seeds_diverge():
    a, _ = _run(seed=1)
    b, _ = _run(seed=2)
    # Overwhelmingly likely to differ; if they matched, the seed jitter is dead.
    assert a.picks != b.picks


# --- invariants ------------------------------------------------------------


def test_all_rosters_are_legal_and_full():
    result, config = _run(seed=7)
    for m in result.managers.values():
        assert len(m.roster) == config.roster_size
        assert is_lineup_legal(m.roster, config)


def test_no_negative_budgets_and_spend_within_budget():
    result, config = _run(seed=3)
    for mid, m in result.managers.items():
        assert m.budget >= 0
        spent = sum(pk.price for pk in result.picks if pk.winner_id == mid)
        assert spent == result.spend(mid)
        assert spent <= config.budget


def test_every_price_is_at_least_min_bid():
    result, _ = _run(seed=5)
    assert result.picks  # something happened
    assert all(pk.price >= MIN_BID for pk in result.picks)


def test_invariant_checker_reports_clean():
    result, _ = _run(seed=9)
    assert invariant_violations(result) == []


def test_pool_shrinks_by_exactly_one_per_pick():
    # Total players drafted equals number of picks equals total slots filled.
    result, config = _run(seed=4)
    total_slots = config.teams * config.roster_size
    assert len(result.picks) == total_slots
    drafted = [pk.player.id for pk in result.picks]
    assert len(set(drafted)) == len(drafted)  # no player drafted twice


# --- outcome quality --------------------------------------------------------


def test_rosters_are_close_enough_in_strength():
    """No seat should end up far weaker than the rest just from RNG.

    Before bid shading, agents offered their full jittered value in a
    first-price auction, so the winner was whoever's jitter ran highest and
    overpaid by construction. At seed 1 that spread starter points from 1198.2
    to 1949.3 -- a gap of ~751, larger than any archetype difference Stage 4
    would introduce, which would have made archetype tests measure the RNG.
    Shading cuts it to ~300. The bound is deliberately loose: it is here to
    catch a return of the winner's curse, not to pin an exact number.
    """
    config = DraftConfig()
    for seed in (1, 2, 3, 7):
        result = run_draft(build_field(12, seed=seed), config, PLAYERS)
        points = [
            sum(s.points for s in starters(m.roster, config) if s is not None)
            for m in result.managers.values()
        ]
        assert max(points) - min(points) < 500, f"seed {seed} spread too wide"


# --- passing on a nomination ------------------------------------------------


class _PassingAgent:
    """Wraps a real agent but declines its first `passes` nominations."""

    def __init__(self, inner, passes):
        self.name = inner.name
        self._inner = inner
        self._passes_left = passes

    def nominate(self, state, my_id):
        if self._passes_left > 0:
            self._passes_left -= 1
            return None
        return self._inner.nominate(state, my_id)

    def bid(self, state, player, my_id):
        return self._inner.bid(state, player, my_id)


def test_one_seat_passing_does_not_end_the_draft():
    # M00 passes once, then drafts normally. Everyone still fills a legal roster.
    config = DraftConfig(teams=4)
    agents = build_field(4, seed=11)
    agents["M00"] = _PassingAgent(agents["M00"], passes=1)
    result = run_draft(agents, config, PLAYERS)
    assert invariant_violations(result) == []
    for manager in result.managers.values():
        assert len(manager.roster) == config.roster_size


def test_draft_ends_when_every_seat_passes():
    # Nobody will ever nominate -> the draft ends cleanly with no picks, rather
    # than spinning forever.
    config = DraftConfig(teams=4)
    agents = {
        manager_id: _PassingAgent(agent, passes=10_000)
        for manager_id, agent in build_field(4, seed=11).items()
    }
    result = run_draft(agents, config, PLAYERS)
    assert result.picks == ()
    for manager in result.managers.values():
        assert manager.roster == []
        assert manager.budget == config.budget


def test_seat_that_cannot_cover_the_reserve_is_excluded_from_bidding():
    # One seat starts broke enough that max_bid falls below MIN_BID; it must be
    # skipped entirely rather than submitting a $0 bid the resolver could pick.
    config = DraftConfig(teams=2, budget=4, roster_slots=("QB", "WR", "BN", "BN"))
    agents = build_field(2, seed=1)
    pool = [
        Player(id=f"p{i}", name=f"P{i}", pos=pos, team="FA", proj_dollar=3, points=10.0)
        for i, pos in enumerate(["QB", "QB", "WR", "WR", "TE", "TE", "RB", "RB"])
    ]
    result = run_draft(agents, config, pool)
    # Nobody was ever awarded a player they could not pay for.
    for manager in result.managers.values():
        assert manager.budget >= 0
    assert all(pick.price >= MIN_BID for pick in result.picks)


def test_runs_on_a_small_field():
    # Sanity that the engine isn't hardwired to 12 seats.
    result, config = _run(seed=1, teams=4)
    assert len(result.managers) == 4
    for m in result.managers.values():
        assert is_lineup_legal(m.roster, config)


def test_picks_record_the_sealed_bids():
    # The report reads pick.bids to show who lost and by how much, so the log
    # has to agree with the sale it belongs to.
    result, _ = _run(seed=3, teams=4)
    assert result.picks
    for pick in result.picks:
        assert pick.bids, f"pick {pick.pick_no} has no bid log"
        amounts = [bid.amount for bid in pick.bids]
        assert pick.price == max(amounts)
        assert pick.winner_id in {bid.manager_id for bid in pick.bids}
        # Only real offers are logged; a seat sitting out leaves no Bid behind.
        assert all(amount >= MIN_BID for amount in amounts)
        assert len({bid.manager_id for bid in pick.bids}) == len(pick.bids)


def test_no_seat_drafts_a_second_defense():
    # Structural, not a rule: DEF has one lineup slot and no flex accepts it, so
    # a seat that owns one has no reason to want another. Guards the whole path
    # -- a seat used to nominate a spare DEF (the only cheap position still
    # carrying a $PROJ) and the forced MIN_BID open then made it buy it.
    from collections import Counter

    for seed in (1, 2, 3):
        result, _ = _run(seed=seed)
        for manager in result.managers.values():
            counts = Counter(p.pos for p in manager.roster)
            assert counts["DEF"] <= 1, f"seed {seed} {manager.manager_id}: {counts}"
            assert counts["K"] == 0, f"seed {seed} drafted a kicker: {counts}"


def test_seats_do_not_all_build_the_identical_roster_shape():
    # The startable slots sum to exactly the roster size, so a hard positional
    # ceiling would force every seat into the same 2/4/5/4/1 shape and leave
    # Stage 4 archetypes nothing to vary. bench_insurance keeps that open.
    from collections import Counter

    result, _ = _run(seed=1)
    shapes = {
        tuple(sorted(Counter(p.pos for p in m.roster).items()))
        for m in result.managers.values()
    }
    assert len(shapes) > 1
