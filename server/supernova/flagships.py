"""The Pioneer Lab and the Iron Behemoth (flagship), following the client's help texts:

- The Pioneer Lab stands on a moon; an empire can have only one.
- Behemoths are built there from 250 alloy fragments and dark matter each; the more an
  empire has, the more the next one costs and the longer it takes.
- Alloy fragments are salvaged from battles (from the debris, with a bonus per level of
  Alloy Detection Tech) or bought in boxes (dark matter; the price rises with every box and
  resets every day).
- Dark energy is gathered only in battles fought with Behemoths; it pays for the two pioneer
  techs. Dynamic Power Tech raises how many Behemoths a fleet may take along.
- Every Behemoth in a fleet raises the fleet's attack, up to the allowed number.

All numbers are Supernova's own.
"""

import time

from . import config
from . import economy as eco
from . import planet as planet_rules

BEHEMOTH = 24
ALLOY_PER_BEHEMOTH = 250
ALLOY_DETECTION, DYNAMIC_POWER = 20, 21  # research types used by the client
RESEARCH_NAMES = {ALLOY_DETECTION: "Alloy Detection Tech", DYNAMIC_POWER: "Dynamic Power Tech"}
RESEARCH_IDS = {ALLOY_DETECTION: 9020, DYNAMIC_POWER: 9021}
ATTACK_PER_BEHEMOTH = 5  # percent fleet attack per Behemoth
BOX_ALLOY = 250


class FlagshipError(planet_rules.GameError):
    pass


def data(state: dict) -> dict:
    return state.setdefault("flagships", {"alloy": 0, "dark_energy": 0, "built": 0, "box_day": 0, "boxes": 0,
                                          "research": {}, "research_event": None})


def research_level(state: dict, rtype: int, now: float | None = None) -> int:
    d = data(state)
    event = d["research_event"]
    if event and event["finish_time"] <= (time.time() if now is None else now):
        d["research"][str(event["research_type"])] = d["research"].get(str(event["research_type"]), 0) + 1
        d["research_event"] = None
    return d["research"].get(str(rtype), 0)


def behemoth_limit(state: dict) -> int:
    """How many Behemoths one fleet may take along."""
    return 1 + research_level(state, DYNAMIC_POWER)


def attack_bonus(behemoths: int, limit: int) -> int:
    return ATTACK_PER_BEHEMOTH * min(behemoths, limit)


def total_behemoths(empire: dict) -> int:
    return sum(planet_rules.fleet(p, BEHEMOTH)["amount"] for p in planet_rules.bodies(empire))


def lab_moon(empire: dict) -> dict | None:
    return next((p for p in planet_rules.bodies(empire) if planet_rules.level(p, eco.PIONEER_LAB)), None)


def build_cost(owned: int) -> tuple[int, int]:
    """(dark matter, seconds) for the next Behemoth."""
    return 50 * (owned + 1), max(60, int(4 * 3600 * (1 + owned / 2) / config.SPEED))


def _duration(seconds: int) -> str:
    """The construction screen shows this value as it is."""
    h, m = divmod(seconds // 60, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {seconds % 60:02d}s"


def box_price(boxes_today: int) -> int:
    return 20 + 10 * boxes_today


def _boxes_today(d: dict, now: float) -> int:
    day = int(now // 86400)
    if d["box_day"] != day:
        d["box_day"], d["boxes"] = day, 0
    return d["boxes"]


def salvage(state: dict, debris: dict[str, int]) -> int:
    """Alloy fragments from a battle: 1 per 20,000 debris, +2 % per Alloy Detection level."""
    found = (debris.get("metal", 0) + debris.get("crystal", 0)) // 20_000
    found = int(found * (1 + 0.02 * research_level(state, ALLOY_DETECTION)))
    data(state)["alloy"] += found
    return found


def gather_dark_energy(state: dict, enemy_losses_points: int) -> int:
    """Dark energy from a battle fought with Behemoths: 1 per 10 points the enemy lost."""
    gained = max(1, enemy_losses_points // 10)
    data(state)["dark_energy"] += gained
    return gained


# --- Views and actions ----------------------------------------------------------------------


def show_info(state: dict) -> dict:
    d = data(state)
    limit = behemoth_limit(state)
    return {"dark_energy_count": d["dark_energy"], "attack_buff": attack_bonus(limit, limit),
            "total_behemoth_num": total_behemoths(state["empire"]), "alloy_fragment_count": d["alloy"],
            "behemoth_limit": limit}


def building_info(state: dict, now: float) -> dict:
    d = data(state)
    owned = total_behemoths(state["empire"])
    dm, seconds = build_cost(owned)
    possible = d["alloy"] // ALLOY_PER_BEHEMOTH
    return {"enable_building_num": max(0, min(possible, 10)), "total_behemoth_num": owned,
            "alloy_fragment_box_price": box_price(_boxes_today(d, now)), "time_cost_next": _duration(seconds),
            "alloy_fragment_cost_next": ALLOY_PER_BEHEMOTH, "dark_matter_cost_next": dm}


def buy_box(state: dict, now: float) -> dict:
    d = data(state)
    empire = state["empire"]
    price = box_price(_boxes_today(d, now))
    if empire.get("dark_matter_count", 0) < price:
        raise FlagshipError(f"An alloy fragment box costs {price} dark matter")
    empire["dark_matter_count"] -= price
    d["alloy"] += BOX_ALLOY
    d["boxes"] += 1
    return {"credit_count": empire.get("gu_count", 0), "alloy_fragment_box_price": box_price(d["boxes"]),
            "alloy_fragment_count": d["alloy"], "enable_building_num": d["alloy"] // ALLOY_PER_BEHEMOTH,
            "message": f"You received {BOX_ALLOY} alloy fragments."}


def _cost_of(owned: int, count: int) -> tuple[int, int]:
    dm = sum(build_cost(owned + i)[0] for i in range(count))
    seconds = sum(build_cost(owned + i)[1] for i in range(count))
    return dm, seconds


def build(state: dict, planet: dict, count: int, confirm: bool, next_event_id, now: float) -> dict:
    d = data(state)
    empire = state["empire"]
    if not planet_rules.level(planet, eco.PIONEER_LAB):
        raise FlagshipError("Behemoths are built in the Pioneer Lab")
    if count < 1:
        raise FlagshipError("Nothing to build")
    if any(o["fleet_type"] == BEHEMOTH for o in planet["fleet_building_events"]):
        raise FlagshipError("A Behemoth is already being built")
    owned = total_behemoths(empire)
    dm, seconds = _cost_of(owned, count)
    alloy = ALLOY_PER_BEHEMOTH * count
    if not confirm:
        return {"message": f"Build {count} Iron Behemoth(s) for {alloy:,} alloy fragments and {dm} dark matter? "
                           f"It takes {seconds // 3600} h {seconds % 3600 // 60} min."}
    if d["alloy"] < alloy:
        raise FlagshipError(f"You need {alloy:,} alloy fragments")
    if empire.get("dark_matter_count", 0) < dm:
        raise FlagshipError(f"You need {dm} dark matter")
    d["alloy"] -= alloy
    empire["dark_matter_count"] -= dm
    fleet = planet_rules.fleet(planet, BEHEMOTH)
    order = {"event_id": next_event_id(), "fleet_id": fleet["fleet_id"], "fleet_type": BEHEMOTH,
             "total_count": count, "delivered": 0, "unit_seconds": max(1, seconds // count),
             "unit_cost": [0, 0, 0], "start_time": int(now), "finish_time": int(now) + seconds,
             "total_time": seconds}
    planet["fleet_building_events"].append(order)
    d["built"] += count
    return {"event_id": order["event_id"], "finished": False, "fleet_id": fleet["fleet_id"],
            "total_time": seconds, "finish_time": order["finish_time"]}


def research_cost(rtype: int, level: int) -> tuple[int, int, int]:
    """(dark energy, dark matter, seconds) for the next level."""
    return 100 * (level + 1) ** 2, 10 * (level + 1), max(60, int(3600 * (level + 1) / config.SPEED))


def research_view(state: dict, now: float) -> list[dict]:
    d = data(state)
    views = []
    for rtype, name in RESEARCH_NAMES.items():
        level = research_level(state, rtype, now)
        energy, dm, seconds = research_cost(rtype, level)
        event = d["research_event"]
        running = event is not None and event["research_type"] == rtype
        view = {"research_id": RESEARCH_IDS[rtype], "research_type": rtype, "research_name": name,
                "research_level": level, "finished": not running, "up_dark_energy_require": energy,
                "up_dark_matter_require": dm, "up_time": seconds}
        if running:
            view.update(event)
        views.append(view)
    return views


def start_research(state: dict, planet: dict, research_id: int, next_event_id, now: float) -> dict:
    d = data(state)
    if lab_moon(state["empire"]) is None:
        raise FlagshipError("You need a Pioneer Lab")
    rtype = next((t for t, i in RESEARCH_IDS.items() if i == research_id), None)
    if rtype is None:
        raise FlagshipError("Unknown research")
    research_level(state, rtype, now)  # finishes a done research
    if d["research_event"]:
        raise FlagshipError("A pioneer tech is already being researched")
    level = d["research"].get(str(rtype), 0)
    energy, dm, seconds = research_cost(rtype, level)
    if d["dark_energy"] < energy:
        raise FlagshipError(f"You need {energy:,} dark energy. Behemoths gather it in battle.")
    if state["empire"].get("dark_matter_count", 0) < dm:
        raise FlagshipError(f"You need {dm} dark matter")
    d["dark_energy"] -= energy
    state["empire"]["dark_matter_count"] -= dm
    d["research_event"] = {"event_id": next_event_id(), "research_id": research_id, "research_type": rtype,
                           "research_name": RESEARCH_NAMES[rtype], "research_level": level,
                           "start_time": int(now), "finish_time": int(now) + seconds, "total_time": seconds,
                           "finished": False}
    return dict(d["research_event"], dark_energy_count=d["dark_energy"])
