"""Run the Supernova client engine without graphics.

    python -m supernova_client --apk ../apk/galaxy-empire-1-9-36.apk --server http://127.0.0.1:8000
    python -m supernova_client ... --build 1        # also order a metal mine (building type 1)

It boots the game's own scripts from your APK, passes the loading screen, logs in (a guest
account, remembered in --save), and prints what the city screen shows.
"""

import argparse
import logging
import sys
from pathlib import Path

from .engine import Engine


def status(engine: Engine) -> str:
    return engine.lua.eval("""function()
        local p = GameGlobalData:GetMainPlanetData()
        local e = GameGlobalData:GetData("empire")
        return string.format("%s on %s: metal %d, crystal %d, deuterium %d, dark matter %d, %d building order(s)",
          tostring(GameGlobalData:GetData("username")), tostring(p.planet_name), p.metal, p.crystal, p.deuterium,
          e.dark_matter_count, #p.building_events)
    end""")().decode()


def login(engine: Engine, timeout: float = 30) -> None:
    engine.boot()
    if not engine.run(timeout, until=lambda: "GameObjectLoading" in engine.active_objects()):
        raise SystemExit("the game did not reach its loading screen")
    engine.press("GameObjectLoading", "LoadingOver")  # the end of the loading animation
    if not engine.run(timeout, until=lambda: "GameUISplash" in engine.active_objects()):
        raise SystemExit("the game did not reach its title screen")
    engine.run(1)
    engine.press("GameUISplash", "btnStartPressed")
    if not engine.run(timeout, until=lambda: engine.state_name() == "GameStatePlanet"):
        raise SystemExit(f"login failed: {engine.alerts or engine.requests[-3:]}")
    engine.run(2)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--apk", required=True, type=Path, help="your original Galaxy Empire APK")
    parser.add_argument("--server", default="http://127.0.0.1:8000")
    parser.add_argument("--save", type=Path, default=Path("client-save"), help="the device's storage")
    parser.add_argument("--language", default="en")
    parser.add_argument("--build", type=int, help="order this building type on its first free lot")
    parser.add_argument("-v", "--verbose", action="store_true")
    args = parser.parse_args()
    logging.basicConfig(level=logging.INFO if args.verbose else logging.WARNING, format="%(message)s")

    engine = Engine(args.apk, args.server, args.save, language=args.language)
    login(engine)
    print(status(engine))
    if args.build:
        lot = engine.lua.eval("""function(btype)
            for _, b in ipairs(GameGlobalData:GetMainPlanetData().buildings) do
              if b.building_type == btype then return b.building_position_index end
            end
        end""")(args.build)
        engine.press("GameUIPlanet", "onBlockClicked", str(lot))
        engine.run(2)
        objects = engine.active_objects()
        if "GameUIBuildingList" in objects:
            engine.press("GameUIBuildingList", "btn_build_clicked", str(args.build))
        else:
            engine.press("GameAllMine", "levelupPressed")
        engine.run(3)
        print(status(engine))
    missing = sorted(name for name in engine.missing if not name.startswith("global "))
    print(f"{len(engine.requests)} requests, native functions not implemented yet: {', '.join(missing) or 'none'}")


if __name__ == "__main__":
    sys.exit(main())
