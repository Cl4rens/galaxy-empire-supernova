"""Initial state of a new player and the login response built from it.

Field names follow what the client's Lua scripts read (GameGlobalData and the UI scripts).
Values for a fresh account are best guesses until we know the original numbers.
"""

import secrets
import time

from . import economy as eco
from . import checkin, config, fleets, fortresses, gifts, growth, mails, ranking
from . import planet as planet_rules
from . import universe, vip

# Planet lots 1-3 are reserved for the mines; free building lots are 4-15.
MINE_POSITIONS = {eco.METAL_MINE: 1, eco.CRYSTAL_MINE: 2, eco.DEUTERIUM_MINE: 3}


PLANET_TYPE_CAPITAL = 2
HOME_PLANET_NAME = "Homeworld"


def guest_username(player_id: int) -> str:
    return f"Commander{player_id}"


PLANET_TYPE_COLONY = 1
COLONY_NAME = "Colony"
PLANET_TYPE_MOON = planet_rules.PLANET_TYPE_MOON
MOON_NAME = "Moon"


def new_planet(planet_id: int, galaxy: int, system: int, position: int, name: str,
               planet_type: int = PLANET_TYPE_COLONY, resources: dict | None = None) -> dict:
    """A planet without buildings or ships."""
    resources = resources or {"metal": 0, "crystal": 0, "deuterium": 0}
    return {
        "planet_id": planet_id,
        "planet_name": name,
        "planet_type": planet_type,
        "system_id": universe.system_id(galaxy, system),
        "galaxy_position": galaxy,
        "system_position": system,
        "planet_position": position,
        **resources,
        "last_update": time.time(),
        "metal_mine_percent": 100,
        "crystal_mine_percent": 100,
        "deuterium_mine_percent": 100,
        "solar_plant_percent": 100,
        "fusion_reactor_percent": 100,
        "buildings": [
            {
                "building_id": planet_id * 100 + btype,
                "building_type": btype,
                "building_level": 0,
                "building_position_index": MINE_POSITIONS.get(btype, 0),
            }
            for btype in eco.BUILDINGS
        ],
        "fleets": [
            {"fleet_id": planet_id * 100 + ftype, "fleet_type": ftype, "amount": 0}
            for ftype in [*eco.UNITS, *eco.SPECIAL_UNITS]
        ],
        "building_events": [],
        "fleet_building_events": [],
        "transmit_recharge_event": [],
        "kryptons": [],
        "is_underattack": False,
        "senor_array_range": 0,
        "planet_icon": "",
        "planet_icon_end_time": 0,
        "planet_icon_info": {},
        "last_chat": {},
    }


def new_state(player_id: int, username: str, home: dict | None = None) -> dict:
    """`home` is where universe.place_home put the planet; without it (tests) a fixed spot."""
    home = home or {"planet_id": player_id, "galaxy": 1, "system": player_id % 499 + 1, "position": 8}
    planet_id = home["planet_id"]
    main_planet = new_planet(planet_id, home["galaxy"], home["system"], home["position"], HOME_PLANET_NAME,
                             PLANET_TYPE_CAPITAL, {"metal": 500, "crystal": 500, "deuterium": 0})
    empire = {
        "empire_id": player_id,
        "user_id": player_id,
        "main_planet_id": planet_id,  # the capital
        "planets": [main_planet],
        "current_planet_id": planet_id,  # the planet the player is looking at
        "main_planet": main_planet,  # == the current planet; not stored, see db.py
        "researches": [
            {"research_id": player_id * 100 + rtype, "research_type": rtype, "research_level": 0}
            for rtype in eco.RESEARCHES
        ],
        "research_upgrading_events": [],
        "fleet_hanging_events": [],
        "fortress_interspace_events": [],
        "alliance_id": 0,
        "alliance_name": "",
        "alliance_application_id": 0,
        "alliance_membership_id": 0,
        "dark_matter_count": config.STARTING_DARK_MATTER,
        "gu_count": 0,
        "score": 0,
        "rank": 0,
        "new_message_count": 0,
        "warning_count": 0,
        "acs_attack_count": 0,
        "safe_explore_times": 0,
        "quests_complete": False,
        "quests_newbie_guide": 0,
        "quests_new_ver": 0,
        "gift_id": 0,
        "buff_tech": 0,
        "buff_resources": 0,
        "buff_fleet": 0,
        "buff_engineer": 0,
        "buff_commander": 0,
    }
    return {
        "state_version": 2,
        "user_id": player_id,
        "empire_id": player_id,
        "username": username,
        "salt": secrets.token_hex(8),
        "account_level": 0,
        "empire": empire,
        "vip_info": {"vip_level": 0, "vip_left": 0},
        "grows": {},
        "purchase": {},
        "events": [],
        "athletics_server_config": {},
    }


def add_moon(player_id: int, planet: dict) -> dict | None:
    """Give `planet` a moon. Returns it, or None if the planet already has one."""
    if planet.get("moon"):
        return None
    galaxy, system, position = planet["galaxy_position"], planet["system_position"], planet["planet_position"]
    moon_id = universe.add_planet(player_id, galaxy, system, position, MOON_NAME, kind=universe.MOON)
    if moon_id is None:
        return None
    moon = new_planet(moon_id, galaxy, system, position, MOON_NAME, PLANET_TYPE_MOON)
    for b in moon["buildings"]:  # the moon screen has a fixed place for each type, found by its type
        if b["building_type"] in eco.MOON_BUILDINGS:
            b["building_position_index"] = b["building_type"]
    planet["moon"] = moon
    return moon


def _moon_info(moon: dict) -> dict:
    used, available = planet_rules.fields(moon)
    return {"moon_id": moon["planet_id"], "moon_name": moon["planet_name"], "moon_field_current": used,
            "moon_field_max": available, "attacked_time": 0, "attacked_num": 0}


def planets_info(empire: dict) -> list[dict]:
    """The player's planets as the planet list shows them."""
    return [{
        "planet_id": p["planet_id"],
        "planet_name": p["planet_name"],
        "planet_type": p["planet_type"],
        "galaxy_position": p["galaxy_position"],
        "system_position": p["system_position"],
        "planet_position": p["planet_position"],
        "field_current": planet_rules.fields(p)[0],
        "field_max": planet_rules.fields(p)[1],
        "planet_activity": 2,
        "radar_level": planet_rules.level(p, eco.RADAR_CENTER),
        "is_radar_on": p.get("radar_on", True),
        "moon_array": [_moon_info(p["moon"])] if p.get("moon") else [],
        "is_protected": 0,
        "protected_time": 0,
        "attacked_time": 0,
        "attacked_num": 0,
        "auto_explore": False,
    } for p in empire["planets"]]


HIDDEN_EMPIRE_FIELDS = ("main_planet", "planets", "current_planet_id", "dark_matter_income", "fleet_devices",
                        "officers", "vip")
PRIVATE_STATE = ("empire", "next_event_id", "quests", "bookmarks", "checkin", "kryptons", "flagships", "ecm",
                 "fortress", "items", "daily", "growth_claimed", "gc_bought", "arena_points", "invited_by",
                 "chest")


def empire_view(state: dict, now: float) -> dict:
    """The empire object as the client expects it."""
    empire = state["empire"]
    view = {k: v for k, v in empire.items() if k not in HIDDEN_EMPIRE_FIELDS}
    view["vip_info"] = vip.summary_view(state, now)
    view.update(score=ranking.points(state)["score"], rank=ranking.rank_of(state["user_id"]),
                gift_id=gifts.pending_id(state["user_id"]))
    return dict(view, main_planet=planet_rules.client_view(empire, now), **planet_rules.researches_view(empire),
                planets_info=planets_info(empire), fleet_hanging_events=fleets.events_view(empire["user_id"]),
                fortress_interspace_events=fortresses.events_view(empire["user_id"]))


def empire_summary(state: dict) -> dict:
    """Empire fields without the planet, for responses that send the planet separately."""
    empire = state["empire"]
    summary = {k: v for k, v in empire.items() if k not in HIDDEN_EMPIRE_FIELDS}
    summary["vip_info"] = vip.summary_view(state, time.time())
    summary["planets_info"] = planets_info(empire)
    summary.update(planet_rules.researches_view(empire))
    summary["fleet_hanging_events"] = fleets.events_view(empire["user_id"])
    summary["fortress_interspace_events"] = fortresses.events_view(empire["user_id"])
    summary["gift_id"] = gifts.pending_id(empire["user_id"])
    summary.update(score=ranking.points(state)["score"], rank=ranking.rank_of(state["user_id"]))
    return summary


def login_response(state: dict, now: float) -> dict:
    response = {k: v for k, v in state.items() if k not in PRIVATE_STATE}
    response["empire"] = empire_view(state, now)
    response["vip_info"] = vip.info_view(state, now)
    response["checkin_days"] = checkin.unclaimed(state)
    response["grows"] = growth.summary(state)
    response["empire"]["new_message_count"] = mails.unread_count(state["user_id"])
    return response
