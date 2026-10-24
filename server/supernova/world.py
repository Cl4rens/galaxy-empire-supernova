"""Game world endpoints under /world/<id>/. Responses are encrypted (client mode "normal").

The client keeps cookies, so the login sets a session cookie that identifies the player in
all following requests.
"""

import json
import logging
import time
from urllib.parse import parse_qs

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse, Response

from . import crypto, db, player

log = logging.getLogger("supernova")
router = APIRouter(prefix="/world/{world_id}")

CLIENT_KEY: bytes = b""  # set by app.py at startup
SESSION_COOKIE = "sn_session"
HEARTBEAT_SECONDS = 6


class NotLoggedIn(Exception):
    pass


async def form(request: Request) -> dict[str, str]:
    """Parameters of a POST (x-www-form-urlencoded body) or GET (query string)."""
    params = dict(request.query_params)
    if request.method == "POST":
        body = (await request.body()).decode("utf-8", "replace")
        params.update({k: v[0] for k, v in parse_qs(body, keep_blank_values=True).items()})
    return params


def encrypted_json(content: dict, status_code: int = 200) -> Response:
    body = crypto.encrypt(CLIENT_KEY, json.dumps(content, separators=(",", ":")))
    return Response(body, status_code=status_code, media_type="application/json")


def error(message: str, status_code: int = 400) -> Response:
    """For non-200 responses the client shows the body as text in a message box."""
    return PlainTextResponse(message, status_code=status_code)


def decrypt_param(value: str) -> str:
    """Decode a parameter the client encrypted with ext.getENC2 (e.g. the username)."""
    try:
        return crypto.decrypt(CLIENT_KEY, value.encode())
    except ValueError:
        return value


def current_player(request: Request) -> int:
    player_id = db.session_player(request.cookies.get(SESSION_COOKIE, ""))
    if player_id is None:
        raise NotLoggedIn()
    return player_id


def logged_in(player_id: int, content: dict) -> Response:
    response = encrypted_json(content)
    # The client's cookie parser keeps attributes with a leading space, so send none.
    response.headers["Set-Cookie"] = f"{SESSION_COOKIE}={db.create_session(player_id)}"
    return response


# --- Login --------------------------------------------------------------------------------


@router.post("/trials.json")
async def guest_login(world_id: int, request: Request):
    """Guest login. An empty username creates a new guest account."""
    username = decrypt_param((await form(request)).get("username", "")).strip()
    if username:
        found = db.find_player(username)
        if not found:
            return error("Unknown guest account", 404)
        player_id, state = found
    else:
        udid = request.headers.get("openudid", "")
        player_id, username, state = db.create_player(player.guest_username, udid, player.new_state)
        log.info("new guest %s (udid %s)", username, udid)
    return logged_in(player_id, player.login_response(state))


# --- Player state -------------------------------------------------------------------------


@router.get("/passive_events")
def heartbeat(player_id: int = Depends(current_player)):
    """Polled every few seconds. empire/planet = 1 makes the client reload that part."""
    return encrypted_json({"heartBeatRate": HEARTBEAT_SECONDS, "empire": 0, "planet": 0, "refresh": {}})


@router.get("/planets/{planet_id}")
def planet(planet_id: int, player_id: int = Depends(current_player)):
    main_planet = db.load_state(player_id)["empire"]["main_planet"]
    if main_planet["planet_id"] != planet_id:
        return error("Unknown planet", 404)
    return encrypted_json(dict(main_planet, current_server_time=int(time.time())))


# Must come before /users/{user_id}.
@router.get("/users/first_charge_misc")
def first_charge(player_id: int = Depends(current_player)):
    return encrypted_json({"first_charge": False})


@router.get("/users/{user_id}")
def user(user_id: int, player_id: int = Depends(current_player)):
    if user_id != player_id:
        return error("Unknown user", 404)
    # do=empire is the only variant seen so far.
    return encrypted_json(db.load_state(player_id)["empire"])


# --- Features we do not implement yet, answered with their "nothing here" values ---------


@router.get("/notices/popup.json")
def popup_notice(player_id: int = Depends(current_player)):
    return encrypted_json({"popup": {}})


@router.get("/notices/lastest.json")
def news(player_id: int = Depends(current_player)):
    return encrypted_json({"list": []})


@router.get("/gc/current")
def galaxy_challenge(player_id: int = Depends(current_player)):
    return encrypted_json({"status": 0})


@router.get("/promotions/my_newbie_invite_info")
def invite_info(player_id: int = Depends(current_player)):
    return encrypted_json({"my_code": f"SN{player_id:06d}"})


@router.api_route("/{path:path}", methods=["GET", "POST", "PUT", "DELETE"])
async def not_implemented(world_id: int, path: str, request: Request):
    body = await request.body()
    log.warning("UNHANDLED %s %s query=%s body=%s", request.method, request.url.path,
                dict(request.query_params), body[:2000].decode("utf-8", "replace"))
    return error(f"not implemented: {path}", 404)
