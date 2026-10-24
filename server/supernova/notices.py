"""News: announcements written by the server operator (admin tool), shown in the news window.

notice_type is one of the client's icons: normal, update, maintain or emergency. The content is
shown as Flash HTML text.
"""

import time

from . import db

TYPES = ("normal", "update", "maintain", "emergency")


def add(notice_type: str, title: str, content: str, now: float | None = None) -> int:
    if notice_type not in TYPES:
        raise ValueError(f"notice_type must be one of {', '.join(TYPES)}")
    conn = db.connect()
    with db.lock(), conn:
        cur = conn.execute("INSERT INTO notices (notice_type, title, content, start_time) VALUES (?, ?, ?, ?)",
                           (notice_type, title, content, int(time.time() if now is None else now)))
    return cur.lastrowid


def delete(notice_id: int) -> None:
    conn = db.connect()
    with db.lock(), conn:
        conn.execute("DELETE FROM notices WHERE id = ?", (notice_id,))


def listing() -> list[dict]:
    rows = db.connect().execute("SELECT id, notice_type, title, start_time FROM notices ORDER BY id DESC").fetchall()
    return [dict(r) for r in rows]


def content(notice_id: int) -> str | None:
    row = db.connect().execute("SELECT content FROM notices WHERE id = ?", (notice_id,)).fetchone()
    return row["content"] if row else None
