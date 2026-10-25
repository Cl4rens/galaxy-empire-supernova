"""Pioneer Lab, Iron Behemoth, alloy fragments, dark energy and pioneer techs."""

import pytest
from fastapi.testclient import TestClient

from supernova import combat, db, fleets, flagships, mails, player
from supernova import economy as eco
from supernova import planet as planet_rules
from supernova.app import app

from .test_fleets import new_player_with_cargo, position
from .test_server import decrypt_json


def lab_owner(udid):
    c = TestClient(app, headers={"openudid": udid})
    me = decrypt_json(c.post("/world/1/trials.json", content="username="))
    with db.player_state(me["user_id"]) as s:
        moon = player.add_moon(me["user_id"], s["empire"]["planets"][0])
        planet_rules.building(moon, eco.MOON_BASE)["building_level"] = 1
        planet_rules.building(moon, eco.PIONEER_LAB)["building_level"] = 1
        s["empire"]["dark_matter_count"] = 1000
        flagships.data(s)["alloy"] = 600
    return c, me, moon["planet_id"]


def test_build_behemoths():
    c, me, moon_id = lab_owner("pioneer")
    info = decrypt_json(c.post("/world/1/flagships/show_info.json", content=f"planet_id={moon_id}"))
    assert info["alloy_fragment_count"] == 600 and info["behemoth_limit"] == 1 and info["total_behemoth_num"] == 0
    plan = decrypt_json(c.post("/world/1/flagships/building_info.json", content=f"planet_id={moon_id}"))
    assert plan["enable_building_num"] == 2 and plan["dark_matter_cost_next"] == 50

    fid = 0
    for f in decrypt_json(c.get(f"/world/1/planets/{moon_id}.json"))["fleets"]:
        if f["fleet_type"] == flagships.BEHEMOTH:
            fid = f["fleet_id"]
    ask = decrypt_json(c.post("/world/1/flagships.json", content=f"planet_id={moon_id}&fleet_count=2&fleet_id={fid}&sure=0"))
    assert "500 alloy" in ask["message"]
    started = decrypt_json(c.post("/world/1/flagships.json",
                                  content=f"planet_id={moon_id}&fleet_count=2&fleet_id={fid}&sure=1"))
    assert started["finish_time"] > started["total_time"]
    with db.player_state(me["user_id"]) as s:
        assert flagships.data(s)["alloy"] == 100 and s["empire"]["dark_matter_count"] == 1000 - 50 - 100
        planet_rules.tick(s["empire"], started["finish_time"] + 1)
        assert flagships.total_behemoths(s["empire"]) == 2

    box = decrypt_json(c.post("/world/1/flagships/buy_flagship_box"))
    assert box["alloy_fragment_count"] == 350 and box["alloy_fragment_box_price"] == 30


def test_only_one_pioneer_lab():
    c, me, moon_id = lab_owner("one-lab")
    with db.player_state(me["user_id"]) as s:
        p = s["empire"]["planets"][0]  # a planet cannot have one anyway; the point is the clear refusal
        with pytest.raises(planet_rules.GameError):
            planet_rules.start_building_event(s["empire"], s, planet_rules.building(p, eco.PIONEER_LAB)["building_id"],
                                              planet_rules.EVENT_UPGRADE, 9, 2_000_000_000.0)


def test_behemoths_raise_attack_and_gather_dark_energy():
    attacker, defender = new_player_with_cargo(0), new_player_with_cargo(0)
    with db.player_state(attacker) as s:
        p = s["empire"]["main_planet"]
        planet_rules.fleet(p, flagships.BEHEMOTH)["amount"] = 3
        planet_rules.fleet(p, 4)["amount"] = 20
        side = fleets._side("a", s["empire"], {flagships.BEHEMOTH: 3, 4: 20}, p, s)
        assert side.attack_percent == flagships.ATTACK_PER_BEHEMOTH  # limit 1 without Dynamic Power Tech
        ids = f"{planet_rules.fleet(p, flagships.BEHEMOTH)['fleet_id']},{planet_rules.fleet(p, 4)['fleet_id']},"
        with pytest.raises(fleets.FleetError, match="at most 1"):
            fleets.send(attacker, s, {"fleet_ids": ids, "fleet_cnt_ids": "3,20,", "speed_ratio": "100",
                                      "end_p_pos": ",".join(map(str, position(defender))),
                                      "mission": str(fleets.ATTACK)}, 2_000_000_000.0)
        fleets.send(attacker, s, {"fleet_ids": ids, "fleet_cnt_ids": "1,20,", "speed_ratio": "100",
                                  "end_p_pos": ",".join(map(str, position(defender))),
                                  "mission": str(fleets.ATTACK)}, 2_000_000_000.0)
    with db.player_state(defender) as s:
        planet_rules.fleet(s["empire"]["main_planet"], 2)["amount"] = 300  # heavy fighters: debris
    fleets.process_due(fleets.events_view(attacker)[0]["finish_time"])
    state = db.load_state(attacker)
    assert flagships.data(state)["dark_energy"] > 0 and flagships.data(state)["alloy"] > 0
    assert "Dark energy" in mails.undelivered(attacker)[-1]["body"]["notify_message"]


def test_pioneer_research():
    c, me, moon_id = lab_owner("pioneer-tech")
    techs = decrypt_json(c.get("/world/1/flagship_researches"))["flagship_research"]
    dynamic = next(t for t in techs if t["research_type"] == flagships.DYNAMIC_POWER)
    poor = c.post("/world/1/flagship_researches/upgrade.json", content=f"research_id={dynamic['research_id']}")
    assert poor.status_code == 400 and "dark energy" in poor.text
    with db.player_state(me["user_id"]) as s:
        flagships.data(s)["dark_energy"] = 1000
    started = decrypt_json(c.post("/world/1/flagship_researches/upgrade.json",
                                  content=f"research_id={dynamic['research_id']}"))
    assert started["dark_energy_count"] == 900
    with db.player_state(me["user_id"]) as s:
        assert flagships.behemoth_limit(s) == 1
        flagships.data(s)["research_event"]["finish_time"] = 1
        assert flagships.behemoth_limit(s) == 2


def test_behemoth_is_a_giant():
    assert combat.STATS[flagships.BEHEMOTH].hull == 350_000
