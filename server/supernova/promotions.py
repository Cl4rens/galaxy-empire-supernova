"""Invitation codes. Every player has a code (Settings > Invite friends). A new player can redeem
a friend's code once, in the first days of their account; both get a reward. The numbers are
Supernova's own."""

import time

from . import db, mails
from . import planet as planet_rules

DAYS = 7  # a code can be redeemed this long after the account was created
REWARD_DARK_MATTER = 30
NEWCOMER_RESOURCES = {"metal": 50_000, "crystal": 30_000, "deuterium": 10_000}


class PromotionError(planet_rules.GameError):
    pass


def code_of(player_id: int) -> str:
    return f"SN{player_id:06d}"


def _owner(code: str) -> int | None:
    code = code.strip().upper()
    if not (code.startswith("SN") and code[2:].isdigit()):
        return None
    row = db.connect().execute("SELECT id FROM players WHERE id = ?", (int(code[2:]),)).fetchone()
    return row["id"] if row else None


def apply_code(player_id: int, code: str, now: float | None = None) -> str:
    now = time.time() if now is None else now
    inviter = _owner(code)
    if inviter is None:
        raise PromotionError("This invitation code does not exist")
    if inviter == player_id:
        raise PromotionError("You cannot use your own invitation code")
    created = db.connect().execute("SELECT created_at FROM players WHERE id = ?", (player_id,)).fetchone()["created_at"]
    if now - created > DAYS * 86400:
        raise PromotionError(f"Invitation codes can only be used in the first {DAYS} days")
    with db.player_state(player_id) as state:
        if state.get("invited_by"):
            raise PromotionError("You have already used an invitation code")
        state["invited_by"] = inviter
        empire = state["empire"]
        empire["dark_matter_count"] = empire.get("dark_matter_count", 0) + REWARD_DARK_MATTER
        for res, n in NEWCOMER_RESOURCES.items():
            empire["planets"][0][res] += n
        name = state["username"]
    with db.player_state(inviter) as state:
        state["empire"]["dark_matter_count"] = state["empire"].get("dark_matter_count", 0) + REWARD_DARK_MATTER
        inviter_name = state["username"]
    mails.send(inviter, mails.SYSTEM, "Supernova", inviter_name, "Your invitation was used",
               f"{name} joined with your invitation code. You received {REWARD_DARK_MATTER} dark matter.")
    return (f"Welcome! You and {inviter_name} received {REWARD_DARK_MATTER} dark matter; your home planet got "
            + ", ".join(f"{n:,} {res}" for res, n in NEWCOMER_RESOURCES.items()) + ".")
