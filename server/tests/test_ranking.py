"""Score and rankings."""

from fastapi.testclient import TestClient

from supernova import db, economy as eco, player, ranking
from supernova import planet as planet_rules
from supernova.app import app

from .test_server import decrypt_json


def test_points_count_buildings_research_and_units():
    state = player.new_state(1, "x")
    assert ranking.points(state) == {"score": 0, "power": 0}
    p = state["empire"]["main_planet"]
    planet_rules.building(p, eco.METAL_MINE)["building_level"] = 2  # 60+15 + 90+22 = 187
    planet_rules.fleet(p, 3)["amount"] = 1  # cruiser 29,000
    assert ranking.points(state) == {"score": (187 + 29_000) // 1000, "power": 29}


def test_ranks_endpoint_orders_by_score():
    c = TestClient(app)
    me = decrypt_json(c.post("/world/1/trials.json", content="username="))
    with db.player_state(me["user_id"]) as state:
        planet_rules.fleet(state["empire"]["main_planet"], 8)["amount"] = 100  # deathstars: top score
    top = decrypt_json(c.get("/world/1/ranks?do=top_users&user_id=1"))["rank"]
    assert top[0]["id"] == me["user_id"] and top[0]["rank"] == 1
    assert top == sorted(top, key=lambda r: -r["score"])
    mine = decrypt_json(c.get("/world/1/ranks?do=my_rank"))["rank"]
    assert any(r["id"] == me["user_id"] for r in mine)
    assert isinstance(decrypt_json(c.get("/world/1/ranks?do=top_alliances"))["rank"], list)


def test_battle_stats():
    ranking.record_battle(424242, "win")
    ranking.record_battle(424242, "draw")
    row = db.connect().execute("SELECT * FROM battle_stats WHERE player_id = 424242").fetchone()
    assert (row["wins"], row["losses"], row["draws"]) == (1, 0, 1)
