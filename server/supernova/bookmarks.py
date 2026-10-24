"""Bookmarks: planets a player saved in the solar system view. Kept in the player's state."""

from . import db
from . import planet as planet_rules

MAX_BOOKMARKS = 50


def _bookmarks(state: dict) -> list[dict]:
    return state.setdefault("bookmarks", [])


def add(state: dict, planet_id: int, bookmark_type: int) -> dict:
    row = db.connect().execute("SELECT * FROM planets WHERE id = ?", (planet_id,)).fetchone()
    if row is None:
        raise planet_rules.GameError("This planet does not exist")
    marks = _bookmarks(state)
    if any(b["planet_id"] == planet_id for b in marks):
        raise planet_rules.GameError("This planet is already bookmarked")
    if len(marks) >= MAX_BOOKMARKS:
        raise planet_rules.GameError(f"You can keep at most {MAX_BOOKMARKS} bookmarks")
    mark = {"id": max((b["id"] for b in marks), default=0) + 1, "planet_id": planet_id,
            "bookmark_type": bookmark_type, "planet_name": row["name"], "galaxy_position": row["galaxy"],
            "system_position": row["system"], "planet_position": row["position"]}
    marks.append(mark)
    return mark


def listing(state: dict) -> list[dict]:
    """The bookmarks with current planet names; planets that are gone keep their last name."""
    conn = db.connect()
    for mark in _bookmarks(state):
        row = conn.execute("SELECT name FROM planets WHERE id = ?", (mark["planet_id"],)).fetchone()
        if row:
            mark["planet_name"] = row["name"]
    return list(_bookmarks(state))


def delete(state: dict, bookmark_id: int) -> None:
    state["bookmarks"] = [b for b in _bookmarks(state) if b["id"] != bookmark_id]
