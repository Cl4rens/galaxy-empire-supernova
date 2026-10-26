"""Moons: forming after battles, the moon screen, moon buildings and flights to moons."""

import math
import sqlite3

import pytest
from fastapi.testclient import TestClient

from supernova import db, fleets, mails, player, universe
from supernova import economy as eco
from supernova import planet as planet_rules
from supernova.app import app

from .test_fleets import new_player_with_cargo, position
from .test_server import decrypt_json


def test_moon_chance_grows_with_debris():
    assert fleets.moon_chance({"metal": 50_000, "crystal": 40_000}) == 0
    assert fleets.moon_chance({"metal": 300_000, "crystal": 250_000}) == 5
    assert fleets.moon_chance({"metal": 9_000_000, "crystal": 0}) == fleets.MOON_CHANCE_MAX


def test_battle_debris_can_form_a_moon(monkeypatch):
    monkeypatch.setattr(fleets.random, "uniform", lambda a, b: 0.0)  # every chance succeeds
    attacker, defender = new_player_with_cargo(0), new_player_with_cargo(0)
    with db.player_state(attacker) as state:
        planet_rules.fleet(state["empire"]["main_planet"], 4)["amount"] = 50  # battleships
    with db.player_state(defender) as state:
        planet_rules.fleet(state["empire"]["main_planet"], 2)["amount"] = 400  # heavy fighters
    with db.player_state(attacker) as state:
        p = state["empire"]["main_planet"]
        fleets.send(attacker, state, {"fleet_ids": f"{planet_rules.fleet(p, 4)['fleet_id']},",
                                      "fleet_cnt_ids": "50,", "end_p_pos": ",".join(map(str, position(defender))),
                                      "mission": str(fleets.ATTACK), "speed_ratio": "100"}, 2_000_000_000.0)
    fleets.process_due(fleets.events_view(attacker)[0]["finish_time"])

    home = db.load_state(defender)["empire"]["planets"][0]
    assert home["moon"]["planet_type"] == player.PLANET_TYPE_MOON
    assert "moon" in mails.undelivered(defender)[0]["body"]["notify_message"]
    slot = universe.system_view(*position(defender)[:2])["planets"][position(defender)[2] - 1]
    assert slot["moon_array"][0]["moon_id"] == home["moon"]["planet_id"]


def guest_with_moon(udid):
    c = TestClient(app, headers={"openudid": udid})
    state = decrypt_json(c.post("/world/1/trials.json", content="username="))
    pid = state["user_id"]
    with db.player_state(pid) as s:
        moon = player.add_moon(pid, s["empire"]["planets"][0])
    return c, pid, moon["planet_id"]


def test_moon_screen_and_buildings():
    c, pid, moon_id = guest_with_moon("mooner")
    info = decrypt_json(c.get(f"/world/1/users/{pid}/my_planets.json"))["planets_info"]
    assert len(info) == 1 and info[0]["moon_array"][0]["moon_id"] == moon_id

    moon = decrypt_json(c.get(f"/world/1/planets/{moon_id}.json"))
    assert moon["planet_type"] == player.PLANET_TYPE_MOON and moon["metal_perhour"] == 0
    assert {b["building_type"] for b in moon["buildings"]} == set(eco.MOON_BUILDINGS)
    assert (moon["field_current"], moon["field_max"]) == (0, 1)

    with db.player_state(pid) as s:
        m = planet_rules.find_planet(s["empire"], moon_id)
        m.update(metal=1_000_000, crystal=1_000_000, deuterium=1_000_000)
        mine = planet_rules.building(m, eco.METAL_MINE)["building_id"]
        robots = planet_rules.building(m, eco.MOON_ROBOTICS_FACTORY)["building_id"]
        base = planet_rules.building(m, eco.MOON_BASE)["building_id"]
    assert c.post("/world/1/building_events.json",
                  content=f"planet_id={moon_id}&building_id={mine}&event_type=1").status_code == 400
    decrypt_json(c.post("/world/1/building_events.json",
                        content=f"planet_id={moon_id}&building_id={robots}&event_type=1"))
    with db.player_state(pid) as s:  # finish it: the only field is used now
        m = planet_rules.find_planet(s["empire"], moon_id)
        planet_rules.tick(s["empire"], m["building_events"][0]["finish_time"])
    full = c.post("/world/1/building_events.json", content=f"planet_id={moon_id}&building_id={robots}&event_type=1")
    assert full.status_code == 400 and "moon base" in full.text
    decrypt_json(c.post("/world/1/building_events.json",
                        content=f"planet_id={moon_id}&building_id={base}&event_type=1"))


def test_deploy_to_own_moon():
    pid = new_player_with_cargo(3)
    with db.player_state(pid) as s:
        moon = player.add_moon(pid, s["empire"]["planets"][0])
        p = s["empire"]["main_planet"]
        info = fleets.plan(s["empire"], pid, {9: 1}, position(pid), moon=True)
        assert fleets.DEPLOY in info["missions"] and info["distance"] == 5
        fleets.send(pid, s, {"fleet_ids": f"{planet_rules.fleet(p, 9)['fleet_id']},", "fleet_cnt_ids": "2,",
                             "end_p_pos": ",".join(map(str, position(pid))), "planet_type": "moon",
                             "mission": str(fleets.DEPLOY), "speed_ratio": "100"}, 2_000_000_000.0)
    fleets.process_due(fleets.events_view(pid)[0]["finish_time"])
    moon = db.load_state(pid)["empire"]["planets"][0]["moon"]
    assert planet_rules.fleet(moon, 9)["amount"] == 2


def test_give_up_moon():
    c, pid, moon_id = guest_with_moon("moon-quitter")
    decrypt_json(c.post(f"/world/1/planets/{moon_id}", content="_method=delete"))
    assert db.load_state(pid)["empire"]["planets"][0]["moon"] is None
    assert fleets.target_planet(position(pid), moon=True) is None


def test_old_planet_tables_get_the_kind_column():
    conn = sqlite3.connect(":memory:")
    conn.execute("CREATE TABLE planets (id INTEGER PRIMARY KEY AUTOINCREMENT, player_id INTEGER NOT NULL, "
                 "galaxy INTEGER NOT NULL, system INTEGER NOT NULL, position INTEGER NOT NULL, name TEXT NOT NULL, "
                 "UNIQUE (galaxy, system, position))")
    conn.execute("INSERT INTO planets (player_id, galaxy, system, position, name) VALUES (1, 1, 2, 3, 'Home')")
    db._migrate(conn)
    conn.execute("INSERT INTO planets (player_id, galaxy, system, position, name, kind) VALUES (1, 1, 2, 3, 'Moon', 1)")
    assert conn.execute("SELECT COUNT(*) FROM planets").fetchone()[0] == 2


def moon_with(pid, planet, **levels):
    with db.player_state(pid) as s:
        p = next(x for x in s["empire"]["planets"] if x["planet_id"] == planet["planet_id"])
        moon = player.add_moon(pid, p)
        for btype, lv in levels.items():
            planet_rules.building(moon, int(btype))["building_level"] = lv
        moon.update(deuterium=20_000)
    return moon["planet_id"]


def test_sensor_array_scans_flights_in_range():
    watcher, sender, receiver = new_player_with_cargo(0), new_player_with_cargo(), new_player_with_cargo(0)
    home = db.load_state(watcher)["empire"]["planets"][0]
    moon_id = moon_with(watcher, home, **{str(eco.SENSOR_ARRAY): 1, str(eco.MOON_BASE): 1})
    target_id = db.load_state(receiver)["empire"]["planets"][0]["planet_id"]
    with db.player_state(sender) as s:
        fleets.send(sender, s, {"fleet_ids": f"{planet_rules.fleet(s['empire']['main_planet'], 9)['fleet_id']},",
                                "fleet_cnt_ids": "2,", "end_p_pos": ",".join(map(str, position(receiver))),
                                "mission": str(fleets.TRANSPORT), "speed_ratio": "100"}, 2_000_000_000.0)

    with db.player_state(watcher) as s:
        moon = planet_rules.find_planet(s["empire"], moon_id)
        sensor = planet_rules.building(moon, eco.SENSOR_ARRAY)
        distance = abs(position(receiver)[1] - home["system_position"])
        if distance > 0:  # level 1 sees only its own system
            with pytest.raises(fleets.FleetError, match="range"):
                fleets.scan(s["empire"], moon_id, target_id, 2_000_000_001.0)
            sensor["building_level"] = math.isqrt(distance) + 1  # range is level² - 1
        found = fleets.scan(s["empire"], moon_id, target_id, 2_000_000_001.0)
        assert moon["deuterium"] <= 20_000 - fleets.SCAN_DEUTERIUM
        sensor["building_level"] = 1  # keep this player's score small for the ranking tests
    assert found[0]["mission"] == fleets.TRANSPORT and found[0]["amount"] == 2 and found[0]["is_returning"] == 0
    assert found[0]["fleets"][0]["name"] == "Small Cargo"

    with db.player_state(watcher) as s:
        fleets.save_scan(watcher, s["empire"], moon_id, target_id)
    mail = mails.undelivered(watcher)[-1]
    assert mail["message_type"] == mails.SCAN and mail["body"]["body"][0]["fleets"][0]["fleet_count"] == 2


def test_transmit_portal_moves_ships_and_recharges():
    c = TestClient(app, headers={"openudid": "jumper"})
    state = decrypt_json(c.post("/world/1/trials.json", content="username="))
    pid = state["user_id"]
    with db.player_state(pid) as s:
        research = next(r for r in s["empire"]["researches"] if r["research_type"] == eco.ASTROPHYSICS)
        research["research_level"] = 2
        s["empire"]["main_planet"].update(metal=10_000, crystal=10_000, deuterium=10_000)
    from .test_colonies import colonize, free_position
    colonize(pid, free_position(pid))
    home, colony = db.load_state(pid)["empire"]["planets"]
    portal = {str(eco.TRANSMIT_PORTAL): 1, str(eco.MOON_BASE): 1}
    a, b = moon_with(pid, home, **portal), moon_with(pid, colony, **portal)
    with db.player_state(pid) as s:
        moon_a = planet_rules.find_planet(s["empire"], a)
        planet_rules.fleet(moon_a, 2)["amount"] = 7
        fid = planet_rules.fleet(moon_a, 2)["fleet_id"]
    target = ",".join(map(str, (colony["galaxy_position"], colony["system_position"], colony["planet_position"])))
    params = f"start_p_id={a}&fleet_ids={fid},&fleet_cnt_ids=7,&end_p_pos={target}&planet_type=moon&speed_ratio=100"
    assert fleets.TRANSMIT in decrypt_json(c.get(f"/world/1/fleet_hanging_events/prepare_hanging?{params}"))["missions"]
    decrypt_json(c.post("/world/1/fleet_hanging_events.json", content=f"{params}&mission={fleets.TRANSMIT}"))
    s = db.load_state(pid)
    assert planet_rules.fleet(planet_rules.find_planet(s["empire"], b), 2)["amount"] == 7
    assert planet_rules.find_planet(s["empire"], a)["transmit_recharge_event"]
    again = c.post("/world/1/fleet_hanging_events.json", content=f"{params}&mission={fleets.TRANSMIT}")
    assert again.status_code == 400
