"""Pirate bases and expeditions."""

import random

import pytest

from supernova import db, fleets, mails, npc
from supernova import economy as eco
from supernova import planet as planet_rules

from .test_fleets import new_player_with_cargo, position

NOW = 2_000_000_000.0


def send(pid, ships: dict[int, int], target, mission):
    with db.player_state(pid) as state:
        p = state["empire"]["main_planet"]
        for t, n in ships.items():
            planet_rules.fleet(p, t)["amount"] = n
        ids = ",".join(str(planet_rules.fleet(p, t)["fleet_id"]) for t in ships) + ","
        counts = ",".join(str(n) for n in ships.values()) + ","
        fleets.send(pid, state, {"fleet_ids": ids, "fleet_cnt_ids": counts, "mission": str(mission),
                                 "end_p_pos": ",".join(map(str, target)), "speed_ratio": "100"}, NOW)
    fleets.process_due(fleets.events_view(pid)[0]["finish_time"])


def test_pirate_base_scales_with_level():
    assert sum(npc.pirate_fleet(5).values()) > sum(npc.pirate_fleet(1).values())
    assert npc.pirate_loot(2)["metal"] == 4 * npc.pirate_loot(1)["metal"]


def test_beat_pirates_and_respawn():
    pid = new_player_with_cargo(0)
    galaxy, system, _ = position(pid)
    target = (galaxy, system, 17)  # level 1
    send(pid, {3: 40, 10: 5}, target, fleets.ATTACK)
    report = mails.undelivered(pid)[0]["body"]
    assert report["mission_type"] == fleets.ATTACK_PIRATES
    assert report["attack_result"]["win"] == "win"
    assert sum(report["attack_result"]["loot"].values()) > 0
    base = npc.pirate_base(*target, now=NOW + 10)
    assert base["fleet"] == {} and base["next_refresh_time"] > 0
    assert npc.pirate_base(*target, now=NOW + npc.respawn_seconds() + 86400)["fleet"]  # flight time + respawn


def test_spy_on_pirates():
    pid = new_player_with_cargo(0)
    galaxy, system, _ = position(pid)
    send(pid, {fleets.ESPIONAGE_PROBE: 1}, (galaxy, system, 19), fleets.ESPIONAGE)
    result = mails.undelivered(pid)[0]["body"]["probe_result"]
    assert result["pirate_level"] == 3 and result["pirate_name"] == "Pirates LV 3"
    assert result["fleets"]


@pytest.mark.parametrize("seed", range(30))
def test_expedition_outcomes_are_well_formed(seed):
    outcome = npc.explore({9: 10, 3: 5}, random.Random(seed))
    assert outcome["situation"] in {o for o, _ in npc.OUTCOMES}
    if outcome["situation"] == npc.RESOURCES:
        assert set(outcome["found"]) == {"metal", "crystal", "deuterium"}


def astrophysics(pid, level):
    with db.player_state(pid) as state:
        next(r for r in state["empire"]["researches"] if r["research_type"] == eco.ASTROPHYSICS)["research_level"] = level


def test_expedition_mission_and_daily_limit():
    pid = new_player_with_cargo(0)
    galaxy, system, _ = position(pid)
    with db.player_state(pid) as state:
        assert fleets.plan(state["empire"], pid, {9: 1}, (galaxy, system, 16))["missions"] == [fleets.EXPEDITION]
    with pytest.raises(planet_rules.GameError, match="Research Astrophysics"):
        send(pid, {9: 5}, (galaxy, system, 16), fleets.EXPEDITION)
    astrophysics(pid, 1)
    send(pid, {9: 5}, (galaxy, system, 16), fleets.EXPEDITION)
    mail = mails.undelivered(pid)[0]
    assert mail["message_type"] == mails.EXPLORE
    assert mail["body"]["explore_data"] == {"curr": 1, "max": 8}  # wiki: 8 safe runs at Astrophysics 1
    assert "situation" in mail["body"]["expedition_result"]
    with pytest.raises(planet_rules.GameError, match="1 expedition fleet"):  # still flying home
        send(pid, {9: 1}, (galaxy, system, 16), fleets.EXPEDITION)
    fleets.process_due(NOW + 10**7)

    for _ in range(8 - 1):
        npc.record_expedition(pid, NOW)
    with pytest.raises(planet_rules.GameError, match="secure explorations"):
        send(pid, {9: 1}, (galaxy, system, 16), fleets.EXPEDITION)
