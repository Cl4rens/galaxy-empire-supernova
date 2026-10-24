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

from . import accounts, acs, alliances, bookmarks, chat, checkin, combat, crypto, db, faq, fleets, gifts, mails
from . import ecm, flagships, fortresses, items, kryptons, lordship, notices, player, quests, ranking, repairs
from . import economy as eco
from . import planet as planet_rules
from . import universe

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


def encrypted_json(content: dict | list, status_code: int = 200) -> Response:
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
    udid = request.headers.get("openudid", "")
    if username:
        try:
            player_id = accounts.guest_login(username, udid)
        except accounts.AccountError as e:
            return error(str(e), 403)
    else:
        player_id, username, _ = db.create_player(
            player.guest_username, udid,
            lambda pid, name: player.new_state(pid, name, universe.place_home(pid, player.HOME_PLANET_NAME)))
        gifts.send(player_id, *gifts.WELCOME)
        log.info("new guest %s (udid %s)", username, udid)
    return enter_world(player_id)


@router.post("/sessions.json")
async def password_login(world_id: int, request: Request):
    """Login with name and password; both are encrypted like the username."""
    params = await form(request)
    try:
        player_id = accounts.password_login(decrypt_param(params.get("username", "")).strip(),
                                            decrypt_param(params.get("password", "")))
    except accounts.AccountError as e:
        return error(str(e), 403)
    return enter_world(player_id)


def enter_world(player_id: int) -> Response:
    now = time.time()
    with db.player_state(player_id) as state:
        for p in state["empire"]["planets"]:
            universe.ensure_registered(player_id, p)
        current = state["empire"]["main_planet"]
        if planet_rules.is_moon(current):  # the client always starts on a planet
            parent = next(p for p in state["empire"]["planets"] if p.get("moon") is current)
            planet_rules.switch_planet(state["empire"], parent["planet_id"])
        planet_rules.tick(state["empire"], now)
        checkin.visit(state, now)
        content = player.login_response(state, now)
    return logged_in(player_id, content)


# --- Accounts ---------------------------------------------------------------------------------


@router.post("/users.json")
async def register(world_id: int, request: Request, player_id: int = Depends(current_player)):
    """A guest chooses a name and password ("end trial")."""
    params = await form(request)
    try:
        accounts.register(player_id, decrypt_param(params.get("username", "")).strip(),
                          decrypt_param(params.get("password", "")),
                          decrypt_param(params.get("password_confirmation", "")))
    except accounts.AccountError as e:
        return error(str(e))
    with db.player_state(player_id) as state:
        return encrypted_json(player.login_response(state, time.time()))


@router.post("/users/reset_username")
async def rename(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    try:
        accounts.rename(player_id, decrypt_param(params.get("username", "")).strip(),
                        decrypt_param(params.get("password", "")))
    except accounts.AccountError as e:
        return error(str(e))
    # The client shows the answer as a message.
    return PlainTextResponse("Your name has been changed.")


@router.post("/users/{user_id}/email_bind_status")
def email_status(user_id: int, player_id: int = Depends(current_player)):
    return encrypted_json({"mail_status": 0})  # e-mail binding is not offered


# --- Player state -------------------------------------------------------------------------


@router.get("/passive_events")
def heartbeat(player_id: int = Depends(current_player)):
    """Polled every few seconds. empire/planet = 1 makes the client reload that part.

    The client does not count resources up by itself, so it reloads the planet every time.
    While an attack is on its way, the empire (with the flight list) is reloaded too.
    """
    under_attack = bool(fleets.incoming_attacks(player_id))
    return encrypted_json({"heartBeatRate": HEARTBEAT_SECONDS, "empire": int(under_attack), "planet": 1,
                           "refresh": {}})


def use_planet(state: dict, params: dict, *keys: str) -> None:
    """Requests name the planet they act on; make it the current one."""
    for key in keys or ("planet_id",):
        value = params.get(key)
        if value and str(value).isdigit():
            planet_rules.switch_planet(state["empire"], int(value))
            return


@router.get("/planets/get_planet_info.json")
async def planet_info_at(world_id: int, request: Request, player_id: int = Depends(current_player)):
    """The info box for a position mentioned in a mail."""
    try:
        galaxy, system, position = fleets.parse_target((await form(request)).get("position", ""))
    except planet_rules.GameError as e:
        return error(str(e))
    row = fleets.target_planet((galaxy, system, position))
    owner = db.load_state(row["player_id"])["empire"] if row else {}
    return encrypted_json({
        "location": row["name"] if row else f"{galaxy}:{system}:{position}",
        "player_name": db.connect().execute("SELECT username FROM players WHERE id = ?",
                                            (row["player_id"],)).fetchone()["username"] if row else "",
        "alliance_id": owner.get("alliance_id", 0),
        "alliance_name": owner.get("alliance_name", ""),
        "temp_max": 150 - 12 * min(position, universe.PLANET_POSITIONS),
        "temp_min": 110 - 12 * min(position, universe.PLANET_POSITIONS),
    })


@router.get("/fleets/{fleet_id}")
def unit_info(fleet_id: int, player_id: int = Depends(current_player)):
    with db.player_state(player_id) as state:
        empire = state["empire"]
        for planet in planet_rules.bodies(empire):
            f = next((f for f in planet["fleets"] if f["fleet_id"] == fleet_id), None)
            if f and f["fleet_type"] in combat.STATS:
                kryptons.sync_planets(state)
                return encrypted_json(fleets.unit_info(empire, planet, f["fleet_type"]))
    return error("Unknown unit", 404)


@router.get("/users/my_buffs_ext")
def buffs(planet_id: int = 0, player_id: int = Depends(current_player)):
    """Active buffs (none yet: they come from shop items) and the planet's krypton bonuses."""
    with db.player_state(player_id) as state:
        kryptons.sync_planets(state)
        planet = planet_rules.find_planet(state["empire"], planet_id) or state["empire"]["main_planet"]
        storage = kryptons.storage_view(state)
        found = next((b["buffs"] for b in storage["kryptons"]["all_buffs"] if b["planet_id"] == planet["planet_id"]), [])
        return encrypted_json({"buff_list": [], "krypton_buffs": found, "kryptons": planet["kryptons"]})


@router.get("/planets/{planet_id}.json")
def enter_planet(planet_id: int, player_id: int = Depends(current_player)):
    """The player switches to another of their planets."""
    now = time.time()
    try:
        with db.player_state(player_id) as state:
            planet_rules.switch_planet(state["empire"], planet_id)
            planet_rules.tick(state["empire"], now)
            view = planet_rules.client_view(state["empire"], now)
            view["researches"] = planet_rules.researches_view(state["empire"])["researches"]
    except planet_rules.GameError as e:
        return error(str(e), 404)
    view["new_message_count"] = mails.unread_count(player_id)
    return encrypted_json(view)


@router.post("/planets/{planet_id}/change_name.json")
async def rename_planet(world_id: int, planet_id: int, request: Request, player_id: int = Depends(current_player)):
    name = decrypt_param((await form(request)).get("name", "")).strip()
    if not 1 <= len(name) <= 20:
        return error("Planet names have 1-20 characters")
    with db.player_state(player_id) as state:
        planet = planet_rules.find_planet(state["empire"], planet_id)
        if planet is None:
            return error("This is not your planet", 404)
        planet["planet_name"] = name
        conn = db.connect()
        with db.lock(), conn:
            conn.execute("UPDATE planets SET name = ? WHERE id = ?", (name, planet_id))
    return encrypted_json({"planet_name": name})


@router.post("/planets/{planet_id}")
async def abandon_planet(world_id: int, planet_id: int, request: Request, player_id: int = Depends(current_player)):
    """_method=delete gives up a colony or moon (a colony with its moon). The home planet and planets
    with fleets on the way stay."""
    if (await form(request)).get("_method") != "delete":
        return error("Unsupported request")
    now = time.time()
    with db.player_state(player_id) as state:
        empire = state["empire"]
        target = planet_rules.find_planet(empire, planet_id)
        if target is None:
            return error("This is not your planet", 404)
        if empire["planets"][0]["planet_id"] == planet_id:
            return error("You cannot give up your home planet")
        gone = [target] + ([target["moon"]] if target.get("moon") else [])
        if any(fleets.planet_has_flights(p["planet_id"]) for p in gone):
            return error("Fleets are flying from or to this planet")
        if planet_rules.is_moon(target):
            next(p for p in empire["planets"] if p.get("moon") is target)["moon"] = None
        else:
            empire["planets"].remove(target)
        for p in gone:
            universe.remove_planet(p["planet_id"])
        planet_rules.switch_planet(empire, empire["planets"][0]["planet_id"])
        planet_rules.tick(empire, now)
        return encrypted_json({"empire_info": player.empire_summary(state),
                               "planet_info": planet_rules.client_view(empire, now)})


@router.get("/users/{user_id}/my_planets.json")
def my_planets(user_id: int, player_id: int = Depends(current_player)):
    now = time.time()
    with db.player_state(player_id) as state:
        planet_rules.tick(state["empire"], now)
        return encrypted_json({"planets_info": player.planets_info(state["empire"]),
                               "empire": player.empire_summary(state)})


@router.get("/planets/{planet_id}")
def planet(planet_id: int, player_id: int = Depends(current_player)):
    now = time.time()
    with db.player_state(player_id) as state:
        if planet_rules.find_planet(state["empire"], planet_id) is None:
            return error("Unknown planet", 404)
        planet_rules.switch_planet(state["empire"], planet_id)
        planet_rules.tick(state["empire"], now)
        view = planet_rules.client_view(state["empire"], now)
    # Shown as the number on the mail button; updatePlanet passes it on.
    view["new_message_count"] = mails.unread_count(player_id)
    view["is_underattack"] = bool(fleets.incoming_attacks(player_id))
    view["last_chat"] = chat.last_line() or view["last_chat"]
    return encrypted_json(view)


# Must come before /users/{user_id}.
@router.get("/users/research_info.json")
def research_info(research_id: int, player_id: int = Depends(current_player)):
    """Requested when a research timer runs out on the client."""
    now = time.time()
    with db.player_state(player_id) as state:
        planet_rules.tick(state["empire"], now)
        research = next((r for r in state["empire"]["researches"] if r["research_id"] == research_id), None)
        if research is None:
            return error("Unknown research", 404)
        return research_update(state, research, now)


# Must come before /users/{user_id}.
@router.get("/users/checkin_info")
def checkin_info(player_id: int = Depends(current_player)):
    with db.player_state(player_id) as state:
        checkin.visit(state, time.time())
        return encrypted_json(checkin.info(state))


@router.get("/users/draw_checkin_gift")
def checkin_claim(gift_name: str = "", player_id: int = Depends(current_player)):
    with db.player_state(player_id) as state:
        planet_rules.tick(state["empire"], time.time())
        return encrypted_json(checkin.claim(state, gift_name))


@router.post("/users/regist_device")
def register_device(player_id: int = Depends(current_player)):
    return encrypted_json({})  # push notifications are not sent


@router.get("/users/first_charge_misc")
def first_charge(player_id: int = Depends(current_player)):
    return encrypted_json({"first_charge": False})


@router.post("/users/{user_id}.json")
async def change_password(user_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    if user_id != player_id or params.get("_method") != "put":
        return error("Unsupported")
    try:
        accounts.change_password(player_id, decrypt_param(params.get("old_password", "")),
                                 decrypt_param(params.get("new_password", "")),
                                 decrypt_param(params.get("new_password_confirmation", "")))
    except accounts.AccountError as e:
        return error(str(e))
    return PlainTextResponse("Your password has been changed.")


@router.get("/users/{user_id}")
def user(user_id: int, player_id: int = Depends(current_player)):
    if user_id != player_id:
        return error("Unknown user", 404)
    # do=empire is the only variant seen so far.
    now = time.time()
    with db.player_state(player_id) as state:
        planet_rules.tick(state["empire"], now)
        return encrypted_json(player.empire_view(state, now))


# --- Buildings ----------------------------------------------------------------------------


def building_update(state: dict, changed: dict, now: float) -> Response:
    """Answer for building actions: the changed building plus the refreshed empire and planet."""
    planet_now = state["empire"]["main_planet"]
    return encrypted_json({
        "update": {"buildings": [planet_rules.building_view(planet_now, changed)]},
        "empire": player.empire_summary(state),
        "planet": planet_rules.client_view(state["empire"], now),
    })


@router.post("/building_events.json")
async def start_building(world_id: int, request: Request, player_id: int = Depends(current_player)):
    """Upgrade (event_type 1) or tear down (2) a building. New buildings also send their lot."""
    params = await form(request)
    now = time.time()
    try:
        with db.player_state(player_id) as state:
            use_planet(state, params)
            changed = planet_rules.start_building_event(
                state["empire"], state, int(params["building_id"]), int(params["event_type"]),
                int(params["building_index"]) if params.get("building_index") else None, now)
            return building_update(state, changed, now)
    except planet_rules.GameError as e:
        return error(str(e))


@router.post("/building_events/{event_id}.json")
async def cancel_building(world_id: int, event_id: int, request: Request,
                          player_id: int = Depends(current_player)):
    """The client sends _method=delete to cancel a construction."""
    if (await form(request)).get("_method") != "delete":
        return error("Unsupported", 400)
    now = time.time()
    try:
        with db.player_state(player_id) as state:
            changed = planet_rules.cancel_building_event(state["empire"], event_id, now)
            return building_update(state, changed, now)
    except planet_rules.GameError as e:
        return error(str(e))


@router.get("/planets/{planet_id}/build_info.json")
def build_info(planet_id: int, bid: int, player_id: int = Depends(current_player)):
    """Requested when a construction timer runs out on the client."""
    now = time.time()
    with db.player_state(player_id) as state:
        planet_rules.tick(state["empire"], now)
        changed = next((b for b in state["empire"]["main_planet"]["buildings"] if b["building_id"] == bid), None)
        if changed is None:
            return error("Unknown building", 404)
        return building_update(state, changed, now)


# --- Research -----------------------------------------------------------------------------


def research_update(state: dict, changed: dict, now: float) -> Response:
    """Answer for research actions: the changed research plus the refreshed empire and planet."""
    return encrypted_json({
        "update": {"researches": [planet_rules.research_view(state["empire"], changed)]},
        "empire": player.empire_summary(state),
        "planet": planet_rules.client_view(state["empire"], now),
    })


@router.post("/research_upgrading_events.json")
async def start_research(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    now = time.time()
    try:
        with db.player_state(player_id) as state:
            use_planet(state, params)
            research = planet_rules.start_research(state["empire"], state, int(params["research_id"]), now)
            return research_update(state, research, now)
    except planet_rules.GameError as e:
        return error(str(e))


@router.post("/research_upgrading_events/{event_id}")
async def cancel_research(world_id: int, event_id: int, request: Request,
                          player_id: int = Depends(current_player)):
    """The client sends _method=delete to cancel research."""
    params = await form(request)
    if params.get("_method") != "delete":
        return error("Unsupported", 400)
    now = time.time()
    try:
        with db.player_state(player_id) as state:
            use_planet(state, params, "current_planet_id")
            research = planet_rules.cancel_research(state["empire"], event_id, now)
            return research_update(state, research, now)
    except planet_rules.GameError as e:
        return error(str(e))


# --- Shipyard -----------------------------------------------------------------------------


def fleet_update(state: dict, changed: dict, now: float) -> Response:
    """Answer for shipyard actions: the changed unit type plus the refreshed empire and planet."""
    planet_now = state["empire"]["main_planet"]
    return encrypted_json({
        "update": {"fleets": [planet_rules.fleet_view(planet_now, changed)]},
        "empire": player.empire_summary(state),
        "planet": planet_rules.client_view(state["empire"], now),
    })


@router.post("/fleet_building_events.json")
async def start_fleet_order(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    now = time.time()
    try:
        with db.player_state(player_id) as state:
            use_planet(state, params)
            changed = planet_rules.start_fleet_order(state["empire"], state, int(params["fleet_id"]),
                                                     int(params["fleet_count"]), now)
            return fleet_update(state, changed, now)
    except planet_rules.GameError as e:
        return error(str(e))


@router.post("/fleet_building_events/{event_id}/cancel.json")
async def cancel_fleet_order(world_id: int, event_id: int, player_id: int = Depends(current_player)):
    now = time.time()
    try:
        with db.player_state(player_id) as state:
            changed = planet_rules.cancel_fleet_order(state["empire"], event_id, now)
            return fleet_update(state, changed, now)
    except planet_rules.GameError as e:
        return error(str(e))


@router.get("/planets/{planet_id}/fleets_build_info")
def fleets_build_info(planet_id: int, fleet_id: int, player_id: int = Depends(current_player)):
    """Requested when a shipyard order's timer runs out on the client."""
    now = time.time()
    with db.player_state(player_id) as state:
        planet_rules.tick(state["empire"], now)
        changed = next((f for f in state["empire"]["main_planet"]["fleets"] if f["fleet_id"] == fleet_id), None)
        if changed is None:
            return error("Unknown unit", 404)
        return fleet_update(state, changed, now)


# --- Fleet missions -------------------------------------------------------------------------


def fleet_response(state: dict, now: float) -> Response:
    """Answer for fleet actions: the empire (with flights), the planet and its ships."""
    planet_now = state["empire"]["main_planet"]
    return encrypted_json({
        "update": {"fleets": [planet_rules.fleet_view(planet_now, f) for f in planet_now["fleets"]]},
        "empire": player.empire_summary(state),
        "planet": planet_rules.client_view(state["empire"], now),
    })


@router.get("/fleet_hanging_events/show_be_attacked")
def flights_overview(player_id: int = Depends(current_player)):
    """The dispatch screen's flight list: own flights and hostile fleets on their way here."""
    return encrypted_json({
        "hanging_info": fleets.events_view(player_id),
        "be_attacked_hanging_info": [],  # no attacks yet
        "planet": {"current_server_time": int(time.time())},
    })


@router.get("/fleet_hanging_events/prepare_hanging")
async def prepare_fleet(world_id: int, request: Request, player_id: int = Depends(current_player)):
    """Speed, flight time, fuel, cargo space and possible missions for the selected ships."""
    params = await form(request)
    try:
        with db.player_state(player_id) as state:
            empire = state["empire"]
            origin = fleets.origin_planet(empire, params)
            ships = fleets.parse_ships(origin, params.get("fleet_ids", ""), params.get("fleet_cnt_ids", ""))
            target = fleets.parse_target(params.get("end_p_pos", ""))
            info = fleets.plan(empire, player_id, ships, target, origin=origin, moon=fleets.targets_moon(params))
    except planet_rules.GameError as e:
        return error(str(e))
    return encrypted_json({k: v for k, v in info.items() if k != "distance"})


@router.post("/fleet_hanging_events.json")
async def send_fleet(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    now = time.time()
    try:
        with db.player_state(player_id) as state:
            fleets.send(player_id, state, params, now)
            return fleet_response(state, now)
    except planet_rules.GameError as e:
        return error(str(e))


@router.post("/fleet_hanging_events/{event_id}/cancel.json")
def recall_fleet(world_id: int, event_id: int, player_id: int = Depends(current_player)):
    now = time.time()
    try:
        fleets.recall(player_id, event_id, now)
    except planet_rules.GameError as e:
        return error(str(e))
    with db.player_state(player_id) as state:
        planet_rules.tick(state["empire"], now)
        return fleet_response(state, now)


@router.get("/planets/{planet_id}/fleet_info.json")
def fleet_info(planet_id: int, player_id: int = Depends(current_player)):
    """Requested when a flight timer runs out. The client merges the answer into both its
    empire and its planet, so it holds the flights and the planet fields side by side."""
    now = time.time()
    fleets.process_due(now)
    with db.player_state(player_id) as state:
        planet_rules.tick(state["empire"], now)
        content = planet_rules.client_view(state["empire"], now)
    content["fleet_hanging_events"] = fleets.events_view(player_id)
    content["fortress_interspace_events"] = fortresses.events_view(player_id, now)
    content["is_underattack"] = bool(fleets.incoming_attacks(player_id))
    return encrypted_json(content)


# --- Alliances -------------------------------------------------------------------------------


def alliance_call(fn):
    try:
        return fn()
    except alliances.AllianceError as e:
        return error(str(e))


@router.get("/alliances.json")
async def alliance_list(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    keyword = decrypt_param(params.get("keyword", "")) if params.get("do") == "search" else ""
    return encrypted_json({"alliances": alliances.listing(keyword)})


@router.post("/alliances.json")
async def alliance_create(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    return alliance_call(lambda: encrypted_json(
        alliances.create(player_id, params.get("alliance_name", ""), params.get("description", ""))))


@router.get("/alliances/{alliance_id}.json")
def alliance_details(alliance_id: int, player_id: int = Depends(current_player)):
    return alliance_call(lambda: encrypted_json(alliances.details(alliance_id, player_id)))


@router.post("/alliances/{alliance_id}.json")
async def alliance_change(world_id: int, alliance_id: int, request: Request,
                          player_id: int = Depends(current_player)):
    """_method=delete dissolves the alliance, _method=put changes description/activity."""
    params = await form(request)

    def act():
        if params.get("_method") == "delete":
            alliances.dissolve(player_id, alliance_id)
        else:
            alliances.update_texts(player_id, alliance_id, params.get("description"), params.get("activity"))
        return encrypted_json({})
    return alliance_call(act)


@router.post("/alliances/{alliance_id}/auto_in.json")
async def alliance_auto_join(world_id: int, alliance_id: int, request: Request,
                             player_id: int = Depends(current_player)):
    on = (await form(request)).get("status") == "1"
    return alliance_call(lambda: encrypted_json({"auto_in": alliances.set_auto_join(player_id, alliance_id, on)}))


@router.post("/alliances/{alliance_id}/list_rewards")
def alliance_rewards(alliance_id: int, player_id: int = Depends(current_player)):
    return encrypted_json({"gifts": fortresses.gifts(player_id)})


@router.post("/alliances/{alliance_id}/get_reward")
async def alliance_get_reward(world_id: int, alliance_id: int, request: Request,
                              player_id: int = Depends(current_player)):
    reward_id = (await form(request)).get("reward_id", "")
    try:
        return encrypted_json({"info": fortresses.claim_gift(player_id, int(reward_id) if reward_id.isdigit() else 0)})
    except planet_rules.GameError as e:
        return error(str(e))


@router.post("/alliances/{alliance_id}/bag_log")
def alliance_log(alliance_id: int, player_id: int = Depends(current_player)):
    return alliance_call(lambda: encrypted_json({"log": alliances.bag_log(player_id, alliance_id)}))


@router.post("/alliances/{alliance_id}/donate")
async def alliance_donate(world_id: int, alliance_id: int, request: Request,
                          player_id: int = Depends(current_player)):
    params = await form(request)

    def amount(key: str) -> int:
        value = params.get(key) or "0"
        return int(float(value)) if value.replace(".", "", 1).isdigit() else -1
    return alliance_call(lambda: encrypted_json(
        alliances.donate(player_id, alliance_id, amount("dark_matter"), amount("credit"))))


@router.get("/alliance_memberships.json")
def alliance_members(alliance_id: int, player_id: int = Depends(current_player)):
    return encrypted_json({"alliance_members": alliances.members_view(alliance_id)})


@router.post("/alliance_memberships/{membership_id}.json")
async def alliance_member_change(world_id: int, membership_id: int, request: Request,
                                 player_id: int = Depends(current_player)):
    """_method=delete: leave (own membership) or kick; _method=put with level: change rank."""
    params = await form(request)

    def act():
        if params.get("_method") == "put":
            alliance_id = alliances.change_post(player_id, membership_id, int(params.get("level", 0)))
        else:
            alliance_id = alliances.kick_or_leave(player_id, membership_id)
        empire = db.load_state(player_id)["empire"]
        return encrypted_json({"alliance_members": alliances.members_view(alliance_id),
                               "alliance_id": empire["alliance_id"], "alliance_name": empire["alliance_name"],
                               "alliance_membership_id": empire["alliance_membership_id"]})
    return alliance_call(act)


@router.get("/alliance_applications.json")
def alliance_applications(alliance_id: int, player_id: int = Depends(current_player)):
    return encrypted_json({"applications": alliances.applications_view(alliance_id)})


@router.post("/alliance_applications.json")
async def alliance_apply(world_id: int, request: Request, player_id: int = Depends(current_player)):
    alliance_id = int((await form(request)).get("alliance_id", 0))

    def act():
        result = alliances.apply(player_id, alliance_id)
        result["planet"] = {}
        return encrypted_json(result)
    return alliance_call(act)


@router.post("/alliance_applications/{application_id}.json")
async def alliance_application_change(world_id: int, application_id: int, request: Request,
                                      player_id: int = Depends(current_player)):
    """do=cancel (applicant), do=approve or do=reject (president, vice president)."""
    action = (await form(request)).get("do")

    def act():
        if action == "cancel":
            alliances.cancel_application(player_id, application_id)
            return encrypted_json({"alliance_id": 0, "alliance_name": "", "alliance_application_id": 0})
        alliance_id = alliances.decide(player_id, application_id, action == "approve")
        return encrypted_json({"applications": alliances.applications_view(alliance_id)})
    return alliance_call(act)


def acs_call(player_id: int, act, with_fleets: bool = False) -> Response:
    now = time.time()
    try:
        with db.player_state(player_id) as state:
            planet_rules.tick(state["empire"], now)
            result = act(state, now)
            planet = state["empire"]["main_planet"]
            result.update(empire=player.empire_summary(state), planet=planet_rules.client_view(state["empire"], now))
            if with_fleets:
                result["update"] = {"fleets": [planet_rules.fleet_view(planet, f) for f in planet["fleets"]]}
            return encrypted_json(result)
    except planet_rules.GameError as e:
        return error(str(e))


@router.get("/acses")
def acs_list(player_id: int = Depends(current_player)):
    return acs_call(player_id, lambda state, now: {"info": acs.listing(player_id, now)})


@router.get("/acses/{acs_id}")
def acs_details(acs_id: int, player_id: int = Depends(current_player)):
    return acs_call(player_id, lambda state, now: {"info": acs.details(player_id, acs_id, now)})


@router.get("/acses/{acs_id}/prepare_append")
def acs_prepare(acs_id: int, planet_id: int = 0, fleets: str = "", player_id: int = Depends(current_player)):
    return acs_call(player_id, lambda state, now: acs.prepare(player_id, state, acs_id, planet_id, fleets, now))


@router.post("/acses/{acs_id}/append")
async def acs_join(world_id: int, acs_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    return acs_call(player_id, lambda state, now: {"acs_info": acs.join(
        player_id, state, acs_id, int(params.get("planet_id") or 0), params.get("fleets", ""), now)}, with_fleets=True)


@router.post("/acses/{acs_id}/delete")
def acs_call_off(acs_id: int, player_id: int = Depends(current_player)):
    def act(state, now):
        acs.call_off(player_id, acs_id, now)
        return {}
    return acs_call(player_id, act)


@router.post("/acses/{acs_id}/expand_acs_limit")
def acs_expand(acs_id: int, player_id: int = Depends(current_player)):
    return error(f"A coordinated attack takes at most {acs.MAX_FLEETS} fleets")


@router.get("/fleet_hanging_events/{event_id}")
def flight_ships(event_id: int, player_id: int = Depends(current_player)):
    try:
        return encrypted_json({"hanging_info": acs.fleet_list(player_id, event_id)})
    except planet_rules.GameError as e:
        return error(str(e))


@router.post("/sensor_arrays/scan")
async def sensor_scan(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    try:
        with db.player_state(player_id) as state:
            found = fleets.scan(state["empire"], int(params.get("moon_id") or 0), int(params.get("planet_id") or 0),
                                time.time())
    except planet_rules.GameError as e:
        return error(str(e))
    return encrypted_json({"data": found})


@router.post("/sensor_arrays/save_report_mail")
async def sensor_save(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    try:
        with db.player_state(player_id) as state:
            fleets.save_scan(player_id, state["empire"], int(params.get("moon_id") or 0),
                             int(params.get("planet_id") or 0))
    except planet_rules.GameError as e:
        return error(str(e))
    return encrypted_json({})


# --- Kryptons -------------------------------------------------------------------------------


def krypton_call(player_id: int, act, with_empire: bool = False) -> Response:
    """Run act(state, now) under the player's lock; game errors become error answers."""
    now = time.time()
    try:
        with db.player_state(player_id) as state:
            planet_rules.tick(state["empire"], now)
            kryptons.sync_planets(state)
            result = act(state, now)
            if with_empire:
                result = dict(result, empire=player.empire_summary(state),
                              planet=planet_rules.client_view(state["empire"], now))
            return encrypted_json(result)
    except planet_rules.GameError as e:
        return error(str(e))


def _int(params: dict, key: str, default: int = 0) -> int:
    value = params.get(key, "")
    return int(value) if str(value).lstrip("-").isdigit() else default


@router.api_route("/alchemy", methods=["GET", "POST"])
def refinery(player_id: int = Depends(current_player)):
    return krypton_call(player_id, lambda state, now: kryptons.refinery_view(state))


@router.api_route("/alchemy/save_to_db", methods=["GET", "POST"])
def refinery_save(player_id: int = Depends(current_player)):
    return encrypted_json({})  # everything is saved at once


@router.api_route("/alchemy/fuse_krypton", methods=["GET", "POST"])
async def refine(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    return krypton_call(player_id, lambda state, now: kryptons.refine(
        state, _int(params, "times", 1), params.get("auto_d") == "1", now))


@router.api_route("/alchemy/move", methods=["GET", "POST"])
async def refinery_collect(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    return krypton_call(player_id, lambda state, now: kryptons.collect(
        state, _int(params, "buff"), _int(params, "level"), _int(params, "star")))


@router.api_route("/alchemy/destroy_crypton", methods=["GET", "POST"])
async def refinery_break_down(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    key = (_int(params, "buff"), _int(params, "level"), _int(params, "star")) if "buff" in params else None
    return krypton_call(player_id, lambda state, now: kryptons.break_down_tray(state, key))


@router.api_route("/kryptons", methods=["GET", "POST"])
def krypton_storage(player_id: int = Depends(current_player)):
    return krypton_call(player_id, lambda state, now: kryptons.storage_view(state))


@router.post("/kryptons/unlock_package")
async def krypton_unlock_storage(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    confirm = params.get("show_info") != "1"
    return krypton_call(player_id, lambda state, now: kryptons.unlock_storage(state, _int(params, "count", 1), confirm),
                        with_empire=confirm)


@router.post("/kryptons/{planet_id}/unlock_planet_hole")
async def krypton_unlock_holes(world_id: int, planet_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    confirm = params.get("show_info") != "1"
    return krypton_call(player_id, lambda state, now: kryptons.unlock_holes(
        state, planet_id, _int(params, "unlock_index", 1), confirm), with_empire=confirm)


@router.post("/kryptons/{item_id}/inset_into_planet")
async def krypton_set(world_id: int, item_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)

    def act(state, now):
        kryptons.set_into_planet(state, item_id, _int(params, "planet_id"))
        return {}
    return krypton_call(player_id, act, with_empire=True)


@router.post("/kryptons/{item_id}/outset_from_planet")
def krypton_take(item_id: int, player_id: int = Depends(current_player)):
    def act(state, now):
        kryptons.take_from_planet(state, item_id)
        return {"price": -1}  # taking a krypton out is free and done at once
    return krypton_call(player_id, act, with_empire=True)


@router.post("/kryptons/{item_id}/upgrade")
def krypton_upgrade(item_id: int, player_id: int = Depends(current_player)):
    return krypton_call(player_id, lambda state, now: {"energy_for_update": kryptons.upgrade(state, item_id)},
                        with_empire=True)


@router.post("/kryptons/{item_id}/conversion")
async def krypton_break_down(world_id: int, item_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)

    def act(state, now):
        if params.get("show_info") == "1":
            return {"energy_info": kryptons.conversion_energy(kryptons._item(kryptons.account(state), item_id))}
        return {"energy": kryptons.break_down_item(state, item_id)}
    return krypton_call(player_id, act)


@router.get("/planets/{planet_id}/kryptons")
def planet_kryptons(planet_id: int, player_id: int = Depends(current_player)):
    def act(state, now):
        planet = planet_rules.find_planet(state["empire"], planet_id)
        if planet is None:
            raise planet_rules.GameError("This is not your planet")
        return {"kryptons": planet["kryptons"], "hole_count": kryptons.holes(kryptons.account(state), planet_id)}
    return krypton_call(player_id, act)


@router.get("/krypton_researches")
def krypton_research(player_id: int = Depends(current_player)):
    return krypton_call(player_id, lambda state, now: kryptons.research_view(state, now))


@router.post("/krypton_researches/upgrade")
def krypton_research_start(player_id: int = Depends(current_player)):
    return krypton_call(player_id, lambda state, now: kryptons.start_research(state, now))


@router.post("/krypton_researches/speedup")
def krypton_research_speedup(player_id: int = Depends(current_player)):
    return error("Speeding up is not available on Supernova")


@router.api_route("/crypton_sale", methods=["GET", "POST"])
def krypton_shop(player_id: int = Depends(current_player)):
    return krypton_call(player_id, lambda state, now: kryptons.shop_view(state, now))


@router.api_route("/crypton_sale/price", methods=["GET", "POST"])
async def krypton_shop_price(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    return krypton_call(player_id, lambda state, now: {
        "message": kryptons.price_message(params.get("type", ""), _int(params, "times", 1))})


@router.api_route("/crypton_sale/exchange", methods=["GET", "POST"])
async def krypton_shop_buy(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    return krypton_call(player_id, lambda state, now: kryptons.buy(
        state, params.get("type", ""), _int(params, "times", 1), now))


# --- Pioneer Lab and Iron Behemoth -----------------------------------------------------------


def flagship_call(player_id: int, planet_id, act) -> Response:
    """act(state, planet, now) -> dict, on the given (or current) planet."""
    now = time.time()
    try:
        with db.player_state(player_id) as state:
            empire = state["empire"]
            planet_rules.tick(empire, now)
            planet = planet_rules.find_planet(empire, int(planet_id)) if str(planet_id or "").isdigit() else None
            return encrypted_json(act(state, planet or empire["main_planet"], now))
    except planet_rules.GameError as e:
        return error(str(e))


def _next_event_id(state):
    return lambda: planet_rules._next_event_id(state)


@router.post("/flagships/show_info.json")
async def flagship_info(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    return flagship_call(player_id, params.get("planet_id"), lambda state, planet, now: flagships.show_info(state))


@router.post("/flagships/building_info.json")
async def flagship_building_info(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    return flagship_call(player_id, params.get("planet_id"),
                         lambda state, planet, now: flagships.building_info(state, now))


@router.post("/flagships/buy_flagship_box")
def flagship_box(player_id: int = Depends(current_player)):
    return flagship_call(player_id, None, lambda state, planet, now: flagships.buy_box(state, now))


@router.post("/flagships.json")
async def flagship_build(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    return flagship_call(player_id, params.get("planet_id"), lambda state, planet, now: flagships.build(
        state, planet, int(params.get("fleet_count") or 0), params.get("sure") == "1", _next_event_id(state), now))


@router.get("/flagship_researches")
def flagship_researches(player_id: int = Depends(current_player)):
    return flagship_call(player_id, None, lambda state, planet, now: {
        "flagship_research": flagships.research_view(state, now)})


@router.post("/flagship_researches/upgrade.json")
async def flagship_research_start(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    return flagship_call(player_id, params.get("planet_id"), lambda state, planet, now: flagships.start_research(
        state, planet, int(params.get("research_id") or 0), _next_event_id(state), now))


@router.post("/flagship_researches/speedup.json")
def flagship_research_speedup(player_id: int = Depends(current_player)):
    return error("Speeding up is not available on Supernova")


# --- Electronic counter measures and system lords ------------------------------------------


@router.get("/electronic_warfare/info.json")
def ecm_info(player_id: int = Depends(current_player)):
    with db.player_state(player_id) as state:
        return encrypted_json(ecm.view(state))


@router.post("/electronic_warfare/level_up")
async def ecm_level_up(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    now = time.time()
    try:
        with db.player_state(player_id) as state:
            result = ecm.level_up(state, params.get("auto_buy") == "1")
            return encrypted_json(dict(result, empire=player.empire_summary(state),
                                       planet=planet_rules.client_view(state["empire"], now)))
    except ValueError as e:
        return error(str(e))


@router.get("/systems/lord_config")
@router.post("/systems/lord_config")
def lord_config(player_id: int = Depends(current_player)):
    return encrypted_json({"lord_configs": lordship.owned_systems(player_id), "lord_des": (
        "The strongest player in a solar system is its lord. The rank grows with the lord's points and "
        "strengthens the lord's planets in that system.")})


# --- Ship repair ---------------------------------------------------------------------------


def repair_call(player_id: int, planet_id, act) -> Response:
    """act(planet, state, now) -> dict; the answer gets the client's "command" block."""
    now = time.time()
    try:
        with db.player_state(player_id) as state:
            empire = state["empire"]
            if planet_id and str(planet_id).isdigit():
                planet_rules.switch_planet(empire, int(planet_id))
            planet_rules.tick(empire, now)
            planet = empire["main_planet"]
            result = act(planet, state, now)
            view = planet_rules.client_view(empire, now)
            command = {"update": {"fleets": [planet_rules.fleet_view(planet, f) for f in planet["fleets"]]},
                       "planet": view, "empire": player.empire_summary(state)}
            return encrypted_json(dict(result, command=command, planet=view,
                                       damaged_fleets=repairs.view(planet, now)["fleets_info"]))
    except planet_rules.GameError as e:
        return error(str(e))


@router.post("/repair_events/repair_right_now")
@router.post("/repair_events/repair_speed_up")
def repair_instant(player_id: int = Depends(current_player)):
    return error("Instant repair is not available on Supernova")


@router.post("/repair_events/handing_over")
async def repair_hand_over(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)

    def act(planet, state, now):
        ships = repairs.hand_over(planet, now)
        return {"repair_fleets": [{"fleet_type": t, "fleet_count": n} for t, n in ships.items()]}
    return repair_call(player_id, params.get("planet_id"), act)


@router.post("/repair_events/resolve")
async def repair_dismantle_ask(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    ships = {int(params.get("fleet_type") or 0): int(params.get("fleet_count") or 0)}
    if not all(t in eco.UNITS for t in ships):
        return error("Unknown ship")
    return encrypted_json({"resolve_fleets": [{"fleet_type": t, "fleet_count": n} for t, n in ships.items()],
                           "resolve_value": repairs.dismantle_value(ships)})


@router.post("/repair_events/make_sure_for_resolve")
async def repair_dismantle(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    return repair_call(player_id, params.get("planet_id"), lambda planet, state, now: {
        "resolve_value": repairs.dismantle(planet, params.get("fleet_type", ""), params.get("fleet_count", ""))})


@router.post("/repair_events/change_repair_switch")
async def repair_switch(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    return repair_call(player_id, params.get("planet_id"), lambda planet, state, now: {
        "sw_repair": repairs.toggle(planet, params.get("switch") == "1")})


@router.post("/repair_events/")
@router.post("/repair_events")
async def repair_start(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    return repair_call(player_id, params.get("planet_id"), lambda planet, state, now: {
        "finish_time": repairs.start(planet, params.get("fleet_type", ""), params.get("fleet_count", ""), now)})


@router.get("/repair_events/{planet_id}")
def repair_view(planet_id: int, player_id: int = Depends(current_player)):
    now = time.time()
    with db.player_state(player_id) as state:
        planet = planet_rules.find_planet(state["empire"], planet_id)
        if planet is None:
            return error("This is not your planet", 404)
        planet_rules.tick(state["empire"], now)
        return encrypted_json(repairs.view(planet, now))


# --- Chat ------------------------------------------------------------------------------------


@router.get("/chat_messages.json")
async def chat_messages(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    try:
        found = chat.messages(player_id, int(params.get("alliance_id") or 0), int(params.get("count") or 50))
    except chat.ChatError as e:
        return error(str(e))
    return encrypted_json({"chat_messages": found})


@router.post("/chat_messages.json")
async def chat_send(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    try:
        chat.send(player_id, params.get("words", ""), int(params.get("alliance_id") or 0))
    except chat.ChatError as e:
        return error(str(e))
    return encrypted_json({})


@router.post("/reports")
async def chat_report(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    chat.report(player_id, int(params.get("message_id") or 0), params.get("additiona_notes", ""))
    return encrypted_json({})


# --- Tutorial quests ---------------------------------------------------------------------------


@router.get("/quest_events")
def quest_events(player_id: int = Depends(current_player)):
    now = time.time()
    with db.player_state(player_id) as state:
        result = quests.current(state, player_id, now)
        result["empire"] = player.empire_summary(state)
        result["planet"] = planet_rules.client_view(state["empire"], now)
    return encrypted_json(result)


@router.post("/quest_event/skip_all_newbie_guide")
def quest_skip_all(player_id: int = Depends(current_player)):
    now = time.time()
    with db.player_state(player_id) as state:
        message = quests.skip_all(state)
        planet_rules.tick(state["empire"], now)
        return encrypted_json({"update": {"rewards_all": message}, "empire": player.empire_summary(state),
                               "planet": planet_rules.client_view(state["empire"], now)})


# --- Rankings ---------------------------------------------------------------------------------


@router.get("/ranks")
def ranks(player_id: int = Depends(current_player), do: str = "top_users"):
    return encrypted_json({"rank": ranking.ranking(do, player_id)})


# --- Mailbox ---------------------------------------------------------------------------------


@router.post("/messages/get_new.json")
async def new_mails(world_id: int, request: Request, player_id: int = Depends(current_player)):
    """Called twice: first to fetch new mails, then with their ids in `checklist` to confirm."""
    params = await form(request)
    mails.confirm(player_id, [int(x) for x in params.get("checklist", "").split(",") if x.strip().isdigit()])
    new = mails.undelivered(player_id)
    return encrypted_json({"new_message_count": len(new), "mails": new})


@router.post("/messages.json")
async def send_mail(world_id: int, request: Request, player_id: int = Depends(current_player)):
    """A mail to another player (message_type 3). Text fields are encrypted like the username."""
    params = await form(request)
    if params.get("message_type") == str(mails.ALLIANCE):
        try:
            alliances.alliance_mail(player_id, decrypt_param(params.get("subject", ""))[:100],
                                    decrypt_param(params.get("body", ""))[:5000])
        except alliances.AllianceError as e:
            return error(str(e))
        return encrypted_json({})
    if params.get("message_type") != str(mails.USER):
        return error("Unsupported message")
    to = decrypt_param(params.get("to", "")).strip()
    found = db.find_player(to)
    if found is None:
        return error("There is no player with this name")
    sender = db.load_state(player_id)["username"]
    mails.send(found[0], mails.USER, sender, to, decrypt_param(params.get("subject", ""))[:100],
               decrypt_param(params.get("body", ""))[:5000])
    return encrypted_json({})


# --- Galaxy map and solar systems ------------------------------------------------------


@router.get("/systems/{sid}/locate")
def locate_system(sid: int, player_id: int = Depends(current_player)):
    """Map tile of a system, used to center the galaxy map."""
    _, system = universe.system_from_id(sid)
    x, y = universe.map_coordinates(system)
    return encrypted_json({"system_coordinate_x": x, "system_coordinate_y": y})


@router.get("/systems/{system_position}/{galaxy_position}")
def system_by_position(system_position: int, galaxy_position: int, player_id: int = Depends(current_player)):
    """Note the order in the URL: system first, then galaxy."""
    if not universe.valid_system(galaxy_position, system_position):
        return error("This system does not exist", 404)
    return encrypted_json(universe.system_view(galaxy_position, system_position, player_id))


@router.get("/systems/{sid}")
def system_by_id(sid: int, player_id: int = Depends(current_player)):
    galaxy, system = universe.system_from_id(sid)
    if not universe.valid_system(galaxy, system):
        return error("This system does not exist", 404)
    return encrypted_json(universe.system_view(galaxy, system, player_id))


@router.get("/galaxy_map.json")
def galaxy_map(galaxy_position: int, x: int, y: int, player_id: int = Depends(current_player)):
    if not 1 <= galaxy_position <= universe.GALAXIES:
        return error("This galaxy does not exist", 404)
    return encrypted_json(universe.galaxy_map(galaxy_position, x, y))


# --- Bookmarks, planet info, news, daily check-in and gifts ---------------------------------


@router.post("/bookmarks.json")
async def bookmark_add(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    try:
        with db.player_state(player_id) as state:
            mark = bookmarks.add(state, int(params.get("planet_id") or 0), int(params.get("bookmark_type") or 1))
    except planet_rules.GameError as e:
        return error(str(e))
    return encrypted_json({"bookmark": mark})


@router.post("/bookmarks/list.json")
def bookmark_list(player_id: int = Depends(current_player)):
    with db.player_state(player_id) as state:
        return encrypted_json({"bookmarks": bookmarks.listing(state)})


@router.post("/bookmarks/{bookmark_id}")
def bookmark_delete(bookmark_id: int, player_id: int = Depends(current_player)):
    with db.player_state(player_id) as state:
        bookmarks.delete(state, bookmark_id)
    return encrypted_json({"id": bookmark_id})


@router.get("/notices")
@router.get("/notices/lastest.json")
def news(player_id: int = Depends(current_player)):
    return encrypted_json({"list": notices.listing()})


@router.get("/notices/popup.json")
def popup_notice(player_id: int = Depends(current_player)):
    return encrypted_json({"popup": {}})


@router.get("/notices/{notice_id}.json")
@router.post("/notices/{notice_id}.json")
def news_details(notice_id: int, player_id: int = Depends(current_player)):
    text = notices.content(notice_id)
    if text is None:
        return error("This news is gone", 404)
    return encrypted_json({"content": text})


@router.get("/faqs/game_faq")
@router.post("/faqs/game_faq")
def game_faq(player_id: int = Depends(current_player)):
    return encrypted_json(faq.FAQ)


@router.get("/system_gifts/{gift_id}")
def gift_details(gift_id: int, player_id: int = Depends(current_player)):
    found = gifts.details(player_id, gift_id)
    if found is None:
        return error("This gift is gone", 404)
    return encrypted_json(found)


@router.post("/system_gifts/get_gift")
async def gift_claim(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    now = time.time()
    with db.player_state(player_id) as state:
        empire = state["empire"]
        planet_id = params.get("planet_id")
        if planet_id and planet_id.isdigit() and planet_rules.find_planet(empire, int(planet_id)):
            planet_rules.switch_planet(empire, int(planet_id))
        planet_rules.tick(empire, now)
        if not gifts.claim(player_id, empire["main_planet"], int(params.get("gift_id") or 0), now):
            return error("This gift is gone")
        return encrypted_json({"empire": player.empire_summary(state), "planet": planet_rules.client_view(empire, now)})


# --- Item shop, depot and speed-ups ------------------------------------------------------------


def item_call(fn):
    try:
        return encrypted_json(fn())
    except planet_rules.GameError as e:
        return error(str(e))


def _money(state: dict) -> dict:
    return {"dark_matter_count": state["empire"].get("dark_matter_count", 0),
            "gu_count": state["empire"].get("gu_count", 0)}


@router.get("/game_items")
async def item_shop(world_id: int, request: Request, player_id: int = Depends(current_player)):
    category = _int(await form(request), "category")
    with db.player_state(player_id) as state:
        return encrypted_json(dict(_money(state), items=items.shop(category)))


@router.get("/game_items/item_packages")
def item_packages(player_id: int = Depends(current_player)):
    return encrypted_json({"item_packages": []})


@router.post("/users/{user_id}/buy_game_item")
async def buy_item(world_id: int, user_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)

    def act():
        with db.player_state(player_id) as state:
            return items.buy(state, _int(params, "game_item_id"), _int(params, "number", 1))
    return item_call(act)


@router.get("/users/{user_id}/game_items")
async def item_depot(world_id: int, user_id: int, request: Request, player_id: int = Depends(current_player)):
    category = _int(await form(request), "category")
    with db.player_state(player_id) as state:
        return encrypted_json(dict(_money(state), items=items.depot_view(state, category)))


@router.post("/users/{user_id}/use_game_item")
async def use_item(world_id: int, user_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    now = time.time()

    def act():
        with db.player_state(player_id) as state:
            use_planet(state, params)
            empire = state["empire"]
            planet_rules.tick(empire, now)
            item_id = _int(params, "game_item_id")
            info = items.use(state, item_id, _int(params, "number", 1), empire["main_planet"])
            return {"info": info, "categories": [items.owned_view(state, items.item(item_id))],
                    "empire": player.empire_summary(state), "planet": planet_rules.client_view(empire, now)}
    return item_call(act)


@router.post("/users/{user_id}/del_game_item")
async def discard_item(world_id: int, user_id: int, request: Request, player_id: int = Depends(current_player)):
    item_id = _int(await form(request), "game_item_id")

    def act():
        with db.player_state(player_id) as state:
            items.discard(state, item_id)
            return {"item": {"game_item_id": item_id}}
    return item_call(act)


SPEEDUP_KINDS = {"building_events": items.BUILDING, "fleet_building_events": items.SHIPYARD,
                 "research_upgrading_events": items.RESEARCH}


def _speedup_kind(request: Request) -> str:
    return SPEEDUP_KINDS[request.url.path.split("/")[3]]  # /world/{id}/{kind}/...


@router.get("/building_events/{event_id}/speedup_info.json")
@router.get("/fleet_building_events/{event_id}/speedup_info.json")
@router.get("/research_upgrading_events/{event_id}/speedup_info.json")
def speedup_info(event_id: int, request: Request, player_id: int = Depends(current_player)):
    kind = _speedup_kind(request)
    with db.player_state(player_id) as state:
        return item_call(lambda: items.speedup_info(state, kind, event_id, time.time()))


@router.post("/building_events/{event_id}/speedup.json")
@router.post("/fleet_building_events/{event_id}/speedup.json")
@router.post("/research_upgrading_events/{event_id}/speedup.json")
@router.post("/building_events/{event_id}/buy_and_use_stone.json")
@router.post("/fleet_building_events/{event_id}/buy_and_use_stone.json")
@router.post("/research_upgrading_events/{event_id}/buy_and_use_stone.json")
async def speed_up(world_id: int, event_id: int, request: Request, player_id: int = Depends(current_player)):
    """speedup.json uses a speed-up item (game_item_id 0: dark matter), buy_and_use_stone.json
    buys one first."""
    kind = _speedup_kind(request)
    buy_first = request.url.path.endswith("buy_and_use_stone.json")
    params = await form(request)
    now = time.time()

    def act():
        with db.player_state(player_id) as state:
            empire = state["empire"]
            items.speed_up(state, kind, event_id, _int(params, "game_item_id"), buy_first, now)
            planet = empire["main_planet"]
            return {"update": {"buildings": [planet_rules.building_view(planet, b) for b in planet["buildings"]],
                               "fleets": [planet_rules.fleet_view(planet, f) for f in planet["fleets"]],
                               "researches": [planet_rules.research_view(empire, r) for r in empire["researches"]]},
                    "empire": player.empire_summary(state), "planet": planet_rules.client_view(empire, now)}
    return item_call(act)


# --- Star fortresses ------------------------------------------------------------------------


@router.get("/challenge_events/{planet_id}")
def galaxy_challenge_events(planet_id: int, player_id: int = Depends(current_player)):
    """The star gate: without a running galaxy challenge the client shows the star fortresses."""
    return encrypted_json({"switch_status": False})


@router.get("/challenge_event/switch")
def galaxy_challenge_switch(player_id: int = Depends(current_player)):
    return encrypted_json({"status": False})


@router.get("/fortresses")
def fortress_list(player_id: int = Depends(current_player)):
    return encrypted_json({"info": fortresses.listing(player_id, time.time())})


@router.api_route("/fortresses/interspace_fleet", methods=["GET", "POST"])
async def interspace_fleet(world_id: int, request: Request, player_id: int = Depends(current_player)):
    interspace_id = (await form(request)).get("interspace_id", "")
    try:
        return encrypted_json({"fleets": fortresses.interspace_fleet(int(interspace_id) if interspace_id.isdigit()
                                                                     else 0)})
    except planet_rules.GameError as e:
        return error(str(e))


def fortress_call(fn):
    try:
        return encrypted_json(fn())
    except planet_rules.GameError as e:
        return error(str(e))


@router.get("/fortresses/{fortress_id}")
def fortress_info(fortress_id: int, player_id: int = Depends(current_player)):
    return fortress_call(lambda: fortresses.info(fortress_id, player_id, time.time()))


@router.post("/fortresses/{fortress_id}/alliance_list")
def fortress_alliances(fortress_id: int, player_id: int = Depends(current_player)):
    return fortress_call(lambda: {"alliance_list": fortresses.alliance_list(fortress_id, time.time())})


@router.post("/fortresses/{fortress_id}/declare_war")
def fortress_declare_war(fortress_id: int, player_id: int = Depends(current_player)):
    def act():
        now = time.time()
        message = fortresses.declare_war(player_id, fortress_id, now)
        return {"info": message, "fortress": fortresses.info(fortress_id, player_id, now)}
    return fortress_call(act)


@router.post("/fortresses/{fortress_id}/stroe")
def fortress_store(fortress_id: int, player_id: int = Depends(current_player)):
    def act():
        items = fortresses.store(fortress_id)
        with db.player_state(player_id) as state:
            empire = state["empire"]
            return {"items": items, "dark_matter_count": empire.get("dark_matter_count", 0),
                    "gu_count": empire.get("gu_count", 0)}
    return fortress_call(act)


@router.post("/fortresses/{fortress_id}/buy")
async def fortress_buy(world_id: int, fortress_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    item, count = params.get("game_item_id", ""), params.get("count", "1")
    return fortress_call(lambda: fortresses.buy(player_id, fortress_id, int(item) if item.isdigit() else 0,
                                                int(count) if count.isdigit() else 0))


@router.post("/alliances/alli_stroe")
def alliance_store(player_id: int = Depends(current_player)):
    return encrypted_json({"items": fortresses.alliance_store()})


@router.post("/alliances/{fortress_id}/alli_stroe_buy")
async def alliance_store_buy(world_id: int, fortress_id: int, request: Request,
                             player_id: int = Depends(current_player)):
    item = (await form(request)).get("item", "")
    return fortress_call(lambda: {"info": fortresses.use_store_item(player_id, fortress_id,
                                                                    int(item) if item.isdigit() else 0, time.time())})


@router.api_route("/fortress_hanging_events/prepare_hanging", methods=["GET", "POST"])
async def fortress_prepare(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    try:
        with db.player_state(player_id) as state:
            origin = fleets.origin_planet(state["empire"], params)
            ships = fleets.parse_ships(origin, params.get("fleet_ids", ""), params.get("fleet_cnt_ids", ""))
            return encrypted_json(fortresses.plan(state["empire"], ships))
    except planet_rules.GameError as e:
        return error(str(e))


@router.post("/fortress_hanging_events")
async def fortress_send(world_id: int, request: Request, player_id: int = Depends(current_player)):
    params = await form(request)
    now = time.time()
    try:
        with db.player_state(player_id) as state:
            planet_rules.tick(state["empire"], now)
            origin = fleets.origin_planet(state["empire"], params)
            ships = fleets.parse_ships(origin, params.get("fleet_ids", ""), params.get("fleet_cnt_ids", ""))
            kind, target = params.get("hanging_type", ""), params.get("end_id", "")
            fortresses.send(player_id, state, origin, ships, int(kind) if kind.isdigit() else 0,
                            int(target) if target.isdigit() else 0, now)
            return fleet_response(state, now)
    except planet_rules.GameError as e:
        return error(str(e))


@router.post("/fortress_hanging_events/{event_id}/cancel.json")
def fortress_recall(world_id: int, event_id: int, player_id: int = Depends(current_player)):
    now = time.time()
    try:
        fortresses.recall(player_id, event_id, now)
    except planet_rules.GameError as e:
        return error(str(e))
    with db.player_state(player_id) as state:
        planet_rules.tick(state["empire"], now)
        return fleet_response(state, now)


# --- Features we do not implement yet, answered with their "nothing here" values ---------


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
