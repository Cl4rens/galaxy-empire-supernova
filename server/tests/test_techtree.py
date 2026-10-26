"""What researches do besides unlocking things: Computer Tech flight routes, Astrophysics colonies
and expeditions, Hyperspace Tech fuel, and the Pioneer Lab techs."""

import pytest

from supernova import db, fleets, vip
from supernova import economy as eco
from supernova import planet as planet_rules

from .test_fleets import new_player_with_cargo, position, send_params
from .test_npc import NOW
from .test_planet import new_player


def _research(state, rtype, level):
    next(r for r in state["empire"]["researches"] if r["research_type"] == rtype)["research_level"] = level


def test_computer_tech_flight_routes_and_vip():
    assert [eco.fleet_slots(lv) for lv in (0, 1, 2, 3, 4, 10)] == [1, 1, 2, 2, 3, 6]
    pid = new_player_with_cargo(5)
    target = position(new_player_with_cargo())

    def transport():
        with db.player_state(pid) as state:
            fleets.send(pid, state, send_params(pid, target), NOW)

    transport()
    with pytest.raises(planet_rules.GameError, match="All 1 flight routes"):
        transport()
    with db.player_state(pid) as state:
        _research(state, eco.COMPUTER_TECH, 2)
    transport()
    with pytest.raises(planet_rules.GameError, match="All 2 flight routes"):
        transport()
    with db.player_state(pid) as state:  # VIP 4 adds a flight route
        vip.add_points(state, 5000)
        vip.activate(state, 1, NOW)
    transport()
    assert fleets.flights_in_air(pid) == 3


def test_astrophysics_table():
    assert [eco.colonies(lv) for lv in (0, 1, 2, 3, 4, 16, 26)] == [0, 0, 1, 1, 2, 8, 8]
    assert [eco.safe_explorations(lv) for lv in (0, 1, 2, 4, 8, 9, 30)] == [0, 8, 16, 48, 80, 120, 120]
    assert [eco.expedition_fleets(lv) for lv in (0, 1, 4, 9, 16, 25, 30)] == [0, 1, 2, 3, 4, 5, 5]
    state = new_player()
    _research(state, eco.ASTROPHYSICS, 5)
    assert fleets.max_planets(state["empire"]) == 3


def test_hyperspace_tech_saves_fuel_as_measured_in_the_wiki():
    ships, dist = {12: 2}, 20000
    measured = {0: 428, 1: 389, 2: 364, 3: 347, 5: 330, 10: 318, 14: 317}
    for level, fuel in measured.items():
        ratio = eco.fuel(ships, dist, 100, level) / eco.fuel(ships, dist, 100)
        assert abs(ratio - fuel / 428) < 0.005, level


def test_pioneer_lab_is_cheaper_and_faster_with_research():
    state = new_player()
    empire = state["empire"]
    planet = empire["main_planet"]
    planet_rules.tick(empire, NOW)
    full_cost, full_time = planet_rules._upgrade(planet, eco.PIONEER_LAB, 0)
    _research(state, eco.ULTRA_TEMPERATURE_POLYMERIZER, 5)
    _research(state, eco.ANTI_GRAVITY, 10)
    planet_rules.tick(empire, NOW)
    cost, seconds = planet_rules._upgrade(planet, eco.PIONEER_LAB, 0)
    assert cost == tuple(c * 85 // 100 for c in full_cost)
    assert seconds == full_time * 80 // 100
    assert planet_rules._upgrade(planet, eco.METAL_MINE, 0)[0] == eco.upgrade_cost(eco.METAL_MINE, 0)


def test_laser_and_ion_tech_speed_up_constructions():
    state = new_player()
    empire = state["empire"]
    planet = empire["main_planet"]
    planet_rules.tick(empire, NOW)
    slow = planet_rules._upgrade(planet, eco.METAL_MINE, 5)[1]
    _research(state, eco.LASER_TECH, 6)
    _research(state, eco.ION_TECH, 4)
    planet_rules.tick(empire, NOW)
    assert planet_rules._upgrade(planet, eco.METAL_MINE, 5)[1] == int(slow / 2)  # 10 levels: twice as fast
    assert planet_rules._faster(planet, "research", 1000) == 1000  # research is not a construction
