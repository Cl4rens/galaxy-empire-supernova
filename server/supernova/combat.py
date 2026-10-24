"""Battles, as in OGame.

Every unit has weapons, shields and hull. A battle lasts at most 6 rounds. In each round every
unit fires at a random enemy unit; shots below 1 % of the target's shield are absorbed, the
shield takes damage first and is restored at the start of every round, and a unit whose hull
drops below 70 % may explode. Rapid fire lets a unit shoot again at certain targets.
Weapons, Shielding and Armour Technology add 10 % per level.

After the battle destroyed defense is rebuilt with 70 % chance per unit, and 30 % of the metal
and crystal of destroyed ships becomes a debris field.
"""

import random
from dataclasses import dataclass, field

from . import economy as eco

MAX_ROUNDS = 6
DEFENSE_REBUILD = 0.7
DEBRIS_SHARE = 0.3


@dataclass(frozen=True)
class CombatStats:
    weapon: int
    shield: int
    hull: int  # structural integrity (metal + crystal) / 10


def _hull(utype: int) -> int:
    rule = eco.UNITS.get(utype)
    if rule:
        return (rule.metal + rule.crystal) // 10
    return {23: 400, 24: 30000}.get(utype, 400)  # interceptor: like a light fighter; behemoth: a giant


STATS: dict[int, CombatStats] = {t: CombatStats(w, s, _hull(t)) for t, (w, s) in {
    1: (50, 10), 2: (150, 25), 3: (400, 50), 4: (1000, 200), 5: (700, 400), 6: (1000, 500),
    7: (2000, 500), 8: (200000, 50000), 9: (5, 10), 10: (5, 25), 11: (50, 100), 12: (1, 10),
    13: (0, 0), 14: (1, 1), 15: (80, 20), 16: (100, 25), 17: (250, 100), 18: (1100, 200),
    19: (150, 500), 20: (3000, 300), 21: (1, 2000), 22: (1, 10000), 23: (50, 10), 24: (2000, 500),
}.items()}

# shooter -> {target: rapid fire value}
RAPID_FIRE: dict[int, dict[int, int]] = {
    1: {13: 5, 14: 5},
    2: {9: 3, 13: 5, 14: 5},
    3: {1: 6, 13: 5, 14: 5, 15: 10},
    4: {13: 5, 14: 5},
    5: {2: 4, 3: 4, 4: 7, 9: 3, 10: 3, 13: 5, 14: 5},
    6: {13: 5, 14: 5, 15: 20, 16: 20, 17: 10, 19: 10},
    7: {5: 2, 13: 5, 14: 5, 16: 10},
    8: {1: 200, 2: 100, 3: 33, 4: 30, 5: 15, 6: 25, 7: 5, 9: 250, 10: 250, 11: 250, 12: 250,
        13: 1250, 14: 1250, 15: 200, 16: 200, 17: 100, 18: 50, 19: 100},
    9: {13: 5, 14: 5}, 10: {13: 5, 14: 5}, 11: {13: 5, 14: 5}, 12: {13: 5, 14: 5},
}


@dataclass
class Side:
    name: str
    units: dict[int, int]  # type -> count
    weapons: int = 0  # technology levels
    shielding: int = 0
    armour: int = 0
    attack_percent: int = 0  # krypton bonuses
    shield_percent: int = 0
    armour_percent: int = 0
    unit_percent: dict[int, tuple[int, int, int]] = field(default_factory=dict)  # ship modification

    def _extra(self, utype: int, index: int) -> float:
        return self.unit_percent.get(utype, (0, 0, 0))[index] / 100

    def weapon_of(self, utype: int) -> float:
        return STATS[utype].weapon * (self.weapon_factor() + self._extra(utype, 0))

    def shield_of(self, utype: int) -> float:
        return STATS[utype].shield * (self.shield_factor() + self._extra(utype, 1))

    def hull_of(self, utype: int) -> float:
        return STATS[utype].hull * (self.armour_factor() + self._extra(utype, 2))

    def weapon_factor(self) -> float:
        return 1 + 0.1 * self.weapons + self.attack_percent / 100

    def shield_factor(self) -> float:
        return 1 + 0.1 * self.shielding + self.shield_percent / 100

    def armour_factor(self) -> float:
        return 1 + 0.1 * self.armour + self.armour_percent / 100

    def count(self) -> int:
        return sum(self.units.values())


def _expand(side: Side) -> list[list]:
    """One entry per unit: [type, hull, shield]."""
    units = []
    for utype, n in side.units.items():
        units += [[utype, side.hull_of(utype), side.shield_of(utype)] for _ in range(n)]
    return units


def _fire(shooters: list, side: Side, targets: list, target_side: Side, rng: random.Random) -> tuple[int, int, int]:
    """One round of fire. Returns (shots, damage, absorbed)."""
    shots = damage = absorbed = 0
    if not targets:
        return 0, 0, 0
    for utype, _, _ in shooters:
        weapon = side.weapon_of(utype)
        rapid = RAPID_FIRE.get(utype, {})
        while True:
            target = targets[rng.randrange(len(targets))]
            shots += 1
            ttype, hull, shield = target
            max_shield = target_side.shield_of(ttype)
            if hull > 0 and weapon >= 0.01 * max_shield:
                hit = weapon
                if shield >= hit:
                    target[2] -= hit
                    absorbed += int(hit)
                else:
                    absorbed += int(shield)
                    target[2] = 0
                    target[1] -= hit - shield
                    damage += int(hit - shield)
                max_hull = STATS[ttype].hull * (1 + 0.1 * target_side.armour)
                if 0 < target[1] < 0.7 * max_hull and rng.random() < 1 - target[1] / max_hull:
                    target[1] = 0
            elif hull > 0:
                absorbed += int(weapon)
            r = rapid.get(ttype)
            if not r or rng.random() >= (r - 1) / r:
                break
    return shots, damage, absorbed


def _survivors(units: list) -> dict[int, int]:
    alive: dict[int, int] = {}
    for utype, hull, _ in units:
        if hull > 0:
            alive[utype] = alive.get(utype, 0) + 1
    return alive


def _fleet_list(units: dict[int, int]) -> list[dict]:
    return [{"fleet_type": t, "amount": n} for t, n in sorted(units.items()) if n]


def fight(attacker: Side, defender: Side, rng: random.Random | None = None) -> dict:
    """Run a battle. Returns rounds, survivors, winner ("attacker", "defender" or "draw")."""
    rng = rng or random.Random()
    att, dfn = _expand(attacker), _expand(defender)
    rounds = []
    for _ in range(MAX_ROUNDS):
        if not att or not dfn:
            break
        for unit in att:
            unit[2] = attacker.shield_of(unit[0])
        for unit in dfn:
            unit[2] = defender.shield_of(unit[0])
        a_shots, a_damage, a_absorbed = _fire(att, attacker, dfn, defender, rng)
        d_shots, d_damage, d_absorbed = _fire(dfn, defender, att, attacker, rng)
        att = [u for u in att if u[1] > 0]
        dfn = [u for u in dfn if u[1] > 0]
        rounds.append({
            "attack_attack_count": a_shots, "attack_attack_value": a_damage, "attack_shield_absorb": d_absorbed,
            "defend_attack_count": d_shots, "defend_attack_value": d_damage, "defend_shield_absorb": a_absorbed,
            "attack_survived_fleets": _fleet_list(_survivors(att)),
            "defend_survived_fleets": _fleet_list(_survivors(dfn)),
        })
    if att and not dfn:
        winner = "attacker"
    elif dfn and not att:
        winner = "defender"
    elif not att and not dfn:
        winner = "draw"
    else:
        winner = "draw"
    return {"rounds": rounds, "attacker_left": _survivors(att), "defender_left": _survivors(dfn), "winner": winner}


def losses(before: dict[int, int], after: dict[int, int]) -> dict[int, int]:
    return {t: n - after.get(t, 0) for t, n in before.items() if n - after.get(t, 0) > 0}


def rebuild_defense(lost: dict[int, int], rng: random.Random | None = None) -> dict[int, int]:
    """Destroyed defense units that are repaired after the battle."""
    rng = rng or random.Random()
    rebuilt = {}
    for utype, n in lost.items():
        if utype in eco.SHIPS:
            continue
        k = sum(1 for _ in range(n) if rng.random() < DEFENSE_REBUILD)
        if k:
            rebuilt[utype] = k
    return rebuilt


def debris(lost_ships: dict[int, int]) -> dict[str, int]:
    """Metal and crystal left by destroyed ships (not defense)."""
    metal = crystal = 0
    for utype, n in lost_ships.items():
        if utype in eco.SHIPS and utype in eco.UNITS:
            m, c, _ = eco.unit_cost(utype)
            metal += m * n
            crystal += c * n
    return {"metal": int(metal * DEBRIS_SHARE), "crystal": int(crystal * DEBRIS_SHARE)}


def plunder(resources: dict[str, float], capacity: int) -> dict[str, int]:
    """OGame: up to half of each resource, limited by free cargo space, filled evenly."""
    available = {r: int(resources[r]) // 2 for r in ("metal", "crystal", "deuterium")}
    take = {r: 0 for r in available}
    free = capacity
    # metal up to a third, crystal up to half of what is left, then deuterium, then the rest
    for res, share in (("metal", 1 / 3), ("crystal", 1 / 2), ("deuterium", 1)):
        amount = min(available[res], int(free * share)) if res != "deuterium" else min(available[res], free)
        take[res] += amount
        available[res] -= amount
        free -= amount
    for res in ("metal", "crystal"):
        amount = min(available[res], free // 2 if res == "metal" else free)
        take[res] += amount
        free -= amount
    return take
