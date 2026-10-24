"""Where dark matter comes from without a real-money shop."""

from supernova import checkin, config, db
from supernova import planet as planet_rules

from .test_acs import member
from .test_server import decrypt_json

DAY = 86400


def test_new_players_start_with_dark_matter():
    _, me = member("dm-start", {})
    assert db.load_state(me["user_id"])["empire"]["dark_matter_count"] == config.STARTING_DARK_MATTER


def test_checkin_dark_matter_days():
    c, me = member("dm-checkin", {})
    uid = me["user_id"]
    with db.player_state(uid) as s:
        s["checkin"] = {"days": 3, "last_day": checkin._today(__import__("time").time()), "claimed": [1, 2]}
        s["empire"]["dark_matter_count"] = 0
    gift = decrypt_json(c.get("/world/1/users/checkin_info"))["config"]["n3"]["g"]
    assert gift["type"] == "dark_matter" and gift["amount"] == checkin.DARK_MATTER_DAYS[3]
    claimed = decrypt_json(c.get("/world/1/users/draw_checkin_gift?gift_name=n3"))
    assert claimed["result"]["ok"] and "dark matter" in claimed["result"]["info"]
    assert db.load_state(uid)["empire"]["dark_matter_count"] == checkin.DARK_MATTER_DAYS[3]


def test_daily_income_is_off_by_default_and_configurable(monkeypatch):
    empire = {"dark_matter_count": 0, "planets": [], "research_upgrading_events": []}
    planet_rules._dark_matter_income(empire, 0)
    planet_rules._dark_matter_income(empire, 3 * DAY)
    assert empire["dark_matter_count"] == 0
    monkeypatch.setattr(config, "DARK_MATTER_PER_DAY", 10)
    planet_rules._dark_matter_income(empire, 3.5 * DAY)
    assert empire["dark_matter_count"] == 5
    planet_rules._dark_matter_income(empire, 3.55 * DAY)  # half a unit stays for later
    planet_rules._dark_matter_income(empire, 3.6 * DAY)
    assert empire["dark_matter_count"] == 6
