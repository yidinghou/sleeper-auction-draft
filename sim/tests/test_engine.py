from draftsim.agents import build_field
from draftsim.auction import MIN_BID
from draftsim.config import DraftConfig
from draftsim.engine import invariant_violations, run_draft
from draftsim.roster import is_lineup_legal
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
