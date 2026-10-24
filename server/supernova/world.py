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

from . import accounts, alliances, crypto, db, fleets, mails, player, quests, ranking
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
        planet_rules.tick(state["empire"], now)
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
    """_method=delete gives up a colony. The home planet and planets with fleets on the way stay."""
    if (await form(request)).get("_method") != "delete":
        return error("Unsupported request")
    now = time.time()
    with db.player_state(player_id) as state:
        empire = state["empire"]
        if planet_rules.find_planet(empire, planet_id) is None:
            return error("This is not your planet", 404)
        if empire["planets"][0]["planet_id"] == planet_id:
            return error("You cannot give up your home planet")
        if fleets.planet_has_flights(planet_id):
            return error("Fleets are flying from or to this planet")
        empire["planets"] = [p for p in empire["planets"] if p["planet_id"] != planet_id]
        universe.remove_planet(planet_id)
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
            ships = fleets.parse_ships(empire["main_planet"], params.get("fleet_ids", ""),
                                       params.get("fleet_cnt_ids", ""))
            target = fleets.parse_target(params.get("end_p_pos", ""))
            info = fleets.plan(empire, player_id, ships, target)
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


@router.post("/alliances/{alliance_id}/bag_log")
def alliance_log(alliance_id: int, player_id: int = Depends(current_player)):
    return encrypted_json({"log": []})  # the alliance treasury is not implemented


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


@router.get("/acses")
def alliance_joint_attacks(player_id: int = Depends(current_player)):
    return encrypted_json({"info": [], "planet": {}, "empire": {}})  # joint attacks: not implemented


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
    return encrypted_json(universe.system_view(galaxy_position, system_position))


@router.get("/systems/{sid}")
def system_by_id(sid: int, player_id: int = Depends(current_player)):
    galaxy, system = universe.system_from_id(sid)
    if not universe.valid_system(galaxy, system):
        return error("This system does not exist", 404)
    return encrypted_json(universe.system_view(galaxy, system))


@router.get("/galaxy_map.json")
def galaxy_map(galaxy_position: int, x: int, y: int, player_id: int = Depends(current_player)):
    if not 1 <= galaxy_position <= universe.GALAXIES:
        return error("This galaxy does not exist", 404)
    return encrypted_json(universe.galaxy_map(galaxy_position, x, y))


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
