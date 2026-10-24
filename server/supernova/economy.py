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
