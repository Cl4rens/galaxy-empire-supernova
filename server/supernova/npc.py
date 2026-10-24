"""Pirate bases (positions 17-21 of every system) and expeditions (position 16).

The original server's rules are unknown; these are Supernova's own:

- A pirate base of level L defends with a fleet that grows with L and holds loot. Beaten bases
  rebuild after a while (PIRATE_RESPAWN_SECONDS / game speed).
- An expedition flies to position 16 and has a random outcome: resources, ships, dark matter,
  a pirate ambush, nothing, or (rarely) a black hole that swallows the fleet. Each player can
  start a limited number of expeditions per day.
"""

import random
import time

from . import combat, config, db
from . import economy as eco

PIRATE_RESPAWN_SECONDS = 4 * 3600
EXPEDITIONS_PER_DAY = 10

# fleet_type -> count per pirate level
PIRATE_FLEET = {1: 8, 2: 3, 3: 1, 15: 10, 16: 4}
PIRATE_LOOT_PER_LEVEL = {"metal": 6000, "crystal": 4000, "deuterium": 1500}


def pirate_level(position: int) -> int:
    return position - 16


def pirate_name(level: int) -> str:
    return f"Pirates LV {level}"


def pirate_fleet(level: int) -> dict[int, int]:
    return {t: n * level * level for t, n in PIRATE_FLEET.items()}


def pirate_loot(level: int) -> dict[str, int]:
    return {r: v * level * level for r, v in PIRATE_LOOT_PER_LEVEL.items()}


def respawn_seconds() -> int:
    return max(60, int(PIRATE_RESPAWN_SECONDS / config.SPEED))


def pirate_base(galaxy: int, system: int, position: int, now: float | None = None) -> dict:
    """The state of a pirate base: its fleet and loot, or nothing while it is rebuilding."""
    now = time.time() if now is None else now
    row = db.connect().execute("SELECT beaten_until FROM pirate_bases WHERE galaxy = ? AND system = ? AND position = ?",
                               (galaxy, system, position)).fetchone()
    level = pirate_level(position)
    if row and row["beaten_until"] > now:
        return {"level": level, "fleet": {}, "loot": {"metal": 0, "crystal": 0, "deuterium": 0},
                "next_refresh_time": int(row["beaten_until"] - now)}
    return {"level": level, "fleet": pirate_fleet(level), "loot": pirate_loot(level), "next_refresh_time": 0}


def beat_pirates(galaxy: int, system: int, position: int, now: float) -> None:
    conn = db.connect()
    with db.lock(), conn:
        conn.execute("INSERT INTO pirate_bases (galaxy, system, position, beaten_until) VALUES (?, ?, ?, ?) "
                     "ON CONFLICT (galaxy, system, position) DO UPDATE SET beaten_until = excluded.beaten_until",
                     (galaxy, system, position, now + respawn_seconds()))


# --- Expeditions ---------------------------------------------------------------------------

RESOURCES, FLEETS, DARK_MATTER, PIRATES, NOTHING, BLACK_HOLE = (
    "get_resources", "get_fleets", "get_dark_matter", "encounter_pirates", "return", "encounter_black_hole")
OUTCOMES = [(RESOURCES, 35), (FLEETS, 15), (DARK_MATTER, 8), (PIRATES, 12), (NOTHING, 29), (BLACK_HOLE, 1)]
MESSAGES = {
    RESOURCES: "Your expedition found an abandoned freighter full of resources.",
    FLEETS: "Your expedition found a drifting fleet whose crew joined you.",
    DARK_MATTER: "Your expedition collected a small amount of dark matter.",
    PIRATES: "Your expedition was ambushed by space pirates.",
    NOTHING: "Your expedition found nothing but the silence of space.",
    BLACK_HOLE: "Your expedition came too close to a black hole. Nothing came back.",
}


def expeditions_today(player_id: int, now: float) -> int:
    day_start = now - now % 86400
    return db.connect().execute(
        "SELECT COUNT(*) FROM expeditions WHERE player_id = ? AND started_at >= ?", (player_id, day_start)).fetchone()[0]


def record_expedition(player_id: int, now: float) -> None:
    conn = db.connect()
    with db.lock(), conn:
        conn.execute("INSERT INTO expeditions (player_id, started_at) VALUES (?, ?)", (player_id, int(now)))


def fleet_points(ships: dict[int, int]) -> int:
    """Structural value of a fleet: sum of metal + crystal of its ships."""
    return sum((eco.UNITS[t].metal + eco.UNITS[t].crystal) * n for t, n in ships.items() if t in eco.UNITS)


def explore(ships: dict[int, int], rng: random.Random | None = None) -> dict:
    """Roll an expedition outcome for a fleet."""
    rng = rng or random.Random()
    situation = rng.choices([o for o, _ in OUTCOMES], weights=[w for _, w in OUTCOMES])[0]
    if situation == FLEETS and not any(t in eco.UNITS for t in ships):
        situation = NOTHING
    points = max(fleet_points(ships), 10_000)
    result = {"situation": situation, "message": MESSAGES[situation]}
    if situation == RESOURCES:
        found = int(points * rng.uniform(0.1, 0.5))
        result["found"] = {"metal": found // 2, "crystal": found // 3, "deuterium": found // 6}
    elif situation == FLEETS:
        candidates = [t for t in (1, 2, 3, 9, 10, 12, 13) if eco.UNITS[t].metal + eco.UNITS[t].crystal <= points]
        ftype = rng.choice(candidates or [9])
        cost = eco.UNITS[ftype].metal + eco.UNITS[ftype].crystal
        result["fleets"] = {ftype: max(1, int(points * rng.uniform(0.05, 0.2)) // cost)}
    elif situation == DARK_MATTER:
        result["dark_matter"] = rng.randint(50, 300)
    elif situation == PIRATES:
        level = rng.randint(1, 3)
        result["pirates"] = {t: max(1, n * level) for t, n in PIRATE_FLEET.items() if t in eco.SHIPS}
    return result


def pirate_side(units: dict[int, int], level: int) -> combat.Side:
    return combat.Side(pirate_name(level), units, weapons=level, shielding=level, armour=level)
