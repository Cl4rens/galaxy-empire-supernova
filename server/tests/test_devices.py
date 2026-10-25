"""Ship modification with devices (wiki rules)."""

from supernova import combat, db, devices, fleets, items, ranking

from .test_acs import member
from .test_server import decrypt_json


def modder(udid, score=500_000, monkeypatch=None):
    c, me = member(udid, {})
    with db.player_state(me["user_id"]) as s:
        s["empire"]["dark_matter_count"] = 1000
    monkeypatch.setattr(ranking, "points", lambda state: {"score": score})
    return c, me


def test_wiki_numbers():
    assert [devices.devices_needed(lv) for lv in range(4)] == [1, 2, 4, 8]
    assert [devices.required_score(lv) for lv in (0, 1, 2, 8, 9, 10)] == [
        100_000, 150_000, 200_000, 1_400_000, 1_800_000, 2_200_000]
    assert [devices.percent(lv) for lv in (1, 9, 10, 11)] == [10, 90, 110, 140]


def test_view_buy_and_install(monkeypatch):
    c, me = modder("dev-buyer", monkeypatch=monkeypatch)
    uid = me["user_id"]
    view = decrypt_json(c.get("/world/1/users/fleet_devices?fleet_type=7"))
    assert len(view["items"]) == 8 and [d["type"] for d in view["fleet_device"]] == [1, 2, 3, 4]
    attack = view["fleet_device"][2]
    assert attack["lv"] == 0 and attack["devices"] == 1 and attack["item_id"] == devices.item_id(3, 1)

    shop = decrypt_json(c.get("/world/1/game_items?category=4"))["items"]
    assert {i["id"] for i in shop} >= {i.id for i in devices.catalog()}
    assert c.post("/world/1/users/use_device", content=f"fleet_type=7&id={attack['item_id']}").status_code == 400
    decrypt_json(c.post(f"/world/1/users/{uid}/buy_game_item", content=f"game_item_id={attack['item_id']}&number=3"))
    assert c.post(f"/world/1/users/{uid}/use_game_item",
                  content=f"game_item_id={attack['item_id']}&number=1").status_code == 400
    done = decrypt_json(c.post("/world/1/users/use_device", content=f"fleet_type=7&id={attack['item_id']}"))
    assert done["fleet_device"]["lv"] == 1 and done["item_number"] == 2 and done["fleet_device"]["devices"] == 2
    done = decrypt_json(c.post("/world/1/users/use_device", content=f"fleet_type=7&id={attack['item_id']}"))
    assert done["fleet_device"]["lv"] == 2 and done["item_number"] == 0

    empire = db.load_state(uid)["empire"]
    assert devices.unit_bonuses(empire)[7] == (20, 0, 0)
    side = fleets._side("x", empire, {7: 1})
    assert side.weapon_of(7) > combat.STATS[7].weapon * side.weapon_factor()
    # the wrong grade, defense engines and unknown ship types are refused
    assert c.post("/world/1/users/use_device", content=f"fleet_type=24&id={attack['item_id']}").status_code == 400
    assert c.post("/world/1/users/use_device",
                  content=f"fleet_type=20&id={devices.item_id(4, 1)}").status_code == 400
    assert c.get("/world/1/users/fleet_devices?fleet_type=30").status_code == 400


def test_score_limit_speed_and_drops(monkeypatch):
    c, me = modder("dev-poor", score=120_000, monkeypatch=monkeypatch)
    uid = me["user_id"]
    engine = devices.item_id(devices.SPEED, 1)
    with db.player_state(uid) as s:
        items._add(s, engine, 10)
    before = fleets.ship_speed(db.load_state(uid)["empire"], 5)
    decrypt_json(c.post("/world/1/users/use_device", content=f"fleet_type=5&id={engine}"))
    assert fleets.ship_speed(db.load_state(uid)["empire"], 5) == int(before * 1.1)
    assert "150,000 points" in c.post("/world/1/users/use_device", content=f"fleet_type=5&id={engine}").text
    with db.player_state(uid) as s:
        note = devices.drop(s, 4)
        assert note.startswith("Devices:") and sum(items.amount(s, i.id) for i in devices.catalog()) == 9 + 4
