"""Fleet missions with a controlled clock: planning, transport, recall."""

import pytest

from supernova import db, fleets, mails, player, universe
from supernova import economy as eco
from supernova import planet as planet_rules


def new_player_with_cargo(n_small_cargo=2):
    pid, name, _ = db.create_player(
        player.guest_username, "test",
        lambda pid, n: player.new_state(pid, n, universe.place_home(pid, player.HOME_PLANET_NAME)))
    with db.player_state(pid) as state:
        p = state["empire"]["main_planet"]
        p.update(metal=50_000, crystal=50_000, deuterium=50_000)
        planet_rules.fleet(p, 9)["amount"] = n_small_cargo
    return pid


def position(pid):
    p = db.load_state(pid)["empire"]["main_planet"]
    return p["galaxy_position"], p["system_position"], p["planet_position"]


def send_params(pid, target, cargo=None, mission=fleets.TRANSPORT, count=1):
    fid = planet_rules.fleet(db.load_state(pid)["empire"]["main_planet"], 9)["fleet_id"]
    params = {"fleet_ids": f"{fid},", "fleet_cnt_ids": f"{count},", "end_p_pos": ",".join(map(str, target)),
              "mission": str(mission), "speed_ratio": "100"}
    params.update({k: str(v) for k, v in (cargo or {}).items()})
    return params


def test_distance_and_flight_time():
    assert eco.distance((1, 5, 4), (1, 5, 4)) == 5
    assert eco.distance((1, 5, 4), (1, 5, 9)) == 1025
    assert eco.distance((1, 5, 4), (1, 9, 4)) == 2700 + 4 * 95
    assert eco.distance((1, 5, 4), (3, 5, 4)) == 40000
    slow = eco.flight_seconds(3000, 5000, 50)
    fast = eco.flight_seconds(3000, 5000, 100)
    assert slow > fast > 0


def test_transport_delivers_and_returns():
    sender, receiver = new_player_with_cargo(), new_player_with_cargo(0)
    now = 2_000_000_000.0
    with db.player_state(sender) as state:
        info = fleets.plan(state["empire"], sender, {9: 1}, position(receiver))
        assert info["missions"] == [fleets.TRANSPORT, fleets.ATTACK]
        assert info["empty_cargobays"] == 5000
        fleets.send(sender, state, send_params(sender, position(receiver), {"metal": 3000}), now)
        p = state["empire"]["main_planet"]
        assert planet_rules.fleet(p, 9)["amount"] == 1
    flight = fleets.events_view(sender)[0]
    assert flight["mission"] == fleets.TRANSPORT and not flight["is_returning"]

    receiver_metal = db.load_state(receiver)["empire"]["main_planet"]["metal"]
    fleets.process_due(flight["finish_time"])
    assert db.load_state(receiver)["empire"]["main_planet"]["metal"] >= receiver_metal + 3000
    back = fleets.events_view(sender)[0]
    assert back["is_returning"] and back["total_time"] == flight["distance_time"]

    fleets.process_due(back["finish_time"])
    assert fleets.events_view(sender) == []
    assert planet_rules.fleet(db.load_state(sender)["empire"]["main_planet"], 9)["amount"] == 2


def test_recall_flies_back_as_long_as_it_flew():
    sender, receiver = new_player_with_cargo(), new_player_with_cargo(0)
    now = 2_000_000_000.0
    with db.player_state(sender) as state:
        fleets.send(sender, state, send_params(sender, position(receiver), {"crystal": 1000}), now)
    event_id = fleets.events_view(sender)[0]["event_id"]
    fleets.recall(sender, event_id, now + 10)
    back = fleets.events_view(sender)[0]
    assert back["is_returning"] and back["finish_time"] == now + 20

    receiver_crystal = db.load_state(receiver)["empire"]["main_planet"]["crystal"]
    fleets.process_due(now + 20)
    assert db.load_state(receiver)["empire"]["main_planet"]["crystal"] == receiver_crystal  # never arrived
    p = db.load_state(sender)["empire"]["main_planet"]
    assert planet_rules.fleet(p, 9)["amount"] == 2 and p["crystal"] >= 49_000


def test_send_checks():
    sender, receiver = new_player_with_cargo(), new_player_with_cargo(0)
    now = 2_000_000_000.0
    with db.player_state(sender) as state:
        with pytest.raises(planet_rules.GameError, match="cargo"):
            fleets.send(sender, state, send_params(sender, position(receiver), {"metal": 6000}), now)
        with pytest.raises(planet_rules.GameError, match="Not enough ships"):
            fleets.send(sender, state, send_params(sender, position(receiver), count=3), now)
        with pytest.raises(planet_rules.GameError, match="not possible"):
            fleets.send(sender, state, send_params(sender, position(receiver), mission=fleets.DEPLOY), now)
        state["empire"]["main_planet"]["deuterium"] = 0
        with pytest.raises(planet_rules.GameError, match="fuel"):
            fleets.send(sender, state, send_params(sender, position(receiver)), now)


def test_espionage_levels():
    assert fleets.espionage_level(1, 0, 0) == 1
    assert fleets.espionage_level(1, 2, 0) == 5
    assert fleets.espionage_level(4, 0, 2) == 0


def test_espionage_report_and_victim_notice():
    spy, victim = new_player_with_cargo(0), new_player_with_cargo(0)
    with db.player_state(spy) as state:
        planet_rules.fleet(state["empire"]["main_planet"], fleets.ESPIONAGE_PROBE)["amount"] = 5
    with db.player_state(victim) as state:
        planet_rules.fleet(state["empire"]["main_planet"], 15)["amount"] = 7  # rocket launchers
    now = 2_000_000_000.0
    with db.player_state(spy) as state:
        probe = planet_rules.fleet(state["empire"]["main_planet"], fleets.ESPIONAGE_PROBE)["fleet_id"]
        params = {"fleet_ids": f"{probe},", "fleet_cnt_ids": "5,", "end_p_pos": ",".join(map(str, position(victim))),
                  "mission": str(fleets.ESPIONAGE), "speed_ratio": "100"}
        assert fleets.ESPIONAGE in fleets.plan(state["empire"], spy, {13: 5}, position(victim))["missions"]
        fleets.send(spy, state, params, now)
    flight = fleets.events_view(spy)[0]
    fleets.process_due(flight["finish_time"])

    report = mails.undelivered(spy)[0]
    result = report["body"]["probe_result"]
    assert report["body"]["mission_type"] == fleets.ESPIONAGE
    assert result["metal"] >= 50_000 and {"fleet_type": 15, "fleet_name": "Rocket Launcher", "amount": 7} in result["defenses"]
    assert "buildings" in result  # 5 probes, equal technology: level 5
    notice = mails.undelivered(victim)[0]
    assert notice["body"]["mission_type"] == fleets.BE_ESPIONAGE

    mails.confirm(spy, [report["id"]])
    assert mails.unread_count(spy) == 0
    fleets.process_due(fleets.events_view(spy)[0]["finish_time"])
    returned = mails.undelivered(spy)[0]
    assert returned["body"]["is_returning"] is True
