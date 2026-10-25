"""Conquer The Universe: the arena behind the star gate, following the client's help texts.

- The operator starts an event (`admin arena-start`). It has three stages: garrison, challenge
  and prize. While it runs, the star gate opens the arena instead of the star fortresses (the
  client decides this from `challenge_events`); afterwards the fortresses are back.
- Players garrison ships of six types from their current planet. The garrison keeps its power
  even when the ships leave. A new garrison must be stronger than the old one (a weaker one
  costs credits). The first garrison puts the player at the bottom of the rank list.
- In the challenge stage players challenge higher ranks with the same six ship types; a win
  swaps the places. 3 challenges a day are free, 2 more can be bought; after a challenge there
  is a cool-down that credits skip. Battles compare the ships' basic attributes (no research)
  and lose no ships.
- Ranks earn a daily prize (points, dark matter, at the top credits) and a final prize. Points
  (at most 1,000,000, kept between events) buy items in the arena shop.
- Alliances: every challenge gives the alliance an activist point (5 per member and day), a
  member's first win of the day a morale point. Each day 4 activist points become 1 % weapons
  and each morale point 1 % armour for the alliance's arena battles; the president and
  vice-presidents can buy points from the treasury. Members of an active alliance get a daily
  activist reward.

The rank list is filled with robot fleets so that a small server has opponents. All numbers
the texts do not give are Supernova's own.
"""

import json
import random
import threading
from contextlib import contextmanager

from . import alliances, combat, db, items
from . import economy as eco
from . import planet as planet_rules

KIND = "arena"
SHIP_TYPES = (2, 3, 4, 5, 6, 7)  # texts: 6 ship types per event
GARRISON, CHALLENGE, PRIZE = 1, 2, 3  # stages (challenge_status.status)
ROBOTS = 100
WORLD = "S1"
FREE_CHALLENGES, MAX_CHALLENGES = 3, 5  # texts: the first 3 free, at most 5 a day
CHALLENGE_PRICE = 20  # credits for one more challenge
COOL_DOWN = 600  # seconds between challenges
SPEED_PER_MIN = 1  # credits per minute of cool-down
REFRESH_PRICE = 5  # credits for new opponents
WEAKER_GARRISON_PRICE = 50  # credits for a garrison weaker than the last one
OPPONENTS = 4
MAX_POINTS = 1_000_000  # texts
WIN_POINTS, LOSS_POINTS = 20, 5
ACTIVE_PER_DAY, ACTIVE_PER_PERCENT = 5, 4  # texts
MAX_ACTIVE, MAX_MORALE = 80, 20  # points an alliance can collect a day (20 % each)
BUY_ACTIVE, BUY_MORALE = (4, 40), (1, 40)  # (points, dark matter from the treasury)
ACTIVIST_REWARD = {"points": 20, "dark_matter": 5}
LOG_LENGTH = 50

POINTS, CREDITS, DARK_MATTER = "1", "2", "3"  # reward keys of the client
DAILY_PRIZES = [(1, 1, 300, 0, 50), (2, 2, 250, 0, 40), (3, 3, 200, 0, 30), (4, 10, 150, 0, 20),
                (11, 50, 100, 0, 10), (51, 100, 60, 0, 5), (101, 10**9, 30, 0, 1)]  # ranks, points, credits, DM
# (the client's rank list shows two rewards per rank, so every rank gets points and dark matter)
FINAL_PRIZES = [(1, 1, 1500, 100, 250), (2, 2, 1200, 50, 200), (3, 3, 1000, 25, 150), (4, 10, 700, 0, 100),
                (11, 50, 400, 0, 50), (51, 100, 200, 0, 20), (101, 10**9, 100, 0, 5)]

SHOP = [  # id, item id or None, fleet type or None, batch, price in points, per event
    (1, 3101, None, 1, 200, 10),  # ECM chips x5
    (2, 3103, None, 1, 150, 10),  # krypton stones x500
    (3, 3102, None, 1, 150, 10),  # alloy box
    (4, 2103, None, 1, 100, 20),  # 1 h speed-up
    (5, None, 25, 1, 1500, 5),  # Devourer Interceptor
    (6, None, 26, 1, 2500, 3),  # Titanenergetic Frigate
]


class ArenaError(planet_rules.GameError):
    pass


_lock = threading.RLock()  # taken after a player's state, never before


def _latest():
    return db.connect().execute("SELECT * FROM events WHERE kind = ? ORDER BY id DESC LIMIT 1", (KIND,)).fetchone()


def start(at: float, garrison: int, challenge: int, prize: int, day: int = 86400) -> int:
    """Schedule a new event: stage lengths in seconds; daily prizes every `day` seconds."""
    ev = {"start": int(at), "garrison": int(garrison), "challenge": int(challenge), "prize": int(prize),
          "day": max(60, int(day)), "ranks": [{"robot": r} for r in range(1, ROBOTS + 1)], "players": {},
          "alliances": {}, "daily": None, "final": None, "reports": {}, "next_report": 1}
    conn = db.connect()
    with db.lock(), conn:
        return conn.execute("INSERT INTO events (kind, data) VALUES (?, ?)", (KIND, json.dumps(ev))).lastrowid


def stop(now: float) -> None:
    """End the challenge stage now; the prize stage follows."""
    with event(now) as ev:
        if ev is None or stage(ev, now) not in (GARRISON, CHALLENGE):
            raise ArenaError("No arena is running")
        ev["challenge"] = max(0, int(now) - ev["start"] - ev["garrison"])
        _advance(ev, now)


@contextmanager
def event(now: float):
    """The latest event, brought up to `now`; saved when the block ends without error."""
    with _lock:
        row = _latest()
        if row is None:
            yield None
            return
        ev = dict(json.loads(row["data"]), id=row["id"])
        _advance(ev, now)
        yield ev
        conn = db.connect()
        with db.lock(), conn:
            conn.execute("UPDATE events SET data = ? WHERE id = ?",
                         (json.dumps({k: v for k, v in ev.items() if k != "id"}), row["id"]))


def _times(ev: dict) -> tuple[int, int, int]:
    challenge_start = ev["start"] + ev["garrison"]
    prize_start = challenge_start + ev["challenge"]
    return challenge_start, prize_start, prize_start + ev["prize"]


def stage(ev: dict | None, now: float) -> int:
    """0 when no arena runs."""
    if ev is None or now < ev["start"]:
        return 0
    challenge_start, prize_start, end = _times(ev)
    if now < challenge_start:
        return GARRISON
    if now < prize_start:
        return CHALLENGE
    return PRIZE if now < end else 0


def running(now: float) -> bool:
    with event(now) as ev:
        return stage(ev, now) != 0


# --- Fleets, robots and ranks ------------------------------------------------------------------


def capacity(units: dict) -> int:
    """The power the client shows: weapons + shields + a tenth of the hull of every ship."""
    total = 0
    for t, n in units.items():
        s = combat.STATS[int(t)]
        total += (s.weapon + s.shield + s.hull // 10) * n
    return total


def _robot_units(rank: int) -> dict[int, int]:
    f = ((ROBOTS + 1 - rank) / ROBOTS) ** 2
    return {4: max(1, int(800 * f)), 3: max(1, int(1500 * f)), 2: max(2, int(3000 * f))}


def _robot_name(rank: int) -> str:
    return f"G.E.F. Drone {rank:03d}"


def _fighting_fleets(planet: dict) -> dict[int, int]:
    return {t: planet_rules.fleet(planet, t)["amount"] for t in SHIP_TYPES if planet_rules.fleet(planet, t)["amount"]}


def _rank_of(ev: dict, pid: int) -> int | None:
    return next((i + 1 for i, e in enumerate(ev["ranks"]) if e.get("pid") == pid), None)


def _entry_units(ev: dict, entry: dict) -> dict[int, int]:
    if "robot" in entry:
        return _robot_units(entry["robot"])
    return {int(t): n for t, n in ev["players"][str(entry["pid"])]["garrison"].items()}


def _player(ev: dict, pid: int) -> dict:
    return ev["players"].setdefault(str(pid), {"garrison": {}, "highest": 0, "defends": 0, "day": -1,
                                               "challenges": 0, "bought": 0, "cool_until": 0, "opponents": [],
                                               "logs": [], "active_today": 0, "won_today": False,
                                               "last_active": 0, "month_active": 0, "claimed_reward_day": -1,
                                               "final_claimed": False, "bought_items": {}})


def _username(pid: int) -> str:
    row = db.connect().execute("SELECT username FROM players WHERE id = ?", (pid,)).fetchone()
    return row["username"] if row else "?"


def _entry_view(ev: dict, rank: int, now: float) -> dict:
    entry = ev["ranks"][rank - 1]
    units = _entry_units(ev, entry)
    robot = "robot" in entry
    view = {"rank": rank, "is_robot": robot, "server": WORLD, "defending_capacity": capacity(units),
            "defending_fleets_info": {str(t): n for t, n in units.items()}, "reward": _prize(DAILY_PRIZES, rank)}
    if robot:
        view.update(user_name=_robot_name(entry["robot"]), user_id=0, defend_num=0,
                    defender_alliance_buff={"active": 0, "morale": 0})
    else:
        pid = entry["pid"]
        view.update(user_name=_username(pid), user_id=pid, defend_num=ev["players"][str(pid)]["defends"],
                    defender_alliance_buff=_buff(ev, pid, now))
    return view


def _prize(table: list, rank: int) -> dict:
    for first, last, points, credits, dark_matter in table:
        if first <= rank <= last:
            return {POINTS: points, CREDITS: credits, DARK_MATTER: dark_matter}
    return {POINTS: 0, CREDITS: 0, DARK_MATTER: 0}


# --- Days, alliances and prizes ------------------------------------------------------------------


def _day(ev: dict, now: float) -> int:
    return int((now - ev["start"]) // ev["day"])


def _today(ev: dict, p: dict, now: float) -> None:
    day = _day(ev, now)
    if p["day"] != day:
        p.update(day=day, challenges=0, bought=0, active_today=0, won_today=False)


def _alliance_data(ev: dict, aid: int, now: float) -> dict:
    a = ev["alliances"].setdefault(str(aid), {"day": _day(ev, now), "active": 0, "morale": 0,
                                              "buff": {"active": 0, "morale": 0}})
    day = _day(ev, now)
    if a["day"] != day:  # texts: points become bonuses each day and are cleared
        a["buff"] = {"active": round(a["active"] / ACTIVE_PER_PERCENT / 100, 4) if a["day"] == day - 1 else 0,
                     "morale": round(a["morale"] / 100, 4) if a["day"] == day - 1 else 0}
        a.update(day=day, active=0, morale=0)
    return a


def _alliance_id(pid: int) -> int | None:
    m = alliances.membership(pid)
    return m["alliance_id"] if m else None


def _buff(ev: dict, pid: int, now: float) -> dict:
    aid = _alliance_id(pid)
    return dict(_alliance_data(ev, aid, now)["buff"]) if aid else {"active": 0, "morale": 0}


def _snapshot(ev: dict) -> dict[str, int]:
    return {str(e["pid"]): i + 1 for i, e in enumerate(ev["ranks"]) if "pid" in e}


def _advance(ev: dict, now: float) -> None:
    """Daily prize ranks during the challenge stage and the final ranks when it ends."""
    challenge_start, prize_start, _ = _times(ev)
    if challenge_start <= now:
        node = min(int((min(now, prize_start) - challenge_start) // ev["day"]), 10**6)
        if node >= 1 and (ev["daily"] is None or ev["daily"]["node"] < node):
            ev["daily"] = {"node": node, "ranks": _snapshot(ev), "claimed": []}
    if now >= prize_start and ev["final"] is None:
        ev["final"] = _snapshot(ev)


def _next_node(ev: dict, now: float) -> int:
    challenge_start, prize_start, _ = _times(ev)
    if now < challenge_start:
        return challenge_start + ev["day"]
    return challenge_start + (int((now - challenge_start) // ev["day"]) + 1) * ev["day"]


def _yesterday_rank(ev: dict, pid: int) -> int:
    d = ev["daily"]
    if not d or str(pid) not in d["ranks"] or pid in d["claimed"]:
        return 0
    return d["ranks"][str(pid)]


def _grant(state: dict, prize: dict) -> str:
    empire = state["empire"]
    state["arena_points"] = min(MAX_POINTS, state.get("arena_points", 0) + prize[POINTS])
    empire["gu_count"] = empire.get("gu_count", 0) + prize[CREDITS]
    empire["dark_matter_count"] = empire.get("dark_matter_count", 0) + prize[DARK_MATTER]
    return {key: n for key, n in prize.items() if n}


def daily_prize(ev: dict, state: dict, now: float) -> dict:
    pid = state["empire"]["user_id"]
    rank = _yesterday_rank(ev, pid)
    if not rank:
        raise ArenaError("There is no prize to collect")
    ev["daily"]["claimed"].append(pid)
    return _grant(state, _prize(DAILY_PRIZES, rank))


def final_prize(ev: dict, state: dict, now: float) -> dict:
    pid = state["empire"]["user_id"]
    p = _player(ev, pid)
    rank = (ev["final"] or {}).get(str(pid))
    if stage(ev, now) != PRIZE or not rank or p["final_claimed"]:
        raise ArenaError("There is no prize to collect")
    p["final_claimed"] = True
    return _grant(state, _prize(FINAL_PRIZES, rank))


# --- Views -----------------------------------------------------------------------------------


def self_info(ev: dict, state: dict, now: float) -> dict:
    pid = state["empire"]["user_id"]
    p = _player(ev, pid)
    _today(ev, p, now)
    rank = _rank_of(ev, pid)
    m = alliances.membership(pid)
    aid = m["alliance_id"] if m else None
    a = _alliance_data(ev, aid, now) if aid else None
    fighting = _fighting_fleets(state["empire"]["main_planet"])
    left = FREE_CHALLENGES - p["challenges"] if p["challenges"] < FREE_CHALLENGES else 0
    challenge_start = _times(ev)[0]
    wait = challenge_start if stage(ev, now) == GARRISON else max(int(now), p["cool_until"])
    return {
        "user_id": pid, "user_name": state["username"], "server_name": WORLD,
        "current_rank": rank or len(ev["ranks"]) + 1, "highest_rank": p["highest"] or len(ev["ranks"]) + 1,
        "rank_list_max": len(ev["ranks"]), "defend_num": p["defends"],
        "defending_capacity": capacity(p["garrison"]), "fighting_capacity": capacity(fighting),
        "defending_fleets_info": dict(p["garrison"]) or "", "fighting_fleets_info": {str(t): n for t, n in fighting.items()} or "",
        "reward": _prize(DAILY_PRIZES, rank or len(ev["ranks"]) + 1), "alliance_buff": _buff(ev, pid, now),
        "hit_point_last_times": left, "hp_wait_time": wait, "fight_logs_num": len(p["logs"]),
        "yesterday_rank": _yesterday_rank(ev, pid), "ultimate_reward": p["final_claimed"] or not (ev["final"] or {}).get(str(pid)),
        "activity_score": state.get("arena_points", 0), "speed_per_min": SPEED_PER_MIN,
        "alliance_info": {"has_alliance": bool(m), "alliance_name": state["empire"].get("alliance_name", ""),
                          "position": m["post"] if m else 0},
        "alliance_reward": bool(a and a["buff"]["active"] > 0 and p["claimed_reward_day"] != _day(ev, now)),
    }


def _opponents(ev: dict, pid: int, rng: random.Random | None = None) -> list[int]:
    rank = _rank_of(ev, pid)
    if not rank or rank == 1:
        return []
    span = max(OPPONENTS + 1, rank // 10)
    candidates = list(range(max(1, rank - span), rank))
    rng = rng or random.Random()
    return sorted(rng.sample(candidates, min(OPPONENTS, len(candidates))))


def overview(ev: dict, state: dict, now: float) -> dict:
    pid = state["empire"]["user_id"]
    p = _player(ev, pid)
    rank = _rank_of(ev, pid)
    if rank and (not p["opponents"] or any(r >= rank for r in p["opponents"])):
        p["opponents"] = _opponents(ev, pid)
    end = {GARRISON: _times(ev)[0], CHALLENGE: _times(ev)[1], PRIZE: _times(ev)[2]}.get(stage(ev, now), int(now))
    return {"switch_status": True,
            "challenge_status": {"status": stage(ev, now), "current_time": int(now), "last_time": end},
            "daily_reward_info": {"time_nodes": _next_node(ev, now)},
            "self_info": self_info(ev, state, now),
            "challenge_info": challenge_info(ev, pid, now)}


def challenge_info(ev: dict, pid: int, now: float) -> dict:
    p = _player(ev, pid)
    top = {str(r): _entry_view(ev, r, now) for r in range(1, min(10, len(ev["ranks"])) + 1)}
    above = {str(r): _entry_view(ev, r, now) for r in p["opponents"] if r <= len(ev["ranks"])}
    return {"top_ten_info": top, "above_info": above}


# --- Garrison and challenges -------------------------------------------------------------------


def garrison(ev: dict, state: dict, now: float, pay: bool = False) -> dict:
    """Garrison the six ship types of the current planet. Returns the client's answer; without
    `pay` a weaker garrison is only offered (`{"msg"}` without `hp`)."""
    if stage(ev, now) not in (GARRISON, CHALLENGE):
        raise ArenaError("The garrison is closed")
    pid = state["empire"]["user_id"]
    p = _player(ev, pid)
    units = _fighting_fleets(state["empire"]["main_planet"])
    if not units:
        raise ArenaError("This planet has none of the ships of this event")
    new, old = capacity(units), capacity(p["garrison"])
    if new <= old and not pay:
        return {"msg": f"The new garrison ({new:,}) is not stronger than the old one ({old:,}). "
                       f"Replace it for {WEAKER_GARRISON_PRICE} credits?"}
    if new <= old:
        _pay(state, WEAKER_GARRISON_PRICE)
    p["garrison"] = {str(t): n for t, n in units.items()}
    if _rank_of(ev, pid) is None:
        ev["ranks"].append({"pid": pid})
        p["highest"] = p["highest"] or len(ev["ranks"])
    p["opponents"] = []
    return {"msg": f"Your garrison has {new:,} power. Rank: {_rank_of(ev, pid)}",
            "defending_fleets_info": dict(p["garrison"]),
            "update": {"challenge_fleet_amount": dict(p["garrison"]), "challenge_fleet_capacity": new},
            "hp": {"hit_point_last_times": max(0, FREE_CHALLENGES - p["challenges"]),
                   "hp_wait_time": max(int(now), p["cool_until"]), "defending_capacity": new,
                   "current_time": int(now), "dark_matter": 0}}


def _pay(state: dict, credits: int) -> None:
    empire = state["empire"]
    if empire.get("gu_count", 0) < credits:
        raise ArenaError(f"You need {credits:,} credits")
    empire["gu_count"] -= credits


def check_challenge(ev: dict, state: dict, target: int, now: float) -> str | None:
    """None if the challenge can start now; else what the player has to pay for first."""
    pid = state["empire"]["user_id"]
    if stage(ev, now) != CHALLENGE:
        raise ArenaError("Challenges are not open")
    p = _player(ev, pid)
    _today(ev, p, now)
    rank = _rank_of(ev, pid)
    if rank is None:
        raise ArenaError("Garrison ships first to get a rank")
    if not 1 <= target < rank:
        raise ArenaError("You can only challenge higher ranks")
    if p["challenges"] >= FREE_CHALLENGES + p["bought"]:
        if p["challenges"] >= MAX_CHALLENGES:
            raise ArenaError(f"You can challenge {MAX_CHALLENGES} times a day")
        return "buy"
    if p["cool_until"] > now:
        return "cool"
    return None


def cool_down_price(ev: dict, pid: int, now: float) -> int:
    left = _player(ev, pid)["cool_until"] - now
    return max(0, -(-int(left) // 60)) * SPEED_PER_MIN


def skip_cool_down(ev: dict, state: dict, now: float) -> None:
    p = _player(ev, state["empire"]["user_id"])
    price = cool_down_price(ev, state["empire"]["user_id"], now)
    if price:
        _pay(state, price)
    p["cool_until"] = 0


def buy_challenge(ev: dict, state: dict, now: float) -> None:
    p = _player(ev, state["empire"]["user_id"])
    _today(ev, p, now)
    if FREE_CHALLENGES + p["bought"] >= MAX_CHALLENGES:
        raise ArenaError(f"You can challenge {MAX_CHALLENGES} times a day")
    _pay(state, CHALLENGE_PRICE)
    p["bought"] += 1


def challenge(ev: dict, state: dict, target: int, now: float, rng: random.Random | None = None) -> dict:
    """Fight the fleet at rank `target`; a win swaps the places."""
    if check_challenge(ev, state, target, now) is not None:
        raise ArenaError("Pay for the challenge first")
    pid = state["empire"]["user_id"]
    p = _player(ev, pid)
    units = _fighting_fleets(state["empire"]["main_planet"])
    if not units:
        raise ArenaError("This planet has none of the ships of this event")
    rank = _rank_of(ev, pid)
    entry = ev["ranks"][target - 1]
    defender_units = _entry_units(ev, entry)
    attacker_buff, defender_buff = _buff(ev, pid, now), (_buff(ev, entry["pid"], now) if "pid" in entry
                                                         else {"active": 0, "morale": 0})
    result = combat.fight(
        combat.Side("challenger", dict(units), attack_percent=int(attacker_buff["active"] * 100),
                    armour_percent=int(attacker_buff["morale"] * 100)),
        combat.Side("defender", dict(defender_units), attack_percent=int(defender_buff["active"] * 100),
                    armour_percent=int(defender_buff["morale"] * 100)),
        rng or random.Random())
    won = result["winner"] == "attacker"
    p["challenges"] += 1
    p["cool_until"] = int(now) + COOL_DOWN
    p["opponents"] = []
    if won:
        ev["ranks"][target - 1], ev["ranks"][rank - 1] = ev["ranks"][rank - 1], ev["ranks"][target - 1]
        p["highest"] = min(p["highest"] or target, target)
    elif "pid" in entry:
        ev["players"][str(entry["pid"])]["defends"] += 1
    _activity(ev, pid, won, now)
    points = WIN_POINTS if won else LOSS_POINTS
    state["arena_points"] = min(MAX_POINTS, state.get("arena_points", 0) + points)
    new_rank = _rank_of(ev, pid)
    log = {"round": result["rounds"], "attacker_alliance_buff": attacker_buff, "defender_alliance_buff": defender_buff,
           "survived_attacker_power": {"challenge_fleet_capacity": capacity(result["attacker_left"])},
           "survived_defender_power": {"challenge_fleet_capacity": capacity(result["defender_left"])}}
    report_id = ev["next_report"]
    ev["next_report"] += 1
    ev["reports"][str(report_id)] = log
    for old in sorted(ev["reports"], key=int)[:-LOG_LENGTH * 4]:
        del ev["reports"][old]
    entry_name = _username(entry["pid"]) if "pid" in entry else _robot_name(entry["robot"])
    record = {"results_of_battle": won, "fighting_time": int(now), "challenger_fighting_capacity": capacity(units),
              "defender_defending_capacity": capacity(defender_units), "att_original_rank": rank,
              "def_original_rank": target, "report_id": report_id}
    _log(p, dict(record, opponent_type=0, opponent_server_name=WORLD, opponent_user_name=entry_name))
    if "pid" in entry:
        _log(ev["players"][str(entry["pid"])], dict(record, results_of_battle=not won, opponent_type=1,
                                                    opponent_server_name=WORLD, opponent_user_name=state["username"]))
    return dict(log, win="win" if won else "lose", report_id=report_id, defend=False,
                challenger={"user_name": state["username"], "current_rank": new_rank},
                defender={"user_name": entry_name, "defending_fleets_info": {str(t): n for t, n in defender_units.items()}},
                reward={POINTS: 0}, activity_score=points, battle_log=log)


def _log(p: dict, record: dict) -> None:
    p["logs"].insert(0, record)
    del p["logs"][LOG_LENGTH:]


def _activity(ev: dict, pid: int, won: bool, now: float) -> None:
    p = _player(ev, pid)
    p["last_active"] = int(now)
    p["month_active"] += 1
    aid = _alliance_id(pid)
    if not aid:
        return
    a = _alliance_data(ev, aid, now)
    if p["active_today"] < ACTIVE_PER_DAY and a["active"] < MAX_ACTIVE:
        p["active_today"] += 1
        a["active"] += 1
    if won and not p["won_today"] and a["morale"] < MAX_MORALE:
        p["won_today"] = True
        a["morale"] += 1


def refresh(ev: dict, state: dict, now: float) -> None:
    pid = state["empire"]["user_id"]
    if _rank_of(ev, pid) is None:
        raise ArenaError("Garrison ships first to get a rank")
    _pay(state, REFRESH_PRICE)
    _player(ev, pid)["opponents"] = _opponents(ev, pid)


def battle_logs(ev: dict, pid: int) -> list[dict]:
    return _player(ev, pid)["logs"]


def report(ev: dict, report_id: int) -> dict:
    log = ev["reports"].get(str(report_id))
    if log is None:
        raise ArenaError("This report is gone")
    return log


# --- Alliance activity -------------------------------------------------------------------------


def alliance_view(ev: dict, state: dict, now: float) -> dict:
    pid = state["empire"]["user_id"]
    m = alliances.membership(pid)
    if m is None:
        raise ArenaError("You haven't joined an alliance yet!")
    a = _alliance_data(ev, m["alliance_id"], now)
    members = db.connect().execute("SELECT player_id FROM alliance_members WHERE alliance_id = ?",
                                   (m["alliance_id"],)).fetchall()
    inactive = []
    for row in members:
        mp = _player(ev, row["player_id"])
        _today(ev, mp, now)
        if mp["active_today"] == 0:
            rank = _rank_of(ev, row["player_id"])
            inactive.append({"user_name": _username(row["player_id"]), "last_active_time": mp["last_active"] or None,
                             "is_challenge": rank is not None, "monthly_active_num": mp["month_active"],
                             "current_rank": rank or "-"})
    p = _player(ev, pid)
    treasury = db.connect().execute("SELECT bag_dark_matter, bag_credits FROM alliances WHERE id = ?",
                                    (m["alliance_id"],)).fetchone()
    return {"alliance_buff": dict(a["buff"]),
            "alliance_current_score": {"all_active": a["active"], "max_active_score": MAX_ACTIVE,
                                       "all_morale": a["morale"], "max_morale_score": MAX_MORALE,
                                       "membership_active": len(members) - len(inactive),
                                       "membership_morale": sum(1 for r in members
                                                                if _player(ev, r["player_id"])["won_today"])},
            "my_current_score": {"active_score": p["active_today"], "morale_score": int(p["won_today"])},
            "alliance_storage": [{"number": treasury["bag_dark_matter"]}, {"number": treasury["bag_credits"]}],
            "inactive_membership": inactive, "planet": {"current_server_time": int(now)}}


def buy_alliance_points(ev: dict, pid: int, morale: bool, now: float) -> dict:
    if stage(ev, now) != CHALLENGE:
        raise ArenaError("Challenges are not open")
    m = alliances.membership(pid)
    if m is None:
        raise ArenaError("You haven't joined an alliance yet!")
    a = _alliance_data(ev, m["alliance_id"], now)
    points, price = BUY_MORALE if morale else BUY_ACTIVE
    key, limit = ("morale", MAX_MORALE) if morale else ("active", MAX_ACTIVE)
    if a[key] >= limit:
        raise ArenaError("Your alliance has the most points for today")
    try:
        alliances.spend(pid, m["alliance_id"], alliances.DARK_MATTER, price,
                        "Arena morale" if morale else "Arena activist points")
    except alliances.AllianceError as e:
        raise ArenaError(str(e)) from e
    a[key] = min(limit, a[key] + points)
    treasury = db.connect().execute("SELECT bag_dark_matter, bag_credits FROM alliances WHERE id = ?",
                                    (m["alliance_id"],)).fetchone()
    return {"alliance_storage": [{"number": treasury["bag_dark_matter"]}, {"number": treasury["bag_credits"]}],
            "current_alliance_active_score": a["active"], "current_alliance_morale_score": a["morale"]}


def activist_reward(ev: dict, state: dict, now: float) -> str:
    pid = state["empire"]["user_id"]
    p = _player(ev, pid)
    aid = _alliance_id(pid)
    if not aid or _alliance_data(ev, aid, now)["buff"]["active"] <= 0 or p["claimed_reward_day"] == _day(ev, now):
        raise ArenaError("There is no activist reward to collect")
    p["claimed_reward_day"] = _day(ev, now)
    state["arena_points"] = min(MAX_POINTS, state.get("arena_points", 0) + ACTIVIST_REWARD["points"])
    state["empire"]["dark_matter_count"] = state["empire"].get("dark_matter_count", 0) + ACTIVIST_REWARD["dark_matter"]
    return f"Activist reward: {ACTIVIST_REWARD['points']} points and {ACTIVIST_REWARD['dark_matter']} dark matter"


# --- Shop --------------------------------------------------------------------------------------


def shop(ev: dict, state: dict) -> list[dict]:
    bought = _player(ev, state["empire"]["user_id"])["bought_items"]
    out = []
    for sid, item_id, ftype, batch, price, limit in SHOP:
        view = {"id": sid, "batch_count": batch, "sale_score": price, "challenge_id": ev["id"], "max_number": limit,
                "buy_amount": bought.get(str(sid), 0), "item_type": 1}
        if item_id:
            i = items.item(item_id)
            view.update(name=i.name, desc=i.desc, img=f"icons/{i.icon}")
        else:
            view.update(name=eco.SPECIAL_UNITS[ftype], fleet_type=ftype,
                        img="icons/item_res_14.png" if ftype == 25 else "icons/item_res_15.png")
        out.append(view)
    return out


def buy(ev: dict, state: dict, sid: int, n: int) -> dict:
    entry = next((s for s in SHOP if s[0] == sid), None)
    if entry is None or n < 1:
        raise ArenaError("This item is not available")
    _, item_id, ftype, batch, price, limit = entry
    bought = _player(ev, state["empire"]["user_id"])["bought_items"]
    if bought.get(str(sid), 0) + n > limit:
        raise ArenaError("You cannot buy that many in this event")
    if state.get("arena_points", 0) < price * n:
        raise ArenaError(f"You need {price * n:,} points")
    state["arena_points"] -= price * n
    bought[str(sid)] = bought.get(str(sid), 0) + n
    if item_id:
        items.add(state, item_id, batch * n)
        name = items.item(item_id).name
    else:
        planet_rules.fleet(state["empire"]["main_planet"], ftype)["amount"] += batch * n
        name = eco.SPECIAL_UNITS[ftype]
    return {"got_item": f"{batch * n}x {name}", "item_id": sid, "buy_amount": bought[str(sid)], "max_num": limit,
            "activity_score": state["arena_points"]}
