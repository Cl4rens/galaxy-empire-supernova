"""Server settings, read from environment variables."""

import os
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Where the database lives. In Docker this is a mounted volume.
DATA_DIR = Path(os.environ.get("SUPERNOVA_DATA", REPO_ROOT / "server" / "data"))

# Original APK to read the client key from. Default: the first APK in the repo's apk/ folder.
APK_PATH = os.environ.get("SUPERNOVA_APK")
APK_DIR = Path(os.environ.get("SUPERNOVA_APK_DIR", REPO_ROOT / "apk"))

WORLD_ID = int(os.environ.get("SUPERNOVA_WORLD_ID", "1"))
# A short code like the original servers' ("EN01"): the login screen shows it next to the account.
WORLD_NAME = os.environ.get("SUPERNOVA_WORLD_NAME", "SN01")

# For CI and tests only: start without an APK. World endpoints then answer 503.
REQUIRE_CLIENT_KEY = os.environ.get("SUPERNOVA_REQUIRE_KEY", "1") != "0"

# Game speed: production and build times are multiplied/divided by this.
SPEED = float(os.environ.get("SUPERNOVA_SPEED", "1"))

# Newbie protection (see protection.py): players below NEWBIE_POINTS are protected; from
# STRONG_POINTS on, the 1:5 points rule no longer applies.
NEWBIE_POINTS = int(os.environ.get("SUPERNOVA_NEWBIE_POINTS", "5000"))
STRONG_POINTS = int(os.environ.get("SUPERNOVA_STRONG_POINTS", "500000"))

# Dark matter: what new players start with, and an income every player gets per day (0: none).
STARTING_DARK_MATTER = int(os.environ.get("SUPERNOVA_STARTING_DARK_MATTER", "200"))
DARK_MATTER_PER_DAY = float(os.environ.get("SUPERNOVA_DARK_MATTER_PER_DAY", "0"))

# Star fortress battles (see fortresses.py) repeat every week / SPEED from this moment
# (seconds since 1970). Default: Sunday 19:00 UTC.
FORTRESS_BATTLE_OFFSET = int(os.environ.get("SUPERNOVA_FORTRESS_BATTLE", str(3 * 86400 + 19 * 3600)))

# Backups of the database into DATA_DIR/backups: every BACKUP_HOURS hours (0: off), keeping the
# newest BACKUP_KEEP files.
BACKUP_HOURS = float(os.environ.get("SUPERNOVA_BACKUP_HOURS", "6"))
BACKUP_KEEP = int(os.environ.get("SUPERNOVA_BACKUP_KEEP", "28"))

# Events that start by themselves: a new Galaxy Challenge / Conquer The Universe arena this many
# days after the previous one started (0: only the operator starts them, see admin.py).
GC_EVERY_DAYS = float(os.environ.get("SUPERNOVA_GC_EVERY_DAYS", "0"))
ARENA_EVERY_DAYS = float(os.environ.get("SUPERNOVA_ARENA_EVERY_DAYS", "0"))
