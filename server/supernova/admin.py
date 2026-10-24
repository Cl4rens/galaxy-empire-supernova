"""Command line tools for server operators.

    python -m supernova.admin list
    python -m supernova.admin show <username>
    python -m supernova.admin give <username> metal=10000 crystal=5000 deuterium=2000
    python -m supernova.admin set-building <username> <building_type> <level> [lot]
    python -m supernova.admin give-fleet <username> 9=5 1=10      (fleet_type=count)
    python -m supernova.admin set-research <username> <research_type> <level>
    python -m supernova.admin chat-reports
    python -m supernova.admin delete-chat <message_id>

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


def cmd_give(args) -> None:
    amounts = {}
    for item in args.amounts:
        resource, _, value = item.partition("=")
        if resource not in planet_rules.RESOURCES or not value.lstrip("-").isdigit():
            raise SystemExit(f"Expected resource=amount with resource in {planet_rules.RESOURCES}, got {item!r}")
        amounts[resource] = int(value)
    with db.player_state(player_id(args.username)) as state:
        planet_rules.tick(state["empire"], time.time())
        p = state["empire"]["main_planet"]
        for resource, value in amounts.items():
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
    sub.add_parser("chat-reports").set_defaults(func=cmd_chat_reports)
    delchat = sub.add_parser("delete-chat")
    delchat.add_argument("message_id", type=int)
    delchat.set_defaults(func=cmd_delete_chat)
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
