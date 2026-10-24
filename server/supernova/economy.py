"""Game rules: building costs, build times, production, energy and storage.

The original server's numbers are unknown. Galaxy Empire is closely modelled on OGame (same
buildings, ships and research), so these are the classic OGame formulas. Buildings that OGame
does not have (radar center, krypton lab, pioneer lab, ...) got made-up values.
"""

import math
from dataclasses import dataclass

from . import config

METAL_MINE, CRYSTAL_MINE, DEUTERIUM_MINE = 1, 2, 3
SOLAR_PLANT, FUSION_REACTOR = 4, 5
METAL_STORAGE, CRYSTAL_STORAGE, DEUTERIUM_STORAGE = 6, 7, 8
ROBOTICS_FACTORY, SHIPYARD, RESEARCH_LAB = 9, 10, 11
ALLIANCE_DEPOT, RADAR_CENTER, NANITE_FACTORY, SENSOR_ARRAY, TRANSMIT_PORTAL = 12, 13, 14, 15, 16
KRYPTON_LAB, PIONEER_LAB = 20, 21

ENERGY_TECH, COMPUTER_TECH, HYPERSPACE_TECH = 1, 10, 4


@dataclass(frozen=True)
class BuildingRule:
    name: str
    metal: int
    crystal: int
    deuterium: int
    factor: float
    buildings_required: dict[int, int]  # building_type -> level
    researches_required: dict[int, int]  # research_type -> level


BUILDINGS: dict[int, BuildingRule] = {
    METAL_MINE: BuildingRule("Metal Mine", 60, 15, 0, 1.5, {}, {}),
    CRYSTAL_MINE: BuildingRule("Crystal Mine", 48, 24, 0, 1.6, {}, {}),
    DEUTERIUM_MINE: BuildingRule("Deuterium Synthesizer", 225, 75, 0, 1.5, {}, {}),
    SOLAR_PLANT: BuildingRule("Solar Plant", 75, 30, 0, 1.5, {}, {}),
    FUSION_REACTOR: BuildingRule("Fusion Reactor", 900, 360, 180, 1.8, {DEUTERIUM_MINE: 5}, {ENERGY_TECH: 3}),
    METAL_STORAGE: BuildingRule("Metal Storage", 1000, 0, 0, 2, {}, {}),
    CRYSTAL_STORAGE: BuildingRule("Crystal Storage", 1000, 500, 0, 2, {}, {}),
    DEUTERIUM_STORAGE: BuildingRule("Deuterium Tank", 1000, 1000, 0, 2, {}, {}),
    ROBOTICS_FACTORY: BuildingRule("Robotics Factory", 400, 120, 200, 2, {}, {}),
    SHIPYARD: BuildingRule("Shipyard", 400, 200, 100, 2, {ROBOTICS_FACTORY: 2}, {}),
    RESEARCH_LAB: BuildingRule("Research Lab", 200, 400, 200, 2, {}, {}),
    ALLIANCE_DEPOT: BuildingRule("Alliance Depot", 20000, 40000, 0, 2, {}, {}),
    RADAR_CENTER: BuildingRule("Radar Center", 20000, 20000, 1000, 2, {SHIPYARD: 1}, {}),
    NANITE_FACTORY: BuildingRule("Nanite Factory", 1000000, 500000, 100000, 2,
                                 {ROBOTICS_FACTORY: 10}, {COMPUTER_TECH: 10}),
    SENSOR_ARRAY: BuildingRule("Sensor Array", 20000, 40000, 20000, 2, {RADAR_CENTER: 1}, {}),
    TRANSMIT_PORTAL: BuildingRule("Transmit Portal", 2000000, 4000000, 2000000, 2, {}, {HYPERSPACE_TECH: 7}),
    KRYPTON_LAB: BuildingRule("Krypton Lab", 10000, 20000, 10000, 2, {RESEARCH_LAB: 5}, {}),
    PIONEER_LAB: BuildingRule("Pioneer Lab", 50000, 50000, 20000, 2, {RESEARCH_LAB: 8}, {}),
}

MINES = (METAL_MINE, CRYSTAL_MINE, DEUTERIUM_MINE)
STORAGE_FOR = {"metal": METAL_STORAGE, "crystal": CRYSTAL_STORAGE, "deuterium": DEUTERIUM_STORAGE}

# Production every planet has without any mine (per hour).
BASE_PRODUCTION = {"metal": 30, "crystal": 15, "deuterium": 0}


def upgrade_cost(btype: int, level: int) -> tuple[int, int, int]:
    """Cost to go from `level` to `level + 1`."""
    rule = BUILDINGS[btype]
    f = rule.factor ** level
    return math.floor(rule.metal * f), math.floor(rule.crystal * f), math.floor(rule.deuterium * f)


def build_seconds(metal: int, crystal: int, robotics: int, nanite: int) -> int:
    hours = (metal + crystal) / (2500 * (1 + robotics) * 2 ** nanite)
    return max(1, math.floor(hours * 3600 / config.SPEED))


def teardown_cost(btype: int, level: int) -> tuple[int, int, int]:
    """Cost to go from `level` to `level - 1`: half of what that level cost to build."""
    metal, crystal, deuterium = upgrade_cost(btype, level - 1)
    return metal // 2, crystal // 2, deuterium // 2


def storage_capacity(level: int) -> int:
    return 5000 * math.floor(2.5 * math.exp(20 * level / 33))


def mine_output(btype: int, level: int) -> float:
    """Raw production per hour at 100 %, before the energy factor and speed."""
    base = {METAL_MINE: 30, CRYSTAL_MINE: 20, DEUTERIUM_MINE: 10}[btype]
    return base * level * 1.1 ** level


def mine_energy(btype: int, level: int) -> float:
    base = {METAL_MINE: 10, CRYSTAL_MINE: 10, DEUTERIUM_MINE: 20}[btype]
    return base * level * 1.1 ** level


def solar_energy(level: int) -> float:
    return 20 * level * 1.1 ** level


def fusion_energy(level: int, energy_tech: int) -> float:
    return 30 * level * (1.05 + 0.01 * energy_tech) ** level


def fusion_deuterium(level: int) -> float:
    return 10 * level * 1.1 ** level


# --- Research -----------------------------------------------------------------------------

LASER_TECH, ION_TECH, PLASMA_TECH = 2, 3, 5
COMBUSTION_DRIVE, IMPULSE_DRIVE, HYPERSPACE_DRIVE = 6, 7, 8
ESPIONAGE_TECH, ASTROPHYSICS = 9, 11
WEAPONS_TECH, SHIELDING_TECH, ARMOUR_TECH = 14, 15, 16
ULTRA_TEMPERATURE_POLYMERIZER, ANTI_GRAVITY, MISSILE_TECH = 18, 19, 22

# Research types the client handles elsewhere (krypton lab, flagship techs), not in the lab.
NOT_IN_LAB = (17, 20, 21)

RESEARCHES: dict[int, BuildingRule] = {
    ENERGY_TECH: BuildingRule("Energy Technology", 0, 800, 400, 2, {RESEARCH_LAB: 1}, {}),
    LASER_TECH: BuildingRule("Laser Technology", 200, 100, 0, 2, {RESEARCH_LAB: 1}, {ENERGY_TECH: 2}),
    ION_TECH: BuildingRule("Ion Technology", 1000, 300, 100, 2, {RESEARCH_LAB: 4},
                           {LASER_TECH: 5, ENERGY_TECH: 4}),
    HYPERSPACE_TECH: BuildingRule("Hyperspace Technology", 0, 4000, 2000, 2, {RESEARCH_LAB: 7},
                                  {ENERGY_TECH: 5, SHIELDING_TECH: 5}),
    PLASMA_TECH: BuildingRule("Plasma Technology", 2000, 4000, 1000, 2, {RESEARCH_LAB: 4},
                              {ENERGY_TECH: 8, LASER_TECH: 10, ION_TECH: 5}),
    COMBUSTION_DRIVE: BuildingRule("Combustion Drive", 400, 0, 600, 2, {RESEARCH_LAB: 1}, {ENERGY_TECH: 1}),
    IMPULSE_DRIVE: BuildingRule("Impulse Drive", 2000, 4000, 600, 2, {RESEARCH_LAB: 2}, {ENERGY_TECH: 1}),
    HYPERSPACE_DRIVE: BuildingRule("Hyperspace Drive", 10000, 20000, 6000, 2, {RESEARCH_LAB: 7},
                                   {HYPERSPACE_TECH: 3}),
    ESPIONAGE_TECH: BuildingRule("Espionage Technology", 200, 1000, 200, 2, {RESEARCH_LAB: 3}, {}),
    COMPUTER_TECH: BuildingRule("Computer Technology", 0, 400, 600, 2, {RESEARCH_LAB: 1}, {}),
    ASTROPHYSICS: BuildingRule("Astrophysics", 4000, 8000, 4000, 1.75, {RESEARCH_LAB: 3},
                               {ESPIONAGE_TECH: 4, IMPULSE_DRIVE: 3}),
    WEAPONS_TECH: BuildingRule("Weapons Technology", 800, 200, 0, 2, {RESEARCH_LAB: 4}, {}),
    SHIELDING_TECH: BuildingRule("Shielding Technology", 200, 600, 0, 2, {RESEARCH_LAB: 6}, {ENERGY_TECH: 3}),
    ARMOUR_TECH: BuildingRule("Armour Technology", 1000, 0, 0, 2, {RESEARCH_LAB: 2}, {}),
    ULTRA_TEMPERATURE_POLYMERIZER: BuildingRule("Ultra Temperature Polymerizer", 5000, 10000, 5000, 2,
                                                {RESEARCH_LAB: 8}, {PLASMA_TECH: 3}),
    ANTI_GRAVITY: BuildingRule("Anti-Gravity", 10000, 20000, 10000, 2, {RESEARCH_LAB: 12},
                               {HYPERSPACE_TECH: 8}),
    MISSILE_TECH: BuildingRule("Missile Technology", 5000, 5000, 2000, 2, {RESEARCH_LAB: 5}, {WEAPONS_TECH: 5}),
}


def research_cost(rtype: int, level: int) -> tuple[int, int, int]:
    """Cost to research `level + 1`."""
    rule = RESEARCHES[rtype]
    f = rule.factor ** level
    return math.floor(rule.metal * f), math.floor(rule.crystal * f), math.floor(rule.deuterium * f)


def research_seconds(metal: int, crystal: int, lab: int) -> int:
    hours = (metal + crystal) / (1000 * (1 + lab))
    return max(1, math.floor(hours * 3600 / config.SPEED))


# --- Ships and defense --------------------------------------------------------------------

LIGHT_FIGHTER, SMALL_CARGO, SOLAR_SATELLITE = 1, 9, 14
SMALL_SHIELD_DOME, LARGE_SHIELD_DOME = 21, 22

# Energy one solar satellite delivers (OGame: depends on the planet's temperature).
SOLAR_SATELLITE_ENERGY = 25

# Units that exist only once per planet.
UNIQUE_UNITS = (SMALL_SHIELD_DOME, LARGE_SHIELD_DOME)

# Per unit: costs and requirements. factor is unused (always 1).
UNITS: dict[int, BuildingRule] = {
    1: BuildingRule("Light Fighter", 3000, 1000, 0, 1, {SHIPYARD: 1}, {COMBUSTION_DRIVE: 1}),
    2: BuildingRule("Heavy Fighter", 6000, 4000, 0, 1, {SHIPYARD: 3}, {ARMOUR_TECH: 2, IMPULSE_DRIVE: 2}),
    3: BuildingRule("Cruiser", 20000, 7000, 2000, 1, {SHIPYARD: 5}, {IMPULSE_DRIVE: 4, ION_TECH: 2}),
    4: BuildingRule("Battleship", 45000, 15000, 0, 1, {SHIPYARD: 7}, {HYPERSPACE_DRIVE: 4}),
    5: BuildingRule("Battlecruiser", 30000, 40000, 15000, 1, {SHIPYARD: 8},
                    {HYPERSPACE_TECH: 5, HYPERSPACE_DRIVE: 5, LASER_TECH: 12}),
    6: BuildingRule("Bomber", 50000, 25000, 15000, 1, {SHIPYARD: 8}, {IMPULSE_DRIVE: 6, PLASMA_TECH: 5}),
    7: BuildingRule("Destroyer", 60000, 50000, 15000, 1, {SHIPYARD: 9},
                    {HYPERSPACE_DRIVE: 6, HYPERSPACE_TECH: 5}),
    8: BuildingRule("Deathstar", 5000000, 4000000, 1000000, 1, {SHIPYARD: 12},
                    {HYPERSPACE_DRIVE: 7, HYPERSPACE_TECH: 6, ANTI_GRAVITY: 1}),
    9: BuildingRule("Small Cargo", 2000, 2000, 0, 1, {SHIPYARD: 2}, {COMBUSTION_DRIVE: 2}),
    10: BuildingRule("Large Cargo", 6000, 6000, 0, 1, {SHIPYARD: 4}, {COMBUSTION_DRIVE: 6}),
    11: BuildingRule("Colony Ship", 10000, 20000, 10000, 1, {SHIPYARD: 4}, {IMPULSE_DRIVE: 3}),
    12: BuildingRule("Recycler", 10000, 6000, 2000, 1, {SHIPYARD: 4}, {COMBUSTION_DRIVE: 6, SHIELDING_TECH: 2}),
    13: BuildingRule("Espionage Probe", 0, 1000, 0, 1, {SHIPYARD: 3}, {COMBUSTION_DRIVE: 3, ESPIONAGE_TECH: 2}),
    14: BuildingRule("Solar Satellite", 0, 2000, 500, 1, {SHIPYARD: 1}, {}),
    15: BuildingRule("Rocket Launcher", 2000, 0, 0, 1, {SHIPYARD: 1}, {}),
    16: BuildingRule("Light Laser", 1500, 500, 0, 1, {SHIPYARD: 2}, {ENERGY_TECH: 1, LASER_TECH: 3}),
    17: BuildingRule("Heavy Laser", 6000, 2000, 0, 1, {SHIPYARD: 4}, {ENERGY_TECH: 3, LASER_TECH: 6}),
    18: BuildingRule("Gauss Cannon", 20000, 15000, 2000, 1, {SHIPYARD: 6},
                     {ENERGY_TECH: 6, WEAPONS_TECH: 3, SHIELDING_TECH: 1}),
    19: BuildingRule("Ion Cannon", 5000, 3000, 0, 1, {SHIPYARD: 4}, {ION_TECH: 4}),
    20: BuildingRule("Plasma Cannon", 50000, 50000, 30000, 1, {SHIPYARD: 8}, {PLASMA_TECH: 7}),
    21: BuildingRule("Small Shield Dome", 10000, 10000, 0, 1, {SHIPYARD: 1}, {SHIELDING_TECH: 2}),
    22: BuildingRule("Large Shield Dome", 50000, 50000, 0, 1, {SHIPYARD: 6}, {SHIELDING_TECH: 6}),
}

# Units the client offers elsewhere (krypton shop) or never lets players build.
SPECIAL_UNITS = {23: "Interceptor", 24: "Behemoth"}


def unit_cost(utype: int) -> tuple[int, int, int]:
    rule = UNITS[utype]
    return rule.metal, rule.crystal, rule.deuterium


def unit_seconds(utype: int, shipyard: int, nanite: int) -> int:
    metal, crystal, _ = unit_cost(utype)
    hours = (metal + crystal) / (2500 * (1 + shipyard) * 2 ** nanite)
    return max(1, math.floor(hours * 3600 / config.SPEED))
