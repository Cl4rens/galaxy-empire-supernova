"""Registering, password login, guest accounts bound to their device."""

from urllib.parse import quote

from fastapi.testclient import TestClient

from supernova import crypto
from supernova.app import app

from .test_server import KEY, decrypt_json


def enc(text: str) -> str:
    return quote(crypto.encrypt(KEY, text).decode(), safe="")


def new_guest(udid="device-a"):
    c = TestClient(app, headers={"openudid": udid})
    return c, decrypt_json(c.post("/world/1/trials.json", content="username="))


def test_guest_account_is_bound_to_its_device():
    _, state = new_guest("device-a")
    other = TestClient(app, headers={"openudid": "device-b"})
    response = other.post("/world/1/trials.json", content=f"username={enc(state['username'])}")
    assert response.status_code == 403 and "another device" in response.text
    same = TestClient(app, headers={"openudid": "device-a"})
    assert decrypt_json(same.post("/world/1/trials.json", content=f"username={enc(state['username'])}"))


def test_register_then_login_anywhere():
    c, state = new_guest("device-c")
    body = f"username={enc('Starlord')}&password={enc('secret1')}&password_confirmation={enc('secret1')}"
    registered = decrypt_json(c.post("/world/1/users.json", content=body))
    assert registered["username"] == "Starlord" and registered["user_id"] == state["user_id"]

    phone = TestClient(app, headers={"openudid": "device-d"})
    wrong = phone.post("/world/1/sessions.json", content=f"username={enc('Starlord')}&password={enc('nope')}")
    assert wrong.status_code == 403
    me = decrypt_json(phone.post("/world/1/sessions.json", content=f"username={enc('Starlord')}&password={enc('secret1')}"))
    assert me["user_id"] == state["user_id"]
    # Guest login no longer works for this account.
    assert phone.post("/world/1/trials.json", content=f"username={enc('Starlord')}").status_code == 403

    taken = TestClient(app, headers={"openudid": "device-e"})
    taken.post("/world/1/trials.json", content="username=")
    clash = taken.post("/world/1/users.json",
                       content=f"username={enc('Starlord')}&password={enc('abcdef')}&password_confirmation={enc('abcdef')}")
    assert clash.status_code == 400 and "taken" in clash.text


def test_change_password_and_rename():
    c, state = new_guest("device-f")
    c.post("/world/1/users.json", content=f"username={enc('Nova')}&password={enc('first1')}&password_confirmation={enc('first1')}")
    uid = state["user_id"]
    changed = c.post(f"/world/1/users/{uid}.json", content=f"_method=put&old_password={enc('first1')}"
                     f"&new_password={enc('second2')}&new_password_confirmation={enc('second2')}")
    assert changed.status_code == 200 and "changed" in changed.text
    renamed = c.post("/world/1/users/reset_username", content=f"username={enc('Nova Prime')}&password={enc('second2')}")
    assert renamed.status_code == 200
    me = decrypt_json(TestClient(app).post("/world/1/sessions.json",
                                           content=f"username={enc('Nova Prime')}&password={enc('second2')}"))
    assert me["username"] == "Nova Prime"
