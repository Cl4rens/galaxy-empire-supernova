"""Battle rules and the attack mission."""

import random

from supernova import combat, db, fleets, mails
from supernova import planet as planet_rules

from .test_fleets import new_player_with_cargo, position


def test_strong_fleet_wins_against_nothing_and_weak_defense():
    rng = random.Random(1)
    assert combat.fight(combat.Side("a", {3: 5}), combat.Side("d", {}), rng)["winner"] == "attacker"
    result = combat.fight(combat.Side("a", {3: 20}), combat.Side("d", {15: 5}), rng)
    assert result["winner"] == "attacker" and result["attacker_left"][3] >= 15
    assert 1 <= len(result["rounds"]) <= combat.MAX_ROUNDS


def test_shots_below_one_percent_of_shield_bounce():
    # Small cargos (weapon 5) cannot hurt a large shield dome (shield 10000).
    result = combat.fight(combat.Side("a", {9: 50}), combat.Side("d", {22: 1}), random.Random(2))
    assert result["defender_left"] == {22: 1}
    assert result["winner"] == "draw" and len(result["rounds"]) == combat.MAX_ROUNDS


def test_technology_helps():
    rng = random.Random(3)
    wins = sum(combat.fight(combat.Side("a", {1: 30}, weapons=10, shielding=10, armour=10),
                            combat.Side("d", {1: 30}), rng)["winner"] == "attacker" for _ in range(5))
    assert wins == 5


def test_plunder_and_debris():
    take = combat.plunder({"metal": 10_000, "crystal": 10_000, "deuterium": 10_000}, 9_000)
    assert sum(take.values()) == 9_000 and all(v <= 5_000 for v in take.values())
    assert combat.plunder({"metal": 1000, "crystal": 0, "deuterium": 0}, 1_000_000) == \
        {"metal": 500, "crystal": 0, "deuterium": 0}
    assert combat.debris({1: 10, 15: 100}) == {"metal": 9000, "crystal": 3000}


def test_attack_mission():
    attacker, defender = new_player_with_cargo(0), new_player_with_cargo(0)
    with db.player_state(attacker) as state:
        planet_rules.fleet(state["empire"]["main_planet"], 3)["amount"] = 30  # cruisers
        planet_rules.fleet(state["empire"]["main_planet"], 10)["amount"] = 5  # large cargo
    with db.player_state(defender) as state:
        planet_rules.fleet(state["empire"]["main_planet"], 15)["amount"] = 10  # rocket launchers
        planet_rules.fleet(state["empire"]["main_planet"], 1)["amount"] = 4  # light fighters
    now = 2_000_000_000.0
    with db.player_state(attacker) as state:
        p = state["empire"]["main_planet"]
        ids = f"{planet_rules.fleet(p, 3)['fleet_id']},{planet_rules.fleet(p, 10)['fleet_id']},"
        fleets.send(attacker, state, {"fleet_ids": ids, "fleet_cnt_ids": "30,5,",
                                      "end_p_pos": ",".join(map(str, position(defender))),
                                      "mission": str(fleets.ATTACK), "speed_ratio": "100"}, now)

    warning = fleets.events_view(defender)
    assert len(warning) == 1 and warning[0]["mission"] == fleets.UNDER_ATTACK

    defender_metal = db.load_state(defender)["empire"]["main_planet"]["metal"]
    fleets.process_due(fleets.events_view(attacker)[0]["finish_time"])
    report = mails.undelivered(attacker)[0]["body"]
    result = report["attack_result"]
    assert result["win"] == "win" and result["winner_name"] == result["attacker_name"]
    assert result["round"] and result["defend_end_fleets_count"] <= 14
    loot = result["loot"]
    assert sum(loot.values()) > 0
    assert db.load_state(defender)["empire"]["main_planet"]["metal"] <= defender_metal - loot["metal"] + 1000
    assert mails.undelivered(defender)[0]["body"]["attack_result"]["round"] == result["round"]
    assert fleets.events_view(defender) == []  # no more warning after the battle

    back = fleets.events_view(attacker)[0]
    assert back["is_returning"]
    fleets.process_due(back["finish_time"])
    p = db.load_state(attacker)["empire"]["main_planet"]
    assert p["metal"] >= 50_000 + loot["metal"] - 100
    assert 25 <= planet_rules.fleet(p, 3)["amount"] <= 30
