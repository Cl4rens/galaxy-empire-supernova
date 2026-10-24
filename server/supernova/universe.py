"""The universe: where every planet is, and the galaxy map and solar system views.

Layout as in the client: 9 galaxies of 499 solar systems, 15 planet positions per system
(16 is the expedition point, 17-21 pirate planets, not implemented yet). On the client's galaxy
map every system is a tile of a 34 x 34 grid.
"""

import random

from . import db

GALAXIES = 9
SYSTEMS_PER_GALAXY = 499
PLANET_POSITIONS = 15
EXPEDITION_POSITION = 16
PIRATE_POSITIONS = range(17, 22)
MAP_WIDTH = 34
HOME_POSITIONS = range(4, 13)  # like OGame: home planets sit in the middle of a system
MAP_RADIUS = 6  # systems around the requested tile returned by galaxy_map.json


def system_id(galaxy: int, system: int) -> int:
    return (galaxy - 1) * SYSTEMS_PER_GALAXY + system


def system_from_id(sid: int) -> tuple[int, int]:
    return (sid - 1) // SYSTEMS_PER_GALAXY + 1, (sid - 1) % SYSTEMS_PER_GALAXY + 1


def map_coordinates(system: int) -> tuple[int, int]:
    return (system - 1) % MAP_WIDTH, (system - 1) // MAP_WIDTH


def system_at(x: int, y: int) -> int | None:
    system = y * MAP_WIDTH + x + 1
    return system if 0 <= x < MAP_WIDTH and 1 <= system <= SYSTEMS_PER_GALAXY else None


def valid_system(galaxy: int, system: int) -> bool:
    return 1 <= galaxy <= GALAXIES and 1 <= system <= SYSTEMS_PER_GALAXY


# --- Placing planets ----------------------------------------------------------------------


def place_home(player_id: int, name: str) -> dict:
    """Find a free home position and register the planet. Early players live close together:
    the range of systems grows with the number of planets."""
    conn = db.connect()
    with db.lock(), conn:
        count = conn.execute("SELECT COUNT(*) FROM planets").fetchone()[0]
        systems = min(SYSTEMS_PER_GALAXY, 10 + count)
        for _ in range(1000):
            system = random.randint(1, systems)
            position = random.choice(HOME_POSITIONS)
            taken = conn.execute("SELECT 1 FROM planets WHERE galaxy = 1 AND system = ? AND position = ?",
                                 (system, position)).fetchone()
            if not taken:
                break
        else:
            raise RuntimeError("No free home position found")
        cur = conn.execute("INSERT INTO planets (player_id, galaxy, system, position, name) VALUES (?, ?, ?, ?, ?)",
                           (player_id, 1, system, position, name))
    return {"planet_id": cur.lastrowid, "galaxy": 1, "system": system, "position": position}


def add_planet(player_id: int, galaxy: int, system: int, position: int, name: str) -> int | None:
    """Register a new planet. Returns its id, or None if the position is taken."""
    conn = db.connect()
    with db.lock(), conn:
        if conn.execute("SELECT 1 FROM planets WHERE galaxy = ? AND system = ? AND position = ?",
                        (galaxy, system, position)).fetchone():
            return None
        cur = conn.execute("INSERT INTO planets (player_id, galaxy, system, position, name) VALUES (?, ?, ?, ?, ?)",
                           (player_id, galaxy, system, position, name))
    return cur.lastrowid


def ensure_registered(player_id: int, planet: dict) -> None:
    """Register a planet created before the universe table existed."""
    conn = db.connect()
    with db.lock(), conn:
        if conn.execute("SELECT 1 FROM planets WHERE id = ?", (planet["planet_id"],)).fetchone():
            return
        conn.execute("INSERT INTO planets (id, player_id, galaxy, system, position, name) VALUES (?, ?, ?, ?, ?, ?)",
                     (planet["planet_id"], player_id, planet["galaxy_position"], planet["system_position"],
                      planet["planet_position"], planet["planet_name"]))


# --- Views --------------------------------------------------------------------------------


def _planets_in(galaxy: int, system: int) -> list:
    return db.connect().execute(
        "SELECT p.id, p.player_id, p.position, p.name, pl.username FROM planets p "
        "JOIN players pl ON pl.id = p.player_id WHERE p.galaxy = ? AND p.system = ?",
        (galaxy, system)).fetchall()


def _slot(galaxy: int, system: int, position: int, name: str = "", planet_id: int = 0,
          empire_id: int = 0, user_name: str = "") -> dict:
    return {
        "id": planet_id,
        "galaxy_position": galaxy,
        "system_position": system,
        "planet_position": position,
        "planet_name": name,
        "empire_id": empire_id,
        "user_name": user_name,
        "alliance_id": 0,
        "alliance_name": "",
        "has_debris": False,
        "is_protected": 0,
        "can_attack": 0,
        "moon_array": [],
        "kryptons": [],  # the client iterates this for every slot
        "planet_icon": 0,
        "pirate_level": 0,
        "next_refresh_time": 0,
    }


def _debris_in(galaxy: int, system: int) -> dict[int, tuple[int, int]]:
    rows = db.connect().execute("SELECT position, metal, crystal FROM debris WHERE galaxy = ? AND system = ?",
                                (galaxy, system)).fetchall()
    return {r["position"]: (r["metal"], r["crystal"]) for r in rows if r["metal"] or r["crystal"]}


def system_view(galaxy: int, system: int) -> dict:
    """One solar system: 15 planet positions, the expedition point and 5 pirate bases."""
    occupied = {row["position"]: row for row in _planets_in(galaxy, system)}
    planets = []
    for position in range(1, PLANET_POSITIONS + 1):
        row = occupied.get(position)
        if row:
            planets.append(_slot(galaxy, system, position, row["name"], row["id"], row["player_id"], row["username"]))
        else:
            planets.append(_slot(galaxy, system, position))
    debris = _debris_in(galaxy, system)
    for slot in planets:
        if slot["planet_position"] in debris:
            metal, crystal = debris[slot["planet_position"]]
            slot["has_debris"] = True
            slot["debris_desc"] = f"Debris: {metal:,} metal, {crystal:,} crystal"
    planets.append(_slot(galaxy, system, EXPEDITION_POSITION, "Expedition"))
    # Pirate bases (NPC planets), see npc.py.
    from . import npc
    for position in PIRATE_POSITIONS:
        base = npc.pirate_base(galaxy, system, position)
        pirate = _slot(galaxy, system, position, "Pirates")
        pirate.update(pirate_level=base["level"], next_refresh_time=base["next_refresh_time"])
        if position in debris:
            metal, crystal = debris[position]
            pirate.update(has_debris=True, debris_desc=f"Debris: {metal:,} metal, {crystal:,} crystal")
        planets.append(pirate)
    return {
        "system_id": system_id(galaxy, system),
        "galaxy_position": galaxy,
        "system_position": system,
        "planets": planets,
    }


def galaxy_map(galaxy: int, x: int, y: int) -> dict:
    """The systems around map tile (x, y), with their number of players."""
    counts = dict(db.connect().execute(
        "SELECT system, COUNT(DISTINCT player_id) FROM planets WHERE galaxy = ? GROUP BY system", (galaxy,)).fetchall())
    debris = dict(db.connect().execute(
        "SELECT system, COUNT(*) FROM debris WHERE galaxy = ? AND (metal > 0 OR crystal > 0) GROUP BY system",
        (galaxy,)).fetchall())
    systems = []
    for ty in range(max(0, y - MAP_RADIUS), y + MAP_RADIUS + 1):
        for tx in range(max(0, x - MAP_RADIUS), min(MAP_WIDTH, x + MAP_RADIUS + 1)):
            system = system_at(tx, ty)
            if system is None:
                continue
            systems.append({
                "system_id": system_id(galaxy, system),
                "system_coordinate_x": tx,
                "system_coordinate_y": ty,
                "galaxy_position": galaxy,
                "system_position": system,
                "user_count": counts.get(system, 0),
                "lord_info": {"lord_name": "", "lord_level": 0},
                "debris_planets": debris.get(system, 0),
                "strong_planets": 0,
                "weak_planets": 0,
                "active_planets": 0,
            })
    return {"systems": systems}
