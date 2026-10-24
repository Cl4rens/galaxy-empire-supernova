"""System lords ("Official Rank" in the client).

The player with the most points among the owners of a solar system's planets is its lord. The
lord's rank (1 Private ... 21 Grand Marshal) grows with their points: rank 1 from 1,000 points,
one more each time the points double. Planets of the lord in that system get +rank % weapons,
shields and armour and +2 × rank % mine output.

Lords are worked out from the rankings at most once a minute (see refresh()).
"""

import math
import time

from . import db, ranking

MIN_POINTS = 1000
MAX_RANK = 21
REFRESH_SECONDS = 60

_lords: dict[tuple[int, int], tuple[int, int, str]] = {}  # (galaxy, system) -> (player_id, rank, name)
_updated = 0.0


def rank_for(points: int) -> int:
    if points < MIN_POINTS:
        return 0
    return min(MAX_RANK, 1 + int(math.log2(points / MIN_POINTS)))


def effect(rank: int) -> dict:
    return {"increase_attack": rank, "increase_shield": rank, "increase_armour": rank,
            "increase_metal": 2 * rank, "increase_crystal": 2 * rank, "increase_deuterium": 2 * rank}


def refresh(force: bool = False) -> None:
    global _updated
    now = time.time()
    if not force and now - _updated < REFRESH_SECONDS:
        return
    _updated = now
    points = {p["id"]: (p["score"], p["username"]) for p in ranking._players()}
    lords = {}
    rows = db.connect().execute("SELECT DISTINCT galaxy, system, player_id FROM planets WHERE kind = 0").fetchall()
    for r in rows:
        score, name = points.get(r["player_id"], (0, ""))
        key = (r["galaxy"], r["system"])
        best = lords.get(key)
        if best is None or score > best[0] or (score == best[0] and r["player_id"] < best[1]):
            lords[key] = (score, r["player_id"], name)
    _lords.clear()
    for key, (score, pid, name) in lords.items():
        rank = rank_for(score)
        if rank:
            _lords[key] = (pid, rank, name)


def lord_of(galaxy: int, system: int) -> tuple[int, int, str] | None:
    refresh()
    return _lords.get((galaxy, system))


def bonus(planet: dict, owner_id: int) -> dict:
    """The lord bonus for one of `owner_id`'s planets (empty when they are not lord there)."""
    lord = lord_of(planet["galaxy_position"], planet["system_position"])
    if lord is None or lord[0] != owner_id or planet.get("planet_type") == 3:
        return {}
    return effect(lord[1])


def owned_systems(player_id: int) -> dict[str, dict]:
    refresh()
    return {f"{g}:{s}": {"level": rank, "effect": effect(rank)}
            for (g, s), (pid, rank, _) in sorted(_lords.items()) if pid == player_id}


def system_info(galaxy: int, system: int) -> dict | None:
    lord = lord_of(galaxy, system)
    return {"lord_level": lord[1], "lord_name": lord[2]} if lord else None
