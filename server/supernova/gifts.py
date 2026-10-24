"""Gifts. Every new player gets a welcome gift, offered as empire.gift_id and opened from the
first tutorial quest (the client has no other way into the gift window). Gifts the server
operator sends with the admin tool are credited at once to the home planet, with a mail.
"""

import json
import time

from . import db, mails, rewards

WELCOME = ("Welcome to Supernova", "A small gift to start your empire.",
           {"metal": 2000, "crystal": 1000, "deuterium": 500}, {})


def send(player_id: int, title: str, description: str, resources: dict[str, int], ships: dict[int, int],
         now: float | None = None) -> int:
    conn = db.connect()
    with db.lock(), conn:
        cur = conn.execute(
            "INSERT INTO gifts (player_id, title, description, resources, ships, created_at) VALUES (?, ?, ?, ?, ?, ?)",
            (player_id, title, description, json.dumps(resources), json.dumps({str(t): n for t, n in ships.items()}),
             int(time.time() if now is None else now)))
    return cur.lastrowid


def credit(player_id: int, title: str, description: str, resources: dict[str, int], ships: dict[int, int]) -> None:
    with db.player_state(player_id) as state:
        rewards.grant(state["empire"]["planets"][0], resources, ships)
        username = state["username"]
    mails.send(player_id, mails.SYSTEM, "Supernova", username, title,
               f"{description}\n\nYour home planet received: {rewards.describe(resources, ships)}.")


def credit_all(title: str, description: str, resources: dict[str, int], ships: dict[int, int]) -> int:
    ids = [r["id"] for r in db.connect().execute("SELECT id FROM players")]
    for pid in ids:
        credit(pid, title, description, resources, ships)
    return len(ids)


def _row(player_id: int, gift_id: int):
    return db.connect().execute("SELECT * FROM gifts WHERE id = ? AND player_id = ? AND claimed_at IS NULL",
                                (gift_id, player_id)).fetchone()


def _contents(row) -> tuple[dict[str, int], dict[int, int]]:
    return json.loads(row["resources"]), {int(t): n for t, n in json.loads(row["ships"]).items()}


def pending_id(player_id: int) -> int:
    row = db.connect().execute("SELECT MIN(id) FROM gifts WHERE player_id = ? AND claimed_at IS NULL",
                               (player_id,)).fetchone()
    return row[0] or 0


def details(player_id: int, gift_id: int) -> dict | None:
    row = _row(player_id, gift_id)
    if row is None:
        return None
    return {"title": row["title"], "desc": row["description"], "rewards": rewards.view(*_contents(row))}


def claim(player_id: int, planet: dict, gift_id: int, now: float | None = None) -> bool:
    row = _row(player_id, gift_id)
    if row is None:
        return False
    rewards.grant(planet, *_contents(row))
    conn = db.connect()
    with db.lock(), conn:
        conn.execute("UPDATE gifts SET claimed_at = ? WHERE id = ?", (int(time.time() if now is None else now), gift_id))
    return True
