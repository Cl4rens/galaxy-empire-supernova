"""Several planets per player: colonization, switching planets, old saves."""

import json

import pytest

from fastapi.testclient import TestClient

from supernova import db, fleets, mails, player
from supernova import economy as eco
from supernova import planet as planet_rules
from supernova.app import app

from .test_fleets import new_player_with_cargo, position
from .test_server import decrypt_json


def free_position(pid):
    galaxy, system, _ = position(pid)
    for pos in range(1, 16):
        if fleets.target_planet((galaxy, system, pos)) is None:
            return galaxy, system, pos
    raise AssertionError("system full")


def colonize(pid, target, now=2_000_000_000.0):
    with db.player_state(pid) as state:
        p = state["empire"]["main_planet"]
        planet_rules.fleet(p, fleets.COLONY_SHIP)["amount"] = 1
        planet_rules.fleet(p, 9)["amount"] = 2
        ids = f"{planet_rules.fleet(p, fleets.COLONY_SHIP)['fleet_id']},{planet_rules.fleet(p, 9)['fleet_id']},"
        fleets.send(pid, state, {"fleet_ids": ids, "fleet_cnt_ids": "1,2,", "mission": str(fleets.COLONY),
                                 "end_p_pos": ",".join(map(str, target)), "speed_ratio": "100",
                                 "metal": "1000", "crystal": "500", "deuterium": "0"}, now)
    fleets.process_due(fleets.events_view(pid)[0]["finish_time"])


def test_colonization_needs_astrophysics():
    pid = new_player_with_cargo(0)
    with pytest.raises(planet_rules.GameError, match="Astrophysics"):
        colonize(pid, free_position(pid))
    assert len(db.load_state(pid)["empire"]["planets"]) == 1


def test_colonize_and_switch_planets():
    pid = new_player_with_cargo(0)
    with db.player_state(pid) as state:
        research = next(r for r in state["empire"]["researches"] if r["research_type"] == eco.ASTROPHYSICS)
        research["research_level"] = 1
    target = free_position(pid)
    colonize(pid, target)

    empire = db.load_state(pid)["empire"]
    assert len(empire["planets"]) == 2
    colony = empire["planets"][1]
    assert (colony["galaxy_position"], colony["system_position"], colony["planet_position"]) == target
    assert colony["metal"] == 1000 and planet_rules.fleet(colony, 9)["amount"] == 2
    assert planet_rules.fleet(colony, fleets.COLONY_SHIP)["amount"] == 0
    assert fleets.target_planet(target)["id"] == colony["planet_id"]
    assert mails.undelivered(pid)[-1]["body"]["mission_type"] == fleets.COLONY
    assert fleets.events_view(pid) == []  # the fleet stays at the colony

    info = player.planets_info(empire)
    assert [p["planet_id"] for p in info] == [empire["main_planet_id"], colony["planet_id"]]

    # Production runs on every planet, not just the one the player looks at.
    with db.player_state(pid) as state:
        planet_rules.tick(state["empire"], colony["last_update"] + 3600)
    assert db.load_state(pid)["empire"]["planets"][1]["metal"] > 1000


def test_switch_planet_over_api():
    c = TestClient(app)
    state = decrypt_json(c.post("/world/1/trials.json", content="username="))
    pid = state["user_id"]
    with db.player_state(pid) as s:
        research = next(r for r in s["empire"]["researches"] if r["research_type"] == eco.ASTROPHYSICS)
        research["research_level"] = 1
        s["empire"]["main_planet"].update(metal=10_000, crystal=10_000, deuterium=10_000)
    colonize(pid, free_position(pid))
    colony_id = db.load_state(pid)["empire"]["planets"][1]["planet_id"]

    planets = decrypt_json(c.get(f"/world/1/users/{pid}/my_planets.json"))["planets_info"]
    assert len(planets) == 2
    entered = decrypt_json(c.get(f"/world/1/planets/{colony_id}.json"))
    assert entered["planet_id"] == colony_id and entered["planet_type"] == player.PLANET_TYPE_COLONY
    assert "researches" in entered
    assert db.load_state(pid)["empire"]["current_planet_id"] == colony_id
    assert c.get("/world/1/planets/999999.json").status_code == 404


def test_old_single_planet_saves_are_migrated():
    state = player.new_state(1, "Old")
    empire = state["empire"]
    old = dict(state, state_version=1,
               empire={k: v for k, v in empire.items() if k not in ("planets", "current_planet_id")})
    loaded = db._decode(json.dumps(old))
    assert loaded["empire"]["planets"][0]["planet_id"] == 1
    assert loaded["empire"]["main_planet"] is loaded["empire"]["planets"][0]
    assert "main_planet" not in json.loads(db._encode(loaded))["empire"]
