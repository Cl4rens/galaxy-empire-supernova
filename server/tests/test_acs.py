"""Coordinated attacks: alliance members join an attack and fight as one side."""

import time

from fastapi.testclient import TestClient

from supernova import db, fleets, mails
from supernova import planet as planet_rules
from supernova.app import app

from .test_fleets import position
from .test_server import decrypt_json

NOW = 2_000_000_000.0


def member(udid, ships):
    c = TestClient(app, headers={"openudid": udid})
    me = decrypt_json(c.post("/world/1/trials.json", content="username="))
    with db.player_state(me["user_id"]) as s:
        p = s["empire"]["main_planet"]
        p.update(metal=100_000, crystal=100_000, deuterium=100_000)
        planet_rules.building(p, 12).update(building_level=1, building_position_index=5)
        for t, n in ships.items():
            planet_rules.fleet(p, t)["amount"] = n
    return c, me


def test_join_fight_and_share():
    boss_c, boss = member("acs-boss", {4: 10, 10: 5})
    helper_c, helper = member("acs-helper", {3: 20})
    stranger_c, _ = member("acs-stranger", {3: 5})
    _, victim = member("acs-victim", {15: 30})
    aid = decrypt_json(boss_c.post("/world/1/alliances.json", content="alliance_name=Strike&description="))["alliance_id"]
    decrypt_json(boss_c.post(f"/world/1/alliances/{aid}/auto_in.json", content="status=1"))
    decrypt_json(helper_c.post("/world/1/alliance_applications.json", content=f"alliance_id={aid}"))

    with db.player_state(boss["user_id"]) as s:
        p = s["empire"]["main_planet"]
        ids = f"{planet_rules.fleet(p, 4)['fleet_id']},{planet_rules.fleet(p, 10)['fleet_id']},"
        fleets.send(boss["user_id"], s, {"fleet_ids": ids, "fleet_cnt_ids": "10,5,", "speed_ratio": "10",
                                         "end_p_pos": ",".join(map(str, position(victim["user_id"]))),
                                         "mission": str(fleets.ATTACK)}, NOW)
    attack = fleets.events_view(boss["user_id"])[0]

    listed = decrypt_json(helper_c.get("/world/1/acses"))["info"]
    assert [a["id"] for a in listed] == [attack["event_id"]] and listed[0]["curr_lines"] == 1
    assert decrypt_json(stranger_c.get("/world/1/acses"))["info"] == []

    hp = helper["empire"]["main_planet"]["planet_id"]
    prepared = decrypt_json(helper_c.get(f"/world/1/acses/{attack['event_id']}/prepare_append?planet_id={hp}&fleets=3:20"))
    assert prepared["missions"] == [fleets.ACS_ATTACK]
    assert stranger_c.post(f"/world/1/acses/{attack['event_id']}/append", content="fleets=3:5").status_code == 400
    joined = decrypt_json(helper_c.post(f"/world/1/acses/{attack['event_id']}/append",
                                        content=f"planet_id={hp}&fleets=3:20"))
    assert joined["acs_info"]["curr_lines"] == 2
    flight = fleets.events_view(helper["user_id"])[0]
    assert flight["origin_mission"] == fleets.ACS_JOIN and flight["finish_time"] == attack["finish_time"]
    ships = decrypt_json(boss_c.get(f"/world/1/fleet_hanging_events/{flight['event_id']}"))["hanging_info"]
    assert ships == [{"name": "Cruiser", "count": 20}]

    fleets.process_due(attack["finish_time"])
    boss_report = mails.undelivered(boss["user_id"])[-1]["body"]["attack_result"]
    helper_report = mails.undelivered(helper["user_id"])[-1]["body"]["attack_result"]
    assert boss_report["round"] == helper_report["round"] and boss_report["attack_start_fleets_count"] == 35
    assert helper_report["attacker_name"] == helper["username"]  # the client finds its side by this name
    assert "Coordinated attack" in mails.undelivered(helper["user_id"])[-1]["body"]["notify_message"]
    back = fleets.events_view(helper["user_id"])
    assert back and back[0]["is_returning"]
    fleets.process_due(back[0]["finish_time"] + 1)
    fleets.process_due(fleets.events_view(boss["user_id"])[0]["finish_time"] + 1)
    cruisers = planet_rules.fleet(db.load_state(helper["user_id"])["empire"]["main_planet"], 3)["amount"]
    assert 0 < cruisers <= 20


def test_too_slow_and_call_off():
    boss_c, boss = member("acs-boss2", {4: 2})
    helper_c, helper = member("acs-helper2", {4: 2})
    _, victim = member("acs-victim2", {})
    aid = decrypt_json(boss_c.post("/world/1/alliances.json", content="alliance_name=Slow&description="))["alliance_id"]
    decrypt_json(boss_c.post(f"/world/1/alliances/{aid}/auto_in.json", content="status=1"))
    decrypt_json(helper_c.post("/world/1/alliance_applications.json", content=f"alliance_id={aid}"))
    with db.player_state(boss["user_id"]) as s:
        p = s["empire"]["main_planet"]
        fleets.send(boss["user_id"], s, {"fleet_ids": f"{planet_rules.fleet(p, 4)['fleet_id']},", "fleet_cnt_ids": "2,",
                                         "speed_ratio": "100", "mission": str(fleets.ATTACK),
                                         "end_p_pos": ",".join(map(str, position(victim["user_id"])))}, NOW)
    attack = fleets.events_view(boss["user_id"])[0]
    with db.connect() as conn:  # the attack arrives in a moment
        conn.execute("UPDATE fleet_events SET arrival_time = ? WHERE id = ?", (int(time.time()) + 1, attack["event_id"]))
    hp = helper["empire"]["main_planet"]["planet_id"]
    late = helper_c.post(f"/world/1/acses/{attack['event_id']}/append", content=f"planet_id={hp}&fleets=4:2")
    assert late.status_code == 400
    assert helper_c.post(f"/world/1/acses/{attack['event_id']}/delete").status_code == 400  # not the attacker
