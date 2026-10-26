"""Coordinated attacks (the client's "ACS").

This client version cannot turn an attack into a coordinated attack itself (its button is
always hidden), so Supernova makes every attack of an alliance member on another player
joinable: it shows up in the alliance's "coordinated attacks" tab until it arrives. Other
members join with their own fleets; joined fleets arrive together with the attack and fight
as one side (with the technologies of the player who started it). Survivors and loot are
shared in proportion to what each fleet brought.
"""

import json

from . import alliances, db, fleets, protection
from . import economy as eco
from . import planet as planet_rules

MAX_FLEETS = 5  # the attack itself and up to four joined fleets


class AcsError(planet_rules.GameError):
    pass


def _leader(acs_id: int):
    return db.connect().execute("SELECT * FROM fleet_events WHERE id = ? AND mission = ? AND status = 'outbound' "
                                "AND target_planet_id IS NOT NULL", (acs_id, fleets.ATTACK)).fetchone()


def _alliance_of(player_id: int) -> int:
    m = alliances.membership(player_id)
    return m["alliance_id"] if m else 0


def _joined(acs_id: int) -> list:
    return db.connect().execute("SELECT * FROM fleet_events WHERE acs_id = ? AND status = 'outbound' ORDER BY id",
                                (acs_id,)).fetchall()


def _info(leader, now: float) -> dict:
    conn = db.connect()
    origin = conn.execute("SELECT * FROM planets WHERE id = ?", (leader["origin_planet_id"],)).fetchone()
    target = conn.execute("SELECT p.*, pl.username FROM planets p JOIN players pl ON pl.id = p.player_id "
                          "WHERE p.id = ?", (leader["target_planet_id"],)).fetchone()
    group = [leader] + _joined(leader["id"])
    lines = [{"id": r["id"], "user_name": fleets._username(r["owner_id"]),
              "morale": sum(fleets._ships(r).values())} for r in group]
    moon = "@" if target and target["kind"] else ""
    return {
        "id": leader["id"],
        "attacker": fleets._username(leader["owner_id"]),
        "defender": target["username"] if target else "",
        "attacker_pos": f"{origin['galaxy']}:{origin['system']}:{origin['position']}" if origin else "",
        "defender_pos": f"{leader['target_galaxy']}:{leader['target_system']}:{leader['target_position']}{moon}",
        "curr_lines": len(group), "max_lines": MAX_FLEETS, "acs_line_limit": MAX_FLEETS,
        "morale": sum(line["morale"] for line in lines),
        "left_time": max(0, int(leader["arrival_time"] - now)),
        "total_time": max(1, leader["arrival_time"] - leader["start_time"]),
        "acs_status": 1,  # joinable until it arrives
        "sub_lines": lines, "expand_base": 0, "expand_incr": 0,
    }


def listing(player_id: int, now: float) -> list[dict]:
    alliance_id = _alliance_of(player_id)
    if not alliance_id:
        return []
    members = [r["player_id"] for r in db.connect().execute(
        "SELECT player_id FROM alliance_members WHERE alliance_id = ?", (alliance_id,))]
    marks = ",".join("?" * len(members))
    rows = db.connect().execute(
        f"SELECT f.* FROM fleet_events f JOIN planets p ON p.id = f.target_planet_id WHERE f.mission = ? "
        f"AND f.status = 'outbound' AND f.arrival_time > ? AND f.owner_id IN ({marks}) "
        f"AND p.player_id NOT IN ({marks}) ORDER BY f.arrival_time",
        (fleets.ATTACK, int(now), *members, *members)).fetchall()
    return [_info(r, now) for r in rows]


def details(player_id: int, acs_id: int, now: float) -> dict:
    leader = _leader(acs_id)
    if leader is None or _alliance_of(player_id) != _alliance_of(leader["owner_id"]) or not _alliance_of(player_id):
        raise AcsError("This coordinated attack is over")
    return _info(leader, now)


def parse_fleets(text: str) -> dict[int, int]:
    """The client sends the selected ships as "type:count,type:count"."""
    ships = {}
    for part in filter(None, text.split(",")):
        t, _, n = part.partition(":")
        if t.isdigit() and n.isdigit() and int(n) > 0:
            ships[int(t)] = ships.get(int(t), 0) + int(n)
    if not ships:
        raise AcsError("Select the ships to send")
    return ships


def _check_join(player_id: int, state: dict, acs_id: int, origin: dict, ships: dict[int, int], now: float):
    leader = _leader(acs_id)
    if leader is None or leader["arrival_time"] <= now:
        raise AcsError("This coordinated attack is over")
    if not _alliance_of(player_id) or _alliance_of(player_id) != _alliance_of(leader["owner_id"]):
        raise AcsError("Only members of the attacker's alliance can join")
    if 1 + len(_joined(acs_id)) >= MAX_FLEETS:
        raise AcsError("This coordinated attack is full")
    target_owner = fleets._planet_owner(leader["target_planet_id"])
    if target_owner == player_id:
        raise AcsError("You cannot attack yourself")
    verdict = protection.can_attack(player_id, target_owner)
    if verdict != protection.ATTACK_OK:
        raise AcsError(protection.MESSAGES[verdict])
    for t, n in ships.items():
        if t not in eco.SHIPS or planet_rules.fleet(origin, t)["amount"] < n:
            raise AcsError("You do not have these ships")
    if not any(t in fleets.combat.STATS and fleets.combat.STATS[t].weapon > 0 for t in ships):
        raise AcsError("Send at least one armed ship")
    target = (leader["target_galaxy"], leader["target_system"], leader["target_position"])
    plan = fleets.plan(state["empire"], player_id, ships, target, origin=origin)
    if now + plan["total_time"] > leader["arrival_time"]:
        raise AcsError("Your fleet is too slow to arrive in time")
    return leader, target, plan


def prepare(player_id: int, state: dict, acs_id: int, planet_id: int, fleets_text: str, now: float) -> dict:
    origin = planet_rules.find_planet(state["empire"], planet_id) or state["empire"]["main_planet"]
    ships = parse_fleets(fleets_text)
    leader, _, plan = _check_join(player_id, state, acs_id, origin, ships, now)
    return {"missions": [fleets.ACS_ATTACK], "speed": plan["speed"],
            "total_time": int(leader["arrival_time"] - now), "fuel_consumption": plan["fuel_consumption"]}


def join(player_id: int, state: dict, acs_id: int, planet_id: int, fleets_text: str, now: float) -> dict:
    empire = state["empire"]
    planet_rules.tick(empire, now)
    origin = planet_rules.find_planet(empire, planet_id) or empire["main_planet"]
    ships = parse_fleets(fleets_text)
    leader, target, plan = _check_join(player_id, state, acs_id, origin, ships, now)
    fleets.check_fleet_slot(player_id, empire, now)
    if origin["deuterium"] < plan["fuel_consumption"]:
        raise AcsError("Not enough deuterium for the flight")
    origin["deuterium"] -= plan["fuel_consumption"]
    for t, n in ships.items():
        planet_rules.fleet(origin, t)["amount"] -= n
    conn = db.connect()
    with db.lock(), conn:
        conn.execute(
            "INSERT INTO fleet_events (owner_id, origin_planet_id, target_planet_id, target_galaxy, target_system, "
            "target_position, mission, ships, cargo, start_time, arrival_time, return_time, status, acs_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'outbound', ?)",
            (player_id, origin["planet_id"], leader["target_planet_id"], *target, fleets.ACS_JOIN,
             json.dumps({str(t): n for t, n in ships.items()}), json.dumps({r: 0 for r in fleets.RESOURCES}),
             int(now), leader["arrival_time"], leader["arrival_time"] + plan["total_time"], acs_id))
    return _info(leader, now)


def call_off(player_id: int, acs_id: int, now: float) -> None:
    """The attacker cancels: the attack and all joined fleets fly home."""
    leader = _leader(acs_id)
    if leader is None or leader["owner_id"] != player_id:
        raise AcsError("Only the attacker can call off the coordinated attack")
    fleets.recall(player_id, acs_id, now)


def fleet_list(player_id: int, event_id: int) -> list[dict]:
    """The ships of a flight: own flights, flights of alliance members and attacks on oneself."""
    row = db.connect().execute("SELECT * FROM fleet_events WHERE id = ?", (event_id,)).fetchone()
    if row is None:
        raise AcsError("This flight is over")
    same_alliance = _alliance_of(player_id) and _alliance_of(player_id) == _alliance_of(row["owner_id"])
    if row["owner_id"] != player_id and not same_alliance and             fleets._planet_owner(row["target_planet_id"]) != player_id:
        raise AcsError("This flight is not yours")
    return [{"name": eco.UNITS[t].name if t in eco.UNITS else str(t), "count": n}
            for t, n in fleets._ships(row).items()]
