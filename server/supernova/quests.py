"""Tutorial quests: a chain of small goals with rewards, shown by the client's quest window.

The client knows each quest by its index (it opens arrows and hints for some of them), so the
indices are the client's "old" quest numbers (`quests_new_ver = 0`). Quests for features this
server does not have (gifts, the shop, kryptons, e-mail) are left out. Titles, texts and
rewards are Supernova's own; the original ones came from the server and are lost.

A quest is rewarded when the player opens the quest window after reaching its goals. Fleet
missions count for later quests too, so nobody has to repeat them.
"""

from dataclasses import dataclass, field
from typing import Callable

from . import db
from . import economy as eco
from . import planet as planet_rules

# Reward types of the client (GameUIQuest.REWARD_TYPE_*)
REWARD_RESOURCE, REWARD_FLEET = 1, 2
RESOURCE_SUBTYPES = {"metal": 1, "crystal": 2, "deuterium": 3}

# Actions recorded when a fleet is sent (see record_action)
ESPIONAGE, ATTACK_PIRATES, EXPEDITION, TRANSPORT, DEPLOY, ATTACK_PLAYER, RECYCLE = (
    "espionage", "attack_pirates", "expedition", "transport", "deploy", "attack_player", "recycle")


@dataclass(frozen=True)
class Target:
    title: str
    done: Callable[[dict, int], bool]  # (state, player_id) -> reached


@dataclass(frozen=True)
class Quest:
    index: int
    title: str
    description: str
    congratulation: str
    targets: list[Target]
    resources: dict[str, int] = field(default_factory=dict)
    ships: dict[int, int] = field(default_factory=dict)
    hints: list[str] = field(default_factory=list)


def _max_level(state: dict, btype: int) -> int:
    return max(planet_rules.level(p, btype) for p in state["empire"]["planets"])


def building(btype: int, lvl: int) -> Target:
    return Target(f"{eco.BUILDINGS[btype].name} level {lvl}", lambda s, _: _max_level(s, btype) >= lvl)


def research(rtype: int, lvl: int) -> Target:
    def done(state, _):
        return any(r["research_type"] == rtype and r["research_level"] >= lvl for r in state["empire"]["researches"])
    return Target(f"Research {eco.RESEARCHES[rtype].name} level {lvl}", done)


def ships(ftype: int, n: int) -> Target:
    def done(state, _):
        return sum(planet_rules.fleet(p, ftype)["amount"] for p in state["empire"]["planets"]) >= n
    return Target(f"Build {n} {eco.UNITS[ftype].name}", done)


def action(name: str, title: str) -> Target:
    return Target(title, lambda s, _: name in s.get("quests", {}).get("actions", []))


def _registered(state: dict, player_id: int) -> bool:
    row = db.connect().execute("SELECT password FROM players WHERE id = ?", (player_id,)).fetchone()
    return bool(row and row["password"])


def _renamed(state: dict, _) -> bool:
    from . import player  # player imports fleets, which imports this module
    return any(p["planet_name"] not in (player.HOME_PLANET_NAME, player.COLONY_NAME) for p in state["empire"]["planets"])


B = eco
QUESTS = [
    Quest(2, "Mining", "Every empire starts with metal and crystal. Build a metal mine and a crystal mine.",
          "Your mines are running.",
          [building(B.METAL_MINE, 1), building(B.CRYSTAL_MINE, 1)], {"metal": 200, "crystal": 100}),
    Quest(3, "More metal", "Metal is needed for almost everything. Upgrade the metal mine.",
          "Metal production is rising.", [building(B.METAL_MINE, 2)], {"metal": 300, "crystal": 150}),
    Quest(4, "Energy", "Mines need energy. Upgrade the solar plant.",
          "Your planet has enough energy for now.", [building(B.SOLAR_PLANT, 2)], {"metal": 300, "crystal": 200}),
    Quest(5, "Deuterium", "Deuterium fuels your fleets. Build a deuterium synthesizer.",
          "The first deuterium flows.", [building(B.DEUTERIUM_MINE, 1)],
          {"metal": 400, "crystal": 200, "deuterium": 100}),
    Quest(7, "Robots", "Robots speed up all constructions. Upgrade the robotics factory.",
          "Construction is faster now.", [building(B.ROBOTICS_FACTORY, 2)],
          {"metal": 800, "crystal": 400, "deuterium": 200}),
    Quest(8, "Shipyard", "Build a shipyard and a rocket launcher to protect your planet.",
          "Your planet can defend itself.", [building(B.SHIPYARD, 1), ships(15, 1)],
          {"metal": 1000, "crystal": 500, "deuterium": 200}),
    Quest(11, "Storage", "Mines stop when the storage is full. Build a metal storage.",
          "There is more room for your metal.", [building(B.METAL_STORAGE, 1)], {"metal": 1000, "crystal": 1000}),
    Quest(12, "A name", "Give your planet a name. Tap the planet name at the top.",
          "Your planet has a name.", [Target("Rename a planet", _renamed)], {"metal": 500, "crystal": 500}),
    Quest(14, "Science", "Build a research lab and research energy technology.",
          "Your scientists are at work.", [building(B.RESEARCH_LAB, 1), research(B.ENERGY_TECH, 1)],
          {"metal": 1500, "crystal": 1500, "deuterium": 500}),
    Quest(15, "First fleet", "Research the combustion drive and build a light fighter.",
          "Your first fleet is ready.", [research(B.COMBUSTION_DRIVE, 1), ships(1, 1)],
          {"metal": 2000, "crystal": 1000, "deuterium": 500}, {13: 2},
          ["Espionage probes reveal what other planets have."]),
    Quest(16, "Espionage", "Send espionage probes to a pirate base (positions 16 to 20 of a system).",
          "Your probes are on their way.", [action(ESPIONAGE, "Send an espionage mission")],
          {"metal": 2000, "crystal": 2000, "deuterium": 500}, {1: 5}),
    Quest(17, "Attack", "Attack a pirate base. Weak bases are easy loot.",
          "The pirates will remember you.", [action(ATTACK_PIRATES, "Attack a pirate base")],
          {"metal": 3000, "crystal": 2000, "deuterium": 1000}),
    Quest(18, "Expedition", "Send ships to position 21 of any system to explore deep space.",
          "Who knows what your fleet will find.", [action(EXPEDITION, "Send an expedition")],
          {"metal": 3000, "crystal": 3000, "deuterium": 1000}),
    Quest(19, "Register", "Protect your empire: register a name and a password in the settings.",
          "You can now log in on any device.", [Target("Register your account", _registered)],
          {"metal": 5000, "crystal": 3000, "deuterium": 1000}, {11: 1}),
    Quest(21, "Colony", "Send the colony ship to a free position to found a second planet.",
          "Your empire grows.", [Target("Rule two planets", lambda s, _: len(s["empire"]["planets"]) >= 2)],
          {"metal": 5000, "crystal": 5000, "deuterium": 2000}, {9: 2}),
    Quest(22, "Transport", "Send resources to one of your planets with a transport mission.",
          "Resources are moving through your empire.", [action(TRANSPORT, "Send a transport")],
          {"metal": 5000, "crystal": 5000, "deuterium": 2000}),
    Quest(23, "Deploy", "Station ships on another of your planets.",
          "Your fleet has a new base.", [action(DEPLOY, "Deploy a fleet")],
          {"metal": 6000, "crystal": 6000, "deuterium": 3000}),
    Quest(24, "Rival", "Attack another player's planet. Spy first!",
          "Your rivals know your name now.", [action(ATTACK_PLAYER, "Attack a player")],
          {"metal": 8000, "crystal": 6000, "deuterium": 3000}, {12: 1}),
    Quest(25, "Recycling", "Battles leave debris fields. Send a recycler to collect one.",
          "Nothing goes to waste.", [action(RECYCLE, "Send a recycler")],
          {"metal": 8000, "crystal": 8000, "deuterium": 4000}),
    Quest(26, "Radar", "A radar center lets you see incoming fleets earlier.",
          "Your radar watches the sky.", [building(B.RADAR_CENTER, 1)],
          {"metal": 10000, "crystal": 10000, "deuterium": 5000}),
    Quest(30, "A new journey", "You know everything you need. The galaxy is yours.",
          "Good luck, Commander!", [], {"metal": 10000, "crystal": 10000, "deuterium": 10000}),
]


def _progress(state: dict) -> dict:
    return state.setdefault("quests", {"position": 0, "actions": []})


def record_action(state: dict, name: str) -> None:
    """Remember an action for the quests, e.g. a fleet mission that was sent."""
    if state["empire"].get("quests_complete"):
        return
    actions = _progress(state)["actions"]
    if name not in actions:
        actions.append(name)


def _rewards_view(q: Quest) -> list[dict]:
    rewards = [{"reward_type": REWARD_RESOURCE, "reward_subtype": RESOURCE_SUBTYPES[r], "reward_count": n,
                "reward_name": r.capitalize()} for r, n in q.resources.items()]
    rewards += [{"reward_type": REWARD_FLEET, "reward_subtype": t, "reward_count": n,
                 "reward_name": eco.UNITS[t].name} for t, n in q.ships.items()]
    return rewards


def _quest_view(q: Quest, state: dict, player_id: int, position: int) -> dict:
    targets = [{"target_title": f"{i:>2}. {t.title}", "finished": t.done(state, player_id)}
               for i, t in enumerate(q.targets, start=1)]
    return {
        "quest_index": q.index,
        "quest_title": q.title,
        "quest_description": q.description,
        "quest_state": all(t["finished"] for t in targets),
        "quest_targets": targets,
        "quest_rewards": _rewards_view(q),
        "congra_text": q.congratulation,
        "hints": q.hints,
        "is_last": position == len(QUESTS) - 1,
    }


def _give_rewards(q: Quest, empire: dict) -> list[dict]:
    """Add the rewards to the current planet; the client wants the new totals."""
    planet = empire["main_planet"]
    data = []
    for r, n in q.resources.items():
        planet[r] += n
        data.append({"type": REWARD_RESOURCE, "subtype": RESOURCE_SUBTYPES[r], "amount": int(planet[r])})
    for t, n in q.ships.items():
        f = planet_rules.fleet(planet, t)
        f["amount"] += n
        data.append({"type": REWARD_FLEET, "subtype": t, "amount": f["amount"]})
    return data


def current(state: dict, player_id: int, now: float) -> dict:
    """The quest window: the current quest, and its rewards once its goals are reached."""
    empire = state["empire"]
    planet_rules.tick(empire, now)
    empire["quests_newbie_guide"] = 1
    progress = _progress(state)
    position = min(progress["position"], len(QUESTS) - 1)
    quest = QUESTS[position]
    view = _quest_view(quest, state, player_id, position)
    reward_info = {}
    if view["quest_state"] and not empire.get("quests_complete"):
        reward_info = {"code": 0, "data": _give_rewards(quest, empire)}
        if position == len(QUESTS) - 1:
            empire["quests_complete"] = True
        else:
            progress["position"] = position + 1
    following = QUESTS[position + 1] if position + 1 < len(QUESTS) else None
    return {
        "update": {"cur_quest": view, "reward_info": reward_info},
        "next_quest": {"quest_index": following.index, "quest_title": following.title} if following else {},
    }


def skip_all(state: dict) -> str:
    state["empire"]["quests_complete"] = True
    state["empire"]["quests_newbie_guide"] = 1
    return "The tutorial is skipped. Good luck, Commander!"
