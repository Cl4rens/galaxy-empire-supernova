"""Gift packs, invitation codes and the (empty) purchase menu."""

import time

from supernova import db, items, promotions
from supernova import planet as planet_rules

from .test_acs import member
from .test_server import decrypt_json


def test_gift_pack_contents_and_opening():
    c, me = member("pack-buyer", {})
    uid = me["user_id"]
    shop = decrypt_json(c.get("/world/1/game_items?category=4"))
    pack = next(i for i in shop["items"] if i["id"] == 4001)
    assert pack["is_gift"] is True
    contents = decrypt_json(c.get("/world/1/gachas/4001"))["data"]
    assert {"name": "10 ECM chips", "amount": 10, "img": "icons/item_res_12.png"} in contents
    assert any(x["name"] == f"3 {items.item(2103).name}" and x["amount"] == 3 for x in contents)
    with db.player_state(uid) as s:
        items.add(s, 4001, 1)
        items.add(s, 4002, 1)
        planet = s["empire"]["main_planet"]
        metal = planet["metal"]
        items.use(s, 4001, 1, planet)
        items.use(s, 4002, 1, planet)
        assert planet["metal"] == metal + 150_000 and items.amount(s, 2103) == 3
        assert planet_rules.fleet(planet, 25)["amount"] == 2 and planet_rules.fleet(planet, 26)["amount"] == 1
    assert decrypt_json(c.get("/world/1/gachas/9001"))["data"][0]["name"] == "100,000 Metal"  # Galaxy Challenge crate


def test_invitation_code():
    c, me = member("invite-new", {})
    _, friend = member("invite-friend", {})
    own = decrypt_json(c.get("/world/1/promotions/my_newbie_invite_info"))["my_code"]
    assert "own" in c.post("/world/1/promotions/apply_newbie_invite_code", content=f"code={own}").text
    assert "does not exist" in c.post("/world/1/promotions/apply_newbie_invite_code", content="code=SN999999").text
    code = promotions.code_of(friend["user_id"])
    dm = db.load_state(friend["user_id"])["empire"].get("dark_matter_count", 0)
    assert "Welcome" in decrypt_json(c.post("/world/1/promotions/apply_newbie_invite_code", content=f"code={code}"))
    assert db.load_state(friend["user_id"])["empire"]["dark_matter_count"] == dm + promotions.REWARD_DARK_MATTER
    assert "already" in c.post("/world/1/promotions/apply_newbie_invite_code", content=f"code={code}").text
    _, late = member("invite-late", {})
    db.connect().execute("UPDATE players SET created_at = ? WHERE id = ?", (int(time.time()) - 8 * 86400, late["user_id"]))
    db.connect().commit()
    assert "first 7 days" in promotions_error(late["user_id"], code)


def promotions_error(pid, code):
    try:
        promotions.apply_code(pid, code)
    except promotions.PromotionError as e:
        return str(e)
    return ""


def test_purchase_menu_is_empty():
    c, _ = member("iap", {})
    assert decrypt_json(c.get("/world/1/in_app_purchases/google_play")) == {"purchase": []}
    assert decrypt_json(c.get("/world/1/in_app_purchases/google_play/packages")) == {"packages": []}
