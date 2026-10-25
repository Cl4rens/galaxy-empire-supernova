"""Officers ("Commanders" in the wiki): hired for a while, they strengthen the empire.

Six kinds with three levels each; level 1 is hired with dark matter for 7 or 30 days, levels 2
and 3 with credits for 7 days. Effects and prices are the Galaxy Empire wiki's
(galaxyempire.fandom.com/wiki/Commanders); where the wiki is vague, Supernova decides:

- Commander: construction time -10 / -10 / -20 %
- Engineer: fusion energy +10 / +10 / +20 %; of the defense a battle destroys, the part that
  would be lost (30 %) is saved by 50 / 70 / 75 %
- Technocrat: research time -25 / -30 / -35 %
- Geologist: mine output +10 / +20 / +30 %
- Admiral: defense construction time -10 / -15 / -20 % (the wiki's extra fleet slots do not
  apply: Supernova has no fleet slot limit)
- Guider: of the ships an expedition loses, 35 / 45 / 55 % come back
The building queues of the wiki's commander do not apply either (one construction at a time).
A higher level replaces a lower one; hiring the same kind again adds time.
"""

import time

COMMANDER, ENGINEER, TECHNOCRAT, GEOLOGIST, ADMIRAL, GUIDER = (
    "commander", "engineer", "technocrat", "geologist", "admiral", "guider")
NAMES = {COMMANDER: "Commander", ENGINEER: "Engineer", TECHNOCRAT: "Technocrat", GEOLOGIST: "Geologist",
         ADMIRAL: "Admiral", GUIDER: "Guider"}
EFFECT = {  # per level 1..3
    COMMANDER: (10, 10, 20), ENGINEER: (10, 10, 20), TECHNOCRAT: (25, 30, 35),
    GEOLOGIST: (10, 20, 30), ADMIRAL: (10, 15, 20), GUIDER: (35, 45, 55),
}
ENGINEER_SAVE = (50, 70, 75)
DESCRIPTIONS = {
    COMMANDER: "Construction time -{}%.",
    ENGINEER: "Fusion energy +{}%; saves more destroyed defense.",
    TECHNOCRAT: "Research time -{}%.",
    GEOLOGIST: "Mine output +{}%.",
    ADMIRAL: "Defense construction time -{}%.",
    GUIDER: "{}% of the ships an expedition loses come back.",
}
# (kind, level, days) -> (pay type, price); wiki prices
PRICES = {
    (COMMANDER, 1, 7): (1, 25), (COMMANDER, 1, 30): (1, 100), (COMMANDER, 2, 7): (2, 400), (COMMANDER, 3, 7): (2, 800),
    (ENGINEER, 1, 7): (1, 25), (ENGINEER, 1, 30): (1, 100), (ENGINEER, 2, 7): (2, 400), (ENGINEER, 3, 7): (2, 800),
    (TECHNOCRAT, 1, 7): (1, 20), (TECHNOCRAT, 1, 30): (1, 80), (TECHNOCRAT, 2, 7): (2, 400),
    (TECHNOCRAT, 3, 7): (2, 850),
    (GEOLOGIST, 1, 7): (1, 25), (GEOLOGIST, 1, 30): (1, 100), (GEOLOGIST, 2, 7): (2, 250), (GEOLOGIST, 3, 7): (2, 480),
    (ADMIRAL, 1, 7): (1, 20), (ADMIRAL, 1, 30): (1, 80), (ADMIRAL, 2, 7): (2, 300), (ADMIRAL, 3, 7): (2, 700),
    (GUIDER, 1, 7): (1, 60), (GUIDER, 1, 30): (1, 100), (GUIDER, 2, 7): (2, 999), (GUIDER, 3, 7): (2, 1050),
}
ICON = "Buff_AffairsOfficer_people.png"  # the client's own officer picture
BUFF_TYPES = {COMMANDER: 1, ENGINEER: 2, TECHNOCRAT: 3, GEOLOGIST: 4, ADMIRAL: 5, GUIDER: 6}


def _hired(empire: dict) -> dict:
    return empire.setdefault("officers", {})


def level(empire: dict, kind: str, now: float | None = None) -> int:
    o = _hired(empire).get(kind)
    if not o or o["until"] <= (time.time() if now is None else now):
        return 0
    return o["level"]


def percent(empire: dict, kind: str, now: float | None = None) -> int:
    lv = level(empire, kind, now)
    return EFFECT[kind][lv - 1] if lv else 0


def defense_save(empire: dict, now: float | None = None) -> int:
    lv = level(empire, ENGINEER, now)
    return ENGINEER_SAVE[lv - 1] if lv else 0


def hire(empire: dict, kind: str, lv: int, days: int, now: float) -> str:
    o = _hired(empire).get(kind)
    seconds = days * 86400
    if o and o["until"] > now and o["level"] == lv:
        o["until"] += seconds
    elif o and o["until"] > now and o["level"] > lv:
        raise ValueError(f"A better {NAMES[kind]} is already hired")
    else:
        _hired(empire)[kind] = {"level": lv, "until": int(now) + seconds}
    return f"{NAMES[kind]} Lv.{lv} hired for {days} days."


def boosts(empire: dict, now: float | None = None) -> dict:
    """What planets need for their timers and production (kept on each planet by tick)."""
    return {"build": percent(empire, COMMANDER, now), "research": percent(empire, TECHNOCRAT, now),
            "defense": percent(empire, ADMIRAL, now), "mines": percent(empire, GEOLOGIST, now),
            "fusion": percent(empire, ENGINEER, now)}


def buff_list(empire: dict, now: float) -> list[dict]:
    """The officers panel of the main screen."""
    out = []
    for kind, o in _hired(empire).items():
        if o["until"] > now:
            effect = EFFECT[kind][o["level"] - 1]
            out.append({"buffer_name": f"{NAMES[kind]} Lv.{o['level']}", "end_time": o["until"],
                        "buffer_desc": DESCRIPTIONS[kind].format(effect), "buff_type": BUFF_TYPES[kind],
                        "img": f"icons/{ICON}"})
    return out
