"""Galaxy Challenge: joining, fighting the G.E.F., leaders, assistance, battles, exchange, rewards."""

import random
import time

from supernova import db
from supernova import galaxy_challenge as gc
from supernova import planet as planet_rules

from .test_acs import member
from .test_server import decrypt_json


def alliance_at_war(tag: str, extra: int = 1):
    """A president and `extra` members (all with ships) in a running event."""
    boss_c, boss = member(f"gc-{tag}-boss", {4: 100, 1: 500})
    aid = decrypt_json(boss_c.post("/world/1/alliances.json", content=f"alliance_name=GC{tag}&description="))["alliance_id"]
    decrypt_json(boss_c.post(f"/world/1/alliances/{aid}/auto_in.json", content="status=1"))
    others = []
    for i in range(extra):
        c, me = member(f"gc-{tag}-{i}", {3: 50})
        decrypt_json(c.post("/world/1/alliance_applications.json", content=f"alliance_id={aid}"))
        others.append((c, me))
    event_id = gc.start(time.time() - 5)
    return event_id, aid, (boss_c, boss), others


def test_status_before_and_without_event():
    c, me = member("gc-nobody", {})
    gc.start(time.time() + 3600)
    view = decrypt_json(c.get("/world/1/gc/current"))
    assert view["status"] == gc.NOT_STARTED and view["next"]["start_time"] > time.time()
    assert view["self_info"]["in_battle"] is False


def test_declare_war_and_attack():
    event_id, aid, (boss_c, boss), [(mc, mem)] = alliance_at_war("war")
    view = decrypt_json(boss_c.get("/world/1/gc/current"))
    assert view["status"] == gc.BATTLE and view["current"]["id"] == event_id
    assert view["self_info"]["alliance"]["role"] == 2 and view["self_info"]["alliance"]["in_battle"] is False
    # a member cannot declare war
    assert "not joined" in mc.post(f"/world/1/gc/{event_id}/join").text
    joined = decrypt_json(boss_c.post(f"/world/1/gc/{event_id}/join"))
    assert joined["self_info"]["in_battle"] is True and len(joined["npcs"]) == 6
    # the strongest members joined with the president
    assert decrypt_json(mc.get("/world/1/gc/current"))["self_info"]["in_battle"] is True
    assert "already joined" in mc.post(f"/world/1/gc/{event_id}/join").text

    hit = decrypt_json(boss_c.post(f"/world/1/gc/{event_id}/attack/1"))
    assert hit["result"]["result"] == "win" and hit["result"]["score"] == 10  # the scouts are always beaten
    assert hit["result"]["user_resources"] == [[1, 10], [2, 3], [3, 2]]
    assert hit["user_info"]["curr_phy"] == gc.MAX_ENERGY - gc.ATTACK_ENERGY and hit["user_info"]["streak"] == 1
    again = decrypt_json(boss_c.post(f"/world/1/gc/{event_id}/attack/1"))
    assert again["result"]["score"] == 11  # the winning streak gives more points

    ranks = decrypt_json(boss_c.get(f"/world/1/gc/{event_id}/rank?rank_type=personal"))
    assert ranks["ranks"][0][1] == boss["username"] and ranks["ranks"][0][4] == 21
    assert ranks["self_rank"]["rank"] == 1 and ranks["self_rank"]["next_range"] == -1
    logs = decrypt_json(boss_c.get(f"/world/1/gc/{event_id}/logs?page=1"))
    assert [entry["score"] for entry in logs["logs"]] == [11, 10]


def test_energy_runs_out_and_can_be_bought():
    event_id, _, (boss_c, boss), _ = alliance_at_war("energy", extra=0)
    decrypt_json(boss_c.post(f"/world/1/gc/{event_id}/join"))
    for _ in range(gc.MAX_ENERGY // gc.ATTACK_ENERGY):
        decrypt_json(boss_c.post(f"/world/1/gc/{event_id}/attack/1"))
    assert "energy" in boss_c.post(f"/world/1/gc/{event_id}/attack/1").text
    assert "credits" in boss_c.post(f"/world/1/gc/{event_id}/add_power", content="power_num=100").text
    with db.player_state(boss["user_id"]) as s:
        s["empire"]["gu_count"] = 500
    bought = decrypt_json(boss_c.post(f"/world/1/gc/{event_id}/add_power", content="power_num=100"))
    assert bought["user_info"]["curr_phy"] >= 100 and bought["empire"]["gu_count"] == 301


def test_double_strike_and_assistance():
    event_id, aid, (boss_c, boss), [(mc, mem)] = alliance_at_war("assist")
    decrypt_json(boss_c.post(f"/world/1/gc/{event_id}/join"))
    with db.player_state(boss["user_id"]) as s:
        s["empire"]["gu_count"] = 100
    assert decrypt_json(boss_c.get(f"/world/1/gc/{event_id}/npc_list"))["npcs"][5]["can_ask_help"] is True
    sent = decrypt_json(boss_c.post(f"/world/1/gc/{event_id}/invite", content="npc_id=6"))
    assert "1 members" in sent["result"]
    requests = decrypt_json(mc.get(f"/world/1/gc/{event_id}/invitations"))["invitations"]
    assert requests[0]["invitor_name"] == boss["username"]
    decrypt_json(mc.post(f"/world/1/gc/{event_id}/accept", content=f"invitation_id={requests[0]['id']}&invitations=0"))
    info = decrypt_json(boss_c.get("/world/1/gc/current"))["self_info"]
    assert info["attack_bonus_to_elite"] == gc.ASSIST_BONUS

    now = time.time()
    with gc.event(now) as ev:
        p = gc._player(ev, boss["user_id"])
        result = gc.attack(ev, boss["user_id"], 6, True, now, random.Random(1))
        assert p["elite_bonus"] == 0 and p["energy"] == gc.MAX_ENERGY - gc.DOUBLE_ENERGY
        assert result["result"] == "win" and result["score"] == 4 * 120  # a double strike gives 4x
        assert gc._player(ev, mem["user_id"])[gc.ORE] == gc.ASSIST_REWARD


def test_leaders_raids_and_battle_result():
    event_id, aid, (boss_c, boss), [(mc, mem)] = alliance_at_war("lead")
    decrypt_json(boss_c.post(f"/world/1/gc/{event_id}/join"))
    assert "application" in decrypt_json(mc.post(f"/world/1/gc/{event_id}/apply_leader"))["result"]
    applies = decrypt_json(boss_c.get(f"/world/1/gc/{event_id}/leader_applies"))["applies"]
    assert applies[0]["name"] == mem["username"]
    done = decrypt_json(boss_c.post(f"/world/1/gc/{event_id}/set_leader", content=f"user_id={mem['user_id']}"))
    assert done["self_info"]["alliance"]["leaders"][0]["name"] == mem["username"]
    leader = decrypt_json(mc.get("/world/1/gc/current"))["self_info"]
    assert leader["leader"] is True and leader["shadow_attack_bonus"] == gc.LEADER_BONUS
    with db.player_state(mem["user_id"]) as s:
        s["empire"]["gu_count"] = gc.COURAGE_PRICE
    assert decrypt_json(mc.post(f"/world/1/gc/{event_id}/add_courage"))["user_info"]["coura"] == gc.MAX_COURAGE
    decrypt_json(boss_c.post(f"/world/1/gc/{event_id}/attack/1"))

    with gc.event(time.time()) as ev:
        start = ev["start"]
    with gc.event(start + 601) as ev:  # the first G.E.F. attack hits the leader
        assert gc._player(ev, mem["user_id"])["courage"] == gc.MAX_COURAGE - gc.COURAGE_PER_RAID
        assert gc._player(ev, mem["user_id"])["logs"][0]["result"] in ("Held", "Fell")
    with gc.event(start + 3601) as ev:  # the first battle is over
        a = ev["alliances"][str(aid)]
        assert a["last"]["round"] == 0 and a["leaders"] == [] and a["score"] == a["last"]["score"] > 0
    last = decrypt_json(boss_c.get(f"/world/1/gc/{event_id}/last_battle"))
    assert last["user_alliance"]["scores"]["total"] > 0 and "last" in last["npc_alliance"]["scores"]
    assert decrypt_json(boss_c.get(f"/world/1/gc/{event_id}/alliance_logs?page=1"))["logs"]


def test_tasks_exchange_and_rewards():
    event_id, aid, (boss_c, boss), _ = alliance_at_war("reward", extra=0)
    decrypt_json(boss_c.post(f"/world/1/gc/{event_id}/join"))
    for _ in range(10):
        decrypt_json(boss_c.post(f"/world/1/gc/{event_id}/attack/1"))
    daily = decrypt_json(boss_c.get("/world/1/gc_quest/daily"))["list"][0]["list"]
    wins = next(t for t in daily if t["id"] == 1)
    assert wins["status"] == 1 and wins["process"] == 10
    claimed = decrypt_json(boss_c.post("/world/1/gc_quest/receive_reward", content="id=1"))
    assert "Crystal Ore" in claimed["info"]
    assert all(t["id"] != 1 for t in decrypt_json(boss_c.get("/world/1/gc_quest/daily"))["list"][0]["list"])

    offer = decrypt_json(boss_c.get("/world/1/gc/fleets"))
    assert offer["resources"]["diamond_mineral"]["amount"] == 100 + 100
    assert {f["fleet_type"] for f in offer["fleets"]} == {gc.DEVOURER, gc.FRIGATE}
    assert "Not enough" in boss_c.post("/world/1/gc/exchange_fleets", content=f"type={gc.DEVOURER}&amount=1").text
    with gc.event(time.time()) as ev:
        gc._player(ev, boss["user_id"]).update(diamond_mineral=2000, energy_gel=500)
    done = decrypt_json(boss_c.post("/world/1/gc/exchange_fleets", content=f"type={gc.DEVOURER}&amount=1"))
    assert done["success"] and next(f for f in done["fleets"] if f["fleet_type"] == gc.DEVOURER)["amount"] == 1

    dark_matter = db.load_state(boss["user_id"])["empire"].get("dark_matter_count", 0)
    gc.stop(time.time())
    assert decrypt_json(boss_c.get("/world/1/gc/current"))["status"] == gc.FINISHED
    empire = db.load_state(boss["user_id"])["empire"]
    assert empire["dark_matter_count"] == dark_matter + 300 + 200  # rank 1 personally and as alliance
    assert planet_rules.fleet(empire["planets"][0], gc.FRIGATE)["amount"] == 2
    assert "Not enough" in boss_c.post("/world/1/gc/exchange_fleets", content=f"type={gc.FRIGATE}&amount=1").text
    assert decrypt_json(boss_c.get(f"/world/1/gc/{event_id}/score"))["one_users"][0]["awards"][0]["amount"] == 300


def test_next_range():
    tiers = gc.PERSONAL_REWARDS
    scores = [500, 400, 300, 200, 100]
    assert gc._next_range(scores, 0, tiers) == -1
    assert gc._next_range(scores, 1, tiers) == 101  # to pass the first
    assert gc._next_range(scores, 4, tiers) == 201  # rank 5 needs the 3rd place
    assert gc._next_range(scores, None, tiers) == 1
