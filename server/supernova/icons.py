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
import io
import re
from dataclasses import dataclass
from pathlib import Path

from . import config

WIKI_FILE_PAGE = "https://galaxyempire.fandom.com/wiki/File:"
SIZE = 108  # the game's item pictures
NAME = re.compile(r"[A-Za-z0-9_.-]+\.png")
# the client keeps a downloaded picture under its name for good, so a picture's name carries a
# mark of its content: "officer_geologist_1-3fa2b1c0.png"
VERSIONED = re.compile(r"(?P<stem>[A-Za-z0-9_.-]+)-[0-9a-f]{8}\.png")

@dataclass(frozen=True)
class Source:
    """A wiki file and how it becomes an item picture: cut to `crop` (left, top, right, bottom;
    else the middle square), with the ellipse `cover` (in the cut picture's pixels) painted over
    from its surroundings."""
    wiki: str
    crop: tuple[int, int, int, int] | None = None
    cover: tuple[int, int, int, int] | None = None


# the name items use -> the wiki's file (the game's own pictures, 108x108)
WIKI_FILES = {
    # the level 1 officers are only pictured in the store's screenshots (without a frame)
    "officer_commander_1.png": Source("Commander_store-1.png"),
    "officer_commander_2.png": "Commander_lv2@2x.png",
    "officer_commander_3.png": "Commander_lv3@2x.png",
    "officer_engineer_1.png": Source("Engineer_store-1.png"),
    "officer_engineer_2.png": "Engineer_lv2@2x.png",
    "officer_engineer_3.png": "Engineer_lv3@2x.png",
    "officer_technocrat_1.png": Source("Technocrat_store-1.png"),
    "officer_technocrat_2.png": "Technocrat_lv2@2x.png",
    "officer_technocrat_3.png": "Technocrat_lv3@2x.png",
    "officer_geologist_1.png": Source("Geologist_store-1.png"),
    "officer_geologist_2.png": "Geologist_lv2@2x.png",
    "officer_geologist_3.png": "Geologist_lv3@2x.png",
    "officer_admiral_1.png": Source("Admiral_store-1.png"),
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
    # VIP points items (vip.py): the wiki's level badges, which show the points
    "vip_points_1.png": Source("VIP_100.png"),
    "vip_points_2.png": Source("VIP_2.png"),
    "vip_points_3.png": Source("VIP_3.png"),
    "vip_points_4.png": Source("VIP_4.png"),
    "vip_points_5.png": Source("VIP_5.png"),
    "vip_points_6.png": Source("VIP_6.png"),
    # the wiki only has a screenshot of the store entry: its picture, without the "i" badge
    "gamma_detector.png": Source("Gamma_Detector_(Large).png", crop=(14, 27, 117, 118), cover=(68, -2, 107, 36)),
}


def _source(entry: "str | Source") -> Source:
    return entry if isinstance(entry, Source) else Source(entry)


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
    return sorted({_source(wiki).wiki for name, wiki in WIKI_FILES.items() if not path(name)})


def _saved(folder: Path, wiki: str) -> Path | None:
    """The file a browser saved for a wiki file (with "_" or " ", in any case, as PNG or as the
    WebP the wiki serves to browsers)."""
    stem = wiki.rsplit(".", 1)[0]
    wanted = {f"{s}.{ext}".lower() for s in (stem, stem.replace("_", " ")) for ext in ("png", "webp")}
    return next((f for f in folder.iterdir() if f.is_file() and f.name.lower() in wanted), None)


def picture(data: bytes, source: Source) -> bytes:
    """A saved wiki file as an item picture: SIZE x SIZE, PNG."""
    from PIL import Image, ImageDraw, ImageFilter  # only importing pictures needs Pillow

    image = Image.open(io.BytesIO(data)).convert("RGBA")
    w, h = image.size
    side = min(w, h)
    image = image.crop(source.crop or ((w - side) // 2, (h - side) // 2, (w + side) // 2, (h + side) // 2))
    if source.cover:
        mask = Image.new("L", image.size, 0)
        ImageDraw.Draw(mask).ellipse(source.cover, fill=255)
        for _ in range(120):  # from the edge inwards
            image.paste(image.filter(ImageFilter.GaussianBlur(2)), mask=mask)
    if image.size != (SIZE, SIZE):
        image = image.resize((SIZE, SIZE), Image.LANCZOS)
    out = io.BytesIO()
    image.save(out, "PNG")
    return out.getvalue()


def import_folder(folder: Path) -> tuple[list[str], list[str]]:
    """Take the pictures saved from the wiki in `folder`; returns (imported, missing or unreadable)."""
    icon_dir().mkdir(parents=True, exist_ok=True)
    imported, problems = [], []
    for name, entry in sorted(WIKI_FILES.items()):
        source = _source(entry)
        saved = _saved(folder, source.wiki)
        if saved is None:
            if not path(name):
                problems.append(f"{source.wiki}: not in {folder}")
            continue
        try:
            (icon_dir() / name).write_bytes(picture(saved.read_bytes(), source))
        except OSError as e:
            problems.append(f"{saved.name}: {e}")
            continue
        imported.append(name)
    return imported, sorted(set(problems))
