"""Rewards: resources and ships handed to a planet (quests, daily check-in, system gifts)."""

from . import economy as eco
from . import planet as planet_rules

# Reward types of the client (GameUIQuest.REWARD_TYPE_*, system gifts)
RESOURCE, FLEET = 1, 2
RESOURCE_SUBTYPES = {"metal": 1, "crystal": 2, "deuterium": 3}


def grant(planet: dict, resources: dict[str, int], ships: dict[int, int]) -> list[dict]:
    """Add the rewards to `planet`. Returns {type, subtype, amount} with the new totals, which is
    what the client writes into its planet data."""
    data = []
    for res, n in resources.items():
        planet[res] += n
        data.append({"type": RESOURCE, "subtype": RESOURCE_SUBTYPES[res], "amount": int(planet[res])})
    for ftype, n in ships.items():
        f = planet_rules.fleet(planet, ftype)
        f["amount"] += n
        data.append({"type": FLEET, "subtype": ftype, "amount": f["amount"]})
    return data


def view(resources: dict[str, int], ships: dict[int, int]) -> list[dict]:
    """Rewards as lists the client shows them (quest and gift windows)."""
    items = [{"reward_type": RESOURCE, "reward_subtype": RESOURCE_SUBTYPES[r], "reward_count": n,
              "reward_name": r.capitalize()} for r, n in resources.items()]
    items += [{"reward_type": FLEET, "reward_subtype": t, "reward_count": n, "reward_name": eco.UNITS[t].name}
              for t, n in ships.items()]
    return items


def describe(resources: dict[str, int], ships: dict[int, int]) -> str:
    parts = [f"{n:,} {r}" for r, n in resources.items()]
    parts += [f"{n} {eco.UNITS[t].name}" for t, n in ships.items()]
    return ", ".join(parts)


def parse(items: list[str]) -> tuple[dict[str, int], dict[int, int]]:
    """Parse `metal=1000 9=5` (resource or fleet_type = amount), as the admin tool takes them."""
    resources, ships = {}, {}
    for item in items:
        key, _, value = item.partition("=")
        if key in RESOURCE_SUBTYPES:
            resources[key] = int(value)
        elif key.isdigit() and int(key) in eco.UNITS:
            ships[int(key)] = int(value)
        else:
            raise ValueError(f"Unknown reward {item!r}")
    return resources, ships
