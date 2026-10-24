"""Newbie protection: OGame's points rules."""

import pytest

from supernova import config, db, fleets, protection, universe
from supernova import planet as planet_rules

from .test_fleets import new_player_with_cargo, position


@pytest.fixture
def protected(monkeypatch):
    monkeypatch.setattr(config, "NEWBIE_POINTS", 5000)
    monkeypatch.setattr(config, "STRONG_POINTS", 500_000)


def test_rules(protected):
    assert protection.rule(10_000, 4_000) == protection.TARGET_TOO_WEAK  # newbie
    assert protection.rule(4_000, 10_000) == protection.TARGET_TOO_STRONG
    assert protection.rule(1_000, 4_000) == protection.ATTACK_OK  # two newbies
    assert protection.rule(60_000, 10_000) == protection.TARGET_TOO_WEAK  # less than 1/5
    assert protection.rule(10_000, 60_000) == protection.TARGET_TOO_STRONG
    assert protection.rule(3_000_000, 600_000) == protection.ATTACK_OK  # both strong


def test_attack_on_a_newbie_is_refused(protected):
    strong, newbie = new_player_with_cargo(0), new_player_with_cargo(0)
    with db.player_state(strong) as s:
        planet_rules.fleet(s["empire"]["main_planet"], 4)["amount"] = 200  # 12,000 points of battleships
    with db.player_state(strong) as s:
        p = s["empire"]["main_planet"]
        assert fleets.ATTACK not in fleets.plan(s["empire"], strong, {4: 1}, position(newbie))["missions"]
        with pytest.raises(fleets.FleetError, match="newbie protection"):
            fleets.send(strong, s, {"fleet_ids": f"{planet_rules.fleet(p, 4)['fleet_id']},", "fleet_cnt_ids": "1,",
                                    "end_p_pos": ",".join(map(str, position(newbie))),
                                    "mission": str(fleets.ATTACK), "speed_ratio": "100"}, 2_000_000_000.0)
    g, sy, pos = position(newbie)
    slot = universe.system_view(g, sy, strong)["planets"][pos - 1]
    assert slot["can_attack"] == protection.TARGET_TOO_WEAK
    with db.player_state(strong) as s:  # keep the ranking tests' expectations simple
        planet_rules.fleet(s["empire"]["main_planet"], 4)["amount"] = 0


def test_unit_info_and_buffs():
    from fastapi.testclient import TestClient
    from supernova.app import app
    from .test_server import decrypt_json
    c = TestClient(app, headers={"openudid": "info-reader"})
    me = decrypt_json(c.post("/world/1/trials.json", content="username="))
    p = me["empire"]["main_planet"]
    cruiser = next(f for f in p["fleets"] if f["fleet_type"] == 3)
    info = decrypt_json(c.get(f"/world/1/fleets/{cruiser['fleet_id']}"))
    assert info["attack"] == 400 and info["capacity"] == 800 and info["bonus_attack"] == 0
    assert any(r["damage_rate"] > 0 for r in info["fatal_damage_list"])  # cruisers have rapid fire
    assert any(r["damage_rate"] < 0 for r in info["fatal_damage_list"])  # battlecruisers fire rapidly at them
    buffs = decrypt_json(c.get(f"/world/1/users/my_buffs_ext?planet_id={p['planet_id']}"))
    assert buffs["buff_list"] == [] and buffs["kryptons"] == []
