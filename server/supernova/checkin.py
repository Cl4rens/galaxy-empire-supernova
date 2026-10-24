"""Daily check-in: every day a player logs in unlocks the next of 21 gifts.

Days 7, 14 and 21 give ships, the others resources. After all 21 gifts are claimed the
calendar starts again. Days are counted in UTC.
"""

import time

from . import rewards

DAYS = 21
BIG_DAYS = {7: {1: 10}, 14: {9: 10}, 21: {3: 5}}  # day -> ships (light fighters, small cargos, cruisers)
RESOURCE_CYCLE = ("metal", "crystal", "deuterium")
# Icons the client already has in data2; the part after the last "/" is the texture name.
ICONS = {"metal": "icons/item_res_04.png", "crystal": "icons/item_res_03.png", "deuterium": "icons/item_res_01.png"}


def gift(day: int) -> tuple[dict[str, int], dict[int, int]]:
    if day in BIG_DAYS:
        return {}, BIG_DAYS[day]
    res = RESOURCE_CYCLE[(day - 1) % 3]
    return {res: (3000 if res != "deuterium" else 1500) * (1 + day // 3)}, {}


def _today(now: float) -> str:
    return time.strftime("%Y-%m-%d", time.gmtime(now))


def _progress(state: dict) -> dict:
    return state.setdefault("checkin", {"days": 0, "last_day": "", "claimed": []})


def visit(state: dict, now: float) -> None:
    """Count today as a check-in day (called when the player logs in)."""
    progress = _progress(state)
    today = _today(now)
    if progress["last_day"] == today:
        return
    if progress["days"] >= DAYS and len(progress["claimed"]) >= DAYS:
        progress.update(days=0, claimed=[])
    progress["last_day"] = today
    progress["days"] = min(DAYS, progress["days"] + 1)


def unclaimed(state: dict) -> int:
    progress = _progress(state)
    return progress["days"] - len(progress["claimed"])


def _config() -> dict:
    config = {}
    for day in range(1, DAYS + 1):
        resources, ships = gift(day)
        if ships:
            (ftype, n), = ships.items()
            item = {"type": "fleet", "fleet_type": ftype, "amount": n, "img": ICONS["metal"]}
        else:
            (res, n), = resources.items()
            item = {"type": "resource", "amount": n, "img": ICONS[res]}
        item["name"] = rewards.describe(resources, ships)
        config[f"n{day}"] = {"d": day, "g": item}
    return config


def info(state: dict) -> dict:
    progress = _progress(state)
    return {"config": _config(), "total_days": progress["days"],
            "status": {f"n{day}": True for day in progress["claimed"]}}


def claim(state: dict, gift_name: str) -> dict:
    progress = _progress(state)
    try:
        day = int(gift_name.removeprefix("n"))
    except ValueError:
        day = 0
    if not 1 <= day <= progress["days"]:
        result = {"ok": False, "info": "This gift is not unlocked yet."}
    elif day in progress["claimed"]:
        result = {"ok": False, "info": "You already have this gift."}
    else:
        resources, ships = gift(day)
        rewards.grant(state["empire"]["main_planet"], resources, ships)
        progress["claimed"].append(day)
        result = {"ok": True, "info": rewards.describe(resources, ships)}
    return dict(info(state), result=result)
