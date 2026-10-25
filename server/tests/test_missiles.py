"""Intercept and interplanetary missiles."""

import random

import pytest

from supernova import combat, db
from supernova import economy as eco
from supernova import planet as planet_rules

from .test_acs import member


def side(units, tech=5):
    return combat.Side("x", units, missile_tech=tech)


def test_limits_follow_the_client():
    assert combat.intercept_limit(0) == 0 and combat.intercept_limit(1) == 23 and combat.intercept_limit(60) == 170
    assert combat.interplanetary_limit(3) == 6 and combat.interplanetary_limit(80) == 100


def test_intercept_missiles_stop_annihilators_missiles_and_big_ships_in_order():
    attacker = {28: 10, 31: 5, 24: 20, 1: 100}
    report = combat.intercept(attacker, side({30: 30}, tech=2))  # limit 26
    assert report == {"origin": 30, "cost": 26, "intercept": [
        {"fleet_type": 28, "amount": 10}, {"fleet_type": 31, "amount": 5}, {"fleet_type": 24, "amount": 11}]}
    assert attacker == {28: 0, 31: 0, 24: 9, 1: 100}


def test_interplanetary_missiles_hit_reapers_first_then_the_biggest_ships():
    own, enemy = {31: 4}, {29: 2, 26: 10, 1: 50}
    report = combat.launch(side(own), own, side(enemy), enemy, True)
    assert own[31] == 0 and report["cost"] == 4 and enemy[29] == 0
    assert {"fleet_type": 29, "amount": 2} in report["ban"] and enemy[26] == 6 and enemy[1] == 50  # 2 frigates per missile
    own = {31: 4}
    assert combat.launch(side(own), own, side(enemy), enemy, False)["cost"] == 0 and own[31] == 4


def test_battle_report_and_unused_missiles():
    result = combat.fight(side({31: 2, 7: 50, 28: 1}), side({30: 1, 29: 1, 4: 5, 23: 1, 24: 1}),
                          random.Random(5))
    assert result["intercept_missile_info"]["intercept"] == [{"fleet_type": 28, "amount": 1}]
    assert result["interplanetary_missile_info"]["attacker_interplanetary_missile_info"]["cost"] == 2
    assert result["self_destruction"]["self_count"] == 0  # the Annihilator was intercepted
    assert 30 not in result["defender_left"]  # used up, and never rebuilt
    assert combat.rebuild_defense({30: 5}) == {}


def test_planets_hold_only_the_limit():
    _, me = member("missile-builder", {})
    with db.player_state(me["user_id"]) as s:
        empire = s["empire"]
        planet = empire["main_planet"]
        planet.update(metal=1e9, crystal=1e9, deuterium=1e9)
        planet_rules.building(planet, eco.SHIPYARD).update(building_level=4, building_position_index=6)
        planet_rules.building(planet, eco.RESEARCH_LAB).update(building_level=10, building_position_index=7)
        research = next(r for r in empire["researches"] if r["research_type"] == eco.MISSILE_TECH)
        research["research_level"] = 1
        planet_rules.add_missing_fleets(planet)
        fid = planet_rules.fleet(planet, 31)["fleet_id"]
        planet_rules.start_fleet_order(empire, s, fid, 2, 0)
        with pytest.raises(planet_rules.GameError):
            planet_rules.start_fleet_order(empire, s, fid, 1, 0)
