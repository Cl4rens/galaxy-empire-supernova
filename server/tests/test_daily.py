"""Daily tasks."""

import time

from supernova import daily, db, ecm
from supernova import economy as eco
from supernova import planet as planet_rules

from .test_acs import member
from .test_server import decrypt_json


def test_tasks_progress_claim_and_reset():
    c, me = member("daily-worker", {})
    uid = me["user_id"]
    tasks = decrypt_json(c.get("/world/1/daily_tasks"))["list"]
    assert len(tasks) == len(daily.TASKS) and all(t["status"] == 0 for t in tasks)
    assert c.post("/world/1/daily_tasks/receive_reward", content="id=1").status_code == 400

    with db.player_state(uid) as s:
        planet = s["empire"]["main_planet"]
        planet.update(metal=1e9, crystal=1e9, deuterium=1e9)
        mine = planet_rules.building(planet, eco.METAL_MINE)["building_id"]
    for _ in range(3):  # upgrades through the client's route
        decrypt_json(c.post("/world/1/building_events.json", content=f"building_id={mine}&event_type=1"))
        with db.player_state(uid) as s:
            planet = s["empire"]["main_planet"]
            planet_rules.tick(s["empire"], planet["building_events"][0]["finish_time"] + 1)
    with db.player_state(uid) as s:
        daily.record(s, daily.ATTACK_PIRATES, 2)
    tasks = {t["id"]: t for t in decrypt_json(c.get("/world/1/daily_tasks"))["list"]}
    assert tasks[1]["status"] == 1 and tasks[1]["process"] == 3 and tasks[4]["status"] == 1
    assert tasks[4]["rewards_info"] == [{"img": daily.ICONS["chips"], "number": 3}]

    done = decrypt_json(c.post("/world/1/daily_tasks/receive_reward", content="id=4"))
    assert "ECM chips" in done["info"] and ecm.data(db.load_state(uid))["chips"] == 3
    assert 4 not in {t["id"] for t in decrypt_json(c.get("/world/1/daily_tasks"))["list"]}
    assert c.post("/world/1/daily_tasks/receive_reward", content="id=4").status_code == 400

    tomorrow = time.time() + 86400
    with db.player_state(uid) as s:
        assert {t["id"] for t in daily.listing(s, tomorrow)} == {t.id for t in daily.TASKS}
        assert all(t["process"] == 0 for t in daily.listing(s, tomorrow))


def test_vip_info_answers():
    c, me = member("daily-vip", {})
    assert decrypt_json(c.post(f"/world/1/users/{me['user_id']}/vip_info"))["vip_info"]["vip_level"] == 0
