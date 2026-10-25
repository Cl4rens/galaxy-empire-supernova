"""Devourer Interceptor, Titanenergetic Frigate, Proton Cannon, Annihilator and Reaper."""

import random

from supernova import combat, db, items
from supernova import planet as planet_rules

from .test_acs import member
from .test_server import decrypt_json


def side(units, name="x"):
    return combat.Side(name, units)


def test_devourer_shots_pass_through_shields():
    # 10 devourers against one large shield dome: without piercing the dome would absorb everything
    result = combat.fight(side({25: 10}), side({22: 1}), random.Random(1))
    assert result["defender_left"] == {}


def test_proton_cannon_is_weak_against_small_ships():
    target = [[1, 10**9, 0]]
    _, small, _ = combat._fire([[27, 1, 1]], side({27: 1}), target, side({1: 1}), random.Random(1))
    target = [[26, 10**9, 0]]
    _, large, _ = combat._fire([[27, 1, 1]], side({27: 1}), target, side({26: 1}), random.Random(1))
    assert small * 5 <= large


def test_reaper_aims_at_the_biggest_ships():
    targets = [[1, 10**9, 0] for _ in range(50)] + [[26, 10**9, 0]]
    combat._fire([[29, 1, 1]], side({29: 1}), targets, side({1: 50, 26: 1}), random.Random(3))
    assert targets[-1][1] < 10**9 and all(t[1] == 10**9 for t in targets[:-1])


def test_annihilators_explode_against_advanced_fleets():
    att, dfn, explosions = combat.annihilate(side({28: 3, 7: 1}), side({23: 10, 24: 10, 29: 5, 1: 100}))
    assert explosions == 2 and att[28] == 1 and dfn[29] == 5  # stops without two advanced types; Reapers immune
    assert dfn[24] < 10 and 1 not in dfn
    # one advanced type is not enough
    att, dfn, explosions = combat.annihilate(side({28: 3}), side({23: 10, 1: 100}))
    assert explosions == 0 and att == {28: 3}
    result = combat.fight(side({28: 2, 7: 5}), side({23: 1, 26: 1}), random.Random(2))
    assert result["explosions"] == 1 and result["winner"] == "attacker" and result["attacker_left"][28] == 1


def test_items_build_the_new_ships():
    c, me = member("adv-ships", {})
    uid = me["user_id"]
    with db.player_state(uid) as s:
        s["empire"]["dark_matter_count"] = 10_000
        items._add(s, items.COMPONENT, 6500)
    for item_id in (5002, 5003, 5004):
        decrypt_json(c.post(f"/world/1/users/{uid}/buy_game_item", content=f"game_item_id={item_id}&number=2"))
        decrypt_json(c.post(f"/world/1/users/{uid}/use_game_item", content=f"game_item_id={item_id}&number=2"))
    assert c.post(f"/world/1/users/{uid}/buy_game_item", content=f"game_item_id={items.COMPONENT}&number=1"
                  ).status_code == 400
    done = decrypt_json(c.post(f"/world/1/users/{uid}/use_game_item",
                               content=f"game_item_id={items.COMPONENT}&number=6500"))
    assert "2 Reaper" in done["info"]
    planet = db.load_state(uid)["empire"]["main_planet"]
    assert [planet_rules.fleet(planet, t)["amount"] for t in (25, 26, 28, 29)] == [2, 2, 2, 2]
    assert items.amount(db.load_state(uid), items.COMPONENT) == 500

    with db.player_state(uid) as s:
        planet_rules.fleet(s["empire"]["main_planet"], 28)["amount"] = items.MAX_ANNIHILATORS
        items._add(s, 5002, 1)
    assert "at most" in c.post(f"/world/1/users/{uid}/use_game_item", content="game_item_id=5002&number=1").text


def test_components_from_large_ships():
    with_state = {"items": {}}
    assert items.add_components(with_state, {1: 100, 7: 3, 24: 1}) == 3 * 2 + 70
    assert items.amount(with_state, items.COMPONENT) == 76


def test_old_planets_get_the_new_types():
    _, me = member("adv-old", {})
    with db.player_state(me["user_id"]) as s:
        s["empire"]["main_planet"]["fleets"] = [f for f in s["empire"]["main_planet"]["fleets"]
                                                if f["fleet_type"] < 25]
        planet_rules.tick(s["empire"])
        assert planet_rules.fleet(s["empire"]["main_planet"], 29)["amount"] == 0
