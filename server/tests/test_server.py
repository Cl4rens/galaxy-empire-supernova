"""End-to-end tests of the client-facing API, using a random key instead of the real one."""

import json
import os
import secrets
import tempfile
from urllib.parse import quote

os.environ["SUPERNOVA_REQUIRE_KEY"] = "0"
os.environ["SUPERNOVA_DATA"] = tempfile.mkdtemp()

from fastapi.testclient import TestClient  # noqa: E402

from supernova import crypto, world  # noqa: E402
from supernova.app import app  # noqa: E402

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


def test_crypto_roundtrip():
    assert crypto.decrypt(KEY, crypto.encrypt(KEY, "Commander1")) == "Commander1"


def test_guest_login_and_session():
    new_guest = client.post("/world/1/trials.json", content="username=")
    state = decrypt_json(new_guest)
    username = state["username"]
    assert state["empire"]["main_planet"]["current_server_time"] > 0
    assert any(b["building_type"] == 1 and b["building_level"] == 1
               for b in state["empire"]["main_planet"]["buildings"])

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
