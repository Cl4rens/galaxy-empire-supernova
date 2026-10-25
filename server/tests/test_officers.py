"""Officers, credit items, terraformers and quick buy."""

import random
import time

from supernova import combat, db, items, officers
from supernova import economy as eco
from supernova import planet as planet_rules

from .test_acs import member
from .test_server import decrypt_json


def officer_item(kind, lv, days):
    return next(i for i in items.CATALOG if i.officer == (kind, lv, days))


def test_hire_officers_and_their_effects():
    c, me = member("officer-boss", {})
    uid = me["user_id"]
    with db.player_state(uid) as s:
        s["empire"]["dark_matter_count"] = 500
        s["empire"]["gu_count"] = 1000
    tab = decrypt_json(c.get("/world/1/game_items?category=3"))["items"]
    assert len(tab) == len(officers.PRICES) and {i["pay_type"] for i in tab} == {1, 2}

    with db.player_state(uid) as s:
        planet = s["empire"]["main_planet"]
        planet_rules.building(planet, eco.METAL_MINE)["building_level"] = 10
        planet_rules.building(planet, eco.SOLAR_PLANT)["building_level"] = 15
        planet_rules.tick(s["empire"])
        before = planet_rules.production(planet, s["empire"])["metal"]
        mine = planet_rules.building_view(planet, planet_rules.building(planet, eco.METAL_MINE))["up_time"]
    geologist = officer_item(officers.GEOLOGIST, 1, 7)
    commander = officer_item(officers.COMMANDER, 2, 7)  # credits
    for i in (geologist, commander):
        decrypt_json(c.post(f"/world/1/users/{uid}/buy_game_item", content=f"game_item_id={i.id}&number=1"))
        decrypt_json(c.post(f"/world/1/users/{uid}/use_game_item", content=f"game_item_id={i.id}&number=1"))
    s = db.load_state(uid)
    assert s["empire"]["gu_count"] == 1000 - commander.price
    planet = s["empire"]["main_planet"]
    after = planet_rules.production(planet, s["empire"])["metal"]
    assert after > before
    assert planet_rules.building_view(planet, planet_rules.building(planet, eco.METAL_MINE))["up_time"] == int(mine * 0.9)

    buffs = decrypt_json(c.get("/world/1/users/my_buffs_ext"))["buff_list"]
    assert {b["buffer_name"] for b in buffs} == {"Geologist Lv.1", "Commander Lv.2"}
    # a weaker one cannot replace a stronger one; the same level adds time
    with db.player_state(uid) as s:
        empire = s["empire"]
        until = empire["officers"][officers.COMMANDER]["until"]
        try:
            officers.hire(empire, officers.COMMANDER, 1, 7, time.time())
            assert False
        except ValueError:
            pass
        officers.hire(empire, officers.COMMANDER, 2, 7, time.time())
        assert empire["officers"][officers.COMMANDER]["until"] == until + 7 * 86400
        assert officers.level(empire, officers.COMMANDER, until + 8 * 86400) == 0


def test_engineer_saves_defense():
    plain = combat.rebuild_defense({15: 1000}, random.Random(1))
    saved = combat.rebuild_defense({15: 1000}, random.Random(1), save=75)
    assert 650 < plain[15] < 750 and saved[15] > 900


def test_terraformer_and_quick_buy():
    c, me = member("terra-boss", {})
    uid = me["user_id"]
    with db.player_state(uid) as s:
        s["empire"]["gu_count"] = 1000
    used, fields = planet_rules.fields(db.load_state(uid)["empire"]["main_planet"])
    shortcut = decrypt_json(c.get(f"/world/1/users/{uid}/game_items_shortcuts?shortcuts=4"))["items"]
    assert [i["id"] for i in shortcut] == [3201, 3202, 3203] and all(i["amount"] == 0 for i in shortcut)
    done = decrypt_json(c.post(f"/world/1/users/{uid}/buy_and_use_game_item", content="game_item_id=3203"))
    extra = db.load_state(uid)["empire"]["main_planet"]["extra_fields"]
    assert 1 <= extra <= 25 and done["planet"]["field_max"] == fields + extra
    assert done["empire"]["gu_count"] == 1000 - 599
    assert c.post(f"/world/1/users/{uid}/buy_and_use_game_item", content="game_item_id=3203").status_code == 400
    assert decrypt_json(c.get(f"/world/1/users/{uid}/game_items_shortcuts?shortcuts=11"))["items"]


def test_credit_speed_stones_need_credits():
    c, me = member("stone-buyer", {})
    uid = me["user_id"]
    assert "credits" in c.post(f"/world/1/users/{uid}/buy_game_item", content="game_item_id=2201&number=1").text
    stones = decrypt_json(c.get("/world/1/game_items?category=2"))["items"]
    assert {i["pay_type"] for i in stones} == {1, 2}
