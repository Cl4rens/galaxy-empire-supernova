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
