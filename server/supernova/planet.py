"""Planet simulation: resource production over time, building upgrades and their timers.

The stored planet only holds base data (levels, resources, running events, last update time).
Everything the client derives from it (costs, production, energy, storage limits) is computed
in `client_view` so it always matches the current rules.
"""

import time

from . import config, economy as eco

FREE_LOTS = range(4, 16)  # lots 1-3 belong to the mines
EVENT_UPGRADE, EVENT_TEARDOWN = 1, 2
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


def research_level(empire: dict, rtype: int) -> int:
    return next((r["research_level"] for r in empire["researches"] if r["research_type"] == rtype), 0)


def production(planet: dict, empire: dict) -> dict:
    """Hourly production and energy balance, with game speed applied."""
    pct = {
        eco.METAL_MINE: planet["metal_mine_percent"] / 100,
        eco.CRYSTAL_MINE: planet["crystal_mine_percent"] / 100,
        eco.DEUTERIUM_MINE: planet["deuterium_mine_percent"] / 100,
    }
    solar = eco.solar_energy(level(planet, eco.SOLAR_PLANT)) * planet["solar_plant_percent"] / 100
    satellites = fleet(planet, eco.SOLAR_SATELLITE)["amount"] * eco.SOLAR_SATELLITE_ENERGY
    fusion_level = level(planet, eco.FUSION_REACTOR)
    fusion_pct = planet["fusion_reactor_percent"] / 100
    fusion = eco.fusion_energy(fusion_level, research_level(empire, eco.ENERGY_TECH)) * fusion_pct
    produced = solar + fusion + satellites
    used = sum(eco.mine_energy(m, level(planet, m)) * pct[m] for m in eco.MINES)
    factor = min(1.0, produced / used) if used > 0 else 1.0

    def mine(btype: int, resource: str) -> float:
        return eco.BASE_PRODUCTION[resource] + eco.mine_output(btype, level(planet, btype)) * pct[btype] * factor

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
            break
        done = min(order["total_count"], int((now - order["start_time"]) // order["unit_seconds"]))
        fleet(planet, order["fleet_type"])["amount"] += done - order["delivered"]
        order["delivered"] = done
        if done < order["total_count"]:
            break
        planet["fleet_building_events"].remove(order)


def tick(empire: dict, now: float | None = None) -> None:
    """Bring the main planet up to `now`: production, constructions, research and shipyard.

    Events are applied in time order, with production calculated in between, because they
    change production (mines, energy technology, solar satellites).
    """
    planet = empire["main_planet"]
    now = time.time() if now is None else now
    t = planet["last_update"]

    def done(events, event, finish, target):
        def apply(at):
            finish(target, event)
            events.remove(event)
        return apply

    queue = [(e["finish_time"], done(planet["building_events"], e, _finish_event, planet))
             for e in planet["building_events"]]
    queue += [(e["finish_time"], done(empire["research_upgrading_events"], e, _finish_research, empire))
              for e in empire["research_upgrading_events"]]
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
    return all(level(planet, t) >= lv for t, lv in rule.buildings_required.items()) and all(
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
    if planet["building_events"]:
        raise GameError("Another building is already under construction")
    if empire["research_upgrading_events"] and building_id == building(planet, eco.RESEARCH_LAB)["building_id"]:
        raise GameError("The research lab cannot be upgraded while research is running")
    if planet["fleet_building_events"] and building_id in (building(planet, eco.SHIPYARD)["building_id"],
                                                           building(planet, eco.NANITE_FACTORY)["building_id"]):
        raise GameError("This building cannot be upgraded while the shipyard is working")
    b = next((b for b in planet["buildings"] if b["building_id"] == building_id), None)
    if b is None:
        raise GameError("Unknown building")
    btype, current = b["building_type"], b["building_level"]

    if event_type == EVENT_UPGRADE:
        if not _requirements_met(planet, empire, btype):
            raise GameError("Requirements not met")
        if current == 0 and btype not in eco.MINES:
            taken = {x["building_position_index"] for x in planet["buildings"]}
            if lot not in FREE_LOTS or lot in taken:
                raise GameError("This building lot is not free")
        cost = eco.upgrade_cost(btype, current)
        seconds = eco.build_seconds(cost[0], cost[1], level(planet, eco.ROBOTICS_FACTORY),
                                    level(planet, eco.NANITE_FACTORY))
    elif event_type == EVENT_TEARDOWN:
        if current == 0:
            raise GameError("Nothing to tear down")
        cost = eco.teardown_cost(btype, current)
        seconds = eco.build_seconds(cost[0], cost[1], level(planet, eco.ROBOTICS_FACTORY),
                                    level(planet, eco.NANITE_FACTORY))
    else:
        raise GameError("Unknown event type")

    _pay(planet, cost)
    if event_type == EVENT_UPGRADE and current == 0 and btype not in eco.MINES:
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
    if empire["research_upgrading_events"]:
        raise GameError("Another research is already running")
    lab = building(planet, eco.RESEARCH_LAB)
    if any(e["building_id"] == lab["building_id"] for e in planet["building_events"]):
        raise GameError("The research lab is being upgraded")
    rtype = research["research_type"]
    if not _requirements_met(planet, empire, rtype, eco.RESEARCHES):
        raise GameError("Requirements not met")
    cost = eco.research_cost(rtype, research["research_level"])
    seconds = eco.research_seconds(cost[0], cost[1], lab["building_level"])
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
    if level(planet, eco.SHIPYARD) < 1:
        raise GameError("You need a shipyard")
    busy = {building(planet, eco.SHIPYARD)["building_id"], building(planet, eco.NANITE_FACTORY)["building_id"]}
    if any(e["building_id"] in busy for e in planet["building_events"]):
        raise GameError("The shipyard is being upgraded")
    if not _requirements_met(planet, empire, ftype, eco.UNITS):
        raise GameError("Requirements not met")
    if ftype in eco.UNIQUE_UNITS:
        queued = sum(o["total_count"] - o["delivered"] for o in planet["fleet_building_events"]
                     if o["fleet_type"] == ftype)
        if f["amount"] + queued + count > 1:
            raise GameError("Only one of these can be built")
    unit_cost = eco.unit_cost(ftype)
    _pay(planet, tuple(c * count for c in unit_cost))
    seconds = eco.unit_seconds(ftype, level(planet, eco.SHIPYARD), level(planet, eco.NANITE_FACTORY))
    queue_end = max((o["finish_time"] for o in planet["fleet_building_events"]), default=now)
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


def building_view(planet: dict, b: dict) -> dict:
    btype, lv = b["building_type"], b["building_level"]
    rule = eco.BUILDINGS[btype]
    robotics, nanite = level(planet, eco.ROBOTICS_FACTORY), level(planet, eco.NANITE_FACTORY)
    up = eco.upgrade_cost(btype, lv)
    view = dict(b)
    view.update({
        "building_name": rule.name,
        "up_metal_require": up[0],
        "up_crystal_require": up[1],
        "up_deuterium_require": up[2],
        "up_time": eco.build_seconds(up[0], up[1], robotics, nanite),
        "up_extra_energy": round(eco.mine_energy(btype, lv + 1) - eco.mine_energy(btype, lv))
        if btype in eco.MINES else 0,
        "tech_tree": _tech_tree(rule),
    })
    if lv > 0:
        down = eco.teardown_cost(btype, lv)
        view.update({
            "down_metal_require": down[0],
            "down_crystal_require": down[1],
            "down_deuterium_require": down[2],
            "down_time": eco.build_seconds(down[0], down[1], robotics, nanite),
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
    view = {k: v for k, v in planet.items() if k not in ("buildings", "building_events", "last_update")}
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
        "buildings": [building_view(planet, b) for b in planet["buildings"]],
        "building_events": [event_view(planet, e) for e in planet["building_events"]],
        "current_server_time": int(now),
    })
    return view


def _tech_tree(rule: eco.BuildingRule) -> dict:
    return {
        "buildings_tree": [{"unit_type": t, "unit_level": l} for t, l in rule.buildings_required.items()],
        "researches_tree": [{"unit_type": t, "unit_level": l} for t, l in rule.researches_required.items()],
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
        "up_time": eco.research_seconds(cost[0], cost[1], level(empire["main_planet"], eco.RESEARCH_LAB)),
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
    view = dict(f, amount_limit=f["amount"])
    if ftype in eco.UNITS:
        rule = eco.UNITS[ftype]
        view.update({
            "fleet_name": rule.name,
            "metal_require": rule.metal,
            "crystal_require": rule.crystal,
            "deuterium_require": rule.deuterium,
            "up_time": eco.unit_seconds(ftype, level(planet, eco.SHIPYARD), level(planet, eco.NANITE_FACTORY)),
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
        "fleet_name": eco.UNITS[order["fleet_type"]].name,
        "building_count": order["total_count"] - order["delivered"],
        "start_time": order["start_time"],
        "finish_time": order["finish_time"],
        "total_time": order["total_time"],
        "finished": False,
    }
