"""Reads the real `data/projections-2026.csv`, so these tests double as a check
that the export hasn't changed shape under us."""

from __future__ import annotations

import pytest

from draftsim import by_sleeper_id, load_players, load_projections, make_id
from draftsim.player import DEFAULT_CSV


@pytest.fixture(scope="module")
def players():
    return load_players()


@pytest.fixture(scope="module")
def proj():
    return load_projections()


def test_the_projections_csv_is_where_we_think_it_is():
    assert DEFAULT_CSV.exists(), f"missing {DEFAULT_CSV} -- run npm run export:projections"


def test_the_pool_is_big_enough_to_fill_a_draft(players):
    assert len(players) > 12 * 16


def test_spot_check_josh_allen(players, proj):
    allen = next(p for p in players if p.name == "Josh Allen" and p.position == "QB")
    assert allen.team == "BUF"
    assert allen.market.rank == 1
    assert allen.market.bye == 7
    assert allen.market.proj_dollar == 58
    assert allen.market.sleeper_id == "4984"
    assert proj[allen.id] == pytest.approx(361.5)
    assert allen.week.week1 == pytest.approx(22.89)


def test_free_agents_are_dropped_unless_asked_for(players):
    assert all(p.team for p in players)
    assert len(load_players(free_agents=True)) > len(players)


def test_ids_are_unique_and_reproducible(players):
    ids = [p.id for p in players]
    assert len(set(ids)) == len(ids)
    allen = next(p for p in players if p.name == "Josh Allen" and p.position == "QB")
    assert allen.id == make_id("Josh Allen", "QB", "BUF") == "josh-allen|qb|buf"


def test_projections_are_keyed_the_same_way_players_are(players, proj):
    assert {p.id for p in players} == set(proj)


def test_players_carry_no_draft_state(players):
    fields = set(vars(players[0]))
    assert not fields & {"drafted", "owner", "price", "points"}


def test_the_sleeper_index_covers_the_pool(players):
    index = by_sleeper_id(players)
    assert len(index) == len(players)
    assert index["4984"].name == "Josh Allen"
