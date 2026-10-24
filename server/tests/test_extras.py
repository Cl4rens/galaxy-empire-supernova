"""Bookmarks, planet info from mails, news, daily check-in and system gifts."""

from fastapi.testclient import TestClient

from supernova import checkin, db, gifts, mails, notices
from supernova.app import app

from .test_fleets import position
from .test_server import decrypt_json


def guest(udid):
    c = TestClient(app, headers={"openudid": udid})
    return c, decrypt_json(c.post("/world/1/trials.json", content="username="))


def test_bookmarks():
    c, me = guest("bookmarker")
    _, other = guest("bookmarked")
    target = other["empire"]["main_planet"]["planet_id"]
    decrypt_json(c.post("/world/1/bookmarks.json", content=f"planet_id={target}&bookmark_type=2"))
    assert c.post("/world/1/bookmarks.json", content=f"planet_id={target}&bookmark_type=2").status_code == 400
    marks = decrypt_json(c.post("/world/1/bookmarks/list.json"))["bookmarks"]
    assert marks[0]["planet_id"] == target and marks[0]["bookmark_type"] == 2
    assert decrypt_json(c.post(f"/world/1/bookmarks/{marks[0]['id']}", content="_method=delete"))["id"] == marks[0]["id"]
    assert decrypt_json(c.post("/world/1/bookmarks/list.json"))["bookmarks"] == []


def test_planet_info_for_mail_positions():
    c, me = guest("mail-reader")
    g, s, p = position(me["user_id"])
    info = decrypt_json(c.get(f"/world/1/planets/get_planet_info.json?position={g},{s},{p}"))
    assert info["player_name"] == me["username"] and info["temp_max"] == 150 - 12 * p
    empty = decrypt_json(c.get("/world/1/planets/get_planet_info.json?position=9,499,1"))
    assert empty["player_name"] == ""


def test_news():
    c, _ = guest("reader")
    nid = notices.add("update", "Version 1.1", "<b>New:</b> moons")
    listed = decrypt_json(c.get("/world/1/notices"))["list"]
    assert listed[0]["id"] == nid and listed[0]["notice_type"] == "update"
    assert decrypt_json(c.get(f"/world/1/notices/{nid}.json"))["content"] == "<b>New:</b> moons"
    notices.delete(nid)
    assert c.get(f"/world/1/notices/{nid}.json").status_code == 404


def test_daily_checkin_counts_days_and_pays_once():
    state = {"empire": {"main_planet": {"metal": 0, "crystal": 0, "deuterium": 0}}}
    day = 86400
    checkin.visit(state, 1_000 * day)
    checkin.visit(state, 1_000 * day + 3600)  # same day
    checkin.visit(state, 1_001 * day)
    assert checkin.unclaimed(state) == 2
    first = checkin.claim(state, "n1")
    assert first["result"]["ok"] and state["empire"]["main_planet"]["metal"] > 0
    assert not checkin.claim(state, "n1")["result"]["ok"]
    assert not checkin.claim(state, "n3")["result"]["ok"]
    assert first["status"] == {"n1": True} and first["total_days"] == 2
    assert checkin.info(state)["config"]["n7"]["g"]["type"] == "fleet"


def test_checkin_over_api():
    c, me = guest("checker")
    info = decrypt_json(c.get("/world/1/users/checkin_info"))
    assert info["total_days"] == 1 and len(info["config"]) == checkin.DAYS
    got = decrypt_json(c.get("/world/1/users/draw_checkin_gift?gift_name=n1"))
    assert got["result"]["ok"] and got["status"]["n1"]


def test_welcome_gift_and_operator_gifts():
    c, me = guest("gifted")
    gift_id = me["empire"]["gift_id"]
    assert gift_id and decrypt_json(c.get(f"/world/1/system_gifts/{gift_id}"))["rewards"]
    metal = db.load_state(me["user_id"])["empire"]["main_planet"]["metal"]
    pid = me["empire"]["main_planet"]["planet_id"]
    claimed = decrypt_json(c.post("/world/1/system_gifts/get_gift", content=f"planet_id={pid}&gift_id={gift_id}"))
    assert claimed["empire"]["gift_id"] == 0 and claimed["planet"]["metal"] >= int(metal) + 2000
    assert c.post("/world/1/system_gifts/get_gift", content=f"gift_id={gift_id}").status_code == 400
    gifts.credit(me["user_id"], "Sorry", "Server was down", {}, {9: 2})
    home = db.load_state(me["user_id"])["empire"]["planets"][0]
    assert next(f for f in home["fleets"] if f["fleet_type"] == 9)["amount"] == 2
    assert mails.undelivered(me["user_id"])[-1]["subject"] == "Sorry"


def test_faq_and_device_registration():
    c, _ = guest("helpless")
    assert decrypt_json(c.get("/world/1/faqs/game_faq"))[0]["title"]
    assert decrypt_json(c.post("/world/1/users/regist_device", content="reg_id=abc")) == {}
