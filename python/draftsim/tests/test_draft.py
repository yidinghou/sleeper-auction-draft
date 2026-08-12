from __future__ import annotations

import pytest
from conftest import make_draft, make_pool, make_teams

from draftsim import Draft, DraftRules, InvalidPick, Pick


def test_the_nominator_rotates_with_the_ledger():
    draft = make_draft()
    assert draft.nominator().id == "t0"
    draft.record_pick("t0", "QB00|QB", 10, 0.0)
    assert draft.nominator().id == "t1"
    draft.record_pick("t5", "QB01|QB", 10, 1.0)  # out of turn: still the 3rd sale
    assert draft.nominator().id == "t2"


def test_recording_a_pick_appends_to_the_ledger_and_updates_the_cache():
    draft = make_draft()
    pick = draft.record_pick("t3", "RB00|RB", 42, 1.5)
    assert pick == Pick("t3", "RB00|RB", 42, 1.5)
    assert draft.picks == [pick]
    ts = draft.team_state("t3")
    assert [p.id for p in ts.roster] == ["RB00|RB"]
    assert ts.spent == 42
    assert ts.remaining(draft.rules) == 158
    assert draft.owner_of("RB00|RB") == "t3"


def test_an_undrafted_player_has_no_owner():
    draft = make_draft()
    assert draft.owner_of("RB00|RB") is None


def test_check_pick_rejects_an_unknown_team():
    draft = make_draft()
    assert "no such team" in draft.check_pick("nobody", "QB00|QB", 5)


def test_check_pick_rejects_an_unknown_player():
    draft = make_draft()
    assert "no such player" in draft.check_pick("t0", "ghost|WR", 5)


def test_check_pick_rejects_a_player_already_sold():
    draft = make_draft()
    draft.record_pick("t0", "QB00|QB", 30, 0.0)
    assert "already drafted by t0" in draft.check_pick("t1", "QB00|QB", 40)


def test_check_pick_rejects_a_price_below_the_minimum():
    draft = make_draft()
    assert "below the $1 minimum" in draft.check_pick("t0", "QB00|QB", 0)


def test_check_pick_rejects_a_price_above_the_max_bid():
    draft = make_draft()
    assert draft.check_pick("t0", "QB00|QB", 185) is None
    assert "exceeds" in draft.check_pick("t0", "QB00|QB", 186)


def test_check_pick_rejects_a_full_roster():
    rules = DraftRules(teams=2, roster_slots=("QB",), budget=10)
    players, proj = make_pool(("A", "QB", 10.0), ("B", "QB", 5.0))
    draft = make_draft(players, proj, teams=2, rules=rules)
    draft.record_pick("t0", "A|QB", 5, 0.0)
    assert "full roster" in draft.check_pick("t0", "B|QB", 1)


def test_check_pick_passes_a_legal_sale():
    draft = make_draft()
    assert draft.check_pick("t0", "QB00|QB", 30) is None


def test_an_illegal_pick_raises_and_leaves_the_ledger_untouched():
    draft = make_draft()
    draft.record_pick("t0", "QB00|QB", 30, 0.0)
    with pytest.raises(InvalidPick, match="already drafted"):
        draft.record_pick("t1", "QB00|QB", 40, 1.0)
    assert len(draft.picks) == 1
    assert draft.team_state("t1").roster == ()
    assert draft.team_state("t1").spent == 0


def test_undo_pops_the_last_sale_and_restores_the_prior_state():
    draft = make_draft()
    draft.record_pick("t0", "QB00|QB", 30, 0.0)
    before = draft.team_state("t0")
    snapshot = (before.roster, before.spent, before.lineup)

    draft.record_pick("t0", "WR00|WR", 40, 1.0)
    undone = draft.undo()

    assert undone == Pick("t0", "WR00|WR", 40, 1.0)
    after = draft.team_state("t0")
    assert (after.roster, after.spent, after.lineup) == snapshot
    assert draft.owner_of("WR00|WR") is None
    assert draft.nominator().id == "t1"


def test_undo_on_an_empty_ledger_is_a_no_op():
    draft = make_draft()
    assert draft.undo() is None
    assert draft.picks == []


def test_available_shrinks_as_the_ledger_grows():
    draft = make_draft()
    before = len(draft.available())
    draft.record_pick("t0", "QB00|QB", 30, 0.0)
    assert len(draft.available()) == before - 1
    assert all(p.id != "QB00|QB" for p in draft.available())


def test_a_draft_built_from_an_existing_ledger_rebuilds_its_cache():
    players, proj = make_pool(("A", "QB", 300.0), ("B", "WR", 200.0))
    draft = Draft(
        rules=DraftRules(teams=2),
        players=players,
        teams=make_teams(2),
        proj=proj,
        picks=[Pick("t1", "A|QB", 25, 0.0)],
    )
    assert draft.team_state("t1").spent == 25
    assert draft.owner_of("A|QB") == "t1"
    assert draft.nominator().id == "t1"  # one sale in, so seat 1 is up


def test_duplicate_team_ids_are_rejected():
    players, proj = make_pool(("A", "QB", 1.0))
    with pytest.raises(ValueError, match="unique"):
        Draft(
            rules=DraftRules(teams=2),
            players=players,
            teams=[make_teams(1)[0], make_teams(1)[0]],
            proj=proj,
        )
