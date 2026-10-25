"""Backups, scheduled events, bans and statistics."""

import json
import sqlite3
import time

from fastapi.testclient import TestClient

from supernova import arena, config, db, operations, world
from supernova import galaxy_challenge as gc
from supernova.app import app

from .test_acs import member


def test_backup_is_a_working_copy_and_old_ones_go(monkeypatch):
    monkeypatch.setattr(config, "BACKUP_KEEP", 2)
    for old in operations.backups():
        old.unlink()
    now = time.time()
    paths = [operations.backup(now + i) for i in range(3)]
    assert operations.backups() == paths[1:]  # only the newest 2 are kept
    copy = sqlite3.connect(paths[-1])
    assert copy.execute("SELECT COUNT(*) FROM players").fetchone()[0] == operations.player_count()
    copy.close()


def test_backups_run_when_due(monkeypatch):
    monkeypatch.setattr(config, "BACKUP_HOURS", 6)
    for old in operations.backups():
        old.unlink()
    assert operations._backup_due(time.time())
    operations.backup()
    assert not operations._backup_due(time.time())
    assert operations._backup_due(time.time() + 6 * 3600 + 1)
    monkeypatch.setattr(config, "BACKUP_HOURS", 0)
    assert not operations._backup_due(time.time() + 10**6)


def test_events_start_by_themselves(monkeypatch):
    now = time.time() + 10**7  # later than every event the other tests started
    assert operations.schedule(now) == []  # off by default
    monkeypatch.setattr(config, "GC_EVERY_DAYS", 14)
    monkeypatch.setattr(config, "ARENA_EVERY_DAYS", 10)
    assert operations.schedule(now) == ["Galaxy Challenge", "Conquer The Universe"]
    assert operations.schedule(now + 86400) == []
    assert operations.schedule(now + 11 * 86400) == ["Conquer The Universe"]
    latest = db.connect().execute("SELECT data FROM events WHERE kind = ? ORDER BY id DESC LIMIT 1", (gc.KIND,)).fetchone()
    assert json.loads(latest["data"])["start"] == int(now)
    with arena.event(now + 11 * 86400 + 10) as ev:
        assert arena.stage(ev, now + 11 * 86400 + 10) == arena.GARRISON


def test_ban_ends_sessions_and_blocks_requests():
    c, me = member("ops-banned", {})
    assert c.get(f"/world/1/users/{me['user_id']}?do=empire").status_code == 200
    operations.ban(me["username"], "Cheating")
    r = c.get(f"/world/1/users/{me['user_id']}?do=empire")
    assert r.status_code == 401  # the session is gone
    c.cookies.set(world.SESSION_COOKIE, db.create_session(me["user_id"]))  # a new login
    r = c.get(f"/world/1/users/{me['user_id']}?do=empire")
    assert r.status_code == 403 and r.text == "Cheating"
    operations.unban(me["username"])
    assert c.get(f"/world/1/users/{me['user_id']}?do=empire").status_code == 200


def test_stats_and_health():
    c, me = member("ops-stats", {})
    c.get(f"/world/1/users/{me['user_id']}?do=empire")
    s = operations.stats()
    assert s["players"] >= 1 and s["active_hour"] >= 1 and s["banned"] == 0
    assert TestClient(app).get("/health").json() == {"status": "ok", "players": s["players"]}
