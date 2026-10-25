"""Chests with alliance members instead of Facebook friends."""

import time

from supernova import chests, db, promotions

from .test_acs import member
from .test_server import decrypt_json


def test_chest_fills_helps_and_opens():
    boss_c, boss = member("chest-boss", {})
    aid = decrypt_json(boss_c.post("/world/1/alliances.json", content="alliance_name=Chests&description="))["alliance_id"]
    decrypt_json(boss_c.post(f"/world/1/alliances/{aid}/auto_in.json", content="status=1"))
    mate_c, mate = member("chest-mate", {})
    decrypt_json(mate_c.post("/world/1/alliance_applications.json", content=f"alliance_id={aid}"))

    view = decrypt_json(boss_c.post("/world/1/chest/enter"))
    info = view["chest_user_info"]
    assert info["chest_level"] == 1 and info["progress"] == 0 and info["help_config"]["need_progress"] == 5
    assert view["friends"] == [{"id": mate["user_id"], "name": mate["username"]}] and info["requests"] == []
    assert "opens at 5" in boss_c.post("/world/1/chest/open_chest").text

    decrypt_json(boss_c.post("/world/1/chest/ask_for_help"))
    seen = decrypt_json(mate_c.post("/world/1/chest/enter"))["chest_user_info"]
    assert seen["requests"] == [boss["user_id"]]
    helped = decrypt_json(mate_c.post("/world/1/chest/help_other", content=f"target_id={boss['user_id']}"))
    assert helped["helped"] == boss["username"] and helped["chest_user_info"]["extra_progress"] == 1
    assert helped["chest_user_info"]["requests"] == []  # once a day
    assert "does not need" in mate_c.post("/world/1/chest/help_other", content=f"target_id={boss['user_id']}").text
    assert decrypt_json(boss_c.post("/world/1/chest/enter"))["chest_user_info"]["progress"] == 1

    # the chest fills by itself, one point every 6 hours
    now = time.time()
    with db.player_state(boss["user_id"]) as s:
        s["chest"]["filled_at"] -= 4 * chests.SELF_FILL_SECONDS
        s["chest"]["extra"] = chests.EXTRA_NEED
        dm = s["empire"].get("dark_matter_count", 0)
    opened = decrypt_json(boss_c.post("/world/1/chest/open_chest"))
    assert "extra rewards" in opened["result"]
    info = opened["chest_user_info"]
    assert info["progress"] == 0 and info["chest_exp"] == 1 and info["extra_progress"] == 0
    assert db.load_state(boss["user_id"])["empire"]["dark_matter_count"] == dm + 30
    with db.player_state(boss["user_id"]) as s:
        s["chest"]["progress"] = chests.need_progress(1)
        chests.open_chest(s, now)
        assert s["chest"]["level"] == 2 and s["chest"]["exp"] == 0  # 2 openings raise level 1


def test_strangers_and_invites():
    c, me = member("chest-lonely", {})
    assert "alliance" in c.post("/world/1/chest/ask_for_help").text
    _, other = member("chest-other", {})
    assert "only help members" in c.post("/world/1/chest/help_other", content=f"target_id={other['user_id']}").text
    new_c, _ = member("chest-newcomer", {})
    decrypt_json(new_c.post("/world/1/promotions/apply_newbie_invite_code", content=f"code={promotions.code_of(me['user_id'])}"))
    assert decrypt_json(c.post("/world/1/chest/enter"))["chest_user_info"]["extra_progress"] == chests.INVITE_EXTRA
