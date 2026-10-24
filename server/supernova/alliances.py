"""Alliances: founding, applications, members and ranks.

The client keeps three fields in the empire:
- alliance_id: the alliance the player belongs to, or applied to
- alliance_application_id: != 0 while an application is pending
- alliance_membership_id: the player's membership (also identifies the president)
"""

import time

from . import db, mails

MEMBER, VICE_PRESIDENT, PRESIDENT = 0, 1, 2
MAX_MEMBERS = 50
FOUNDING_COST = 999  # of each resource, as the client shows it
ALLIANCE_DEPOT = 12


class AllianceError(Exception):
    """Shown to the player."""


def _conn():
    return db.connect()


def _set_empire(player_id: int, alliance_id: int, alliance_name: str, membership_id: int,
                application_id: int) -> None:
    with db.player_state(player_id) as state:
        state["empire"].update(alliance_id=alliance_id, alliance_name=alliance_name,
                               alliance_membership_id=membership_id, alliance_application_id=application_id)


def membership(player_id: int):
    return _conn().execute("SELECT * FROM alliance_members WHERE player_id = ?", (player_id,)).fetchone()


def _alliance(alliance_id: int):
    row = _conn().execute("SELECT * FROM alliances WHERE id = ?", (alliance_id,)).fetchone()
    if row is None:
        raise AllianceError("This alliance does not exist")
    return row


def _require_post(player_id: int, alliance_id: int, minimum: int) -> None:
    m = membership(player_id)
    if m is None or m["alliance_id"] != alliance_id or m["post"] < minimum:
        raise AllianceError("You are not allowed to do this")


# --- Founding and leaving -------------------------------------------------------------------


def create(player_id: int, name: str, description: str) -> dict:
    name = name.strip()
    if not 3 <= len(name) <= 20:
        raise AllianceError("Alliance names have 3-20 characters")
    if membership(player_id):
        raise AllianceError("You are already in an alliance")
    if _conn().execute("SELECT 1 FROM alliances WHERE lower(name) = lower(?)", (name,)).fetchone():
        raise AllianceError("This name is already taken")
    _pay_founding(player_id)
    conn = _conn()
    with db.lock(), conn:
        now = int(time.time())
        alliance_id = conn.execute(
            "INSERT INTO alliances (name, description, activity, creator_id, created_at, auto_in) "
            "VALUES (?, ?, '', ?, ?, 0)", (name, description[:500], player_id, now)).lastrowid
        membership_id = conn.execute(
            "INSERT INTO alliance_members (alliance_id, player_id, post, joined_at) VALUES (?, ?, ?, ?)",
            (alliance_id, player_id, PRESIDENT, now)).lastrowid
        conn.execute("DELETE FROM alliance_applications WHERE player_id = ?", (player_id,))
    _set_empire(player_id, alliance_id, name, membership_id, 0)
    return {"alliance_id": alliance_id, "alliance_name": name, "president_id": membership_id,
            "alliance_application_id": 0}


def _pay_founding(player_id: int) -> None:
    from . import planet as planet_rules
    with db.player_state(player_id) as state:
        planet_rules.tick(state["empire"], time.time())
        planets = state["empire"]["planets"]
        if not any(planet_rules.level(p, ALLIANCE_DEPOT) for p in planets):
            raise AllianceError("You need an alliance depot")
        planet = state["empire"]["main_planet"]
        if any(planet[r] < FOUNDING_COST for r in planet_rules.RESOURCES):
            raise AllianceError("Not enough resources")
        for r in planet_rules.RESOURCES:
            planet[r] -= FOUNDING_COST


def _remove_member(player_id: int) -> None:
    conn = _conn()
    with db.lock(), conn:
        conn.execute("DELETE FROM alliance_members WHERE player_id = ?", (player_id,))
    _set_empire(player_id, 0, "", 0, 0)


def leave(player_id: int) -> None:
    m = membership(player_id)
    if m is None:
        raise AllianceError("You are not in an alliance")
    if m["post"] == PRESIDENT:
        others = _conn().execute("SELECT player_id FROM alliance_members WHERE alliance_id = ? AND player_id != ? "
                                 "ORDER BY post DESC, joined_at", (m["alliance_id"], player_id)).fetchall()
        if others:  # hand the alliance to the next in line
            conn = _conn()
            with db.lock(), conn:
                conn.execute("UPDATE alliance_members SET post = ? WHERE player_id = ?",
                             (PRESIDENT, others[0]["player_id"]))
        else:
            dissolve(player_id, m["alliance_id"])
            return
    _remove_member(player_id)


def dissolve(player_id: int, alliance_id: int) -> None:
    _require_post(player_id, alliance_id, PRESIDENT)
    members = [r["player_id"] for r in _conn().execute(
        "SELECT player_id FROM alliance_members WHERE alliance_id = ?", (alliance_id,))]
    applicants = [r["player_id"] for r in _conn().execute(
        "SELECT player_id FROM alliance_applications WHERE alliance_id = ?", (alliance_id,))]
    conn = _conn()
    with db.lock(), conn:
        conn.execute("DELETE FROM alliance_members WHERE alliance_id = ?", (alliance_id,))
        conn.execute("DELETE FROM alliance_applications WHERE alliance_id = ?", (alliance_id,))
        conn.execute("DELETE FROM alliances WHERE id = ?", (alliance_id,))
    for pid in members + applicants:
        _set_empire(pid, 0, "", 0, 0)


def update_texts(player_id: int, alliance_id: int, description: str | None, activity: str | None) -> None:
    _require_post(player_id, alliance_id, VICE_PRESIDENT)
    conn = _conn()
    with db.lock(), conn:
        if description is not None:
            conn.execute("UPDATE alliances SET description = ? WHERE id = ?", (description[:500], alliance_id))
        if activity is not None:
            conn.execute("UPDATE alliances SET activity = ? WHERE id = ?", (activity[:500], alliance_id))


def set_auto_join(player_id: int, alliance_id: int, on: bool) -> int:
    _require_post(player_id, alliance_id, VICE_PRESIDENT)
    conn = _conn()
    with db.lock(), conn:
        conn.execute("UPDATE alliances SET auto_in = ? WHERE id = ?", (int(on), alliance_id))
    return int(on)


# --- Applications ---------------------------------------------------------------------------


def _join(player_id: int, alliance_id: int) -> None:
    alliance = _alliance(alliance_id)
    count = _conn().execute("SELECT COUNT(*) FROM alliance_members WHERE alliance_id = ?", (alliance_id,)).fetchone()[0]
    if count >= MAX_MEMBERS:
        raise AllianceError("This alliance is full")
    conn = _conn()
    with db.lock(), conn:
        conn.execute("DELETE FROM alliance_applications WHERE player_id = ?", (player_id,))
        membership_id = conn.execute(
            "INSERT INTO alliance_members (alliance_id, player_id, post, joined_at) VALUES (?, ?, ?, ?)",
            (alliance_id, player_id, MEMBER, int(time.time()))).lastrowid
    _set_empire(player_id, alliance_id, alliance["name"], membership_id, 0)


def apply(player_id: int, alliance_id: int) -> dict:
    alliance = _alliance(alliance_id)
    if membership(player_id):
        raise AllianceError("You are already in an alliance")
    if alliance["auto_in"]:
        _join(player_id, alliance_id)
        return {"application_id": 0, "alliance": {"auto_in": 1}}
    conn = _conn()
    with db.lock(), conn:
        conn.execute("DELETE FROM alliance_applications WHERE player_id = ?", (player_id,))
        application_id = conn.execute(
            "INSERT INTO alliance_applications (alliance_id, player_id, created_at) VALUES (?, ?, ?)",
            (alliance_id, player_id, int(time.time()))).lastrowid
    _set_empire(player_id, alliance_id, alliance["name"], 0, application_id)
    president = _conn().execute("SELECT player_id FROM alliance_members WHERE alliance_id = ? AND post = ?",
                                (alliance_id, PRESIDENT)).fetchone()
    if president:
        name = db.load_state(player_id)["username"]
        mails.send(president["player_id"], mails.SYSTEM, alliance["name"], "", "New application",
                   f"{name} wants to join your alliance.")
    return {"application_id": application_id,
            "alliance": {"alliance_id": alliance_id, "alliance_name": alliance["name"], "auto_in": 0}}


def _application(application_id: int):
    row = _conn().execute("SELECT * FROM alliance_applications WHERE id = ?", (application_id,)).fetchone()
    if row is None:
        raise AllianceError("This application no longer exists")
    return row


def cancel_application(player_id: int, application_id: int) -> None:
    app = _application(application_id)
    if app["player_id"] != player_id:
        raise AllianceError("You are not allowed to do this")
    conn = _conn()
    with db.lock(), conn:
        conn.execute("DELETE FROM alliance_applications WHERE id = ?", (application_id,))
    _set_empire(player_id, 0, "", 0, 0)


def decide(player_id: int, application_id: int, approve: bool) -> int:
    """Approve or reject an application. Returns the alliance id."""
    app = _application(application_id)
    _require_post(player_id, app["alliance_id"], VICE_PRESIDENT)
    alliance = _alliance(app["alliance_id"])
    if approve:
        _join(app["player_id"], app["alliance_id"])
        text = f"Welcome to {alliance['name']}!"
    else:
        conn = _conn()
        with db.lock(), conn:
            conn.execute("DELETE FROM alliance_applications WHERE id = ?", (application_id,))
        _set_empire(app["player_id"], 0, "", 0, 0)
        text = f"{alliance['name']} has declined your application."
    mails.send(app["player_id"], mails.SYSTEM, alliance["name"], "", "Your application", text)
    return app["alliance_id"]


# --- Members --------------------------------------------------------------------------------


def _member(membership_id: int):
    row = _conn().execute("SELECT * FROM alliance_members WHERE id = ?", (membership_id,)).fetchone()
    if row is None:
        raise AllianceError("This member no longer exists")
    return row


def kick_or_leave(player_id: int, membership_id: int) -> int:
    """The client uses the same request to leave (own membership) and to kick a member."""
    target = _member(membership_id)
    if target["player_id"] == player_id:
        leave(player_id)
        return target["alliance_id"]
    _require_post(player_id, target["alliance_id"], VICE_PRESIDENT)
    if target["post"] >= membership(player_id)["post"]:
        raise AllianceError("You are not allowed to do this")
    _remove_member(target["player_id"])
    return target["alliance_id"]


def change_post(player_id: int, membership_id: int, post: int) -> int:
    target = _member(membership_id)
    _require_post(player_id, target["alliance_id"], PRESIDENT)
    if post not in (MEMBER, VICE_PRESIDENT, PRESIDENT) or target["player_id"] == player_id:
        raise AllianceError("You are not allowed to do this")
    conn = _conn()
    with db.lock(), conn:
        if post == PRESIDENT:  # hand over the presidency
            conn.execute("UPDATE alliance_members SET post = ? WHERE player_id = ?", (VICE_PRESIDENT, player_id))
        conn.execute("UPDATE alliance_members SET post = ? WHERE id = ?", (post, membership_id))
    return target["alliance_id"]


# --- Views ----------------------------------------------------------------------------------


def _members(alliance_id: int) -> list:
    return _conn().execute(
        "SELECT m.*, p.username FROM alliance_members m JOIN players p ON p.id = m.player_id "
        "WHERE m.alliance_id = ? ORDER BY m.post DESC, m.joined_at", (alliance_id,)).fetchall()


def _scores() -> dict[int, int]:
    from . import ranking
    return {p["id"]: p["score"] for p in ranking._players()}


def _alliance_ranking() -> list[dict]:
    scores = _scores()
    rows = []
    for a in _conn().execute("SELECT * FROM alliances"):
        members = _members(a["id"])
        president = next((m for m in members if m["post"] == PRESIDENT), None)
        rows.append({"alliance_id": a["id"], "alliance_name": a["name"], "member_count": len(members),
                     "score": sum(scores.get(m["player_id"], 0) for m in members),
                     "president_name": president["username"] if president else "",
                     "alliance_description": a["description"], "row": a})
    rows.sort(key=lambda r: (-r["score"], r["alliance_id"]))
    for i, r in enumerate(rows, start=1):
        r["alliance_rank"] = i
    return rows


def listing(keyword: str = "") -> list[dict]:
    rows = _alliance_ranking()
    if keyword:
        rows = [r for r in rows if keyword.lower() in r["alliance_name"].lower()]
    return [{k: v for k, v in r.items() if k != "row"} for r in rows]


def details(alliance_id: int, player_id: int) -> dict:
    entry = next((r for r in _alliance_ranking() if r["alliance_id"] == alliance_id), None)
    if entry is None:
        raise AllianceError("This alliance does not exist")
    a = entry["row"]
    members = _members(alliance_id)
    president = next((m for m in members if m["post"] == PRESIDENT), None)
    mine = membership(player_id)
    applications = _conn().execute("SELECT COUNT(*) FROM alliance_applications WHERE alliance_id = ?",
                                   (alliance_id,)).fetchone()[0]
    return {
        "alliance_id": alliance_id,
        "alliance_name": a["name"],
        "alliance_description": a["description"],
        "alliance_activity": a["activity"],
        "president_name": president["username"] if president else "",
        "president_id": president["id"] if president else 0,
        "vice_president_ids": [m["id"] for m in members if m["post"] == VICE_PRESIDENT],
        "creator_id": a["creator_id"],
        "alliance_membership_id": mine["id"] if mine and mine["alliance_id"] == alliance_id else 0,
        "member_count": len(members),
        "application_count": applications,
        "alliance_rank": entry["alliance_rank"],
        "alliance_fight_rank": entry["alliance_rank"],
        "score": entry["score"],
        "alliance_score": entry["score"],
        "fight_score": 0,
        "morale_ratio": 0,
        "auto_in": a["auto_in"],
        # Alliance treasury (credits / dark matter) is not implemented.
        "bag": [{"bag_type": "2", "number": 0}, {"bag_type": "1", "number": 0}],
    }


def members_view(alliance_id: int) -> list[dict]:
    scores = _scores()
    return [{"membership_id": m["id"], "user_id": m["player_id"], "username": m["username"], "post": m["post"],
             "join_at": time.strftime("%Y-%m-%d", time.localtime(m["joined_at"])),
             "score": scores.get(m["player_id"], 0)} for m in _members(alliance_id)]


def applications_view(alliance_id: int) -> list[dict]:
    scores = _scores()
    return [{"application_id": a["id"], "user_id": a["player_id"], "username": a["username"],
             "user_score": scores.get(a["player_id"], 0)}
            for a in _conn().execute(
                "SELECT a.*, p.username FROM alliance_applications a JOIN players p ON p.id = a.player_id "
                "WHERE a.alliance_id = ? ORDER BY a.id", (alliance_id,))]


def alliance_mail(player_id: int, subject: str, body: str) -> None:
    m = membership(player_id)
    if m is None:
        raise AllianceError("You are not in an alliance")
    alliance = _alliance(m["alliance_id"])
    sender = db.load_state(player_id)["username"]
    for member in _members(m["alliance_id"]):
        mails.send(member["player_id"], mails.ALLIANCE, sender, alliance["name"], subject, body)
