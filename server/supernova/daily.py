"""Daily tasks: small goals that start over every day (UTC), each with a reward.

The client opens them from the button the patcher makes visible (see tools/client_tweaks.py).
A task is listed while it runs (status 0) and when it is done (status 1); once its reward is
collected it disappears until the next day. Tasks and rewards are Supernova's own; the wiki
only says that daily tasks gave resources and ECM chips.
"""

import time
from dataclasses import dataclass, field

from . import ecm
from . import planet as planet_rules

# actions counted (see record)
BUILD, RESEARCH, SHIPYARD, ATTACK_PIRATES, ESPIONAGE, EXPEDITION, RECYCLE, TRANSPORT = (
    "build", "research", "shipyard", "attack_pirates", "espionage", "expedition", "recycle", "transport")


@dataclass(frozen=True)
class Task:
    id: int
    tag: str
    title: str
    desc: str
    action: str
    number: int
    resources: dict[str, int] = field(default_factory=dict)
    chips: int = 0
    dark_matter: int = 0


TASKS = [
    Task(1, "Build", "Upgrade buildings", "Start 3 building upgrades.", BUILD, 3, {"metal": 20_000, "crystal": 10_000}),
    Task(2, "Research", "Research", "Start a research.", RESEARCH, 1, {"crystal": 15_000, "deuterium": 5_000}),
    Task(3, "Shipyard", "Build units", "Order 20 ships or defense units in the shipyard.", SHIPYARD, 20,
         {"metal": 15_000, "crystal": 15_000}),
    Task(4, "Fleet", "Fight pirates", "Attack 2 pirate bases.", ATTACK_PIRATES, 2, chips=3),
    Task(5, "Fleet", "Scout", "Send an espionage mission.", ESPIONAGE, 1, {"deuterium": 10_000}),
    Task(6, "Fleet", "Explore", "Send an expedition.", EXPEDITION, 1, chips=2, dark_matter=10),
    Task(7, "Fleet", "Salvage", "Send recyclers to a debris field.", RECYCLE, 1, {"metal": 15_000, "crystal": 15_000}),
    Task(8, "Fleet", "Supply", "Transport resources to a planet.", TRANSPORT, 1, {"deuterium": 8_000}),
]
ICONS = {"metal": "icons/item_res_04.png", "crystal": "icons/item_res_03.png", "deuterium": "icons/item_res_01.png",
         "chips": "icons/item_res_12.png", "dark_matter": "icons/item_res_05.png"}


class TaskError(planet_rules.GameError):
    pass


def _data(state: dict, now: float | None = None) -> dict:
    day = int((time.time() if now is None else now) // 86400)
    d = state.setdefault("daily", {"day": day, "progress": {}, "claimed": []})
    if d["day"] != day:
        d.update(day=day, progress={}, claimed=[])
    return d


def record(state: dict, action: str, n: int = 1, now: float | None = None) -> None:
    progress = _data(state, now)["progress"]
    progress[action] = progress.get(action, 0) + n


def _rewards(task: Task) -> list[tuple[str, int, str]]:
    """(name, number, icon)"""
    out = [(res.capitalize(), n, ICONS[res]) for res, n in task.resources.items()]
    if task.chips:
        out.append(("ECM chips", task.chips, ICONS["chips"]))
    if task.dark_matter:
        out.append(("Dark matter", task.dark_matter, ICONS["dark_matter"]))
    return out


def listing(state: dict, now: float | None = None) -> list[dict]:
    d = _data(state, now)
    views = []
    for t in TASKS:
        if t.id in d["claimed"]:
            continue
        done = min(t.number, d["progress"].get(t.action, 0))
        rewards = _rewards(t)
        views.append({"id": t.id, "tag": t.tag, "title": t.title, "desc": t.desc,
                      "status": int(done >= t.number), "process": done, "require_number": t.number,
                      "rewards_desc": ", ".join(f"{n:,} {name}" for name, n, _ in rewards),
                      "rewards_info": [{"img": icon, "number": n} for _, n, icon in rewards]})
    return views


def claim(state: dict, task_id: int, now: float | None = None) -> str:
    d = _data(state, now)
    task = next((t for t in TASKS if t.id == task_id), None)
    if task is None or task_id in d["claimed"]:
        raise TaskError("This task is not available")
    if d["progress"].get(task.action, 0) < task.number:
        raise TaskError("This task is not done yet")
    d["claimed"].append(task_id)
    empire = state["empire"]
    planet = empire["main_planet"]
    for res, n in task.resources.items():
        planet[res] += n
    if task.chips:
        ecm.add_chips(state, task.chips)
    if task.dark_matter:
        empire["dark_matter_count"] = empire.get("dark_matter_count", 0) + task.dark_matter
    return "Reward: " + ", ".join(f"{n:,} {name}" for name, n, _ in _rewards(task))
