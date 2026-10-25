"""Item pictures the client does not have.

The original server sent item pictures by URL and the client downloaded them from there; only a
few are left in the client (icons/item_res_NN.png, see items.py). The Galaxy Empire wiki kept
many of the originals. They are game art, so they are not part of this project: the operator
saves them from the wiki (`python -m supernova.admin icons-list` names the pages) and imports
them into DATA_DIR/icons (`import-icons <folder>`). The server serves them under
/world/<id>/icons/ and items use them from then on; without them items keep the client's own
pictures.
"""

import hashlib
import re
import shutil
from pathlib import Path

from . import config

WIKI_FILE_PAGE = "https://galaxyempire.fandom.com/wiki/File:"
PNG = b"\x89PNG\r\n\x1a\n"
NAME = re.compile(r"[A-Za-z0-9_.-]+\.png")
# the client keeps a downloaded picture under its name for good, so a picture's name carries a
# mark of its content: "officer_geologist_1-3fa2b1c0.png"
VERSIONED = re.compile(r"(?P<stem>[A-Za-z0-9_.-]+)-[0-9a-f]{8}\.png")

# the name items use -> the wiki's file (the game's own pictures, 108x108 PNG)
WIKI_FILES = {
    "officer_commander_1.png": "Commander_lv2@2x.png",  # the wiki has no level 1 picture of some
    "officer_commander_2.png": "Commander_lv2@2x.png",
    "officer_commander_3.png": "Commander_lv3@2x.png",
    "officer_engineer_1.png": "Engineer_lv2@2x.png",
    "officer_engineer_2.png": "Engineer_lv2@2x.png",
    "officer_engineer_3.png": "Engineer_lv3@2x.png",
    "officer_technocrat_1.png": "Technocrat_lv2@2x.png",
    "officer_technocrat_2.png": "Technocrat_lv2@2x.png",
    "officer_technocrat_3.png": "Technocrat_lv3@2x.png",
    "officer_geologist_1.png": "Geologist@2x.png",
    "officer_geologist_2.png": "Geologist_lv2@2x.png",
    "officer_geologist_3.png": "Geologist_lv3@2x.png",
    "officer_admiral_1.png": "Admiral_lv2@2x.png",
    "officer_admiral_2.png": "Admiral_lv2@2x.png",
    "officer_admiral_3.png": "Admiral_lv3@2x.png",
    "officer_guider_1.png": "Navigator@2x.png",  # the guider's pictures are named navigator
    "officer_guider_2.png": "Navigator_lv2@2x.png",
    "officer_guider_3.png": "Navigator_lv3@2x.png",
    "container_metal.png": "Items_metal@2x.png",
    "container_crystal.png": "Items_crystal@2x.png",
    "container_gas.png": "Items_gas@2x.png",
    "terraformer.png": "Terraformation@2x.png",
    "ecm_chip.png": "Electronic_warfare_chip.png",
}


def icon_dir() -> Path:
    return config.DATA_DIR / "icons"


def path(name: str) -> Path | None:
    """The file of an imported picture (by its name, with or without the content mark)."""
    versioned = VERSIONED.fullmatch(name)
    if versioned:
        name = versioned["stem"] + ".png"
    if not NAME.fullmatch(name):
        return None
    file = icon_dir() / name
    return file if file.is_file() else None


_marks: dict[tuple[Path, float], str] = {}


def _mark(file: Path) -> str:
    key = (file, file.stat().st_mtime)
    if key not in _marks:
        _marks[key] = hashlib.sha1(file.read_bytes()).hexdigest()[:8]
    return _marks[key]


def pick(name: str, fallback: str) -> str:
    """The picture `name` (with its content mark) when the server has it, else the client's own
    `fallback`."""
    file = path(name) if name else None
    return f"icons/{file.stem}-{_mark(file)}.png" if file else f"icons/{fallback}"


def missing() -> list[str]:
    """The wiki files still needed, each once."""
    return sorted({wiki for name, wiki in WIKI_FILES.items() if not path(name)})


def _saved(folder: Path, wiki: str) -> Path | None:
    """The file a browser saved for a wiki file (with "_" or " ", in any case)."""
    wanted = {wiki.lower(), wiki.replace("_", " ").lower()}
    return next((f for f in folder.iterdir() if f.is_file() and f.name.lower() in wanted), None)


def import_folder(folder: Path) -> tuple[list[str], list[str]]:
    """Take the pictures saved from the wiki in `folder`; returns (imported, not found or not PNG)."""
    icon_dir().mkdir(parents=True, exist_ok=True)
    imported, problems = [], []
    for name, wiki in sorted(WIKI_FILES.items()):
        saved = _saved(folder, wiki)
        if saved is None:
            if not path(name):
                problems.append(f"{wiki}: not in {folder}")
            continue
        if not saved.read_bytes().startswith(PNG):
            problems.append(f"{saved.name}: not a PNG (save the original file from the wiki page)")
            continue
        shutil.copyfile(saved, icon_dir() / name)
        imported.append(name)
    return imported, sorted(set(problems))
