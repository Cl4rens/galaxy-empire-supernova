"""Electronic counter measures (ECM), as the client's help text describes them:

One ECM module per empire, upgraded with ECM chips. An upgrade can fail; every failure adds
"fortune", which raises the chance of the next try, and full fortune guarantees success.
Fortune is lost at midnight (UTC). Each level raises weapons, shields and armour of all fleets
and defense. Chips come from beaten pirate bases or are bought with dark matter.

Numbers are Supernova's own.
"""

import random
import time

MAX_LEVEL = 20
CHIP_PRICE = 5  # dark matter per chip when buying


def chip_cost(level: int) -> int:
    """Chips for one try to go from `level` to the next."""
    return 1 + level // 2


def full_fortune(level: int) -> int:
    return 5 + 5 * level


def enhance(level: int) -> float:
    """Bonus of a level as a fraction (0.02 = 2 %)."""
    return round(0.02 * level, 4)


def percent(state: dict) -> int:
    return round(100 * enhance(data(state)["level"]))


def data(state: dict) -> dict:
    d = state.setdefault("ecm", {"level": 0, "chips": 0, "fortune": 0, "fortune_day": 0})
    day = int(time.time() // 86400)
    if d["fortune_day"] != day:
        d["fortune"], d["fortune_day"] = 0, day
    return d


def add_chips(state: dict, n: int) -> None:
    data(state)["chips"] += n


def _effect(level: int) -> dict:
    return {"enhence": enhance(level), "chip_cost": chip_cost(level), "full_bless": full_fortune(level)}


def view(state: dict) -> dict:
    d = data(state)
    level = d["level"]
    return {"curr_lvl": level, "max_lvl": MAX_LEVEL, "curr_bless": d["fortune"], "chip_own": d["chips"],
            "curr_effect": _effect(level), "next_effect": _effect(min(level + 1, MAX_LEVEL)),
            "can_auto_up": state["empire"].get("dark_matter_count", 0) >= CHIP_PRICE * chip_cost(level),
            "item_info": {"name": "ECM chip", "desc": "The core chip, an essential component for ECM module "
                                                       "upgrades. Pirate bases carry some.",
                          "img": "icons/item_res_06.png"}}


def level_up(state: dict, auto_buy: bool, rng: random.Random | None = None) -> dict:
    rng = rng or random.Random()
    d = data(state)
    level = d["level"]
    if level >= MAX_LEVEL:
        raise ValueError("The ECM module has the highest level")
    cost = chip_cost(level)
    if d["chips"] < cost:
        missing = cost - d["chips"]
        empire = state["empire"]
        if not auto_buy or empire.get("dark_matter_count", 0) < missing * CHIP_PRICE:
            raise ValueError("Not enough ECM chips")
        empire["dark_matter_count"] -= missing * CHIP_PRICE
        d["chips"] += missing
    d["chips"] -= cost
    full = full_fortune(level)
    chance = 0.5 / (1 + level / 4) + 0.5 * d["fortune"] / full
    if d["fortune"] >= full or rng.random() < chance:
        d["level"] += 1
        d["fortune"] = 0
    else:
        d["fortune"] = min(full, d["fortune"] + 1 + level // 3)
    return view(state)
