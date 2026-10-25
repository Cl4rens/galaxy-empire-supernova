"""Chests: the client's Facebook chest screen, played with alliance members instead of
Facebook friends (the patcher replaces the screen's script, see tools/client_scripts).

- Every player has a chest. It fills by itself (1 point every 6 hours) and faster when alliance
  members help: a player asks for help, every member can help once a day (+1 each).
- A full chest is opened for rewards that grow with the chest level; every opening gives chest
  experience, and enough experience raises the level (at most 10).
- The extra bar fills when the player helps others (+1) and when somebody joins with the
  player's invitation code (+3). When it is full, the next opening also gives the extra rewards.

All numbers are Supernova's own.
"""

from . import alliances, db, ecm, items
from . import planet as planet_rules

MAX_LEVEL = 10
SELF_FILL_SECONDS = 6 * 3600
HELP_REQUEST_SECONDS = 86400
EXTRA_NEED, INVITE_EXTRA = 10, 3
EXTRA_REWARD = [("dark_matter", 30), ("chips", 5), (3103, 1), (2103, 1)]  # kind, amount
ICONS = {"metal": "icons/item_res_04.png", "crystal": "icons/item_res_03.png",
         "deuterium": "icons/item_res_01.png", "dark_matter": "icons/item_res_05.png",
         "chips": "icons/item_res_12.png"}
NAMES = {"metal": "metal", "crystal": "crystal", "deuterium": "deuterium", "dark_matter": "dark matter",
         "chips": "ECM chips"}


class ChestError(planet_rules.GameError):
    pass


def need_progress(level: int) -> int:
    return 4 + level


def level_up_exp(level: int) -> int:
    return 2 * level


def rewards(level: int) -> list[tuple]:
    out = [("metal", 30_000 * level), ("crystal", 20_000 * level), ("deuterium", 8_000 * level)]
    if level >= 3:
        out.append(("chips", level // 2))
    if level >= 5:
        out.append(("dark_matter", 5 * (level - 3)))
    return out


def _data(state: dict, now: float) -> dict:
    c = state.setdefault("chest", {"level": 1, "exp": 0, "progress": 0, "filled_at": int(now), "extra": 0,
                                    "asked_at": 0, "helped": {}, "invites": 0})
    gained = int((now - c["filled_at"]) // SELF_FILL_SECONDS)
    if gained:
        c["filled_at"] += gained * SELF_FILL_SECONDS
        c["progress"] = min(need_progress(c["level"]), c["progress"] + gained)
    return c


def _day(now: float) -> int:
    return int(now // 86400)


def _members(pid: int) -> list[int]:
    m = alliances.membership(pid)
    if m is None:
        return []
    return [r["player_id"] for r in db.connect().execute(
        "SELECT player_id FROM alliance_members WHERE alliance_id = ? AND player_id != ? ORDER BY joined_at",
        (m["alliance_id"], pid))]


def _asks_for_help(other: dict, helper_id: int, now: float) -> bool:
    c = other.get("chest")
    return bool(c) and now - c["asked_at"] < HELP_REQUEST_SECONDS \
        and c["helped"].get(str(helper_id)) != _day(now) and c["progress"] < need_progress(c["level"])


def _gift_view(kind, amount: int) -> list:
    """[kind, amount, name, picture] as the extra reward icons read it (index 4 in Lua)."""
    if isinstance(kind, int):
        i = items.item(kind)
        return [kind, amount, i.name, f"icons/{i.icon}"]
    return [kind, amount, NAMES[kind], ICONS[kind]]


def view(state: dict, now: float) -> dict:
    pid = state["empire"]["user_id"]
    c = _data(state, now)
    friends, requests = [], []
    for other in _members(pid):
        o = db.load_state(other)
        friends.append({"id": other, "name": o["username"]})
        if _asks_for_help(o, pid, now):
            requests.append(other)
    return {"chest_user_info": {
        "chest_level": c["level"], "chest_exp": c["exp"], "progress": c["progress"],
        "help_config": {"level_up_exp": level_up_exp(c["level"]), "need_progress": need_progress(c["level"])},
        "extra_level": 1, "extra_progress": min(c["extra"], EXTRA_NEED),
        "extra_config": {"need_progress": EXTRA_NEED, "client_gift": [_gift_view(k, n) for k, n in EXTRA_REWARD]},
        "requests": requests, "asked": now - c["asked_at"] < HELP_REQUEST_SECONDS,
        "next_fill": c["filled_at"] + SELF_FILL_SECONDS},
        "friends": friends, "invite_code": f"SN{pid:06d}"}


def _grant(state: dict, gifts: list[tuple]) -> str:
    empire = state["empire"]
    home = empire["planets"][0]
    for kind, n in gifts:
        if kind in ("metal", "crystal", "deuterium"):
            home[kind] += n
        elif kind == "dark_matter":
            empire["dark_matter_count"] = empire.get("dark_matter_count", 0) + n
        elif kind == "chips":
            ecm.add_chips(state, n)
        else:
            items.add(state, kind, n)
    return ", ".join(f"{n:,} {items.item(k).name if isinstance(k, int) else NAMES[k]}" for k, n in gifts)


def open_chest(state: dict, now: float) -> str:
    c = _data(state, now)
    if c["progress"] < need_progress(c["level"]):
        raise ChestError(f"The chest opens at {need_progress(c['level'])} points; it has {c['progress']}.")
    gifts = rewards(c["level"])
    extra = c["extra"] >= EXTRA_NEED
    if extra:
        gifts += EXTRA_REWARD
        c["extra"] -= EXTRA_NEED
    text = _grant(state, gifts)
    c["progress"], c["asked_at"], c["helped"] = 0, 0, {}
    c["exp"] += 1
    if c["level"] < MAX_LEVEL and c["exp"] >= level_up_exp(c["level"]):
        c["exp"] -= level_up_exp(c["level"])
        c["level"] += 1
        text += f". Your chest is now level {c['level']}"
    return ("Chest opened" + (" with extra rewards" if extra else "") + f": {text}.")


def ask_for_help(state: dict, now: float) -> None:
    if not _members(state["empire"]["user_id"]):
        raise ChestError("Join an alliance to ask its members for help.")
    c = _data(state, now)
    if c["progress"] >= need_progress(c["level"]):
        raise ChestError("Your chest is full: open it!")
    c["asked_at"] = int(now)


def help_member(state: dict, target_id: int, now: float) -> str:
    """Help an alliance member; called with the helper's state (the target is changed here)."""
    pid = state["empire"]["user_id"]
    if target_id not in _members(pid):
        raise ChestError("You can only help members of your alliance.")
    with db.player_state(target_id) as other:
        if not _asks_for_help(other, pid, now):
            raise ChestError("This member does not need help right now.")
        c = _data(other, now)
        c["helped"][str(pid)] = _day(now)
        c["progress"] = min(need_progress(c["level"]), c["progress"] + 1)
        name = other["username"]
    mine = _data(state, now)
    mine["extra"] = min(EXTRA_NEED * 3, mine["extra"] + 1)
    return name


def invited(state: dict, now: float) -> None:
    """Somebody joined with this player's invitation code."""
    c = _data(state, now)
    c["extra"] = min(EXTRA_NEED * 3, c["extra"] + INVITE_EXTRA)
