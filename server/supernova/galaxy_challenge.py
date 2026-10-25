"""The Galaxy Challenge ("Fight G.E.F."), following the client's help texts and the wiki:

- The operator starts an event (`admin gc-start`). It lasts 4 days with 96 battles of one hour.
- An alliance's president or vice-president declares war; the 30 members with the most power
  join automatically (more battle force can be bought with credits, up to 50). Other members
  sign up themselves while there is room.
- Attack and defense are taken from the player's ships at sign-up; no ships are lost. Every
  attack on the G.E.F. costs energy (1,000 at most, 1 back per minute, refills for credits).
  Wins give points and the event resources crystal ore, energy gel and titanenergetic cubes,
  which are exchanged for Devourer Interceptors and Titanenergetic Frigates.
- Up to 3 mission leaders per battle get better attributes and 5 % more points for their
  alliance. The G.E.F. attacks every 10 minutes: leaders with courage points (bought with
  credits) defend first, otherwise a random member. Successful defenses give points.
- After each battle the alliance's points (members + leader bonus) are compared with the rival
  G.E.F. fleet; a won battle gives 20 % more. Ranks are rewarded when the event ends.
- The event resources decompose 3 days after the end; until then ships can be exchanged.

Numbers the texts do not give (NPC strength, points, rewards, prices) are Supernova's own.
"""

import json
import random
import threading
from contextlib import contextmanager

from . import alliances, combat, db, fortresses, mails
from . import economy as eco
from . import planet as planet_rules

KIND = "galaxy_challenge"
ROUNDS, ROUND_SECONDS = 96, 3600  # texts: 4 days, one battle per hour
RAIDS_PER_ROUND = 6  # the G.E.F. attacks every 10 minutes
AFTERMATH = 3 * 86400  # texts: exchange for 3 more days, then the resources decompose
MAX_ENERGY, ENERGY_SECONDS = 1000, 60  # texts: at most 1,000, 1 back per minute
ATTACK_ENERGY, DOUBLE_ENERGY = 25, 100  # wiki: normal attack 25, double strike 100
DOUBLE_ATTACK = 200  # percent attack of a double strike, as the client shows; it gives 4x points and resources
ENERGY_PRICES = {100: 199, 1000: 1990}  # texts: credits for 100 / 1,000 energy
MAX_COURAGE, COURAGE_PER_RAID, COURAGE_PRICE = 100, 10, 100
BATTLE_FORCE, MAX_BATTLE_FORCE, BATTLE_FORCE_PRICE = 30, 50, 300
MAX_LEADERS, LEADER_BONUS, LEADER_POINTS = 3, 20, 5  # +20 % attributes; +5 % alliance points each
FORTRESS_BONUS = 10  # percent attributes for alliances holding a star fortress
WIN_BONUS = 20  # percent alliance points for a won battle
INVITE_PRICE, ASSIST_BONUS, MAX_ASSIST = 50, 10, 100  # wiki: assistance gives +10 %, at most +100 %
ASSIST_REWARD = 20  # crystal ore for helping
RIVAL_POINTS = 60  # the rival fleet scores about this much per member and battle
LOG_LENGTH = 200
WORLD = "S1"  # the client shows a server name in front of every rank

ORE, GEL, CUBE = "diamond_mineral", "energy_gel", "titanium_cube"
RESOURCES = (ORE, GEL, CUBE)
RESOURCE_NAMES = {ORE: "Crystal Ore", GEL: "Energy Gel", CUBE: "Titanenergetic Cube"}
ICONS = {ORE: "icons/item_res_13.png", GEL: "icons/item_res_14.png", CUBE: "icons/item_res_15.png",
         "dark_matter": "icons/item_res_05.png"}
DEVOURER, FRIGATE = 25, 26
SHIP_ICONS = {DEVOURER: "icons/item_res_14.png", FRIGATE: "icons/item_res_15.png"}  # as their shop permits
EXCHANGE = {DEVOURER: {ORE: 2000, GEL: 500}, FRIGATE: {ORE: 3500, CUBE: 1000}}  # wiki prices

# wiki: power of the ship types (the 30 strongest members join)
POWER = {1: 1, 2: 3, 3: 10, 4: 24, 5: 31, 6: 35, 7: 46, 23: 563, 24: 1246, DEVOURER: 650, FRIGATE: 1500}

NORMAL, ELITE = "PuNpc", "PuEliteNpc"
NPCS = [  # id, name, strength against the player's attributes, points, ore, gel, cube
    (1, "G.E.F. Scouts", 0.5, 10, 10, 3, 2),
    (2, "G.E.F. Patrol", 0.7, 20, 20, 6, 4),
    (3, "G.E.F. Strike Group", 0.9, 30, 30, 9, 6),
    (4, "G.E.F. Battle Group", 1.1, 45, 40, 12, 9),
    (5, "G.E.F. Armada", 1.4, 60, 55, 15, 12),
    (6, "G.E.F. Elite Fleet", 2.0, 120, 120, 35, 30),
]
NPC_IMAGE = "images/Challenge_war_bg.png"  # the client uses the name after the last "/" (it has this one)

NOT_STARTED, BATTLE, FINISHED = 0, 1, 2  # status for the client

# Rewards when the event ends: (first rank, last rank, dark matter, ships)
PERSONAL_REWARDS = [(1, 1, 300, {FRIGATE: 2}), (2, 2, 200, {FRIGATE: 1}), (3, 3, 150, {DEVOURER: 2}),
                    (4, 10, 100, {DEVOURER: 1}), (11, 50, 50, {})]
ALLIANCE_REWARDS = [(1, 1, 200, {DEVOURER: 1}), (2, 2, 150, {}), (3, 3, 100, {}), (4, 10, 50, {})]

# Event tasks: id, group, title, description, counter, number, rewards
DAILY_TASKS = [
    (1, "Daily", "Victories", "Win 10 attacks against the G.E.F.", "wins", 10, {ORE: 100, GEL: 30}),
    (2, "Daily", "Double strike", "Make 2 double strikes.", "doubles", 2, {GEL: 50, CUBE: 30}),
    (3, "Daily", "Comrades", "Assist an alliance member against an elite fleet.", "assists", 1, {ORE: 60}),
    (4, "Daily", "Points", "Score 500 points.", "points", 500, {"dark_matter": 10}),
]
ALLIANCE_TASKS = [
    (101, "Alliance", "First victories", "Your alliance wins 5 battles.", "won_battles", 5, {ORE: 300}),
    (102, "Alliance", "Campaign", "Your alliance wins 20 battles.", "won_battles", 20, {ORE: 500, CUBE: 200}),
    (103, "Alliance", "Glory", "Your alliance scores 10,000 points.", "alliance_points", 10_000,
     {"dark_matter": 30}),
]

CONGRATULATE_ITEMS = [  # id, code, name, description, dark matter, cool down, resources
    (1, "gc_crate", "Victory crate", "100,000 metal and 50,000 crystal.", 20, 86400,
     {"metal": 100_000, "crystal": 50_000}),
    (2, "gc_supply", "Alliance supply", "50,000 metal, crystal and deuterium.", 30, 86400,
     {"metal": 50_000, "crystal": 50_000, "deuterium": 50_000}),
]


class ChallengeError(planet_rules.GameError):
    pass


# --- Storage -------------------------------------------------------------------------------------


def _latest():
    return db.connect().execute("SELECT * FROM events WHERE kind = ? ORDER BY id DESC LIMIT 1", (KIND,)).fetchone()


def start(at: float, rounds: int = ROUNDS, round_seconds: int = ROUND_SECONDS) -> int:
    """Schedule a new event (the operator's command)."""
    data = {"start": int(at), "rounds": rounds, "round_seconds": max(6, int(round_seconds)), "raids": 0,
            "closed": 0, "rewarded": False, "alliances": {}, "players": {}, "invitations": [], "next_invitation": 1}
    conn = db.connect()
    with db.lock(), conn:
        return conn.execute("INSERT INTO events (kind, data) VALUES (?, ?)", (KIND, json.dumps(data))).lastrowid


def stop(now: float) -> None:
    """End the running event now; the battle that is running counts as finished."""
    with event(now) as ev:
        if ev is None or now >= end_time(ev):
            raise ChallengeError("No Galaxy Challenge is running")
        ev["end"] = int(max(now, ev["start"]))
        _advance(ev, now)


_lock = threading.RLock()  # taken after a player's state, never before


@contextmanager
def event(now: float, event_id: int | None = None, hand_out: bool = False):
    """The latest event, brought up to `now`; saved when the block ends without error.

    With `hand_out` the rank rewards that are due are given afterwards; callers that hold a
    player's state must not ask for it (the rewards change players' states)."""
    with _lock:
        row = _latest()
        if row is None or (event_id is not None and row["id"] != event_id):
            if event_id is not None:
                raise ChallengeError("This event is over")
            yield None
            return
        ev = dict(json.loads(row["data"]), id=row["id"])
        _advance(ev, now)
        yield ev
        grants = ev.pop("grants", []) if hand_out else []
        conn = db.connect()
        with db.lock(), conn:
            conn.execute("UPDATE events SET data = ? WHERE id = ?",
                         (json.dumps({k: v for k, v in ev.items() if k != "id"}), row["id"]))
    for grant in grants:
        _grant(*grant)


def end_time(ev: dict) -> int:
    return ev.get("end", ev["start"] + ev["rounds"] * ev["round_seconds"])


def status(ev: dict | None, now: float) -> int:
    if ev is None or now < ev["start"]:
        return NOT_STARTED
    if now < end_time(ev):
        return BATTLE
    return FINISHED if now < end_time(ev) + AFTERMATH else NOT_STARTED


def _round(ev: dict, now: float) -> int:
    return int((now - ev["start"]) // ev["round_seconds"])


def _rng(*key) -> random.Random:
    return random.Random("-".join(map(str, key)))


# --- Players and alliances ---------------------------------------------------------------------


def _strength(state: dict) -> tuple[int, int, int]:
    """(attack, defense, power) of the player's ships on all planets and moons."""
    attack = defense = power = 0
    for body in planet_rules.bodies(state["empire"]):
        for f in body["fleets"]:
            t, n = f["fleet_type"], f["amount"]
            if n <= 0 or t not in eco.SHIPS or t not in combat.STATS:
                continue
            s = combat.STATS[t]
            attack += s.weapon * n
            defense += (s.shield + s.hull // 10) * n
            power += POWER.get(t, 0) * n
    return max(10, attack // 10), max(10, defense // 10), power


def _new_player(pid: int, aid: int, now: float) -> dict:
    attack, defense, power = _strength(db.load_state(pid))
    return {"alliance": aid, "attack": attack, "defence": defense, "power": power, "energy": MAX_ENERGY,
            "energy_at": int(now), "courage": 0, "wins": 0, "battles": 0, "streak": 0, "score": 0,
            "round_points": 0, "elite_bonus": 0, "invited": False, "logs": [], "doubles": 0, "assists": 0,
            "day": int(now // 86400), "daily": {}, "claimed": [], ORE: 0, GEL: 0, CUBE: 0}


def _ship_name(ftype: int) -> str:
    return eco.UNITS[ftype].name if ftype in eco.UNITS else eco.SPECIAL_UNITS[ftype]


def _player(ev: dict, pid: int) -> dict | None:
    return ev["players"].get(str(pid))


def _resources(ev: dict, pid: int) -> dict:
    """The player's event resources (also for players who never fought)."""
    p = ev["players"].setdefault(str(pid), {"alliance": 0, ORE: 0, GEL: 0, CUBE: 0, "in_battle": False})
    return p


def _in_battle(p: dict | None) -> bool:
    return bool(p) and p.get("in_battle", True) and "attack" in p


def _energy(p: dict, now: float) -> int:
    gained = int((now - p["energy_at"]) // ENERGY_SECONDS)
    if gained:
        p["energy"] = min(MAX_ENERGY, p["energy"] + gained)
        p["energy_at"] = int(now) if p["energy"] >= MAX_ENERGY else p["energy_at"] + gained * ENERGY_SECONDS
    if p["energy"] >= MAX_ENERGY:
        p["energy_at"] = int(now)
    return p["energy"]


def _daily(p: dict, now: float) -> dict:
    day = int(now // 86400)
    if p.get("day") != day:
        p.update(day=day, daily={}, claimed=[c for c in p.get("claimed", []) if c >= 100])
    return p["daily"]


def _count(p: dict, key: str, n: int, now: float) -> None:
    daily = _daily(p, now)
    daily[key] = daily.get(key, 0) + n


def _fortress_holder(aid: int) -> bool:
    try:
        return any(fortresses.owner(f[0]) == aid for f in fortresses.FORTRESSES)
    except Exception:  # noqa: BLE001 - fortresses are not set up yet
        return False


def _bonus(ev: dict, pid: int) -> int:
    p = _player(ev, pid)
    a = ev["alliances"].get(str(p["alliance"]), {})
    return (LEADER_BONUS if pid in a.get("leaders", []) else 0) + (FORTRESS_BONUS if _fortress_holder(p["alliance"])
                                                                    else 0)


def _winning(p: dict) -> float:
    return p["wins"] / p["battles"] if p.get("battles") else 0.0


def _membership(pid: int):
    m = alliances.membership(pid)
    if m is None:
        raise ChallengeError("You need to join an alliance to battle")
    return m


def _username(pid: int) -> str:
    row = db.connect().execute("SELECT username FROM players WHERE id = ?", (pid,)).fetchone()
    return row["username"] if row else "?"


def _alliance_name(aid: int) -> str:
    row = db.connect().execute("SELECT name FROM alliances WHERE id = ?", (aid,)).fetchone()
    return row["name"] if row else ""


def _members_in_battle(ev: dict, aid: int) -> list[int]:
    return [int(k) for k, p in ev["players"].items() if p.get("alliance") == aid and _in_battle(p)]


def _alliance(ev: dict, aid: int) -> dict | None:
    return ev["alliances"].get(str(aid))


def _require_running(ev: dict | None, now: float) -> dict:
    if ev is None or status(ev, now) != BATTLE:
        raise ChallengeError("The battle is not running")
    return ev


def _require_fighter(ev: dict, pid: int) -> dict:
    p = _player(ev, pid)
    if not _in_battle(p):
        raise ChallengeError("You need to join the battle first.")
    return p


def _add_points(ev: dict, pid: int, points: int, now: float) -> None:
    p = _player(ev, pid)
    p["score"] += points
    p["round_points"] += points
    _count(p, "points", points, now)


def _log(entries: list, now: float, result: str, score: int) -> None:
    entries.insert(0, {"created_at": int(now), "result": result, "score": score})
    del entries[LOG_LENGTH:]


def join(ev: dict, pid: int, now: float) -> str:
    _require_running(ev, now)
    m = _membership(pid)
    aid = m["alliance_id"]
    a = _alliance(ev, aid)
    if _in_battle(_player(ev, pid)) and a is not None:
        raise ChallengeError("Invalid request. You have already joined the battle.")
    if a is None:
        if m["post"] < alliances.VICE_PRESIDENT:
            raise ChallengeError("Your alliance has not joined the battle yet")
        force = ev.get("force", {}).get(str(aid), BATTLE_FORCE)
        a = ev["alliances"].setdefault(str(aid), {"max_members": force, "score": 0, "leaders": [],
                                                  "applies": [], "logs": [], "won_battles": 0, "last": None})
        rows = db.connect().execute("SELECT player_id FROM alliance_members WHERE alliance_id = ?", (aid,))
        candidates = [(_new_player(r["player_id"], aid, now), r["player_id"]) for r in rows]
        candidates.sort(key=lambda c: (-c[0]["power"], c[1]))
        for data, member in candidates[:a["max_members"]]:
            _enlist(ev, member, data)
        joined = "Your alliance has declared war"
        if not _in_battle(_player(ev, pid)):
            return joined + "; your battle force is full."
        return joined + f" with {min(len(candidates), a['max_members'])} members."
    if len(_members_in_battle(ev, aid)) >= a["max_members"]:
        raise ChallengeError("Your alliance's battle force is full")
    _enlist(ev, pid, _new_player(pid, aid, now))
    return "You joined the battle."


def _enlist(ev: dict, pid: int, data: dict) -> None:
    old = ev["players"].get(str(pid), {})
    for res in RESOURCES:  # resources a player collected before stay
        data[res] += old.get(res, 0)
    ev["players"][str(pid)] = data


def add_battle_force(ev: dict, state: dict, now: float) -> dict:
    pid = state["empire"]["user_id"]
    m = _membership(pid)
    if m["post"] < alliances.VICE_PRESIDENT:
        raise ChallengeError("Only the president and vice-presidents can do this")
    if status(ev, now) != BATTLE:
        raise ChallengeError("The battle is not running")
    a = _alliance(ev, m["alliance_id"])
    force = a["max_members"] if a else ev.setdefault("force", {}).get(str(m["alliance_id"]), BATTLE_FORCE)
    if force >= MAX_BATTLE_FORCE:
        raise ChallengeError(f"The battle force has {MAX_BATTLE_FORCE} members at most")
    _pay(state, BATTLE_FORCE_PRICE)
    if a:
        a["max_members"] += 1
    else:
        ev["force"][str(m["alliance_id"])] = force + 1
    return {"success": True, "result": f"Your battle force has room for {force + 1} members.",
            "max_member_size": force + 1, "max_size": MAX_BATTLE_FORCE}


def _pay(state: dict, credits: int) -> None:
    empire = state["empire"]
    if empire.get("gu_count", 0) < credits:
        raise ChallengeError(f"You need {credits:,} credits")
    empire["gu_count"] -= credits


# --- Views -----------------------------------------------------------------------------------------


def current_view(ev: dict | None, pid: int, now: float) -> dict:
    st = status(ev, now)
    if ev is None or st == NOT_STARTED and now >= ev["start"]:
        return {"status": NOT_STARTED, "current": {"id": ev["id"] if ev else 0, "end_time": 0},
                "next": {}, "maps": {"colors": [1] * 6, "current": 1}, "self_info": self_info(ev, pid, now)}
    if st == NOT_STARTED:
        current_end, next_start = 0, ev["start"]
    elif st == BATTLE:
        current_end = ev["start"] + (_round(ev, now) + 1) * ev["round_seconds"]
        next_start = current_end if _round(ev, now) + 1 < ev["rounds"] else -1
    else:
        current_end, next_start = end_time(ev), -1
    m = alliances.membership(pid)
    area = (m["alliance_id"] - 1) % 6 + 1 if m else 1
    view = {"status": st, "current": {"id": ev["id"], "end_time": current_end, "invite_cost": INVITE_PRICE,
                                     "add_courage_cost": COURAGE_PRICE},
            "next": {"start_time": next_start} if next_start >= 0 else {},
            "maps": {"colors": [1] * 6, "current": area}, "self_info": self_info(ev, pid, now)}
    return view


def self_info(ev: dict | None, pid: int, now: float) -> dict:
    m = alliances.membership(pid)
    p = _player(ev, pid) if ev else None
    fighting = _in_battle(p)
    if fighting:
        energy = _energy(p, now)
        attack, defense, power = p["attack"], p["defence"], p["power"]
    else:
        energy = MAX_ENERGY
        attack, defense, power = _strength(db.load_state(pid))
    info = {"in_battle": fighting, "leader": False, "winning": round(_winning(p), 4) if fighting else 0,
            "streak": p["streak"] if fighting else 0, "attack": attack, "defence": defense, "power": power,
            "shadow_attack_bonus": 0, "shadow_defence_bonus": 0, "curr_phy": energy, "max_phy": MAX_ENERGY,
            "full_time": int(now + (MAX_ENERGY - energy) * ENERGY_SECONDS),
            "phy_consume": ATTACK_ENERGY, "adv_phy_consume": DOUBLE_ENERGY, "adv_attack_bonus": DOUBLE_ATTACK,
            "coura": p.get("courage", 0) if fighting else 0, "max_coura": MAX_COURAGE,
            "attack_to_elite": attack, "attack_bonus_to_elite": p.get("elite_bonus", 0) if fighting else 0,
            "score": p["score"] if fighting else 0,
            "add_member_cost": {"type": 2, "amount": BATTLE_FORCE_PRICE}}
    if fighting:
        bonus = _bonus(ev, pid)
        info.update(leader=pid in ev["alliances"].get(str(p["alliance"]), {}).get("leaders", []),
                    shadow_attack_bonus=bonus, shadow_defence_bonus=bonus)
    if m:
        a = _alliance(ev, m["alliance_id"]) if ev else None
        force = a["max_members"] if a else (ev or {}).get("force", {}).get(str(m["alliance_id"]), BATTLE_FORCE)
        info["alliance"] = {"id": m["alliance_id"], "role": m["post"], "in_battle": a is not None,
                            "leaders": [{"id": lid, "name": _username(lid)} for lid in (a or {}).get("leaders", [])],
                            "max_member_size": force, "max_size": MAX_BATTLE_FORCE}
    return info


def _npc_strength(p: dict, factor: float) -> float:
    return factor * (0.8 + 0.4 * _winning(p))  # texts: the higher the win ratio, the stronger the opponent


def npc_list(ev: dict, pid: int) -> list[dict]:
    p = _player(ev, pid)
    if not _in_battle(p):
        return []
    out = []
    for nid, name, factor, *_ in NPCS:
        f = _npc_strength(p, factor)
        elite = nid == NPCS[-1][0]
        out.append({"id": nid, "name": name, "type": ELITE if elite else NORMAL, "image": NPC_IMAGE,
                    "attack": int(p["defence"] * f), "defence": int(p["attack"] * f),
                    "can_ask_help": elite and not p.get("invited")})
    return out


# --- Fighting --------------------------------------------------------------------------------------


def attack(ev: dict, pid: int, npc_id: int, double: bool, now: float, rng: random.Random | None = None) -> dict:
    _require_running(ev, now)
    p = _require_fighter(ev, pid)
    npc = next((n for n in NPCS if n[0] == npc_id), None)
    if npc is None:
        raise ChallengeError("This fleet is gone")
    cost = DOUBLE_ENERGY if double else ATTACK_ENERGY
    if _energy(p, now) < cost:
        raise ChallengeError("Not enough energy")
    p["energy"] -= cost
    rng = rng or random.Random()
    nid, _, factor, points, ore, gel, cube = npc
    elite = nid == NPCS[-1][0]
    power = p["attack"] * (100 + _bonus(ev, pid) + (p["elite_bonus"] if elite else 0)) / 100
    if double:
        power *= DOUBLE_ATTACK / 100
        _count(p, "doubles", 1, now)
    if elite:
        p.update(elite_bonus=0, invited=False)
    defense = p["attack"] * _npc_strength(p, factor)
    p["battles"] += 1
    won = power * rng.uniform(0.75, 1.25) >= defense
    gains = {ORE: 0, GEL: 0, CUBE: 0}
    score = 0
    if won:
        p["wins"] += 1
        times = 4 if double else 1
        score = int(points * (1 + min(p["streak"], 10) / 10) * times)  # longer streaks give more points
        p["streak"] += 1
        gains = {ORE: ore * times, GEL: gel * times, CUBE: cube * times}
        for res, n in gains.items():
            p[res] += n
        _add_points(ev, pid, score, now)
        _count(p, "wins", 1, now)
    else:
        p["streak"] = 0
    _log(p["logs"], now, "Won" if won else "Lost", score)
    return {"result": "win" if won else "lose", "score": score, "items": [],
            "user_resources": [[1, gains[ORE]], [2, gains[GEL]], [3, gains[CUBE]]]}


def buy_energy(ev: dict, state: dict, amount: int, now: float) -> None:
    _require_running(ev, now)
    p = _require_fighter(ev, state["empire"]["user_id"])
    if amount not in ENERGY_PRICES:
        raise ChallengeError("Invalid amount")
    _pay(state, ENERGY_PRICES[amount])
    p["energy"] = min(MAX_ENERGY, _energy(p, now) + amount)


def buy_courage(ev: dict, state: dict, now: float) -> None:
    _require_running(ev, now)
    pid = state["empire"]["user_id"]
    p = _require_fighter(ev, pid)
    if pid not in ev["alliances"][str(p["alliance"])]["leaders"]:
        raise ChallengeError("Courage points are for mission leaders only")
    if p["courage"] >= MAX_COURAGE:
        raise ChallengeError("Your courage points are full")
    _pay(state, COURAGE_PRICE)
    p["courage"] = MAX_COURAGE


def _raid(ev: dict, aid: int, at: float, rng: random.Random) -> None:
    """A G.E.F. attack on an alliance: a leader with courage defends, else a random member."""
    a = ev["alliances"][str(aid)]
    members = _members_in_battle(ev, aid)
    if not members:
        return
    brave = [lid for lid in a["leaders"] if _player(ev, lid)["courage"] >= COURAGE_PER_RAID]
    if brave:
        targets, points = brave, 30
        for lid in brave:
            _player(ev, lid)["courage"] -= COURAGE_PER_RAID
    else:
        others = [m for m in members if m not in a["leaders"]] or members
        targets, points = [rng.choice(others)], 20
    for pid in targets:
        p = _player(ev, pid)
        defense = p["defence"] * (100 + _bonus(ev, pid)) / 100
        held = defense * rng.uniform(0.75, 1.25) >= p["defence"] * _npc_strength(p, 0.9)
        if held:
            _add_points(ev, pid, points, at)
        _log(p["logs"], at, "Held" if held else "Fell", points if held else 0)


def _close_round(ev: dict, rnd: int, at: float) -> None:
    for aid_s, a in ev["alliances"].items():
        aid = int(aid_s)
        members = _members_in_battle(ev, aid)
        points = sum(_player(ev, m)["round_points"] for m in members)
        points = int(points * (100 + LEADER_POINTS * len(a["leaders"])) / 100)
        rival = int(len(members) * RIVAL_POINTS * _rng(ev["id"], rnd, aid).uniform(0.5, 1.5))
        won = points > rival
        if won:
            points = int(points * (100 + WIN_BONUS) / 100)
            a["won_battles"] += 1
        a["score"] += points
        a["last"] = {"round": rnd, "result": "win" if won else "lose", "score": points, "rival": rival}
        _log(a["logs"], at, "Won" if won else "Lost", points)
        for m in members:
            _player(ev, m)["round_points"] = 0
        a["leaders"], a["applies"] = [], []
    for p in ev["players"].values():
        p["invited"] = False
    ev["invitations"] = []


def _advance(ev: dict, now: float) -> None:
    """Run the G.E.F. attacks and battle ends that are due, then hand out the rewards."""
    raid_seconds = ev["round_seconds"] / RAIDS_PER_ROUND
    last = min(now, end_time(ev))
    while ev["start"] + (ev["raids"] + 1) * raid_seconds <= last:
        ev["raids"] += 1
        at = ev["start"] + ev["raids"] * raid_seconds
        rng = _rng(ev["id"], "raid", ev["raids"])
        for aid in sorted(ev["alliances"], key=int):
            _raid(ev, int(aid), at, rng)
        if ev["raids"] % RAIDS_PER_ROUND == 0:
            _close_round(ev, ev["closed"], at)
            ev["closed"] += 1
    if now >= end_time(ev) and ev["closed"] * ev["round_seconds"] < end_time(ev) - ev["start"]:
        _close_round(ev, ev["closed"], end_time(ev))  # the battle the operator cut short
        ev["closed"] += 1
    if now >= end_time(ev) and not ev["rewarded"]:
        ev["rewarded"] = True
        _reward(ev)


# --- Leaders and assistance ----------------------------------------------------------------------


def apply_leader(ev: dict, pid: int, now: float) -> str:
    _require_running(ev, now)
    p = _require_fighter(ev, pid)
    a = ev["alliances"][str(p["alliance"])]
    if pid in a["leaders"]:
        raise ChallengeError("You are already leader")
    if pid not in a["applies"]:
        a["applies"].append(pid)
    return "Your application for mission leadership was sent."


def leader_applies(ev: dict, pid: int) -> list[dict]:
    m = _membership(pid)
    a = _alliance(ev, m["alliance_id"]) or {"applies": []}
    posts = {r["player_id"]: r["post"] for r in db.connect().execute(
        "SELECT player_id, post FROM alliance_members WHERE alliance_id = ?", (m["alliance_id"],))}
    return [{"id": a_pid, "name": _username(a_pid), "level": posts.get(a_pid, 0), "score": _player(ev, a_pid)["power"]}
            for a_pid in a["applies"]]


def set_leader(ev: dict, pid: int, leader_id: int, now: float) -> None:
    _require_running(ev, now)
    m = _membership(pid)
    if m["post"] < alliances.VICE_PRESIDENT:
        raise ChallengeError("Only the president and vice-presidents can appoint leaders")
    a = _alliance(ev, m["alliance_id"])
    target = _player(ev, leader_id)
    if a is None or not _in_battle(target) or target["alliance"] != m["alliance_id"]:
        raise ChallengeError("This member is not in the battle")
    if leader_id in a["leaders"]:
        raise ChallengeError("This member is already leader")
    if len(a["leaders"]) >= MAX_LEADERS:
        raise ChallengeError(f"There are {MAX_LEADERS} leaders at most")
    a["leaders"].append(leader_id)
    if leader_id in a["applies"]:
        a["applies"].remove(leader_id)


def invite(ev: dict, state: dict, npc_id: int, now: float) -> str:
    _require_running(ev, now)
    pid = state["empire"]["user_id"]
    p = _require_fighter(ev, pid)
    if npc_id != NPCS[-1][0]:
        raise ChallengeError("Assistance is only for attacks on the elite fleet")
    if p["invited"]:
        raise ChallengeError("You already asked for assistance")
    others = [m for m in _members_in_battle(ev, p["alliance"]) if m != pid]
    if not others:
        raise ChallengeError("No other alliance member is in the battle")
    _pay(state, INVITE_PRICE)
    p["invited"] = True
    for m in others:
        ev["invitations"].append({"id": ev["next_invitation"], "from": pid, "to": m, "created_at": int(now)})
        ev["next_invitation"] += 1
    return f"Your request for assistance was sent to {len(others)} members."


def invitations(ev: dict, pid: int) -> list[dict]:
    return [{"id": i["id"], "created_at": i["created_at"], "invitor_name": _username(i["from"])}
            for i in ev["invitations"] if i["to"] == pid]


def accept(ev: dict, pid: int, invitation_id: int | None, now: float) -> str:
    _require_running(ev, now)
    helper = _require_fighter(ev, pid)
    chosen = [i for i in ev["invitations"] if i["to"] == pid and (invitation_id is None or i["id"] == invitation_id)]
    if not chosen:
        raise ChallengeError("This request is gone")
    for i in chosen:
        ev["invitations"].remove(i)
        asker = _player(ev, i["from"])
        asker["elite_bonus"] = min(MAX_ASSIST, asker["elite_bonus"] + ASSIST_BONUS)
        helper[ORE] += ASSIST_REWARD
        _count(helper, "assists", 1, now)
    return f"You assisted {len(chosen)} member(s) and got {ASSIST_REWARD * len(chosen)} crystal ore."


# --- Ranks, logs and rewards -----------------------------------------------------------------------


def _personal(ev: dict, aid: int | None = None) -> list[tuple[int, dict]]:
    rows = [(int(k), p) for k, p in ev["players"].items() if "score" in p and (aid is None or p["alliance"] == aid)]
    rows.sort(key=lambda r: (-r[1]["score"], r[0]))
    return rows


def _alliance_rows(ev: dict) -> list[tuple[int, dict]]:
    rows = [(int(k), a) for k, a in ev["alliances"].items()]
    rows.sort(key=lambda r: (-r[1]["score"], r[0]))
    return rows


def _next_range(scores: list[int], index: int | None, tiers: list) -> int:
    """Points the player needs for the next better reward rank (-1 if there is none)."""
    if index is None:  # not ranked yet
        return scores[tiers[-1][1] - 1] + 1 if len(scores) >= tiers[-1][1] else 1
    rank = index + 1
    better = [last for _, last, *_ in tiers if last < rank]
    if not better:
        return -1
    return max(0, scores[better[-1] - 1] - scores[index] + 1)


def ranks(ev: dict, pid: int, rank_type: str) -> dict:
    if rank_type == "alliance":
        rows = _alliance_rows(ev)
        m = alliances.membership(pid)
        mine = next((i for i, (aid, _) in enumerate(rows) if m and aid == m["alliance_id"]), None)
        return {"ranks": [[WORLD, _alliance_name(aid), len(_members_in_battle(ev, aid)), a["score"]] for aid, a in rows],
                "self_rank": {"rank": mine + 1 if mine is not None else 0,
                              "score": rows[mine][1]["score"] if mine is not None else 0,
                              "next_range": _next_range([a["score"] for _, a in rows], mine, ALLIANCE_REWARDS)}}
    p = _player(ev, pid)
    rows = _personal(ev, p["alliance"] if rank_type == "member" and p else None)
    if rank_type == "member" and not p:
        rows = []
    mine = next((i for i, (k, _) in enumerate(rows) if k == pid), None)
    return {"ranks": [[WORLD, _username(k), _alliance_name(r["alliance"]), r["power"], r["score"]] for k, r in rows],
            "self_rank": {"rank": mine + 1 if mine is not None else 0, "name": _username(pid),
                          "score": rows[mine][1]["score"] if mine is not None else 0,
                          "next_range": _next_range([r["score"] for _, r in rows], mine, PERSONAL_REWARDS)}}


def histories(ev: dict, pid: int) -> dict:
    users = ranks(ev, pid, "personal")
    allies = ranks(ev, pid, "alliance")
    m = alliances.membership(pid)
    users["self_rank"]["alliance_name"] = _alliance_name(m["alliance_id"]) if m else ""
    allies["self_rank"].update(name=users["self_rank"]["alliance_name"],
                               alliance_name=users["self_rank"]["alliance_name"])
    return {"users": users, "alliances": allies}


def _awards(dark_matter: int, ships: dict[int, int]) -> list[dict]:
    out = [{"name": "Dark Matter", "amount": dark_matter, "img": ICONS["dark_matter"]}] if dark_matter else []
    return out + [{"name": _ship_name(t), "amount": n, "img": SHIP_ICONS[t]} for t, n in ships.items()]


def _tier_label(first: int, last: int) -> str:
    return str(first) if first == last else f"{first}-{last}"


def bounties() -> dict:
    return {"all_alliances": [], "all_users": [],
            "one_alliances": [{"rank": _tier_label(f, l), "awards": _awards(dm, s)} for f, l, dm, s in ALLIANCE_REWARDS],
            "one_users": [{"rank": _tier_label(f, l), "awards": _awards(dm, s)} for f, l, dm, s in PERSONAL_REWARDS]}


def _tier(tiers: list, rank: int):
    return next(((dm, ships) for first, last, dm, ships in tiers if first <= rank <= last), None)


def _reward(ev: dict) -> None:
    """Hand out the rank rewards (dark matter and ships on the home planet, with a mail)."""
    grants = ev.setdefault("grants", [])
    for rank, (pid, p) in enumerate([r for r in _personal(ev) if r[1]["score"] > 0], start=1):
        tier = _tier(PERSONAL_REWARDS, rank)
        if tier:
            grants.append((pid, f"Galaxy Challenge: rank {rank}", *tier))
    for rank, (aid, a) in enumerate([r for r in _alliance_rows(ev) if r[1]["score"] > 0], start=1):
        tier = _tier(ALLIANCE_REWARDS, rank)
        if tier:
            for pid in _members_in_battle(ev, aid):
                grants.append((pid, f"Galaxy Challenge: your alliance is rank {rank}", *tier))


def _grant(pid: int, title: str, dark_matter: int, ships: dict) -> None:
    ships = {int(t): n for t, n in ships.items()}  # the keys went through JSON
    with db.player_state(pid) as state:
        empire = state["empire"]
        empire["dark_matter_count"] = empire.get("dark_matter_count", 0) + dark_matter
        home = empire["planets"][0]
        for t, n in ships.items():
            planet_rules.fleet(home, t)["amount"] += n
        username = state["username"]
    parts = [f"{dark_matter} dark matter"] + [f"{n} {_ship_name(t)}" for t, n in ships.items()]
    mails.send(pid, mails.SYSTEM, "Supernova", username, title,
               "Thank you for fighting the G.E.F.! Your reward: " + ", ".join(parts) + ".")


def logs(entries: list, page: int, per_page: int = 20) -> dict:
    page = max(1, page)
    return {"logs": entries[(page - 1) * per_page:page * per_page], "max_page": max(1, -(-len(entries) // per_page))}


def last_battle(ev: dict, pid: int) -> dict:
    m = alliances.membership(pid)
    a = _alliance(ev, m["alliance_id"]) if m else None
    last = (a or {}).get("last") or {"result": "lose", "score": 0, "rival": 0}
    return {"user_alliance": {"result": last["result"], "name": _alliance_name(m["alliance_id"]) if m else "",
                              "scores": {"last": last["score"], "total": (a or {}).get("score", 0)}},
            "npc_alliance": {"name": "G.E.F.", "scores": {"last": last["rival"]}}}


# --- Exchange and shops ------------------------------------------------------------------------------


def _can_exchange(ev: dict | None, now: float) -> bool:
    return ev is not None and ev["start"] <= now < end_time(ev) + AFTERMATH


def resources_view(ev: dict | None, pid: int, now: float) -> dict:
    p = _resources(ev, pid) if _can_exchange(ev, now) else {}
    return {res: {"amount": p.get(res, 0), "name": RESOURCE_NAMES[res]} for res in RESOURCES}


def exchange_list() -> list[dict]:
    return [{"fleet_type": t, "fleet_name": _ship_name(t), "attack": combat.STATS[t].weapon,
             "shield": combat.STATS[t].shield, ORE: cost.get(ORE, 0), GEL: cost.get(GEL, 0), CUBE: cost.get(CUBE, 0)}
            for t, cost in EXCHANGE.items()]


def exchange(ev: dict | None, state: dict, ftype: int, n: int, now: float) -> str:
    if not _can_exchange(ev, now):
        raise ChallengeError("The exchange is closed")
    if ftype not in EXCHANGE or n < 1:
        raise ChallengeError("Invalid exchange")
    p = _resources(ev, state["empire"]["user_id"])
    cost = {res: amount * n for res, amount in EXCHANGE[ftype].items()}
    missing = [RESOURCE_NAMES[r] for r, c in cost.items() if p.get(r, 0) < c]
    if missing:
        raise ChallengeError("Not enough " + " and ".join(missing))
    for res, c in cost.items():
        p[res] -= c
    planet_rules.fleet(state["empire"]["main_planet"], ftype)["amount"] += n
    return f"{n} {_ship_name(ftype)} joined your fleet."


def congratulate_items(state: dict, now: float) -> list[dict]:
    bought = state.get("gc_bought", {})
    return [{"id": i, "code": code, "name": name, "desc": desc, "price": price, "img": "icons/item_res_05.png",
             "gacha_type": 1 if i == 1 else 2, "item_type": 1,
             "user_cool_time": max(0, int(bought.get(code, 0) + cool - now))}
            for i, code, name, desc, price, cool, _ in CONGRATULATE_ITEMS]


def buy_congratulate_item(state: dict, code: str, now: float) -> dict:
    item = next((i for i in CONGRATULATE_ITEMS if i[1] == code), None)
    if item is None:
        raise ChallengeError("This item is not available")
    iid, _, name, _, price, cool, res = item
    bought = state.setdefault("gc_bought", {})
    if bought.get(code, 0) + cool > now:
        raise ChallengeError("You can buy this again later")
    empire = state["empire"]
    if empire.get("dark_matter_count", 0) < price:
        raise ChallengeError(f"You need {price} dark matter")
    empire["dark_matter_count"] -= price
    bought[code] = int(now)
    planet = empire["main_planet"]
    for r, n in res.items():
        planet[r] += n
    return {"id": iid, "user_cool_time": cool, "info": {"info": f"{name}: {', '.join(f'{n:,} {r}' for r, n in res.items())}"}}


# --- Event tasks --------------------------------------------------------------------------------


def _task_rewards(rewards: dict) -> tuple[str, list[dict]]:
    names = {**RESOURCE_NAMES, "dark_matter": "Dark Matter"}
    return (", ".join(f"{n:,} {names[r]}" for r, n in rewards.items()),
            [{"img": ICONS[r], "number": n} for r, n in rewards.items()])


def _progress(ev: dict, pid: int, counter: str, now: float) -> int:
    p = _player(ev, pid)
    a = _alliance(ev, p["alliance"]) or {}
    if counter == "won_battles":
        return a.get("won_battles", 0)
    if counter == "alliance_points":
        return a.get("score", 0)
    return _daily(p, now).get(counter, 0)


def tasks(ev: dict, pid: int, alliance_tasks: bool, now: float) -> list[dict]:
    p = _player(ev, pid)
    if not _in_battle(p):
        return []
    groups: dict[str, list] = {}
    for tid, group, title, desc, counter, number, rewards in (ALLIANCE_TASKS if alliance_tasks else DAILY_TASKS):
        _daily(p, now)
        if tid in p["claimed"]:
            continue
        done = min(number, _progress(ev, pid, counter, now))
        text, info = _task_rewards(rewards)
        groups.setdefault(group, []).append({"id": tid, "title": title, "desc": desc, "status": int(done >= number),
                                             "process": done, "require_number": number, "rewards_desc": text,
                                             "rewards_info": info})
    return [{"lable": group, "list": entries} for group, entries in groups.items()]


def claim_task(ev: dict, state: dict, tid: int, now: float) -> str:
    pid = state["empire"]["user_id"]
    p = _require_fighter(ev, pid)
    task = next((t for t in DAILY_TASKS + ALLIANCE_TASKS if t[0] == tid), None)
    _daily(p, now)
    if task is None or tid in p["claimed"]:
        raise ChallengeError("This task is not available")
    if _progress(ev, pid, task[4], now) < task[5]:
        raise ChallengeError("This task is not done yet")
    p["claimed"].append(tid)
    for res, n in task[6].items():
        if res == "dark_matter":
            state["empire"]["dark_matter_count"] = state["empire"].get("dark_matter_count", 0) + n
        else:
            p[res] += n
    return "Reward: " + _task_rewards(task[6])[0]
