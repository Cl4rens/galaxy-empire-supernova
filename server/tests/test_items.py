"""Item shop, depot and speed-ups."""

import time

from supernova import db, ecm, items
from supernova import economy as eco
from supernova import planet as planet_rules

from .test_acs import member
from .test_server import decrypt_json


def rich(udid, dark_matter=500):
    c, me = member(udid, {})
    with db.player_state(me["user_id"]) as s:
        s["empire"]["dark_matter_count"] = dark_matter
    return c, me


def test_buy_and_use_resources_and_chips():
    c, me = rich("shop-buyer")
    uid = me["user_id"]
    shop = decrypt_json(c.get("/world/1/game_items?category=1"))
    assert shop["dark_matter_count"] == 500 and {i["code"] for i in shop["items"]} >= {"1101"}
    featured = decrypt_json(c.get("/world/1/game_items?category=0"))["items"]
    assert featured and all(i["is_featured"] for i in featured)

    bought = decrypt_json(c.post(f"/world/1/users/{uid}/buy_game_item", content="game_item_id=1102&number=2"))
    assert bought["darkmatter"] == 500 - 2 * items.item(1102).price
    assert c.post(f"/world/1/users/{uid}/buy_game_item", content="game_item_id=2105&number=99").status_code == 400

    depot = decrypt_json(c.get(f"/world/1/users/{uid}/game_items?category=1"))["items"]
    assert [(i["id"], i["amount"], i["can_be_use"]) for i in depot] == [(1102, 2, True)]
    metal = db.load_state(uid)["empire"]["main_planet"]["metal"]
    used = decrypt_json(c.post(f"/world/1/users/{uid}/use_game_item", content="game_item_id=1102&number=2"))
    assert used["categories"][0]["amount"] == 0 and used["planet"]["metal"] >= metal + 200_000
    assert decrypt_json(c.get(f"/world/1/users/{uid}/game_items?category=1"))["items"] == []

    decrypt_json(c.post(f"/world/1/users/{uid}/buy_game_item", content="game_item_id=3101&number=1"))
    decrypt_json(c.post(f"/world/1/users/{uid}/use_game_item", content="game_item_id=3101&number=1"))
    assert ecm.data(db.load_state(uid))["chips"] == 5
    decrypt_json(c.post(f"/world/1/users/{uid}/buy_game_item", content="game_item_id=1101&number=3"))
    decrypt_json(c.post(f"/world/1/users/{uid}/del_game_item", content="game_item_id=1101"))
    assert items.amount(db.load_state(uid), 1101) == 0


def _start_mine(uid: int, now: float) -> int:
    with db.player_state(uid) as s:
        empire = s["empire"]
        planet = empire["main_planet"]
        planet.update(metal=1e9, crystal=1e9, deuterium=1e9)
        mine = planet_rules.building(planet, eco.METAL_MINE)
        planet_rules.start_building_event(empire, s, mine["building_id"], 1, None, now)
        return planet["building_events"][0]["event_id"]


def test_speed_up_building_with_item_and_dark_matter():
    c, me = rich("speed-builder", dark_matter=10_000)
    uid = me["user_id"]
    for _ in range(12):  # make the next mine level take a while
        with db.player_state(uid) as s:
            planet_rules.building(s["empire"]["main_planet"], eco.METAL_MINE)["building_level"] += 1
    event_id = _start_mine(uid, time.time())
    info = decrypt_json(c.get(f"/world/1/building_events/{event_id}/speedup_info.json"))
    left = info["left_time"]
    assert left > 600 and info["dark_matter_count"] == items.instant_price(left)
    assert {i["game_item_id"] for i in info["items"]} == {i.id for i in items.CATALOG if i.category == items.SPEEDUP and i.seconds}

    assert c.post(f"/world/1/building_events/{event_id}/speedup.json", content="game_item_id=2101").status_code == 400
    decrypt_json(c.post(f"/world/1/building_events/{event_id}/buy_and_use_stone.json", content="game_item_id=2101"))
    event = db.load_state(uid)["empire"]["main_planet"]["building_events"][0]
    assert event["finish_time"] - time.time() <= left - 290

    level = planet_rules.level(db.load_state(uid)["empire"]["main_planet"], eco.METAL_MINE)
    done = decrypt_json(c.post(f"/world/1/building_events/{event_id}/speedup.json", content="game_item_id=0"))
    planet = db.load_state(uid)["empire"]["main_planet"]
    assert planet["building_events"] == [] and planet_rules.level(planet, eco.METAL_MINE) == level + 1
    assert done["empire"]["dark_matter_count"] < 10_000 - items.item(2101).price
    assert c.get(f"/world/1/building_events/{event_id}/speedup_info.json").status_code == 400


def test_speed_up_shipyard_delivers_ships():
    c, me = rich("speed-yard", dark_matter=10_000)
    uid = me["user_id"]
    now = time.time()
    with db.player_state(uid) as s:
        empire = s["empire"]
        planet = empire["main_planet"]
        planet.update(metal=1e9, crystal=1e9, deuterium=1e9)
        planet_rules.building(planet, eco.SHIPYARD).update(building_level=4, building_position_index=6)
        planet_rules.start_fleet_order(empire, s, planet_rules.fleet(planet, 15)["fleet_id"], 20, now)
        event_id = planet["fleet_building_events"][0]["event_id"]
    decrypt_json(c.post(f"/world/1/fleet_building_events/{event_id}/speedup.json", content="game_item_id=0"))
    planet = db.load_state(uid)["empire"]["main_planet"]
    assert planet["fleet_building_events"] == [] and planet_rules.fleet(planet, 15)["amount"] == 20
