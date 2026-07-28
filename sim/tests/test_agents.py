from draftsim.agents import DraftAgent, HeuristicAgent, build_field
from draftsim.auction import MIN_BID, max_bid
from draftsim.config import DraftConfig
from draftsim.engine import DraftState, ManagerState
from draftsim.valuation import Player, replacement_points


def _state(config, rosters, budgets, pool):
    managers = {
        mid: ManagerState(mid, budgets[mid], list(rosters.get(mid, [])))
        for mid in budgets
    }
    return DraftState(config, managers, list(pool), replacement_points(list(pool), config))


def P(name, pos, dollar=0, points=0.0):
    return Player(id=name, name=name, pos=pos, team="FA", proj_dollar=dollar, points=points)


def test_heuristic_agent_satisfies_protocol():
    assert isinstance(HeuristicAgent("x"), DraftAgent)


def test_build_field_gives_distinct_deterministic_seats():
    field = build_field(3, seed=42)
    assert set(field) == {"M00", "M01", "M02"}
    assert field["M00"].seed != field["M01"].seed
    # Same master seed rebuilds the identical field.
    assert build_field(3, seed=42)["M01"].seed == field["M01"].seed


# --- valuation determinism -------------------------------------------------


def test_valuation_is_stable_and_order_independent():
    agent = HeuristicAgent("a", seed="1")
    p = P("Star", "WR", dollar=50)
    first = agent._valuation(p)
    # Same object, and a fresh agent with the same seed, both agree.
    assert agent._valuation(p) == first
    assert HeuristicAgent("a", seed="1")._valuation(p) == first
    # Jitter stays within the configured band.
    assert 50 * (1 - agent.jitter_frac) <= first <= 50 * (1 + agent.jitter_frac)


# --- bidding rails ---------------------------------------------------------


def test_full_roster_cannot_bid():
    config = DraftConfig()
    full = [P(f"p{i}", "WR") for i in range(config.roster_size)]
    state = _state(config, {"me": full}, {"me": 200}, [P("x", "WR", 10)])
    assert HeuristicAgent("me").bid(state, P("x", "WR", 10), "me") == 0


def test_bid_never_exceeds_reserve_ceiling():
    config = DraftConfig()
    state = _state(config, {"me": []}, {"me": 200}, [])
    agent = HeuristicAgent("me", jitter_frac=0.0)
    # A wildly over-priced player: bid must still clamp to the reserve ceiling.
    got = agent.bid(state, P("Whale", "QB", dollar=10_000), "me")
    assert got == max_bid(200, config.roster_size)


def test_depth_is_discounted_below_a_need_of_equal_price():
    config = DraftConfig()
    # Roster already has its full QB quota (2) -> another QB is depth; a WR is a
    # need. Equal market price, but depth should be bid lower.
    roster = [P("qb1", "QB"), P("qb2", "QB")]
    state = _state(config, {"me": roster}, {"me": 200}, [])
    agent = HeuristicAgent("me", jitter_frac=0.0, depth_value_mult=0.4)
    need_bid = agent.bid(state, P("wrN", "WR", dollar=30), "me")
    depth_bid = agent.bid(state, P("qbN", "QB", dollar=30), "me")
    assert depth_bid < need_bid


def test_depth_refused_when_all_slots_claimed_by_needs():
    # roster_size == number of hard needs -> no room for depth at all.
    config = DraftConfig(teams=1, budget=50, roster_slots=("QB", "WR"))
    state = _state(config, {"me": []}, {"me": 50}, [])
    agent = HeuristicAgent("me", jitter_frac=0.0)
    # QB and WR are both needs and both get bids...
    assert agent.bid(state, P("qb", "QB", dollar=10), "me") >= MIN_BID
    assert agent.bid(state, P("wr", "WR", dollar=10), "me") >= MIN_BID
    # ...but a TE (no slot for it) is pure depth with no room -> refused.
    assert agent.bid(state, P("te", "TE", dollar=10), "me") == 0


# --- nomination ------------------------------------------------------------


def test_nominates_a_needed_position_over_a_pricier_non_need():
    config = DraftConfig()
    roster = [P("qb1", "QB"), P("qb2", "QB")]  # QB quota met
    pool = [P("cheapWR", "WR", dollar=5), P("richQB", "QB", dollar=99)]
    state = _state(config, {"me": roster}, {"me": 200}, pool)
    nom = HeuristicAgent("me", jitter_frac=0.0).nominate(state, "me")
    assert nom.pos == "WR"  # a need beats the richer non-need
