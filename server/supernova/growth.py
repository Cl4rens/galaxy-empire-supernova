"""Growth packages (the gift button on the left of the city screen).

Client text: players get 1 growth point for every 1,000 metal, crystal or deuterium they spend,
and a package each time they reach a certain number of points. Spent resources are what the
score counts, so growth points are the player's score. The steps and packages are Supernova's
own.
"""

from . import ecm, items, ranking

# growth points, [(kind, amount)]: kind is a resource, "dark_matter", "chips" or an item id
STEPS = [
    (20, [("metal", 20_000), ("crystal", 10_000)]),
    (100, [("metal", 50_000), ("crystal", 30_000), ("deuterium", 10_000)]),
    (300, [("dark_matter", 20), ("chips", 5)]),
    (1_000, [("metal", 200_000), ("crystal", 120_000), ("deuterium", 50_000), (2103, 2)]),
    (3_000, [("dark_matter", 50), ("chips", 10), (3103, 2)]),
    (10_000, [("metal", 1_000_000), ("crystal", 600_000), ("deuterium", 300_000), (2104, 2)]),
    (30_000, [("dark_matter", 100), ("chips", 20), (3102, 3)]),
    (100_000, [("dark_matter", 200), (5003, 2), (5004, 1)]),
]
ICONS = {"metal": "icons/item_res_04.png", "crystal": "icons/item_res_03.png",
         "deuterium": "icons/item_res_01.png", "dark_matter": "icons/item_res_05.png",
         "chips": "icons/item_res_12.png"}
NAMES = {"metal": "Metal", "crystal": "Crystal", "deuterium": "Deuterium", "dark_matter": "Dark Matter",
         "chips": "ECM chips"}


def _claimed(state: dict) -> list[int]:
    return state.setdefault("growth_claimed", [])


def _gift(kind, amount: int) -> list:
    """[kind, amount, name, 0, picture] as the client reads it (index 2 name, 5 picture)."""
    if isinstance(kind, int):
        i = items.item(kind)
        return [kind, amount, i.name, 0, f"icons/{i.icon}"]
    return [kind, amount, NAMES[kind], 0, ICONS[kind]]


def listing(state: dict) -> list[dict]:
    score = ranking.points(state)["score"]
    claimed = _claimed(state)
    return [{"score": need, "got": need in claimed, "show_tips_img": score >= need and need not in claimed,
             "gifts": [_gift(k, n) for k, n in gifts]} for need, gifts in STEPS]


def summary(state: dict) -> dict:
    """The login field `grows`: the button shows while packages are left, with a counter."""
    rows = listing(state)
    return {"has_gift": not all(r["got"] for r in rows), "gift_num": sum(r["show_tips_img"] for r in rows)}


def claim(state: dict) -> list[dict]:
    """Collect every package the player has reached."""
    score = ranking.points(state)["score"]
    claimed = _claimed(state)
    empire = state["empire"]
    home = empire["planets"][0]
    for need, gifts in STEPS:
        if score < need or need in claimed:
            continue
        claimed.append(need)
        for kind, n in gifts:
            if kind in ("metal", "crystal", "deuterium"):
                home[kind] += n
            elif kind == "dark_matter":
                empire["dark_matter_count"] = empire.get("dark_matter_count", 0) + n
            elif kind == "chips":
                ecm.add_chips(state, n)
            else:
                items.add(state, kind, n)
    return listing(state)
