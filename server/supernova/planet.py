"""Planet simulation: resource production over time, building upgrades and their timers.

The stored planet only holds base data (levels, resources, running events, last update time).
Everything the client derives from it (costs, production, energy, storage limits) is computed
in `client_view` so it always matches the current rules.
"""

import time

from . import config, economy as eco

FREE_LOTS = range(4, 16)  # lots 1-3 belong to the mines
EVENT_UPGRADE, EVENT_TEARDOWN = 1, 2
FLEET_CATEGORY_SHIP, FLEET_CATEGORY_DEFENSE = 1, 2
PLANET_TYPE_MOON = 3
MOON_FIELDS_PER_BASE_LEVEL = 3
RESOURCES = ("metal", "crystal", "deuterium")


class GameError(Exception):
    """A request the rules do not allow. The message is shown to the player."""


# --- Reading the planet -------------------------------------------------------------------


def building(planet: dict, btype: int) -> dict:
    return next(b for b in planet["buildings"] if b["building_type"] == btype)


def level(planet: dict, btype: int) -> int:
    return building(planet, btype)["building_level"]


def fleet(planet: dict, ftype: int) -> dict:
    return next(f for f in planet["fleets"] if f["fleet_type"] == ftype)


def add_missing_fleets(planet: dict) -> None:
    """Planets created before a unit type existed get an entry for it."""
    have = {f["fleet_type"] for f in planet["fleets"]}
    for ftype in [*eco.UNITS, *eco.SPECIAL_UNITS]:
        if ftype not in have:
            planet["fleets"].append({"fleet_id": planet["planet_id"] * 100 + ftype, "fleet_type": ftype, "amount": 0})


def is_moon(planet: dict) -> bool:
    return planet.get("planet_type") == PLANET_TYPE_MOON


def bodies(empire: dict) -> list[dict]:
    """All planets and moons of the empire. Moons are stored in their planet's "moon"."""
    return empire["planets"] + [p["moon"] for p in empire["planets"] if p.get("moon")]


def find_planet(empire: dict, planet_id: int) -> dict | None:
    return next((p for p in bodies(empire) if p["planet_id"] == planet_id), None)


def robotics_levels(planet: dict) -> tuple[int, int]:
    """(robotics factory, nanite factory) levels that speed up constructions here."""
    if is_moon(planet):
        return level(planet, eco.MOON_ROBOTICS_FACTORY), 0
    return level(planet, eco.ROBOTICS_FACTORY), level(planet, eco.NANITE_FACTORY)


def shipyard_type(planet: dict) -> int:
    return eco.MOON_SHIPYARD if is_moon(planet) else eco.SHIPYARD


def _requirement_level(planet: dict, btype: int) -> int:
    if is_moon(planet):
        btype = {eco.SHIPYARD: eco.MOON_SHIPYARD, eco.ROBOTICS_FACTORY: eco.MOON_ROBOTICS_FACTORY}.get(btype, btype)
    return level(planet, btype)


def fields(planet: dict) -> tuple[int, int]:
    """(used, available) building fields."""
    used = sum(b["building_level"] for b in planet["buildings"])
    if is_moon(planet):
        # lunar terraformers add fields
        return used, 1 + MOON_FIELDS_PER_BASE_LEVEL * level(planet, eco.MOON_BASE) + planet.get("extra_fields", 0)
    # colonies have their own size, home planets the wiki's 178; terraformers add fields
    return used, planet.get("max_fields", eco.HOME_FIELDS) + planet.get("extra_fields", 0)


def temperatures(planet: dict) -> tuple[int, int]:
    """(minimum, maximum) temperature; it changes when a planet shifter moves the planet."""
    return eco.temperatures(planet["planet_position"], planet.get("temp_offset", 0))


PERCENT_FIELDS = ("metal_mine_percent", "crystal_mine_percent", "deuterium_mine_percent", "solar_plant_percent",
                  "fusion_reactor_percent")


# Radar Facility (wiki): the level that detects each ship type; everything bigger needs 14.
RADAR_DETECTION = {1: 1, 13: 2, 11: 4, 9: 5, 2: 7, 12: 8, 10: 10, 3: 11, 4: 12, 5: 13}
RADAR_TOP_LEVEL = 14


def radar_fleets(planet: dict) -> list[str]:
    """Names of the ship types the radar center detects (the radar screen lists them)."""
    lv = level(planet, eco.RADAR_CENTER)
    names = [eco.UNITS[t].name for t, need in sorted(RADAR_DETECTION.items(), key=lambda x: x[1]) if lv >= need]
    if lv >= RADAR_TOP_LEVEL:
        names.append("All other ships")
    return names


RADAR_ENERGY_PER_LEVEL = 10


def radar_energy(planet: dict) -> int:
    """Energy a switched-on radar center needs (Supernova: 10 per level; the wiki only says it
    costs energy)."""
    lv = level(planet, eco.RADAR_CENTER)
    return RADAR_ENERGY_PER_LEVEL * lv if lv and planet.get("radar_on", True) else 0


def _faster(planet: dict, key: str, seconds: int) -> int:
    """Officers shorten timers by a percentage (planet["officer"], kept up to date by tick);
    Laser and Ion Technology make constructions and the shipyard more efficient."""
    boost = planet.get("officer", {})
    efficiency = 1 + boost.get("construction", 0) / 100 if key in ("build", "ships", "defense") else 1
    return max(1, int(seconds * (100 - boost.get(key, 0)) / 100 / efficiency))


def sensor_range(moon: dict) -> int:
    """Systems a sensor array sees on each side (OGame: level² - 1)."""
    lv = level(moon, eco.SENSOR_ARRAY)
    return lv * lv - 1 if lv else -1


def buildable_here(planet: dict, btype: int) -> bool:
    return (btype in eco.MOON_BUILDINGS) == is_moon(planet)


def switch_planet(empire: dict, planet_id: int) -> dict:
    """Make `planet_id` the planet the player is looking at (empire["main_planet"])."""
    planet = find_planet(empire, planet_id)
    if planet is None:
        raise GameError("This is not your planet")
    empire["current_planet_id"] = planet_id
    empire["main_planet"] = planet
    return planet


def research_level(empire: dict, rtype: int) -> int:
    return next((r["research_level"] for r in empire["researches"] if r["research_type"] == rtype), 0)


def production(planet: dict, empire: dict) -> dict:
    """Hourly production and energy balance, with game speed applied."""
    if is_moon(planet):  # moons have no mines and no energy
        return dict.fromkeys(("metal", "crystal", "deuterium", "solar_energy", "fusion_energy",
                              "satellite_energy", "energy_max", "energy_used"), 0)
    pct = {
        eco.METAL_MINE: planet["metal_mine_percent"] / 100,
        eco.CRYSTAL_MINE: planet["crystal_mine_percent"] / 100,
        eco.DEUTERIUM_MINE: planet["deuterium_mine_percent"] / 100,
    }
    solar = eco.solar_energy(level(planet, eco.SOLAR_PLANT)) * planet["solar_plant_percent"] / 100
    temp_max = temperatures(planet)[1]
    satellites = fleet(planet, eco.SOLAR_SATELLITE)["amount"] * eco.satellite_energy(temp_max)
    fusion_level = level(planet, eco.FUSION_REACTOR)
    fusion_pct = planet["fusion_reactor_percent"] / 100
    officer = planet.get("officer", {})
    fusion = eco.fusion_energy(fusion_level, research_level(empire, eco.ENERGY_TECH)) * fusion_pct
    fusion *= 1 + officer.get("fusion", 0) / 100
    produced = solar + fusion + satellites
    used = sum(eco.mine_energy(m, level(planet, m)) * pct[m] for m in eco.MINES) + radar_energy(planet)
    factor = min(1.0, produced / used) if used > 0 else 1.0

    from . import lordship  # lordship reads the rankings, which import this module
    lord = lordship.bonus(planet, empire.get("user_id", 0)) if empire.get("user_id") else {}
    from . import fortresses
    fortress = {res: fortresses.production_bonus(empire, res) for res in RESOURCES}

    def mine(btype: int, resource: str) -> float:
        krypton = 1 + (planet.get("krypton_bonus", {}).get(resource, 0) + lord.get(f"increase_{resource}", 0) + fortress[resource]
                       + officer.get("mines", 0)) / 100
        climate = eco.gas_factor(temp_max) if btype == eco.DEUTERIUM_MINE else 1
        return (eco.BASE_PRODUCTION[resource]
                + eco.mine_output(btype, level(planet, btype)) * pct[btype] * factor * krypton * climate)

    return {
        "metal": mine(eco.METAL_MINE, "metal") * config.SPEED,
        "crystal": mine(eco.CRYSTAL_MINE, "crystal") * config.SPEED,
        "deuterium": (mine(eco.DEUTERIUM_MINE, "deuterium") - eco.fusion_deuterium(fusion_level) * fusion_pct)
        * config.SPEED,
        "solar_energy": solar,
        "fusion_energy": fusion,
        "satellite_energy": satellites,
        "energy_max": produced,
        "energy_used": used,
    }


def capacity(planet: dict, resource: str) -> int:
    return eco.storage_capacity(level(planet, eco.STORAGE_FOR[resource]))


# --- Time ---------------------------------------------------------------------------------


def _accrue(planet: dict, empire: dict, seconds: float) -> None:
    if seconds <= 0:
        return
    rates = production(planet, empire)
    for res in RESOURCES:
        cap = capacity(planet, res)
        amount = planet[res]
        if rates[res] > 0 and amount < cap:
            planet[res] = min(cap, amount + rates[res] * seconds / 3600)
        elif rates[res] < 0:
            planet[res] = max(0.0, amount + rates[res] * seconds / 3600)


def _finish_event(planet: dict, event: dict) -> None:
    b = next(b for b in planet["buildings"] if b["building_id"] == event["building_id"])
    if event["event_type"] == EVENT_UPGRADE:
        b["building_level"] += 1
    else:
        b["building_level"] -= 1
        if b["building_level"] == 0 and b["building_type"] not in eco.MINES:
            b["building_position_index"] = 0  # the lot is free again


def _finish_research(empire: dict, event: dict) -> None:
    research = next(r for r in empire["researches"] if r["research_id"] == event["research_id"])
    research["research_level"] += 1


def _deliver_fleets(planet: dict, now: float) -> None:
    """Hand over every ship/defense unit of the shipyard queue finished by `now`."""
    for order in list(planet["fleet_building_events"]):
        if order["start_time"] > now:
            continue  # later orders may run side by side with this one (VIP)
        done = min(order["total_count"], int((now - order["start_time"]) // order["unit_seconds"]))
        fleet(planet, order["fleet_type"])["amount"] += done - order["delivered"]
        order["delivered"] = done
        if done < order["total_count"]:
            continue
        planet["fleet_building_events"].remove(order)


def tick(empire: dict, now: float | None = None) -> None:
    """Bring every planet up to `now`: production, constructions, research and shipyard."""
    now = time.time() if now is None else now
    _dark_matter_income(empire, now)
    from . import officers, vip  # small, but keep this module free of shop rules
    boost = officers.boosts(empire, now)
    for key, benefit in (("build", "build"), ("research", "research"), ("mines", "production"),
                         ("ships", "shipyard")):
        boost[key] = boost.get(key, 0) + vip.benefit(empire, benefit, now)
    boost["construction"] = eco.CONSTRUCTION_EFFICIENCY * (research_level(empire, eco.LASER_TECH)
                                                           + research_level(empire, eco.ION_TECH))
    boost["pioneer_cost"] = eco.PIONEER_COST_CUT * research_level(empire, eco.ULTRA_TEMPERATURE_POLYMERIZER)
    boost["pioneer_time"] = eco.PIONEER_TIME_CUT * research_level(empire, eco.ANTI_GRAVITY)
    from . import specials
    for planet in bodies(empire):
        add_missing_fleets(planet)
        specials.expire(planet, now)
        abyss = specials.projection_bonus(planet, "build", now)  # an Abyss Projection
        planet["officer"] = dict(boost, build=boost.get("build", 0) + abyss) if abyss else boost
        _tick_planet(empire, planet, now)
        planet["transmit_recharge_event"] = [e for e in planet.get("transmit_recharge_event", [])
                                             if e["finish_time"] > now]


TIMER_GRACE = 3  # seconds a client's timer may run ahead of the server's clock


def tick_for_timer(empire: dict, now: float) -> None:
    """tick, when a client's timer ran out: a construction or research ending within a few
    seconds is finished now (a client that asks a moment too early would otherwise show it
    stuck at 1 second, as it does not ask again)."""
    for planet in bodies(empire):
        for event in planet["building_events"]:
            if event["finish_time"] - now <= TIMER_GRACE:
                event["finish_time"] = min(event["finish_time"], now)
    for event in empire["research_upgrading_events"]:
        if event["finish_time"] - now <= TIMER_GRACE:
            event["finish_time"] = min(event["finish_time"], now)
    tick(empire, now)


def _dark_matter_income(empire: dict, now: float) -> None:
    """config.DARK_MATTER_PER_DAY, paid out in whole units as time passes."""
    income = empire.setdefault("dark_matter_income", {"time": now, "fraction": 0.0})
    from . import vip
    per_day = config.DARK_MATTER_PER_DAY * (1 + vip.benefit(empire, "dark_matter", now) / 100)
    earned = income["fraction"] + per_day * max(0.0, now - income["time"]) / 86400
    income["time"] = now
    whole = int(earned)
    income["fraction"] = earned - whole
    if whole:
        empire["dark_matter_count"] = empire.get("dark_matter_count", 0) + whole


def _research_planet(empire: dict, event: dict) -> dict:
    return find_planet(empire, event.get("planet_id")) or empire["planets"][0]


def _tick_planet(empire: dict, planet: dict, now: float) -> None:
    """Events are applied in time order, with production calculated in between, because they
    change production (mines, energy technology, solar satellites). Research belongs to the
    planet whose lab runs it."""
    t = planet["last_update"]

    def done(events, event, finish, target):
        def apply(at):
            finish(target, event)
            events.remove(event)
        return apply

    queue = [(e["finish_time"], done(planet["building_events"], e, _finish_event, planet))
             for e in planet["building_events"]]
    queue += [(e["finish_time"], done(empire["research_upgrading_events"], e, _finish_research, empire))
              for e in empire["research_upgrading_events"] if _research_planet(empire, e) is planet]
    queue += [(e["finish_time"], lambda at: _deliver_fleets(planet, at)) for e in planet["fleet_building_events"]]
    for finish_time, apply in sorted(queue, key=lambda q: q[0]):
        if finish_time > now:
            break
        _accrue(planet, empire, finish_time - t)
        t = max(t, finish_time)
        apply(finish_time)
    _accrue(planet, empire, now - t)
    _deliver_fleets(planet, now)
    planet["last_update"] = max(t, now)


# --- Actions ------------------------------------------------------------------------------


def _requirements_met(planet: dict, empire: dict, btype: int, rules=eco.BUILDINGS) -> bool:
    rule = rules[btype]
    return all(_requirement_level(planet, t) >= lv for t, lv in rule.buildings_required.items()) and all(
        research_level(empire, t) >= lv for t, lv in rule.researches_required.items()
    )


def _pay(planet: dict, cost: tuple[int, int, int]) -> None:
    if any(planet[res] < amount for res, amount in zip(RESOURCES, cost)):
        raise GameError("Not enough resources")
    for res, amount in zip(RESOURCES, cost):
        planet[res] -= amount


def start_building_event(empire: dict, state: dict, building_id: int, event_type: int,
                         lot: int | None, now: float) -> dict:
    """Start upgrading or tearing down a building. Returns the changed building."""
    planet = empire["main_planet"]
    tick(empire, now)
    from . import vip
    from . import officers
    if len(planet["building_events"]) >= 1 + vip.benefit(empire, "build_queue", now) + officers.build_queue(empire, now):
        raise GameError("Another building is already under construction")
    if any(e["building_id"] == building_id for e in planet["building_events"]):
        raise GameError("This building is already under construction")
    if empire["research_upgrading_events"] and building_id == building(planet, eco.RESEARCH_LAB)["building_id"]:
        raise GameError("The research lab cannot be upgraded while research is running")
    if planet["fleet_building_events"] and building_id in (building(planet, shipyard_type(planet))["building_id"],
                                                           building(planet, eco.NANITE_FACTORY)["building_id"]):
        raise GameError("This building cannot be upgraded while the shipyard is working")
    b = next((b for b in planet["buildings"] if b["building_id"] == building_id), None)
    if b is None:
        raise GameError("Unknown building")
    btype, current = b["building_type"], b["building_level"]
    fixed_lot = btype in eco.MINES or is_moon(planet)  # moon buildings have their own places

    if event_type == EVENT_UPGRADE:
        if not buildable_here(planet, btype):
            raise GameError("This building cannot be built here")
        if btype == eco.PIONEER_LAB and current == 0 and any(
                p is not planet and (level(p, btype) or any(e["building_id"] == building(p, btype)["building_id"]
                                                            for e in p["building_events"]))
                for p in bodies(empire)):
            raise GameError("An empire can have only one Pioneer Lab")
        if not _requirements_met(planet, empire, btype):
            raise GameError("Requirements not met")
        used, available = fields(planet)
        if used >= available and btype != eco.MOON_BASE:
            raise GameError("No free fields left. Upgrade the moon base." if is_moon(planet) else "No free fields left")
        if current == 0 and not fixed_lot:
            taken = {x["building_position_index"] for x in planet["buildings"]}
            if lot not in FREE_LOTS or lot in taken:
                raise GameError("This building lot is not free")
        cost, seconds = _upgrade(planet, btype, current)
    elif event_type == EVENT_TEARDOWN:
        if current == 0:
            raise GameError("Nothing to tear down")
        cost = eco.teardown_cost(btype, current)
        seconds = _faster(planet, "build", eco.build_seconds(cost[0], cost[1], *robotics_levels(planet)))
    else:
        raise GameError("Unknown event type")

    _pay(planet, cost)
    if event_type == EVENT_UPGRADE and current == 0 and not fixed_lot:
        b["building_position_index"] = lot
    planet["building_events"].append({
        "event_id": _next_event_id(state),
        "building_id": building_id,
        "building_type": btype,
        "event_type": event_type,
        "cost": list(cost),
        "start_time": int(now),
        "finish_time": int(now) + seconds,
        "total_time": seconds,
    })
    return b


def cancel_building_event(empire: dict, event_id: int, now: float) -> dict:
    """Cancel a running event and refund its cost. Returns the building."""
    planet = empire["main_planet"]
    tick(empire, now)
    event = next((e for e in planet["building_events"] if e["event_id"] == event_id), None)
    if event is None:
        raise GameError("This construction has already finished")
    planet["building_events"].remove(event)
    for res, amount in zip(RESOURCES, event["cost"]):
        planet[res] += amount
    b = next(b for b in planet["buildings"] if b["building_id"] == event["building_id"])
    if b["building_level"] == 0 and b["building_type"] not in eco.MINES:
        b["building_position_index"] = 0
    return b


def _next_event_id(state: dict) -> int:
    state["next_event_id"] = state.get("next_event_id", 0) + 1
    return state["next_event_id"]


def start_research(empire: dict, state: dict, research_id: int, now: float) -> dict:
    """Start researching the next level. Returns the research."""
    planet = empire["main_planet"]
    tick(empire, now)
    research = next((r for r in empire["researches"] if r["research_id"] == research_id), None)
    if research is None or research["research_type"] not in eco.RESEARCHES:
        raise GameError("Unknown research")
    from . import vip
    if len(empire["research_upgrading_events"]) >= 1 + vip.benefit(empire, "research_queue", now):
        raise GameError("Another research is already running")
    if any(e.get("research_id") == research_id or e.get("research_type") == research["research_type"]
           for e in empire["research_upgrading_events"]):
        raise GameError("This research is already running")
    lab = building(planet, eco.RESEARCH_LAB)
    if any(e["building_id"] == lab["building_id"] for e in planet["building_events"]):
        raise GameError("The research lab is being upgraded")
    rtype = research["research_type"]
    if not _requirements_met(planet, empire, rtype, eco.RESEARCHES):
        raise GameError("Requirements not met")
    cost = eco.research_cost(rtype, research["research_level"])
    seconds = _faster(planet, "research", eco.research_seconds(cost[0], cost[1], lab["building_level"]))
    _pay(planet, cost)
    empire["research_upgrading_events"].append({
        "event_id": _next_event_id(state),
        "research_id": research_id,
        "research_type": rtype,
        "planet_id": planet["planet_id"],
        "cost": list(cost),
        "start_time": int(now),
        "finish_time": int(now) + seconds,
        "total_time": seconds,
    })
    return research


def cancel_research(empire: dict, event_id: int, now: float) -> dict:
    """Cancel running research and refund its cost. Returns the research."""
    planet = empire["main_planet"]
    tick(empire, now)
    event = next((e for e in empire["research_upgrading_events"] if e["event_id"] == event_id), None)
    if event is None:
        raise GameError("This research has already finished")
    empire["research_upgrading_events"].remove(event)
    for res, amount in zip(RESOURCES, event["cost"]):
        planet[res] += amount
    return next(r for r in empire["researches"] if r["research_id"] == event["research_id"])


def _reschedule_shipyard(planet: dict, now: float) -> None:
    """Let the shipyard queue run back to back again, e.g. after an order was cancelled."""
    previous_end = None
    for order in planet["fleet_building_events"]:
        running = order["start_time"] <= now and previous_end is None
        if not running:
            order["start_time"] = int(max(now, previous_end or now))
        order["finish_time"] = order["start_time"] + order["total_count"] * order["unit_seconds"]
        previous_end = order["finish_time"]


def start_fleet_order(empire: dict, state: dict, fleet_id: int, count: int, now: float) -> dict:
    """Queue `count` ships or defense units in the shipyard. Returns the fleet entry."""
    planet = empire["main_planet"]
    tick(empire, now)
    f = next((f for f in planet["fleets"] if f["fleet_id"] == fleet_id), None)
    if f is None or f["fleet_type"] not in eco.UNITS:
        raise GameError("This unit cannot be built in the shipyard")
    if count < 1:
        raise GameError("Nothing to build")
    ftype = f["fleet_type"]
    if level(planet, shipyard_type(planet)) < 1:
        raise GameError("You need a shipyard")
    busy = {building(planet, shipyard_type(planet))["building_id"],
            building(planet, eco.NANITE_FACTORY)["building_id"]}
    if any(e["building_id"] in busy for e in planet["building_events"]):
        raise GameError("The shipyard is being upgraded")
    if not _requirements_met(planet, empire, ftype, eco.UNITS):
        raise GameError("Requirements not met")
    if ftype in eco.UNIQUE_UNITS:
        queued = sum(o["total_count"] - o["delivered"] for o in planet["fleet_building_events"]
                     if o["fleet_type"] == ftype)
        if f["amount"] + queued + count > 1:
            raise GameError("Only one of these can be built")
    if ftype in (30, 31):  # missiles: a planet holds as many as one battle can use
        from . import combat
        tech = research_level(empire, eco.MISSILE_TECH)
        limit = combat.intercept_limit(tech) if ftype == 30 else combat.interplanetary_limit(tech)
        queued = sum(o["total_count"] - o["delivered"] for o in planet["fleet_building_events"]
                     if o["fleet_type"] == ftype)
        if f["amount"] + queued + count > limit:
            raise GameError(f"This planet can hold at most {limit} of these; research Combined Guidance Tech")
    unit_cost = eco.unit_cost(ftype)
    _pay(planet, tuple(c * count for c in unit_cost))
    seconds = eco.unit_seconds(ftype, level(planet, shipyard_type(planet)), level(planet, eco.NANITE_FACTORY))
    if ftype not in eco.SHIPS:
        seconds = _faster(planet, "defense", seconds)
    else:
        seconds = _faster(planet, "ships", seconds)
    # orders run one after another; a VIP ship building queue runs that many more side by side
    from . import vip
    lines = 1 + vip.benefit(empire, "shipyard_queue", now)
    ends = sorted(o["finish_time"] for o in planet["fleet_building_events"])
    queue_end = ends[-lines] if len(ends) >= lines else now
    start = int(max(now, queue_end))
    planet["fleet_building_events"].append({
        "event_id": _next_event_id(state),
        "fleet_id": fleet_id,
        "fleet_type": ftype,
        "total_count": count,
        "delivered": 0,
        "unit_seconds": seconds,
        "unit_cost": list(unit_cost),
        "start_time": start,
        "finish_time": start + count * seconds,
        "total_time": count * seconds,
    })
    return f


def cancel_fleet_order(empire: dict, event_id: int, now: float) -> dict:
    """Cancel a shipyard order; units not built yet are refunded. Returns the fleet entry."""
    planet = empire["main_planet"]
    tick(empire, now)
    order = next((o for o in planet["fleet_building_events"] if o["event_id"] == event_id), None)
    if order is None:
        raise GameError("This order has already finished")
    planet["fleet_building_events"].remove(order)
    remaining = order["total_count"] - order["delivered"]
    for res, amount in zip(RESOURCES, order["unit_cost"]):
        planet[res] += amount * remaining
    _reschedule_shipyard(planet, now)
    return fleet(planet, order["fleet_type"])


# --- What the client sees -----------------------------------------------------------------


def _upgrade(planet: dict, btype: int, lv: int) -> tuple[tuple[int, int, int], int]:
    """Cost and seconds of the next level (the Pioneer Lab's cut by the empire's research)."""
    cost = eco.upgrade_cost(btype, lv)
    seconds = _faster(planet, "build", eco.build_seconds(cost[0], cost[1], *robotics_levels(planet)))
    if btype == eco.PIONEER_LAB:
        boost = planet.get("officer", {})
        cost = tuple(c * (100 - min(90, boost.get("pioneer_cost", 0))) // 100 for c in cost)
        seconds = max(1, seconds * (100 - min(90, boost.get("pioneer_time", 0))) // 100)
    return cost, seconds


def building_view(planet: dict, b: dict) -> dict:
    btype, lv = b["building_type"], b["building_level"]
    rule = eco.BUILDINGS[btype]
    robotics, nanite = robotics_levels(planet)
    up, up_time = _upgrade(planet, btype, lv)
    view = dict(b)
    view.update({
        "building_name": rule.name,
        "up_metal_require": up[0],
        "up_crystal_require": up[1],
        "up_deuterium_require": up[2],
        "up_time": up_time,
        "up_extra_energy": RADAR_ENERGY_PER_LEVEL if btype == eco.RADAR_CENTER
        else round(eco.mine_energy(btype, lv + 1) - eco.mine_energy(btype, lv))
        if btype in eco.MINES else 0,
        "tech_tree": _tech_tree(rule),
    })
    if lv > 0:
        down = eco.teardown_cost(btype, lv)
        view.update({
            "down_metal_require": down[0],
            "down_crystal_require": down[1],
            "down_deuterium_require": down[2],
            "down_time": _faster(planet, "build", eco.build_seconds(down[0], down[1], robotics, nanite)),
        })
    return view


def event_view(planet: dict, event: dict) -> dict:
    b = next(b for b in planet["buildings"] if b["building_id"] == event["building_id"])
    view = {k: v for k, v in event.items() if k != "cost"}
    view.update({
        "building_name": eco.BUILDINGS[b["building_type"]].name,
        "building_level": b["building_level"],
        "building_position_index": b["building_position_index"],
        "finished": False,
    })
    return view


def client_view(empire: dict, now: float) -> dict:
    """The main_planet object as the client expects it."""
    planet = empire["main_planet"]
    rates = production(planet, empire)
    hidden = ("buildings", "building_events", "last_update", "moon", "last_scan", "krypton_bonus", "repair",
              "officer")
    view = {k: v for k, v in planet.items() if k not in hidden}
    view.update({res: int(planet[res]) for res in RESOURCES})
    view.update({f"{res}_max": capacity(planet, res) for res in RESOURCES})
    view.update({f"{res}_perhour": round(rates[res]) for res in RESOURCES})
    view.update({
        "energy_max": round(rates["energy_max"]),
        "energy_used": round(rates["energy_used"]),
        "solar_plant_energy": round(rates["solar_energy"]),
        "fleets": [fleet_view(planet, f) for f in planet["fleets"]],
        "fleet_building_events": [fleet_event_view(e) for e in planet["fleet_building_events"]],
        "fusion_energy": round(rates["fusion_energy"]),
        "buildings": [building_view(planet, b) for b in planet["buildings"]
                      if buildable_here(planet, b["building_type"]) or b["building_level"]],
        "building_events": [event_view(planet, e) for e in planet["building_events"]],
        "current_server_time": int(now),
        # Planet info screen
        "senor_array_range": max(0, sensor_range(planet)) if is_moon(planet) else 0,
        "field_current": fields(planet)[0],
        "field_max": fields(planet)[1],
        "temp_max": temperatures(planet)[1],
        "temp_min": temperatures(planet)[0],
        # no projection: the client checks for 0, not for empty values
        "planet_icon": planet.get("planet_icon") or 0,
        "is_radar_on": planet.get("radar_on", True),
        # resource settings: energy each mine uses (negative), satellites' energy, the share of
        # the needed energy that is there
        **{f"{res}_energy": -round(eco.mine_energy(m, level(planet, m)) * planet[f"{res}_mine_percent"] / 100)
           for res, m in zip(RESOURCES, eco.MINES)},
        "solar_satellite_energy": round(rates["satellite_energy"]),
        "produce_percent": min(100, round(100 * rates["energy_max"] / rates["energy_used"]))
        if rates["energy_used"] else 100,
        "radar_energy": radar_energy(planet),
        "radar_fleets": radar_fleets(planet),
        "planet_icon_info": planet.get("planet_icon_info") or 0,
    })
    return view


def _tech_tree(rule: eco.BuildingRule) -> dict:
    return {
        "buildings_tree": [{"unit_type": t, "unit_level": l, "unit_name": eco.BUILDINGS[t].name}
                           for t, l in rule.buildings_required.items()],
        "researches_tree": [{"unit_type": t, "unit_level": l, "unit_name": eco.RESEARCHES[t].name}
                            for t, l in rule.researches_required.items()],
    }


def research_view(empire: dict, research: dict) -> dict:
    rtype, lv = research["research_type"], research["research_level"]
    rule = eco.RESEARCHES[rtype]
    cost = eco.research_cost(rtype, lv)
    view = dict(research)
    view.update({
        "research_name": rule.name,
        "up_metal_require": cost[0],
        "up_crystal_require": cost[1],
        "up_deuterium_require": cost[2],
        "up_time": _faster(empire["main_planet"], "research",
                           eco.research_seconds(cost[0], cost[1], level(empire["main_planet"], eco.RESEARCH_LAB))),
        "tech_tree": _tech_tree(rule),
    })
    return view


def research_event_view(empire: dict, event: dict) -> dict:
    research = next(r for r in empire["researches"] if r["research_id"] == event["research_id"])
    view = {k: v for k, v in event.items() if k != "cost"}
    view.update({
        "research_name": eco.RESEARCHES[research["research_type"]].name,
        "research_level": research["research_level"],
        "finished": False,
    })
    return view


def researches_view(empire: dict) -> dict:
    """The research fields of the empire as the client expects them."""
    return {
        "researches": [research_view(empire, r) for r in empire["researches"]
                       if r["research_type"] in eco.RESEARCHES],
        "research_upgrading_events": [research_event_view(empire, e)
                                      for e in empire["research_upgrading_events"]],
    }


def fleet_view(planet: dict, f: dict) -> dict:
    ftype = f["fleet_type"]
    # id is what the dispatch screen sends; only ships (category 1) are offered there.
    view = dict(f, id=f["fleet_id"], amount_limit=f["amount"],
                fleet_category=FLEET_CATEGORY_SHIP if ftype in eco.SHIPS else FLEET_CATEGORY_DEFENSE)
    if ftype in eco.UNITS:
        rule = eco.UNITS[ftype]
        view.update({
            "fleet_name": rule.name,
            "metal_require": rule.metal,
            "crystal_require": rule.crystal,
            "deuterium_require": rule.deuterium,
            "up_time": _faster(planet, "defense" if ftype not in eco.SHIPS else "ships",
                               eco.unit_seconds(ftype, level(planet, shipyard_type(planet)),
                                                level(planet, eco.NANITE_FACTORY))),
            "tech_tree": _tech_tree(rule),
        })
    else:
        # Not buildable here; the client hides the build button for these types.
        view.update({
            "fleet_name": eco.SPECIAL_UNITS.get(ftype, f"Unit {ftype}"),
            "metal_require": 0, "crystal_require": 0, "deuterium_require": 0, "up_time": 0,
            "tech_tree": {"buildings_tree": [], "researches_tree": []},
        })
    return view


def fleet_event_view(order: dict) -> dict:
    return {
        "event_id": order["event_id"],
        "fleet_id": order["fleet_id"],
        "fleet_type": order["fleet_type"],
        "fleet_name": eco.UNITS[order["fleet_type"]].name if order["fleet_type"] in eco.UNITS
        else eco.SPECIAL_UNITS.get(order["fleet_type"], ""),
        "building_count": order["total_count"] - order["delivered"],
        "start_time": order["start_time"],
        "finish_time": order["finish_time"],
        "total_time": order["total_time"],
        "finished": False,
    }
