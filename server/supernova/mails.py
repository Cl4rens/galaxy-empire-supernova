"""Mailbox: player messages and mission reports.

The client keeps its own copy of every mail (MAILS.tfl). The server only hands out mails the
client has not confirmed yet: `messages/get_new.json` returns them, and a second call with their
ids in `checklist` confirms receipt.
"""

import json
import time

from . import db

REPORT, SYSTEM, USER, ALLIANCE, EXPLORE, SCAN = 1, 2, 3, 4, 5, 6  # message_type
SENDER_FLEET = "Fleet Command"
SENDER_SENSOR = "Sensor Array"


def send(recipient_id: int, message_type: int, sender: str, recipient_name: str, subject: str,
         body, now: float | None = None) -> int:
    """Store a mail. `body` is text for player mails and a dict for reports."""
    conn = db.connect()
    with db.lock(), conn:
        cur = conn.execute(
            "INSERT INTO mails (recipient_id, message_type, sender, recipient_name, subject, body, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (recipient_id, message_type, sender, recipient_name, subject, json.dumps(body),
             int(time.time() if now is None else now)))
    return cur.lastrowid


def _view(row) -> dict:
    return {
        "id": row["id"],
        "message_type": row["message_type"],
        "content_type": 0,
        "from": row["sender"],
        "to": row["recipient_name"],
        "subject": row["subject"],
        "body": json.loads(row["body"]),
        "created_at": row["created_at"],
        "is_read": False,
    }


def undelivered(player_id: int) -> list[dict]:
    rows = db.connect().execute(
        "SELECT * FROM mails WHERE recipient_id = ? AND delivered = 0 ORDER BY id", (player_id,)).fetchall()
    return [_view(r) for r in rows]


def unread_count(player_id: int) -> int:
    return db.connect().execute(
        "SELECT COUNT(*) FROM mails WHERE recipient_id = ? AND delivered = 0", (player_id,)).fetchone()[0]


def confirm(player_id: int, ids: list[int]) -> None:
    if not ids:
        return
    conn = db.connect()
    with db.lock(), conn:
        conn.executemany("UPDATE mails SET delivered = 1 WHERE recipient_id = ? AND id = ?",
                         [(player_id, i) for i in ids])
