"""Electronic counter measures and system lords."""

import random

import pytest
from fastapi.testclient import TestClient

from supernova import db, ecm, fleets, lordship, universe
from supernova import planet as planet_rules
from supernova.app import app

from .test_fleets import new_player_with_cargo, position
from .test_server import decrypt_json


def test_ecm_fortune_guarantees_success():
    state = {"empire": {"dark_matter_count": 0}}
    ecm.add_chips(state, 1000)
    always_fail = random.Random()
    always_fail.random = lambda: 0.999
    tries = 0
    while ecm.data(state)["level"] == 0:
        ecm.level_up(state, False, always_fail)
        tries += 1
    assert tries == ecm.full_fortune(0) + 1  # every failure adds fortune until it is full
    assert ecm.percent(state) == 2 and ecm.data(state)["fortune"] == 0
    with pytest.raises(ValueError, match="chips"):
        ecm.data(state)["chips"] = 0
        ecm.level_up(state, True, always_fail)  # no dark matter to buy chips


def test_ecm_over_api_and_combat_bonus():
    c = TestClient(app, headers={"openudid": "ecm-user"})
    me = decrypt_json(c.post("/world/1/trials.json", content="username="))
    with db.player_state(me["user_id"]) as s:
        s["empire"]["dark_matter_count"] = 100
    info = decrypt_json(c.get("/world/1/electronic_warfare/info.json"))
    assert info["curr_lvl"] == 0 and info["can_auto_up"] and info["curr_effect"]["chip_cost"] == 1
    after = decrypt_json(c.post("/world/1/electronic_warfare/level_up", content="auto_buy=1"))
    assert after["curr_lvl"] == 1 or after["curr_bless"] > 0
    with db.player_state(me["user_id"]) as s:
        ecm.data(s)["level"] = 5
        side = fleets._side("x", s["empire"], {1: 1}, s["empire"]["main_planet"], s)
    assert side.attack_percent >= 10 and side.shield_percent >= 10


def test_system_lord():
    strong = new_player_with_cargo(0)
    with db.player_state(strong) as s:
        planet_rules.fleet(s["empire"]["main_planet"], 4)["amount"] = 100  # 6,000 points
    lordship.refresh(force=True)
    g, sy, _ = position(strong)
    lord = lordship.lord_of(g, sy)
    assert lord[0] == strong and lord[1] == lordship.rank_for(6000) == 3
    assert f"{g}:{sy}" in lordship.owned_systems(strong)
    assert universe.system_view(g, sy)["lord_info"]["lord_level"] == 3
    state = db.load_state(strong)
    bonus = lordship.bonus(state["empire"]["main_planet"], strong)
    assert bonus["increase_metal"] == 6 and bonus["increase_attack"] == 3
    with db.player_state(strong) as s:  # keep the other tests' rankings simple
        planet_rules.fleet(s["empire"]["main_planet"], 4)["amount"] = 0
    lordship.refresh(force=True)
