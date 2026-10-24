# Network protocol

How the client talks to the server. Based on client 1.9.36: decompiled Lua scripts
(`AutoUpdateInit.lua`, `GameUtils.lua`), the Java HTTP client (`ExtHttpConnection`,
`SecurityUtils`) and logs from BlueStacks 5 (Android 7.1.1).

**Everything is HTTP(S) + JSON.** The engine also ships a raw TCP socket module, but the game
does not use it (`OnConnectToServer` is empty). The API looks like a Rails app:
`users/<id>`, `planets/<id>`, `building_events.json`, `passive_events`, ...

## Startup sequence

1. On first start the client extracts `assets/tap4fun.zip` to
   `/storage/emulated/0/tap4fun/galaxy_empire/AppOriginalData/Android/`.
2. The engine runs `AutoUpdateInit.lua`, which loads the local file lists (`ofl.tfl`,
   `lfl.tfl`) and sets the gateway pool:
   `b.portal-platform.tap4fun.com/ge/url_get/online/world`
   (`b.portal-platform.t4f.cn/...` for Chinese clients).
3. `GET https://<gateway>/servers/auto_update` (plain JSON, see below).
   The gateway URL `https://<gateway>/` is remembered as `AutoUpdate.gameGateway`.
4. `GET <url>/ver.txt` where `url` comes from the `auto_update` response.
5. If the server's resource version is newer: download `<url>/fl.tfl` (or `fl_64.tfl`) and every
   changed file listed in it, verify CRC32, save `lfl.tfl`.
6. Otherwise, or when done: run `GameEngineUpdate.tfl` and continue to the login screen.

### `GET <gateway>/servers/auto_update`

Request mode `notencrypt`. Response: JSON (`Content-Type: application/json`).

| Field         | Required | Meaning |
|---------------|----------|---------|
| `url`         | yes      | base URL of the update file server (no trailing slash) |
| `https_url`   | no       | used instead of `url` when present |
| `lowestv`     | yes      | minimum client version, e.g. `"1.9.36"`; older clients are told to update |
| `appStoreUrl` | no       | store link shown when the client is too old |
| `error`       | no       | non-empty → shown to the player as an alert, startup stops |
| `ignoreDeveloper` | no   | only relevant for developer builds |

A status code of `298` with an `error` field shows the error (maintenance message).

### `GET <url>/ver.txt`

Plain text, four lines:

```
tag
<resource version>      e.g. 6507, compared with the client's local version
<app version>           e.g. 1.9.36
<debug version>         any non-space string
```

If `<resource version>` is not newer than the client's own (`resv` header), no files are
downloaded.

### `GET <gateway>/servers/track_quest`

Progress tracking: `udid`, `event` (e.g. `010_LaunchApplication`, `020_AutoUpdateSuccess`,
`030_GameLoadingFinished`, `033_GameLoginFromStartButton`), optional `server_url`.
Built as `gateway .. "/servers/..."`, so the path arrives as `//servers/track_quest`.
Any 200 response is fine.

### `GET <gateway>/servers.json`

The world (server) list shown on the title screen. Mode `notencrypt`.

```json
{"server_list": [{
  "server_id": 1, "server_name": "Supernova",
  "server_root_url": "http://host:8000/world/1/",
  "server_root_https_url": "optional, preferred over server_root_url",
  "language": "all", "busy": 0, "is_local": false,
  "status": "new", "is_recommended": true,
  "merge_server_id": 2, "merge_server_name": "optional, for merged worlds"
}]}
```

The chosen entry's `server_root_url` becomes the base URL for all game requests.

## Login

Guest login: `POST <world>/trials.json` with body `username=<name>`.

- A new guest sends an empty `username`. The server creates an account and returns its state,
  including the generated `username`; the client stores it in `ACCOUNT.tfl`.
- A returning guest sends its stored name encrypted with `ext.getENC2` (see below).
- Login with password: `POST <world>/sessions.json` with `username` (encrypted) and `password`.

The response goes to `GameUISplash.LoginSuccCallback` → `GameGlobalData:UpdateGameData(content)`
and becomes the client's global state. Then the client switches to the planet view.

Top-level fields: `user_id`, `empire_id`, `username`, `salt` (used in the `sig` header),
`account_level`, `vip_info` (`vip_left`), `grows`, `purchase`, `events`,
`athletics_server_config`, `checkin_days`, and `empire`:

| `empire` field | Meaning |
|----------------|---------|
| `main_planet` | the planet shown, see below |
| `planets_info` | list of `{planet_id, planet_name, planet_type, galaxy_position, system_position, planet_position}` |
| `researches` | list of `{research_id, research_type, research_name, research_level}` |
| `research_upgrading_events`, `fleet_hanging_events`, `fortress_interspace_events` | running timers |
| `alliance_id`, `alliance_name`, `alliance_application_id`, `alliance_membership_id` | 0 / "" without alliance |
| `dark_matter_count`, `gu_count` | premium currencies |
| `score`, `rank`, `new_message_count`, `warning_count`, `acs_attack_count`, `safe_explore_times` | counters |
| `quests_complete`, `quests_newbie_guide`, `quests_new_ver`, `gift_id`, `buff_*` | tutorial and buffs |

| `main_planet` field | Meaning |
|---------------------|---------|
| `planet_id`, `planet_name`, `planet_type` (2 = capital), `galaxy_position`, `system_position`, `planet_position`, `system_id` | identity and coordinates |
| `metal`, `crystal`, `deuterium`, `*_max`, `energy_max`, `energy_used`, `*_mine_percent` | resources |
| `buildings` | list of `{building_id, building_type, building_name, building_level, building_position_index}`; must contain every building type, unbuilt ones with level 0; lots 1-15, the mines use 1-3 |
| `fleets` | list of `{fleet_id, fleet_type, fleet_name, amount, amount_limit}` |
| `building_events`, `fleet_building_events`, `transmit_recharge_event` | running timers |
| `current_server_time` | Unix time in seconds, the client syncs its clock with it |

Names (`building_name`, ...) come from the server, so they have to be localized there
(`Locale` header).

### Sessions

The client keeps cookies per host (`MyCookieManager`), so the server sets a session cookie at
login. The cookie parser is simplistic: attributes after the value keep a leading space, so the
cookie is sent without attributes.

### Encrypted parameters (`ext.getENC2`)

Some parameters (the username, the `requestindex` header) are encrypted with the same key as
responses: `URL-encode(Base64(AES-256-CBC(key, zero IV, value)))`. The Lua code passes a longer
string as key; the Java side truncates it to the same 32 bytes.

### After login

| Request | Response |
|---------|----------|
| `GET passive_events?heartBeatRate=6` | heartbeat, every `heartBeatRate` seconds: `{heartBeatRate, empire, planet, refresh}`. `empire`/`planet` = 1 makes the client reload `users/<id>?do=empire` / `planets/<id>`. If it fails, the client stops polling. |
| `GET planets/<id>` | the `main_planet` object |
| `GET users/<id>?do=empire` | the `empire` object |
| `GET notices/popup.json` | `{"popup": {}}` (no popup) |
| `GET notices/lastest.json` | `{"list": [{id, notice_type: "activity" or other, ...}]}` |
| `GET users/first_charge_misc` | `{"first_charge": false}` (no first-purchase offer) |
| `GET gc/current` | `{"status": 0}` (no galaxy challenge running) |
| `GET promotions/my_newbie_invite_info` | `{"my_code": "..."}` |

### Buildings

| Request | Meaning |
|---------|---------|
| `POST building_events.json` with `planet_id`, `building_id`, `event_type` (1 = upgrade, 2 = tear down), `building_index` (lot, for a new building) | start a construction |
| `POST building_events/<event_id>.json` with `_method=delete` | cancel it |
| `GET planets/<planet_id>/build_info.json?bid=<building_id>` | sent when the client's timer for that building runs out |
| `POST building_events/<event_id>/sprint` | finish instantly for premium currency (not implemented) |

All of them answer `{"update": {"buildings": [changed building]}, "empire": {...}, "planet": {...}}`;
the client merges `empire` and `planet` into its state.

Extra building fields the UI reads: `up_metal_require`, `up_crystal_require`,
`up_deuterium_require`, `up_time`, `up_extra_energy` (additional energy of the next level),
`down_*_require`, `down_time` and `tech_tree` =
`{"buildings_tree": [{unit_type, unit_level}], "researches_tree": [...]}`.

A building event: `{event_id, building_id, building_type, event_type, start_time, finish_time,
total_time, finished: false, building_name, building_level, building_position_index}`. The client
counts down from `finish_time` (server time) on its own.

Extra planet fields: `metal_perhour`, `crystal_perhour`, `deuterium_perhour`,
`solar_plant_percent`, `solar_plant_energy`, `fusion_reactor_percent`, `fusion_energy`.

The client never counts resources up by itself; it only shows what the server sends. The
heartbeat therefore asks it to reload the planet every time.

### Research

| Request | Meaning |
|---------|---------|
| `POST research_upgrading_events.json` with `planet_id`, `research_id` | start research |
| `POST research_upgrading_events/<event_id>` with `_method=delete`, `current_planet_id` | cancel it |
| `GET users/research_info.json?research_id=..&planet_id=..` | sent when the client's research timer runs out |

All answer `{"update": {"researches": [changed research]}, "empire": {...}, "planet": {...}}`.

Research fields: `research_id`, `research_type`, `research_name`, `research_level`,
`up_metal_require`, `up_crystal_require`, `up_deuterium_require`, `up_time`, `tech_tree`.
Research events live in `empire.research_upgrading_events`:
`{event_id, research_id, research_type, research_name, research_level, start_time, finish_time,
total_time, finished: false}`.

The lab screen groups researches into three tabs by type
(`GameResearchDefine:getResearchIndexByType`); unknown types land in the first tab, so the
server only lists lab researches.

### Shipyard

| Request | Meaning |
|---------|---------|
| `POST fleet_building_events.json` with `planet_id`, `fleet_id`, `fleet_count` | order units |
| `POST fleet_building_events/<event_id>/cancel.json` with `planet_id` | cancel an order |
| `GET planets/<planet_id>/fleets_build_info?fleet_id=..&event_id=..` | sent when an order's timer runs out |

All answer `{"update": {"fleets": [changed unit type]}, "empire": {...}, "planet": {...}}`.

Unit fields (`main_planet.fleets`): `fleet_id`, `fleet_type`, `fleet_name`, `amount`,
`amount_limit`, `metal_require`, `crystal_require`, `deuterium_require` (per unit), `up_time`
(seconds per unit), `tech_tree`, optionally `items` (e.g. `[["guidance_chip", n]]`).
Orders live in `main_planet.fleet_building_events`:
`{event_id, fleet_id, fleet_type, fleet_name, building_count (units left), start_time,
finish_time, total_time, finished: false}`.

The shipyard screen has three tabs (`GameFleetDefine:getFleetIndexByType`): civil ships
(9-14), warships (1-8, 23-26, 28, 29, 31) and defense (15-22, 27, 30). The client hides the build
button for types 24-26, 28 and 29, and sends type 23 to the krypton shop.



For a status other than 200 the client shows the response body **as text** (or the `<title>` of
an HTML page) in a message box. Send plain text, not JSON.

## Game requests

`GameUtils:SendMsg(path, params, ...)` → `ext.http.request` → `ExtHttpConnection`.

- **URL:** `<server><path>` where `<server>` is
  - `AutoUpdate.gameGateway` for `servers`, `users/get_user_server` and `faqs/game_faq`
    (mode `notencrypt`),
  - otherwise `GameGlobalData:GetCurrentServerURL()`, the game world the player picked
    (mode `normal`).
- **Method:** `GET` by default, parameters as query string `k=v&k=v`. For `POST` the
  parameters go into the body as `application/x-www-form-urlencoded`.
  Parameters are **not** encrypted (observed: `POST trials.json` with body `username=`).
- **Cookies** set by the server are stored and sent back.
- **gzip** response bodies are supported.

### Response encryption (mode `normal`)

If the response is JSON (`Content-Type: application/json`, `text/json`,
`application/javascript` or `text/javascript`) and the request mode is `normal`, the body must be

```
Base64( AES-256-CBC( key, iv = 16 zero bytes, PKCS#7 padding, utf-8 JSON ) )
```

The 32-byte key is a constant in `libgalaxyempire.so` (returned by the JNI function
`NetUtils.fuck()`). It is not stored in this repository; the setup tools read it from the
player's own APK.

Other content types are passed to Lua as text or binary without decryption.

### Request headers

Built in `ext.http.request` (`AutoUpdateInit.lua`):

| Header            | Value |
|-------------------|-------|
| `sig`             | `ENC1(url_or_body .. udid .. salt .. requestindex .. "uCeU5X67Bt")`, 32 hex chars, probably MD5. `salt` comes from `GameGlobalData:GetSalt()` (empty before login) |
| `requestindex`    | `ENC2(counter)`; the counter starts at a random value ≤ 10000 and increases per request |
| `openUDID`, `DeviceToken` | device identifier (32 hex chars) |
| `AndroidId`, `MACAddress` | Android only |
| `Appid`           | `500099330` (Google Play), `500099331` (Alipay), `500099332` (Amazon) |
| `BundleId`        | `com.tap4fun.ge` |
| `clientv`         | app version, e.g. `1.9.36` |
| `resv`            | local resource version, e.g. `6507` |
| `Locale`          | game language, e.g. `de` |
| `shiqu`           | time zone, e.g. `Europe/Berlin` |
| `platform`        | `Android` / `iOS` |
| `Model`           | device model |
| `IosVersion`      | OS version (also on Android) |
| `User-Agent`      | `Tap4fun client` |

A replacement server can ignore `sig` and `requestindex` at first.

## Open questions

- `ENC1` / `ENC2`: exact algorithms (MD5? AES?) in case we want to validate requests.
- Original values for a new account (resources, storage limits) and the economy formulas.
