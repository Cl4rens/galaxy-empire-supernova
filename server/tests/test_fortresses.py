"""Alliance treasury and star fortresses."""

import time

import pytest

from supernova import db, ecm, fortresses, mails
from supernova import planet as planet_rules

from .test_acs import member
from .test_server import decrypt_json


@pytest.fixture(autouse=True)
def battle_in_a_day(monkeypatch):
    """The war phase is on; the battle comes in a day."""
    now = int(time.time())
    monkeypatch.setattr(fortresses, "BATTLE_OFFSET", now + 86400 - fortresses.cycle_seconds())
    conn = db.connect()
    with conn:
        conn.execute("DELETE FROM fortresses")
        conn.execute("DELETE FROM fortress_wars")
        conn.execute("DELETE FROM fortress_events")
    fortresses._owners_read = 0


def alliance(udid, name, ships, dark_matter=500):
    c, me = member(udid, ships)
    with db.player_state(me["user_id"]) as s:
        s["empire"]["dark_matter_count"] = dark_matter
    aid = decrypt_json(c.post("/world/1/alliances.json", content=f"alliance_name={name}&description="))["alliance_id"]
    return c, me, aid


def station(c, me, ftype, count, kind=fortresses.FORTRESS, target=1):
    p = db.load_state(me["user_id"])["empire"]["main_planet"]
    fid = planet_rules.fleet(p, ftype)["fleet_id"]
    body = (f"fleet_ids={fid},&fleet_cnt_ids={count},&mission=7&planet_id={p['planet_id']}"
            f"&end_id={target}&hanging_type={kind}")
    return c.post("/world/1/fortress_hanging_events", content=body)


def test_treasury_donate_log_and_spend():
    c, me, aid = alliance("bag-boss", "Coffers", {})
    helper_c, helper = member("bag-helper", {})
    with db.player_state(helper["user_id"]) as s:
        s["empire"]["dark_matter_count"] = 80
    decrypt_json(c.post(f"/world/1/alliances/{aid}/auto_in.json", content="status=1"))
    decrypt_json(helper_c.post("/world/1/alliance_applications.json", content=f"alliance_id={aid}"))

    assert helper_c.post(f"/world/1/alliances/{aid}/donate", content="dark_matter=81&credit=0").status_code == 400
    done = decrypt_json(helper_c.post(f"/world/1/alliances/{aid}/donate", content="dark_matter=60&credit=0"))
    assert done["my_dark_matter"] == 20 and {"bag_type": "1", "number": 60} in done["bag_info"]
    details = decrypt_json(c.get(f"/world/1/alliances/{aid}.json"))
    assert {"bag_type": "1", "number": 60} in details["bag"]
    log = decrypt_json(c.post(f"/world/1/alliances/{aid}/bag_log"))["log"]
    assert log[0]["user"] == helper["username"] and log[0]["number"] == 60 and log[0]["action"] == "Donation"

    # members cannot declare war, and the leader needs enough in the treasury
    assert helper_c.post("/world/1/fortresses/1/declare_war").status_code == 400
    assert "treasury" in c.post("/world/1/fortresses/1/declare_war").text
    decrypt_json(c.post(f"/world/1/alliances/{aid}/donate", content="dark_matter=40&credit=0"))
    declared = decrypt_json(c.post("/world/1/fortresses/1/declare_war"))
    assert declared["fortress"]["declare_war"] and declared["fortress"]["apply_count"] == 1
    assert decrypt_json(c.get(f"/world/1/alliances/{aid}.json"))["bag"][1]["number"] == 0
    assert decrypt_json(c.post(f"/world/1/alliances/{aid}/bag_log"))["log"][0]["number"] == -100


def test_war_phase(monkeypatch):
    c, me, aid = alliance("war-boss", "Latecomers", {4: 5}, dark_matter=500)
    decrypt_json(c.post(f"/world/1/alliances/{aid}/donate", content="dark_matter=100&credit=0"))
    monkeypatch.setattr(fortresses, "BATTLE_OFFSET", int(time.time()) + 5 * 86400 - fortresses.cycle_seconds())
    view = decrypt_json(c.get("/world/1/fortresses/2"))
    assert view["status"] == fortresses.NOT_OCCUPIED and view["start_time"][1] == fortresses.war_start(1)
    assert "war phase starts" in c.post("/world/1/fortresses/2/declare_war").text
    monkeypatch.setattr(fortresses, "BATTLE_OFFSET", int(time.time()) + 86400 - fortresses.cycle_seconds())
    view = decrypt_json(c.post("/world/1/fortresses/2/declare_war"))["fortress"]
    assert view["status"] == fortresses.WAR_PHASE and view["start_time"][1] == fortresses.battle_time(1)


def test_battle_for_a_fortress():
    a_c, a, a_id = alliance("fort-a", "Orionites", {4: 40}, dark_matter=1000)
    b_c, b, b_id = alliance("fort-b", "Dracos", {2: 30}, dark_matter=1000)
    for c, aid in ((a_c, a_id), (b_c, b_id)):
        decrypt_json(c.post(f"/world/1/alliances/{aid}/donate", content="dark_matter=200&credit=0"))
    # without a declaration no fleet may go there
    assert station(a_c, a, 4, 40).status_code == 400
    decrypt_json(a_c.post("/world/1/fortresses/1/declare_war"))
    decrypt_json(b_c.post("/world/1/fortresses/1/declare_war"))
    song = decrypt_json(b_c.post("/world/1/alliances/1/alli_stroe_buy", content="item=2"))
    assert "morale +50" in song["info"]

    prepared = decrypt_json(a_c.get(f"/world/1/fortress_hanging_events/prepare_hanging?fleet_ids="
                                    f"{planet_rules.fleet(a['empire']['main_planet'], 4)['fleet_id']},"
                                    f"&fleet_cnt_ids=40,&end_id=1&hanging_type=1&mission=7"))
    assert prepared["missions"] == [7] and prepared["total_time"] > 0
    flown = decrypt_json(station(a_c, a, 4, 40))
    assert flown["empire"]["fortress_interspace_events"][0]["end_planet_name"] == "Orion Fortress"
    decrypt_json(station(b_c, b, 2, 30))

    arrival = max(e["arrival_time"] for e in db.connect().execute("SELECT arrival_time FROM fortress_events"))
    fortresses.process_due(arrival)
    ranking = decrypt_json(a_c.post("/world/1/fortresses/1/alliance_list"))["alliance_list"]
    assert [r["alli_name"] for r in ranking] == ["Orionites", "Dracos"]  # 40 battleships beat 30 + a song
    assert ranking[0]["member"] == 1 and ranking[0]["morale"] == 40 * 60_000 // 1000

    battle = fortresses.battle_time(fortresses.next_battle(time.time()))
    fortresses.process_due(battle + 1)
    assert fortresses.owner(1) == a_id
    report = mails.undelivered(b["user_id"])[-1]["body"]
    assert "held by Orionites" in report["notify_message"] and report["attack_result"]["round"]
    view = decrypt_json(a_c.get("/world/1/fortresses/1"))
    assert view["alliance_name"] == "Orionites"
    assert all(e["is_returning"] for e in fortresses.events_view(a["user_id"]))

    # the holder's members produce more metal, get supplies and may shop
    empire = db.load_state(a["user_id"])["empire"]
    assert fortresses.production_bonus(empire, "metal") == fortresses.PRODUCTION_BONUS
    assert fortresses.production_bonus(db.load_state(b["user_id"])["empire"], "metal") == 0
    gifts = decrypt_json(a_c.post(f"/world/1/alliances/{a_id}/list_rewards"))["gifts"]
    assert [g["id"] for g in gifts] == [1]
    decrypt_json(a_c.post(f"/world/1/alliances/{a_id}/get_reward", content="reward_id=1"))
    assert decrypt_json(a_c.post(f"/world/1/alliances/{a_id}/list_rewards"))["gifts"] == []
    assert ecm.data(db.load_state(a["user_id"]))["chips"] == fortresses.GIFT_CHIPS
    bought = decrypt_json(a_c.post("/world/1/fortresses/1/buy", content="game_item_id=1&count=2"))
    assert bought["darkmatter"] == 800 - 80
    assert b_c.post("/world/1/fortresses/1/buy", content="game_item_id=1&count=1").status_code == 400

    fortresses.process_due(battle + 86400)
    assert planet_rules.fleet(db.load_state(a["user_id"])["empire"]["main_planet"], 4)["amount"] > 0
    assert fortresses.events_view(a["user_id"]) == []


def test_interspace_attacks():
    c, me, aid = alliance("space-boss", "Riftwalkers", {7: 50}, dark_matter=500)
    decrypt_json(c.post(f"/world/1/alliances/{aid}/donate", content="dark_matter=100&credit=0"))
    decrypt_json(c.post("/world/1/fortresses/1/declare_war"))
    guards = decrypt_json(c.get("/world/1/fortresses/interspace_fleet?interspace_id=101"))["fleets"]
    assert guards and all(g["count"] > 0 for g in guards)
    for _ in range(fortresses.INTERSPACE_ATTACKS):
        decrypt_json(station(c, me, 7, 10, fortresses.INTERSPACE, 101))
    assert "No more" in station(c, me, 7, 10, fortresses.INTERSPACE, 101).text
    assert decrypt_json(c.get("/world/1/fortresses/1"))["interspace_info"]["left_count"] == 0
    arrival = max(e["arrival_time"] for e in db.connect().execute("SELECT arrival_time FROM fortress_events"))
    fortresses.process_due(arrival)
    reports = [m["body"] for m in mails.undelivered(me["user_id"]) if "attack_result" in m["body"]]
    assert len(reports) == 3 and "Orion Rift" in str(reports[-1]["event_info"])
    morale = fortresses.alliance_list(1, time.time())[0]["morale"]
    wins = sum("morale" in r["notify_message"] for r in reports)
    assert morale == wins * fortresses.INTERSPACE_MORALE
