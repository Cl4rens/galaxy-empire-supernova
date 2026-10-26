"""Ship repair, with the rules from the client's help text:

- Only the defender: when ship repair is switched on (the default), 50 % of the ships the
  defender loses at a planet are kept there as damaged ships.
- Repairing costs no resources, only time; a higher robotics factory makes it faster.
  Defense units cannot be repaired.
- Repaired ships wait in the dock until the player takes them over ("deploy").
- Damaged ships can be dismantled into metal and crystal (Supernova: 30 % of their cost).
"""

from . import economy as eco
from . import planet as planet_rules

REPAIR_SHARE = 0.5
DISMANTLE_SHARE = 0.3


def dock(planet: dict) -> dict:
    return planet.setdefault("repair", {"damaged": {}, "repairing": {}, "finish_time": 0, "switch": 1})


def _counts(d: dict) -> dict[int, int]:
    return {int(t): n for t, n in d.items() if n > 0}


def unit_seconds(planet: dict, ftype: int) -> int:
    """Half of what building the unit with the planet's robotics factory would take."""
    rule = eco.UNITS[ftype]
    robotics, nanite = planet_rules.robotics_levels(planet)
    return max(1, eco.build_seconds(rule.metal, rule.crystal, robotics, nanite) // 2)


def add_damaged(planet: dict, lost_ships: dict[int, int], empire: dict | None = None) -> dict[int, int]:
    """After a defense: keep part of the lost ships as damaged (more with VIP battle loss
    recovery). Returns what was kept."""
    from . import vip
    d = dock(planet)
    if not d["switch"]:
        return {}
    share = REPAIR_SHARE + (vip.benefit(empire, "battle_recovery") / 100 if empire else 0)
    kept = {t: int(n * share) for t, n in lost_ships.items() if t in eco.SHIPS and int(n * share)}
    for t, n in kept.items():
        d["damaged"][str(t)] = d["damaged"].get(str(t), 0) + n
    return kept


def _fleets_view(planet: dict) -> list[dict]:
    return [{"fleet_type": t, "fleet_count": n, "single_r_time": unit_seconds(planet, t)}
            for t, n in sorted(_counts(dock(planet)["damaged"]).items())]


def view(planet: dict, now: float) -> dict:
    d = dock(planet)
    done = bool(d["repairing"]) and d["finish_time"] <= now
    return {
        "base_info": {"finished_time": 0 if done else d["finish_time"], "handing_over": int(done),
                      "switch_repair": d["switch"], "all_switch": True, "switch_resolve": True},
        "fleets_info": _fleets_view(planet),
        "planet": {"current_server_time": int(now)},
    }


def _parse(fleet_types: str, counts: str) -> dict[int, int]:
    try:
        pairs = zip((int(x) for x in fleet_types.split(",") if x), (int(x) for x in counts.split(",") if x))
        return {t: n for t, n in pairs if n > 0}
    except ValueError:
        raise planet_rules.GameError("Invalid selection") from None


def _take_damaged(planet: dict, ships: dict[int, int]) -> None:
    d = dock(planet)
    for t, n in ships.items():
        if d["damaged"].get(str(t), 0) < n:
            raise planet_rules.GameError("Not that many damaged ships")
    for t, n in ships.items():
        d["damaged"][str(t)] -= n
    d["damaged"] = {k: v for k, v in d["damaged"].items() if v > 0}


def start(planet: dict, fleet_types: str, counts: str, now: float) -> int:
    d = dock(planet)
    if d["repairing"]:
        raise planet_rules.GameError("Ships are already being repaired here")
    ships = _parse(fleet_types, counts)
    if not ships:
        raise planet_rules.GameError("Select the ships to repair")
    _take_damaged(planet, ships)
    seconds = 1 + sum(unit_seconds(planet, t) * n for t, n in ships.items())
    d["repairing"] = {str(t): n for t, n in ships.items()}
    d["finish_time"] = int(now) + seconds
    return d["finish_time"]


def hand_over(planet: dict, now: float) -> dict[int, int]:
    d = dock(planet)
    if not d["repairing"] or d["finish_time"] > now:
        raise planet_rules.GameError("The repair is not finished yet")
    ships = _counts(d["repairing"])
    for t, n in ships.items():
        planet_rules.fleet(planet, t)["amount"] += n
    d["repairing"], d["finish_time"] = {}, 0
    return ships


def dismantle_value(ships: dict[int, int]) -> dict[str, int]:
    metal = sum(eco.UNITS[t].metal * n for t, n in ships.items())
    crystal = sum(eco.UNITS[t].crystal * n for t, n in ships.items())
    return {"metal": int(metal * DISMANTLE_SHARE), "crystal": int(crystal * DISMANTLE_SHARE)}


def dismantle(planet: dict, fleet_types: str, counts: str) -> dict[str, int]:
    ships = _parse(fleet_types, counts)
    _take_damaged(planet, ships)
    value = dismantle_value(ships)
    for res, n in value.items():
        planet[res] += n
    return value


def toggle(planet: dict, on: bool) -> int:
    dock(planet)["switch"] = int(on)
    return dock(planet)["switch"]
