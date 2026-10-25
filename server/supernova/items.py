"""The item shop ("Universe Store"), the item depot ("My Capsule") and speed-ups.

Items are bought with dark matter and land in the depot, from where the player uses them.
Speed-up items are used from a running construction, research or shipyard order; dark matter
can also finish such an order at once. Supernova has no real-money purchases, so every item
costs dark matter (pay type 1). Catalog and prices are Supernova's own.
"""

import math
from dataclasses import dataclass

from . import ecm, flagships, kryptons
from . import planet as planet_rules

DARK_MATTER, CREDITS = 1, 2  # pay_type
FEATURED, RESOURCE, SPEEDUP, AFFAIR, OTHER = 0, 1, 2, 3, 4  # shop tabs
SECONDS_PER_DARK_MATTER = 300  # finishing an order at once costs 1 dark matter per 5 minutes left
MAX_ANNIHILATORS = 200  # wiki
COMPONENT, REAPER_COMPONENTS = 5001, 3000  # wiki: a Reaper takes 3,000 intricate components


class ItemError(planet_rules.GameError):
    pass


@dataclass(frozen=True)
class Item:
    id: int
    category: int
    name: str
    desc: str
    icon: str  # a texture the client has in data2
    price: int
    resources: tuple[int, int, int] = (0, 0, 0)  # metal, crystal, deuterium
    seconds: int = 0  # speed-up
    ecm_chips: int = 0
    alloy: int = 0
    krypton_stones: int = 0
    featured: bool = False
    code: str = ""
    device: tuple[int, int] | None = None  # (device type, grade), see devices.py
    unit: int = 0  # a ship this item turns into
    sold: bool = True


def _res(item_id, name, amount, index, icon, price, featured=False, code=""):
    resources = tuple(amount if i == index else 0 for i in range(3))
    return Item(item_id, RESOURCE, f"{name} {amount:,}", f"{amount:,} {name.lower()} for the planet you are on.",
                icon, price, resources=resources, featured=featured, code=code)


def _speedup(item_id, label, seconds, price, featured=False):
    return Item(item_id, SPEEDUP, f"Speed-up {label}",
                f"Shortens a running construction, research or shipyard order by {label}.",
                "item_res_09.png", price, seconds=seconds, featured=featured)


CATALOG = [
    # code 1101: the tutorial points at this item when it asks the player to buy resources
    _res(1101, "Metal", 10_000, 0, "item_res_04.png", 2, featured=True, code="1101"),
    _res(1102, "Metal", 100_000, 0, "item_res_04.png", 15),
    _res(1103, "Crystal", 10_000, 1, "item_res_03.png", 3),
    _res(1104, "Crystal", 100_000, 1, "item_res_03.png", 22),
    _res(1105, "Deuterium", 10_000, 2, "item_res_01.png", 4),
    _res(1106, "Deuterium", 100_000, 2, "item_res_01.png", 30),
    _speedup(2101, "5 min", 300, 1),
    _speedup(2102, "30 min", 1800, 5, featured=True),
    _speedup(2103, "1 h", 3600, 9),
    _speedup(2104, "4 h", 4 * 3600, 30),
    _speedup(2105, "12 h", 12 * 3600, 80),
    Item(3101, AFFAIR, "ECM chips x5", "Five core chips for ECM module upgrades.", "item_res_10.png", 20,
         ecm_chips=5),
    Item(3102, AFFAIR, "Alloy fragment box", f"{flagships.BOX_ALLOY} alloy fragments for the Pioneer Lab.",
         "item_res_07.png", 15, alloy=flagships.BOX_ALLOY),
    Item(3103, AFFAIR, "Krypton stones x500", "500 krypton stones for the Krypton Lab.", "item_res_13.png", 15,
         krypton_stones=500),
    Item(5002, OTHER, "Annihilator construction permit",
         f"Builds one Annihilator on the planet you are on. A commander can have at most {MAX_ANNIHILATORS}.",
         "ship_destruction_star.png", 25, unit=28),
    Item(5003, OTHER, "Devourer Interceptor", "One Devourer Interceptor for the planet you are on. "
         "Its shots pass through shields.", "item_res_14.png", 50, unit=25),
    Item(5004, OTHER, "Titanenergetic Frigate", "One Titanenergetic Frigate for the planet you are on. "
         "Its armour is immense.", "item_res_12.png", 80, unit=26),
    Item(COMPONENT, OTHER, "Intricate component", f"Left behind by large warships destroyed in battle. "
         f"{REAPER_COMPONENTS:,} build one Reaper.", "item_res_15.png", 0, sold=False),
]
from . import devices  # noqa: E402 (devices builds its items with Item)

CATALOG += devices.catalog()
BY_ID = {i.id: i for i in CATALOG}


def item(item_id: int) -> Item:
    found = BY_ID.get(item_id)
    if found is None:
        raise ItemError("This item does not exist")
    return found


def _view(i: Item) -> dict:
    return {"id": i.id, "game_item_id": i.id, "name": i.name, "desc": i.desc, "img": f"icons/{i.icon}",
            "pay_type": DARK_MATTER, "sale_price": i.price, "code": i.code, "item_type": 1, "category": i.category,
            "is_featured": i.featured, "is_gift": False, "step_gacha": 0}


def shop(category: int) -> list[dict]:
    return [_view(i) for i in CATALOG
            if i.sold and (i.category == category or (category == FEATURED and i.featured))]


# --- Depot ---------------------------------------------------------------------------------------


def _depot(state: dict) -> dict:
    return state.setdefault("items", {})


def amount(state: dict, item_id: int) -> int:
    return _depot(state).get(str(item_id), 0)


def _add(state: dict, item_id: int, n: int) -> None:
    depot = _depot(state)
    left = depot.get(str(item_id), 0) + n
    if left > 0:
        depot[str(item_id)] = left
    else:
        depot.pop(str(item_id), None)


def depot_view(state: dict, category: int) -> list[dict]:
    return [owned_view(state, i) for i in CATALOG
            if amount(state, i.id) and (i.category == category or (category == FEATURED and i.featured))]


def owned_view(state: dict, i: Item) -> dict:
    return dict(_view(i), amount=amount(state, i.id), can_be_use=i.category != SPEEDUP and i.device is None)


def _pay(state: dict, price: int) -> None:
    empire = state["empire"]
    if empire.get("dark_matter_count", 0) < price:
        raise ItemError(f"You need {price:,} dark matter")
    empire["dark_matter_count"] -= price


def buy(state: dict, item_id: int, n: int) -> dict:
    i = item(item_id)
    if not 1 <= n <= 999 or not i.sold:
        raise ItemError("Invalid number" if i.sold else "This item is not for sale")
    _pay(state, i.price * n)
    _add(state, i.id, n)
    empire = state["empire"]
    return {"darkmatter": empire["dark_matter_count"], "credits": empire.get("gu_count", 0),
            "info": f"{i.name} x{n} is in your capsule.", "id": i.id, "item_type": 1}


def use(state: dict, item_id: int, n: int, planet: dict) -> str:
    i = item(item_id)
    if i.category == SPEEDUP:
        raise ItemError("Use speed-ups on a running construction, research or shipyard order")
    if i.device:
        raise ItemError("Install devices in the Modification screen")
    if not 1 <= n <= amount(state, i.id):
        raise ItemError("You do not have that many")
    if i.id == COMPONENT:
        return _build_reapers(state, n, planet)
    if i.unit == 28:
        owned = sum(planet_rules.fleet(p, 28)["amount"] for p in planet_rules.bodies(state["empire"]))
        if owned + n > MAX_ANNIHILATORS:
            raise ItemError(f"A commander can have at most {MAX_ANNIHILATORS} Annihilators")
    _add(state, i.id, -n)
    if i.unit:
        planet_rules.fleet(planet, i.unit)["amount"] += n
    for res, value in zip(planet_rules.RESOURCES, i.resources):
        planet[res] += value * n
    if i.ecm_chips:
        ecm.add_chips(state, i.ecm_chips * n)
    if i.alloy:
        flagships.data(state)["alloy"] += i.alloy * n
    if i.krypton_stones:
        kryptons.add_stones(state, i.krypton_stones * n)
    return f"Used {i.name} x{n}."


def _build_reapers(state: dict, n: int, planet: dict) -> str:
    reapers = n // REAPER_COMPONENTS
    if not reapers:
        raise ItemError(f"A Reaper takes {REAPER_COMPONENTS:,} intricate components")
    _add(state, COMPONENT, -reapers * REAPER_COMPONENTS)
    planet_rules.fleet(planet, 29)["amount"] += reapers
    return f"{reapers} Reaper(s) built from {reapers * REAPER_COMPONENTS:,} intricate components."


def add_components(state: dict, enemy_lost: dict[int, int]) -> int:
    """Intricate components from large enemy ships destroyed in a battle: hull / 5,000 each."""
    from . import combat
    found = sum(max(1, combat.STATS[t].hull // 5000) * n for t, n in enemy_lost.items()
                if t in combat.STATS and combat.STATS[t].hull >= 7000 and t in planet_rules.eco.SHIPS)
    if found:
        _add(state, COMPONENT, found)
    return found


def discard(state: dict, item_id: int) -> None:
    i = item(item_id)
    _add(state, i.id, -amount(state, i.id))


# --- Speed-ups -----------------------------------------------------------------------------------

BUILDING, SHIPYARD, RESEARCH = "building", "shipyard", "research"


def _find(empire: dict, kind: str, event_id: int):
    """(event, planet) of a running order."""
    if kind == RESEARCH:
        event = next((e for e in empire["research_upgrading_events"] if e["event_id"] == event_id), None)
        return event, None
    key = "building_events" if kind == BUILDING else "fleet_building_events"
    for planet in planet_rules.bodies(empire):
        event = next((e for e in planet[key] if e["event_id"] == event_id), None)
        if event:
            return event, planet
    return None, None


def _running(empire: dict, kind: str, event_id: int, now: float):
    planet_rules.tick(empire, now)
    event, planet = _find(empire, kind, event_id)
    if event is None:
        raise ItemError("This order is already finished")
    if kind == SHIPYARD and event["start_time"] > now:
        raise ItemError("Only the order the shipyard is working on can be sped up")
    return event, planet


def instant_price(left_seconds: float) -> int:
    return max(1, math.ceil(left_seconds / SECONDS_PER_DARK_MATTER))


def speedup_info(state: dict, kind: str, event_id: int, now: float) -> dict:
    event, _ = _running(state["empire"], kind, event_id, now)
    left = max(0, int(event["finish_time"] - now))
    # this screen always draws a credit icon next to the price, so the price says what it is
    return {"items": [dict(owned_view(state, i), sale_price=f"{i.price} DM") for i in CATALOG
                      if i.category == SPEEDUP],
            "left_time": left, "dark_matter_count": instant_price(left)}


def speed_up(state: dict, kind: str, event_id: int, item_id: int, buy_first: bool, now: float) -> None:
    """Shorten an order with a speed-up item (bought first if `buy_first`), or finish it with
    dark matter when `item_id` is 0."""
    empire = state["empire"]
    event, planet = _running(empire, kind, event_id, now)
    left = event["finish_time"] - now
    if item_id == 0:
        _pay(state, instant_price(left))
        seconds = left
    else:
        i = item(item_id)
        if i.category != SPEEDUP:
            raise ItemError("This is not a speed-up")
        if buy_first:
            _pay(state, i.price)
        elif not amount(state, i.id):
            raise ItemError(f"You have no {i.name}")
        else:
            _add(state, i.id, -1)
        seconds = min(left, i.seconds)
    event["finish_time"] -= seconds
    if kind == SHIPYARD:
        event["start_time"] -= seconds
        planet_rules._reschedule_shipyard(planet, now)
    planet_rules.tick(empire, now)
