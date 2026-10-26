"""Rules for buildings and production, tested with a controlled clock."""

import pytest

from supernova import economy as eco
from supernova import planet, player

T0 = 1_000_000.0


def new_player():
    state = player.new_state(1, "Tester")
    state["empire"]["main_planet"]["last_update"] = T0
    return state


def building_id(state, btype):
    return planet.building(state["empire"]["main_planet"], btype)["building_id"]


def test_upgrade_metal_mine_finishes_after_build_time():
    state = new_player()
    empire = state["empire"]
    p = empire["main_planet"]
    planet.start_building_event(empire, state, building_id(state, eco.METAL_MINE), planet.EVENT_UPGRADE, None, T0)
    assert p["metal"] == 500 - 60 and p["crystal"] == 500 - 15
    event = p["building_events"][0]
    assert event["total_time"] == eco.build_seconds(60, 15, 0, 0)

    planet.tick(empire, event["finish_time"] - 1)
    assert planet.level(p, eco.METAL_MINE) == 0
    planet.tick(empire, event["finish_time"])
    assert planet.level(p, eco.METAL_MINE) == 1
    assert p["building_events"] == []


def test_only_one_construction_at_a_time():
    state = new_player()
    empire = state["empire"]
    planet.start_building_event(empire, state, building_id(state, eco.METAL_MINE), planet.EVENT_UPGRADE, None, T0)
    with pytest.raises(planet.GameError):
        planet.start_building_event(empire, state, building_id(state, eco.CRYSTAL_MINE), planet.EVENT_UPGRADE,
                                    None, T0)


def test_base_production_and_storage_limit():
    state = new_player()
    empire = state["empire"]
    p = empire["main_planet"]
    planet.tick(empire, T0 + 3600)
    assert p["metal"] == pytest.approx(500 + eco.BASE_PRODUCTION["metal"] * eco.config.SPEED)
    planet.tick(empire, T0 + 3600 * 10_000)
    assert p["metal"] == planet.capacity(p, "metal")


def test_mines_need_energy():
    state = new_player()
    empire = state["empire"]
    p = empire["main_planet"]
    planet.building(p, eco.METAL_MINE)["building_level"] = 5
    without_energy = planet.production(p, empire)["metal"]
    planet.building(p, eco.SOLAR_PLANT)["building_level"] = 10
    with_energy = planet.production(p, empire)["metal"]
    assert without_energy == pytest.approx(eco.BASE_PRODUCTION["metal"] * eco.config.SPEED)
    assert with_energy > without_energy


def test_new_building_needs_free_lot_and_cancel_refunds():
    state = new_player()
    empire = state["empire"]
    p = empire["main_planet"]
    solar = building_id(state, eco.SOLAR_PLANT)
    with pytest.raises(planet.GameError):
        planet.start_building_event(empire, state, solar, planet.EVENT_UPGRADE, 1, T0)  # mine lot
    planet.start_building_event(empire, state, solar, planet.EVENT_UPGRADE, 4, T0)
    assert planet.building(p, eco.SOLAR_PLANT)["building_position_index"] == 4

    planet.cancel_building_event(empire, p["building_events"][0]["event_id"], T0)
    assert planet.building(p, eco.SOLAR_PLANT)["building_position_index"] == 0
    assert (p["metal"], p["crystal"]) == (500, 500)


def test_requirements():
    state = new_player()
    empire = state["empire"]
    with pytest.raises(planet.GameError, match="Requirements"):
        planet.start_building_event(empire, state, building_id(state, eco.SHIPYARD), planet.EVENT_UPGRADE, 4, T0)


def test_client_view_has_everything_the_ui_reads():
    state = new_player()
    view = planet.client_view(state["empire"], T0)
    for key in ("metal_perhour", "metal_max", "energy_max", "energy_used", "solar_plant_energy",
                "current_server_time"):
        assert key in view
    mine = next(b for b in view["buildings"] if b["building_type"] == eco.METAL_MINE)
    for key in ("building_name", "up_metal_require", "up_time", "up_extra_energy", "tech_tree"):
        assert key in mine


def test_teardown_frees_the_lot():
    state = new_player()
    empire = state["empire"]
    p = empire["main_planet"]
    solar = planet.building(p, eco.SOLAR_PLANT)
    solar.update(building_level=1, building_position_index=5)
    planet.start_building_event(empire, state, solar["building_id"], planet.EVENT_TEARDOWN, None, T0)
    planet.tick(empire, p["building_events"][0]["finish_time"])
    assert solar["building_level"] == 0 and solar["building_position_index"] == 0


def research_id(state, rtype):
    return next(r["research_id"] for r in state["empire"]["researches"] if r["research_type"] == rtype)


def rich_player_with_lab():
    state = new_player()
    p = state["empire"]["main_planet"]
    p.update(metal=100_000, crystal=100_000, deuterium=100_000)
    lab = planet.building(p, eco.RESEARCH_LAB)
    lab.update(building_level=1, building_position_index=4)
    return state


def test_research_needs_a_lab():
    state = new_player()
    state["empire"]["main_planet"].update(crystal=10_000, deuterium=10_000)
    with pytest.raises(planet.GameError, match="Requirements"):
        planet.start_research(state["empire"], state, research_id(state, eco.ENERGY_TECH), T0)


def test_research_finishes_and_raises_level():
    state = rich_player_with_lab()
    empire = state["empire"]
    planet.start_research(empire, state, research_id(state, eco.ENERGY_TECH), T0)
    event = empire["research_upgrading_events"][0]
    assert event["total_time"] == eco.research_seconds(0, 800, 1)
    planet.tick(empire, event["finish_time"])
    assert planet.research_level(empire, eco.ENERGY_TECH) == 1
    assert empire["research_upgrading_events"] == []


def test_lab_and_research_block_each_other():
    state = rich_player_with_lab()
    empire = state["empire"]
    lab_id = planet.building(empire["main_planet"], eco.RESEARCH_LAB)["building_id"]
    planet.start_research(empire, state, research_id(state, eco.ENERGY_TECH), T0)
    with pytest.raises(planet.GameError, match="research lab"):
        planet.start_building_event(empire, state, lab_id, planet.EVENT_UPGRADE, None, T0)
    with pytest.raises(planet.GameError, match="Another research"):
        planet.start_research(empire, state, research_id(state, eco.COMPUTER_TECH), T0)


def test_cancel_research_refunds():
    state = rich_player_with_lab()
    empire = state["empire"]
    p = empire["main_planet"]
    planet.start_research(empire, state, research_id(state, eco.ENERGY_TECH), T0)
    assert p["crystal"] == 100_000 - 800
    planet.cancel_research(empire, empire["research_upgrading_events"][0]["event_id"], T0)
    assert (p["crystal"], p["deuterium"]) == (100_000, 100_000)


def test_lab_list_hides_types_handled_elsewhere():
    view = planet.researches_view(new_player()["empire"])
    types = {r["research_type"] for r in view["researches"]}
    assert not types & set(eco.NOT_IN_LAB)
    assert eco.ENERGY_TECH in types


def fleet_id(state, ftype):
    return planet.fleet(state["empire"]["main_planet"], ftype)["fleet_id"]


def player_with_shipyard():
    state = new_player()
    p = state["empire"]["main_planet"]
    p.update(metal=1_000_000, crystal=1_000_000, deuterium=1_000_000)
    planet.building(p, eco.SHIPYARD).update(building_level=2, building_position_index=6)
    planet.building(p, eco.ROBOTICS_FACTORY).update(building_level=2, building_position_index=7)
    return state


def test_shipyard_delivers_units_one_by_one():
    state = player_with_shipyard()
    empire = state["empire"]
    p = empire["main_planet"]
    planet.start_fleet_order(empire, state, fleet_id(state, 15), 5, T0)  # rocket launchers
    assert p["metal"] == 1_000_000 - 5 * 2000
    order = p["fleet_building_events"][0]
    unit = order["unit_seconds"]
    planet.tick(empire, T0 + 2 * unit + 1)
    assert planet.fleet(p, 15)["amount"] == 2
    assert planet.fleet_event_view(order)["building_count"] == 3
    planet.tick(empire, T0 + 5 * unit)
    assert planet.fleet(p, 15)["amount"] == 5 and p["fleet_building_events"] == []


def test_shipyard_queue_and_cancel_refunds_unbuilt_units():
    state = player_with_shipyard()
    empire = state["empire"]
    p = empire["main_planet"]
    planet.start_fleet_order(empire, state, fleet_id(state, 15), 4, T0)
    planet.start_fleet_order(empire, state, fleet_id(state, eco.SOLAR_SATELLITE), 2, T0)
    first, second = p["fleet_building_events"]
    assert second["start_time"] == first["finish_time"]

    unit = first["unit_seconds"]
    planet.cancel_fleet_order(empire, first["event_id"], T0 + unit)  # one launcher already built
    assert planet.fleet(p, 15)["amount"] == 1
    assert p["metal"] == 1_000_000 - 2000  # three unbuilt launchers refunded
    assert p["fleet_building_events"][0]["start_time"] == int(T0 + unit)


def test_solar_satellites_produce_energy():
    state = player_with_shipyard()
    p = state["empire"]["main_planet"]
    before = planet.production(p, state["empire"])["energy_max"]
    planet.fleet(p, eco.SOLAR_SATELLITE)["amount"] = 4
    each = eco.satellite_energy(planet.temperatures(p)[1])
    assert planet.production(p, state["empire"])["energy_max"] == before + 4 * each


def test_planets_differ_by_position():
    assert [eco.satellite_energy(t) for t in (300, 100, 0, -200)] == [40, 40, 23, 1]  # wiki: 1-40
    assert eco.gas_factor(-30) > eco.gas_factor(100)  # cold planets yield more gas
    assert eco.temperatures(1) == (98, 138) and eco.temperatures(15) == (-70, -30)
    sizes = [eco.COLONY_FIELDS[pos] for pos in range(1, 16)]
    assert min(low for low, _ in sizes) >= 60 and max(high for _, high in sizes) <= 289  # wiki: 60s to 280s
    assert max(range(1, 16), key=lambda pos: sum(eco.COLONY_FIELDS[pos])) in range(3, 8)
    state = new_player()
    p = state["empire"]["main_planet"]
    assert planet.fields(p)[1] == eco.HOME_FIELDS == 178
    p.update(max_fields=90, extra_fields=5, temp_offset=-10)
    assert planet.fields(p)[1] == 95
    assert planet.temperatures(p) == tuple(t - 10 for t in eco.temperatures(p["planet_position"]))
    # wiki gas mine level 2: 175 / h on a planet of about 40 degrees
    assert abs(eco.mine_output(eco.DEUTERIUM_MINE, 2) * eco.gas_factor(40) - 175) < 2
    # wiki metal mine: 350 at level 2, 3,172 at level 10 (with the base production)
    assert round(eco.mine_output(eco.METAL_MINE, 10) + eco.BASE_PRODUCTION["metal"]) == 3172


def test_shipyard_rules():
    state = player_with_shipyard()
    empire = state["empire"]
    with pytest.raises(planet.GameError, match="Requirements"):
        planet.start_fleet_order(empire, state, fleet_id(state, eco.LIGHT_FIGHTER), 1, T0)  # needs combustion
    with pytest.raises(planet.GameError, match="cannot be built"):
        planet.start_fleet_order(empire, state, fleet_id(state, 24), 1, T0)  # behemoth
    research = next(r for r in empire["researches"] if r["research_type"] == eco.SHIELDING_TECH)
    research["research_level"] = 2
    planet.start_fleet_order(empire, state, fleet_id(state, eco.SMALL_SHIELD_DOME), 1, T0)
    with pytest.raises(planet.GameError, match="Only one"):
        planet.start_fleet_order(empire, state, fleet_id(state, eco.SMALL_SHIELD_DOME), 1, T0)
    shipyard = planet.building(empire["main_planet"], eco.SHIPYARD)["building_id"]
    with pytest.raises(planet.GameError, match="shipyard is working"):
        planet.start_building_event(empire, state, shipyard, planet.EVENT_UPGRADE, None, T0)
