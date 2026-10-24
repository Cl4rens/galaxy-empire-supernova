"""Kryptons: refining, storage, planet slots, upgrades, effects, research and shop."""

import random

from fastapi.testclient import TestClient

from supernova import combat, db, kryptons
from supernova import economy as eco
from supernova import planet as planet_rules
from supernova.app import app

from .test_server import decrypt_json


def lab_player(udid, stones=50_000, dark_matter=1_000):
    c = TestClient(app, headers={"openudid": udid})
    me = decrypt_json(c.post("/world/1/trials.json", content="username="))
    with db.player_state(me["user_id"]) as s:
        planet_rules.building(s["empire"]["main_planet"], eco.KRYPTON_LAB)["building_level"] = 1
        kryptons.add_stones(s, stones)
        s["empire"]["dark_matter_count"] = dark_matter
    return c, me


def test_refining_needs_a_lab_and_stones():
    c = TestClient(app, headers={"openudid": "no-lab"})
    decrypt_json(c.post("/world/1/trials.json", content="username="))
    assert c.post("/world/1/alchemy/fuse_krypton", content="times=1&auto_d=0").status_code == 400
    c, me = lab_player("poor-refiner", stones=0)
    no_stones = c.post("/world/1/alchemy/fuse_krypton", content="times=1&auto_d=0")
    assert no_stones.status_code == 400 and "stones" in no_stones.text


def test_refine_collect_set_upgrade_and_break_down():
    c, me = lab_player("refiner")
    refined = decrypt_json(c.post("/world/1/alchemy/fuse_krypton", content="times=5&auto_d=0"))
    assert len(refined["data"]["array"]) == 5 and refined["account"][0]["number"] < 50_000
    tray = decrypt_json(c.get(f"/world/1/alchemy?user_id={me['user_id']}"))["crypton"]
    assert len(tray) == 5
    first = tray[0]
    decrypt_json(c.get(f"/world/1/alchemy/move?buff={first['b']}&level={first['l']}&star={first['s']}"))
    energy = decrypt_json(c.get("/world/1/alchemy/destroy_crypton"))["energy"]
    assert energy > 0 and decrypt_json(c.get("/world/1/alchemy"))["crypton"] == []

    storage = decrypt_json(c.get("/world/1/kryptons"))
    item = storage["kryptons"]["package"][0]
    pid = me["empire"]["main_planet"]["planet_id"]
    decrypt_json(c.post(f"/world/1/kryptons/{item['id']}/inset_into_planet", content=f"id={item['id']}&planet_id={pid}"))
    s = db.load_state(me["user_id"])
    assert s["empire"]["main_planet"]["kryptons"][0]["krypton_type"] == item["type"]
    upgraded = decrypt_json(c.post(f"/world/1/kryptons/{item['id']}/upgrade", content=f"id={item['id']}"))
    assert upgraded["planet"]["kryptons"][0]["krypton_level"] == 2
    assert decrypt_json(c.get(f"/world/1/planets/{pid}/kryptons"))["hole_count"] == 1

    taken = decrypt_json(c.post(f"/world/1/kryptons/{item['id']}/outset_from_planet", content="show_info=1"))
    assert taken["price"] == -1 and taken["planet"]["kryptons"] == []
    asked = decrypt_json(c.post(f"/world/1/kryptons/{item['id']}/conversion", content="show_info=1"))
    done = decrypt_json(c.post(f"/world/1/kryptons/{item['id']}/conversion", content="show_info=0"))
    assert done["energy"] == asked["energy_info"]
    assert decrypt_json(c.get("/world/1/kryptons"))["kryptons"]["package"] == []


def test_unlocking_slots_and_storage():
    c, me = lab_player("unlocker")
    pid = me["empire"]["main_planet"]["planet_id"]
    ask = decrypt_json(c.post(f"/world/1/kryptons/{pid}/unlock_planet_hole", content="unlock_index=2&show_info=1"))
    assert ask["show_info"] and "3,000" in ask["show_message"]  # 1,000 + 2,000
    decrypt_json(c.post(f"/world/1/kryptons/{pid}/unlock_planet_hole", content="unlock_index=2&show_info=0"))
    assert decrypt_json(c.get("/world/1/kryptons"))["planet_hole"][0]["hole_count"] == 3
    assert decrypt_json(c.post("/world/1/kryptons/unlock_package", content="count=2&show_info=1"))["price"] == 600
    decrypt_json(c.post("/world/1/kryptons/unlock_package", content="count=2&show_info=0"))
    assert decrypt_json(c.get("/world/1/kryptons"))["package_count"] == kryptons.STORAGE_START + 2


def test_effects_on_production_and_combat():
    state = {"empire": {"planets": [], "main_planet": None}}
    acc = kryptons.account(state)
    acc["items"] = [{"id": 1, "type": kryptons.METAL, "rank": 3, "level": 1, "planet_id": 7, "sort": 0},
                    {"id": 2, "type": kryptons.WEAPON, "rank": 6, "level": 10, "planet_id": 7, "sort": 1}]
    planet = {"planet_id": 7}
    state["empire"]["planets"] = [planet]
    kryptons.sync_planets(state)
    assert planet["krypton_bonus"] == {"metal": 6, "attack": 21}
    side = combat.Side("x", {1: 1}, weapons=2, attack_percent=21)
    assert abs(side.weapon_factor() - 1.41) < 1e-9


def test_research_costs_dark_matter_and_shop_sells_stones():
    c, me = lab_player("researcher", stones=0, dark_matter=100)
    started = decrypt_json(c.post("/world/1/krypton_researches/upgrade"))["research"]
    assert not started["finished"] and started["finish_time"] > 0
    assert c.post("/world/1/krypton_researches/upgrade").status_code == 400  # already running

    shop = decrypt_json(c.get(f"/world/1/crypton_sale?user_id={me['user_id']}"))
    assert shop["dk_balance"] == 80 and shop["data"][0]["exchange"]["base"] == 10
    bought = decrypt_json(c.get("/world/1/crypton_sale/exchange?type=stones_small&times=2"))
    assert bought["code"] == 0 and bought["stone_balance"] == 2000 and bought["dk_balance"] == 60
    assert decrypt_json(c.get("/world/1/crypton_sale/exchange?type=stones_large&times=2"))["code"] == 1  # no DM


def test_higher_stages_give_better_kryptons():
    rng = random.Random(1)
    low = sum(kryptons._roll_rank(1, 0, rng) for _ in range(500))
    high = sum(kryptons._roll_rank(5, 10, rng) for _ in range(500))
    assert high > low
