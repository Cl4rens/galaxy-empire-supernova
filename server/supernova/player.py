"""Initial state of a new player and the login response built from it.

Field names follow what the client's Lua scripts read (GameGlobalData and the UI scripts).
Values for a fresh account are best guesses until we know the original numbers.
"""

import secrets
import time

# building_type -> English name. Moon buildings (17-19) are not on a normal planet.
BUILDINGS = {
    1: "Metal Mine",
    2: "Crystal Mine",
    3: "Deuterium Synthesizer",
    4: "Solar Plant",
    5: "Fusion Reactor",
    6: "Metal Storage",
    7: "Crystal Storage",
    8: "Deuterium Tank",
    9: "Robotics Factory",
    10: "Shipyard",
    11: "Research Lab",
    12: "Alliance Depot",
    13: "Radar Center",
    14: "Nanite Factory",
    15: "Sensor Array",
    16: "Transmit Portal",
    20: "Krypton Lab",
    21: "Pioneer Lab",
}

# Planet positions 1-3 are taken by the mines; free building lots are 4-15.
MINE_POSITIONS = {1: 1, 2: 2, 3: 3}

RESEARCHES = {
    1: "Energy Technology",
    2: "Laser Technology",
    3: "Ion Technology",
    4: "Hyperspace Technology",
    5: "Plasma Technology",
    6: "Combustion Drive",
    7: "Impulse Drive",
    8: "Hyperspace Drive",
    9: "Espionage Technology",
    10: "Computer Technology",
    11: "Astrophysics",
    14: "Weapons Technology",
    15: "Shielding Technology",
    16: "Armour Technology",
    17: "Krypton Technology",
    18: "Ultra Temperature Polymerizer",
    19: "Anti-Gravity",
    20: "Alloy Detection Technology",
    21: "Dynamic Power Technology",
    22: "Missile Technology",
}

FLEETS = {
    1: "Light Fighter",
    2: "Heavy Fighter",
    3: "Cruiser",
    4: "Battleship",
    5: "Battlecruiser",
    6: "Bomber",
    7: "Destroyer",
    8: "Deathstar",
    9: "Small Cargo",
    10: "Large Cargo",
    11: "Colony Ship",
    12: "Recycler",
    13: "Espionage Probe",
    14: "Solar Satellite",
    15: "Rocket Launcher",
    16: "Light Laser",
    17: "Heavy Laser",
    18: "Gauss Cannon",
    19: "Ion Cannon",
    20: "Plasma Cannon",
    21: "Small Shield Dome",
    22: "Large Shield Dome",
    23: "Interceptor",
    24: "Behemoth",
}

PLANET_TYPE_CAPITAL = 2


def guest_username(player_id: int) -> str:
    return f"Commander{player_id}"


def new_state(player_id: int, username: str) -> dict:
    planet_id = player_id
    buildings = [
        {
            "building_id": planet_id * 100 + btype,
            "building_type": btype,
            "building_name": name,
            "building_level": 1 if btype in MINE_POSITIONS else 0,
            "building_position_index": MINE_POSITIONS.get(btype, 0),
        }
        for btype, name in BUILDINGS.items()
    ]
    fleets = [
        {
            "fleet_id": planet_id * 100 + ftype,
            "fleet_type": ftype,
            "fleet_name": name,
            "amount": 0,
            "amount_limit": 0,
        }
        for ftype, name in FLEETS.items()
    ]
    researches = [
        {
            "research_id": player_id * 100 + rtype,
            "research_type": rtype,
            "research_name": name,
            "research_level": 0,
        }
        for rtype, name in RESEARCHES.items()
    ]
    position = {"galaxy_position": 1, "system_position": player_id % 499 + 1, "planet_position": 8}
    main_planet = {
        "planet_id": planet_id,
        "planet_name": "Homeworld",
        "planet_type": PLANET_TYPE_CAPITAL,
        "system_id": position["system_position"],
        **position,
        "metal": 500,
        "crystal": 500,
        "deuterium": 0,
        "metal_max": 10000,
        "crystal_max": 10000,
        "deuterium_max": 10000,
        "energy_max": 0,
        "energy_used": 0,
        "metal_mine_percent": 100,
        "crystal_mine_percent": 100,
        "deuterium_mine_percent": 100,
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


def login_response(state: dict) -> dict:
    response = dict(state)
    response["empire"] = dict(state["empire"])
    response["empire"]["main_planet"] = dict(state["empire"]["main_planet"], current_server_time=int(time.time()))
    response["checkin_days"] = 0
    return response
