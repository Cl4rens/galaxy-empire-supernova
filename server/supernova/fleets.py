"""Fleet missions: sending ships between planets, arrival and return.

Flights are stored in the fleet_events table, not in the player state, because a mission can
change another player's planet. A background task in the server calls `process_due` every
second, so missions also complete while their owner is offline.

Implemented missions: transport, deploy, espionage, attack (players and pirates), recycle,
colonize and expedition. Arrivals and returns send reports to
the mailbox.
"""

import json
import logging
import math
import random
import time

from . import combat, config, db, ranking
from . import specials, vip
from . import economy as eco
from . import daily, devices, ecm, flagships, items, officers, kryptons, lordship, mails, npc, protection, quests, repairs
from . import planet as planet_rules
from . import universe

log = logging.getLogger("supernova")

EXPEDITION, COLONY, RECYCLE, TRANSPORT, DEPLOY, ESPIONAGE, ATTACK = 1, 2, 3, 4, 5, 6, 7
TRANSMIT = 16  # jump through the transmit portals of two own moons
ACS_ATTACK, ACS_JOIN = 8, 21  # joining a coordinated attack (8 in the dispatch screen, 21 for the flight)
MISSION_NAMES = {
    EXPEDITION: "Expedition", COLONY: "Colonize", RECYCLE: "Recycle", TRANSPORT: "Transport",
    DEPLOY: "Deploy", ESPIONAGE: "Espionage", ATTACK: "Attack", TRANSMIT: "Transmit",
    ACS_JOIN: "Coordinated attack ",
}
UNDER_ATTACK = 11  # how incoming attacks appear in the defender's flight list
ATTACK_PIRATES = 13  # report type of battles against pirate bases
MOON_CHANCE_DEBRIS = 100_000  # each this much debris gives 1 % moon chance ...
MOON_CHANCE_MAX = 20  # ... up to this many percent
SCAN_DEUTERIUM = 5000  # a sensor array scan costs this much deuterium
PORTAL_RECHARGE_SECONDS = 3600  # at transmit portal level 1, before game speed
QUEST_ACTIONS = {ESPIONAGE: quests.ESPIONAGE, EXPEDITION: quests.EXPEDITION, TRANSPORT: quests.TRANSPORT,
                 DEPLOY: quests.DEPLOY, RECYCLE: quests.RECYCLE}
DAILY_ACTIONS = {ESPIONAGE: daily.ESPIONAGE, EXPEDITION: daily.EXPEDITION, RECYCLE: daily.RECYCLE,
                 TRANSPORT: daily.TRANSPORT}
BE_ESPIONAGE, BE_TRANSPORT = 14, 15  # report types for the target's side
ESPIONAGE_PROBE = 13
RECYCLER = 12
COLONY_SHIP = 11
RESOURCES = planet_rules.RESOURCES


class FleetError(planet_rules.GameError):
    pass


# --- Planning a flight --------------------------------------------------------------------


def _research_levels(empire: dict) -> dict[int, int]:
    return {r["research_type"]: r["research_level"] for r in empire["researches"]}


def ship_speed(empire: dict, ftype: int) -> int:
    """Speed with drive research and thrusters (ship modification)."""
    base = eco.ship_speed(ftype, _research_levels(empire))
    return int(base * (1 + (devices.bonus(empire, ftype, devices.SPEED) + vip.benefit(empire, "flight_speed")) / 100))


def secure_explorations(empire: dict, now: float | None = None) -> int:
    """Expeditions a day: by Astrophysics, more with VIP (gamma detectors add beyond)."""
    return (eco.safe_explorations(planet_rules.research_level(empire, eco.ASTROPHYSICS))
            + vip.benefit(empire, "secure_explorations", now))


def fleet_slots(empire: dict, now: float | None = None) -> int:
    """Flights at once besides expeditions: Computer Tech, a VIP flight route and an Admiral."""
    return (eco.fleet_slots(planet_rules.research_level(empire, eco.COMPUTER_TECH))
            + vip.benefit(empire, "flight_routes", now) + officers.fleet_slots(empire, now))


def flights_in_air(player_id: int) -> int:
    return db.connect().execute("SELECT COUNT(*) FROM fleet_events WHERE owner_id = ? AND mission != ?",
                                (player_id, EXPEDITION)).fetchone()[0]


def check_fleet_slot(player_id: int, empire: dict, now: float) -> None:
    slots = fleet_slots(empire, now)
    if flights_in_air(player_id) >= slots:
        raise FleetError(f"All {slots} flight routes are in use. Every two levels of Computer Tech add one.")


def _expeditions_from(planet_id: int) -> int:
    return db.connect().execute("SELECT COUNT(*) FROM fleet_events WHERE origin_planet_id = ? AND mission = ?",
                                (planet_id, EXPEDITION)).fetchone()[0]


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


def target_planet(target: tuple[int, int, int], moon: bool = False):
    galaxy, system, position = target
    return db.connect().execute(
        "SELECT id, player_id, name FROM planets WHERE galaxy = ? AND system = ? AND position = ? AND kind = ?",
        (galaxy, system, position, universe.MOON if moon else universe.PLANET)).fetchone()


def origin_planet(empire: dict, params: dict) -> dict:
    start = params.get("start_p_id") or params.get("planet_id")
    origin = planet_rules.find_planet(empire, int(start)) if start else empire["main_planet"]
    if origin is None:
        raise FleetError("This is not your planet")
    return origin


def targets_moon(params: dict) -> bool:
    """The dispatch screen sends planet_type=moon when the player picked the moon."""
    return params.get("planet_type") == "moon"


def debris_at(target: tuple[int, int, int]) -> dict[str, int]:
    row = db.connect().execute("SELECT metal, crystal FROM debris WHERE galaxy = ? AND system = ? AND position = ?",
                               target).fetchone()
    return {"metal": row["metal"], "crystal": row["crystal"]} if row else {"metal": 0, "crystal": 0}


def allowed_missions(player_id: int, origin: dict, target: tuple[int, int, int], ships: dict[int, int],
                     moon: bool = False) -> list[int]:
    recycle = [RECYCLE] if RECYCLER in ships and any(debris_at(target).values()) else []
    row = target_planet(target, moon)
    if row is None and moon:
        return recycle
    if row is None:
        if COLONY_SHIP in ships and target[2] <= universe.PLANET_POSITIONS:
            return [COLONY] + recycle
        if target[2] == universe.EXPEDITION_POSITION:
            return [EXPEDITION]
        if target[2] in universe.PIRATE_POSITIONS:
            pirates = [ESPIONAGE] if ESPIONAGE_PROBE in ships else []
            if any(combat.STATS[t].weapon > 0 for t in ships):
                pirates.append(ATTACK)
            return pirates + recycle
        return recycle
    if row["id"] == origin["planet_id"]:
        return recycle
    missions = [TRANSPORT] + recycle
    if row["player_id"] == player_id:
        missions.append(DEPLOY)
        if moon and planet_rules.is_moon(origin) and planet_rules.level(origin, eco.TRANSMIT_PORTAL):
            missions.append(TRANSMIT)
    else:
        if ESPIONAGE_PROBE in ships:
            missions.append(ESPIONAGE)
        if any(combat.STATS[t].weapon > 0 for t in ships) and \
                protection.can_attack(player_id, row["player_id"]) == protection.ATTACK_OK and \
                not specials.target_at_peace(row["id"], time.time()):
            missions.append(ATTACK)
    return missions


def plan(empire: dict, player_id: int, ships: dict[int, int], target: tuple[int, int, int],
         percent: int = 100, origin: dict | None = None, moon: bool = False) -> dict:
    origin = origin or empire["main_planet"]
    black_hole = specials.projection_bonus(origin, "flight_speed", time.time())
    speed = int(min(ship_speed(empire, t) for t in ships) * (1 + black_hole / 100))
    dist = eco.distance(_position(origin), target)
    return {
        "missions": allowed_missions(player_id, origin, target, ships, moon),
        "speed": speed,
        "distance": dist,
        "total_time": eco.flight_seconds(dist, speed, percent),
        "fuel_consumption": eco.fuel(ships, dist, percent,
                                     planet_rules.research_level(empire, eco.HYPERSPACE_TECH)),
        "empty_cargobays": eco.cargo_capacity(ships),
    }


# --- Sending and recalling ------------------------------------------------------------------


def send(player_id: int, state: dict, params: dict, now: float) -> None:
    empire = state["empire"]
    origin = origin_planet(empire, params)
    planet_rules.tick(empire, now)
    ships = parse_ships(origin, params.get("fleet_ids", ""), params.get("fleet_cnt_ids", ""))
    target = parse_target(params.get("end_p_pos", ""))
    mission = int(params.get("mission", 0))
    percent = int(params.get("speed_ratio", 100))
    if not 10 <= percent <= 100 or percent % 10:
        raise FleetError("Invalid speed")
    moon = targets_moon(params)
    info = plan(empire, player_id, ships, target, percent, origin, moon)
    if mission not in info["missions"]:
        target_row = target_planet(target, moon)
        if mission == ATTACK and target_row and target_row["player_id"] != player_id:
            verdict = protection.can_attack(player_id, target_row["player_id"])
            if verdict != protection.ATTACK_OK:
                raise FleetError(protection.MESSAGES[verdict])
            if specials.target_at_peace(target_row["id"], now):
                raise FleetError("This planet is protected by a peace agreement")
        raise FleetError("This mission is not possible here")
    if mission == TRANSMIT:
        _transmit(empire, origin, target, ships, now)
        return
    if mission == COLONY and len(empire["planets"]) >= max_planets(empire):
        raise FleetError("Research more Astrophysics to rule more planets")
    if mission != EXPEDITION:
        check_fleet_slot(player_id, empire, now)
    if mission == EXPEDITION:
        astrophysics = planet_rules.research_level(empire, eco.ASTROPHYSICS)
        if not astrophysics:
            raise FleetError("Research Astrophysics to send expeditions")
        if _expeditions_from(origin["planet_id"]) >= eco.expedition_fleets(astrophysics):
            raise FleetError(f"This planet can have {eco.expedition_fleets(astrophysics)} expedition fleet(s) "
                             "out at once; more Astrophysics allows more")
        if npc.expeditions_today(player_id, now) >= secure_explorations(empire, now):
            if empire.get("secure_explorations", 0) <= 0:
                raise FleetError("No more secure explorations today; a Gamma Detector gives more")
            empire["secure_explorations"] -= 1  # a gamma detector's
        npc.record_expedition(player_id, now)

    limit = flagships.behemoth_limit(state)
    if ships.get(flagships.BEHEMOTH, 0) > limit:
        raise FleetError(f"A fleet can take at most {limit} Iron Behemoth(s). Research Dynamic Power Tech for more.")
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
    target_row = target_planet(target, moon)
    target_id = target_row["id"] if target_row else None  # empty space, e.g. a debris field
    quest_action = QUEST_ACTIONS.get(mission)
    if mission == ATTACK:
        quest_action = quests.ATTACK_PLAYER if target_row else quests.ATTACK_PIRATES
    if mission == ATTACK and target_row and target_row["player_id"] != player_id:
        specials.end_peace(empire)  # attacking ends your own peace agreements
    if quest_action:
        quests.record_action(state, quest_action)
    if mission in DAILY_ACTIONS or (mission == ATTACK and not target_row):
        daily.record(state, DAILY_ACTIONS.get(mission, daily.ATTACK_PIRATES))
    conn = db.connect()
    with db.lock(), conn:
        cur = conn.execute(
            "INSERT INTO fleet_events (owner_id, origin_planet_id, target_planet_id, target_galaxy, target_system, "
            "target_position, mission, ships, cargo, start_time, arrival_time, return_time, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'outbound')",
            (player_id, origin["planet_id"], target_id, *target, mission,
             json.dumps({str(t): n for t, n in ships.items()}), json.dumps(cargo),
             int(now), int(now) + flight, int(now) + 2 * flight))
    if mission == EXPEDITION:
        specials.navigator_start(empire, cur.lastrowid, ships)


def portal_recharge_seconds(portal_level: int) -> int:
    return max(60, int(PORTAL_RECHARGE_SECONDS * 0.7 ** (portal_level - 1) / config.SPEED))


def _transmit(empire: dict, origin: dict, target: tuple[int, int, int], ships: dict[int, int], now: float) -> None:
    """Move ships at once between two own moons with transmit portals, which then recharge."""
    row = target_planet(target, moon=True)
    destination = planet_rules.find_planet(empire, row["id"]) if row else None
    if destination is None or not planet_rules.level(destination, eco.TRANSMIT_PORTAL):
        raise FleetError("The target moon needs a transmit portal")
    for moon in (origin, destination):
        if moon["transmit_recharge_event"]:
            raise FleetError("The transmit portal is still recharging")
    if any(ftype not in eco.SHIPS for ftype in ships):
        raise FleetError("Only ships can pass the transmit portal")
    for ftype, n in ships.items():
        planet_rules.fleet(origin, ftype)["amount"] -= n
        planet_rules.fleet(destination, ftype)["amount"] += n
    for moon in (origin, destination):
        seconds = portal_recharge_seconds(planet_rules.level(moon, eco.TRANSMIT_PORTAL))
        moon["transmit_recharge_event"] = [{"start_time": int(now), "finish_time": int(now) + seconds,
                                            "total_time": seconds}]


def scan(empire: dict, moon_id: int, planet_id: int, now: float) -> list[dict]:
    """Sensor array: the flights from and to a planet in range of the moon. The result is kept
    in the moon's "last_scan" so the player can save it as a mail."""
    moon = planet_rules.find_planet(empire, moon_id)
    if moon is None or not planet_rules.is_moon(moon) or not planet_rules.level(moon, eco.SENSOR_ARRAY):
        raise FleetError("You need a moon with a sensor array")
    target = db.connect().execute("SELECT * FROM planets WHERE id = ?", (planet_id,)).fetchone()
    if target is None:
        raise FleetError("There is nothing to scan")
    if target["galaxy"] != moon["galaxy_position"] or \
            abs(target["system"] - moon["system_position"]) > planet_rules.sensor_range(moon):
        raise FleetError("The target is out of the sensor array's range")
    planet_rules.tick(empire, now)
    if moon["deuterium"] < SCAN_DEUTERIUM:
        raise FleetError(f"A scan needs {SCAN_DEUTERIUM:,} deuterium")
    moon["deuterium"] -= SCAN_DEUTERIUM
    rows = db.connect().execute(
        "SELECT * FROM fleet_events WHERE origin_planet_id = ? OR target_planet_id = ? ORDER BY id",
        (planet_id, planet_id)).fetchall()
    found = []
    for row in rows:
        origin = db.connect().execute("SELECT * FROM planets WHERE id = ?", (row["origin_planet_id"],)).fetchone()
        start_name, start_pos = _planet_label(row["origin_planet_id"], origin["galaxy"], origin["system"],
                                              origin["position"])
        end_name, end_pos = _planet_label(row["target_planet_id"], row["target_galaxy"], row["target_system"],
                                          row["target_position"])
        returning = row["status"] == "returning"
        ships = _ships(row)
        found.append({
            "mission": row["mission"],
            "start_planet_name": start_name, "start_position": start_pos,
            "end_planet_name": end_name, "end_position": end_pos,
            "start_time": row["turn_time"] if returning else row["start_time"],
            "finish_time": row["return_time"] if returning else row["arrival_time"],
            "is_returning": int(returning),
            "amount": sum(ships.values()),
            "fleets": [{"fleet_type": t, "name": eco.UNITS[t].name if t in eco.UNITS else str(t), "fleet_count": n}
                       for t, n in ships.items()],
        })
    moon["last_scan"] = {"planet_id": planet_id, "planet_name": target["name"], "time": int(now), "flights": found,
                         "position": f"{target['galaxy']}:{target['system']}:{target['position']}"}
    return found


def save_scan(player_id: int, empire: dict, moon_id: int, planet_id: int) -> None:
    """Mail the moon's last scan of `planet_id` to the player."""
    moon = planet_rules.find_planet(empire, moon_id)
    last = moon.get("last_scan") if moon else None
    if not last or last["planet_id"] != planet_id:
        raise FleetError("Scan the planet first")
    body = {"head": "Scan report ", "head_name": f"{last['planet_name']} [{last['position']}]", "body": [{
        "title": "Flight ",
        "mission": MISSION_NAMES.get(f["mission"], ""),
        "mission_type": f["mission"],
        "time_left": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(f["finish_time"])),
        "from_name": f["start_planet_name"], "from_position": f["start_position"],
        "to_name": f["end_planet_name"], "to_position": f["end_position"],
        "fleets": f["fleets"],
    } for f in last["flights"]]}
    mails.send(player_id, mails.SCAN, mails.SENDER_SENSOR, _username(player_id),
               f"Scan report [{last['position']}]", body, last["time"])


def planet_has_flights(planet_id: int) -> bool:
    return db.connect().execute("SELECT 1 FROM fleet_events WHERE origin_planet_id = ? OR target_planet_id = ?",
                                (planet_id, planet_id)).fetchone() is not None


def recall(player_id: int, event_id: int, now: float) -> None:
    """Turn a fleet around. It flies back as long as it has been flying."""
    conn = db.connect()
    with db.lock(), conn:
        row = conn.execute("SELECT * FROM fleet_events WHERE id = ? AND owner_id = ?", (event_id, player_id)).fetchone()
        if row is None or row["status"] != "outbound" or row["arrival_time"] <= now:
            raise FleetError("This fleet cannot be recalled")
        group = [row]
        if row["mission"] == ATTACK:  # a coordinated attack turns with its leader
            group += conn.execute("SELECT * FROM fleet_events WHERE acs_id = ? AND status = 'outbound'",
                                  (event_id,)).fetchall()
        for r in group:
            elapsed = int(now) - r["start_time"]
            conn.execute("UPDATE fleet_events SET status = 'returning', turn_time = ?, return_time = ? WHERE id = ?",
                         (int(now), int(now) + max(1, elapsed), r["id"]))


# --- Processing -----------------------------------------------------------------------------


def _ships(row) -> dict[int, int]:
    return {int(t): n for t, n in json.loads(row["ships"]).items()}


def _planet_owner(planet_id: int | None) -> int | None:
    if not planet_id:
        return None
    row = db.connect().execute("SELECT player_id FROM planets WHERE id = ?", (planet_id,)).fetchone()
    return row["player_id"] if row else None


def _username(player_id: int) -> str:
    row = db.connect().execute("SELECT username FROM players WHERE id = ?", (player_id,)).fetchone()
    return row["username"] if row else ""


def _event_info(row, finish_time: int) -> dict:
    """The flight as mission reports describe it."""
    origin = db.connect().execute("SELECT * FROM planets WHERE id = ?", (row["origin_planet_id"],)).fetchone()
    start_name, start_pos = _planet_label(row["origin_planet_id"], origin["galaxy"], origin["system"],
                                          origin["position"])
    end_name, end_pos = _planet_label(row["target_planet_id"], row["target_galaxy"], row["target_system"],
                                      row["target_position"])
    cargo = json.loads(row["cargo"])
    return {
        "mission": row["mission"],
        "mission_name": MISSION_NAMES.get(row["mission"], ""),
        "start_planet_name": start_name,
        "start_planet_pos": start_pos,
        "end_planet_name": end_name,
        "end_planet_pos": end_pos,
        "finish_time": finish_time,
        "resource_metal": cargo["metal"],
        "resource_crystal": cargo["crystal"],
        "resource_deuterium": cargo["deuterium"],
        "hanging_fleets": [{"fleet_type": t, "fleet_count": n} for t, n in _ships(row).items()],
    }


def espionage_level(probes: int, own_tech: int, target_tech: int) -> int:
    """How much a report reveals (OGame): probes, adjusted by the espionage technology gap."""
    gap = own_tech - target_tech
    return probes + gap * abs(gap)


def probe_result(target_state: dict, level: int, planet: dict) -> dict:
    """What the probes see: resources always; more with a higher level (OGame thresholds)."""
    empire = target_state["empire"]
    rates = planet_rules.production(planet, empire)
    result = {
        "metal": int(planet["metal"]),
        "crystal": int(planet["crystal"]),
        "deuterium": int(planet["deuterium"]),
        "energy_max": round(rates["energy_max"]),
        "krypton_stone": 0,
        "pirate_krypton_level": [0],  # read by the client for every report
    }
    def units(category):
        return [{"fleet_type": f["fleet_type"], "fleet_name": planet_rules.fleet_view(planet, f)["fleet_name"],
                 "amount": f["amount"]}
                for f in planet["fleets"]
                if f["amount"] and (f["fleet_type"] in eco.SHIPS) == (category == "ships")]
    if level >= 2:
        result["fleets"] = units("ships")
    if level >= 3:
        result["defenses"] = units("defense")
        result["radar_able"] = False
    if level >= 5:
        result["buildings"] = [{"building_type": b["building_type"],
                                "building_name": eco.BUILDINGS[b["building_type"]].name,
                                "building_level": b["building_level"]}
                               for b in planet["buildings"] if b["building_level"]]
    return result


def _espionage(row, target_owner: int, state: dict, planet: dict) -> None:
    owner_state = db.load_state(row["owner_id"])
    level = espionage_level(_ships(row).get(ESPIONAGE_PROBE, 0),
                            planet_rules.research_level(owner_state["empire"], eco.ESPIONAGE_TECH)
                            + officers.espionage_levels(owner_state["empire"], row["arrival_time"]),
                            planet_rules.research_level(state["empire"], eco.ESPIONAGE_TECH)
                            + officers.espionage_levels(state["empire"], row["arrival_time"]))
    info = _event_info(row, row["arrival_time"])
    spy, victim = _username(row["owner_id"]), _username(target_owner)
    mails.send(row["owner_id"], mails.REPORT, mails.SENDER_FLEET, spy,
               f"Espionage report [{info['end_planet_pos']}]",
               {"mission_type": ESPIONAGE, "is_returning": False, "event_info": info,
                "probe_result": probe_result(state, level, planet)}, row["arrival_time"])
    mails.send(target_owner, mails.REPORT, mails.SENDER_FLEET, victim,
               f"Espionage activity [{info['end_planet_pos']}]",
               {"mission_type": BE_ESPIONAGE, "is_returning": False,
                "event_info": {"espionage_user": spy, "espionage_planet_pos": info["start_planet_pos"],
                               "be_espionage_user": victim, "be_espionage_planet_pos": info["end_planet_pos"]}},
               row["arrival_time"])


def add_debris(galaxy: int, system: int, position: int, metal: int, crystal: int) -> None:
    if metal <= 0 and crystal <= 0:
        return
    conn = db.connect()
    with db.lock(), conn:
        conn.execute(
            "INSERT INTO debris (galaxy, system, position, metal, crystal) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (galaxy, system, position) DO UPDATE SET metal = metal + excluded.metal, "
            "crystal = crystal + excluded.crystal", (galaxy, system, position, metal, crystal))


def _tech_bonus(empire: dict) -> dict:
    return {
        "attack_bonus_percent": 10 * planet_rules.research_level(empire, eco.WEAPONS_TECH),
        "shield_bonus_percent": 10 * planet_rules.research_level(empire, eco.SHIELDING_TECH),
        "armour_bonus_percent": 10 * planet_rules.research_level(empire, eco.ARMOUR_TECH),
    }


def _side(name: str, empire: dict, units: dict[int, int], planet: dict | None = None,
          state: dict | None = None) -> combat.Side:
    """A player's side; `planet` (where the fleet starts or the defense stands) adds its krypton
    bonuses, Behemoths in `units` raise the attack (`state` gives the player's Behemoth limit)."""
    planet = planet or {}
    behemoths = units.get(flagships.BEHEMOTH, 0)
    behemoth_bonus = flagships.attack_bonus(behemoths, flagships.behemoth_limit(state)) if state and behemoths else 0
    ecm_bonus = ecm.percent(state) if state else 0
    lord = lordship.bonus(planet, empire.get("user_id", 0)) if planet and empire.get("user_id") else {}
    return combat.Side(name, units, planet_rules.research_level(empire, eco.WEAPONS_TECH),
                       planet_rules.research_level(empire, eco.SHIELDING_TECH),
                       planet_rules.research_level(empire, eco.ARMOUR_TECH),
                       kryptons.bonus(planet, "attack") + behemoth_bonus + ecm_bonus + lord.get("increase_attack", 0)
                       + vip.benefit(empire, "attack") + specials.projection_bonus(planet, "attack"),
                       kryptons.bonus(planet, "shield") + ecm_bonus + lord.get("increase_shield", 0)
                       + vip.benefit(empire, "shield") + specials.projection_bonus(planet, "shield"),
                       kryptons.bonus(planet, "armour") + ecm_bonus + lord.get("increase_armour", 0)
                       + vip.benefit(empire, "armour") + specials.projection_bonus(planet, "armour"),
                       devices.unit_bonuses(empire), planet_rules.research_level(empire, eco.MISSILE_TECH),
                       officers.defense_save(empire))


def _salvage(player_id: int, debris: dict, own_units: dict[int, int], enemy_lost: dict[int, int],
             state: dict | None = None, gains: dict | None = None) -> str:
    """Alloy fragments (and dark energy, when Behemoths fought) for one side of a battle. The
    amounts also go into `gains` (alloy, dark_energy) for the battle report."""
    gains = {} if gains is None else gains
    gains.update(alloy=0, dark_energy=0, galaxium=0)

    def apply(s: dict) -> str:
        notes = []
        alloy = flagships.salvage(s, debris)
        gains["alloy"] = alloy
        if alloy:
            notes.append(f"Alloy fragments: {alloy:,}")
        components = items.add_components(s, enemy_lost)
        if components:
            notes.append(f"Intricate components: {components:,}")
        gains["galaxium"] = items.galaxium_for(enemy_lost)
        if gains["galaxium"]:
            items.add(s, items.GALAXIUM, gains["galaxium"])
            notes.append(f"Galaxium: {gains['galaxium']:,}")
        if own_units.get(flagships.BEHEMOTH):
            lost_points = sum(sum(eco.unit_cost(t)) * n for t, n in enemy_lost.items() if t in eco.UNITS) // 1000
            gains["dark_energy"] = flagships.gather_dark_energy(s, lost_points)
            notes.append(f"Dark energy: {gains['dark_energy']:,}")
        return ", ".join(notes)
    if state is not None:
        return apply(state)
    with db.player_state(player_id) as s:
        return apply(s)


def unit_info(empire: dict, planet: dict, ftype: int) -> dict:
    """The unit info screen: values with the empire's technologies and the planet's kryptons."""
    side = _side("", empire, {ftype: 1}, planet)
    stats = combat.STATS[ftype]

    def bonus(base: float, value: float) -> int:
        return round(value) - round(base)

    names = {**{t: r.name for t, r in eco.UNITS.items()}, **eco.SPECIAL_UNITS}
    rapid = [{"unit_name": names.get(t, str(t)), "damage_rate": n, "bonus_damage_rate": 0}
             for t, n in combat.RAPID_FIRE.get(ftype, {}).items()]
    rapid += [{"unit_name": names.get(t, str(t)), "damage_rate": -n, "bonus_damage_rate": 0}
              for t, against in combat.RAPID_FIRE.items() for target, n in against.items() if target == ftype]
    info = {"fatal_damage_list": rapid,
            "armour": round(stats.hull), "bonus_armour": bonus(stats.hull, side.hull_of(ftype)),
            "shield": stats.shield, "bonus_shield": bonus(stats.shield, side.shield_of(ftype)),
            "attack": stats.weapon, "bonus_attack": bonus(stats.weapon, side.weapon_of(ftype)),
            "speed": 0, "bonus_speed": 0, "capacity": 0, "fuel_usage": 0}
    if ftype in eco.SHIPS:
        ship = eco.SHIPS[ftype]
        speed = ship_speed(empire, ftype)
        info.update(speed=ship.speed, bonus_speed=speed - ship.speed, capacity=ship.cargo, fuel_usage=ship.fuel)
    return info


def _krypton_bonus(side: combat.Side) -> dict:
    return {"attack_bonus_percent": side.attack_percent, "shield_bonus_percent": side.shield_percent,
            "armour_bonus_percent": side.armour_percent}


def _battle(row, attacker: combat.Side, attacker_bonus: dict, defender: combat.Side, defender_bonus: dict) -> dict:
    """Fight and build the battle report's attack_result. The caller applies losses and loot."""
    result = combat.fight(attacker, defender)
    result["attack_lost"] = combat.losses(attacker.units, result["attacker_left"])
    result["defend_lost"] = combat.losses(defender.units, result["defender_left"])
    result["rebuilt"] = combat.rebuild_defense(result["defend_lost"], save=defender.defense_save)
    lost = {t: result["attack_lost"].get(t, 0) + result["defend_lost"].get(t, 0)
            for t in {*result["attack_lost"], *result["defend_lost"]}}
    result["debris"] = combat.debris(lost)  # only ships leave debris
    rounds = list(result["rounds"])
    if result["rebuilt"]:
        rounds.append({"attack_recovered_fleets": [], "defend_recovered_fleets": combat._fleet_list(result["rebuilt"]),
                       "attack_survived_fleets": [], "defend_survived_fleets": []})
    result["report"] = {
        "attacker_name": attacker.name,
        "defender_name": defender.name,
        "win": {"attacker": "win", "defender": "lose", "draw": "draw"}[result["winner"]],
        "winner_name": {"attacker": attacker.name, "defender": defender.name}.get(result["winner"]),
        "attack_start_fleets_count": attacker.count(),
        "attack_end_fleets_count": sum(result["attacker_left"].values()),
        "defend_start_fleets_count": defender.count(),
        "defend_end_fleets_count": sum(result["defender_left"].values()) + sum(result["rebuilt"].values()),
        "attack_bonus_info": {"tech_bonus": attacker_bonus, "kry_bonus": _krypton_bonus(attacker)},
        "defend_bonus_info": {"tech_bonus": defender_bonus, "kry_bonus": _krypton_bonus(defender)},
        "attack_bonus": attacker_bonus,
        "defend_bonus": defender_bonus,
        "attack_krypton_bonus": _krypton_bonus(attacker),
        "defend_krypton_bonus": _krypton_bonus(defender),
        "round": rounds,
        "debirs": result["debris"],
        # the client's full report: missiles, Annihilators, intricate components, loot ("booty")
        "intercept_missile_info": result["intercept_missile_info"],
        "interplanetary_missile_info": result["interplanetary_missile_info"],
        "self_destruction": result["self_destruction"],
        "reaper_frag_info": {"attacker_reaper_frag_got": items.components_for(result["defend_lost"]),
                             "defender_reaper_frag_got": items.components_for(result["attack_lost"])},
        "booty": {"metal": 0, "crystal": 0, "deuterium": 0, "krypton": 0},
    }
    return result


# The battle report lists the ship modification of these types (the modification screen's);
# attackers show the first nine. Without this part the client hides missiles and Annihilators.
DEVICE_REPORT_TYPES = (5, 6, 7, 23, 24, 25, 26, 28, 29, 20, 27)


def _device_report(attacker_empire: dict | None, defender_empire: dict | None) -> dict:
    def row(empire, types):
        return [{str(t): devices.bonus(empire, t, devices.ATTACK) if empire else 0} for t in types]
    return {"attack": row(attacker_empire, DEVICE_REPORT_TYPES[:9]), "defend": row(defender_empire, DEVICE_REPORT_TYPES)}


def _loot_text(loot: dict) -> str:
    return f"Loot: {loot['metal']:,} metal, {loot['crystal']:,} crystal, {loot['deuterium']:,} deuterium"


def _fly_home(row, survivors: dict[int, int], cargo: dict) -> str:
    """Status after a battle: survivors fly home with `cargo`."""
    if not survivors:
        return "done"
    conn = db.connect()
    with db.lock(), conn:
        conn.execute("UPDATE fleet_events SET ships = ?, cargo = ? WHERE id = ?",
                     (json.dumps({str(t): n for t, n in survivors.items()}), json.dumps(cargo), row["id"]))
    return "returning"


def _share(total: dict[int, int], parts: list[dict[int, int]]) -> list[dict[int, int]]:
    """Split the surviving units of a group back to its fleets, in proportion to what each sent."""
    shares = [{} for _ in parts]
    for t, left in total.items():
        sent = [p.get(t, 0) for p in parts]
        given = [left * s // max(1, sum(sent)) for s in sent]
        given[sent.index(max(sent))] += left - sum(given)
        for share, n in zip(shares, given):
            if n:
                share[t] = n
    return shares


def _split_loot(loot: dict[str, int], capacities: list[int]) -> list[dict[str, int]]:
    total = max(1, sum(capacities))
    parts = [{res: loot[res] * c // total for res in RESOURCES} for c in capacities]
    if parts:
        biggest = capacities.index(max(capacities))
        for res in RESOURCES:
            parts[biggest][res] += loot[res] - sum(p[res] for p in parts)
    return parts


def _attack(row, target_owner: int, state: dict, planet: dict) -> str:
    """Fight at a player's planet, together with every fleet that joined the attack. Returns the
    new status of the leading flight; the joined flights are updated here."""
    conn = db.connect()
    joined = conn.execute("SELECT * FROM fleet_events WHERE acs_id = ? AND status = 'outbound' ORDER BY id",
                          (row["id"],)).fetchall()
    group = [row] + list(joined)
    owner_state = db.load_state(row["owner_id"])
    names = []
    for r in group:
        if _username(r["owner_id"]) not in names:
            names.append(_username(r["owner_id"]))
    attacker_name, defender_name = " + ".join(names), _username(target_owner)
    defend_units = {f["fleet_type"]: f["amount"] for f in planet["fleets"]
                    if f["amount"] and f["fleet_type"] in combat.STATS}
    sent = [_ships(r) for r in group]
    attack_units = {}
    for ships in sent:
        for t, n in ships.items():
            attack_units[t] = attack_units.get(t, 0) + n
    origin = planet_rules.find_planet(owner_state["empire"], row["origin_planet_id"])
    # The group fights with the technologies and kryptons of the player who started the attack.
    result = _battle(row, _side(attacker_name, owner_state["empire"], attack_units, origin, owner_state),
                     _tech_bonus(owner_state["empire"]),
                     _side(defender_name, state["empire"], defend_units, planet, state), _tech_bonus(state["empire"]))
    gains = {r["owner_id"]: {} for r in group}
    salvage_notes = {r["owner_id"]: _salvage(r["owner_id"], result["debris"], _ships(r), result["defend_lost"],
                                             gains=gains[r["owner_id"]]) for r in group}
    defender_gains: dict = {}
    defender_note = _salvage(target_owner, result["debris"], defend_units, result["attack_lost"], state,
                             gains=defender_gains)
    for f in planet["fleets"]:
        if f["fleet_type"] in defend_units:
            f["amount"] = result["defender_left"].get(f["fleet_type"], 0) + result["rebuilt"].get(f["fleet_type"], 0)
    repaired = repairs.add_damaged(planet, result["defend_lost"], state["empire"] if state else None)
    loot = {res: 0 for res in RESOURCES}
    if result["winner"] == "attacker":
        loot = combat.plunder(planet, eco.cargo_capacity(result["attacker_left"]))
        for res in RESOURCES:
            planet[res] -= loot[res]
    add_debris(row["target_galaxy"], row["target_system"], row["target_position"],
               result["debris"]["metal"], result["debris"]["crystal"])
    moon = _maybe_moon(target_owner, planet, result["debris"])
    survivors = _share(result["attacker_left"], sent)
    loots = _split_loot(loot, [eco.cargo_capacity(s) for s in survivors])

    win = result["report"]["win"]
    for owner in {r["owner_id"] for r in group}:
        ranking.record_battle(owner, win)
    ranking.record_battle(target_owner, {"win": "lose", "lose": "win"}.get(win, "draw"))
    info = _event_info(row, row["arrival_time"])
    notes = [_loot_text(loot)] if result["winner"] == "attacker" else []
    if moon:
        notes.append("The debris gathered into a moon.")
    if repaired:
        info["defender_recover_ratio"] = 0
    body = {"mission_type": ATTACK, "is_returning": False, "event_info": info, "notify_message": " ".join(notes),
            "attack_result": dict(result["report"], loot=loot,
                                  booty=dict(loot, krypton=0, attacker_energy_particle=gains[row["owner_id"]]["galaxium"],
                                             defender_energy_particle=defender_gains["galaxium"])),
            "defender_repair_sw": int(bool(repaired)), "device": _device_report(owner_state["empire"], state["empire"]),
            "defend_alloy_detection_num": defender_gains["alloy"],
            "defend_dark_energy_num": defender_gains["dark_energy"]}
    subject = f"Battle report [{info['end_planet_pos']}]"
    for owner in {r["owner_id"] for r in group}:  # the client finds its side by attacker_name
        name = _username(owner)
        own = dict(body, attack_result=dict(body["attack_result"], attacker_name=name,
                                            winner_name=name if result["winner"] == "attacker"
                                            else body["attack_result"]["winner_name"]))
        prefix = f"Coordinated attack: {attacker_name}." if len(names) > 1 else ""
        own["notify_message"] = " ".join(filter(None, [prefix, body["notify_message"], salvage_notes[owner]]))
        if result["winner"] == "attacker":  # wiki: devices come from battles against players
            with db.player_state(owner) as s:
                own["notify_message"] += " " + devices.drop(s, devices.BATTLE_DROPS)
        own.update(attack_alloy_detection_num=gains[owner]["alloy"], attack_dark_energy_num=gains[owner]["dark_energy"])
        mails.send(owner, mails.REPORT, mails.SENDER_FLEET, name, subject, own, row["arrival_time"])
    defender_body = dict(body, notify_message=" ".join(filter(None, [body["notify_message"], defender_note])))
    mails.send(target_owner, mails.REPORT, mails.SENDER_FLEET, defender_name, subject, defender_body,
               row["arrival_time"])
    for r, ships, share in zip(joined, survivors[1:], loots[1:]):
        status = _fly_home(r, ships, share)
        with db.lock(), conn:
            if status == "done":
                conn.execute("DELETE FROM fleet_events WHERE id = ?", (r["id"],))
            else:
                conn.execute("UPDATE fleet_events SET status = 'returning', turn_time = arrival_time WHERE id = ?",
                             (r["id"],))
    return _fly_home(row, survivors[0], loots[0])


def moon_chance(debris: dict) -> int:
    """Percent chance that a battle's debris forms a moon."""
    return min(MOON_CHANCE_MAX, (debris["metal"] + debris["crystal"]) // MOON_CHANCE_DEBRIS)


def _maybe_moon(owner: int, planet: dict, debris: dict) -> dict | None:
    from . import player  # player imports this module

    if planet_rules.is_moon(planet) or planet.get("moon") or random.uniform(0, 100) >= moon_chance(debris):
        return None
    return player.add_moon(owner, planet)


def _attack_pirates(row) -> str:
    target = (row["target_galaxy"], row["target_system"], row["target_position"])
    base = npc.pirate_base(*target, now=row["arrival_time"])
    owner_state = db.load_state(row["owner_id"])
    attacker_name = _username(row["owner_id"])
    level = base["level"]
    pirates = npc.pirate_side(base["fleet"], level)
    pirate_bonus = {"attack_bonus_percent": 10 * level, "shield_bonus_percent": 10 * level,
                    "armour_bonus_percent": 10 * level}
    origin = planet_rules.find_planet(owner_state["empire"], row["origin_planet_id"])
    result = _battle(row, _side(attacker_name, owner_state["empire"], _ships(row), origin, owner_state),
                     _tech_bonus(owner_state["empire"]), pirates, pirate_bonus)
    gains: dict = {}
    salvage_note = _salvage(row["owner_id"], result["debris"], _ships(row), result["defend_lost"], gains=gains)
    loot = {res: 0 for res in RESOURCES}
    stones = chips = dark_matter = 0
    if result["winner"] == "attacker":
        space = eco.cargo_capacity(result["attacker_left"])
        loot = combat.plunder({r: 2 * v for r, v in base["loot"].items()}, space)  # all of it, if it fits
        if base["fleet"]:
            npc.beat_pirates(*target, row["arrival_time"])
            stones = kryptons.pirate_stones(level)
            chips = level
            dark_matter = npc.pirate_dark_matter(level)
            with db.player_state(row["owner_id"]) as state:
                kryptons.add_stones(state, stones)
                ecm.add_chips(state, chips)
                state["empire"]["dark_matter_count"] = state["empire"].get("dark_matter_count", 0) + dark_matter
    add_debris(*target, result["debris"]["metal"], result["debris"]["crystal"])
    info = _event_info(row, row["arrival_time"])
    info["end_planet_name"] = npc.pirate_name(level)
    notes = [_loot_text(loot)] if result["winner"] == "attacker" else []
    if stones:
        notes.append(f"Krypton stones: {stones:,}, ECM chips: {chips}, dark matter: {dark_matter}")
    if salvage_note:
        notes.append(salvage_note)
    body = {"mission_type": ATTACK_PIRATES, "is_returning": False, "event_info": info,
            "notify_message": ", ".join(notes),
            "attack_result": dict(result["report"], loot=loot,
                                  booty=dict(loot, krypton=0, attacker_energy_particle=gains["galaxium"])),
            "device": _device_report(owner_state["empire"], None),
            "attack_alloy_detection_num": gains["alloy"], "attack_dark_energy_num": gains["dark_energy"]}
    mails.send(row["owner_id"], mails.REPORT, mails.SENDER_FLEET, attacker_name,
               f"Battle report [{info['end_planet_pos']}]", body, row["arrival_time"])
    return _fly_home(row, result["attacker_left"], loot)


def _spy_pirates(row) -> str:
    target = (row["target_galaxy"], row["target_system"], row["target_position"])
    base = npc.pirate_base(*target, now=row["arrival_time"])
    info = _event_info(row, row["arrival_time"])
    result = {**base["loot"], "pirate_name": npc.pirate_name(base["level"]), "pirate_level": base["level"],
              "pirate_krypton_level": [0], "recent_attack_user_list": [],
              "fleets": [{"fleet_type": t, "fleet_name": eco.UNITS[t].name, "amount": n}
                         for t, n in base["fleet"].items()]}
    mails.send(row["owner_id"], mails.REPORT, mails.SENDER_FLEET, _username(row["owner_id"]),
               f"Espionage report [{info['end_planet_pos']}]",
               {"mission_type": ESPIONAGE, "is_returning": False, "event_info": info, "probe_result": result},
               row["arrival_time"])
    return "returning"


def _expedition(row) -> str:
    ships = _ships(row)
    owner = row["owner_id"]
    outcome = npc.explore(ships)
    situation = outcome["situation"]
    cargo = json.loads(row["cargo"])
    capacity = eco.cargo_capacity(ships)
    expedition_result = {"situation": situation, "capacity": capacity}
    if situation == npc.RESOURCES:
        free = capacity - sum(cargo.values())
        taken = {}
        for res in RESOURCES:
            taken[res] = max(0, min(outcome["found"][res], free))
            free -= taken[res]
            cargo[res] += taken[res]
        expedition_result.update(found_resources=outcome["found"], resources=taken)
    elif situation == npc.FLEETS:
        for t, n in outcome["fleets"].items():
            ships[t] = ships.get(t, 0) + n
        expedition_result["fleets"] = [{"fleet_type": t, "amount": n} for t, n in outcome["fleets"].items()]
    elif situation == npc.DARK_MATTER:
        with db.player_state(owner) as state:
            outcome["dark_matter"] = vip.apply_dark_matter(state["empire"], outcome["dark_matter"])
            state["empire"]["dark_matter_count"] += outcome["dark_matter"]
        expedition_result["dark_matter"] = outcome["dark_matter"]
    elif situation == npc.PIRATES:
        owner_state = db.load_state(owner)
        name = _username(owner)
        result = _battle(row, _side(name, owner_state["empire"], ships), _tech_bonus(owner_state["empire"]),
                         npc.pirate_side(outcome["pirates"], 1),
                         {"attack_bonus_percent": 0, "shield_bonus_percent": 0, "armour_bonus_percent": 0})
        lost = combat.losses(ships, result["attacker_left"])
        ships = result["attacker_left"]
        expedition_result["attack_report"] = result["report"]
    elif situation == npc.BLACK_HOLE:
        lost, ships = ships, {}
    if situation in (npc.PIRATES, npc.BLACK_HOLE):
        guider = officers.percent(db.load_state(owner)["empire"], officers.GUIDER)
        saved = {t: n * guider // 100 for t, n in lost.items() if n * guider // 100}
        for t, n in saved.items():
            ships[t] = ships.get(t, 0) + n
        if saved:
            outcome["message"] += f" Your guider brought {sum(saved.values())} ships back."
    if random.random() < devices.EXPEDITION_DROP_CHANCE:
        with db.player_state(owner) as state:
            outcome["message"] += " " + devices.drop(state, random.randint(1, 5))

    now = row["arrival_time"]
    info = _event_info(row, now)
    mails.send(owner, mails.EXPLORE, mails.SENDER_FLEET, _username(owner), "Expedition report",
               {"mission_type": EXPEDITION, "is_returning": False, "event_info": info,
                "notify_message": outcome["message"], "expedition_result": expedition_result,
                "explore_data": {"curr": npc.expeditions_today(owner, now),
                                 "max": secure_explorations(db.load_state(owner)["empire"], now)}},
               now)
    if _explore_again(row, ships, cargo):
        return "outbound"
    return _fly_home(row, ships, cargo)


def _explore_again(row, ships: dict[int, int], cargo: dict) -> bool:
    """A navigated expedition (Nebula Navigator) sets out on its next run from the nebula."""
    owner, now = row["owner_id"], row["arrival_time"]
    with db.player_state(owner) as state:
        empire = state["empire"]
        if str(row["id"]) not in (empire.get("navigator") or {}).get("fleets", {}):
            return False
        origin = planet_rules.find_planet(empire, row["origin_planet_id"])
        if origin:
            planet_rules.tick(empire, now)
        left = npc.expeditions_today(owner, now) < secure_explorations(empire, now)
        ships = specials.navigator_next(empire, row["id"], origin, ships, left)
        if ships is None:
            return False
    npc.record_expedition(owner, now)
    one_way = row["arrival_time"] - row["start_time"]
    conn = db.connect()
    with db.lock(), conn:
        conn.execute("UPDATE fleet_events SET ships = ?, cargo = ?, start_time = ?, arrival_time = ?, "
                     "return_time = ? WHERE id = ?",
                     (json.dumps({str(t): n for t, n in ships.items()}), json.dumps(cargo), now, now + one_way,
                      now + 2 * one_way, row["id"]))
    return True


def _recycle(row) -> str:
    """Recyclers collect debris up to their cargo space, metal first."""
    target = (row["target_galaxy"], row["target_system"], row["target_position"])
    ships = _ships(row)
    space = eco.SHIPS[RECYCLER].cargo * ships.get(RECYCLER, 0)
    conn = db.connect()
    with db.lock(), conn:
        field = debris_at(target)
        metal = min(field["metal"], space)
        crystal = min(field["crystal"], space - metal)
        conn.execute("UPDATE debris SET metal = metal - ?, crystal = crystal - ? "
                     "WHERE galaxy = ? AND system = ? AND position = ?", (metal, crystal, *target))
        conn.execute("DELETE FROM debris WHERE metal <= 0 AND crystal <= 0")
        cargo = json.loads(row["cargo"])
        cargo["metal"] += metal
        cargo["crystal"] += crystal
        conn.execute("UPDATE fleet_events SET cargo = ? WHERE id = ?", (json.dumps(cargo), row["id"]))
    info = _event_info(row, row["arrival_time"])
    info.update(resource_metal=metal, resource_crystal=crystal, resource_deuterium=0)
    mails.send(row["owner_id"], mails.REPORT, mails.SENDER_FLEET, _username(row["owner_id"]),
               f"Recycling report [{info['end_planet_pos']}]",
               {"mission_type": RECYCLE, "is_returning": False, "event_info": info,
                "total_garbage": {"garbage_metal": field["metal"], "garbage_crystal": field["crystal"],
                                  "garbage_deuterium": 0}}, row["arrival_time"])
    return "returning"


def max_planets(empire: dict) -> int:
    """Astrophysics allows one more colony every two levels, at most eight."""
    return 1 + eco.colonies(planet_rules.research_level(empire, eco.ASTROPHYSICS))


def _colonize(row) -> str:
    from . import player  # player imports this module

    ships = _ships(row)
    target = (row["target_galaxy"], row["target_system"], row["target_position"])
    owner = row["owner_id"]
    info = _event_info(row, row["arrival_time"])
    with db.player_state(owner) as state:
        empire = state["empire"]
        failure = None
        if len(empire["planets"]) >= max_planets(empire):
            failure = "Research more Astrophysics to rule more planets."
        else:
            planet_id = universe.add_planet(owner, *target, player.COLONY_NAME)
            if planet_id is None:
                failure = "The position is already taken."
        if failure:
            mails.send(owner, mails.SYSTEM, mails.SENDER_FLEET, state["username"],
                       f"Colonization failed [{info['end_planet_pos']}]", failure, row["arrival_time"])
            return "returning"
        planet_rules.tick(empire, row["arrival_time"])
        colony = player.new_planet(planet_id, *target, player.COLONY_NAME, resources=json.loads(row["cargo"]))
        low, high = eco.COLONY_FIELDS[target[2]]  # a colony's size and climate are its own
        colony.update(max_fields=random.randint(low, high),
                      temp_offset=random.randint(-eco.TEMPERATURE_SPREAD, eco.TEMPERATURE_SPREAD))
        colony["last_update"] = row["arrival_time"]
        ships[COLONY_SHIP] -= 1  # the colony ship becomes the colony
        for ftype, n in ships.items():
            planet_rules.fleet(colony, ftype)["amount"] += n
        empire["planets"].append(colony)
    info["end_planet_name"] = player.COLONY_NAME
    mails.send(owner, mails.REPORT, mails.SENDER_FLEET, _username(owner),
               f"New colony [{info['end_planet_pos']}]",
               {"mission_type": COLONY, "is_returning": False, "event_info": info}, row["arrival_time"])
    return "done"


def _arrive(row) -> str:
    """Apply the mission at the target. Returns the new status of the flight."""
    if row["mission"] == RECYCLE:
        return _recycle(row)
    if row["mission"] == COLONY:
        return _colonize(row)
    if row["mission"] == EXPEDITION:
        return _expedition(row)
    if row["target_position"] in universe.PIRATE_POSITIONS:
        return _attack_pirates(row) if row["mission"] == ATTACK else _spy_pirates(row)
    if row["mission"] == ACS_JOIN:
        return "returning"  # its attack already happened or was called off
    cargo = json.loads(row["cargo"])
    target_owner = _planet_owner(row["target_planet_id"])
    if target_owner is None:
        return "returning"  # the planet is gone: fly back with everything
    with db.player_state(target_owner) as state:
        planet = planet_rules.find_planet(state["empire"], row["target_planet_id"])
        if planet is None:
            return "returning"
        planet_rules.tick(state["empire"], row["arrival_time"])
        if row["mission"] == ESPIONAGE:
            _espionage(row, target_owner, state, planet)
            return "returning"
        if row["mission"] == ATTACK:
            if specials.at_peace(planet, row["arrival_time"]):
                mails.send(row["owner_id"], mails.REPORT, mails.SENDER_FLEET, _username(row["owner_id"]),
                           "Attack called off", {"mission_type": ATTACK, "is_returning": False,
                                                 "event_info": _event_info(row, row["arrival_time"]),
                                                 "notify_message": "The planet signed a peace agreement; "
                                                                   "your fleet turned back."},
                           row["arrival_time"])
                return "returning"
            return _attack(row, target_owner, state, planet)
        for res in RESOURCES:
            planet[res] += cargo[res]
        if row["mission"] == DEPLOY:
            for ftype, n in _ships(row).items():
                planet_rules.fleet(planet, ftype)["amount"] += n

    info = _event_info(row, row["arrival_time"])
    owner_name, target_name = _username(row["owner_id"]), _username(target_owner)
    if row["mission"] == DEPLOY:
        mails.send(row["owner_id"], mails.REPORT, mails.SENDER_FLEET, owner_name,
                   f"Fleet deployed [{info['end_planet_pos']}]",
                   {"mission_type": DEPLOY, "is_returning": False, "event_info": info}, row["arrival_time"])
    else:
        mails.send(row["owner_id"], mails.REPORT, mails.SENDER_FLEET, owner_name,
                   f"Transport arrived [{info['end_planet_pos']}]",
                   {"mission_type": TRANSPORT, "is_returning": False, "event_info": info}, row["arrival_time"])
        if target_owner != row["owner_id"]:
            mails.send(target_owner, mails.REPORT, mails.SENDER_FLEET, target_name,
                       f"Delivery from {owner_name}",
                       {"mission_type": BE_TRANSPORT, "is_returning": False,
                        "event_info": dict(info, mission=BE_TRANSPORT)}, row["arrival_time"])
    conn = db.connect()
    with db.lock(), conn:
        conn.execute("UPDATE fleet_events SET cargo = ? WHERE id = ?",
                     (json.dumps({res: 0 for res in RESOURCES}), row["id"]))
    return "done" if row["mission"] == DEPLOY else "returning"


def _return(row) -> None:
    with db.player_state(row["owner_id"]) as state:
        planet = planet_rules.find_planet(state["empire"], row["origin_planet_id"]) or state["empire"]["planets"][0]
        planet_rules.tick(state["empire"], row["return_time"])
        for ftype, n in _ships(row).items():
            planet_rules.fleet(planet, ftype)["amount"] += n
        cargo = json.loads(row["cargo"])
        for res in RESOURCES:
            planet[res] += cargo[res]
    info = _event_info(row, row["return_time"])
    mails.send(row["owner_id"], mails.REPORT, mails.SENDER_FLEET, _username(row["owner_id"]),
               f"Fleet returned [{info['start_planet_pos']}]",
               {"mission_type": row["mission"], "is_returning": True, "event_info": info}, row["return_time"])


def process_due(now: float | None = None) -> int:
    """Handle every arrival and return that is due. Returns how many steps were done."""
    now = time.time() if now is None else now
    conn = db.connect()
    rows = conn.execute(
        "SELECT * FROM fleet_events WHERE (status = 'outbound' AND arrival_time <= ?) "
        "OR (status = 'returning' AND return_time <= ?) ORDER BY id", (now, now)).fetchall()
    for due in rows:
        row = conn.execute("SELECT * FROM fleet_events WHERE id = ?", (due["id"],)).fetchone()
        if row is None or row["status"] != due["status"]:
            continue  # handled together with another flight (coordinated attack)
        try:
            if row["status"] == "outbound":
                status = _arrive(row)
                with db.lock(), conn:
                    if status == "done":
                        conn.execute("DELETE FROM fleet_events WHERE id = ?", (row["id"],))
                    elif status == "returning":
                        conn.execute("UPDATE fleet_events SET status = 'returning', turn_time = arrival_time "
                                     "WHERE id = ?", (row["id"],))
            else:
                _return(conn.execute("SELECT * FROM fleet_events WHERE id = ?", (row["id"],)).fetchone())
                with db.lock(), conn:
                    conn.execute("DELETE FROM fleet_events WHERE id = ?", (row["id"],))
        except Exception:
            log.exception("fleet event %s failed", due["id"])
    return len(rows)


# --- What the client sees -------------------------------------------------------------------


def _planet_label(planet_id: int | None, galaxy: int, system: int, position: int) -> tuple[str, str]:
    name = ""
    if planet_id:
        row = db.connect().execute("SELECT name FROM planets WHERE id = ?", (planet_id,)).fetchone()
        name = row["name"] if row else ""
    return name, f"{galaxy}:{system}:{position}"


RADAR_DETECTION, RADAR_TOP_LEVEL = planet_rules.RADAR_DETECTION, planet_rules.RADAR_TOP_LEVEL


def radar_detects(planet: dict, ships: dict[int, int]) -> bool:
    """A switched-on radar center sees an attack when it can detect one of its ship types."""
    lv = planet_rules.level(planet, eco.RADAR_CENTER)
    if not lv or not planet.get("radar_on", True):
        return False
    return any(lv >= RADAR_DETECTION.get(t, RADAR_TOP_LEVEL) for t in ships)


def incoming_attacks(player_id: int, empire: dict | None = None) -> list:
    """Attacks on the player's planets that their radar centers see."""
    rows = db.connect().execute(
        "SELECT f.* FROM fleet_events f JOIN planets p ON p.id = f.target_planet_id "
        "WHERE p.player_id = ? AND f.owner_id != ? AND f.mission = ? AND f.status = 'outbound' ORDER BY f.id",
        (player_id, player_id, ATTACK)).fetchall()
    if not rows:
        return []
    empire = empire or db.load_state(player_id)["empire"]
    return [r for r in rows if (p := planet_rules.find_planet(empire, r["target_planet_id"])) is not None
            and radar_detects(p, _ships(r))]


def accelerate_cost(row, now: float, state: dict | None = None) -> tuple[int, int]:
    """(light speed engines, credits) it takes to bring a returning fleet home at once: one
    engine from the capsule, else 1 credit per 5 minutes left (Supernova's numbers). The client
    shows both numbers as the price, so the one that is not used is 0."""
    from . import items
    if state is not None and items.amount(state, items.LIGHT_SPEED_ENGINE) >= 1:
        return 1, 0
    return 0, max(1, math.ceil((row["return_time"] - now) / 300))


def returning_flight(player_id: int, event_id: int):
    row = db.connect().execute("SELECT * FROM fleet_events WHERE id = ? AND owner_id = ?",
                               (event_id, player_id)).fetchone()
    if row is None or row["status"] != "returning":
        raise FleetError("Only returning fleets can be accelerated")
    return row


def accelerate(player_id: int, state: dict, event_id: int, now: float) -> None:
    """Land a returning fleet now, paying a light speed engine or credits."""
    from . import items
    row = returning_flight(player_id, event_id)
    engines, credits = accelerate_cost(row, now, state)
    if engines:
        items._add(state, items.LIGHT_SPEED_ENGINE, -engines)
    else:
        items._pay(state, credits, items.CREDITS)
    conn = db.connect()
    with db.lock(), conn:
        conn.execute("UPDATE fleet_events SET return_time = ? WHERE id = ?", (int(now), event_id))
    landed = conn.execute("SELECT * FROM fleet_events WHERE id = ?", (event_id,)).fetchone()
    planet = planet_rules.find_planet(state["empire"], landed["origin_planet_id"]) or state["empire"]["planets"][0]
    for ftype, n in _ships(landed).items():
        planet_rules.fleet(planet, ftype)["amount"] += n
    cargo = json.loads(landed["cargo"])
    for res in RESOURCES:
        planet[res] += cargo[res]
    with db.lock(), conn:
        conn.execute("DELETE FROM fleet_events WHERE id = ?", (event_id,))


def events_view(player_id: int, include_incoming: bool = True) -> list[dict]:
    """The player's flights as `empire.fleet_hanging_events`, plus attacks on the way to them
    (mission 11, shown as "under attack")."""
    rows = db.connect().execute("SELECT * FROM fleet_events WHERE owner_id = ? ORDER BY id", (player_id,)).fetchall()
    incoming = {r["id"] for r in incoming_attacks(player_id)} if include_incoming else set()
    if incoming:
        rows = list(rows) + list(incoming_attacks(player_id))
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
            "mission": UNDER_ATTACK if row["id"] in incoming else row["mission"],
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
            "acs_id": row["acs_id"] or "",
            "show_hanging": True,
            "fleets": [{"fleet_type": t, "fleet_count": n} for t, n in _ships(row).items()],
        })
    return events
