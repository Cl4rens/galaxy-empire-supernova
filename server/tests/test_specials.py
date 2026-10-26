"""The Space Store's planet and moon items: projections, peace agreements, planet shifters,
lunar items, reformers and nebula navigators."""

import random

import pytest

from supernova import db, fleets, items, npc, player, specials, universe
from supernova import planet as planet_rules

from .test_fleets import new_player_with_cargo, position, send_params
from .test_npc import NOW

DAY = specials.DAY


def _item(special_kind, *args):
    return next(i for i in items.CATALOG if i.special[:1 + len(args)] == (special_kind, *args))


def _use(pid, item, now=NOW, target=None, planet_id=None, rng=None):
    with db.player_state(pid) as state:
        items.add(state, item.id, 1)
        planet = planet_rules.find_planet(state["empire"], planet_id) if planet_id else state["empire"]["main_planet"]
        return items.use(state, item.id, 1, planet, now, rng=rng, target=target)


def test_projection_changes_the_look_and_boosts_the_planet():
    pid = new_player_with_cargo()
    _use(pid, _item("projection", "evil", 2))
    planet = db.load_state(pid)["empire"]["main_planet"]
    assert planet["planet_icon"] == 27 and planet["planet_icon_end_time"] == NOW + specials.WEEK
    assert specials.projection_bonus(planet, "attack") == 20 and specials.projection_bonus(planet, "shield") == 0
    galaxy, system, pos = position(pid)
    slot = next(s for s in universe.system_view(galaxy, system)["planets"] if s["planet_position"] == pos)
    assert slot["planet_icon"] == 27  # what everyone sees in the solar system
    _use(pid, _item("projection", "abyss", 1), now=0)  # a new one replaces it
    with db.player_state(pid) as state:
        planet_rules.tick(state["empire"], 1)
        p = state["empire"]["main_planet"]
        assert p["projection"]["kind"] == "abyss" and p["officer"]["build"] >= 10
        planet_rules.tick(state["empire"], specials.WEEK + 1)
        assert "projection" not in p and p["planet_icon"] == 0


def test_black_hole_projection_speeds_up_fleets_from_the_planet():
    pid = new_player_with_cargo()
    empire = db.load_state(pid)["empire"]
    slow = fleets.plan(empire, pid, {9: 1}, (1, 1, 1))["speed"]
    _use(pid, _item("projection", "black_hole", 2), now=4_000_000_000)
    empire = db.load_state(pid)["empire"]
    assert fleets.plan(empire, pid, {9: 1}, (1, 1, 1))["speed"] == int(slow * 1.2)


def test_peace_agreement_keeps_attackers_away_and_ends_with_an_own_attack():
    import time
    now = time.time()
    victim, attacker = new_player_with_cargo(), new_player_with_cargo()
    _use(victim, _item("peace", 4), now=now)
    with db.player_state(victim) as state:
        with pytest.raises(planet_rules.GameError, match="once a day"):
            items.add(state, _item("peace", 12).id, 1)
            items.use(state, _item("peace", 12).id, 1, state["empire"]["main_planet"], now)
    galaxy, system, pos = position(victim)
    slot = next(s for s in universe.system_view(galaxy, system)["planets"] if s["planet_position"] == pos)
    assert slot["is_protected"] == 1
    assert player.planets_info(db.load_state(victim)["empire"])[0]["is_protected"] == 1
    with db.player_state(attacker) as state:
        origin = state["empire"]["main_planet"]
        planet_rules.fleet(origin, 1)["amount"] = 5  # light fighters
        assert fleets.ATTACK not in fleets.allowed_missions(attacker, origin, (galaxy, system, pos), {1: 5})
    # attacking someone else ends one's own agreement
    with db.player_state(victim) as state:
        assert specials.end_peace(state["empire"])
        assert not specials.at_peace(state["empire"]["main_planet"], now)
    origin = db.load_state(attacker)["empire"]["main_planet"]
    assert fleets.ATTACK in fleets.allowed_missions(attacker, origin, (galaxy, system, pos), {1: 5})


def test_planet_shifters_move_the_planet_with_its_moon():
    pid = new_player_with_cargo()
    with db.player_state(pid) as state:
        moon = player.add_moon(pid, state["empire"]["main_planet"])
    before = position(pid)
    _use(pid, _item("shift", specials.SHIFT_RANDOM), rng=random.Random(3))
    after = position(pid)
    assert after != before and after[0] == before[0]
    planet = db.load_state(pid)["empire"]["main_planet"]
    assert (planet["moon"]["galaxy_position"], planet["moon"]["system_position"]) == after[:2]
    rows = db.connect().execute("SELECT galaxy, system, position FROM planets WHERE player_id = ?", (pid,)).fetchall()
    assert {tuple(r) for r in rows} == {after} and len(rows) == 2 and moon
    chosen = _item("shift", specials.SHIFT_CHOSEN)
    assert items.item(chosen.id).item_type == items.CHOOSE_POSITION
    target = (2, 77, 15)
    _use(pid, chosen, target=target)
    assert position(pid) == target
    with pytest.raises(planet_rules.GameError, match="48 hours"):
        _use(pid, chosen, target=(2, 78, 15))
    other = new_player_with_cargo()
    with pytest.raises(planet_rules.GameError, match="taken"):
        _use(other, chosen, target=target)


def test_lunar_items():
    pid = new_player_with_cargo()
    catalyst = _item("moon", 0.20)
    rng = random.Random()
    rng.random = lambda: 0.5  # the catalyst fails
    assert "no moon" in _use(pid, catalyst, rng=rng)
    rng.random = lambda: 0.0  # the catalyst works
    _use(pid, catalyst, rng=rng)
    moon = db.load_state(pid)["empire"]["main_planet"]["moon"]
    with pytest.raises(planet_rules.GameError, match="already has a moon"):
        _use(pid, catalyst, rng=rng)
    terraformer = _item("lunar_fields", 5)
    _use(pid, terraformer, planet_id=moon["planet_id"])
    moon = db.load_state(pid)["empire"]["main_planet"]["moon"]
    assert 5 <= moon["extra_fields"] <= 15
    assert planet_rules.fields(moon)[1] == 1 + moon["extra_fields"]
    with pytest.raises(planet_rules.GameError, match="only work on moons"):
        _use(pid, terraformer)


def test_reformers():
    pid = new_player_with_cargo()
    reformer = next(i for i in items.CATALOG if i.special[:3] == ("reform", "crystal", 100_000)
                    and i.pay_type == items.CREDITS)
    with db.player_state(pid) as state:
        p = state["empire"]["main_planet"]
        p.update(metal=0, crystal=250_000)
        items.add(state, reformer.id, 2)
        items.use(state, reformer.id, 2, p, NOW)
        assert (p["metal"], p["crystal"]) == (160_000, 50_000)
        items.add(state, reformer.id, 1)
        with pytest.raises(planet_rules.GameError, match="needs 100,000 crystal"):
            items.use(state, reformer.id, 1, p, NOW)
    gas = next(i for i in items.CATALOG if i.special[:2] == ("reform", "deuterium"))
    assert gas.special[3] == "crystal"


def test_nebula_navigator_explores_again_and_again():
    from .test_npc import astrophysics
    pid = new_player_with_cargo(5)
    astrophysics(pid, 1)
    _use(pid, _item("navigator", 20))
    with db.player_state(pid) as state:
        state["empire"]["navigator"]["runs"] = 3
        planet_rules.fleet(state["empire"]["main_planet"], 9)["amount"] = 100  # replacements
    galaxy, system, _ = position(pid)
    with db.player_state(pid) as state:
        params = send_params(pid, (galaxy, system, universe.EXPEDITION_POSITION), mission=fleets.EXPEDITION,
                             count=5)
        fleets.send(pid, state, params, NOW)
    t = NOW
    for _ in range(10):
        events = fleets.events_view(pid)
        if not events:
            break
        t = max(t, events[0]["finish_time"])
        fleets.process_due(t)
    assert npc.expeditions_today(pid, t) == 3
    assert db.load_state(pid)["empire"]["navigator"] == {"runs": 0, "fleets": {}}


def test_store_has_the_wiki_items_with_pictures():
    names = {i.name for i in items.CATALOG}
    assert {"Abyss Projection Lv.1", "Peace Agreement Lv.2", "Directional Planet Shifter",
            "Nebula Navigator (20 runs)", "Lunar Catalyst", "Crystal Reformer (S) Lv.2"} <= names
    assert items.item(2206).price == 864 and items.item(1103).resources[1] == 6_500
    assert all(i.picture for i in items.CATALOG if i.special)
    assert {v["id"] for v in items.shortcut_view({"empire": {}}, items.SHORTCUT_PEACE)} == {3501, 3502}
