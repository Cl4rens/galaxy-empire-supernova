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
    state      TEXT NOT NULL,
    last_seen  INTEGER NOT NULL DEFAULT 0,
    banned     TEXT
);
CREATE TABLE IF NOT EXISTS planets (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id  INTEGER NOT NULL REFERENCES players(id),
    galaxy     INTEGER NOT NULL,
    system     INTEGER NOT NULL,
    position   INTEGER NOT NULL,
    name       TEXT NOT NULL,
    kind       INTEGER NOT NULL DEFAULT 0,  -- 0 planet, 1 moon
    UNIQUE (galaxy, system, position, kind)
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
    status           TEXT NOT NULL,   -- outbound, returning
    acs_id           INTEGER          -- coordinated attack: the leading attack's event id
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
CREATE TABLE IF NOT EXISTS debris (
    galaxy   INTEGER NOT NULL,
    system   INTEGER NOT NULL,
    position INTEGER NOT NULL,
    metal    INTEGER NOT NULL,
    crystal  INTEGER NOT NULL,
    PRIMARY KEY (galaxy, system, position)
);
CREATE TABLE IF NOT EXISTS pirate_bases (
    galaxy       INTEGER NOT NULL,
    system       INTEGER NOT NULL,
    position     INTEGER NOT NULL,
    beaten_until REAL NOT NULL,
    PRIMARY KEY (galaxy, system, position)
);
CREATE TABLE IF NOT EXISTS expeditions (
    player_id  INTEGER NOT NULL,
    started_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS battle_stats (
    player_id INTEGER PRIMARY KEY,
    wins      INTEGER NOT NULL DEFAULT 0,
    losses    INTEGER NOT NULL DEFAULT 0,
    draws     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS notices (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    notice_type TEXT NOT NULL,
    title       TEXT NOT NULL,
    content     TEXT NOT NULL,
    start_time  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS gifts (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    player_id   INTEGER NOT NULL REFERENCES players(id),
    title       TEXT NOT NULL,
    description TEXT NOT NULL,
    resources   TEXT NOT NULL,
    ships       TEXT NOT NULL,
    created_at  INTEGER NOT NULL,
    claimed_at  INTEGER
);
CREATE TABLE IF NOT EXISTS chat_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    channel     INTEGER NOT NULL,
    alliance_id INTEGER NOT NULL,
    player_id   INTEGER NOT NULL REFERENCES players(id),
    words       TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS chat_by_channel ON chat_messages (channel, alliance_id, id);
CREATE TABLE IF NOT EXISTS chat_reports (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    message_id  INTEGER NOT NULL,
    reporter_id INTEGER NOT NULL REFERENCES players(id),
    words       TEXT NOT NULL,
    notes       TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS alliances (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL UNIQUE COLLATE NOCASE,
    description TEXT NOT NULL,
    activity    TEXT NOT NULL,
    creator_id  INTEGER NOT NULL,
    created_at  INTEGER NOT NULL,
    auto_in     INTEGER NOT NULL DEFAULT 0,
    bag_dark_matter INTEGER NOT NULL DEFAULT 0,
    bag_credits     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS alliance_bag_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    alliance_id INTEGER NOT NULL,
    username    TEXT NOT NULL,
    bag_type    TEXT NOT NULL,
    number      INTEGER NOT NULL,
    action      TEXT NOT NULL,
    created_at  INTEGER NOT NULL
);
CREATE INDEX IF NOT EXISTS bag_log_by_alliance ON alliance_bag_log (alliance_id, id);
CREATE TABLE IF NOT EXISTS fortresses (
    id          INTEGER PRIMARY KEY,
    alliance_id INTEGER,
    battle      INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS fortress_wars (
    fortress_id INTEGER NOT NULL,
    alliance_id INTEGER NOT NULL,
    battle      INTEGER NOT NULL,
    morale      INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (fortress_id, alliance_id, battle)
);
CREATE TABLE IF NOT EXISTS fortress_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    owner_id     INTEGER NOT NULL REFERENCES players(id),
    planet_id    INTEGER NOT NULL,
    kind         INTEGER NOT NULL,
    target_id    INTEGER NOT NULL,
    ships        TEXT NOT NULL,
    flight       INTEGER NOT NULL,
    start_time   INTEGER NOT NULL,
    arrival_time INTEGER NOT NULL,
    return_time  INTEGER NOT NULL,
    status       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS alliance_members (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    alliance_id INTEGER NOT NULL REFERENCES alliances(id),
    player_id   INTEGER NOT NULL UNIQUE REFERENCES players(id),
    post        INTEGER NOT NULL,
    joined_at   INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS alliance_applications (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    alliance_id INTEGER NOT NULL REFERENCES alliances(id),
    player_id   INTEGER NOT NULL UNIQUE REFERENCES players(id),
    created_at  INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS sessions (
    token      TEXT PRIMARY KEY,
    player_id  INTEGER NOT NULL REFERENCES players(id),
    created_at INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS events (
    id    INTEGER PRIMARY KEY AUTOINCREMENT,
    kind  TEXT NOT NULL,
    data  TEXT NOT NULL
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
        _migrate(_conn)
        _conn.executescript(SCHEMA)
    return _conn


def _migrate(conn: sqlite3.Connection) -> None:
    player_columns = [r[1] for r in conn.execute("PRAGMA table_info(players)")]
    if player_columns and "last_seen" not in player_columns:
        with conn:
            conn.execute("ALTER TABLE players ADD COLUMN last_seen INTEGER NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE players ADD COLUMN banned TEXT")
    alliance_columns = [r[1] for r in conn.execute("PRAGMA table_info(alliances)")]
    if alliance_columns and "bag_credits" not in alliance_columns:
        with conn:
            conn.execute("ALTER TABLE alliances ADD COLUMN bag_dark_matter INTEGER NOT NULL DEFAULT 0")
            conn.execute("ALTER TABLE alliances ADD COLUMN bag_credits INTEGER NOT NULL DEFAULT 0")
    fleet_columns = [r[1] for r in conn.execute("PRAGMA table_info(fleet_events)")]
    if fleet_columns and "acs_id" not in fleet_columns:
        with conn:
            conn.execute("ALTER TABLE fleet_events ADD COLUMN acs_id INTEGER")
    columns = [r[1] for r in conn.execute("PRAGMA table_info(planets)")]
    if columns and "kind" not in columns:  # moons share positions with their planets
        with conn:
            conn.executescript("""
                ALTER TABLE planets RENAME TO planets_old;
                CREATE TABLE planets (
                    id         INTEGER PRIMARY KEY AUTOINCREMENT,
                    player_id  INTEGER NOT NULL REFERENCES players(id),
                    galaxy     INTEGER NOT NULL,
                    system     INTEGER NOT NULL,
                    position   INTEGER NOT NULL,
                    name       TEXT NOT NULL,
                    kind       INTEGER NOT NULL DEFAULT 0,
                    UNIQUE (galaxy, system, position, kind)
                );
                INSERT INTO planets (id, player_id, galaxy, system, position, name)
                    SELECT id, player_id, galaxy, system, position, name FROM planets_old;
                DROP TABLE planets_old;
            """)


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
            (username, _encode(state), player_id),
        )
    return player_id, username, state


def _decode(text: str) -> dict:
    """Load a stored state. empire["main_planet"] becomes a reference to the current planet."""
    state = json.loads(text)
    empire = state.get("empire")
    if empire is None:
        return state
    if "planets" not in empire:  # state_version 1: a single planet stored as main_planet
        empire["planets"] = [empire["main_planet"]]
        empire["current_planet_id"] = empire["main_planet"]["planet_id"]
        empire.pop("planets_info", None)
        state["state_version"] = 2
    bodies = empire["planets"] + [p["moon"] for p in empire["planets"] if p.get("moon")]
    current = next((p for p in bodies if p["planet_id"] == empire.get("current_planet_id")), empire["planets"][0])
    empire["current_planet_id"] = current["planet_id"]
    empire["main_planet"] = current
    return state


def _encode(state: dict) -> str:
    empire = state.get("empire")
    if empire is None or "main_planet" not in empire:
        return json.dumps(state)
    stored = dict(state, empire={k: v for k, v in empire.items() if k != "main_planet"})
    return json.dumps(stored)


def find_player(username: str) -> tuple[int, dict] | None:
    row = connect().execute("SELECT id, state FROM players WHERE username = ?", (username,)).fetchone()
    return (row["id"], _decode(row["state"])) if row else None


def save_state(player_id: int, state: dict) -> None:
    conn = connect()
    with _lock, conn:
        conn.execute("UPDATE players SET state = ? WHERE id = ?", (_encode(state), player_id))


def load_state(player_id: int) -> dict:
    row = connect().execute("SELECT state FROM players WHERE id = ?", (player_id,)).fetchone()
    return _decode(row["state"])


def create_session(player_id: int) -> str:
    token = secrets.token_urlsafe(32)
    conn = connect()
    with _lock, conn:
        conn.execute("INSERT INTO sessions (token, player_id, created_at) VALUES (?, ?, ?)",
                     (token, player_id, int(time.time())))
    return token


class Banned(Exception):
    """The player behind a session is banned; the message is the operator's reason."""


_seen: dict[int, float] = {}
SEEN_INTERVAL = 300  # last_seen is written at most this often per player


def session_player(token: str) -> int | None:
    row = connect().execute(
        "SELECT s.player_id, p.banned FROM sessions s JOIN players p ON p.id = s.player_id WHERE s.token = ?",
        (token,)).fetchone()
    if row is None:
        return None
    if row["banned"] is not None:
        raise Banned(row["banned"] or "Your account is banned.")
    now = time.time()
    if now - _seen.get(row["player_id"], 0) > SEEN_INTERVAL:
        _seen[row["player_id"]] = now
        conn = connect()
        with _lock, conn:
            conn.execute("UPDATE players SET last_seen = ? WHERE id = ?", (int(now), row["player_id"]))
    return row["player_id"]


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
