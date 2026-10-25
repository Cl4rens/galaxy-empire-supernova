"""Fetch Ruffle at the commit the engine is built against and apply Supernova's patch.

    python setup_ruffle.py

Ruffle (https://ruffle.rs, MIT/Apache-2.0) ends up in ./ruffle, which git ignores.
"""

import shutil
import subprocess
import sys
from pathlib import Path

RUFFLE_URL = "https://github.com/ruffle-rs/ruffle.git"
RUFFLE_COMMIT = "2cf0fe76e4629befd870c75684b3cae71fa5b8ba"  # nightly of 2026-10-24
HERE = Path(__file__).resolve().parent
TARGET = HERE / "ruffle"


def git(*args: str, cwd: Path = TARGET) -> str:
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout.strip()


def main() -> None:
    if not TARGET.exists():
        TARGET.mkdir()
        git("init", "-q")
        git("remote", "add", "origin", RUFFLE_URL)
    git("fetch", "-q", "--depth", "1", "origin", RUFFLE_COMMIT)
    git("checkout", "-q", "--force", RUFFLE_COMMIT)
    git("apply", str(HERE / "ruffle.patch"))
    # first setup: build with the dependency versions Ruffle is tested with
    if not (HERE / "Cargo.lock").exists():
        shutil.copyfile(TARGET / "Cargo.lock", HERE / "Cargo.lock")
    print(f"Ruffle {RUFFLE_COMMIT[:10]} ready in {TARGET}")


if __name__ == "__main__":
    sys.exit(main())
