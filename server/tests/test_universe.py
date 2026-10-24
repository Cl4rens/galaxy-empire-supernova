"""Universe layout, home placement and the galaxy/system views."""

from fastapi.testclient import TestClient

from supernova import universe
from supernova.app import app

from .test_server import decrypt_json


def test_map_coordinates_round_trip():
    for system in (1, 34, 35, 499):
        x, y = universe.map_coordinates(system)
        assert universe.system_at(x, y) == system
    assert universe.system_at(33, 14) is None  # beyond system 499
    assert universe.system_from_id(universe.system_id(3, 42)) == (3, 42)


def test_new_players_get_distinct_home_planets():
    players = [TestClient(app) for _ in range(5)]
    homes = []
    for c in players:
        p = decrypt_json(c.post("/world/1/trials.json", content="username="))["empire"]["main_planet"]
        assert p["planet_position"] in universe.HOME_POSITIONS
        assert p["system_id"] == universe.system_id(p["galaxy_position"], p["system_position"])
        homes.append((p["galaxy_position"], p["system_position"], p["planet_position"]))
    assert len(set(homes)) == len(homes)


def test_system_view_shows_the_player():
    c = TestClient(app)
    state = decrypt_json(c.post("/world/1/trials.json", content="username="))
    p = state["empire"]["main_planet"]

    by_id = decrypt_json(c.get(f"/world/1/systems/{p['system_id']}"))
    by_pos = decrypt_json(c.get(f"/world/1/systems/{p['system_position']}/{p['galaxy_position']}"))
    assert by_id == by_pos
    assert [s["planet_position"] for s in by_id["planets"]] == list(range(1, 22))
    mine = by_id["planets"][p["planet_position"] - 1]
    assert (mine["id"], mine["user_name"]) == (p["planet_id"], state["username"])
    assert all(s["kryptons"] == [] for s in by_id["planets"])

    locate = decrypt_json(c.get(f"/world/1/systems/{p['system_id']}/locate"))
    x, y = locate["system_coordinate_x"], locate["system_coordinate_y"]
    tiles = decrypt_json(c.get(f"/world/1/galaxy_map.json?galaxy_position=1&x={x}&y={y}"))["systems"]
    here = next(t for t in tiles if t["system_position"] == p["system_position"])
    assert here["user_count"] >= 1 and "lord_name" in here["lord_info"]

    assert c.get("/world/1/systems/500/1").status_code == 404
