"""Resource settings, radar, quick finish, fleet acceleration, interceptor fusion, galaxium."""

import time

from supernova import db, fleets, items
from supernova import economy as eco
from supernova import planet as planet_rules

from .test_acs import member
from .test_fleets import position
from .test_server import decrypt_json


def test_mine_percent_and_radar():
    c, me = member("core-settings", {})
    uid = me["user_id"]
    pid = me["empire"]["main_planet"]["planet_id"]
    view = decrypt_json(c.post(f"/world/1/planets/{pid}/change_percent.json",
                               content="name=metal_mine_percent&percent=40"))
    assert view["metal_mine_percent"] == 40
    assert c.post(f"/world/1/planets/{pid}/change_percent.json", content="name=metal&percent=40").status_code == 400
    assert c.post(f"/world/1/planets/{pid}/change_percent.json",
                  content="name=metal_mine_percent&percent=45").status_code == 400

    with db.player_state(uid) as s:
        planet_rules.building(s["empire"]["main_planet"], eco.RADAR_CENTER).update(building_level=3,
                                                                                    building_position_index=9)
    on = decrypt_json(c.post(f"/world/1/planets/{pid}/toggle_radar.json", content="toggle_on=1"))
    assert on["is_radar_on"] is True and on["radar_energy"] == 30
    off = decrypt_json(c.post(f"/world/1/planets/{pid}/toggle_radar.json", content="toggle_on=0"))
    assert off["is_radar_on"] is False and off["radar_energy"] == 0 and off["energy_used"] == on["energy_used"] - 30


def test_quick_finish_only_in_the_last_minute():
    c, me = member("core-sprint", {})
    uid = me["user_id"]
    now = time.time()
    with db.player_state(uid) as s:
        empire = s["empire"]
        planet = empire["main_planet"]
        planet.update(metal=1e9, crystal=1e9, deuterium=1e9)
        mine = planet_rules.building(planet, eco.METAL_MINE)
        mine["building_level"] = 15
        planet_rules.start_building_event(empire, s, mine["building_id"], 1, None, now)
        event = planet["building_events"][0]
    assert c.post(f"/world/1/building_events/{event['event_id']}/sprint").status_code == 400
    with db.player_state(uid) as s:
        s["empire"]["main_planet"]["building_events"][0]["finish_time"] = int(time.time()) + 30
    done = decrypt_json(c.post(f"/world/1/building_events/{event['event_id']}/sprint"))
    assert next(b for b in done["update"]["buildings"] if b["building_type"] == eco.METAL_MINE)["building_level"] == 16


def test_accelerate_returning_fleet():
    c, me = member("core-acce", {9: 5})
    uid = me["user_id"]
    _, other = member("core-acce-target", {})
    now = time.time()
    with db.player_state(uid) as s:
        p = s["empire"]["main_planet"]
        fleets.send(uid, s, {"fleet_ids": f"{planet_rules.fleet(p, 9)['fleet_id']},", "fleet_cnt_ids": "5,",
                             "end_p_pos": ",".join(map(str, position(other["user_id"]))),
                             "mission": str(fleets.TRANSPORT), "speed_ratio": "100"}, now)
    flight = fleets.events_view(uid)[0]
    assert c.get(f"/world/1/fleet_hanging_events/{flight['event_id']}/acce_cost").status_code == 400  # outbound
    fleets.process_due(flight["finish_time"])
    cost = decrypt_json(c.get(f"/world/1/fleet_hanging_events/{flight['event_id']}/acce_cost"))
    assert cost["item_cost"] == 0 and cost["credit_cost"] >= 1
    assert "credits" in c.post(f"/world/1/fleet_hanging_events/{flight['event_id']}/acce").text
    with db.player_state(uid) as s:
        items.add(s, items.LIGHT_SPEED_ENGINE, 1)
    assert decrypt_json(c.get(f"/world/1/fleet_hanging_events/{flight['event_id']}/acce_cost")) == {
        "item_cost": 1, "credit_cost": 0}
    done = decrypt_json(c.post(f"/world/1/fleet_hanging_events/{flight['event_id']}/acce"))
    assert fleets.events_view(uid) == [] and done["empire"]["credits"] == 0
    assert planet_rules.fleet(db.load_state(uid)["empire"]["main_planet"], 9)["amount"] == 5


def test_interceptor_fusion():
    c, me = member("core-fusion", {})
    uid = me["user_id"]
    cost = decrypt_json(c.post(f"/world/1/users/{uid}/the_ghost_exchange_cost", content="number=2"))
    assert cost["item"]["code"] == "01401" and cost["cost"] == {"dark_matter": 0, "gu": 0}
    assert c.post(f"/world/1/users/{uid}/use_game_item",
                  content=f"game_item_id={items.GALAXIUM}&number=2&pay_type=1").status_code == 400
    with db.player_state(uid) as s:
        items.add(s, items.GALAXIUM, 400)
    done = decrypt_json(c.post(f"/world/1/users/{uid}/use_game_item",
                               content=f"game_item_id={items.GALAXIUM}&number=2&pay_type=1"))
    assert done["interceptor"] == 2 and done["energy_particle_amount"] == 100
    with db.player_state(uid) as s:
        planet_rules.fleet(s["empire"]["main_planet"], 23)["amount"] = 999
        assert items.fusion_cost(s, 3) == {"dark_matter": 2, "gu": 4}


def test_battles_give_galaxium():
    assert items.galaxium_for({4: 10}) == 30  # 10 battleships of 60,000 metal and crystal each
    assert items.galaxium_for({15: 100}) == 0  # defense gives none


def test_radar_lists_detected_ship_types():
    planet = {"buildings": [{"building_type": eco.RADAR_CENTER, "building_level": 5}]}
    assert planet_rules.radar_fleets(planet) == ["Light Fighter", "Espionage Probe", "Colony Ship", "Small Cargo"]
    planet["buildings"][0]["building_level"] = 14
    assert planet_rules.radar_fleets(planet)[-1] == "All other ships"
