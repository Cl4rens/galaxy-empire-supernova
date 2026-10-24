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
WORLD_NAME = os.environ.get("SUPERNOVA_WORLD_NAME", "Supernova")

# For CI and tests only: start without an APK. World endpoints then answer 503.
REQUIRE_CLIENT_KEY = os.environ.get("SUPERNOVA_REQUIRE_KEY", "1") != "0"
