"""Fleet missions: sending ships between planets, arrival and return.

Flights are stored in the fleet_events table, not in the player state, because a mission can
change another player's planet. A background task in the server calls `process_due` every
second, so missions also complete while their owner is offline.

Implemented missions: transport and deploy.
"""

import json
import logging
import time

from . import db
from . import economy as eco
from . import planet as planet_rules
from . import universe

log = logging.getLogger("supernova")

EXPEDITION, COLONY, RECYCLE, TRANSPORT, DEPLOY, ESPIONAGE, ATTACK = 1, 2, 3, 4, 5, 6, 7
MISSION_NAMES = {
    EXPEDITION: "Expedition", COLONY: "Colonize", RECYCLE: "Recycle", TRANSPORT: "Transport",
    DEPLOY: "Deploy", ESPIONAGE: "Espionage", ATTACK: "Attack",
}
RESOURCES = planet_rules.RESOURCES


class FleetError(planet_rules.GameError):
    pass


# --- Planning a flight --------------------------------------------------------------------


def _research_levels(empire: dict) -> dict[int, int]:
    return {r["research_type"]: r["research_level"] for r in empire["researches"]}


def _position(p: dict) -> tuple[int, int, int]:
    return p["galaxy_position"], p["system_position"], p["planet_position"]


def parse_ships(planet: dict, fleet_ids: str, counts: str) -> dict[int, int]:
    """'101,109,' + '5,2,' -> {fleet_type: count}, checked against the planet's ships."""
    ids = [int(x) for x in fleet_ids.split(",") if x.strip()]
    numbers = [int(x) for x in counts.split(",") if x.strip()]
    if not ids or len(ids) != len(numbers):
        raise FleetError("No ships selected")
    ships: dict[int, int] = {}
    for fid, n in zip(ids, numbers):
        f = next((f for f in planet["fleets"] if f["fleet_id"] == fid), None)
        if f is None or f["fleet_type"] not in eco.SHIPS or n < 1:
            raise FleetError("These ships cannot fly")
        if n > f["amount"]:
            raise FleetError("Not enough ships")
        ships[f["fleet_type"]] = ships.get(f["fleet_type"], 0) + n
    return ships


def parse_target(end_p_pos: str) -> tuple[int, int, int]:
    try:
        galaxy, system, position = (int(x) for x in end_p_pos.split(",")[:3])
    except ValueError:
        raise FleetError("Invalid target") from None
    if not universe.valid_system(galaxy, system) or not 1 <= position <= 21:
        raise FleetError("Invalid target")
    return galaxy, system, position


def target_planet(target: tuple[int, int, int]):
    galaxy, system, position = target
    return db.connect().execute(
        "SELECT id, player_id, name FROM planets WHERE galaxy = ? AND system = ? AND position = ?",
        (galaxy, system, position)).fetchone()


def allowed_missions(player_id: int, origin: dict, target: tuple[int, int, int], ships: dict[int, int]) -> list[int]:
    row = target_planet(target)
    if row is None or row["id"] == origin["planet_id"]:
        return []  # colonize, expedition, pirates: not implemented yet
    missions = [TRANSPORT]
    if row["player_id"] == player_id:
        missions.append(DEPLOY)
    return missions


def plan(empire: dict, player_id: int, ships: dict[int, int], target: tuple[int, int, int],
         percent: int = 100) -> dict:
    origin = empire["main_planet"]
    levels = _research_levels(empire)
    speed = min(eco.ship_speed(t, levels) for t in ships)
    dist = eco.distance(_position(origin), target)
    return {
        "missions": allowed_missions(player_id, origin, target, ships),
        "speed": speed,
        "distance": dist,
        "total_time": eco.flight_seconds(dist, speed, percent),
        "fuel_consumption": eco.fuel(ships, dist, percent),
        "empty_cargobays": eco.cargo_capacity(ships),
    }


# --- Sending and recalling ------------------------------------------------------------------


def send(player_id: int, state: dict, params: dict, now: float) -> None:
    empire = state["empire"]
    origin = empire["main_planet"]
    planet_rules.tick(empire, now)
    ships = parse_ships(origin, params.get("fleet_ids", ""), params.get("fleet_cnt_ids", ""))
    target = parse_target(params.get("end_p_pos", ""))
    mission = int(params.get("mission", 0))
    percent = int(params.get("speed_ratio", 100))
    if not 10 <= percent <= 100 or percent % 10:
        raise FleetError("Invalid speed")
    info = plan(empire, player_id, ships, target, percent)
    if mission not in info["missions"]:
        raise FleetError("This mission is not possible here")

    cargo = {res: max(0, int(float(params.get(res, 0) or 0))) for res in RESOURCES}
    if sum(cargo.values()) > info["empty_cargobays"]:
        raise FleetError("Not enough cargo space")
    need = dict(cargo, deuterium=cargo["deuterium"] + info["fuel_consumption"])
    if any(origin[res] < need[res] for res in RESOURCES):
        raise FleetError("Not enough resources (deuterium is also needed as fuel)")
    for res in RESOURCES:
        origin[res] -= need[res]
    for ftype, n in ships.items():
        planet_rules.fleet(origin, ftype)["amount"] -= n

    flight = info["total_time"]
    conn = db.connect()
    with db.lock(), conn:
        conn.execute(
            "INSERT INTO fleet_events (owner_id, origin_planet_id, target_planet_id, target_galaxy, target_system, "
            "target_position, mission, ships, cargo, start_time, arrival_time, return_time, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'outbound')",
            (player_id, origin["planet_id"], target_planet(target)["id"], *target, mission,
             json.dumps({str(t): n for t, n in ships.items()}), json.dumps(cargo),
             int(now), int(now) + flight, int(now) + 2 * flight))


def recall(player_id: int, event_id: int, now: float) -> None:
    """Turn a fleet around. It flies back as long as it has been flying."""
    conn = db.connect()
    with db.lock(), conn:
        row = conn.execute("SELECT * FROM fleet_events WHERE id = ? AND owner_id = ?", (event_id, player_id)).fetchone()
        if row is None or row["status"] != "outbound" or row["arrival_time"] <= now:
            raise FleetError("This fleet cannot be recalled")
        elapsed = int(now) - row["start_time"]
        conn.execute("UPDATE fleet_events SET status = 'returning', turn_time = ?, return_time = ? WHERE id = ?",
                     (int(now), int(now) + max(1, elapsed), event_id))


# --- Processing -----------------------------------------------------------------------------


def _ships(row) -> dict[int, int]:
    return {int(t): n for t, n in json.loads(row["ships"]).items()}


def _planet_owner(planet_id: int | None) -> int | None:
    if not planet_id:
        return None
    row = db.connect().execute("SELECT player_id FROM planets WHERE id = ?", (planet_id,)).fetchone()
    return row["player_id"] if row else None


def _arrive(row) -> str:
    """Apply the mission at the target. Returns the new status of the flight."""
    cargo = json.loads(row["cargo"])
    target_owner = _planet_owner(row["target_planet_id"])
    if target_owner is None:
        return "returning"  # the planet is gone: fly back with everything
    with db.player_state(target_owner) as state:
        planet = state["empire"]["main_planet"]
        if planet["planet_id"] != row["target_planet_id"]:
            return "returning"
        planet_rules.tick(state["empire"], row["arrival_time"])
        for res in RESOURCES:
            planet[res] += cargo[res]
        if row["mission"] == DEPLOY:
            for ftype, n in _ships(row).items():
                planet_rules.fleet(planet, ftype)["amount"] += n
    conn = db.connect()
    with db.lock(), conn:
        conn.execute("UPDATE fleet_events SET cargo = ? WHERE id = ?",
                     (json.dumps({res: 0 for res in RESOURCES}), row["id"]))
    return "done" if row["mission"] == DEPLOY else "returning"


def _return(row) -> None:
    with db.player_state(row["owner_id"]) as state:
        planet = state["empire"]["main_planet"]
        planet_rules.tick(state["empire"], row["return_time"])
        for ftype, n in _ships(row).items():
            planet_rules.fleet(planet, ftype)["amount"] += n
        cargo = json.loads(row["cargo"])
        for res in RESOURCES:
            planet[res] += cargo[res]


def process_due(now: float | None = None) -> int:
    """Handle every arrival and return that is due. Returns how many steps were done."""
    now = time.time() if now is None else now
    conn = db.connect()
    rows = conn.execute(
        "SELECT * FROM fleet_events WHERE (status = 'outbound' AND arrival_time <= ?) "
        "OR (status = 'returning' AND return_time <= ?) ORDER BY id", (now, now)).fetchall()
    for row in rows:
        try:
            if row["status"] == "outbound":
                status = _arrive(row)
                with db.lock(), conn:
                    if status == "done":
                        conn.execute("DELETE FROM fleet_events WHERE id = ?", (row["id"],))
                    else:
                        conn.execute("UPDATE fleet_events SET status = 'returning', turn_time = arrival_time "
                                     "WHERE id = ?", (row["id"],))
            else:
                _return(conn.execute("SELECT * FROM fleet_events WHERE id = ?", (row["id"],)).fetchone())
                with db.lock(), conn:
                    conn.execute("DELETE FROM fleet_events WHERE id = ?", (row["id"],))
        except Exception:
            log.exception("fleet event %s failed", row["id"])
    return len(rows)


# --- What the client sees -------------------------------------------------------------------


def _planet_label(planet_id: int | None, galaxy: int, system: int, position: int) -> tuple[str, str]:
    name = ""
    if planet_id:
        row = db.connect().execute("SELECT name FROM planets WHERE id = ?", (planet_id,)).fetchone()
        name = row["name"] if row else ""
    return name, f"{galaxy}:{system}:{position}"


def events_view(player_id: int) -> list[dict]:
    """The player's flights as `empire.fleet_hanging_events`."""
    rows = db.connect().execute("SELECT * FROM fleet_events WHERE owner_id = ? ORDER BY id", (player_id,)).fetchall()
    events = []
    for row in rows:
        origin = db.connect().execute("SELECT * FROM planets WHERE id = ?", (row["origin_planet_id"],)).fetchone()
        start_name, start_pos = _planet_label(row["origin_planet_id"], origin["galaxy"], origin["system"],
                                              origin["position"])
        end_name, end_pos = _planet_label(row["target_planet_id"], row["target_galaxy"], row["target_system"],
                                          row["target_position"])
        returning = row["status"] == "returning"
        finish = row["return_time"] if returning else row["arrival_time"]
        leg_start = row["turn_time"] if returning else row["start_time"]
        events.append({
            "event_id": row["id"],
            "planet_id": row["origin_planet_id"],
            "mission": row["mission"],
            "origin_mission": row["mission"],
            "mission_name": MISSION_NAMES.get(row["mission"], ""),
            "start_planet_name": start_name,
            "start_planet_pos": start_pos,
            "end_planet_id": row["target_planet_id"] or 0,
            "end_planet_name": end_name,
            "end_planet_pos": end_pos,
            "distance_time": row["arrival_time"] - row["start_time"],
            "is_returning": returning,
            "finish_time": finish,
            "total_time": max(1, finish - leg_start),
            "acs_status": 0,
            "show_hanging": True,
            "fleets": [{"fleet_type": t, "fleet_count": n} for t, n in _ships(row).items()],
        })
    return events
