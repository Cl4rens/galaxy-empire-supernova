"""Newbie protection, as in OGame:

- Players below config.NEWBIE_POINTS can only be attacked by other players below it.
- A player cannot attack someone with less than 1/RATIO of their own points, and cannot
  attack someone with more than RATIO times their points, unless the weaker side has at least
  config.STRONG_POINTS.

Values for the client's can_attack: 0 attack possible, 1 the target is too strong, 2 the
target is too weak (protected).
"""

from . import config, db, ranking

ATTACK_OK, TARGET_TOO_STRONG, TARGET_TOO_WEAK = 0, 1, 2
RATIO = 5


def points_of(player_id: int) -> int:
    return ranking.points(db.load_state(player_id))["score"]


def rule(attacker_points: int, target_points: int) -> int:
    if target_points < config.NEWBIE_POINTS <= attacker_points:
        return TARGET_TOO_WEAK
    if attacker_points < config.NEWBIE_POINTS <= target_points:
        return TARGET_TOO_STRONG
    if target_points * RATIO < attacker_points and target_points < config.STRONG_POINTS:
        return TARGET_TOO_WEAK
    if attacker_points * RATIO < target_points and attacker_points < config.STRONG_POINTS:
        return TARGET_TOO_STRONG
    return ATTACK_OK


def can_attack(attacker_id: int, target_id: int) -> int:
    if attacker_id == target_id:
        return ATTACK_OK
    return rule(points_of(attacker_id), points_of(target_id))


MESSAGES = {TARGET_TOO_WEAK: "This player is under newbie protection.",
            TARGET_TOO_STRONG: "This player is too strong for you while you are under newbie protection."}
