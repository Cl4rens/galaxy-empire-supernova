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
    fusion_level = level(planet, eco.FUSION_REACTOR)
    fusion_pct = planet["fusion_reactor_percent"] / 100
    fusion = eco.fusion_energy(fusion_level, research_level(empire, eco.ENERGY_TECH)) * fusion_pct
    produced = solar + fusion
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


def tick(empire: dict, now: float | None = None) -> None:
    """Bring the main planet up to `now`: production and finished building events."""
    planet = empire["main_planet"]
    now = time.time() if now is None else now
    t = planet["last_update"]
    for event in sorted(planet["building_events"], key=lambda e: e["finish_time"]):
        if event["finish_time"] > now:
            break
        _accrue(planet, empire, event["finish_time"] - t)
        t = max(t, event["finish_time"])
        _finish_event(planet, event)
        planet["building_events"].remove(event)
    _accrue(planet, empire, now - t)
    planet["last_update"] = max(t, now)


# --- Actions ------------------------------------------------------------------------------


def _requirements_met(planet: dict, empire: dict, btype: int) -> bool:
    rule = eco.BUILDINGS[btype]
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
    state["next_event_id"] = state.get("next_event_id", 0) + 1
    planet["building_events"].append({
        "event_id": state["next_event_id"],
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
        "tech_tree": {
            "buildings_tree": [{"unit_type": t, "unit_level": l} for t, l in rule.buildings_required.items()],
            "researches_tree": [{"unit_type": t, "unit_level": l} for t, l in rule.researches_required.items()],
        },
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
        "fusion_energy": round(rates["fusion_energy"]),
        "buildings": [building_view(planet, b) for b in planet["buildings"]],
        "building_events": [event_view(planet, e) for e in planet["building_events"]],
        "current_server_time": int(now),
    })
    return view
