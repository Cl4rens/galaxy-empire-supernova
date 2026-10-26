"""VIP: points raise the VIP level for good; benefits work while VIP is activated.

The original's levels and benefits (galaxyempire.fandom.com/wiki/VIP) had 8 levels; Supernova
has 6: the points of the original's levels 1 to 6, level 6 with the benefits of the original's
level 8, and every benefit growing evenly from where the original's table starts it. Two
benefits of the table are left out because Supernova has no such limits: more flight routes
and a higher shard limit from neutral forces. The original's "purchase reward" for dark matter
and credits (Supernova sells neither) becomes more dark matter from expeditions and the daily
income.

Points come from the daily login (more on days in a row) and from VIP points items bought with
dark matter; activation items buy days of benefits. The client asks for this through
users/<id>/vip_info, users/<id>/vip_effect and game_items/vip_item.
"""

import math
import time

MAX_LEVEL = 6
NEED_SCORE = [100, 500, 1500, 5000, 15000, 50000]  # points for levels 1-6

DAY = 86400
LOGIN_POINTS = 20  # the first login of a day ...
LOGIN_STREAK_POINTS = 10  # ... plus this much per day in a row before it ...
LOGIN_STREAK_MAX = 8  # ... for up to this many days


def _grow(first: int, start: float, end: float, ints: bool = True) -> list:
    """A benefit from level `first` (value `start`) to level 6 (value `end`), evenly."""
    out = []
    for lv in range(1, MAX_LEVEL + 1):
        if lv < first:
            out.append(0)
        else:
            value = start + (end - start) * (lv - first) / max(1, MAX_LEVEL - first)
            out.append(round(value) if ints else value)
    return out


# key -> (name the VIP screen shows, unit, value per level 1-6)
BENEFITS = {
    "production": ("Resource production", "%", _grow(1, 5, 40)),
    "build": ("Construction speed", "%", _grow(1, 3, 20)),
    "research": ("Research speed", "%", _grow(1, 3, 20)),
    "shipyard": ("Ship building speed", "%", _grow(1, 3, 20)),
    "dark_matter": ("Dark matter from expeditions and income", "%", _grow(1, 5, 20)),
    "secure_explorations": ("Secure explorations", "+", _grow(2, 5, 30)),
    "research_queue": ("Research queue", "+", [0, 1, 1, 1, 1, 2]),
    "build_queue": ("Construction queue", "+", [0, 0, 1, 1, 1, 2]),
    "flight_speed": ("Flight speed", "%", _grow(3, 5, 15)),
    "shield": ("Shielding", "%", _grow(3, 5, 20)),
    "armour": ("Strength", "%", _grow(3, 5, 20)),
    "attack": ("Weapons", "%", _grow(4, 5, 20)),
    "battle_recovery": ("Battle loss recovery", "%", _grow(4, 10, 15)),
    "shipyard_queue": ("Ship building queue", "+", [0, 0, 0, 0, 1, 1]),
    "flight_routes": ("Flight routes (expeditions excluded)", "+", [0, 0, 0, 1, 1, 1]),
}


def data(state: dict) -> dict:
    """The VIP record, kept in the empire (hidden from the client); `state` may be the player's
    state or the empire."""
    empire = state.get("empire", state)
    return empire.setdefault("vip", {"score": 0, "until": 0, "login_day": -1, "streak": 0})


def level(score: int) -> int:
    return sum(1 for need in NEED_SCORE if score >= need)


def active_level(state: dict, now: float | None = None) -> int:
    """The VIP level whose benefits count now: 0 unless VIP is activated."""
    v = data(state)
    now = time.time() if now is None else now
    return level(v["score"]) if v["until"] > now else 0


def benefit(state: dict | None, key: str, now: float | None = None) -> int:
    """The value of a benefit (percent or number) the player has now."""
    if not state:
        return 0
    lv = active_level(state, now)
    return BENEFITS[key][2][lv - 1] if lv else 0


def add_points(state: dict, points: int) -> None:
    data(state)["score"] += points


def activate(state: dict, days: float, now: float) -> None:
    v = data(state)
    v["until"] = max(v["until"], now) + days * DAY


def _streak_points(streak: int) -> int:
    return LOGIN_POINTS + LOGIN_STREAK_POINTS * min(streak, LOGIN_STREAK_MAX)


def daily_login(state: dict, now: float) -> int:
    """The day's login points, once a day; more for days in a row. Returns the points given."""
    v = data(state)
    day = int(now // DAY)
    if v["login_day"] == day:
        return 0
    v["streak"] = v["streak"] + 1 if v["login_day"] == day - 1 else 0
    v["login_day"] = day
    points = _streak_points(v["streak"])
    add_points(state, points)
    return points


def detail_text() -> str:
    return ("Commanders raise their VIP level with VIP points: every day's first login gives "
            f"{LOGIN_POINTS} points, {LOGIN_STREAK_POINTS} more for each day in a row (up to "
            f"{_streak_points(LOGIN_STREAK_MAX)}), and VIP points items give more. The level stays; its "
            "benefits work while VIP is activated with an activation item. Levels: "
            + ", ".join(f"VIP{lv} {need:,}" for lv, need in enumerate(NEED_SCORE, 1)) + " points.")


def info_view(state: dict, now: float) -> dict:
    """users/<id>/vip_info (and the empire's vip_info)."""
    v = data(state)
    lv = level(v["score"])
    tomorrow_streak = v["streak"] + 1 if v["login_day"] == int(now // DAY) else 0
    return {"vip_level": lv, "vip_left": max(0, int(v["until"] - now)), "vip_score": v["score"],
            "vip_curr_lvl_score": NEED_SCORE[lv - 1] if lv else 0,
            "vip_next_lvl_score": NEED_SCORE[min(lv, MAX_LEVEL - 1)],
            "tomorrow_score": _streak_points(tomorrow_streak), "vip_detail": detail_text()}


def summary_view(state: dict, now: float) -> dict:
    v = data(state)
    return {"vip_level": level(v["score"]), "vip_left": max(0, int(v["until"] - now))}


def _value(unit: str, value: int) -> str:
    return f"+{value}%" if unit == "%" else f"+{value}"


def effect_view() -> dict:
    """users/<id>/vip_effect: per level [level, {need_score}, [{name, value} ...]]."""
    return {"effect": [[lv, {"need_score": need},
                        [{"name": name, "value": _value(unit, values[lv - 1])}
                         for name, unit, values in BENEFITS.values() if values[lv - 1]]]
                       for lv, need in enumerate(NEED_SCORE, 1)]}


def apply_dark_matter(state: dict | None, amount: int, now: float | None = None) -> int:
    """Dark matter with the VIP bonus."""
    return int(math.floor(amount * (1 + benefit(state, "dark_matter", now) / 100)))
