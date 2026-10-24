"""Ship modification ("Ship Equipment"), as the client's help text describes it:

Commanders install devices in their ships to raise armour, shields, attack and speed of a ship
type. The bonus stacks with technology and kryptons. High-level ships need advanced devices,
and every further level needs more of them. The screen unlocks at 100,000 points.

Devices are items (see items.py): bought with dark matter, kept in the capsule. Each ship
type has a level per device type (1 armour, 2 shield, 3 attack, 4 speed). Numbers are
Supernova's own.
"""

from . import ranking
from . import planet as planet_rules

ARMOUR, SHIELD, ATTACK, SPEED = 1, 2, 3, 4
NAMES = {ARMOUR: "Hull Plating", SHIELD: "Shield Emitter", ATTACK: "Weapon Module", SPEED: "Thruster"}
ICONS = {ARMOUR: "item_res_16.png", SHIELD: "item_res_11.png", ATTACK: "item_res_07.png", SPEED: "item_res_18.png"}
PERCENT = {ARMOUR: 3, SHIELD: 3, ATTACK: 3, SPEED: 2}  # bonus per level
PRICE = {1: 10, 2: 30}  # dark matter per device of grade 1 / 2
MAX_LEVEL = 10
UNLOCK_SCORE = 100_000  # the client opens the screen from here on
# ship types the client offers, and the device grade they take; 25-29 do not exist here
SHIP_GRADES = {5: 1, 6: 1, 7: 1, 20: 2, 23: 2, 24: 2}
DEFENSE = {20}  # defense does not move, so it gets no thrusters


class DeviceError(planet_rules.GameError):
    pass


def item_id(dtype: int, grade: int) -> int:
    return 4000 + 10 * dtype + grade


def catalog() -> list["items.Item"]:
    from . import items  # items lists these devices in the shop
    grades = {1: "Basic", 2: "Advanced"}
    return [items.Item(item_id(t, g), items.OTHER, f"{grades[g]} {NAMES[t]}",
                       f"Device for ship modification: +{PERCENT[t]} % per level. "
                       + ("For battlecruisers, bombers and destroyers." if g == 1
                          else "For plasma cannons, interceptors and Iron Behemoths."),
                       ICONS[t], PRICE[g], device=(t, g))
            for t in NAMES for g in PRICE]


def levels(empire: dict, ftype: int) -> dict[int, int]:
    return {int(t): n for t, n in empire.get("fleet_devices", {}).get(str(ftype), {}).items()}


def bonus(empire: dict, ftype: int, dtype: int) -> int:
    """Percent bonus of `dtype` for ship type `ftype`."""
    return PERCENT[dtype] * levels(empire, ftype).get(dtype, 0)


def unit_bonuses(empire: dict) -> dict[int, tuple[int, int, int]]:
    """(attack, shield, armour) percent per ship type, for battles."""
    return {int(f): (bonus(empire, int(f), ATTACK), bonus(empire, int(f), SHIELD), bonus(empire, int(f), ARMOUR))
            for f in empire.get("fleet_devices", {})}


def required_score(level: int) -> int:
    return UNLOCK_SCORE * (level + 1)


def _grade(ftype: int) -> int:
    grade = SHIP_GRADES.get(ftype)
    if grade is None:
        raise DeviceError("This ship type cannot be modified")
    return grade


def _slot(state: dict, ftype: int, dtype: int) -> dict:
    level = levels(state["empire"], ftype).get(dtype, 0)
    grade = _grade(ftype)
    maxed = level >= MAX_LEVEL
    return {"item_id": item_id(dtype, grade), "type": dtype, "lv": level, "num": f"+{PERCENT[dtype] * level}%",
            "next": f"{PERCENT[dtype] * min(level + 1, MAX_LEVEL)}%", "devices": 0 if maxed else level + 1,
            "score": required_score(level)}


def view(state: dict, ftype: int) -> dict:
    from . import items  # items lists these devices in the shop
    _grade(ftype)
    device_items = [dict(items.owned_view(state, i), type=i.device[0], lv=i.device[1]) for i in catalog()]
    return {"items": device_items, "fleet_device": [_slot(state, ftype, t) for t in NAMES]}


def install(state: dict, ftype: int, device: int) -> dict:
    from . import items  # items lists these devices in the shop
    """Use devices of item `device` to raise ship type `ftype` one level."""
    grade = _grade(ftype)
    i = items.item(device)
    if i.device is None:
        raise DeviceError("This is not a device")
    dtype, device_grade = i.device
    if device_grade != grade:
        raise DeviceError(f"This ship type needs {'basic' if grade == 1 else 'advanced'} devices")
    if dtype == SPEED and ftype in DEFENSE:
        raise DeviceError("Defense does not move")
    empire = state["empire"]
    level = levels(empire, ftype).get(dtype, 0)
    if level >= MAX_LEVEL:
        raise DeviceError("This device is at the highest level")
    score = ranking.points(state)["score"]
    if score < required_score(level):
        raise DeviceError(f"The next level needs {required_score(level):,} points")
    need = level + 1
    if items.amount(state, device) < need:
        raise DeviceError(f"You need {need} x {i.name}")
    items._add(state, device, -need)
    empire.setdefault("fleet_devices", {}).setdefault(str(ftype), {})[str(dtype)] = level + 1
    return {"fleet_device": _slot(state, ftype, dtype), "item_number": items.amount(state, device),
            "info": f"{NAMES[dtype]} level {level + 1}: +{PERCENT[dtype] * (level + 1)} %."}
