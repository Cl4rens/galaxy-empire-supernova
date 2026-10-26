"""Space Store items that change a planet or the universe (galaxyempire.fandom.com/wiki/Space_Store,
.../wiki/Projections and the item pages linked from there):

- Projections change how a planet looks for a week and give it a bonus: Abyss construction
  speed, Black Hole flight speed of fleets starting there, Energy shields, Steel armour, Evil
  attack (level 2 about twice as much). A new projection replaces the old one.
- Peace Agreements keep attackers off a planet for 4 or 12 hours, once a day; launching an
  attack ends them.
- Planet Shifters move a planet (with its moon and buildings) to a random place in its galaxy,
  to a random galaxy, or to chosen coordinates (once in 48 hours).
- Lunar Catalysts may create a moon; Lunar Terraformers add fields to a moon.
- Reformers turn crystal or gas into metal.
- Nebula Navigators let an expedition fleet explore the nebula several times in a row.

Prices and numbers are the wiki's; where it gives none, Supernova's own (see items.py).
"""

import random

from . import db, universe
from . import planet as planet_rules

WEEK, DAY, HOUR = 7 * 86400, 86400, 3600


class SpecialError(planet_rules.GameError):
    pass


# --- Projections ---------------------------------------------------------------------------

# kind -> (name, bonus key, bonus per level 1-2, the solar system's planet picture per level: its frame
# label planet_Icon_N)
PROJECTIONS = {
    "abyss": ("Abyss Projection", "build", (10, 20), (26, 31)),
    "black_hole": ("Black Hole Projection", "flight_speed", (10, 20), (25, 30)),
    "energy": ("Energy Projection", "shield", (15, 30), (24, 29)),
    "steel": ("Steel Projection", "armour", (10, 21), (23, 28)),
    "evil": ("Evil Projection", "attack", (10, 20), (22, 27)),
}
BONUS_TEXT = {"build": "construction speed", "flight_speed": "flight speed of fleets starting here",
              "shield": "shields", "armour": "armour", "attack": "attack power"}


def projection_desc(kind: str, level: int) -> str:
    name, key, bonus, _ = PROJECTIONS[kind]
    return (f"Makes the planet look different and gives +{bonus[level - 1]}% {BONUS_TEXT[key]} for a week. "
            "Replaces the planet's projection.")


def apply_projection(planet: dict, kind: str, level: int, now: float) -> str:
    name, key, bonus, frames = PROJECTIONS[kind]
    if planet_rules.is_moon(planet):
        raise SpecialError("Projections are for planets, not moons")
    planet["projection"] = {"kind": kind, "level": level, "until": int(now + WEEK)}
    planet["planet_icon"] = frames[level - 1]
    planet["planet_icon_end_time"] = int(now + WEEK)
    planet["planet_icon_info"] = {"name": f"{name} Lv.{level}", "desc": f"+{bonus[level - 1]}% {BONUS_TEXT[key]}"}
    return f"{name} Lv.{level} covers {planet['planet_name']} for a week."


def expire(planet: dict, now: float) -> None:
    """Take away what has run out (called by planet.tick)."""
    p = planet.get("projection")
    if p and p["until"] <= now:
        planet.pop("projection")
        planet.update(planet_icon=0, planet_icon_end_time=0, planet_icon_info={})


def projection_bonus(planet: dict | None, key: str, now: float | None = None) -> int:
    """The planet's projection bonus (percent) of the kind `key`."""
    p = (planet or {}).get("projection")
    if not p or (now is not None and p["until"] <= now):
        return 0
    _, bonus_key, bonus, _ = PROJECTIONS[p["kind"]]
    return bonus[p["level"] - 1] if bonus_key == key else 0


# --- Peace agreements ----------------------------------------------------------------------


def make_peace(state: dict, planet: dict, hours: int, now: float) -> str:
    empire = state["empire"]
    day = int(now // DAY)
    if empire.get("peace_day") == day:
        raise SpecialError("A peace agreement can be signed once a day")
    if planet.get("peace_until", 0) > now:
        raise SpecialError("This planet already has a peace agreement")
    empire["peace_day"] = day
    planet["peace_until"] = int(now + hours * HOUR)
    return f"{planet['planet_name']} is safe from attacks for {hours} hours, unless you attack yourself."


def at_peace(planet: dict | None, now: float) -> bool:
    return bool(planet) and planet.get("peace_until", 0) > now


def end_peace(empire: dict) -> bool:
    """An attack ends the attacker's peace agreements. Returns whether there was one."""
    ended = False
    for p in planet_rules.bodies(empire):
        if p.get("peace_until"):
            p["peace_until"] = 0
            ended = True
    return ended


def peace_left(planet: dict, now: float) -> int:
    return max(0, int(planet.get("peace_until", 0) - now))


# --- Planet shifters -----------------------------------------------------------------------

SHIFT_RANDOM, SHIFT_GALAXY, SHIFT_CHOSEN = "random", "galaxy", "chosen"
CHOSEN_SHIFT_SECONDS = 48 * HOUR


def _free(galaxy: int, system: int, position: int) -> bool:
    return db.connect().execute("SELECT 1 FROM planets WHERE galaxy = ? AND system = ? AND position = ? AND kind = 0",
                                (galaxy, system, position)).fetchone() is None


def shift_planet(player_id: int, state: dict, planet: dict, kind: str, now: float,
                 target: tuple[int, int, int] | None = None, rng: random.Random | None = None) -> str:
    from . import fleets
    rng = rng or random.Random()
    empire = state["empire"]
    if planet_rules.is_moon(planet):
        raise SpecialError("Move the planet; its moon goes with it")
    moon = planet.get("moon")
    if fleets.planet_has_flights(planet["planet_id"]) or (moon and fleets.planet_has_flights(moon["planet_id"])):
        raise SpecialError("Fleets are on their way from or to this planet; wait until they have landed")
    if kind == SHIFT_CHOSEN:
        if now - empire.get("chosen_shift_time", 0) < CHOSEN_SHIFT_SECONDS:
            raise SpecialError("A directional shift is possible once in 48 hours")
        if not target or not universe.valid_system(target[0], target[1]) \
                or not 1 <= target[2] <= universe.PLANET_POSITIONS:
            raise SpecialError("Choose a free planet position")
        if not _free(*target):
            raise SpecialError("This position is taken")
    else:
        galaxy = rng.randint(1, universe.GALAXIES) if kind == SHIFT_GALAXY else planet["galaxy_position"]
        for _ in range(1000):
            target = (galaxy, rng.randint(1, universe.SYSTEMS_PER_GALAXY), rng.randint(1, universe.PLANET_POSITIONS))
            if _free(*target):
                break
        else:
            raise SpecialError("No free place found")
    old = (planet["galaxy_position"], planet["system_position"], planet["planet_position"])
    conn = db.connect()
    with db.lock(), conn:
        conn.execute("UPDATE planets SET galaxy = ?, system = ?, position = ? WHERE galaxy = ? AND system = ? "
                     "AND position = ? AND player_id = ?", (*target, *old, player_id))
    for body in [planet] + ([moon] if moon else []):
        body.update(galaxy_position=target[0], system_position=target[1], planet_position=target[2])
    if kind == SHIFT_CHOSEN:
        empire["chosen_shift_time"] = int(now)
    return f"{planet['planet_name']} moved to {target[0]}:{target[1]}:{target[2]}."


# --- Moons ---------------------------------------------------------------------------------


def catalyse_moon(player_id: int, planet: dict, chance: float, rng: random.Random | None = None) -> str:
    from . import player
    if planet_rules.is_moon(planet):
        raise SpecialError("Use the catalyst on a planet")
    if planet.get("moon"):
        raise SpecialError("This planet already has a moon")
    if (rng or random.Random()).random() >= chance:
        return "The catalyst flared, but no moon formed this time."
    player.add_moon(player_id, planet)
    return f"A moon formed around {planet['planet_name']}!"


def terraform_moon(moon: dict, low: int, high: int, rng: random.Random | None = None) -> str:
    if not planet_rules.is_moon(moon):
        raise SpecialError("Lunar terraformers only work on moons")
    best = (rng or random.Random()).randint(low, high)
    before = moon.get("extra_fields", 0)
    moon["extra_fields"] = max(before, best)
    return (f"The lunar terraformer found room for {best} fields; the moon now has {moon['extra_fields']} extra "
            "fields." if best > before else f"The lunar terraformer found room for {best} fields; the moon keeps "
            f"its {before} extra fields.")


# --- Reformers -----------------------------------------------------------------------------


def reform(planet: dict, source: str, amount: int, target: str, out: int, n: int) -> str:
    if planet[source] < amount * n:
        raise SpecialError(f"The planet needs {amount * n:,} {RESOURCE_NAMES[source]} for this")
    planet[source] -= amount * n
    planet[target] += out * n
    return f"{amount * n:,} {RESOURCE_NAMES[source]} became {out * n:,} {RESOURCE_NAMES[target]}."


RESOURCE_NAMES = {"metal": "metal", "crystal": "crystal", "deuterium": "gas"}


# --- Nebula navigators ---------------------------------------------------------------------


def add_navigator_runs(state: dict, runs: int) -> str:
    nav = state["empire"].setdefault("navigator", {"runs": 0, "fleets": {}})
    nav["runs"] += runs
    return f"Your next expedition fleet explores the nebula up to {nav['runs']} times in a row."


def navigator_start(empire: dict, event_id: int, ships: dict[int, int]) -> None:
    """An expedition fleet leaves: with navigator runs it keeps exploring (see navigator_next)."""
    nav = empire.get("navigator")
    if nav and nav["runs"] > 0:
        nav["runs"] -= 1
        nav["fleets"][str(event_id)] = {str(t): n for t, n in ships.items()}


def navigator_next(empire: dict, event_id: int, origin: dict | None, ships: dict[int, int],
                   explorations_left: bool) -> dict[int, int] | None:
    """After a navigated run: the fleet for the next one (losses made up from `origin`), or None
    when the runs, the secure explorations or the ships are used up."""
    nav = empire.get("navigator") or {}
    sent = nav.get("fleets", {}).pop(str(event_id), None)
    if sent is None or nav["runs"] <= 0 or not explorations_left:
        return None
    ships = dict(ships)
    for t, n in ((int(t), n) for t, n in sent.items()):
        missing = n - ships.get(t, 0)
        if missing > 0 and origin:
            have = planet_rules.fleet(origin, t)
            refill = min(missing, have["amount"])
            have["amount"] -= refill
            ships[t] = ships.get(t, 0) + refill
    ships = {t: n for t, n in ships.items() if n}
    if not ships:
        return None
    nav["runs"] -= 1
    nav["fleets"][str(event_id)] = sent
    return ships


# --- What other players see ----------------------------------------------------------------


def public_view(player_id: int, planet_id: int, now: float) -> dict:
    """A planet's projection frame and peace agreement for the solar system view."""
    planet = planet_rules.find_planet(db.load_state(player_id)["empire"], planet_id)
    p = (planet or {}).get("projection")
    return {"planet_icon": PROJECTIONS[p["kind"]][3][p["level"] - 1] if p and p["until"] > now else 0,
            "is_protected": int(at_peace(planet, now))}


def target_at_peace(planet_id: int | None, now: float) -> bool:
    from . import fleets
    owner = fleets._planet_owner(planet_id)
    if owner is None:
        return False
    return at_peace(planet_rules.find_planet(db.load_state(owner)["empire"], planet_id), now)
