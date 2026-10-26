"""VIP: levels from points, benefits while activated, the store and the client's views."""

import pytest
from fastapi.testclient import TestClient

from supernova import db, fleets, items, npc, player, vip
from supernova import economy as eco
from supernova import planet as planet_rules
from supernova.app import app

from .test_fleets import new_player_with_cargo, position
from .test_planet import T0, building_id, new_player
from .test_server import decrypt_json

DAY = vip.DAY


def test_six_levels_grow_to_the_originals_level_8():
    assert [vip.level(p) for p in (0, 99, 100, 499, 500, 49_999, 50_000, 10**9)] == [0, 0, 1, 1, 2, 5, 6, 6]
    for name, _, values in vip.BENEFITS.values():
        assert values == sorted(values), name  # never less at a higher level
    assert vip.BENEFITS["production"][2] == [5, 12, 19, 26, 33, 40]  # wiki: 5 % ... 40 % at level 8
    assert vip.BENEFITS["build"][2][-1] == 20 and vip.BENEFITS["attack"][2][-1] == 20
    assert vip.BENEFITS["build_queue"][2][-1] == 2 and vip.BENEFITS["research_queue"][2][-1] == 2


def test_benefits_only_while_activated():
    state = new_player()
    vip.add_points(state, 1500)  # level 3
    assert vip.benefit(state, "production", T0) == 0
    vip.activate(state, 1, T0)
    assert vip.benefit(state, "production", T0) == 19 and vip.benefit(state, "shield", T0) == 5
    assert vip.benefit(state, "production", T0 + DAY + 1) == 0
    view = vip.info_view(state, T0)
    assert view["vip_level"] == 3 and view["vip_left"] == DAY
    assert view["vip_curr_lvl_score"] == 1500 and view["vip_next_lvl_score"] == 5000


def test_daily_login_points_grow_with_days_in_a_row():
    state = new_player()
    assert vip.daily_login(state, T0) == vip.LOGIN_POINTS
    assert vip.daily_login(state, T0 + 60) == 0  # once a day
    assert vip.daily_login(state, T0 + DAY) == vip.LOGIN_POINTS + vip.LOGIN_STREAK_POINTS
    assert vip.daily_login(state, T0 + 3 * DAY) == vip.LOGIN_POINTS  # a day missed
    assert vip.info_view(state, T0 + 3 * DAY)["tomorrow_score"] == vip.LOGIN_POINTS + vip.LOGIN_STREAK_POINTS


def test_vip_speeds_up_constructions_and_adds_a_queue():
    state = new_player()
    empire = state["empire"]
    p = empire["main_planet"]
    p.update(metal=10**7, crystal=10**7, deuterium=10**7)
    planet_rules.tick(empire, T0)
    slow = planet_rules.building_view(p, planet_rules.building(p, eco.METAL_MINE))["up_time"]
    vip.add_points(state, 50_000)
    vip.activate(state, 30, T0)
    planet_rules.tick(empire, T0)
    fast = planet_rules.building_view(p, planet_rules.building(p, eco.METAL_MINE))["up_time"]
    assert fast == max(1, int(slow * 80 / 100))
    planet_rules.start_building_event(empire, state, building_id(state, eco.METAL_MINE), 1, None, T0)
    planet_rules.start_building_event(empire, state, building_id(state, eco.CRYSTAL_MINE), 1, None, T0)
    planet_rules.start_building_event(empire, state, building_id(state, eco.DEUTERIUM_MINE), 1, None, T0)
    with pytest.raises(planet_rules.GameError, match="already under construction"):
        planet_rules.start_building_event(empire, state, building_id(state, eco.SOLAR_PLANT), 1, None, T0)


def test_store_items_work_at_once_and_detectors_add_explorations():
    state = new_player()
    state["empire"]["dark_matter_count"] = 1000
    points = next(i for i in items.CATALOG if i.category == items.VIP_POINTS and i.vip_points == 500)
    days = next(i for i in items.CATALOG if i.category == items.VIP_TIME and i.vip_days == 7)
    items.buy(state, points.id, 1)
    items.buy(state, days.id, 1)
    assert vip.level(vip.data(state)["score"]) == 2 and not items.amount(state, points.id)  # at once
    assert vip.active_level(state) == 0 and items.amount(state, days.id) == 1  # used from the capsule
    assert [v["id"] for v in items.depot_view(state, items.OTHER)] == [days.id]
    items.use(state, days.id, 1, state["empire"]["main_planet"])
    assert vip.active_level(state) == 2
    assert {v["id"] for v in items.vip_shop(items.VIP_POINTS)} >= {points.id}
    assert all(v["id"] not in {points.id, days.id} for v in items.shop(items.FEATURED) + items.shop(items.OTHER))
    state["empire"]["dark_matter_count"] = 100
    items.buy(state, items.GAMMA_DETECTOR_S, 1)
    items.use(state, items.GAMMA_DETECTOR_S, 1, state["empire"]["main_planet"], T0)
    assert state["empire"]["secure_explorations"] == 10


def test_expeditions_past_the_secure_ones_need_a_detector():
    from .test_npc import NOW, astrophysics, send
    pid = new_player_with_cargo(5)
    astrophysics(pid, 4)  # 48 safe explorations, two fleets at once
    galaxy, system, _ = position(pid)
    for _ in range(48):
        npc.record_expedition(pid, NOW)
    with pytest.raises(planet_rules.GameError, match="Gamma Detector"):
        send(pid, {9: 1}, (galaxy, system, 16), fleets.EXPEDITION)
    with db.player_state(pid) as state:
        state["empire"]["secure_explorations"] = 1
    send(pid, {9: 1}, (galaxy, system, 16), fleets.EXPEDITION)  # the detector's
    assert db.load_state(pid)["empire"]["secure_explorations"] == 0
    with db.player_state(pid) as state:  # VIP 2 adds five a day
        vip.add_points(state, 500)
        vip.activate(state, 1, NOW)
    assert fleets.secure_explorations(db.load_state(pid)["empire"], NOW) == 48 + 5


def test_client_views():
    c = TestClient(app, headers={"openudid": "vip-user"})
    me = decrypt_json(c.post("/world/1/trials.json", content="username="))
    assert me["vip_info"]["vip_score"] == vip.LOGIN_POINTS  # the first login of the day
    uid = me["user_id"]
    info = decrypt_json(c.post(f"/world/1/users/{uid}/vip_info"))["vip_info"]
    assert info["vip_level"] == 0 and info["vip_next_lvl_score"] == 100 and info["vip_detail"]
    effect = decrypt_json(c.post(f"/world/1/users/{uid}/vip_effect"))["effect"]
    assert [e[1]["need_score"] for e in effect] == vip.NEED_SCORE
    assert {"name": "Resource production", "value": "+40%"} in effect[-1][2]
    shop = decrypt_json(c.post("/world/1/game_items/vip_item", content="type=add_time"))["items"]
    assert [i["sale_price"] for i in shop] == [10, 60, 200]
    assert player.HIDDEN_EMPIRE_FIELDS.count("vip") == 1 and "vip" not in me["empire"]
