"""Kryptons ("Galactonites"): gems that give a planet bonuses.

The original rules are lost; these are Supernova's own:

- Refining (the client's "alchemy") costs krypton stones and puts a random krypton into the
  refinery tray (18 places). The refinery has stages 1-5: every refining either moves it one
  stage up or back to stage 1, and higher stages give better kryptons (more stars).
- From the tray a krypton is collected into the krypton storage (18 places, more can be
  unlocked with stones) or broken down into krypton energy.
- Kryptons in the storage can be set into planet slots (1, up to 5 with stones), upgraded with
  energy (levels 1-10) and broken down again.
- Effects: weapon, shield and armour kryptons strengthen the planet's defense and fleets
  starting there; metal, crystal and gas kryptons raise the planet's mine output. A krypton
  gives 2 % per star plus 1 % per level above 1.
- Stones come from pirate bases and the krypton shop (dark matter, found on expeditions).
  The krypton lab research makes good kryptons more likely; it costs dark matter.
"""

import random

from . import config
from . import economy as eco
from . import planet as planet_rules

WEAPON, SHIELD, ARMOUR, METAL, CRYSTAL, GAS = 1, 2, 3, 4, 5, 6  # krypton_type, as the client draws them
NAMES = {WEAPON: "Weapon Galactonite", SHIELD: "Shield Galactonite", ARMOUR: "Armour Galactonite",
         METAL: "Metal Galactonite", CRYSTAL: "Crystal Galactonite", GAS: "Gas Galactonite"}
EFFECTS = {WEAPON: "attack", SHIELD: "shield", ARMOUR: "armour", METAL: "metal", CRYSTAL: "crystal", GAS: "deuterium"}
EFFECT_TEXT = {WEAPON: "Weapons of defense and fleets +{}%", SHIELD: "Shields of defense and fleets +{}%",
               ARMOUR: "Armour of defense and fleets +{}%", METAL: "Metal mine output +{}%",
               CRYSTAL: "Crystal mine output +{}%", GAS: "Deuterium synthesizer output +{}%"}
MAX_RANK, MAX_LEVEL, MAX_STAGE = 6, 10, 5
TRAY_SIZE, STORAGE_START, STORAGE_MAX = 18, 18, 54
HOLES_START, HOLES_MAX = 1, 5
STONE, ENERGY = 1, 2  # account crypton_type
COMBO_TIMES = 50  # the client's "refine many at once"
AUTO_BREAK_DOWN_RANK = 4  # "4-star and lower kryptons are broken down automatically"


class KryptonError(planet_rules.GameError):
    pass


# --- Numbers ------------------------------------------------------------------------------


def effect_percent(rank: int, level: int) -> int:
    return 2 * rank + (level - 1)


def refine_cost(stage: int) -> int:
    return 200 * stage


def break_down_energy(rank: int) -> int:
    return 10 * 2 ** (rank - 1)


def upgrade_energy(rank: int, level: int) -> int:
    return 20 * rank * level


def hole_cost(holes: int) -> int:
    """Stones to unlock the next planet slot when the planet has `holes`."""
    return 1000 * holes


def storage_cost(count: int) -> int:
    return 300 * count


def research_cost(level: int) -> int:
    """Dark matter for the next krypton research level."""
    return 20 * (level + 1)


def research_seconds(level: int) -> int:
    return max(1, int(3600 * (level + 1) / config.SPEED))


MAX_RESEARCH = 10


# --- State --------------------------------------------------------------------------------


def account(state: dict) -> dict:
    return state.setdefault("kryptons", {
        "stone": 0, "energy": 0, "stage": 1, "tray": [], "items": [], "next_id": 1,
        "storage": STORAGE_START, "holes": {}, "research": {"level": 0, "finish_time": 0, "total_time": 0},
    })


def _has_lab(state: dict) -> bool:
    return any(planet_rules.level(p, eco.KRYPTON_LAB) for p in state["empire"]["planets"])


def _require_lab(state: dict) -> None:
    if not _has_lab(state):
        raise KryptonError("You need a krypton lab")


def _research_level(acc: dict, now: float) -> int:
    research = acc["research"]
    if research["finish_time"] and research["finish_time"] <= now:
        research.update(level=research["level"] + 1, finish_time=0, total_time=0)
    return research["level"]


def holes(acc: dict, planet_id: int) -> int:
    return acc["holes"].get(str(planet_id), HOLES_START)


def _item(acc: dict, item_id: int) -> dict:
    found = next((i for i in acc["items"] if i["id"] == item_id), None)
    if found is None:
        raise KryptonError("This krypton is gone")
    return found


def _accounts_view(acc: dict) -> list[dict]:
    return [{"crypton_type": STONE, "number": acc["stone"]}, {"crypton_type": ENERGY, "number": acc["energy"]}]


def add_stones(state: dict, n: int) -> None:
    account(state)["stone"] += n


# --- Planet effects -----------------------------------------------------------------------


def sync_planets(state: dict) -> None:
    """Write the kryptons set into each planet to planet["kryptons"] (what the client shows) and
    planet["krypton_bonus"] (percent per effect, used by production and combat)."""
    acc = account(state)
    for planet in planet_rules.bodies(state["empire"]):
        own = sorted((i for i in acc["items"] if i.get("planet_id") == planet["planet_id"]), key=lambda i: i["sort"])
        planet["kryptons"] = [{"krypton_type": i["type"], "krypton_rank": i["rank"], "krypton_level": i["level"]}
                              for i in own]
        bonus = {}
        for i in own:
            effect = EFFECTS[i["type"]]
            bonus[effect] = bonus.get(effect, 0) + effect_percent(i["rank"], i["level"])
        planet["krypton_bonus"] = bonus


def bonus(planet: dict, effect: str) -> int:
    return planet.get("krypton_bonus", {}).get(effect, 0)


# --- Refining (alchemy) -------------------------------------------------------------------


def _tray_view(tile: dict) -> dict:
    t, rank = tile["b"], tile["s"]
    return dict(tile, title=NAMES[t], des1=EFFECT_TEXT[t].format(effect_percent(rank, 1)),
                des2=f"{rank} stars. Collect it into the storage or break it down for {break_down_energy(rank)} energy.",
                upcost=upgrade_energy(rank, 1), energy=break_down_energy(rank))


def refinery_view(state: dict) -> dict:
    acc = account(state)
    return {"expire": 0, "cost": refine_cost(acc["stage"]), "level": acc["stage"], "account": _accounts_view(acc),
            "crypton": [_tray_view(t) for t in acc["tray"]]}


def _roll_rank(stage: int, research: int, rng: random.Random) -> int:
    chance = 0.3 + 0.03 * research
    return min(MAX_RANK, 1 + sum(rng.random() < chance for _ in range(stage)))


def refine(state: dict, times: int, auto_break_down: bool, now: float, rng: random.Random | None = None) -> dict:
    rng = rng or random.Random()
    _require_lab(state)
    acc = account(state)
    research = _research_level(acc, now)
    new, made, broken = [], 0, 0
    for _ in range(max(1, min(times, COMBO_TIMES))):
        cost = refine_cost(acc["stage"])
        if acc["stone"] < cost or len(acc["tray"]) >= TRAY_SIZE:
            break
        acc["stone"] -= cost
        rank = _roll_rank(acc["stage"], research, rng)
        acc["stage"] = min(MAX_STAGE, acc["stage"] + 1) if rng.random() < 0.5 else 1
        made += 1
        if auto_break_down and rank <= AUTO_BREAK_DOWN_RANK:
            acc["energy"] += break_down_energy(rank)
            broken += 1
            continue
        tile = {"b": rng.choice(list(NAMES)), "l": 1, "s": rank}
        acc["tray"].append(tile)
        new.append(_tray_view(tile))
    if not made:
        raise KryptonError("Not enough krypton stones" if len(acc["tray"]) < TRAY_SIZE else "The tray is full")
    msg = f"Refined {made} time(s)."
    if broken:
        msg += f" {broken} weak krypton(s) were broken down into energy."
    return dict(refinery_view(state), code=0, data={"array": new, "crypton": {}}, msg=msg)


def _take_from_tray(acc: dict, buff: int, level: int, star: int) -> dict:
    for tile in acc["tray"]:
        if (tile["b"], tile["l"], tile["s"]) == (buff, level, star):
            acc["tray"].remove(tile)
            return tile
    raise KryptonError("This krypton is not in the tray")


def collect(state: dict, buff: int, level: int, star: int) -> dict:
    acc = account(state)
    stored = [i for i in acc["items"] if i.get("planet_id") is None]
    if len(stored) >= acc["storage"]:
        raise KryptonError("The krypton storage is full")
    tile = _take_from_tray(acc, buff, level, star)
    free = min(set(range(acc["storage"])) - {i["sort"] for i in stored})
    acc["items"].append({"id": acc["next_id"], "type": tile["b"], "rank": tile["s"], "level": tile["l"],
                         "planet_id": None, "sort": free, "spent": 0})
    acc["next_id"] += 1
    return {"code": 0, "expire": 0, "msg": f"{NAMES[tile['b']]} moved to the krypton storage."}


def break_down_tray(state: dict, tile_key: tuple[int, int, int] | None) -> dict:
    acc = account(state)
    tiles = [_take_from_tray(acc, *tile_key)] if tile_key else list(acc["tray"])
    if tile_key is None:
        acc["tray"] = []
    energy = sum(break_down_energy(t["s"]) for t in tiles)
    acc["energy"] += energy
    return {"code": 0, "expire": 0, "stones": acc["stone"], "energy": acc["energy"],
            "msg": f"Broken down into {energy} krypton energy."}


# --- Storage and planets ------------------------------------------------------------------


def _item_view(item: dict) -> dict:
    t, rank, level = item["type"], item["rank"], item["level"]
    return dict(item, name=NAMES[t], description=f"{rank} stars, level {level}/{MAX_LEVEL}",
                effect_description=EFFECT_TEXT[t].format(effect_percent(rank, level)),
                energy_for_update=upgrade_energy(rank, level) if level < MAX_LEVEL else 0)


def storage_view(state: dict) -> dict:
    acc = account(state)
    planets = state["empire"]["planets"]
    buffs = []
    for p in planets:
        parts = [EFFECT_TEXT[t].format(v) for t, effect in EFFECTS.items() if (v := bonus(p, effect))]
        buffs.append({"planet_id": p["planet_id"], "buffs": [{"description": ", ".join(parts)}] if parts else []})
    return {
        "kryptons": {"planet": [_item_view(i) for i in acc["items"] if i.get("planet_id") is not None],
                     "package": [_item_view(i) for i in acc["items"] if i.get("planet_id") is None],
                     "all_buffs": buffs},
        "planet_hole": [{"planet_id": p["planet_id"], "planet_name": p["planet_name"],
                         "hole_count": holes(acc, p["planet_id"])} for p in planets],
        "krypton_stone": acc["stone"], "krypton_energy": acc["energy"], "package_count": acc["storage"],
    }


def set_into_planet(state: dict, item_id: int, planet_id: int) -> None:
    acc = account(state)
    item = _item(acc, item_id)
    if not any(p["planet_id"] == planet_id for p in state["empire"]["planets"]):
        raise KryptonError("This is not your planet")
    taken = {i["sort"] for i in acc["items"] if i.get("planet_id") == planet_id}
    free = [s for s in range(holes(acc, planet_id)) if s not in taken]
    if not free:
        raise KryptonError("All krypton slots of this planet are used. Unlock another one first.")
    item.update(planet_id=planet_id, sort=free[0])
    sync_planets(state)


def take_from_planet(state: dict, item_id: int) -> None:
    acc = account(state)
    item = _item(acc, item_id)
    stored = {i["sort"] for i in acc["items"] if i.get("planet_id") is None}
    free = sorted(set(range(acc["storage"])) - stored)
    if not free:
        raise KryptonError("The krypton storage is full")
    item.update(planet_id=None, sort=free[0])
    sync_planets(state)


def upgrade(state: dict, item_id: int) -> int:
    acc = account(state)
    item = _item(acc, item_id)
    if item["level"] >= MAX_LEVEL:
        raise KryptonError("This krypton has the highest level")
    cost = upgrade_energy(item["rank"], item["level"])
    if acc["energy"] < cost:
        raise KryptonError(f"Upgrading needs {cost} krypton energy")
    acc["energy"] -= cost
    item["spent"] = item.get("spent", 0) + cost
    item["level"] += 1
    sync_planets(state)
    return upgrade_energy(item["rank"], item["level"]) if item["level"] < MAX_LEVEL else 0


def conversion_energy(item: dict) -> int:
    return break_down_energy(item["rank"]) + item.get("spent", 0) // 2


def break_down_item(state: dict, item_id: int) -> int:
    acc = account(state)
    item = _item(acc, item_id)
    energy = conversion_energy(item)
    acc["items"].remove(item)
    acc["energy"] += energy
    sync_planets(state)
    return energy


def unlock_holes(state: dict, planet_id: int, count: int, confirm: bool) -> dict:
    acc = account(state)
    if not any(p["planet_id"] == planet_id for p in state["empire"]["planets"]):
        raise KryptonError("This is not your planet")
    current = holes(acc, planet_id)
    count = max(1, min(count, HOLES_MAX - current))
    if current >= HOLES_MAX:
        raise KryptonError("All slots are unlocked")
    cost = sum(hole_cost(current + k) for k in range(count))
    if not confirm:
        return {"show_info": True, "show_message": f"Unlock {count} krypton slot(s) for {cost:,} krypton stones?"}
    if acc["stone"] < cost:
        raise KryptonError(f"Unlocking needs {cost:,} krypton stones")
    acc["stone"] -= cost
    acc["holes"][str(planet_id)] = current + count
    return {}


def unlock_storage(state: dict, count: int, confirm: bool) -> dict:
    acc = account(state)
    count = max(1, min(count, STORAGE_MAX - acc["storage"]))
    if acc["storage"] >= STORAGE_MAX:
        raise KryptonError("The storage is fully unlocked")
    cost = storage_cost(count)
    if not confirm:
        return {"show_info": True, "price": cost}
    if acc["stone"] < cost:
        raise KryptonError(f"Unlocking needs {cost:,} krypton stones")
    acc["stone"] -= cost
    acc["storage"] += count
    return {}


# --- Krypton lab research -----------------------------------------------------------------


def research_view(state: dict, now: float) -> dict:
    acc = account(state)
    level = _research_level(acc, now)
    research = acc["research"]
    view = {"research_name": "Krypton Refining", "research_level": level, "finished": not research["finish_time"],
            "up_dark_matter_require": research_cost(level) if level < MAX_RESEARCH else 0,
            "up_time": research_seconds(level), "up_credits_require": 0, "speed_up_effect": 0}
    if research["finish_time"]:
        view.update(finish_time=research["finish_time"], total_time=research["total_time"],
                    start_time=research["finish_time"] - research["total_time"])
    return {"research": view, "resource": {"stone": acc["stone"], "energy": acc["energy"]}}


def start_research(state: dict, now: float) -> dict:
    _require_lab(state)
    acc = account(state)
    level = _research_level(acc, now)
    if acc["research"]["finish_time"]:
        raise KryptonError("The research is already running")
    if level >= MAX_RESEARCH:
        raise KryptonError("The research is complete")
    cost = research_cost(level)
    empire = state["empire"]
    if empire.get("dark_matter_count", 0) < cost:
        raise KryptonError(f"The research needs {cost} dark matter. Expeditions find dark matter.")
    empire["dark_matter_count"] -= cost
    seconds = research_seconds(level)
    acc["research"].update(finish_time=int(now) + seconds, total_time=seconds)
    return research_view(state, now)


# --- Shop ---------------------------------------------------------------------------------

SHOP = [  # (type, stones, dark matter, purchases per day)
    ("stones_small", 1000, 10, 10),
    ("stones_large", 6000, 50, 3),
]


def _shop_bought(acc: dict, now: float) -> dict:
    day = int(now // 86400)
    if acc.get("shop_day") != day:
        acc["shop_day"], acc["shop_bought"] = day, {}
    return acc["shop_bought"]


def shop_view(state: dict, now: float) -> dict:
    acc = account(state)
    bought = _shop_bought(acc, now)
    data = [{"type": t, "title": f"{stones:,} krypton stones", "description": "Trade dark matter for krypton stones.",
             "times": bought.get(t, 0), "max_times": per_day, "exchange": {"base": price},
             "image": "icons/item_res_05.png"} for t, stones, price, per_day in SHOP]
    return {"data": data, "dk_balance": state["empire"].get("dark_matter_count", 0), "gu_balance": 0,
            "stone_balance": acc["stone"]}


def buy(state: dict, item_type: str, times: int, now: float) -> dict:
    acc = account(state)
    offer = next((o for o in SHOP if o[0] == item_type), None)
    if offer is None:
        raise KryptonError("Unknown offer")
    _, stones, price, per_day = offer
    bought = _shop_bought(acc, now)
    times = max(1, times)
    if bought.get(item_type, 0) + times > per_day:
        return {"code": 1, "msg": "You cannot buy this as often today."}
    empire = state["empire"]
    if empire.get("dark_matter_count", 0) < price * times:
        return {"code": 1, "msg": "Not enough dark matter. Expeditions find dark matter."}
    empire["dark_matter_count"] -= price * times
    acc["stone"] += stones * times
    bought[item_type] = bought.get(item_type, 0) + times
    view = shop_view(state, now)
    return {"code": 0, "dk_balance": view["dk_balance"], "gu_balance": 0, "stone_balance": acc["stone"],
            "data": next(d for d in view["data"] if d["type"] == item_type)}


def price_message(item_type: str, times: int) -> str:
    offer = next((o for o in SHOP if o[0] == item_type), None)
    if offer is None:
        raise KryptonError("Unknown offer")
    return f"Buy {offer[1] * times:,} krypton stones for {offer[2] * times} dark matter?"


def pirate_stones(level: int) -> int:
    """Krypton stones looted from a beaten pirate base."""
    return 50 * level * (level + 1)
