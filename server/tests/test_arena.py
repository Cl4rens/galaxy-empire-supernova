"""Conquer The Universe: stages, garrison, challenges, prizes, shop and alliance activity."""

import random
import time

from supernova import arena, db
from supernova import planet as planet_rules

from .test_acs import member
from .test_server import decrypt_json


def at_stage(stage: int) -> float:
    """Start an event (garrison 1 h, challenge 2 days, prize 1 day) so that `stage` runs now."""
    now = time.time()
    offset = {arena.GARRISON: 10, arena.CHALLENGE: 3600 + 10, arena.PRIZE: 3600 + 2 * 86400 + 10}[stage]
    arena.start(now - offset, 3600, 2 * 86400, 86400)
    return now


def pid_and_planet(me):
    return me["user_id"], me["empire"]["main_planet"]["planet_id"]


def test_star_gate_shows_fortresses_without_event():
    c, me = member("arena-none", {})
    _, planet = pid_and_planet(me)
    arena.start(time.time() + 3600, 3600, 3600, 3600)  # not started yet
    assert decrypt_json(c.get(f"/world/1/challenge_events/{planet}")) == {"switch_status": False}
    assert decrypt_json(c.get("/world/1/challenge_event/switch")) == {"status": False}


def test_garrison_and_overview():
    c, me = member("arena-garrison", {4: 10})
    pid, planet = pid_and_planet(me)
    at_stage(arena.GARRISON)
    view = decrypt_json(c.get(f"/world/1/challenge_events/{planet}"))
    assert view["switch_status"] is True and view["challenge_status"]["status"] == arena.GARRISON
    assert view["self_info"]["current_rank"] == arena.ROBOTS + 1 and len(view["challenge_info"]["top_ten_info"]) == 10
    done = decrypt_json(c.post(f"/world/1/challenge_event/defending?planet_id={planet}&recognize=0"))
    assert done["hp"]["defending_capacity"] == arena.capacity({4: 10}) and "Rank: 101" in done["msg"]
    # the same ships again are not stronger: the client asks to pay for it
    again = decrypt_json(c.post(f"/world/1/challenge_event/defending?planet_id={planet}&recognize=0"))
    assert "hp" not in again and "credits" in again["msg"]
    # challenges are not open yet
    assert "not open" in c.post(f"/world/1/challenge_event/challenge?target_rank=100&planet_id={planet}").text


def test_challenge_swaps_ranks_and_limits():
    c, me = member("arena-fight", {4: 3000, 3: 3000})
    pid, planet = pid_and_planet(me)
    at_stage(arena.CHALLENGE)
    decrypt_json(c.post(f"/world/1/challenge_event/defending?planet_id={planet}&recognize=0"))
    result = decrypt_json(c.post(f"/world/1/challenge_event/challenge?target_rank=95&planet_id={planet}"))
    assert result["win"] == "win" and result["challenger"]["current_rank"] == 95
    assert result["activity_score"] == arena.WIN_POINTS and result["battle_log"]["round"]
    assert decrypt_json(c.get(f"/world/1/challenge_event/get_battle_report/{result['report_id']}"))["round"]
    # cool-down: the client offers to skip it for credits
    cool = decrypt_json(c.post(f"/world/1/challenge_event/challenge?target_rank=90&planet_id={planet}"))
    assert cool["is_cooling_time"] is True
    assert "credits" in c.post(f"/world/1/challenge_event/speed_up_and_challenge?planet_id={planet}&target_rank=90").text
    with db.player_state(pid) as s:
        s["empire"]["gu_count"] = 1000
    skipped = decrypt_json(c.post(f"/world/1/challenge_event/speed_up_and_challenge?planet_id={planet}&target_rank=90"))
    assert skipped["result"]["challenger"]["current_rank"] == 90
    with arena.event(time.time()) as ev:
        arena._player(ev, pid)["cool_until"] = 0
    decrypt_json(c.post(f"/world/1/challenge_event/challenge?target_rank=80&planet_id={planet}"))
    with arena.event(time.time()) as ev:
        arena._player(ev, pid)["cool_until"] = 0
    # 3 free challenges are used: the client gets a question and buys one more
    question = decrypt_json(c.post(f"/world/1/challenge_event/challenge?target_rank=70&planet_id={planet}"))
    assert isinstance(question, str) and str(arena.CHALLENGE_PRICE) in question
    bought = decrypt_json(c.post(f"/world/1/challenge_event/buy_hp_times_for_challenge?target_rank=70&planet_id={planet}"))
    assert bought["win"] == "win"
    logs = decrypt_json(c.get("/world/1/challenge_event/battle_logs"))["result"]
    assert len(logs) == 4 and logs[0]["def_original_rank"] == 70
    assert db.load_state(pid)["arena_points"] == 4 * arena.WIN_POINTS


def test_daily_and_final_prizes():
    c, me = member("arena-prize", {4: 50})
    pid, planet = pid_and_planet(me)
    now = at_stage(arena.CHALLENGE)
    decrypt_json(c.post(f"/world/1/challenge_event/defending?planet_id={planet}&recognize=0"))
    with arena.event(now) as ev:
        start = ev["start"]
    with arena.event(start + 3600 + 86400 + 5) as ev:  # the first daily prize node
        assert ev["daily"]["ranks"][str(pid)] == arena.ROBOTS + 1
    view = decrypt_json(c.get(f"/world/1/challenge_events/{planet}"))
    assert view["self_info"]["yesterday_rank"] == arena.ROBOTS + 1
    got = decrypt_json(c.post(f"/world/1/challenge_event/daily_reward/{planet}"))
    assert got["reward"] == {arena.POINTS: 30, arena.DARK_MATTER: 1}
    assert "no prize" in c.post(f"/world/1/challenge_event/daily_reward/{planet}").text
    arena.stop(time.time())
    view = decrypt_json(c.get(f"/world/1/challenge_events/{planet}"))
    assert view["challenge_status"]["status"] == arena.PRIZE and view["self_info"]["ultimate_reward"] is False
    final = decrypt_json(c.post(f"/world/1/challenge_event/ultimate_reward/{planet}"))
    assert final["reward"] == {arena.POINTS: 100, arena.DARK_MATTER: 5}
    assert db.load_state(pid)["arena_points"] == 130


def test_shop():
    c, me = member("arena-shop", {})
    pid, _ = pid_and_planet(me)
    at_stage(arena.GARRISON)
    offer = decrypt_json(c.get("/world/1/challenge_event/item_info"))
    assert offer["activity_score"] == 0 and {i["id"] for i in offer["all_items"]} == {s[0] for s in arena.SHOP}
    assert "points" in c.post("/world/1/challenge_event/exchange?item_id=5&item_count=1").text
    with db.player_state(pid) as s:
        s["arena_points"] = 2000
    bought = decrypt_json(c.post("/world/1/challenge_event/exchange?item_id=5&item_count=1"))
    assert bought["activity_score"] == 500 and bought["buy_amount"] == 1
    assert planet_rules.fleet(db.load_state(pid)["empire"]["main_planet"], 25)["amount"] == 1


def test_alliance_activity():
    boss_c, boss = member("arena-ally-boss", {4: 3000})
    aid = decrypt_json(boss_c.post("/world/1/alliances.json", content="alliance_name=Arena&description="))["alliance_id"]
    pid, planet = pid_and_planet(boss)
    now = at_stage(arena.CHALLENGE)
    decrypt_json(boss_c.post(f"/world/1/challenge_event/defending?planet_id={planet}&recognize=0"))
    with arena.event(now) as ev:
        arena.challenge(ev, db.load_state(pid), 100, now, random.Random(1))
    view = decrypt_json(boss_c.post("/world/1/challenge_event/alliance"))
    assert view["alliance_current_score"]["all_active"] == 1 and view["alliance_current_score"]["all_morale"] == 1
    assert "treasury" in decrypt_json(boss_c.get("/world/1/challenge_event/add_alliance_active_score"))["msg"]
    assert "treasury needs" in boss_c.post("/world/1/challenge_event/make_sure_for_add_active_score").text
    db.connect().execute("UPDATE alliances SET bag_dark_matter = 100 WHERE id = ?", (aid,))
    db.connect().commit()
    added = decrypt_json(boss_c.post("/world/1/challenge_event/make_sure_for_add_active_score"))
    assert added["current_alliance_active_score"] == 5 and added["alliance_storage"][0]["number"] == 60
    # the next day the points become bonuses and an activist reward
    with arena.event(now) as ev:
        start = ev["start"]
    with arena.event(start + 3600 + 86400 + 5) as ev:
        a = arena._alliance_data(ev, aid, start + 3600 + 86400 + 5)
        assert a["buff"] == {"active": round(5 / 4 / 100, 4), "morale": 0.01} and a["active"] == 0
