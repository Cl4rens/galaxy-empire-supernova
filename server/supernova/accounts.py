"""Accounts: guests can register a name and password, then log in on any device.

Guest accounts are bound to the device that created them (its openUDID); without a password
nobody else can log in to them, even when they know the name. Passwords are stored as
scrypt hashes.
"""

import hashlib
import hmac
import re
import secrets

from . import db

NAME_PATTERN = re.compile(r"^[A-Za-z0-9_\-. ]{3,20}$")


class AccountError(Exception):
    """Shown to the player."""


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2 ** 14, r=8, p=1)
    return f"scrypt${salt.hex()}${digest.hex()}"


def check_password(stored: str | None, password: str) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, salt, digest = stored.split("$")
    candidate = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=2 ** 14, r=8, p=1)
    return hmac.compare_digest(candidate.hex(), digest)


def _row(username: str):
    return db.connect().execute("SELECT id, password, udid FROM players WHERE username = ?", (username,)).fetchone()


def guest_login(username: str, udid: str) -> int:
    """A returning guest. Only the device that created the account may use it."""
    row = _row(username)
    if row is None:
        raise AccountError("Unknown account")
    if row["password"]:
        raise AccountError("This account has a password. Please log in with it.")
    if row["udid"] and udid != row["udid"]:
        raise AccountError("This guest account belongs to another device")
    return row["id"]


def password_login(username: str, password: str) -> int:
    row = _row(username)
    if row is None or not check_password(row["password"], password):
        raise AccountError("Wrong name or password")
    return row["id"]


def _check_name(username: str, player_id: int) -> None:
    if not NAME_PATTERN.match(username):
        raise AccountError("Names have 3-20 letters, digits, spaces or _ - .")
    other = _row(username)
    if other and other["id"] != player_id:
        raise AccountError("This name is already taken")


def _check_password(password: str) -> None:
    if len(password) < 6:
        raise AccountError("Passwords need at least 6 characters")


def register(player_id: int, username: str, password: str, confirmation: str) -> None:
    """Turn the current guest account into a named account with a password."""
    if password != confirmation:
        raise AccountError("The passwords do not match")
    _check_name(username, player_id)
    _check_password(password)
    with db.player_state(player_id) as state:
        conn = db.connect()
        with db.lock(), conn:
            conn.execute("UPDATE players SET username = ?, password = ? WHERE id = ?",
                         (username, hash_password(password), player_id))
        state["username"] = username


def rename(player_id: int, username: str, password: str) -> None:
    row = db.connect().execute("SELECT password FROM players WHERE id = ?", (player_id,)).fetchone()
    if not check_password(row["password"], password):
        raise AccountError("Wrong password")
    _check_name(username, player_id)
    with db.player_state(player_id) as state:
        conn = db.connect()
        with db.lock(), conn:
            conn.execute("UPDATE players SET username = ? WHERE id = ?", (username, player_id))
        state["username"] = username


def change_password(player_id: int, old: str, new: str, confirmation: str) -> None:
    row = db.connect().execute("SELECT password FROM players WHERE id = ?", (player_id,)).fetchone()
    if not check_password(row["password"], old):
        raise AccountError("Wrong password")
    if new != confirmation:
        raise AccountError("The passwords do not match")
    _check_password(new)
    conn = db.connect()
    with db.lock(), conn:
        conn.execute("UPDATE players SET password = ? WHERE id = ?", (hash_password(new), player_id))
