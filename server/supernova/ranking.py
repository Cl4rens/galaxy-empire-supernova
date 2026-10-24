"""Rankings: score, fleet power and battle statistics.

Score is OGame's: one point per 1,000 resources spent on buildings, research and units
(all planets). Fleet power counts only ships and defense. Rankings are computed when asked
for, which is fine for community-sized servers.
"""

from . import db
from . import economy as eco
from . import planet as planet_rules

AROUND_ME = 10  # entries above and below the player in the "my rank" lists


def _cost_sum(costs) -> int:
    return sum(sum(c) for c in costs)


def points(state: dict) -> dict:
    empire = state["empire"]
    spent_buildings = spent_research = spent_units = 0
    for planet in planet_rules.bodies(empire):
        for b in planet["buildings"]:
            spent_buildings += _cost_sum(eco.upgrade_cost(b["building_type"], lv) for lv in range(b["building_level"]))
        for f in planet["fleets"]:
            if f["fleet_type"] in eco.UNITS:
                spent_units += sum(eco.unit_cost(f["fleet_type"])) * f["amount"]
    for r in empire["researches"]:
        if r["research_type"] in eco.RESEARCHES:
            spent_research += _cost_sum(eco.research_cost(r["research_type"], lv) for lv in range(r["research_level"]))
    return {
        "score": (spent_buildings + spent_research + spent_units) // 1000,
        "power": spent_units // 1000,
    }


def record_battle(player_id: int, outcome: str) -> None:
    """outcome: 'win', 'lose' or 'draw' from this player's view."""
    column = {"win": "wins", "lose": "losses", "draw": "draws"}[outcome]
    conn = db.connect()
    with db.lock(), conn:
        conn.execute("INSERT INTO battle_stats (player_id) VALUES (?) ON CONFLICT DO NOTHING", (player_id,))
        conn.execute(f"UPDATE battle_stats SET {column} = {column} + 1 WHERE player_id = ?", (player_id,))


def _players() -> list[dict]:
    conn = db.connect()
    stats = {r["player_id"]: r for r in conn.execute("SELECT * FROM battle_stats")}
    players = []
    for row in conn.execute("SELECT id, username FROM players"):
        state = db.load_state(row["id"])
        p = points(state)
        s = stats.get(row["id"])
        players.append({
            "id": row["id"],
            "username": row["username"],
            "alliance_name": state["empire"].get("alliance_name", ""),
            "score": p["score"],
            "power": p["power"],
            "fight_win_count": s["wins"] if s else 0,
            "fight_lose_count": s["losses"] if s else 0,
            "fight_draw_count": s["draws"] if s else 0,
        })
    return players


def _ranked(players: list[dict], key, rank_field: str = "rank") -> list[dict]:
    ordered = sorted(players, key=key, reverse=True)
    for i, p in enumerate(ordered, start=1):
        p[rank_field] = i
    return ordered


def _around(ordered: list[dict], player_id: int) -> list[dict]:
    index = next((i for i, p in enumerate(ordered) if p["id"] == player_id), 0)
    return ordered[max(0, index - AROUND_ME): index + AROUND_ME + 1]


def ranking(kind: str, player_id: int, top: int = 100) -> list[dict]:
    """kind is the client's `do` parameter."""
    players = _players()
    if kind in ("top_users", "my_rank"):
        ordered = _ranked(players, lambda p: (p["score"], -p["id"]))
    elif kind in ("top_fight_score", "my_fight_score"):
        ordered = [dict(p, score=p["power"]) for p in _ranked(players, lambda p: (p["power"], -p["id"]))]
    elif kind in ("top_fight_users", "my_fight_rank"):
        ordered = _ranked(players, lambda p: (p["fight_win_count"], -p["fight_lose_count"], -p["id"]),
                          "fight_rank")
    elif kind in ("top_alliances", "my_alliance"):
        from . import alliances
        rows = [{"id": a["alliance_id"], "rank": a["alliance_rank"], "alliance_name": a["alliance_name"],
                 "president_name": a["president_name"], "score": a["score"]} for a in alliances.listing()]
        if kind == "my_alliance":
            mine = alliances.membership(player_id)
            return [r for r in rows if mine and r["id"] == mine["alliance_id"]]
        return rows[:top]
    else:
        return []
    return _around(ordered, player_id) if kind.startswith("my_") else ordered[:top]


def rank_of(player_id: int) -> int:
    ordered = _ranked(_players(), lambda p: (p["score"], -p["id"]))
    return next((p["rank"] for p in ordered if p["id"] == player_id), 0)
