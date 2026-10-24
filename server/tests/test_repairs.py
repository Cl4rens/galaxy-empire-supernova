"""Ship repair: defenders keep half of their lost ships as damaged and repair them."""

from fastapi.testclient import TestClient

from supernova import db, fleets, mails, repairs
from supernova import planet as planet_rules
from supernova.app import app

from .test_fleets import new_player_with_cargo, position
from .test_server import decrypt_json


def attack(attacker, defender, ships):
    with db.player_state(attacker) as s:
        p = s["empire"]["main_planet"]
        for t, n in ships.items():
            planet_rules.fleet(p, t)["amount"] = n
        ids = ",".join(str(planet_rules.fleet(p, t)["fleet_id"]) for t in ships) + ","
        counts = ",".join(str(n) for n in ships.values()) + ","
        fleets.send(attacker, s, {"fleet_ids": ids, "fleet_cnt_ids": counts, "end_p_pos": ",".join(map(str, position(defender))),
                                  "mission": str(fleets.ATTACK), "speed_ratio": "100"}, 2_000_000_000.0)
    fleets.process_due(fleets.events_view(attacker)[0]["finish_time"])


def test_defender_keeps_half_of_lost_ships_as_damaged():
    attacker, defender = new_player_with_cargo(0), new_player_with_cargo(0)
    with db.player_state(defender) as s:
        planet_rules.fleet(s["empire"]["main_planet"], 1)["amount"] = 20  # light fighters
        planet_rules.fleet(s["empire"]["main_planet"], 15)["amount"] = 10  # rocket launchers: not repairable
    attack(attacker, defender, {4: 30})  # battleships win
    home = db.load_state(defender)["empire"]["planets"][0]
    assert home["repair"]["damaged"] == {"1": 10}
    report = mails.undelivered(defender)[0]["body"]
    assert report["defender_repair_sw"] == 1 and report["event_info"]["defender_recover_ratio"] == 0


def test_repair_cycle_over_api():
    c = TestClient(app, headers={"openudid": "repairer"})
    me = decrypt_json(c.post("/world/1/trials.json", content="username="))
    pid = me["empire"]["main_planet"]["planet_id"]
    with db.player_state(me["user_id"]) as s:
        p = s["empire"]["main_planet"]
        repairs.dock(p)["damaged"] = {"1": 6, "9": 2}
        p.update(metal=0, crystal=0)
    info = decrypt_json(c.get(f"/world/1/repair_events/{pid}"))
    assert [f["fleet_type"] for f in info["fleets_info"]] == [1, 9] and info["base_info"]["switch_repair"] == 1

    started = decrypt_json(c.post("/world/1/repair_events/", content=f"planet_id={pid}&fleet_type=1&fleet_count=4"))
    assert started["finish_time"] > 0 and started["damaged_fleets"][0]["fleet_count"] == 2
    assert c.post("/world/1/repair_events/", content=f"planet_id={pid}&fleet_type=1&fleet_count=1").status_code == 400
    assert c.post(f"/world/1/repair_events/handing_over?planet_id={pid}").status_code == 400  # not finished
    with db.player_state(me["user_id"]) as s:
        repairs.dock(s["empire"]["main_planet"])["finish_time"] = 1
    over = decrypt_json(c.post(f"/world/1/repair_events/handing_over?planet_id={pid}"))
    assert over["repair_fleets"] == [{"fleet_type": 1, "fleet_count": 4}]
    assert next(f for f in over["planet"]["fleets"] if f["fleet_type"] == 1)["amount"] == 4

    ask = decrypt_json(c.post("/world/1/repair_events/resolve", content=f"planet_id={pid}&fleet_type=9&fleet_count=2"))
    assert ask["resolve_value"] == {"metal": 1200, "crystal": 1200}
    decrypt_json(c.post("/world/1/repair_events/make_sure_for_resolve",
                        content=f"planet_id={pid}&fleet_type=9&fleet_count=2"))
    p = db.load_state(me["user_id"])["empire"]["planets"][0]
    assert p["metal"] >= 1200 and repairs.dock(p)["damaged"] == {"1": 2}

    off = decrypt_json(c.post("/world/1/repair_events/change_repair_switch", content=f"planet_id={pid}&switch=0"))
    assert off["sw_repair"] == 0
    assert c.post("/world/1/repair_events/repair_right_now", content=f"planet_id={pid}").status_code == 400
