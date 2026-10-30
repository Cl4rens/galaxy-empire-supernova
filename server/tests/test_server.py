"""End-to-end tests of the client-facing API, using a random key instead of the real one."""

import json
import secrets
from urllib.parse import quote

from fastapi.testclient import TestClient

from supernova import crypto, world
from supernova.app import app

KEY = secrets.token_bytes(32)
world.CLIENT_KEY = KEY
client = TestClient(app)


def decrypt_json(response) -> dict:
    assert response.status_code == 200, response.text
    return json.loads(crypto.decrypt(KEY, response.content))


def test_gateway():
    update = client.get("/servers/auto_update").json()
    assert update["lowestv"] == "1.9.36"
    assert client.get("/update/ver.txt").text.startswith("tag\n")
    # Some client URLs contain a double slash.
    assert client.get("http://testserver//servers/track_quest?event=x").status_code == 200
    world_entry = client.get("/servers.json").json()["server_list"][0]
    assert world_entry["server_root_url"].endswith("/world/1/")
    # a new server's name ends in "(New)", which the client cuts off ("SN01")
    assert world_entry["status"] == "new" and world_entry["server_name"].endswith("(New)")


def test_crypto_roundtrip():
    assert crypto.decrypt(KEY, crypto.encrypt(KEY, "Commander1")) == "Commander1"


def test_guest_login_and_session():
    new_guest = client.post("/world/1/trials.json", content="username=")
    state = decrypt_json(new_guest)
    username = state["username"]
    assert state["empire"]["main_planet"]["current_server_time"] > 0
    assert {b["building_type"] for b in state["empire"]["main_planet"]["buildings"]} >= {1, 2, 3}

    # Like the game client, the test client sends the session cookie back automatically.
    assert world.SESSION_COOKIE in client.cookies
    heartbeat = decrypt_json(client.get("/world/1/passive_events?heartBeatRate=6"))
    assert heartbeat["heartBeatRate"] == world.HEARTBEAT_SECONDS
    planet_id = state["empire"]["main_planet"]["planet_id"]
    assert decrypt_json(client.get(f"/world/1/planets/{planet_id}"))["planet_id"] == planet_id
    assert decrypt_json(client.get("/world/1/users/first_charge_misc")) == {"first_charge": False}

    # The client sends the saved username encrypted (ext.getENC2).
    encrypted = quote(crypto.encrypt(KEY, username).decode(), safe="")
    again = decrypt_json(client.post("/world/1/trials.json", content=f"username={encrypted}"))
    assert again["user_id"] == state["user_id"]


def test_requires_session():
    assert TestClient(app).get("/world/1/passive_events").status_code == 401


def test_build_metal_mine():
    c = TestClient(app)
    state = decrypt_json(c.post("/world/1/trials.json", content="username="))
    p = state["empire"]["main_planet"]
    mine = next(b for b in p["buildings"] if b["building_type"] == 1)
    body = f"planet_id={p['planet_id']}&building_id={mine['building_id']}&event_type=1&building_index=1"
    result = decrypt_json(c.post("/world/1/building_events.json", content=body))
    assert result["planet"]["metal"] == p["metal"] - mine["up_metal_require"]
    event = result["planet"]["building_events"][0]
    assert event["building_id"] == mine["building_id"] and event["finished"] is False

    again = c.post("/world/1/building_events.json", content=body)
    assert again.status_code == 400 and "already" in again.text

    cancel = decrypt_json(c.post(f"/world/1/building_events/{event['event_id']}.json", content="_method=delete"))
    assert cancel["planet"]["building_events"] == []


def test_research_requires_lab_over_api():
    c = TestClient(app)
    state = decrypt_json(c.post("/world/1/trials.json", content="username="))
    energy = next(r for r in state["empire"]["researches"] if r["research_type"] == 1)
    assert energy["tech_tree"]["buildings_tree"] == [{"unit_type": 11, "unit_level": 1, "unit_name": "Research Lab"}]
    response = c.post("/world/1/research_upgrading_events.json",
                      content=f"planet_id={state['empire']['main_planet']['planet_id']}&research_id={energy['research_id']}")
    assert response.status_code == 400 and "Requirements" in response.text


def test_player_mail():
    alice, bob = TestClient(app), TestClient(app)
    a = decrypt_json(alice.post("/world/1/trials.json", content="username="))
    b = decrypt_json(bob.post("/world/1/trials.json", content="username="))

    def enc(text):
        return quote(crypto.encrypt(KEY, text).decode(), safe="")
    body = f"to={enc(b['username'])}&subject={enc('Hello')}&body={enc('Peace?')}&message_type=3&from={enc(a['username'])}"
    assert alice.post("/world/1/messages.json", content=body).status_code == 200

    planet_id = b["empire"]["main_planet"]["planet_id"]
    assert decrypt_json(bob.get(f"/world/1/planets/{planet_id}"))["new_message_count"] == 1
    new = decrypt_json(bob.post("/world/1/messages/get_new.json", content="checklist="))
    mail = new["mails"][0]
    assert (mail["from"], mail["subject"], mail["body"], mail["message_type"]) == (a["username"], "Hello", "Peace?", 3)
    confirmed = decrypt_json(bob.post("/world/1/messages/get_new.json", content=f"checklist={mail['id']}"))
    assert confirmed == {"new_message_count": 0, "mails": []}


def test_email_binding():
    c = TestClient(app, headers={"openudid": "mail-user"})
    uid = decrypt_json(c.post("/world/1/trials.json", content="username="))["user_id"]
    assert decrypt_json(c.post(f"/world/1/users/{uid}/email_bind_status"))["mail_status"] == 0
    assert c.post(f"/world/1/users/{uid}/send_mail_for_authenticate", content="email=nope").status_code == 400
    bound = decrypt_json(c.post(f"/world/1/users/{uid}/send_mail_for_authenticate", content="email=a%40b.de"))
    assert bound == {"mail_status": 2, "email": "a@b.de"}
    assert decrypt_json(c.post(f"/world/1/users/{uid}/email_bind_status")) == bound


def test_heartbeat_reloads_the_empire_after_a_reward():
    from supernova import db
    c = TestClient(app, headers={"openudid": "reward-user"})
    me = decrypt_json(c.post("/world/1/trials.json", content="username="))
    beat = lambda: decrypt_json(c.get("/world/1/passive_events"))["empire"]
    assert beat() == 0
    with db.player_state(me["user_id"]) as state:  # a reward that did not send the empire back
        state["empire"]["dark_matter_count"] += 50
    assert beat() == 1 and beat() == 0
