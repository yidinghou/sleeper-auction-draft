from draftsim.auction import MIN_BID, Bid, can_bid, max_bid, resolve_auction_round


def test_no_bids_has_no_winner():
    assert resolve_auction_round([]) is None


def test_highest_amount_wins():
    bids = [Bid("a", 10, 1), Bid("b", 25, 2), Bid("c", 15, 3)]
    assert resolve_auction_round(bids) == ("b", 25)


def test_tie_broken_by_earliest_submission():
    bids = [Bid("late", 20, 5), Bid("early", 20, 2), Bid("mid", 20, 3)]
    assert resolve_auction_round(bids) == ("early", 20)


def test_resolution_is_order_independent():
    bids = [Bid("a", 10, 1), Bid("b", 25, 2), Bid("c", 25, 1)]
    forward = resolve_auction_round(bids)
    backward = resolve_auction_round(list(reversed(bids)))
    assert forward == backward == ("c", 25)  # tie 25 -> earliest tick (1)


def test_full_collision_is_still_order_independent():
    # Same amount AND same tick: manager_id is the final tiebreak, so the winner
    # is deterministic regardless of iteration order (in the sim, many bids for a
    # round can share a logical tick).
    bids = [Bid("b", 20, 1), Bid("a", 20, 1)]
    assert resolve_auction_round(bids) == ("a", 20)
    assert resolve_auction_round(list(reversed(bids))) == ("a", 20)


# --- reserve rule ---------------------------------------------------------


def test_max_bid_reserves_one_dollar_per_other_slot():
    # 16 open slots, $200 -> can spend at most 200 - 15 = 185 now.
    assert max_bid(200, 16) == 185


def test_max_bid_last_slot_can_use_whole_budget():
    assert max_bid(200, 1) == 200


def test_max_bid_full_roster_is_zero():
    assert max_bid(200, 0) == 0
    assert max_bid(200, -1) == 0


def test_max_bid_never_negative():
    assert max_bid(5, 16) == 0


# --- can_bid sentinel -----------------------------------------------------


def test_can_bid_true_only_when_a_real_offer_exists():
    assert can_bid(200, 16) is True          # plenty of room
    assert can_bid(200, 1) is True           # last slot, whole budget
    assert can_bid(MIN_BID, 1) is True       # exactly the floor


def test_can_bid_false_distinguishes_the_two_zero_cases():
    # Full roster: max_bid is 0 because there is nothing to buy...
    assert max_bid(200, 0) == 0
    assert can_bid(200, 0) is False
    # ...and budget-starved: max_bid is also 0, but for lack of money. Both are
    # "cannot bid", so callers gate on can_bid instead of reading 0 as a $0 bid.
    assert max_bid(5, 16) == 0
    assert can_bid(5, 16) is False
