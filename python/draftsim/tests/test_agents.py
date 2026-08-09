from draftsim.agents import SIT_OUT, DraftAgent, HeuristicAgent, build_field
from draftsim.auction import MIN_BID, max_bid
from draftsim.config import DraftConfig
from draftsim.engine import DraftState, ManagerState
from draftsim.valuation import Player, points_per_dollar, replacement_points


def _state(config, rosters, budgets, pool):
    managers = {
        mid: ManagerState(mid, budgets[mid], list(rosters.get(mid, [])))
        for mid in budgets
    }
    replacement = replacement_points(list(pool), config)
    return DraftState(
        config,
        managers,
        list(pool),
        replacement,
        points_per_dollar(list(pool), replacement, config),
    )


def _thresholds(config, roster, pool):
    """Marginal thresholds for a roster, as _valuation now expects."""
    from draftsim.roster import marginal_thresholds
    from draftsim.valuation import replacement_points

    return marginal_thresholds(roster, config, replacement_points(list(pool), config))


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
    config = DraftConfig()
    agent = HeuristicAgent("a", seed="1")
    p = P("Star", "WR", dollar=50)
    state = _state(config, {"me": []}, {"me": 200}, [p])
    th = _thresholds(config, [], [p])
    first = agent._valuation(state, p, th)
    # Same object, and a fresh agent with the same seed, both agree.
    assert agent._valuation(state, p, th) == first
    assert HeuristicAgent("a", seed="1")._valuation(state, p, th) == first
    # Jitter stays within the configured band.
    band = 50 * (1 + agent.jitter_frac)
    assert 0 < first <= band


def test_vorp_weight_zero_is_the_market_anchor():
    # Guards the default: with vorp_weight off, valuation must be market-only.
    config = DraftConfig()
    pool = [P(f"wr{i}", "WR", dollar=20, points=200.0 - i) for i in range(40)]
    state = _state(config, {"me": []}, {"me": 200}, pool)
    target = pool[0]
    th = _thresholds(config, [], pool)
    market_only = HeuristicAgent("a", seed="1", vorp_weight=0.0)
    # Market-only ignores points entirely: every $20 player is worth the same
    # up to its own jitter, however far apart they project.
    assert market_only._valuation(state, target, th) == market_only._valuation(
        state, P("wr0", "WR", dollar=20, points=1.0), th
    )
    # And turning it on actually moves the number.
    blended = HeuristicAgent("a", seed="1", vorp_weight=1.0)
    assert blended._valuation(state, target, th) != market_only._valuation(state, target, th)


# --- bidding rails ---------------------------------------------------------


def test_full_roster_cannot_bid():
    config = DraftConfig()
    full = [P(f"p{i}", "WR") for i in range(config.roster_size)]
    state = _state(config, {"me": full}, {"me": 200}, [P("x", "WR", 10)])
    assert HeuristicAgent("me").bid(state, P("x", "WR", 10), "me") == 0


def test_bid_never_exceeds_reserve_ceiling():
    config = DraftConfig()
    state = _state(config, {"me": []}, {"me": 200}, [])
    # Cap off, so the reserve ceiling is the only thing left to clamp.
    agent = HeuristicAgent("me", jitter_frac=0.0, max_pick_share=1.0)
    got = agent.bid(state, P("Whale", "QB", dollar=10_000), "me")
    assert got == max_bid(200, config.roster_size)


def test_bid_offers_full_market_value_without_shading():
    # No holdback: a $40 player draws a $40 offer once jitter and the cap are
    # out of the way. Bidding under value stranded budget; see HeuristicAgent.bid.
    config = DraftConfig()
    state = _state(config, {"me": []}, {"me": 200}, [])
    agent = HeuristicAgent("me", jitter_frac=0.0, max_pick_share=1.0)
    assert agent.bid(state, P("Star", "QB", dollar=40), "me") == 40


def test_pick_cap_limits_one_buy_to_a_share_of_the_ceiling():
    config = DraftConfig()
    state = _state(config, {"me": []}, {"me": 200}, [])
    ceiling = max_bid(200, config.roster_size)
    agent = HeuristicAgent("me", jitter_frac=0.0, max_pick_share=0.5)
    # Even for a player worth far more than the whole budget, the cap binds.
    assert agent.bid(state, P("Whale", "QB", dollar=10_000), "me") == round(ceiling * 0.5)


def test_pick_cap_never_blocks_a_dollar_buy():
    # A seat down to its reserve has a ceiling of $1; the cap must not round
    # that to 0 and leave the roster short.
    config = DraftConfig()
    roster = [P(f"p{i}", "WR") for i in range(config.roster_size - 4)]
    state = _state(config, {"me": roster}, {"me": 4}, [])
    agent = HeuristicAgent("me", jitter_frac=0.0, max_pick_share=0.01)
    assert agent.bid(state, P("te", "TE", dollar=30), "me") >= MIN_BID


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

def test_dollar_ties_are_broken_by_points_not_jitter():
    # The defect this guards: only ~126 draftable players are priced above the
    # $1 floor, so the rest of the pool anchors at exactly $1. With a raw
    # valuation the jitter draw picked the nomination, handing the back half of
    # the draft to the RNG -- a 0-point body could outrank a real starter.
    # Jitter is left ON here; the test would fail without dollar quantization.
    config = DraftConfig()
    pool = [P("scrub", "WR", dollar=1, points=0.0), P("starter", "WR", dollar=1, points=120.0)]
    state = _state(config, {"me": []}, {"me": 200}, pool)
    for seed in ("1", "2", "3", "17"):
        agent = HeuristicAgent("me", seed=seed, jitter_frac=0.15)
        assert agent.nominate(state, "me").name == "starter"


def test_sleeper_rank_breaks_a_tie_on_equal_dollars_and_points():
    config = DraftConfig()
    ranked = Player(id="ranked", name="ranked", pos="WR", team="BUF",
                    proj_dollar=1, points=50.0, rank=40)
    unranked = Player(id="unranked", name="unranked", pos="WR", team="BUF",
                      proj_dollar=1, points=50.0, rank=None)
    worse = Player(id="worse", name="worse", pos="WR", team="BUF",
                   proj_dollar=1, points=50.0, rank=300)
    state = _state(config, {"me": []}, {"me": 200}, [unranked, worse, ranked])
    agent = HeuristicAgent("me", jitter_frac=0.0)
    assert agent.nominate(state, "me").name == "ranked"  # lowest rank wins


def test_quantization_does_not_invert_the_top_of_the_board():
    # Points only breaks ties *within* a dollar. A genuinely pricier player must
    # still be nominated over a cheaper one that happens to project more points.
    # vorp_weight is off so this tests the tiebreak alone -- with the blend on,
    # the 300-point player really is worth more and correctly wins.
    config = DraftConfig()
    pool = [P("pricey", "WR", dollar=50, points=10.0), P("cheap", "WR", dollar=5, points=300.0)]
    state = _state(config, {"me": []}, {"me": 200}, pool)
    agent = HeuristicAgent("me", jitter_frac=0.0, vorp_weight=0.0)
    assert agent.nominate(state, "me").name == "pricey"


# --- positional depth ceilings ---------------------------------------------


def test_startable_slots_counts_concrete_and_flex_routes():
    from draftsim.roster import startable_slots

    config = DraftConfig()
    # Default 2QB lineup: QB+SFLX, RB+RB+FLEX+SFLX, WR+WR+FLEX+RFLX+SFLX,
    # TE+FLEX+RFLX+SFLX, DEF, and no K slot at all.
    assert startable_slots("QB", config) == 2
    assert startable_slots("RB", config) == 4
    assert startable_slots("WR", config) == 5
    assert startable_slots("TE", config) == 4
    assert startable_slots("DEF", config) == 1
    assert startable_slots("K", config) == 0


def test_a_second_defense_adds_nothing_to_the_lineup():
    # No rule names DEF anywhere. It has one lineup slot and no flex accepts it,
    # so once a seat owns one, another cannot enter the lineup and its marginal
    # value is 0 -- which is the whole mechanism, tested at its source.
    from draftsim.roster import marginal_points

    config = DraftConfig()
    incumbent = P("def1", "DEF", dollar=2, points=100.0)
    spare = P("def2", "DEF", dollar=2, points=90.0)
    roster = [P(f"p{i}", "WR", points=150.0) for i in range(9)] + [incumbent]
    th = _thresholds(config, roster, [incumbent, spare])
    assert marginal_points(spare, th) == 0.0


def test_a_better_defense_is_still_an_upgrade():
    # The flip side, which a structural slot cap gets wrong: owning a weak
    # defense must not make a good one worthless.
    from draftsim.roster import marginal_points

    config = DraftConfig()
    weak = P("weak", "DEF", dollar=1, points=40.0)
    strong = P("strong", "DEF", dollar=2, points=100.0)
    roster = [P(f"p{i}", "WR", points=150.0) for i in range(9)] + [weak]
    th = _thresholds(config, roster, [weak, strong])
    assert marginal_points(strong, th) > 0.0


def test_a_second_defense_is_never_nominated():
    # The engine forces a nominator to open at MIN_BID, so nominating a body you
    # would not bid on is how you end up owning it.
    config = DraftConfig()
    roster = [P(f"p{i}", "WR") for i in range(3)] + [P("def1", "DEF", dollar=2, points=100.0)]
    # The spare DEF projects more points than the spare RB, so a points-ordered
    # nomination would take it if insurance didn't rank it below.
    pool = [P("def2", "DEF", dollar=1, points=90.0), P("rb", "RB", dollar=1, points=40.0)]
    state = _state(config, {"me": roster}, {"me": 200}, pool)
    assert HeuristicAgent("me", jitter_frac=0.0).nominate(state, "me").pos == "RB"


def test_insurance_ranks_a_spare_receiver_over_a_defense_over_a_kicker():
    # What orders the bench once nothing improves the lineup: how many slots the
    # position could ever fill, times quality against replacement.
    config = DraftConfig()
    pool = [P(f"wr{i}", "WR", points=200.0 - i) for i in range(40)]
    pool += [P(f"d{i}", "DEF", points=100.0 - i) for i in range(14)]
    pool += [P(f"k{i}", "K", points=150.0 - i) for i in range(14)]
    state = _state(config, {"me": []}, {"me": 200}, pool)
    agent = HeuristicAgent("me", jitter_frac=0.0)
    wr = agent._insurance(state, pool[39])
    de = agent._insurance(state, pool[40 + 13])
    ki = agent._insurance(state, pool[40 + 14 + 13])
    assert wr > de > ki
    assert ki == 0.0  # no K slot in the lineup at all
