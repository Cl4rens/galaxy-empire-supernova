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
    assert p["metal"] == pytest.approx(500 + 30 * eco.config.SPEED)
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
    assert without_energy == pytest.approx(30 * eco.config.SPEED)
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
