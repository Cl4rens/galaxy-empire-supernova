"""Ship modification, following the Galaxy Empire wiki (galaxyempire.fandom.com/wiki/Modification
and /wiki/Ship_Armor_Plate_Lv1):

Commanders install devices in their ships to raise armour, shields, attack and speed of a ship
type. The bonus stacks with technology and kryptons. Lv1 devices (Ship Armor Plate, Energy
Device, Power Adaptor, Engine System) fit battlecruisers, bombers, destroyers and plasma
cannons; Lv2 devices the advanced ships and proton cannons. Level n of a device type takes
2^(n-1) devices and some experience (points): 100K, 150K, 200K, then +200K up to 1.4M at level 9,
1.8M at 10 and 2.2M at 11 (wiki), then +400K (Supernova). Levels 1-9 add 10 % each, level 10
adds 20 % and every level from 11 on 30 % (wiki; above 11 Supernova's reading). At most 20
levels. Devices come from expeditions and from battles against players (wiki), or from the shop.
The screen unlocks at 100,000 points.
"""

import random

from . import ranking
from . import planet as planet_rules

ARMOUR, SHIELD, ATTACK, SPEED = 1, 2, 3, 4
NAMES = {ARMOUR: "Ship Armor Plate", SHIELD: "Energy Device", ATTACK: "Power Adaptor", SPEED: "Engine System"}
ICONS = {ARMOUR: "item_res_16.png", SHIELD: "item_res_11.png", ATTACK: "item_res_07.png", SPEED: "item_res_18.png"}
PRICE = {1: 2, 2: 6}  # dark matter per device of grade 1 / 2
MAX_LEVEL = 20
UNLOCK_SCORE = 100_000  # the client opens the screen from here on
# ship types the client offers, and the device grade they take
SHIP_GRADES = {5: 1, 6: 1, 7: 1, 20: 1, 23: 2, 24: 2, 25: 2, 26: 2, 27: 2, 28: 2, 29: 2}
DEFENSE = {20, 27}  # defense does not move, so it gets no engine systems
EXPEDITION_DROP_CHANCE = 0.25  # an expedition finds 1-5 devices with this chance
BATTLE_DROPS = 3  # devices for winning a battle against a player


class DeviceError(planet_rules.GameError):
    pass


def item_id(dtype: int, grade: int) -> int:
    return 4000 + 10 * dtype + grade


def catalog() -> list["items.Item"]:
    from . import items  # items lists these devices in the shop
    return [items.Item(item_id(t, g), items.OTHER, f"{NAMES[t]} Lv{g}",
                       "Device for ship modification. "
                       + ("For battlecruisers, bombers, destroyers and plasma cannons." if g == 1
                          else "For the advanced ships and proton cannons."),
                       ICONS[t], PRICE[g], device=(t, g))
            for t in NAMES for g in PRICE]


def percent(level: int) -> int:
    """Bonus of a device level in percent."""
    return 10 * min(level, 9) + (20 if level >= 10 else 0) + 30 * max(0, level - 10)


def devices_needed(level: int) -> int:
    """Devices to go from `level` to `level + 1`."""
    return 2 ** level


def required_score(level: int) -> int:
    """Points needed to go from `level` to `level + 1`."""
    n = level + 1
    if n <= 3:
        return 100_000 + 50_000 * (n - 1)
    if n <= 9:
        return 200_000 * (n - 2)
    return 1_400_000 + 400_000 * (n - 9)


def levels(empire: dict, ftype: int) -> dict[int, int]:
    return {int(t): n for t, n in empire.get("fleet_devices", {}).get(str(ftype), {}).items()}


def bonus(empire: dict, ftype: int, dtype: int) -> int:
    """Percent bonus of `dtype` for ship type `ftype`."""
    return percent(levels(empire, ftype).get(dtype, 0))


def unit_bonuses(empire: dict) -> dict[int, tuple[int, int, int]]:
    """(attack, shield, armour) percent per ship type, for battles."""
    return {int(f): (bonus(empire, int(f), ATTACK), bonus(empire, int(f), SHIELD), bonus(empire, int(f), ARMOUR))
            for f in empire.get("fleet_devices", {})}


def _grade(ftype: int) -> int:
    grade = SHIP_GRADES.get(ftype)
    if grade is None:
        raise DeviceError("This ship type cannot be modified")
    return grade


def _slot(state: dict, ftype: int, dtype: int) -> dict:
    level = levels(state["empire"], ftype).get(dtype, 0)
    grade = _grade(ftype)
    maxed = level >= MAX_LEVEL
    return {"item_id": item_id(dtype, grade), "type": dtype, "lv": level, "num": f"+{percent(level)}%",
            "next": f"{percent(min(level + 1, MAX_LEVEL))}%", "devices": 0 if maxed else devices_needed(level),
            "score": required_score(level)}


def view(state: dict, ftype: int) -> dict:
    from . import items  # items lists these devices in the shop
    _grade(ftype)
    device_items = [dict(items.owned_view(state, i), type=i.device[0], lv=i.device[1]) for i in catalog()]
    return {"items": device_items, "fleet_device": [_slot(state, ftype, t) for t in NAMES]}


def install(state: dict, ftype: int, device: int) -> dict:
    """Use devices of item `device` to raise ship type `ftype` one level."""
    from . import items  # items lists these devices in the shop
    grade = _grade(ftype)
    i = items.item(device)
    if i.device is None:
        raise DeviceError("This is not a device")
    dtype, device_grade = i.device
    if device_grade != grade:
        raise DeviceError(f"This ship type needs Lv{grade} devices")
    if dtype == SPEED and ftype in DEFENSE:
        raise DeviceError("Defense does not move")
    empire = state["empire"]
    level = levels(empire, ftype).get(dtype, 0)
    if level >= MAX_LEVEL:
        raise DeviceError("This device is at the highest level")
    score = ranking.points(state)["score"]
    if score < required_score(level):
        raise DeviceError(f"The next level needs {required_score(level):,} points")
    need = devices_needed(level)
    if items.amount(state, device) < need:
        raise DeviceError(f"You need {need:,} x {i.name}")
    items._add(state, device, -need)
    empire.setdefault("fleet_devices", {}).setdefault(str(ftype), {})[str(dtype)] = level + 1
    return {"fleet_device": _slot(state, ftype, dtype), "item_number": items.amount(state, device),
            "info": f"{NAMES[dtype]} level {level + 1}: +{percent(level + 1)} %."}


def drop(state: dict, count: int, rng: random.Random | None = None) -> str:
    """Give `count` random devices (any type and grade); returns a note for the report."""
    from . import items
    rng = rng or random.Random()
    found: dict[int, int] = {}
    for _ in range(count):
        i = item_id(rng.choice(list(NAMES)), rng.choice((1, 1, 1, 2)))
        found[i] = found.get(i, 0) + 1
    for i, n in found.items():
        items.add(state, i, n)
    return "Devices: " + ", ".join(f"{items.item(i).name} x{n}" for i, n in found.items())
