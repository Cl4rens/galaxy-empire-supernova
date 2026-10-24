"""Star fortresses ("The Star Gate"), following the client's help texts:

- Alliances fight for a few star fortresses. To take part, the alliance leader buys a Gravity
  Field Beacon (declaration of war) from the alliance treasury.
- Members station fleets at the fortress. Every participating alliance has morale; the two
  alliances with the highest morale meet in the battle. Morale grows with the stationed fleets,
  with Battle Songs (bought from the treasury) and with victories in the fortress's interspace,
  where guard fleets wait to be attacked a few times a day.
- The winner holds the fortress until the next battle. Its members get more production of the
  fortress's resource, a daily supply gift and access to the fortress store.

Battles happen once a week (divided by the game speed). The war phase, in which alliances
declare war, sing and station fleets, is the last two days before a battle. All numbers are
Supernova's own.
"""

import json
import time

from . import alliances, combat, config, db, ecm, flagships, kryptons, mails, npc
from . import economy as eco
from . import planet as planet_rules

# id, name, interspace id, interspace name, resource with the production bonus, guard level
FORTRESSES = [
    (1, "Orion Fortress", 101, "Orion Rift", "metal", 3),
    (2, "Draco Fortress", 102, "Draco Rift", "crystal", 5),
    (3, "Hydra Fortress", 103, "Hydra Rift", "deuterium", 7),
]
FORTRESS, INTERSPACE = 1, 2  # hanging_type
ATTACK = 7  # the client sends both kinds of flight as attacks
OCCUPIED, NOT_OCCUPIED, WAR_PHASE = 1, 2, 3  # status
BATTLE_OFFSET = config.FORTRESS_BATTLE_OFFSET  # the first battle, seconds since 1970
DISTANCE = 5_000  # flights to the star gate are as long as across a large part of a galaxy
WAR_PRICE = 100  # dark matter from the treasury for a Gravity Field Beacon
SONGS = {2: ("Battle Song", 20, alliances.DARK_MATTER, 50), 3: ("Grand Battle Song", 50, alliances.CREDITS, 200)}
INTERSPACE_ATTACKS = 3  # per player and day
INTERSPACE_MORALE = 30  # for every won interspace battle
POINTS_PER_MORALE = 1000  # fleet points (metal + crystal) of stationed ships
MORALE_ADVANTAGE = 10  # percent weapons, shields and armour for the side with more morale
PRODUCTION_BONUS = 10  # percent
MAX_MEMBERS = 50
GIFT = {"metal": 50_000, "crystal": 30_000, "deuterium": 10_000}
GIFT_CHIPS = 5
STORE = [  # id, name, description, dark matter
    (1, "ECM chips x10", "Ten core chips for ECM module upgrades.", 40),
    (2, "Alloy fragment box", "250 alloy fragments for the Pioneer Lab.", 15),
    (3, "Krypton stones x1,000", "1,000 krypton stones for the Krypton Lab.", 30),
    (4, "Resource crate", "100,000 metal, 60,000 crystal and 20,000 deuterium.", 50),
]


class FortressError(planet_rules.GameError):
    pass


def _conn():
    return db.connect()


def _fortress(fortress_id: int) -> tuple:
    f = next((f for f in FORTRESSES if f[0] == fortress_id), None)
    if f is None:
        raise FortressError("This star fortress does not exist")
    return f


def _by_interspace(interspace_id: int) -> tuple:
    f = next((f for f in FORTRESSES if f[2] == interspace_id), None)
    if f is None:
        raise FortressError("This interspace does not exist")
    return f


# --- Schedule ---------------------------------------------------------------------------------


def cycle_seconds() -> int:
    return max(3600, int(7 * 86400 / config.SPEED))


def last_battle(now: float) -> int:
    """Number of the last battle that is due at `now`."""
    return int((now - BATTLE_OFFSET) // cycle_seconds())


def battle_time(number: int) -> int:
    return BATTLE_OFFSET + number * cycle_seconds()


def next_battle(now: float) -> int:
    """Number of the coming battle; declarations count for it."""
    return last_battle(now) + 1


def war_start(number: int) -> int:
    return battle_time(number) - cycle_seconds() * 2 // 7


def war_phase(now: float) -> bool:
    return now >= war_start(next_battle(now))


def _require_war_phase(now: float) -> None:
    if not war_phase(now):
        hours = (war_start(next_battle(now)) - now) / 3600
        raise FortressError(f"The war phase starts in {hours:.1f} hours")


# --- Owners, participants and morale ------------------------------------------------------------


def _row(fortress_id: int):
    conn = _conn()
    row = conn.execute("SELECT * FROM fortresses WHERE id = ?", (fortress_id,)).fetchone()
    if row is None:
        with db.lock(), conn:
            conn.execute("INSERT OR IGNORE INTO fortresses (id, battle) VALUES (?, ?)",
                         (fortress_id, last_battle(time.time())))
        row = conn.execute("SELECT * FROM fortresses WHERE id = ?", (fortress_id,)).fetchone()
    return row


def owner(fortress_id: int) -> int | None:
    return _row(fortress_id)["alliance_id"]


def _alliance_name(alliance_id: int | None) -> str:
    if not alliance_id:
        return ""
    row = _conn().execute("SELECT name FROM alliances WHERE id = ?", (alliance_id,)).fetchone()
    return row["name"] if row else ""


def _member_alliance(player_id: int) -> int | None:
    m = alliances.membership(player_id)
    return m["alliance_id"] if m else None


def _stationed(fortress_id: int, until: float | None = None) -> list:
    """Fleets stationed at the fortress (arrived by `until`), with their owner's alliance."""
    rows = _conn().execute(
        "SELECT e.*, m.alliance_id FROM fortress_events e JOIN alliance_members m ON m.player_id = e.owner_id "
        "WHERE e.kind = ? AND e.target_id = ? AND e.status = 'stationed' ORDER BY e.id",
        (FORTRESS, fortress_id)).fetchall()
    return [r for r in rows if until is None or r["arrival_time"] <= until]


def _ships(row) -> dict[int, int]:
    return {int(t): n for t, n in json.loads(row["ships"]).items()}


def participants(fortress_id: int, number: int, until: float | None = None) -> list[dict]:
    """The alliances in battle `number`: declared ones and the holder, by morale."""
    wars = {r["alliance_id"]: r["morale"] for r in _conn().execute(
        "SELECT alliance_id, morale FROM fortress_wars WHERE fortress_id = ? AND battle = ?", (fortress_id, number))}
    holder = owner(fortress_id)
    if holder and holder not in wars and _alliance_name(holder):
        wars[holder] = 0
    stationed = _stationed(fortress_id, until)
    result = []
    for alliance_id, bonus in wars.items():
        fleets = [r for r in stationed if r["alliance_id"] == alliance_id]
        points = sum(npc.fleet_points(_ships(r)) for r in fleets)
        result.append({"alliance_id": alliance_id, "alli_name": _alliance_name(alliance_id),
                       "morale": bonus + points // POINTS_PER_MORALE,
                       "member": len({r["owner_id"] for r in fleets}), "fleets": fleets})
    result.sort(key=lambda p: (-p["morale"], p["alliance_id"]))
    for i, p in enumerate(result):  # the battle bonus, shown as "Addition"
        ahead = i == 0 and (len(result) == 1 or p["morale"] > result[1]["morale"])
        p["morale_ratio"] = MORALE_ADVANTAGE if ahead else 0
    return result


def _participant(fortress_id: int, alliance_id: int | None, now: float) -> bool:
    if not alliance_id:
        return False
    if owner(fortress_id) == alliance_id:
        return True
    return _conn().execute("SELECT 1 FROM fortress_wars WHERE fortress_id = ? AND alliance_id = ? AND battle = ?",
                           (fortress_id, alliance_id, next_battle(now))).fetchone() is not None


def _add_morale(fortress_id: int, alliance_id: int, morale: int, now: float) -> None:
    conn = _conn()
    with db.lock(), conn:
        conn.execute("INSERT OR IGNORE INTO fortress_wars (fortress_id, alliance_id, battle) VALUES (?, ?, ?)",
                     (fortress_id, alliance_id, next_battle(now)))
        conn.execute("UPDATE fortress_wars SET morale = morale + ? WHERE fortress_id = ? AND alliance_id = ? "
                     "AND battle = ?", (morale, fortress_id, alliance_id, next_battle(now)))


# --- Holder rewards ----------------------------------------------------------------------------

_owners: dict[int, int | None] = {}
_owners_read = 0.0


def held_by(alliance_id: int | None) -> list[tuple]:
    """The fortresses an alliance holds (cached for a few seconds; production asks often)."""
    global _owners_read
    if not alliance_id:
        return []
    if time.time() - _owners_read > 5:
        _owners.clear()
        _owners.update({r["id"]: r["alliance_id"] for r in _conn().execute("SELECT id, alliance_id FROM fortresses")})
        _owners_read = time.time()
    return [f for f in FORTRESSES if _owners.get(f[0]) == alliance_id]


def production_bonus(empire: dict, resource: str) -> int:
    if empire.get("alliance_application_id") or not empire.get("alliance_id"):
        return 0
    return sum(PRODUCTION_BONUS for f in held_by(empire["alliance_id"]) if f[4] == resource)


def rewards_text(fortress: tuple) -> list[str]:
    return [f"{fortress[4].capitalize()} production +{PRODUCTION_BONUS} % on all planets",
            f"Daily supplies: {GIFT['metal']:,} metal, {GIFT['crystal']:,} crystal, "
            f"{GIFT['deuterium']:,} deuterium, {GIFT_CHIPS} ECM chips",
            "Access to the fortress store"]


def _fortress_state(state: dict) -> dict:
    d = state.setdefault("fortress", {"day": 0, "attacks": 0, "gifts": []})
    day = int(time.time() // 86400)
    if d["day"] != day:
        d.update(day=day, attacks=0, gifts=[])
    return d


def gifts(player_id: int) -> list[dict]:
    with db.player_state(player_id) as state:
        claimed = _fortress_state(state)["gifts"]
    return [{"id": f[0], "name": f"{f[1]} supplies", "image": "fortress_gift_default.png"}
            for f in held_by(_member_alliance(player_id)) if f[0] not in claimed]


def claim_gift(player_id: int, fortress_id: int) -> str:
    fortress = _fortress(fortress_id)
    if fortress not in held_by(_member_alliance(player_id)):
        raise FortressError("Your alliance does not hold this fortress")
    with db.player_state(player_id) as state:
        d = _fortress_state(state)
        if fortress_id in d["gifts"]:
            raise FortressError("You already received today's supplies")
        d["gifts"].append(fortress_id)
        planet = state["empire"]["planets"][0]
        for res, n in GIFT.items():
            planet[res] += n
        ecm.add_chips(state, GIFT_CHIPS)
    return (f"You received {GIFT['metal']:,} metal, {GIFT['crystal']:,} crystal, {GIFT['deuterium']:,} deuterium "
            f"and {GIFT_CHIPS} ECM chips on your home planet.")


def store(fortress_id: int) -> list[dict]:
    _fortress(fortress_id)
    return [{"id": i, "name": name, "desc": desc, "img": "item_res_06.png", "pay_type": alliances.DARK_MATTER,
             "sale_price": price} for i, name, desc, price in STORE]


def buy(player_id: int, fortress_id: int, item_id: int, count: int) -> dict:
    fortress = _fortress(fortress_id)
    if fortress not in held_by(_member_alliance(player_id)):
        raise FortressError("Only members of the alliance holding this fortress can shop here")
    item = next((i for i in STORE if i[0] == item_id), None)
    if item is None or not 1 <= count <= 99:
        raise FortressError("This item is not for sale")
    price = item[3] * count
    with db.player_state(player_id) as state:
        empire = state["empire"]
        if empire.get("dark_matter_count", 0) < price:
            raise FortressError(f"You need {price:,} dark matter")
        empire["dark_matter_count"] -= price
        for _ in range(count):
            if item_id == 1:
                ecm.add_chips(state, 10)
            elif item_id == 2:
                flagships.data(state)["alloy"] += flagships.BOX_ALLOY
            elif item_id == 3:
                kryptons.add_stones(state, 1000)
            else:
                planet = empire["planets"][0]
                for res, n in (("metal", 100_000), ("crystal", 60_000), ("deuterium", 20_000)):
                    planet[res] += n
        return {"darkmatter": empire["dark_matter_count"], "credits": empire.get("gu_count", 0),
                "info": f"You bought {item[1]} x{count}."}


# --- Declarations and the alliance store ---------------------------------------------------------


def alliance_store() -> list[dict]:
    beacon = {"id": 1, "name": "Gravity Field Beacon",
              "detail": "Declares war: your alliance joins the next battle for this fortress.",
              "price": WAR_PRICE, "pay_type": alliances.DARK_MATTER}
    songs = [{"id": i, "name": name, "detail": f"Raises the alliance's morale at this fortress by {morale}.",
              "price": price, "pay_type": pay} for i, (name, price, pay, morale) in SONGS.items()]
    return [beacon] + songs


def declare_war(player_id: int, fortress_id: int, now: float) -> str:
    fortress = _fortress(fortress_id)
    m = alliances.membership(player_id)
    if m is None or m["post"] != alliances.PRESIDENT:
        raise FortressError("Only the alliance leader can declare war")
    _require_war_phase(now)
    if _participant(fortress_id, m["alliance_id"], now):
        raise FortressError("Your alliance already takes part in this battle")
    try:
        alliances.spend(player_id, m["alliance_id"], alliances.DARK_MATTER, WAR_PRICE,
                        f"Gravity Field Beacon ({fortress[1]})")
    except alliances.AllianceError as e:
        raise FortressError(str(e)) from None
    _add_morale(fortress_id, m["alliance_id"], 0, now)
    return f"War declared! Station fleets at the {fortress[1]} before the battle."


def use_store_item(player_id: int, fortress_id: int, item_id: int, now: float) -> str:
    if item_id == 1:
        return declare_war(player_id, fortress_id, now)
    if item_id not in SONGS:
        raise FortressError("Unknown item")
    _require_war_phase(now)
    fortress = _fortress(fortress_id)
    alliance_id = _member_alliance(player_id)
    if not _participant(fortress_id, alliance_id, now):
        raise FortressError("Your alliance has not declared war on this fortress")
    name, price, pay, morale = SONGS[item_id]
    try:
        alliances.spend(player_id, alliance_id, pay, price, f"{name} ({fortress[1]})")
    except alliances.AllianceError as e:
        raise FortressError(str(e)) from None
    _add_morale(fortress_id, alliance_id, morale, now)
    return f"{name}: morale +{morale}."


# --- Views ---------------------------------------------------------------------------------------


def interspace_left(player_id: int) -> int:
    with db.player_state(player_id) as state:
        return max(0, INTERSPACE_ATTACKS - _fortress_state(state)["attacks"])


def info(fortress_id: int, player_id: int, now: float) -> dict:
    fortress = _fortress(fortress_id)
    holder = owner(fortress_id)
    m = alliances.membership(player_id)
    alliance_id = m["alliance_id"] if m else None
    number = next_battle(now)
    ranking = participants(fortress_id, number)
    mine = next((p for p in ranking if p["alliance_id"] == alliance_id), None)
    return {
        "fortress_id": fortress_id, "name": fortress[1], "lock": False,
        "status": WAR_PHASE if war_phase(now) else OCCUPIED if holder else NOT_OCCUPIED,
        "alliance_name": _alliance_name(holder),
        "declare_war": mine is not None,
        "our_rank": ranking.index(mine) + 1 if mine else 0,
        "apply_count": len(ranking),
        "alliance_info": {"apply_member": mine["member"] if mine else 0, "apply_max": MAX_MEMBERS},
        "war_price": f"{WAR_PRICE} DM",  # only shown; the client draws a credit icon next to it
        "is_president": bool(m and m["post"] == alliances.PRESIDENT),
        # the countdown: to the battle in the war phase, else to the war phase
        "start_time": [battle_time(number - 1), battle_time(number) if war_phase(now) else war_start(number)],
        "reward": rewards_text(fortress),
        "interspace_info": {"interspace_id": fortress[2], "name": fortress[3],
                            "left_count": interspace_left(player_id), "max_count": INTERSPACE_ATTACKS},
    }


def listing(player_id: int, now: float) -> list[dict]:
    process_due(now)
    return [info(f[0], player_id, now) for f in FORTRESSES]


def alliance_list(fortress_id: int, now: float) -> list[dict]:
    _fortress(fortress_id)
    return [{k: v for k, v in p.items() if k != "fleets"} for p in participants(fortress_id, next_battle(now))]


def guard_fleet(interspace_id: int) -> dict[int, int]:
    return npc.pirate_fleet(_by_interspace(interspace_id)[5])


def interspace_fleet(interspace_id: int) -> list[dict]:
    return [{"name": eco.UNITS[t].name, "count": n} for t, n in guard_fleet(interspace_id).items()]


# --- Flights -------------------------------------------------------------------------------------


def plan(empire: dict, ships: dict[int, int]) -> dict:
    levels = {r["research_type"]: r["research_level"] for r in empire["researches"]}
    speed = min(eco.ship_speed(t, levels) for t in ships)
    return {"missions": [ATTACK], "speed": speed, "total_time": eco.flight_seconds(DISTANCE, speed, 100),
            "fuel_consumption": eco.fuel(ships, DISTANCE, 100), "empty_cargobays": 0}


def send(player_id: int, state: dict, origin: dict, ships: dict[int, int], kind: int, target_id: int,
         now: float) -> None:
    empire = state["empire"]
    alliance_id = _member_alliance(player_id)
    if not alliance_id:
        raise FortressError("Join an alliance to fight for star fortresses")
    if kind == FORTRESS:
        _require_war_phase(now)
        _fortress(target_id)
        if not _participant(target_id, alliance_id, now):
            raise FortressError("Your alliance has not declared war on this fortress")
    elif kind == INTERSPACE:
        _by_interspace(target_id)
        d = _fortress_state(state)
        if d["attacks"] >= INTERSPACE_ATTACKS:
            raise FortressError("No more interspace attacks today")
        if not any(combat.STATS[t].weapon > 0 for t in ships):
            raise FortressError("These ships cannot fight")
    else:
        raise FortressError("Invalid target")
    if ships.get(flagships.BEHEMOTH, 0) > flagships.behemoth_limit(state):
        raise FortressError("Too many Iron Behemoths in one fleet")
    info_ = plan(empire, ships)
    if origin["deuterium"] < info_["fuel_consumption"]:
        raise FortressError("Not enough deuterium")
    origin["deuterium"] -= info_["fuel_consumption"]
    for ftype, n in ships.items():
        planet_rules.fleet(origin, ftype)["amount"] -= n
    if kind == INTERSPACE:
        _fortress_state(state)["attacks"] += 1
    flight = info_["total_time"]
    conn = _conn()
    with db.lock(), conn:
        conn.execute("INSERT INTO fortress_events (owner_id, planet_id, kind, target_id, ships, flight, start_time, "
                     "arrival_time, return_time, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'outbound')",
                     (player_id, origin["planet_id"], kind, target_id,
                      json.dumps({str(t): n for t, n in ships.items()}), flight, int(now), int(now) + flight,
                      int(now) + 2 * flight))


def recall(player_id: int, event_id: int, now: float) -> None:
    conn = _conn()
    with db.lock(), conn:
        row = conn.execute("SELECT * FROM fortress_events WHERE id = ? AND owner_id = ?",
                           (event_id, player_id)).fetchone()
        if row is None or row["status"] == "returning":
            raise FortressError("This fleet cannot be recalled")
        if row["status"] == "outbound":
            back = int(now) + max(1, int(now) - row["start_time"])
        else:
            back = int(now) + row["flight"]
        conn.execute("UPDATE fortress_events SET status = 'returning', return_time = ? WHERE id = ?",
                     (back, event_id))


def _target_name(row) -> str:
    if row["kind"] == FORTRESS:
        return _fortress(row["target_id"])[1]
    return _by_interspace(row["target_id"])[3]


def events_view(player_id: int, now: float | None = None) -> list[dict]:
    """The player's star gate flights, as `empire.fortress_interspace_events`."""
    now = time.time() if now is None else now
    events = []
    for row in _conn().execute("SELECT * FROM fortress_events WHERE owner_id = ? ORDER BY id", (player_id,)):
        planet = _conn().execute("SELECT * FROM planets WHERE id = ?", (row["planet_id"],)).fetchone()
        if row["status"] == "returning":
            start, finish = row["return_time"] - row["flight"], row["return_time"]
            name = "Return"
        elif row["status"] == "stationed":
            start, finish = row["arrival_time"], max(battle_time(next_battle(now)), row["arrival_time"] + 1)
            name = "Garrison"
        else:
            start, finish = row["start_time"], row["arrival_time"]
            name = "Attack"
        events.append({
            "id": row["id"], "planet_id": row["planet_id"],
            "planet_name": planet["name"] if planet else "",
            "planet_position": f"{planet['galaxy']}:{planet['system']}:{planet['position']}" if planet else "",
            "end_planet_name": _target_name(row), "end_planet_pos": "Star Gate",
            "mission_name": name, "origin_mission": ATTACK,
            "is_returning": 1 if row["status"] == "returning" else 0,
            "start_time": start, "finish_time": finish, "total_time": max(1, finish - start),
            "show_hanging": True,
            "fleets": [{"fleet_type": t, "fleet_count": n} for t, n in _ships(row).items()],
        })
    return events


# --- Processing ----------------------------------------------------------------------------------


def _report(player_id: int, subject: str, report: dict, notes: list[str], target: str, ships: dict[int, int],
            when: int) -> None:
    from . import fleets  # fleets imports this module
    info_ = {"mission": ATTACK, "mission_name": "Attack", "start_planet_name": "", "start_planet_pos": "",
             "end_planet_name": target, "end_planet_pos": "Star Gate", "finish_time": when,
             "resource_metal": 0, "resource_crystal": 0, "resource_deuterium": 0,
             "hanging_fleets": [{"fleet_type": t, "fleet_count": n} for t, n in ships.items()]}
    body = {"mission_type": fleets.ATTACK, "is_returning": False, "event_info": info_,
            "notify_message": ", ".join(notes), "attack_result": dict(report, loot={"metal": 0, "crystal": 0,
                                                                                     "deuterium": 0})}
    mails.send(player_id, mails.REPORT, mails.SENDER_FLEET, fleets._username(player_id), subject, body, when)


def _interspace_battle(row) -> None:
    from . import fleets
    fortress = _by_interspace(row["target_id"])
    level = fortress[5]
    state = db.load_state(row["owner_id"])
    name = fleets._username(row["owner_id"])
    ships = _ships(row)
    guard = combat.Side(f"{fortress[3]} Guard", guard_fleet(fortress[2]), level, level, level)
    bonus = {f"{k}_bonus_percent": 10 * level for k in ("attack", "shield", "armour")}
    result = fleets._battle(row, fleets._side(name, state["empire"], ships, None, state),
                            fleets._tech_bonus(state["empire"]), guard, bonus)
    notes = []
    if result["winner"] == "attacker":
        loot = npc.pirate_loot(level)
        with db.player_state(row["owner_id"]) as s:
            planet = planet_rules.find_planet(s["empire"], row["planet_id"]) or s["empire"]["planets"][0]
            for res, n in loot.items():
                planet[res] += n
        notes.append(f"Supplies found: {loot['metal']:,} metal, {loot['crystal']:,} crystal, "
                     f"{loot['deuterium']:,} deuterium")
        alliance_id = _member_alliance(row["owner_id"])
        if _participant(fortress[0], alliance_id, row["arrival_time"]):
            _add_morale(fortress[0], alliance_id, INTERSPACE_MORALE, row["arrival_time"])
            notes.append(f"Alliance morale at the {fortress[1]} +{INTERSPACE_MORALE}")
    _report(row["owner_id"], f"Battle report [{fortress[3]}]", result["report"], notes, fortress[3], ships,
            row["arrival_time"])
    survivors = result["attacker_left"]
    conn = _conn()
    with db.lock(), conn:
        if survivors:
            conn.execute("UPDATE fortress_events SET ships = ?, status = 'returning' WHERE id = ?",
                         (json.dumps({str(t): n for t, n in survivors.items()}), row["id"]))
        else:
            conn.execute("DELETE FROM fortress_events WHERE id = ?", (row["id"],))


def _alliance_side(name: str, fleets_: list, bonus_percent: int):
    """One side of a fortress battle: all stationed ships of an alliance, with the technology of
    the member who sent the most."""
    from . import fleets
    units: dict[int, int] = {}
    for r in fleets_:
        for t, n in _ships(r).items():
            units[t] = units.get(t, 0) + n
    leader = max(fleets_, key=lambda r: npc.fleet_points(_ships(r)))
    state = db.load_state(leader["owner_id"])
    side = fleets._side(name, state["empire"], units, None, state)
    side = combat.Side(name, units, side.weapons, side.shielding, side.armour, side.attack_percent + bonus_percent,
                       side.shield_percent + bonus_percent, side.armour_percent + bonus_percent)
    return side, fleets._tech_bonus(state["empire"])


def fight(fortress_id: int, number: int) -> int | None:
    """Battle `number` for a fortress. Returns the alliance holding it afterwards."""
    from . import fleets
    fortress = _fortress(fortress_id)
    when = battle_time(number)
    holder = owner(fortress_id)
    ranking = [p for p in participants(fortress_id, number, until=when) if p["fleets"]]
    new_holder = holder
    if len(ranking) == 1:
        new_holder = ranking[0]["alliance_id"]
        for r in ranking[0]["fleets"]:
            mails.send(r["owner_id"], mails.REPORT, mails.SENDER_FLEET, fleets._username(r["owner_id"]),
                       f"{fortress[1]}", {"mission_type": 0, "notify_message":
                                          f"{ranking[0]['alli_name']} holds the {fortress[1]} without a fight."},
                       when)
    elif len(ranking) >= 2:
        first, second = ranking[0], ranking[1]
        # the holder defends; without the holder among them, the alliance with less morale does
        attacker, defender = (first, second) if first["alliance_id"] != holder else (second, first)
        advantage = {p["alliance_id"]: MORALE_ADVANTAGE if p is first and first["morale"] > second["morale"] else 0
                     for p in (first, second)}
        a_side, a_bonus = _alliance_side(attacker["alli_name"], attacker["fleets"], advantage[attacker["alliance_id"]])
        d_side, d_bonus = _alliance_side(defender["alli_name"], defender["fleets"], advantage[defender["alliance_id"]])
        result = fleets._battle(None, a_side, a_bonus, d_side, d_bonus)
        if result["winner"] == "attacker":
            new_holder = attacker["alliance_id"]
        elif result["winner"] == "defender" or holder == defender["alliance_id"]:
            new_holder = defender["alliance_id"]
        conn = _conn()
        for side, left in ((attacker, result["attacker_left"]), (defender, result["defender_left"])):
            parts = [_ships(r) for r in side["fleets"]]
            with db.lock(), conn:
                for r, share in zip(side["fleets"], fleets._share(left, parts)):
                    if share:
                        conn.execute("UPDATE fortress_events SET ships = ? WHERE id = ?",
                                     (json.dumps({str(t): n for t, n in share.items()}), r["id"]))
                    else:
                        conn.execute("DELETE FROM fortress_events WHERE id = ?", (r["id"],))
        winner = _alliance_name(new_holder) if new_holder else "nobody"
        for side in (attacker, defender):
            for r in side["fleets"]:
                _report(r["owner_id"], f"Battle for the {fortress[1]}", result["report"],
                        [f"The {fortress[1]} is held by {winner}"], fortress[1], _ships(r), when)
    conn = _conn()
    with db.lock(), conn:
        conn.execute("UPDATE fortresses SET alliance_id = ?, battle = ? WHERE id = ?",
                     (new_holder, number, fortress_id))
        conn.execute("UPDATE fortress_events SET status = 'returning', return_time = ? + flight "
                     "WHERE kind = ? AND target_id = ? AND status = 'stationed'", (when, FORTRESS, fortress_id))
        conn.execute("DELETE FROM fortress_wars WHERE fortress_id = ? AND battle <= ?", (fortress_id, number))
    global _owners_read
    _owners_read = 0
    return new_holder


def _return(row) -> None:
    with db.player_state(row["owner_id"]) as state:
        planet = planet_rules.find_planet(state["empire"], row["planet_id"]) or state["empire"]["planets"][0]
        planet_rules.tick(state["empire"], row["return_time"])
        for ftype, n in _ships(row).items():
            planet_rules.fleet(planet, ftype)["amount"] += n


def process_due(now: float | None = None) -> None:
    now = time.time() if now is None else now
    conn = _conn()
    due = last_battle(now)
    for f in FORTRESSES:
        row = _row(f[0])
        if row["battle"] < due:
            # arrivals before the battle count for it
            _arrivals(min(now, battle_time(due)))
            fight(f[0], due)
    _arrivals(now)
    for row in conn.execute("SELECT * FROM fortress_events WHERE status = 'returning' AND return_time <= ?",
                            (now,)).fetchall():
        _return(row)
        with db.lock(), conn:
            conn.execute("DELETE FROM fortress_events WHERE id = ?", (row["id"],))


def _arrivals(now: float) -> None:
    conn = _conn()
    for row in conn.execute("SELECT * FROM fortress_events WHERE status = 'outbound' AND arrival_time <= ?",
                            (now,)).fetchall():
        if row["kind"] == FORTRESS:
            with db.lock(), conn:
                conn.execute("UPDATE fortress_events SET status = 'stationed' WHERE id = ?", (row["id"],))
        else:
            _interspace_battle(row)
