"""Command line tools for server operators.

    python -m supernova.admin list
    python -m supernova.admin show <username>
    python -m supernova.admin give <username> metal=10000 crystal=5000 deuterium=2000 credits=500 dark_matter=50
    python -m supernova.admin set-building <username> <building_type> <level> [lot]
    python -m supernova.admin give-fleet <username> 9=5 1=10      (fleet_type=count)
    python -m supernova.admin set-research <username> <research_type> <level>
    python -m supernova.admin create-moon <username> [planet_id]
    python -m supernova.admin chat-reports
    python -m supernova.admin news-add <normal|update|maintain|emergency> <title> <text>
    python -m supernova.admin news-list
    python -m supernova.admin news-delete <id>
    python -m supernova.admin gift <username|all> <title> <description> metal=5000 9=2 ...
    python -m supernova.admin delete-chat <message_id>
    python -m supernova.admin gc-start [--in MINUTES] [--rounds 96] [--round-minutes 60]
    python -m supernova.admin gc-stop
    python -m supernova.admin gc-status
    python -m supernova.admin arena-start [--in MINUTES] [--garrison-hours 24] [--challenge-days 6] [--prize-days 1]
    python -m supernova.admin arena-stop
    python -m supernova.admin events
    python -m supernova.admin stats
    python -m supernova.admin backup
    python -m supernova.admin ban <username> "<reason>"
    python -m supernova.admin unban <username>

Run it while the server is running or stopped; changes are visible at the player's next request.
"""

import argparse
import json
import time

from . import db
from . import economy as eco
from . import planet as planet_rules


def player_id(username: str) -> int:
    found = db.find_player(username)
    if not found:
        raise SystemExit(f"No player named {username!r}")
    return found[0]


def cmd_list(args) -> None:
    for row in db.connect().execute("SELECT id, username, created_at FROM players ORDER BY id"):
        print(f"{row['id']:>6}  {row['username']:<24} {time.strftime('%Y-%m-%d %H:%M', time.localtime(row['created_at']))}")


def cmd_show(args) -> None:
    with db.player_state(player_id(args.username)) as state:
        planet_rules.tick(state["empire"], time.time())
        p = state["empire"]["main_planet"]
        print(json.dumps({
            "resources": {r: int(p[r]) for r in planet_rules.RESOURCES},
            "buildings": {eco.BUILDINGS[b["building_type"]].name: b["building_level"]
                          for b in p["buildings"] if b["building_level"]},
            "researches": {eco.RESEARCHES[r["research_type"]].name: r["research_level"]
                           for r in state["empire"]["researches"]
                           if r["research_level"] and r["research_type"] in eco.RESEARCHES},
        }, indent=2))


CURRENCIES = {"dark_matter": "dark_matter_count", "credits": "gu_count"}


def cmd_give(args) -> None:
    amounts = {}
    for item in args.amounts:
        resource, _, value = item.partition("=")
        if resource not in (*planet_rules.RESOURCES, *CURRENCIES) or not value.lstrip("-").isdigit():
            raise SystemExit(f"Expected name=amount with name in {(*planet_rules.RESOURCES, *CURRENCIES)}, got {item!r}")
        amounts[resource] = int(value)
    with db.player_state(player_id(args.username)) as state:
        empire = state["empire"]
        planet_rules.tick(empire, time.time())
        p = empire["main_planet"]
        for resource, value in amounts.items():
            if resource in CURRENCIES:
                empire[CURRENCIES[resource]] = max(0, empire.get(CURRENCIES[resource], 0) + value)
            else:
                p[resource] = max(0, p[resource] + value)
    print("ok")


def cmd_give_fleet(args) -> None:
    amounts = {}
    for item in args.amounts:
        ftype, _, value = item.partition("=")
        if not ftype.isdigit() or not value.lstrip("-").isdigit():
            raise SystemExit(f"Expected fleet_type=count, got {item!r}")
        amounts[int(ftype)] = int(value)
    with db.player_state(player_id(args.username)) as state:
        p = state["empire"]["main_planet"]
        for ftype, value in amounts.items():
            f = next((f for f in p["fleets"] if f["fleet_type"] == ftype), None)
            if f is None:
                raise SystemExit(f"Unknown fleet type {ftype}")
            f["amount"] = max(0, f["amount"] + value)
    print("ok")


def cmd_set_research(args) -> None:
    with db.player_state(player_id(args.username)) as state:
        research = next((r for r in state["empire"]["researches"] if r["research_type"] == args.research_type), None)
        if research is None:
            raise SystemExit(f"Unknown research type {args.research_type}")
        research["research_level"] = args.level
    print("ok")


def cmd_set_building(args) -> None:
    if args.building_type not in eco.BUILDINGS:
        raise SystemExit(f"Unknown building type {args.building_type}")
    with db.player_state(player_id(args.username)) as state:
        planet_rules.tick(state["empire"], time.time())
        b = planet_rules.building(state["empire"]["main_planet"], args.building_type)
        b["building_level"] = args.level
        if args.lot is not None:
            b["building_position_index"] = args.lot
        elif args.level > 0 and not b["building_position_index"]:
            raise SystemExit("This building has no lot yet; pass one (4-15)")
    print("ok")


def cmd_create_moon(args) -> None:
    from . import player

    pid = player_id(args.username)
    with db.player_state(pid) as state:
        planets = state["empire"]["planets"]
        planet = next((p for p in planets if p["planet_id"] == args.planet_id), None) if args.planet_id else planets[0]
        if planet is None:
            raise SystemExit("No such planet")
        moon = player.add_moon(pid, planet)
        if moon is None:
            raise SystemExit("This planet already has a moon")
    print(f"moon {moon['planet_id']} at {planet['galaxy_position']}:{planet['system_position']}:"
          f"{planet['planet_position']}")


def cmd_chat_reports(args) -> None:
    rows = db.connect().execute(
        "SELECT r.*, p.username AS reporter, a.username AS author FROM chat_reports r "
        "JOIN players p ON p.id = r.reporter_id "
        "LEFT JOIN chat_messages m ON m.id = r.message_id LEFT JOIN players a ON a.id = m.player_id "
        "ORDER BY r.id").fetchall()
    for r in rows:
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(r["created_at"]))
        print(f"{when}  message {r['message_id']} by {r['author'] or '(deleted)'}, reported by {r['reporter']}")
        print(f"    {r['words']}")
        if r["notes"]:
            print(f"    note: {r['notes']}")


def cmd_delete_chat(args) -> None:
    conn = db.connect()
    with db.lock(), conn:
        conn.execute("DELETE FROM chat_messages WHERE id = ?", (args.message_id,))
    print("ok")


def cmd_news_add(args) -> None:
    from . import notices
    print(f"news {notices.add(args.notice_type, args.title, args.text)}")


def cmd_news_list(args) -> None:
    from . import notices
    for n in notices.listing():
        when = time.strftime("%Y-%m-%d %H:%M", time.localtime(n["start_time"]))
        print(f"{n['id']:>4}  {when}  {n['notice_type']:<9} {n['title']}")


def cmd_news_delete(args) -> None:
    from . import notices
    notices.delete(args.notice_id)
    print("ok")


def cmd_gift(args) -> None:
    from . import gifts, rewards
    try:
        resources, ships = rewards.parse(args.rewards)
    except ValueError as e:
        raise SystemExit(str(e))
    if args.username == "all":
        print(f"credited to {gifts.credit_all(args.title, args.description, resources, ships)} players")
    else:
        gifts.credit(player_id(args.username), args.title, args.description, resources, ships)
        print("ok")


def cmd_gc_start(args) -> None:
    from . import galaxy_challenge as gc
    at = time.time() + args.in_minutes * 60
    event_id = gc.start(at, args.rounds, args.round_minutes * 60)
    print(f"Galaxy Challenge {event_id} starts {time.strftime('%Y-%m-%d %H:%M', time.localtime(at))} "
          f"with {args.rounds} battles of {args.round_minutes} minutes")


def cmd_gc_stop(args) -> None:
    from . import galaxy_challenge as gc
    gc.stop(time.time())
    print("ok")


def cmd_gc_status(args) -> None:
    from . import galaxy_challenge as gc
    now = time.time()
    with gc.event(now, hand_out=True) as ev:
        if ev is None:
            print("No Galaxy Challenge yet")
            return
        names = {0: "not running", 1: "battle", 2: "finished (exchange still open)"}
        print(f"Event {ev['id']}: {names[gc.status(ev, now)]}, battle {ev['closed']}/{ev['rounds']} done, "
              f"ends {time.strftime('%Y-%m-%d %H:%M', time.localtime(gc.end_time(ev)))}")
        for aid, a in gc._alliance_rows(ev):
            print(f"  {gc._alliance_name(aid):<20} {a['score']:>10,} points")


def cmd_arena_start(args) -> None:
    from . import arena
    at = time.time() + args.in_minutes * 60
    event_id = arena.start(at, args.garrison_hours * 3600, args.challenge_days * 86400, args.prize_days * 86400,
                           args.day_minutes * 60)
    print(f"Conquer The Universe {event_id} starts {time.strftime('%Y-%m-%d %H:%M', time.localtime(at))}")


def cmd_arena_stop(args) -> None:
    from . import arena
    arena.stop(time.time())
    print("ok: the prize stage has begun")


def cmd_events(args) -> None:
    from . import arena
    from . import galaxy_challenge as gc
    now = time.time()
    with gc.event(now, hand_out=True) as ev:
        names = {0: "not running", 1: "battle", 2: "finished (exchange still open)"}
        print("Galaxy Challenge:", f"event {ev['id']}, {names[gc.status(ev, now)]}" if ev else "none yet")
    with arena.event(now) as ev:
        names = {0: "not running", 1: "garrison", 2: "challenge", 3: "prize"}
        print("Conquer The Universe:", f"event {ev['id']}, {names[arena.stage(ev, now)]}" if ev else "none yet")


def cmd_stats(args) -> None:
    from . import operations
    for key, value in operations.stats().items():
        print(f"{key:<14} {value}")


def cmd_icons_list(args) -> None:
    from . import icons
    wanted = icons.missing()
    if not wanted:
        print("the server has all item pictures")
    for wiki in wanted:
        print(f"{wiki}  {icons.WIKI_FILE_PAGE}{wiki}")


def cmd_import_icons(args) -> None:
    from pathlib import Path

    from . import icons
    imported, problems = icons.import_folder(Path(args.folder))
    for p in problems:
        print(p)
    print(f"{len(imported)} pictures imported")


def cmd_backup(args) -> None:
    from . import operations
    print(operations.backup())


def cmd_ban(args) -> None:
    from . import operations
    try:
        operations.ban(args.username, args.reason)
    except ValueError as e:
        raise SystemExit(str(e))
    print("ok")


def cmd_unban(args) -> None:
    from . import operations
    try:
        operations.unban(args.username)
    except ValueError as e:
        raise SystemExit(str(e))
    print("ok")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(required=True)
    sub.add_parser("list").set_defaults(func=cmd_list)
    show = sub.add_parser("show")
    show.add_argument("username")
    show.set_defaults(func=cmd_show)
    give = sub.add_parser("give")
    give.add_argument("username")
    give.add_argument("amounts", nargs="+")
    give.set_defaults(func=cmd_give)
    givef = sub.add_parser("give-fleet")
    givef.add_argument("username")
    givef.add_argument("amounts", nargs="+")
    givef.set_defaults(func=cmd_give_fleet)
    setr = sub.add_parser("set-research")
    setr.add_argument("username")
    setr.add_argument("research_type", type=int)
    setr.add_argument("level", type=int)
    setr.set_defaults(func=cmd_set_research)
    setb = sub.add_parser("set-building")
    setb.add_argument("username")
    setb.add_argument("building_type", type=int)
    setb.add_argument("level", type=int)
    setb.add_argument("lot", type=int, nargs="?")
    setb.set_defaults(func=cmd_set_building)
    moon = sub.add_parser("create-moon")
    moon.add_argument("username")
    moon.add_argument("planet_id", type=int, nargs="?")
    moon.set_defaults(func=cmd_create_moon)
    sub.add_parser("chat-reports").set_defaults(func=cmd_chat_reports)
    news_add = sub.add_parser("news-add")
    news_add.add_argument("notice_type")
    news_add.add_argument("title")
    news_add.add_argument("text")
    news_add.set_defaults(func=cmd_news_add)
    sub.add_parser("news-list").set_defaults(func=cmd_news_list)
    news_delete = sub.add_parser("news-delete")
    news_delete.add_argument("notice_id", type=int)
    news_delete.set_defaults(func=cmd_news_delete)
    gift = sub.add_parser("gift")
    gift.add_argument("username")
    gift.add_argument("title")
    gift.add_argument("description")
    gift.add_argument("rewards", nargs="+")
    gift.set_defaults(func=cmd_gift)
    delchat = sub.add_parser("delete-chat")
    delchat.add_argument("message_id", type=int)
    delchat.set_defaults(func=cmd_delete_chat)
    gc_start = sub.add_parser("gc-start")
    gc_start.add_argument("--in", dest="in_minutes", type=float, default=0)
    gc_start.add_argument("--rounds", type=int, default=96)
    gc_start.add_argument("--round-minutes", type=float, default=60)
    gc_start.set_defaults(func=cmd_gc_start)
    sub.add_parser("gc-stop").set_defaults(func=cmd_gc_stop)
    sub.add_parser("gc-status").set_defaults(func=cmd_gc_status)
    arena_start = sub.add_parser("arena-start")
    arena_start.add_argument("--in", dest="in_minutes", type=float, default=0)
    arena_start.add_argument("--garrison-hours", type=float, default=24)
    arena_start.add_argument("--challenge-days", type=float, default=6)
    arena_start.add_argument("--prize-days", type=float, default=1)
    arena_start.add_argument("--day-minutes", type=float, default=1440, help="time between daily prizes")
    arena_start.set_defaults(func=cmd_arena_start)
    sub.add_parser("arena-stop").set_defaults(func=cmd_arena_stop)
    sub.add_parser("events").set_defaults(func=cmd_events)
    sub.add_parser("stats").set_defaults(func=cmd_stats)
    sub.add_parser("backup").set_defaults(func=cmd_backup)
    sub.add_parser("icons-list").set_defaults(func=cmd_icons_list)
    import_icons = sub.add_parser("import-icons")
    import_icons.add_argument("folder")
    import_icons.set_defaults(func=cmd_import_icons)
    ban = sub.add_parser("ban")
    ban.add_argument("username")
    ban.add_argument("reason", nargs="?", default="Your account is banned.")
    ban.set_defaults(func=cmd_ban)
    unban = sub.add_parser("unban")
    unban.add_argument("username")
    unban.set_defaults(func=cmd_unban)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
