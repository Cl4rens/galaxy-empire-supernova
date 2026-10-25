"""Turn the game's screens (data2/*.tfs) into standard SWF files with their pictures, for a
renderer such as Ruffle.

    python -m supernova_client.screens --apk ../apk/galaxy-empire-1-9-36.apk --out screens

The output is made from your APK on your machine; do not share it.
"""

import argparse
import re
from pathlib import Path

from . import swf, textures
from .assets import Assets


class Pictures:
    """data2 pictures as PNG/JPEG bytes; GPU textures are decoded once."""

    def __init__(self, assets: Assets):
        self.assets = assets
        self._cache: dict[str, bytes | None] = {}

    def __call__(self, name: str) -> bytes | None:
        if name not in self._cache:
            data = self.assets.file("data2/" + name)
            if data is not None and textures.is_pvr(data):
                alpha = self.assets.file("data2/" + re.sub(r"\.png$", "_alpha.pak", name))
                data = textures.to_png(data, alpha)
            self._cache[name] = data
        return self._cache[name]


def screen_names(assets: Assets) -> list[str]:
    """The screens in their phone layout (the _16x9/_4x3 variants are for other screen shapes)."""
    return sorted(n[len("data2/"):] for n in assets._files
                  if n.startswith("data2/") and n.endswith(".tfs") and not re.search(r"_(16x9|4x3)\.tfs$", n))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apk", required=True, type=Path)
    parser.add_argument("--out", type=Path, default=Path("screens"))
    parser.add_argument("names", nargs="*", help="only these screens, e.g. Login.tfs City.tfs")
    args = parser.parse_args()
    assets = Assets(args.apk)
    pictures = Pictures(assets)
    args.out.mkdir(parents=True, exist_ok=True)
    runtime = set()
    for name in args.names or screen_names(assets):
        data, missing = swf.fill_bitmaps(assets.file("data2/" + name), pictures)
        (args.out / re.sub(r"\.tfs$", ".swf", name)).write_bytes(data)
        runtime.update(m for m in missing if m.startswith(("NA_", "NAN_")))
        others = [m for m in missing if not m.startswith(("NA_", "NAN_"))]
        print(f"{name}: {'missing ' + ', '.join(others) if others else 'ok'}")
    print(f"{len(runtime)} picture slots are filled by the game at run time (NA_*).")


if __name__ == "__main__":
    main()
