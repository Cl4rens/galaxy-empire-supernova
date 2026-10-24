"""Chat: a world channel for everybody and one channel per alliance.

The client polls `chat_messages.json` every few seconds while the chat is open and shows the
newest message on the planet screen.
"""

import time

from . import alliances, db

WORLD, ALLIANCE = 1, 2  # channels
TRIAL, STANDARD = 0, 1  # account levels; the client colours GM (3) and system (4) messages
MAX_LENGTH = 200
MIN_INTERVAL = 3  # seconds between two messages of a player
KEEP = 500  # messages kept per channel


class ChatError(Exception):
    """Shown to the player."""


def _channel_for(player_id: int, alliance_id: int) -> tuple[int, int]:
    if not alliance_id:
        return WORLD, 0
    mine = alliances.membership(player_id)
    if mine is None or mine["alliance_id"] != alliance_id:
        raise ChatError("You are not in this alliance")
    return ALLIANCE, alliance_id


def send(player_id: int, words: str, alliance_id: int, now: float | None = None) -> None:
    now = time.time() if now is None else now
    words = " ".join(words.split())[:MAX_LENGTH]
    if not words:
        raise ChatError("Please write something")
    channel, alliance_id = _channel_for(player_id, alliance_id)
    conn = db.connect()
    with db.lock(), conn:
        last = conn.execute("SELECT MAX(created_at) FROM chat_messages WHERE player_id = ?", (player_id,)).fetchone()[0]
        if last and now - last < MIN_INTERVAL:
            raise ChatError("You are writing too fast")
        conn.execute("INSERT INTO chat_messages (channel, alliance_id, player_id, words, created_at) "
                     "VALUES (?, ?, ?, ?, ?)", (channel, alliance_id, player_id, words, now))
        conn.execute("DELETE FROM chat_messages WHERE channel = ? AND alliance_id = ? AND id NOT IN "
                     "(SELECT id FROM chat_messages WHERE channel = ? AND alliance_id = ? ORDER BY id DESC LIMIT ?)",
                     (channel, alliance_id, channel, alliance_id, KEEP))


def messages(player_id: int, alliance_id: int, count: int = 50) -> list[dict]:
    """Newest first, as the client takes the first one for the planet screen."""
    channel, alliance_id = _channel_for(player_id, alliance_id)
    rows = db.connect().execute(
        "SELECT c.*, p.username, p.password FROM chat_messages c JOIN players p ON p.id = c.player_id "
        "WHERE c.channel = ? AND c.alliance_id = ? ORDER BY c.id DESC LIMIT ?",
        (channel, alliance_id, max(1, min(count, 100)))).fetchall()
    return [{
        "message_id": r["id"],
        "channel": r["channel"],
        "alliance_id": r["alliance_id"],
        "from_user_id": r["player_id"],
        "from_user_name": r["username"],
        "words": r["words"],
        "created_at": int(r["created_at"]),
        "account_level": STANDARD if r["password"] else TRIAL,
    } for r in rows]


def last_line() -> str | None:
    """The newest world message for the planet screen."""
    newest = messages(0, 0, 1)
    return f"{newest[0]['from_user_name']}:{newest[0]['words']}" if newest else None


def report(player_id: int, message_id: int, notes: str) -> None:
    conn = db.connect()
    with db.lock(), conn:
        conn.execute("INSERT INTO chat_reports (message_id, reporter_id, notes, created_at, words) "
                     "SELECT ?, ?, ?, ?, words FROM chat_messages WHERE id = ?",
                     (message_id, player_id, notes[:500], int(time.time()), message_id))
