"""Running a server: database backups, events that start by themselves, bans and statistics.

The server calls housekeeping() every second (app.py); it only works when something is due.
"""

import json
import logging
import sqlite3
import time
from pathlib import Path

from . import arena, config, db
from . import galaxy_challenge as gc

log = logging.getLogger("supernova")
HOUSEKEEPING_SECONDS = 60
_last_housekeeping = 0.0


def backup_dir() -> Path:
    return config.DATA_DIR / "backups"


def backups() -> list[Path]:
    return sorted(backup_dir().glob("supernova-*.db"))


def backup(now: float | None = None) -> Path:
    """Copy the database while the server runs (SQLite's online backup), then drop old copies."""
    now = time.time() if now is None else now
    backup_dir().mkdir(parents=True, exist_ok=True)
    target = backup_dir() / time.strftime("supernova-%Y%m%d-%H%M%S.db", time.gmtime(now))
    dest = sqlite3.connect(target)
    try:
        with db.lock():
            db.connect().backup(dest)
    finally:
        dest.close()
    for old in backups()[:-max(1, config.BACKUP_KEEP)]:
        old.unlink()
    return target


def _backup_due(now: float) -> bool:
    if config.BACKUP_HOURS <= 0:
        return False
    newest = backups()[-1:] if backup_dir().exists() else []
    return not newest or now - newest[0].stat().st_mtime >= config.BACKUP_HOURS * 3600


def _latest_start(kind: str) -> float | None:
    row = db.connect().execute("SELECT data FROM events WHERE kind = ? ORDER BY id DESC LIMIT 1", (kind,)).fetchone()
    return json.loads(row["data"])["start"] if row else None


def schedule(now: float) -> list[str]:
    """Start the events that are due (SUPERNOVA_GC_EVERY_DAYS / SUPERNOVA_ARENA_EVERY_DAYS)."""
    started = []
    if config.GC_EVERY_DAYS > 0:
        last = _latest_start(gc.KIND)
        if last is None or now - last >= config.GC_EVERY_DAYS * 86400:
            gc.start(now)
            started.append("Galaxy Challenge")
    if config.ARENA_EVERY_DAYS > 0:
        last = _latest_start(arena.KIND)
        if last is None or now - last >= config.ARENA_EVERY_DAYS * 86400:
            arena.start(now, 86400, 6 * 86400, 86400)
            started.append("Conquer The Universe")
    return started


def housekeeping(now: float) -> None:
    global _last_housekeeping
    if now - _last_housekeeping < HOUSEKEEPING_SECONDS:
        return
    _last_housekeeping = now
    if _backup_due(now):
        log.info("backup written: %s", backup(now))
    for name in schedule(now):
        log.info("%s started", name)


# --- Players -------------------------------------------------------------------------------------


def ban(username: str, reason: str) -> None:
    conn = db.connect()
    with db.lock(), conn:
        cur = conn.execute("UPDATE players SET banned = ? WHERE username = ?", (reason, username))
        if cur.rowcount == 0:
            raise ValueError(f"No player named {username!r}")
        player_id = conn.execute("SELECT id FROM players WHERE username = ?", (username,)).fetchone()["id"]
        conn.execute("DELETE FROM sessions WHERE player_id = ?", (player_id,))


def unban(username: str) -> None:
    conn = db.connect()
    with db.lock(), conn:
        if conn.execute("UPDATE players SET banned = NULL WHERE username = ?", (username,)).rowcount == 0:
            raise ValueError(f"No player named {username!r}")


def player_count() -> int:
    return db.connect().execute("SELECT COUNT(*) FROM players").fetchone()[0]


def stats(now: float | None = None) -> dict:
    now = time.time() if now is None else now
    conn = db.connect()

    def seen_within(seconds: int) -> int:
        return conn.execute("SELECT COUNT(*) FROM players WHERE last_seen >= ?", (int(now - seconds),)).fetchone()[0]

    newest = backups()[-1:] if backup_dir().exists() else []
    return {
        "players": player_count(),
        "active_hour": seen_within(3600), "active_day": seen_within(86400), "active_week": seen_within(7 * 86400),
        "new_week": conn.execute("SELECT COUNT(*) FROM players WHERE created_at >= ?",
                                 (int(now - 7 * 86400),)).fetchone()[0],
        "banned": conn.execute("SELECT COUNT(*) FROM players WHERE banned IS NOT NULL").fetchone()[0],
        "alliances": conn.execute("SELECT COUNT(*) FROM alliances").fetchone()[0],
        "fleets_flying": conn.execute("SELECT COUNT(*) FROM fleet_events").fetchone()[0],
        "last_backup": newest[0].name if newest else None,
    }
