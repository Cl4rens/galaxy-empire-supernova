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


# where the wiki's armour is not (metal + crystal) / 10; the Reaper's values are Supernova's own
HULL = {13: 100, 23: 160_000, 24: 350_000, 25: 150_000, 26: 450_000, 27: 120_000, 28: 1, 29: 400_000}

DEVOURER, PROTON_CANNON, ANNIHILATOR, REAPER = 25, 27, 28, 29
LARGE_HULL = 10_000  # proton cannons do a fifth of their damage to units below this hull
ANNIHILATOR_DAMAGE = 500_000  # per explosion, shared by the enemy's unit types
ANNIHILATOR_MULTIPLIER = {26: 5, 25: 4, 23: 4, 24: 3}  # wiki: bonus damage against these
ANNIHILATOR_TRIGGERS = (23, 24, 25, 26)  # explosions go on while the enemy has two of these

# Missiles (client texts and battle report; the numbers are Supernova's own)
INTERCEPT_MISSILE, INTERPLANETARY_MISSILE = 30, 31
MISSILES = (INTERCEPT_MISSILE, INTERPLANETARY_MISSILE)  # they never take part in the rounds
INTERCEPT_TARGETS = (ANNIHILATOR, INTERPLANETARY_MISSILE, 24, 23, 25, 26)  # in this order, one each
MISSILE_DAMAGE = 1_000_000  # an interplanetary missile that finds no Reaper hits the biggest ship type
DEFENSE_LAUNCH_CHANCE = 0.5  # a planet's interplanetary missiles fire at an attack with this chance


def intercept_limit(missile_tech: int) -> int:
    """Intercept missiles one battle can use (the client caps orders the same way)."""
    return min(170, 20 + 3 * missile_tech) if missile_tech else 0


def interplanetary_limit(missile_tech: int) -> int:
    return min(100, 2 * missile_tech)


def _hull(utype: int) -> int:
    if utype in HULL:
        return HULL[utype]
    rule = eco.UNITS[utype]
    return (rule.metal + rule.crystal) // 10


STATS: dict[int, CombatStats] = {t: CombatStats(w, s, _hull(t)) for t, (w, s) in {
    1: (50, 10), 2: (150, 25), 3: (400, 50), 4: (1000, 200), 5: (700, 400), 6: (1000, 500),
    7: (2000, 500), 8: (200000, 50000), 9: (5, 10), 10: (5, 25), 11: (50, 100), 12: (1, 10),
    13: (0, 0), 14: (1, 1), 15: (80, 20), 16: (100, 25), 17: (250, 100), 18: (1100, 200),
    19: (150, 500), 20: (3000, 300), 21: (1, 2000), 22: (1, 10000), 23: (7000, 1500), 24: (5500, 4000), 30: (0, 0), 31: (0, 0),
    25: (6500, 1500), 26: (5000, 3000), 27: (5000, 5000), 28: (0, 1), 29: (8000, 3000),
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
    19: {24: 5},
    23: {7: 10, 13: 5, 14: 5, 15: 10},
    24: {5: 5, 7: 10, 13: 10, 17: 5, 18: 5, 23: 10},
    25: {1: 20, 10: 10, 23: 5, 24: 5},
    26: {1: 10, 7: 5, 10: 15, 24: 5, 25: 5},
    27: {23: 12, 25: 12, 26: 12},
    29: {7: 10, 24: 5, 25: 5, 26: 5},
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
    missile_tech: int = 0  # Combined Guidance Tech level
    defense_save: int = 0  # percent of the otherwise lost defense an engineer saves

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
    largest = None  # Reapers go for the biggest ships first
    for utype, _, _ in shooters:
        weapon = side.weapon_of(utype)
        if weapon <= 0:
            continue
        rapid = RAPID_FIRE.get(utype, {})
        while True:
            if utype == REAPER:
                if largest is None:
                    top = max(STATS[t[0]].hull for t in targets)
                    largest = [t for t in targets if STATS[t[0]].hull == top]
                target = largest[rng.randrange(len(largest))]
            else:
                target = targets[rng.randrange(len(targets))]
            shots += 1
            ttype, hull, shield = target
            max_shield = target_side.shield_of(ttype)
            hit = weapon
            if utype == PROTON_CANNON and STATS[ttype].hull < LARGE_HULL:
                hit = weapon / 5
            pierce = utype == DEVOURER  # its shots pass through shields
            if hull > 0 and (pierce or hit >= 0.01 * max_shield):
                if pierce:
                    target[1] -= hit
                    damage += int(hit)
                elif shield >= hit:
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


def _with_units(side: Side, units: dict[int, int]) -> Side:
    return Side(side.name, units, side.weapons, side.shielding, side.armour, side.attack_percent,
                side.shield_percent, side.armour_percent, side.unit_percent, side.missile_tech, side.defense_save)


def intercept(attacker: dict[int, int], defender: Side) -> dict:
    """The defender's intercept missiles stop Annihilators, interplanetary missiles and advanced
    ships, one each, before the battle. Changes `attacker`; returns the report part."""
    stock = defender.units.get(INTERCEPT_MISSILE, 0)
    left = min(stock, intercept_limit(defender.missile_tech))
    stopped = []
    for t in INTERCEPT_TARGETS:
        n = min(left, attacker.get(t, 0))
        if n:
            attacker[t] -= n
            left -= n
            stopped.append({"fleet_type": t, "amount": n})
    used = min(stock, intercept_limit(defender.missile_tech)) - left
    return {"origin": stock, "cost": used, "intercept": stopped}


def launch(shooter: Side, own: dict[int, int], target: Side, enemy: dict[int, int], fire: bool) -> dict:
    """Interplanetary missiles: each destroys one Reaper or, without Reapers, MISSILE_DAMAGE worth
    of the enemy's biggest ship type. Changes `own` and `enemy`; returns the report part."""
    stock = own.get(INTERPLANETARY_MISSILE, 0)
    count = min(stock, interplanetary_limit(shooter.missile_tech)) if fire else 0
    banned: dict[int, int] = {}
    for _ in range(count):
        if enemy.get(REAPER):
            enemy[REAPER] -= 1
            banned[REAPER] = banned.get(REAPER, 0) + 1
            continue
        ships = [t for t, n in enemy.items() if n > 0 and t in eco.SHIPS and t not in MISSILES]
        if not ships:
            break
        t = max(ships, key=lambda u: STATS[u].hull)
        killed = min(enemy[t], max(1, int(MISSILE_DAMAGE * shooter.weapon_factor() // target.hull_of(t))))
        enemy[t] -= killed
        banned[t] = banned.get(t, 0) + killed
    own[INTERPLANETARY_MISSILE] = stock - count
    return {"origin": stock, "cost": count, "ban": [{"fleet_type": t, "amount": n} for t, n in banned.items()]}


def annihilate(attacker: Side, defender: Side) -> tuple[dict[int, int], dict[int, int], int]:
    """The attacker's Annihilators explode before the battle, one after the other, as long as the
    defender has at least two kinds of advanced ships (interceptors, Behemoths, Devourers,
    Titanenergetic Frigates). Each explosion hits every enemy unit type except Reapers; the more
    types, the less damage each takes. Defenders' Annihilators never explode.
    Returns (attacker units left, defender units left, explosions)."""
    att, dfn = dict(attacker.units), dict(defender.units)
    explosions = 0
    while att.get(ANNIHILATOR, 0) > 0 and sum(1 for t in ANNIHILATOR_TRIGGERS if dfn.get(t)) >= 2:
        types = [t for t, n in dfn.items() if n > 0 and t != REAPER]
        share = ANNIHILATOR_DAMAGE * attacker.weapon_factor() / len(types)
        for t in types:
            killed = max(1, int(share * ANNIHILATOR_MULTIPLIER.get(t, 1) // defender.hull_of(t)))
            dfn[t] = max(0, dfn[t] - killed)
        att[ANNIHILATOR] -= 1
        explosions += 1
    return ({t: n for t, n in att.items() if n > 0}, {t: n for t, n in dfn.items() if n > 0}, explosions)


def fight(attacker: Side, defender: Side, rng: random.Random | None = None) -> dict:
    """Run a battle. Returns rounds, survivors, winner ("attacker", "defender" or "draw")."""
    rng = rng or random.Random()
    # before the battle: intercept missiles, interplanetary missiles, Annihilators
    att_units, dfn_units = dict(attacker.units), dict(defender.units)
    intercepted = intercept(att_units, defender)
    if intercepted["cost"]:
        dfn_units[INTERCEPT_MISSILE] -= intercepted["cost"]
    missiles = {"attacker_interplanetary_missile_info": launch(attacker, att_units, defender, dfn_units, True),
                "defender_interplanetary_missile_info": launch(defender, dfn_units, attacker, att_units,
                                                               rng.random() < DEFENSE_LAUNCH_CHANCE)}
    bombers = att_units.get(ANNIHILATOR, 0)
    before = dict(dfn_units)
    att_units, dfn_units, explosions = annihilate(_with_units(attacker, att_units), _with_units(defender, dfn_units))
    destruction = {"self_count": bombers, "left_count": att_units.get(ANNIHILATOR, 0),
                   "attack_result": {str(t): n - dfn_units.get(t, 0) for t, n in before.items()
                                     if n - dfn_units.get(t, 0) > 0}}
    idle_att = {t: n for t, n in att_units.items() if t in MISSILES and n > 0}  # missiles do not fight
    idle_dfn = {t: n for t, n in dfn_units.items() if t in MISSILES and n > 0}
    att = _expand(_with_units(attacker, {t: n for t, n in att_units.items() if t not in MISSILES}))
    dfn = _expand(_with_units(defender, {t: n for t, n in dfn_units.items() if t not in MISSILES}))
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
    return {"rounds": rounds, "attacker_left": {**_survivors(att), **idle_att},
            "defender_left": {**_survivors(dfn), **idle_dfn}, "winner": winner, "explosions": explosions,
            "intercept_missile_info": intercepted, "interplanetary_missile_info": missiles,
            "self_destruction": destruction}


def losses(before: dict[int, int], after: dict[int, int]) -> dict[int, int]:
    return {t: n - after.get(t, 0) for t, n in before.items() if n - after.get(t, 0) > 0}


def rebuild_defense(lost: dict[int, int], rng: random.Random | None = None, save: int = 0) -> dict[int, int]:
    """Destroyed defense units that are repaired after the battle; `save` percent of the rest
    is saved too (engineer)."""
    rng = rng or random.Random()
    chance = DEFENSE_REBUILD + (1 - DEFENSE_REBUILD) * save / 100
    rebuilt = {}
    for utype, n in lost.items():
        if utype in eco.SHIPS or utype in MISSILES:  # used missiles have to be built again
            continue
        k = sum(1 for _ in range(n) if rng.random() < chance)
        if k:
            rebuilt[utype] = k
    return rebuilt


def debris(lost_ships: dict[int, int]) -> dict[str, int]:
    """Metal and crystal left by destroyed ships (not defense)."""
    metal = crystal = 0
    for utype, n in lost_ships.items():
        if utype in eco.SHIPS and utype in eco.UNITS and utype not in MISSILES:
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
