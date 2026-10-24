"""Initial state of a new player and the login response built from it.

Field names follow what the client's Lua scripts read (GameGlobalData and the UI scripts).
Values for a fresh account are best guesses until we know the original numbers.
"""

import secrets
import time

from . import economy as eco
from . import planet as planet_rules
from . import universe

# Planet lots 1-3 are reserved for the mines; free building lots are 4-15.
MINE_POSITIONS = {eco.METAL_MINE: 1, eco.CRYSTAL_MINE: 2, eco.DEUTERIUM_MINE: 3}


PLANET_TYPE_CAPITAL = 2
HOME_PLANET_NAME = "Homeworld"


def guest_username(player_id: int) -> str:
    return f"Commander{player_id}"


def new_state(player_id: int, username: str, home: dict | None = None) -> dict:
    """`home` is where universe.place_home put the planet; without it (tests) a fixed spot."""
    home = home or {"planet_id": player_id, "galaxy": 1, "system": player_id % 499 + 1, "position": 8}
    planet_id = home["planet_id"]
    buildings = [
        {
            "building_id": planet_id * 100 + btype,
            "building_type": btype,
            "building_level": 0,
            "building_position_index": MINE_POSITIONS.get(btype, 0),
        }
        for btype in eco.BUILDINGS
    ]
    fleets = [
        {"fleet_id": planet_id * 100 + ftype, "fleet_type": ftype, "amount": 0}
        for ftype in [*eco.UNITS, *eco.SPECIAL_UNITS]
    ]
    researches = [
        {"research_id": player_id * 100 + rtype, "research_type": rtype, "research_level": 0}
        for rtype in eco.RESEARCHES
    ]
    position = {"galaxy_position": home["galaxy"], "system_position": home["system"],
                "planet_position": home["position"]}
    main_planet = {
        "planet_id": planet_id,
        "planet_name": HOME_PLANET_NAME,
        "planet_type": PLANET_TYPE_CAPITAL,
        "system_id": universe.system_id(home["galaxy"], home["system"]),
        **position,
        "metal": 500,
        "crystal": 500,
        "deuterium": 0,
        "last_update": time.time(),
        "metal_mine_percent": 100,
        "crystal_mine_percent": 100,
        "deuterium_mine_percent": 100,
        "solar_plant_percent": 100,
        "fusion_reactor_percent": 100,
        "buildings": buildings,
        "fleets": fleets,
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
    empire = {
        "empire_id": player_id,
        "user_id": player_id,
        "main_planet_id": planet_id,
        "main_planet": main_planet,
        "planets_info": [
            {"planet_id": planet_id, "planet_name": main_planet["planet_name"],
             "planet_type": PLANET_TYPE_CAPITAL, **position}
        ],
        "researches": researches,
        "research_upgrading_events": [],
        "fleet_hanging_events": [],
        "fortress_interspace_events": [],
        "alliance_id": 0,
        "alliance_name": "",
        "alliance_application_id": 0,
        "alliance_membership_id": 0,
        "dark_matter_count": 0,
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
        "state_version": 1,
        "user_id": player_id,
        "empire_id": player_id,
        "username": username,
        "salt": secrets.token_hex(8),
        "account_level": 0,
        "empire": empire,
        "vip_info": {"vip_left": 0},
        "grows": {},
        "purchase": {},
        "events": [],
        "athletics_server_config": {},
    }


def empire_view(state: dict, now: float) -> dict:
    """The empire object as the client expects it."""
    empire = state["empire"]
    return dict(empire, main_planet=planet_rules.client_view(empire, now), **planet_rules.researches_view(empire))


def empire_summary(state: dict) -> dict:
    """Empire fields without the planet, for responses that send the planet separately."""
    empire = state["empire"]
    summary = {k: v for k, v in empire.items() if k != "main_planet"}
    summary.update(planet_rules.researches_view(empire))
    return summary


def login_response(state: dict, now: float) -> dict:
    response = {k: v for k, v in state.items() if k not in ("empire", "next_event_id")}
    response["empire"] = empire_view(state, now)
    response["checkin_days"] = 0
    return response
