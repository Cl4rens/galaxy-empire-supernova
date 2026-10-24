"""Tutorial quests: goals, rewards, progress and skipping."""

from fastapi.testclient import TestClient

from supernova import db, quests
from supernova import planet as planet_rules
from supernova.app import app

from .test_server import decrypt_json


def guest(udid):
    c = TestClient(app, headers={"openudid": udid})
    return c, decrypt_json(c.post("/world/1/trials.json", content="username="))


def test_quest_chain_rewards_once():
    c, state = guest("quester")
    first = decrypt_json(c.get("/world/1/quest_events"))["update"]
    assert first["cur_quest"]["quest_index"] == 2 and first["cur_quest"]["quest_state"] is False
    assert first["reward_info"] == {}
    assert db.load_state(state["user_id"])["empire"]["quests_newbie_guide"] == 1

    with db.player_state(state["user_id"]) as s:
        p = s["empire"]["main_planet"]
        for btype in (1, 2):
            planet_rules.building(p, btype)["building_level"] = 1
        metal = p["metal"]
    done = decrypt_json(c.get("/world/1/quest_events"))["update"]
    assert done["cur_quest"]["quest_state"] is True
    assert {"type": 1, "subtype": 1, "amount": int(metal) + 200} in done["reward_info"]["data"]

    after = decrypt_json(c.get("/world/1/quest_events"))["update"]
    assert after["cur_quest"]["quest_index"] == 3 and after["reward_info"] == {}
    assert db.load_state(state["user_id"])["empire"]["main_planet"]["metal"] < metal + 400  # paid once


def test_fleet_missions_count_for_quests():
    state = {"empire": {"quests_complete": False}}
    quests.record_action(state, quests.ESPIONAGE)
    quests.record_action(state, quests.ESPIONAGE)
    assert state["quests"]["actions"] == [quests.ESPIONAGE]
    espionage = next(q for q in quests.QUESTS if q.index == 16)
    assert espionage.targets[0].done(state, 0)


def test_rename_planet_and_skip():
    c, state = guest("skipper")
    pid = state["empire"]["main_planet"]["planet_id"]
    renamed = decrypt_json(c.post(f"/world/1/planets/{pid}/change_name.json", content="name=Nova Prime"))
    assert renamed["planet_name"] == "Nova Prime"
    s = db.load_state(state["user_id"])
    rename_quest = next(q for q in quests.QUESTS if q.index == 12)
    assert rename_quest.targets[0].done(s, state["user_id"])

    skipped = decrypt_json(c.post("/world/1/quest_event/skip_all_newbie_guide"))
    assert skipped["empire"]["quests_complete"] is True and skipped["update"]["rewards_all"]
    assert "quests" not in decrypt_json(c.post("/world/1/trials.json", content=f"username={state['username']}"))
