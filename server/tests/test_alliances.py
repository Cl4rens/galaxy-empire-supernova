"""Alliances over the API: found, apply, approve, ranks, leave, dissolve."""

from fastapi.testclient import TestClient

from supernova import db, mails
from supernova import planet as planet_rules
from supernova.app import app

from .test_server import decrypt_json


def guest(udid):
    c = TestClient(app, headers={"openudid": udid})
    state = decrypt_json(c.post("/world/1/trials.json", content="username="))
    with db.player_state(state["user_id"]) as s:  # founding needs a depot and resources
        p = s["empire"]["main_planet"]
        planet_rules.building(p, 12).update(building_level=1, building_position_index=5)
        p.update(metal=5000, crystal=5000, deuterium=5000)
    return c, state


def empire(pid):
    return db.load_state(pid)["empire"]


def test_alliance_lifecycle():
    boss_c, boss = guest("ally-boss")
    member_c, member = guest("ally-member")

    created = decrypt_json(boss_c.post("/world/1/alliances.json", content="alliance_name=Nova Corps&description=Hi"))
    aid = created["alliance_id"]
    assert empire(boss["user_id"])["alliance_name"] == "Nova Corps"
    dup = member_c.post("/world/1/alliances.json", content="alliance_name=nova corps&description=")
    assert dup.status_code == 400 and "taken" in dup.text

    listed = decrypt_json(member_c.get("/world/1/alliances.json?page=0&count=0"))["alliances"]
    assert any(a["alliance_id"] == aid and a["member_count"] == 1 for a in listed)

    applied = decrypt_json(member_c.post("/world/1/alliance_applications.json", content=f"alliance_id={aid}"))
    app_id = applied["application_id"]
    assert empire(member["user_id"])["alliance_application_id"] == app_id
    assert mails.undelivered(boss["user_id"])[-1]["subject"] == "New application"

    pending = decrypt_json(boss_c.get(f"/world/1/alliance_applications.json?alliance_id={aid}"))["applications"]
    assert [a["application_id"] for a in pending] == [app_id]
    assert member_c.post(f"/world/1/alliance_applications/{app_id}.json",
                         content="_method=delete&do=approve").status_code == 400  # not allowed
    decrypt_json(boss_c.post(f"/world/1/alliance_applications/{app_id}.json", content="_method=delete&do=approve"))
    e = empire(member["user_id"])
    assert e["alliance_id"] == aid and e["alliance_application_id"] == 0 and e["alliance_membership_id"]

    details = decrypt_json(member_c.get(f"/world/1/alliances/{aid}.json"))
    assert details["member_count"] == 2 and details["president_name"] == boss["username"]
    members = decrypt_json(boss_c.get(f"/world/1/alliance_memberships.json?alliance_id={aid}"))["alliance_members"]
    member_ms = next(m["membership_id"] for m in members if m["user_id"] == member["user_id"])

    decrypt_json(boss_c.post(f"/world/1/alliance_memberships/{member_ms}.json", content="_method=put&level=1"))
    assert decrypt_json(member_c.get(f"/world/1/alliances/{aid}.json"))["vice_president_ids"] == [member_ms]

    top = decrypt_json(boss_c.get("/world/1/ranks?do=top_alliances"))["rank"]
    assert any(r["id"] == aid for r in top)

    left = decrypt_json(member_c.post(f"/world/1/alliance_memberships/{member_ms}.json", content="_method=delete"))
    assert left["alliance_id"] == 0 and empire(member["user_id"])["alliance_id"] == 0

    decrypt_json(boss_c.post(f"/world/1/alliances/{aid}.json", content="_method=delete"))
    assert empire(boss["user_id"])["alliance_id"] == 0
    assert db.connect().execute("SELECT COUNT(*) FROM alliances WHERE id = ?", (aid,)).fetchone()[0] == 0


def test_auto_join_and_cancel():
    boss_c, _ = guest("auto-boss")
    aid = decrypt_json(boss_c.post("/world/1/alliances.json", content="alliance_name=Open Door&description="))["alliance_id"]
    decrypt_json(boss_c.post(f"/world/1/alliances/{aid}/auto_in.json", content="status=1"))
    joiner_c, joiner = guest("auto-joiner")
    result = decrypt_json(joiner_c.post("/world/1/alliance_applications.json", content=f"alliance_id={aid}"))
    assert result["alliance"]["auto_in"] == 1 and empire(joiner["user_id"])["alliance_id"] == aid

    other_boss_c, _ = guest("closed-boss")
    closed = decrypt_json(other_boss_c.post("/world/1/alliances.json", content="alliance_name=Closed&description="))
    applicant_c, applicant = guest("canceller")
    app_id = decrypt_json(applicant_c.post("/world/1/alliance_applications.json",
                                           content=f"alliance_id={closed['alliance_id']}"))["application_id"]
    decrypt_json(applicant_c.post(f"/world/1/alliance_applications/{app_id}.json", content="_method=delete&do=cancel"))
    assert empire(applicant["user_id"])["alliance_application_id"] == 0


def test_founding_needs_depot_and_costs_resources():
    c = TestClient(app, headers={"openudid": "poor"})
    state = decrypt_json(c.post("/world/1/trials.json", content="username="))
    no_depot = c.post("/world/1/alliances.json", content="alliance_name=Nope&description=")
    assert no_depot.status_code == 400 and "depot" in no_depot.text
    rich_c, rich = guest("rich")
    decrypt_json(rich_c.post("/world/1/alliances.json", content="alliance_name=Paid&description="))
    assert db.load_state(rich["user_id"])["empire"]["main_planet"]["metal"] < 5000 - 990
