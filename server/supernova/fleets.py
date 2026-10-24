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
import time

from . import combat, db, ranking
from . import economy as eco
from . import mails, npc, quests
from . import planet as planet_rules
from . import universe

log = logging.getLogger("supernova")

EXPEDITION, COLONY, RECYCLE, TRANSPORT, DEPLOY, ESPIONAGE, ATTACK = 1, 2, 3, 4, 5, 6, 7
MISSION_NAMES = {
    EXPEDITION: "Expedition", COLONY: "Colonize", RECYCLE: "Recycle", TRANSPORT: "Transport",
    DEPLOY: "Deploy", ESPIONAGE: "Espionage", ATTACK: "Attack",
}
UNDER_ATTACK = 11  # how incoming attacks appear in the defender's flight list
ATTACK_PIRATES = 13  # report type of battles against pirate bases
QUEST_ACTIONS = {ESPIONAGE: quests.ESPIONAGE, EXPEDITION: quests.EXPEDITION, TRANSPORT: quests.TRANSPORT,
                 DEPLOY: quests.DEPLOY, RECYCLE: quests.RECYCLE}
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


def debris_at(target: tuple[int, int, int]) -> dict[str, int]:
    row = db.connect().execute("SELECT metal, crystal FROM debris WHERE galaxy = ? AND system = ? AND position = ?",
                               target).fetchone()
    return {"metal": row["metal"], "crystal": row["crystal"]} if row else {"metal": 0, "crystal": 0}


def allowed_missions(player_id: int, origin: dict, target: tuple[int, int, int], ships: dict[int, int]) -> list[int]:
    recycle = [RECYCLE] if RECYCLER in ships and any(debris_at(target).values()) else []
    row = target_planet(target)
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
    else:
        if ESPIONAGE_PROBE in ships:
            missions.append(ESPIONAGE)
        if any(combat.STATS[t].weapon > 0 for t in ships):
            missions.append(ATTACK)
    return missions


def plan(empire: dict, player_id: int, ships: dict[int, int], target: tuple[int, int, int],
         percent: int = 100, origin: dict | None = None) -> dict:
    origin = origin or empire["main_planet"]
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
    start = params.get("start_p_id") or params.get("planet_id")
    origin = planet_rules.find_planet(empire, int(start)) if start else empire["main_planet"]
    if origin is None:
        raise FleetError("This is not your planet")
    planet_rules.tick(empire, now)
    ships = parse_ships(origin, params.get("fleet_ids", ""), params.get("fleet_cnt_ids", ""))
    target = parse_target(params.get("end_p_pos", ""))
    mission = int(params.get("mission", 0))
    percent = int(params.get("speed_ratio", 100))
    if not 10 <= percent <= 100 or percent % 10:
        raise FleetError("Invalid speed")
    info = plan(empire, player_id, ships, target, percent, origin)
    if mission not in info["missions"]:
        raise FleetError("This mission is not possible here")
    if mission == COLONY and len(empire["planets"]) >= max_planets(empire):
        raise FleetError("Research more Astrophysics to rule more planets")
    if mission == EXPEDITION:
        if npc.expeditions_today(player_id, now) >= npc.EXPEDITIONS_PER_DAY:
            raise FleetError("No more expeditions possible today")
        npc.record_expedition(player_id, now)

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
    target_row = target_planet(target)
    target_id = target_row["id"] if target_row else None  # empty space, e.g. a debris field
    quest_action = QUEST_ACTIONS.get(mission)
    if mission == ATTACK:
        quest_action = quests.ATTACK_PLAYER if target_row else quests.ATTACK_PIRATES
    if quest_action:
        quests.record_action(state, quest_action)
    conn = db.connect()
    with db.lock(), conn:
        conn.execute(
            "INSERT INTO fleet_events (owner_id, origin_planet_id, target_planet_id, target_galaxy, target_system, "
            "target_position, mission, ships, cargo, start_time, arrival_time, return_time, status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'outbound')",
            (player_id, origin["planet_id"], target_id, *target, mission,
             json.dumps({str(t): n for t, n in ships.items()}), json.dumps(cargo),
             int(now), int(now) + flight, int(now) + 2 * flight))


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
                            planet_rules.research_level(owner_state["empire"], eco.ESPIONAGE_TECH),
                            planet_rules.research_level(state["empire"], eco.ESPIONAGE_TECH))
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


def _side(name: str, empire: dict, units: dict[int, int]) -> combat.Side:
    return combat.Side(name, units, planet_rules.research_level(empire, eco.WEAPONS_TECH),
                       planet_rules.research_level(empire, eco.SHIELDING_TECH),
                       planet_rules.research_level(empire, eco.ARMOUR_TECH))


def _battle(row, attacker: combat.Side, attacker_bonus: dict, defender: combat.Side, defender_bonus: dict) -> dict:
    """Fight and build the battle report's attack_result. The caller applies losses and loot."""
    result = combat.fight(attacker, defender)
    result["attack_lost"] = combat.losses(attacker.units, result["attacker_left"])
    result["defend_lost"] = combat.losses(defender.units, result["defender_left"])
    result["rebuilt"] = combat.rebuild_defense(result["defend_lost"])
    lost = {t: result["attack_lost"].get(t, 0) + result["defend_lost"].get(t, 0)
            for t in {*result["attack_lost"], *result["defend_lost"]}}
    result["debris"] = combat.debris(lost)  # only ships leave debris
    rounds = list(result["rounds"])
    if result["rebuilt"]:
        rounds.append({"attack_recovered_fleets": [], "defend_recovered_fleets": combat._fleet_list(result["rebuilt"]),
                       "attack_survived_fleets": [], "defend_survived_fleets": []})
    no_krypton = {"attack_bonus_percent": 0, "shield_bonus_percent": 0, "armour_bonus_percent": 0}
    result["report"] = {
        "attacker_name": attacker.name,
        "defender_name": defender.name,
        "win": {"attacker": "win", "defender": "lose", "draw": "draw"}[result["winner"]],
        "winner_name": {"attacker": attacker.name, "defender": defender.name}.get(result["winner"]),
        "attack_start_fleets_count": attacker.count(),
        "attack_end_fleets_count": sum(result["attacker_left"].values()),
        "defend_start_fleets_count": defender.count(),
        "defend_end_fleets_count": sum(result["defender_left"].values()) + sum(result["rebuilt"].values()),
        "attack_bonus_info": {"tech_bonus": attacker_bonus},
        "defend_bonus_info": {"tech_bonus": defender_bonus},
        "attack_bonus": attacker_bonus,
        "defend_bonus": defender_bonus,
        "attack_krypton_bonus": no_krypton,
        "defend_krypton_bonus": no_krypton,
        "round": rounds,
        "debirs": result["debris"],
    }
    return result


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


def _attack(row, target_owner: int, state: dict, planet: dict) -> str:
    """Fight at a player's planet. Returns the new status of the flight."""
    owner_state = db.load_state(row["owner_id"])
    attacker_name, defender_name = _username(row["owner_id"]), _username(target_owner)
    defend_units = {f["fleet_type"]: f["amount"] for f in planet["fleets"]
                    if f["amount"] and f["fleet_type"] in combat.STATS}
    result = _battle(row, _side(attacker_name, owner_state["empire"], _ships(row)), _tech_bonus(owner_state["empire"]),
                     _side(defender_name, state["empire"], defend_units), _tech_bonus(state["empire"]))
    for f in planet["fleets"]:
        if f["fleet_type"] in defend_units:
            f["amount"] = result["defender_left"].get(f["fleet_type"], 0) + result["rebuilt"].get(f["fleet_type"], 0)
    loot = {res: 0 for res in RESOURCES}
    if result["winner"] == "attacker":
        loot = combat.plunder(planet, eco.cargo_capacity(result["attacker_left"]))
        for res in RESOURCES:
            planet[res] -= loot[res]
    add_debris(row["target_galaxy"], row["target_system"], row["target_position"],
               result["debris"]["metal"], result["debris"]["crystal"])

    win = result["report"]["win"]
    ranking.record_battle(row["owner_id"], win)
    ranking.record_battle(target_owner, {"win": "lose", "lose": "win"}.get(win, "draw"))
    info = _event_info(row, row["arrival_time"])
    body = {"mission_type": ATTACK, "is_returning": False, "event_info": info,
            "notify_message": _loot_text(loot) if result["winner"] == "attacker" else "",
            "attack_result": dict(result["report"], loot=loot)}
    subject = f"Battle report [{info['end_planet_pos']}]"
    mails.send(row["owner_id"], mails.REPORT, mails.SENDER_FLEET, attacker_name, subject, body, row["arrival_time"])
    mails.send(target_owner, mails.REPORT, mails.SENDER_FLEET, defender_name, subject, body, row["arrival_time"])
    return _fly_home(row, result["attacker_left"], loot)


def _attack_pirates(row) -> str:
    target = (row["target_galaxy"], row["target_system"], row["target_position"])
    base = npc.pirate_base(*target, now=row["arrival_time"])
    owner_state = db.load_state(row["owner_id"])
    attacker_name = _username(row["owner_id"])
    level = base["level"]
    pirates = npc.pirate_side(base["fleet"], level)
    pirate_bonus = {"attack_bonus_percent": 10 * level, "shield_bonus_percent": 10 * level,
                    "armour_bonus_percent": 10 * level}
    result = _battle(row, _side(attacker_name, owner_state["empire"], _ships(row)), _tech_bonus(owner_state["empire"]),
                     pirates, pirate_bonus)
    loot = {res: 0 for res in RESOURCES}
    if result["winner"] == "attacker":
        space = eco.cargo_capacity(result["attacker_left"])
        loot = combat.plunder({r: 2 * v for r, v in base["loot"].items()}, space)  # all of it, if it fits
        if base["fleet"]:
            npc.beat_pirates(*target, row["arrival_time"])
    add_debris(*target, result["debris"]["metal"], result["debris"]["crystal"])
    info = _event_info(row, row["arrival_time"])
    info["end_planet_name"] = npc.pirate_name(level)
    body = {"mission_type": ATTACK_PIRATES, "is_returning": False, "event_info": info,
            "notify_message": _loot_text(loot) if result["winner"] == "attacker" else "",
            "attack_result": dict(result["report"], loot=loot)}
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
            state["empire"]["dark_matter_count"] += outcome["dark_matter"]
        expedition_result["dark_matter"] = outcome["dark_matter"]
    elif situation == npc.PIRATES:
        owner_state = db.load_state(owner)
        name = _username(owner)
        result = _battle(row, _side(name, owner_state["empire"], ships), _tech_bonus(owner_state["empire"]),
                         npc.pirate_side(outcome["pirates"], 1),
                         {"attack_bonus_percent": 0, "shield_bonus_percent": 0, "armour_bonus_percent": 0})
        ships = result["attacker_left"]
        expedition_result["attack_report"] = result["report"]
    elif situation == npc.BLACK_HOLE:
        ships = {}

    now = row["arrival_time"]
    info = _event_info(row, now)
    mails.send(owner, mails.EXPLORE, mails.SENDER_FLEET, _username(owner), "Expedition report",
               {"mission_type": EXPEDITION, "is_returning": False, "event_info": info,
                "notify_message": outcome["message"], "expedition_result": expedition_result,
                "explore_data": {"curr": npc.expeditions_today(owner, now), "max": npc.EXPEDITIONS_PER_DAY}},
               now)
    return _fly_home(row, ships, cargo)


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
    """OGame: Astrophysics allows one more colony every two levels."""
    return 1 + (planet_rules.research_level(empire, eco.ASTROPHYSICS) + 1) // 2


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


def incoming_attacks(player_id: int) -> list:
    return db.connect().execute(
        "SELECT f.* FROM fleet_events f JOIN planets p ON p.id = f.target_planet_id "
        "WHERE p.player_id = ? AND f.owner_id != ? AND f.mission = ? AND f.status = 'outbound' ORDER BY f.id",
        (player_id, player_id, ATTACK)).fetchall()


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
            "show_hanging": True,
            "fleets": [{"fleet_type": t, "fleet_count": n} for t, n in _ships(row).items()],
        })
    return events
