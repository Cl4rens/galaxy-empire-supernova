"""Chat: world and alliance channels, flood limit, reports."""

from supernova import chat, db
from supernova.app import app

from .test_alliances import guest
from .test_server import decrypt_json


def test_world_chat_newest_first_and_flood_limit():
    a_c, a = guest("chat-a")
    b_c, b = guest("chat-b")
    decrypt_json(a_c.post("/world/1/chat_messages.json", content="words=Hello  galaxy&channel=1&alliance_id=0"))
    assert a_c.post("/world/1/chat_messages.json", content="words=again&alliance_id=0").status_code == 400
    chat.send(b["user_id"], "Hi " + "x" * 300, 0, now=4_000_000_000)

    found = decrypt_json(b_c.get("/world/1/chat_messages.json?alliance_id=0&last_id=0&count=50"))["chat_messages"]
    assert found[0]["from_user_name"] == b["username"] and len(found[0]["words"]) == chat.MAX_LENGTH
    assert found[1]["words"] == "Hello galaxy" and found[1]["channel"] == chat.WORLD
    assert chat.last_line().startswith(b["username"] + ":Hi")

    decrypt_json(b_c.post("/world/1/reports", content=f"message_id={found[1]['message_id']}&additiona_notes=spam"))
    assert db.connect().execute("SELECT words FROM chat_reports").fetchone()["words"] == "Hello galaxy"


def test_alliance_chat_is_for_members_only():
    boss_c, boss = guest("chat-boss")
    aid = decrypt_json(boss_c.post("/world/1/alliances.json", content="alliance_name=Talkers&description="))["alliance_id"]
    decrypt_json(boss_c.post("/world/1/chat_messages.json", content=f"words=secret&channel=2&alliance_id={aid}"))
    stranger_c, _ = guest("chat-stranger")
    assert stranger_c.get(f"/world/1/chat_messages.json?alliance_id={aid}").status_code == 400
    mine = decrypt_json(boss_c.get(f"/world/1/chat_messages.json?alliance_id={aid}"))["chat_messages"]
    assert [m["words"] for m in mine] == ["secret"] and mine[0]["channel"] == chat.ALLIANCE
    world = decrypt_json(stranger_c.get("/world/1/chat_messages.json?alliance_id=0"))["chat_messages"]
    assert "secret" not in [m["words"] for m in world]
