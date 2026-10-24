"""Player storage in SQLite. Each player's game state is one JSON document for now."""

import json
import secrets
import sqlite3
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS players (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    username   TEXT NOT NULL UNIQUE,
    password   TEXT,
    udid       TEXT,
    created_at INTEGER NOT NULL,
    state      TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS planets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id  INTEGER NOT NULL REFERENCES players(id),
    galaxy     INTEGER NOT NULL,
    system     INTEGER NOT NULL,
    position   INTEGER NOT NULL,
    name       TEXT NOT NULL,
    UNIQUE (galaxy, system, position)
);
CREATE TABLE IF NOT EXISTS fleet_events (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id         INTEGER NOT NULL REFERENCES players(id),
    origin_planet_id INTEGER NOT NULL,
    target_planet_id INTEGER,
    target_galaxy    INTEGER NOT NULL,
    target_system    INTEGER NOT NULL,
    target_position  INTEGER NOT NULL,
    mission          INTEGER NOT NULL,
    ships            TEXT NOT NULL,   -- JSON {fleet_type: count}
    cargo            TEXT NOT NULL,   -- JSON {metal, crystal, deuterium}
    start_time       INTEGER NOT NULL,
    arrival_time     INTEGER NOT NULL,
    return_time      INTEGER NOT NULL,
    turn_time        INTEGER NOT NULL DEFAULT 0,  -- when it turned around (arrival or recall)
    status           TEXT NOT NULL    -- outbound, returning
);
CREATE INDEX IF NOT EXISTS fleet_events_due ON fleet_events (status, arrival_time, return_time);
CREATE TABLE IF NOT EXISTS mails (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    recipient_id   INTEGER NOT NULL REFERENCES players(id),
    message_type   INTEGER NOT NULL,
    sender         TEXT NOT NULL,
    recipient_name TEXT NOT NULL,
    subject        TEXT NOT NULL,
    body           TEXT NOT NULL,   -- JSON: text for player mails, object for reports
    created_at     INTEGER NOT NULL,
    delivered      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS mails_new ON mails (recipient_id, delivered);
CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    player_id  INTEGER NOT NULL REFERENCES players(id),
    created_at INTEGER NOT NULL
);
"""

_lock = threading.RLock()  # reentrant: universe code runs inside create_player
_conn: sqlite3.Connection | None = None


def lock() -> threading.RLock:
    """The lock that serializes writes to the database."""
    return _lock


def connect(path: Path | None = None) -> sqlite3.Connection:
    global _conn
    if _conn is None:
        path = path or config.DATA_DIR / "supernova.db"
        path.parent.mkdir(parents=True, exist_ok=True)
        _conn = sqlite3.connect(path, check_same_thread=False)
        _conn.row_factory = sqlite3.Row
        _conn.executescript(SCHEMA)
    return _conn


def create_player(username_for_id, udid: str, new_state) -> tuple[int, str, dict]:
    """Insert a player. The username and state depend on the new id, so they are callbacks."""
    conn = connect()
    with _lock, conn:
        cur = conn.execute(
            "INSERT INTO players (username, udid, created_at, state) VALUES (?, ?, ?, '{}')",
            (f"pending-{time.time_ns()}", udid, int(time.time())),
        )
        player_id = cur.lastrowid
        username = username_for_id(player_id)
        state = new_state(player_id, username)
        conn.execute(
            "UPDATE players SET username = ?, state = ? WHERE id = ?",
            (username, json.dumps(state), player_id),
        )
    return player_id, username, state


def find_player(username: str) -> tuple[int, dict] | None:
    row = connect().execute("SELECT id, state FROM players WHERE username = ?", (username,)).fetchone()
    return (row["id"], json.loads(row["state"])) if row else None


def save_state(player_id: int, state: dict) -> None:
    conn = connect()
    with _lock, conn:
        conn.execute("UPDATE players SET state = ? WHERE id = ?", (json.dumps(state), player_id))


def load_state(player_id: int) -> dict:
    row = connect().execute("SELECT state FROM players WHERE id = ?", (player_id,)).fetchone()
    return json.loads(row["state"])


def create_session(player_id: int) -> str:
    token = secrets.token_urlsafe(32)
    conn = connect()
    with _lock, conn:
        conn.execute("INSERT INTO sessions (token, player_id, created_at) VALUES (?, ?, ?)",
                     (token, player_id, int(time.time())))
    return token


def session_player(token: str) -> int | None:
    row = connect().execute("SELECT player_id FROM sessions WHERE token = ?", (token,)).fetchone()
    return row["player_id"] if row else None


_player_locks: dict[int, threading.Lock] = {}


@contextmanager
def player_state(player_id: int):
    """Load a player's state for changing it; saved when the block ends without error."""
    with _lock:
        lock = _player_locks.setdefault(player_id, threading.Lock())
    with lock:
        state = load_state(player_id)
        yield state
        save_state(player_id, state)
