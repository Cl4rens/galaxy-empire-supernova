"""Growth packages: growth points are the score; reached packages are collected at once."""

from supernova import db, growth, items, player, ranking

from .test_acs import member
from .test_server import decrypt_json


def test_growth_packages(monkeypatch):
    c, me = member("growth", {})
    uid = me["user_id"]
    score = {"value": 5}
    monkeypatch.setattr(ranking, "points", lambda state: {"score": score["value"], "power": 0})
    rows = decrypt_json(c.get("/world/1/grows"))["grows"]
    assert len(rows) == len(growth.STEPS) and not any(r["show_tips_img"] for r in rows)
    assert rows[0]["gifts"][0] == ["metal", 20_000, "Metal", 0, "icons/item_res_04.png"]

    score["value"] = 1_000
    with db.player_state(uid) as s:
        assert growth.summary(s) == {"has_gift": True, "gift_num": 4}
        metal, dm = s["empire"]["planets"][0]["metal"], s["empire"].get("dark_matter_count", 0)
    got = decrypt_json(c.get("/world/1/grows/get"))["grows"]
    assert [r["got"] for r in got[:5]] == [True, True, True, True, False]
    state = db.load_state(uid)
    assert state["empire"]["planets"][0]["metal"] == metal + 270_000
    assert state["empire"]["dark_matter_count"] == dm + 20 and items.amount(state, 2103) == 2
    assert growth.summary(state) == {"has_gift": True, "gift_num": 0}
    again = decrypt_json(c.get("/world/1/grows/get"))["grows"]  # nothing new
    assert again == got
    assert player.login_response(db.load_state(uid), 0)["grows"] == {"has_gift": True, "gift_num": 0}
