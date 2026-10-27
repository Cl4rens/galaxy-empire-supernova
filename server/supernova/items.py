"""The item shop ("Space Store"), the item depot ("Capsule") and speed-ups.

Items are bought with dark matter or credits and land in the depot, from where the player uses
them. Speed-up items are used from a running construction, research or shipyard order; dark
matter can also finish such an order at once.

The catalog follows the Galaxy Empire wiki (galaxyempire.fandom.com/wiki/Space_Store): resource
containers, speed stones (credits), officers (dark matter at level 1, credits above), terraformers
(credits) and the like. Prices the wiki does not give are Supernova's own. Supernova has no
real-money purchases; credits only come from the server operator for now.

Icons: the original server sent pictures by URL; the client keeps only a few it had downloaded
(icons/item_res_NN.png), and the battle report shows what they are: 01 deuterium, 03 crystal,
04 metal, 05 dark matter, 06 dark energy, 07 alloy, 08 credits, 09 speed-up, 10 galaxium,
11 krypton, 12 ECM chip, 13 crystal ore, 14 energy gel, 15 titanenergetic cube; 16, 11, 07 and
18 are also the armour, shield, attack and speed symbols of ship modification. Where the server
has the original picture (icons.py), an item shows that instead.
"""

import math
import random
import time
from dataclasses import dataclass

from . import ecm, flagships, icons, kryptons, officers
from . import planet as planet_rules

DARK_MATTER, CREDITS = 1, 2  # pay_type
FEATURED, RESOURCE, SPEEDUP, AFFAIR, OTHER = 0, 1, 2, 3, 4  # shop tabs; AFFAIR is "Officer"
VIP_POINTS, VIP_TIME = 10, 11  # the VIP store's tabs (vip.py), not in the item shop
GAMMA_DETECTOR_S, GAMMA_DETECTOR_L = 3301, 3302
SECONDS_PER_DARK_MATTER = 300  # finishing an order at once costs 1 dark matter per 5 minutes left
MAX_ANNIHILATORS = 200  # wiki
COMPONENT, REAPER_COMPONENTS = 5001, 3000  # wiki: a Reaper takes 3,000 intricate components
GALAXIUM, LIGHT_SPEED_ENGINE = 5005, 2301
INTERCEPTOR_GALAXIUM = 150  # wiki: an interceptor is fused from 150 galaxium ...
FREE_INTERCEPTORS = 1000  # ... and from 1,000 owned on costs 1 dark matter or 2 credits more
CURRENCY = {DARK_MATTER: "dark matter", CREDITS: "credits"}


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
    pay_type: int = DARK_MATTER
    officer: tuple[str, int, int] | None = None  # (kind, level, days)
    fields: int = 0  # terraformer: up to this many extra fields
    shortcut: int = 0  # quick-buy list the item appears in (GameUIQuickBuyMall)
    pack: tuple[tuple[int, int], ...] = ()  # gift pack: (item id, number) put into the capsule
    units: tuple[tuple[int, int], ...] = ()  # gift pack: (ship type, number) for the planet
    picture: str = ""  # the original picture, if the server has it (icons.py)
    vip_points: int = 0  # VIP points, added when bought
    vip_days: int = 0  # days of VIP benefits, added when bought
    explorations: int = 0  # gamma detector: secure explorations beyond the day's
    special: tuple = ()  # planet and moon items of specials.py: (kind, *arguments)
    item_type: int = 1  # what the capsule does on "Use" (92: asks for coordinates)

    def image(self) -> str:
        """What the client shows for the item ("icons/..."), the original picture if the server has it."""
        return icons.pick(self.picture, self.icon)


# quick-buy lists of the client
SHORTCUT_METAL, SHORTCUT_CRYSTAL, SHORTCUT_DEUTERIUM, SHORTCUT_FIELDS, SHORTCUT_OFFICERS = 1, 2, 3, 4, 11
SHORTCUT_SHIFT, SHORTCUT_MOON_FIELDS, SHORTCUT_PROJECTION, SHORTCUT_PEACE, SHORTCUT_EXPLORER = 5, 7, 8, 9, 10
CHOOSE_POSITION = 92  # item_type: the capsule opens the coordinate picker (GameUIDirecMigration)


def _container(item_id, name, size, amount, index, icon, price, shortcut, featured=False, code=""):
    resources = tuple(amount if i == index else 0 for i in range(3))
    return Item(item_id, RESOURCE, f"{name} Container ({size})",
                f"{amount:,} {name.lower()} for the planet you are on.", icon, price, resources=resources,
                featured=featured, code=code, shortcut=shortcut, picture=f"container_{name.lower()}.png")


def _specials() -> list[Item]:
    """The wiki's planet and moon items (specials.py)."""
    from . import specials
    out = []
    # wiki: Projections Lv.1 359 credits, Lv.2 499, for a week
    for n, kind in enumerate(specials.PROJECTIONS, 1):
        for lv, price in ((1, 359), (2, 499)):
            out.append(Item(3400 + 10 * n + lv, OTHER, f"{specials.PROJECTIONS[kind][0]} Lv.{lv}",
                            specials.projection_desc(kind, lv), "item_res_06.png", price, pay_type=CREDITS,
                            special=("projection", kind, lv), shortcut=SHORTCUT_PROJECTION,
                            picture=f"projection_{kind}_{lv}.png"))
    # wiki: Peace Agreement Lv.1 4 hours for 69 credits, Lv.2 12 hours for 159
    for lv, hours, price in ((1, 4, 69), (2, 12, 159)):
        out.append(Item(3500 + lv, OTHER, f"Peace Agreement Lv.{lv}", f"No one can attack the planet you are on "
                        f"for {hours} hours. One agreement a day; attacking someone yourself ends it.",
                        "item_res_16.png", price, pay_type=CREDITS, special=("peace", hours),
                        shortcut=SHORTCUT_PEACE, picture=f"peace_{lv}.png"))
    # wiki: Planet Shifter 799 credits, Advanced (random galaxy) 1,000, Directional 1,499
    out += [
        Item(3601, OTHER, "Planet Shifter", "Moves the planet you are on, with its moon and buildings, to a random "
             "free position in its galaxy.", "item_res_13.png", 799, pay_type=CREDITS,
             special=("shift", specials.SHIFT_RANDOM), shortcut=SHORTCUT_SHIFT, picture="planet_shifter.png"),
        Item(3602, OTHER, "Advanced Planet Shifter", "Moves the planet you are on, with its moon and buildings, to a "
             "random free position in a random galaxy.", "item_res_13.png", 1000, pay_type=CREDITS,
             special=("shift", specials.SHIFT_GALAXY), shortcut=SHORTCUT_SHIFT, picture="planet_shifter_galaxy.png"),
        Item(3603, OTHER, "Directional Planet Shifter", "Moves the planet you are on, with its moon and buildings, "
             "to a free position you choose. Once in 48 hours.", "item_res_13.png", 1499, pay_type=CREDITS,
             special=("shift", specials.SHIFT_CHOSEN), shortcut=SHORTCUT_SHIFT, item_type=CHOOSE_POSITION,
             picture="planet_shifter_chosen.png"),
    ]
    # wiki: 20 runs for 25 credits in the store (50 and 100 runs came from events)
    for lv, (runs, price) in enumerate(((20, 25), (50, 59), (100, 109)), 1):
        out.append(Item(3700 + lv, OTHER, f"Nebula Navigator ({runs} runs)", f"Your next expedition fleet explores "
                        f"the nebula up to {runs} times in a row (as long as secure explorations are left); ships it "
                        "loses are replaced from its planet.", "item_res_13.png", price, pay_type=CREDITS,
                        special=("navigator", runs), shortcut=SHORTCUT_EXPLORER, picture=f"nebula_navigator_{lv}.png"))
    # wiki store screenshots: Lunar Terraformer 1-10 fields for 159 credits, Lv.2 5-15 for 245
    for lv, (low, high, price) in enumerate(((1, 10, 159), (5, 15, 245)), 1):
        out.append(Item(3800 + lv, OTHER, f"Lunar Terraformer Lv.{lv}", f"Adds {low}-{high} building fields to the "
                        "moon you are on (the best result counts).", "chests.png", price, pay_type=CREDITS,
                        special=("lunar_fields", low, high), shortcut=SHORTCUT_MOON_FIELDS,
                        picture=f"lunar_terraformer_{lv}.png"))
    # wiki store screenshots: Lunar Catalyst 10 % for 99 credits, Advanced 20 % for 169
    out += [
        Item(3811, OTHER, "Lunar Catalyst", "A 10% chance of creating a moon around the planet you are on.",
             "item_res_13.png", 99, pay_type=CREDITS, special=("moon", 0.10), picture="lunar_catalyst.png"),
        Item(3812, OTHER, "Advanced Lunar Catalyst", "A 20% chance of creating a moon around the planet you are on.",
             "item_res_13.png", 169, pay_type=CREDITS, special=("moon", 0.20), picture="lunar_catalyst_advanced.png"),
    ]
    # wiki: Crystal Reformers turn 100k / 1M / 10M crystal into half as much metal for 2 / 12 / 70 dark
    # matter, Lv.2 into 80 % for 9 / 59 / 349 credits. The wiki's Gas Reformers page repeats that
    # text; here they turn gas into crystal the same way.
    for base, (source, target, name) in enumerate((("crystal", "metal", "Crystal"),
                                                   ("deuterium", "crystal", "Gas"))):
        for n, (size, amount, dm, credits) in enumerate((("S", 100_000, 2, 9), ("M", 1_000_000, 12, 59),
                                                         ("L", 10_000_000, 70, 349))):
            for lv, share, price, pay in ((1, 50, dm, DARK_MATTER), (2, 80, credits, CREDITS)):
                label = f"{name} Reformer ({size})" + (" Lv.2" if lv == 2 else "")
                out.append(Item(3901 + 10 * base + 3 * (lv - 1) + n, RESOURCE, label,
                                f"Turns {amount:,} {name.lower()} on the planet you are on into "
                                f"{amount * share // 100:,} {target}.", "item_res_04.png" if target == "metal"
                                else "item_res_03.png", price, pay_type=pay,
                                special=("reform", source, amount, target, amount * share // 100),
                                picture=f"reformer_{name.lower()}_{lv}.png"))
    return out


def _speedup(item_id, label, seconds, price, pay_type=DARK_MATTER, featured=False):
    name = f"Speed Stone {label}" if pay_type == CREDITS else f"Speed-up {label}"
    return Item(item_id, SPEEDUP, name, f"Shortens a running construction, research or shipyard order by {label}.",
                "item_res_09.png", price, seconds=seconds, featured=featured, pay_type=pay_type,
                picture=f"speedstone_{label}.png" if pay_type == CREDITS else "")


def _officers() -> list[Item]:
    out = []
    for n, ((kind, lv, days), (pay, price)) in enumerate(officers.PRICES.items()):
        out.append(Item(6001 + n, AFFAIR, f"{officers.NAMES[kind]} Lv.{lv} ({days} days)",
                        officers.describe(kind, lv) + f" Hired for {days} days.",
                        officers.ICON, price, pay_type=pay, officer=(kind, lv, days), shortcut=SHORTCUT_OFFICERS,
                        featured=(kind, lv, days) == (officers.GEOLOGIST, 1, 7), picture=officers.picture(kind, lv)))
    return out


CATALOG = [
    # code 1101: the tutorial points at this item when it asks the player to buy resources
    # wiki: 10,000 metal, 6,500 crystal or 3,500 gas for 1 dark matter; M ten times, L a hundred times
    _container(1101, "Metal", "S", 10_000, 0, "item_res_04.png", 1, SHORTCUT_METAL, featured=True, code="1101"),
    _container(1102, "Metal", "M", 100_000, 0, "item_res_04.png", 10, SHORTCUT_METAL),
    _container(1107, "Metal", "L", 1_000_000, 0, "item_res_04.png", 100, SHORTCUT_METAL),
    _container(1103, "Crystal", "S", 6_500, 1, "item_res_03.png", 1, SHORTCUT_CRYSTAL),
    _container(1104, "Crystal", "M", 65_000, 1, "item_res_03.png", 10, SHORTCUT_CRYSTAL),
    _container(1108, "Crystal", "L", 650_000, 1, "item_res_03.png", 100, SHORTCUT_CRYSTAL),
    _container(1105, "Gas", "S", 3_500, 2, "item_res_01.png", 1, SHORTCUT_DEUTERIUM),
    _container(1106, "Gas", "M", 35_000, 2, "item_res_01.png", 10, SHORTCUT_DEUTERIUM),
    _container(1109, "Gas", "L", 350_000, 2, "item_res_01.png", 100, SHORTCUT_DEUTERIUM),
    _speedup(2101, "5 min", 300, 1),
    _speedup(2102, "30 min", 1800, 5, featured=True),
    _speedup(2103, "1 h", 3600, 9),
    _speedup(2104, "4 h", 4 * 3600, 30),
    _speedup(2105, "12 h", 12 * 3600, 80),
    # the wiki's speed stones, sold for credits (prices from its store screenshots)
    _speedup(2201, "1m", 60, 2, CREDITS),
    _speedup(2202, "5m", 300, 5, CREDITS),
    _speedup(2203, "30m", 1800, 18, CREDITS),
    _speedup(2204, "1h", 3600, 36, CREDITS),
    _speedup(2205, "5h", 5 * 3600, 180, CREDITS),
    _speedup(2206, "24h", 24 * 3600, 864, CREDITS),
    Item(LIGHT_SPEED_ENGINE, SPEEDUP, "Light Speed Engine", "Brings a returning fleet home at once (from the flight "
         "list).", "item_res_02.png", 10, pay_type=CREDITS),
    Item(3101, OTHER, "ECM chips x5", "Five core chips for ECM module upgrades.", "item_res_12.png", 20,
         ecm_chips=5, picture="ecm_chip.png"),
    Item(3102, OTHER, "Alloy fragment box", f"{flagships.BOX_ALLOY} alloy fragments for the Pioneer Lab.",
         "hiEnergy_alloy_box.png", 15, alloy=flagships.BOX_ALLOY),
    Item(3104, OTHER, "Alloy fragment box (credits)", f"{flagships.BOX_ALLOY} alloy fragments for the Pioneer Lab.",
         "hiEnergy_alloy_box.png", 150, alloy=flagships.BOX_ALLOY, pay_type=CREDITS),
    Item(3103, OTHER, "Krypton stones x500", "500 krypton stones for the Krypton Lab.", "item_res_11.png", 15,
         krypton_stones=500),
    # wiki: small / medium / large terraformer for 99 / 249 / 599 credits, 1-15 / 1-20 / 1-25 fields;
    # only the best roll so far counts
    Item(3201, OTHER, "Terraformer (S)", "Adds 1-15 building fields to the planet you are on (the best result "
         "of all terraformers used there counts). Not for moons.", "chests.png", 99, pay_type=CREDITS, fields=15,
         shortcut=SHORTCUT_FIELDS, picture="terraformer.png"),
    Item(3202, OTHER, "Terraformer (M)", "Adds 1-20 building fields to the planet you are on (the best result "
         "counts). Not for moons.", "chests.png", 249, pay_type=CREDITS, fields=20, shortcut=SHORTCUT_FIELDS,
         picture="terraformer.png"),
    Item(3203, OTHER, "Terraformer (L)", "Adds 1-25 building fields to the planet you are on (the best result "
         "counts). Not for moons.", "chests.png", 599, pay_type=CREDITS, fields=25, shortcut=SHORTCUT_FIELDS,
         picture="terraformer.png"),
    Item(5002, OTHER, "Annihilator construction permit",
         f"Builds one Annihilator on the planet you are on. A commander can have at most {MAX_ANNIHILATORS}.",
         "ship_destruction_star.png", 25, unit=28),
    Item(5003, OTHER, "Devourer Interceptor", "One Devourer Interceptor for the planet you are on. "
         "Its shots pass through shields.", "item_res_14.png", 50, unit=25, picture="ship_devourer_interceptor.png"),
    Item(5004, OTHER, "Titanenergetic Frigate", "One Titanenergetic Frigate for the planet you are on. "
         "Its armour is immense.", "item_res_15.png", 80, unit=26, picture="ship_titanenergetic_frigate.png"),
    Item(COMPONENT, OTHER, "Intricate component", f"Left behind by large warships destroyed in battle. "
         f"{REAPER_COMPONENTS:,} build one Reaper.", "box_activity.png", 0, sold=False),
    # code 01401 makes the capsule open the interceptor fusion screen
    Item(GALAXIUM, OTHER, "Galaxium", f"Collected from battles. {INTERCEPTOR_GALAXIUM} galaxium fuse into one "
         "interceptor.", "item_res_10.png", 0, sold=False, code="01401"),
    # gift packs: the client shows their contents (gachas/{id}) and opens them in the capsule
    Item(4001, OTHER, "Commander Pack", "Resources, ECM chips and speed-ups for a fast start.", "icon_superGift_01.png", 60,
         resources=(150_000, 100_000, 50_000), ecm_chips=10, pack=((2103, 3),), featured=True),
    Item(4002, OTHER, "Fleet Pack", "Two Devourer Interceptors and a Titanenergetic Frigate.", "icon_superGift_01.png", 199,
         pay_type=CREDITS, units=((25, 2), (26, 1)), featured=True),
]
# The VIP store (vip.py): points items with the wiki's level badges, and activation items.
# Points cost about a dark matter per 10 points, less in bigger packs.
CATALOG += [
    Item(7000 + n, VIP_POINTS, f"{points:,} VIP points", f"Adds {points:,} VIP points at once.", "item_res_05.png",
         price, vip_points=points, picture=f"vip_points_{n}.png")
    for n, (points, price) in enumerate([(100, 10), (500, 45), (1500, 120), (5000, 350), (15000, 900),
                                         (50000, 2500)], 1)
]
CATALOG += [
    Item(7100 + n, VIP_TIME, f"VIP activation ({days} {'day' if days == 1 else 'days'})",
         f"VIP benefits of your level for {days} {'day' if days == 1 else 'days'} more.", "item_res_09.png", price,
         vip_days=days)
    for n, (days, price) in enumerate([(1, 10), (7, 60), (30, 200)], 1)
]
# wiki (store screenshot): Gamma Detector (Small) 49 dark matter for 10 secure explorations,
# (Large) 398 credits for 20
CATALOG += [
    Item(GAMMA_DETECTOR_S, OTHER, "Gamma Detector (Small)", "Safely conducts 10 exploration missions beyond the "
         "day's secure explorations.", "item_res_13.png", 49, explorations=10, shortcut=SHORTCUT_EXPLORER,
         picture="gamma_detector.png"),
    Item(GAMMA_DETECTOR_L, OTHER, "Gamma Detector (Large)", "Safely conducts 20 exploration missions beyond the "
         "day's secure explorations.", "item_res_13.png", 398, pay_type=CREDITS, explorations=20,
         shortcut=SHORTCUT_EXPLORER, picture="gamma_detector.png"),
]
CATALOG += _specials()
CATALOG += _officers()
from . import devices  # noqa: E402 (devices builds its items with Item)

CATALOG += devices.catalog()
BY_ID = {i.id: i for i in CATALOG}


def contents(item_id: int) -> list[dict]:
    """What a gift pack holds, as the client's package windows list it ({name, amount, img})."""
    from . import economy as eco
    i = item(item_id)
    out = [{"name": res.capitalize(), "amount": n, "img": f"icons/item_res_0{icon}.png"}
           for res, n, icon in zip(planet_rules.RESOURCES, i.resources, (4, 3, 1)) if n]
    if i.ecm_chips:
        out.append({"name": "ECM chips", "amount": i.ecm_chips, "img": "icons/item_res_12.png"})
    out += [{"name": item(pid).name, "amount": n, "img": item(pid).image()} for pid, n in i.pack]
    out += [{"name": eco.SPECIAL_UNITS.get(t) or eco.UNITS[t].name, "amount": n,
             "img": next(x.image() for x in CATALOG if x.unit == t)} for t, n in i.units]
    out = out or [{"name": i.name, "amount": 1, "img": i.image()}]
    # the opening window shows only the name, so it carries the amount too
    return [dict(c, name=f"{c['amount']:,} {c['name']}") for c in out]


def item(item_id: int) -> Item:
    found = BY_ID.get(item_id)
    if found is None:
        raise ItemError("This item does not exist")
    return found


def _view(i: Item) -> dict:
    return {"id": i.id, "game_item_id": i.id, "name": i.name, "desc": i.desc, "img": i.image(),
            "pay_type": i.pay_type, "sale_price": i.price, "code": i.code, "item_type": i.item_type,
            "category": i.category,
            "is_featured": i.featured, "is_gift": bool(i.pack or i.units), "step_gacha": 0}


def vip_shop(category: int) -> list[dict]:
    return [_view(i) for i in CATALOG if i.category == category and i.sold]


PROMO_BACKGROUND = "NAN_promo_default.png"  # the banner picture the client has
NO_COUNTDOWN = -100  # the banner shows no time left


def banner() -> list[dict]:
    """The store's top banner (game_items/item_packages): the gift packs, the first one first.
    Tapping one shows its contents, buying it buys the pack."""
    packs = [i for i in CATALOG if i.sold and (i.pack or i.units)]
    return [dict(_view(i), count_down=NO_COUNTDOWN, package_priority=len(packs) - n,
                 package_background=PROMO_BACKGROUND) for n, i in enumerate(packs)]


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


def add(state: dict, item_id: int, n: int) -> None:
    """Put items into the capsule (rewards, drops)."""
    _add(state, item(item_id).id, n)


def depot_view(state: dict, category: int) -> list[dict]:
    def tab(i: Item) -> int:
        return OTHER if i.category == VIP_TIME else i.category  # VIP activation items: "Other"
    return [owned_view(state, i) for i in CATALOG
            if amount(state, i.id) and (tab(i) == category or (category == FEATURED and i.featured))]


def owned_view(state: dict, i: Item) -> dict:
    usable = i.category != SPEEDUP and i.device is None
    return dict(_view(i), amount=amount(state, i.id), can_be_use=usable)


def _pay(state: dict, price: int, pay_type: int = DARK_MATTER) -> None:
    empire = state["empire"]
    key = "dark_matter_count" if pay_type == DARK_MATTER else "gu_count"
    if empire.get(key, 0) < price:
        raise ItemError(f"You need {price:,} {CURRENCY[pay_type]}")
    empire[key] = empire.get(key, 0) - price


def buy(state: dict, item_id: int, n: int) -> dict:
    i = item(item_id)
    if not 1 <= n <= 999 or not i.sold:
        raise ItemError("Invalid number" if i.sold else "This item is not for sale")
    _pay(state, i.price * n, i.pay_type)
    empire = state["empire"]
    if i.vip_points:  # "Buy&Use": VIP points count at once; activation items go to the capsule
        from . import vip
        vip.add_points(state, i.vip_points * n)
        info = f"{i.name} x{n}: VIP{vip.level(vip.data(state)['score'])}, {vip.data(state)['score']:,} points."
    else:
        _add(state, i.id, n)
        info = f"{i.name} x{n} is in your capsule."
    return {"darkmatter": empire["dark_matter_count"], "credits": empire.get("gu_count", 0),
            "info": info, "id": i.id, "item_type": 1}


def use(state: dict, item_id: int, n: int, planet: dict, now: float | None = None,
        rng: random.Random | None = None, target: tuple[int, int, int] | None = None) -> str:
    i = item(item_id)
    if i.category == SPEEDUP:
        raise ItemError("Use speed-ups on a running construction, research or shipyard order")
    if i.device:
        raise ItemError("Install devices in the Modification screen")
    if not 1 <= n <= amount(state, i.id):
        raise ItemError("You do not have that many")
    if i.id == COMPONENT:
        return _build_reapers(state, n, planet)
    if i.special:
        info = _use_special(state, i, n, planet, time.time() if now is None else now, rng, target)
        _add(state, i.id, -n)
        return info
    if i.unit == 28:
        owned = sum(planet_rules.fleet(p, 28)["amount"] for p in planet_rules.bodies(state["empire"]))
        if owned + n > MAX_ANNIHILATORS:
            raise ItemError(f"A commander can have at most {MAX_ANNIHILATORS} Annihilators")
    if i.fields and planet_rules.is_moon(planet):
        raise ItemError("Terraformers do not work on moons")
    info = f"Used {i.name} x{n}."
    if i.officer:
        kind, lv, days = i.officer
        try:
            info = officers.hire(state["empire"], kind, lv, days * n, time.time() if now is None else now)
        except ValueError as e:
            raise ItemError(str(e)) from None
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
    if i.vip_days:
        from . import vip
        vip.activate(state, i.vip_days * n, time.time() if now is None else now)
        info = f"Used {i.name} x{n}: VIP benefits for {vip.info_view(state, time.time() if now is None else now)['vip_left'] // 3600} more hours."
    if i.explorations:
        state["empire"]["secure_explorations"] = state["empire"].get("secure_explorations", 0) + i.explorations * n
        info = (f"Used {i.name} x{n}: {state['empire']['secure_explorations']} secure explorations beyond the "
                "day's are ready.")
    for pack_item, number in i.pack:
        _add(state, pack_item, number * n)
    for unit, number in i.units:
        planet_rules.fleet(planet, unit)["amount"] += number * n
    if i.pack or i.units:
        info = f"Opened {i.name} x{n}: " + ", ".join(f"{c['amount']:,} {c['name']}" for c in contents(i.id)) + "."
    if i.fields:
        rng = rng or random.Random()
        best = max(rng.randint(1, i.fields) for _ in range(n))
        before = planet.get("extra_fields", 0)
        planet["extra_fields"] = max(before, best)
        info = (f"The terraformer found room for {best} fields; the planet now has {planet['extra_fields']} extra "
                "fields." if best > before else f"The terraformer found room for {best} fields; the planet keeps its "
                f"{before} extra fields.")
    return info


def _use_special(state: dict, i: Item, n: int, planet: dict, now: float, rng, target) -> str:
    """Planet and moon items (specials.py). Only reformers can be used several at once."""
    from . import specials
    kind, *args = i.special
    if kind == "reform":
        source, amount_, out_res, out = args
        return specials.reform(planet, source, amount_, out_res, out, n)
    if n != 1:
        raise ItemError(f"Use one {i.name} at a time")
    if kind == "projection":
        return specials.apply_projection(planet, *args, now)
    if kind == "peace":
        return specials.make_peace(state, planet, args[0], now)
    if kind == "shift":
        return specials.shift_planet(state["empire"]["user_id"], state, planet, args[0], now, target, rng)
    if kind == "navigator":
        return specials.add_navigator_runs(state, args[0])
    if kind == "lunar_fields":
        return specials.terraform_moon(planet, *args, rng)
    if kind == "moon":
        return specials.catalyse_moon(state["empire"]["user_id"], planet, args[0], rng)
    raise ItemError("This item cannot be used")


def _build_reapers(state: dict, n: int, planet: dict) -> str:
    reapers = n // REAPER_COMPONENTS
    if not reapers:
        raise ItemError(f"A Reaper takes {REAPER_COMPONENTS:,} intricate components")
    _add(state, COMPONENT, -reapers * REAPER_COMPONENTS)
    planet_rules.fleet(planet, 29)["amount"] += reapers
    return f"{reapers} Reaper(s) built from {reapers * REAPER_COMPONENTS:,} intricate components."


def components_for(enemy_lost: dict[int, int]) -> int:
    """Intricate components from large enemy ships destroyed in a battle: hull / 5,000 each."""
    from . import combat
    return sum(max(1, combat.STATS[t].hull // 5000) * n for t, n in enemy_lost.items()
               if t in combat.STATS and combat.STATS[t].hull >= 7000 and t in planet_rules.eco.SHIPS
               and t not in combat.MISSILES)


def add_components(state: dict, enemy_lost: dict[int, int]) -> int:
    found = components_for(enemy_lost)
    if found:
        _add(state, COMPONENT, found)
    return found


def galaxium_for(enemy_lost: dict[int, int]) -> int:
    """Galaxium from a battle: 1 per 20,000 metal and crystal the enemy's ships were worth."""
    from . import economy as eco
    return sum((eco.UNITS[t].metal + eco.UNITS[t].crystal) * n for t, n in enemy_lost.items()
               if t in eco.UNITS and t in eco.SHIPS) // 20_000


def fusion_cost(state: dict, n: int) -> dict:
    """Extra dark matter / credits for fusing `n` more interceptors (wiki: free up to 1,000 owned)."""
    owned = sum(planet_rules.fleet(p, 23)["amount"] for p in planet_rules.bodies(state["empire"]))
    paid = max(0, owned + n - max(owned, FREE_INTERCEPTORS))
    return {"dark_matter": paid, "gu": 2 * paid}


def fuse_interceptors(state: dict, n: int, pay_type: int, planet: dict) -> None:
    if n < 1:
        raise ItemError("Invalid number")
    if amount(state, GALAXIUM) < n * INTERCEPTOR_GALAXIUM:
        raise ItemError(f"{n} interceptor(s) take {n * INTERCEPTOR_GALAXIUM:,} galaxium")
    cost = fusion_cost(state, n)
    _pay(state, cost["dark_matter"] if pay_type == DARK_MATTER else cost["gu"], pay_type)
    _add(state, GALAXIUM, -n * INTERCEPTOR_GALAXIUM)
    planet_rules.fleet(planet, 23)["amount"] += n


def discard(state: dict, item_id: int) -> None:
    i = item(item_id)
    _add(state, i.id, -amount(state, i.id))


# --- Quick buy (the shortcut lists of resource settings, planet info, officers ...) -------------


def shortcut_view(state: dict, shortcut: int) -> list[dict]:
    return [owned_view(state, i) for i in CATALOG if i.shortcut == shortcut and i.sold]


def buy_and_use(state: dict, item_id: int, planet: dict, now: float,
                target: tuple[int, int, int] | None = None) -> str:
    i = item(item_id)
    if not i.sold:
        raise ItemError("This item is not for sale")
    _pay(state, i.price, i.pay_type)
    _add(state, i.id, 1)
    try:
        return use(state, i.id, 1, planet, now, target=target)
    except ItemError:
        _add(state, i.id, -1)
        key = "dark_matter_count" if i.pay_type == DARK_MATTER else "gu_count"
        state["empire"][key] += i.price
        raise


# --- Speed-ups -----------------------------------------------------------------------------------

BUILDING, SHIPYARD, RESEARCH, PIONEER = "building", "shipyard", "research", "pioneer"
QUICK_FINISH_SECONDS = 60  # the client offers a free finish for the last minute


def _find(state: dict, kind: str, event_id: int):
    """(event, planet) of a running order."""
    empire = state["empire"]
    if kind == RESEARCH:
        event = next((e for e in empire["research_upgrading_events"] if e["event_id"] == event_id), None)
        return event, None
    if kind == PIONEER:
        event = flagships.data(state)["research_event"]
        return (event if event and event["event_id"] == event_id else None), None
    key = "building_events" if kind == BUILDING else "fleet_building_events"
    for planet in planet_rules.bodies(empire):
        event = next((e for e in planet[key] if e["event_id"] == event_id), None)
        if event:
            return event, planet
    return None, None


def _finish_due(state: dict, now: float) -> None:
    planet_rules.tick(state["empire"], now)
    flagships.research_level(state, 0, now)  # finishes the pioneer research when it is due


def _running(state: dict, kind: str, event_id: int, now: float):
    _finish_due(state, now)
    event, planet = _find(state, kind, event_id)
    if event is None:
        raise ItemError("This order is already finished")
    if kind == SHIPYARD and event["start_time"] > now:
        raise ItemError("Only the order the shipyard is working on can be sped up")
    return event, planet


def instant_price(left_seconds: float) -> int:
    return max(1, math.ceil(left_seconds / SECONDS_PER_DARK_MATTER))


def speedup_info(state: dict, kind: str, event_id: int, now: float) -> dict:
    event, _ = _running(state, kind, event_id, now)
    left = max(0, int(event["finish_time"] - now))
    # this screen always draws a credit icon next to the price, so the price says what it is
    return {"items": [dict(owned_view(state, i), sale_price=f"{i.price} {'DM' if i.pay_type == DARK_MATTER else 'Cr'}")
                      for i in CATALOG if i.category == SPEEDUP and i.seconds],
            "left_time": left, "dark_matter_count": instant_price(left)}


def _shorten(state: dict, kind: str, event: dict, planet: dict | None, seconds: float, now: float) -> None:
    event["finish_time"] -= seconds
    if kind == SHIPYARD:
        event["start_time"] -= seconds
        planet_rules._reschedule_shipyard(planet, now)
    _finish_due(state, now)


def finish_now(state: dict, kind: str, event_id: int, now: float) -> None:
    """Finish an order without paying (the client's quick finish in the last minute)."""
    event, planet = _running(state, kind, event_id, now)
    if event["finish_time"] - now > QUICK_FINISH_SECONDS + 5:
        raise ItemError("Only the last minute can be skipped for free")
    _shorten(state, kind, event, planet, event["finish_time"] - now, now)


def speed_up(state: dict, kind: str, event_id: int, item_id: int, buy_first: bool, now: float) -> None:
    """Shorten an order with a speed-up item (bought first if `buy_first`), or finish it with
    dark matter when `item_id` is 0."""
    event, planet = _running(state, kind, event_id, now)
    left = event["finish_time"] - now
    if item_id == 0:
        _pay(state, instant_price(left))
        seconds = left
    else:
        i = item(item_id)
        if i.category != SPEEDUP or not i.seconds:
            raise ItemError("This is not a speed-up")
        if buy_first:
            _pay(state, i.price, i.pay_type)
        elif not amount(state, i.id):
            raise ItemError(f"You have no {i.name}")
        else:
            _add(state, i.id, -1)
        seconds = min(left, i.seconds)
    _shorten(state, kind, event, planet, seconds, now)
