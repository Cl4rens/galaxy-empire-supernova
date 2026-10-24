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
- Login with password: `POST <world>/sessions.json` with `username` and `password` (both ENC2).
- Register ("end trial"): `POST <world>/users.json` with `username`, `password`,
  `password_confirmation` (all ENC2); answers like a login. The client then saves name and
  password and uses `sessions.json` from now on.
- Change password: `POST users/<id>.json` with `_method=put`, `username`, `old_password`,
  `new_password`, `new_password_confirmation` (ENC2). Rename: `POST users/reset_username` with
  `username`, `password`. Both show the answer text in a message box.
- `POST users/<id>/email_bind_status` → `{mail_status: 0}` (0 unbound, 1 waiting, 2 bound).

Supernova binds guest accounts to the device that created them (`openUDID`); registered
accounts need their password and work on any device. Passwords are stored as scrypt hashes.

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

### Galaxy map and solar systems

| Request | Response |
|---------|----------|
| `GET systems/<system_id>` | a solar system (`system_id` = (galaxy − 1) × 499 + system) |
| `GET systems/<system_position>/<galaxy_position>` | the same, by position — note the order: system first |
| `GET systems/<system_id>/locate` | `{system_coordinate_x, system_coordinate_y}`: the system's tile on the galaxy map |
| `GET galaxy_map.json?galaxy_position=..&x=..&y=..` | `{"systems": [...]}`: the tiles around (x, y) |

A **solar system** is `{system_id, galaxy_position, system_position, planets, lord_info?,
safe_explore_times?}`. The client expects all 21 slots in `planets`: 1–15 planets, 16 the
expedition point, 17–21 pirate bases (their name is shown as `"<planet_name> LV <pirate_level>"`).
Slots it does not get keep the placeholder text of the Flash UI. Each slot:

```
{id, galaxy_position, system_position, planet_position, planet_name, empire_id, user_name,
 alliance_id, alliance_name, has_debris, is_protected, can_attack, moon_array, kryptons,
 planet_icon, pirate_level, next_refresh_time}
```

- An empty slot has `id` and `empire_id` 0.
- `kryptons` must be a list for every slot, even empty ones; the client iterates it.
- `planet_icon` must be 0 unless a special icon is wanted.
- `lord_info` (system ruler) is optional here.
- A slot with `has_debris: true` offers "Recycle"; the confirmation dialog shows `debris_desc`.
- Pirate slots (17–21) show `next_refresh_time` (seconds) as the time until the base is rebuilt.
- A pirate espionage report sets `probe_result.pirate_name`, `pirate_level` and lists
  `fleets`.

The **galaxy map** is a grid of 34 × 34 tiles per galaxy (`TILE_W` 338, `TILE_H` 274), one
system per tile. Supernova lays the 499 systems out row by row: system *n* is tile
((n − 1) mod 34, (n − 1) div 34). A tile:

```
{system_id, system_coordinate_x, system_coordinate_y, galaxy_position, system_position,
 user_count, lord_info: {lord_name, lord_level}, debris_planets, strong_planets, weak_planets,
 active_planets}
```

Here `lord_info` is required (the client reads `lord_info.lord_name`).

### Fleet missions

| Request | Meaning |
|---------|---------|
| `GET fleet_hanging_events/show_be_attacked` | flight list of the dispatch screen: `{hanging_info: [own flights], be_attacked_hanging_info: [{radar_level, hanging: [...]}], planet: {current_server_time}}` |
| `GET fleet_hanging_events/prepare_hanging?fleet_ids=..&fleet_cnt_ids=..&end_p_pos=g,s,p&mission=..&start_p_id=..&planet_type=planet` | before sending: `{missions: [...], speed, total_time, fuel_consumption, empty_cargobays}` |
| `POST fleet_hanging_events.json` with the same fields plus `speed_ratio` (10–100), `metal`, `crystal`, `deuterium` | send the fleet |
| `POST fleet_hanging_events/<event_id>/cancel.json` with `planet_id` | recall a fleet that is still outbound |
| `GET planets/<planet_id>/fleet_info.json` | sent when a flight timer runs out; the answer is merged into both the empire and the planet, so it carries planet fields plus `fleet_hanging_events` |

- `fleet_ids` / `fleet_cnt_ids` are comma-terminated lists (`"109,"`, `"2,"`). The ids are the `id`
  field of `main_planet.fleets`; the dispatch screen only lists entries with `fleet_category` 1
  (ships), not 2 (defense).
- `missions` decides which mission buttons are enabled. Mission ids and button positions:
  transport 4 (0), recycle 3 (1), espionage 6 (2), attack 7 (3), deploy 5 (4), colonize 2 (5),
  expedition 1 (6), transmit 16 (7). The client only shows the cargo panel for transport, deploy
  and colonize.
- The client shows `total_time × 100 / speed_ratio` and `fuel_consumption × speed_ratio / 100`
  itself, so `prepare_hanging` reports the values for 100 %.

A flight in `empire.fleet_hanging_events`:

```
{event_id, planet_id, mission, origin_mission, mission_name, start_planet_name,
 start_planet_pos ("g:s:p"), end_planet_id, end_planet_name, end_planet_pos, distance_time,
 is_returning, finish_time, total_time, acs_status, show_hanging, fleets: [{fleet_type, fleet_count}]}
```

`finish_time` is the end of the current leg (arrival, or return when `is_returning`), and
`total_time` the length of that leg. Mission 11 marks hostile fleets on their way to the player.

### Mailbox

| Request | Meaning |
|---------|---------|
| `POST messages/get_new.json` with `username` (ENC2), `device_type`, `checklist`, `is_update` | new mails: `{new_message_count, mails: [...]}`. The client calls it twice: once with an empty `checklist`, then with the received ids (`"4,5,6"`) to confirm them |
| `POST messages.json` with `from`, `to`, `subject`, `body` (all `ext.ENC3`, the same encryption as ENC2), `message_type` 3 | send a mail to a player; alliance mails use `message_type` 4, `alliance_id`, `do=alliance_mail` |

The client stores every mail itself (`MAILS.tfl`, up to 99 player and 250 system mails); the
server only hands out unconfirmed ones. The number on the mail button is
`main_planet.new_message_count` / `empire.new_message_count`.

A mail: `{id, message_type, content_type, from, to, subject, body, created_at, is_read}`.

| `message_type` | Tab | |
|---:|-----|-|
| 1 | System | mission reports |
| 2 | System | system messages |
| 3 | Personal | player mail, `body` is text |
| 4 | Personal | alliance mail |
| 5 | Explore | expedition / exploration combat |
| 6 | System | scan reports (sensor array) |

For reports `body` is an object; `body.mission_type` picks the layout:

| `mission_type` | Report | `body` fields |
|---:|--------|---------------|
| any, with `is_returning: true` | fleet returned | `event_info` |
| 4 | transport arrived | `event_info` |
| 15 | delivery received | `event_info` |
| 5 | deployed | `event_info` |
| 6 | espionage | `event_info`, `probe_result` |
| 14 | spied on | `event_info: {espionage_user, espionage_planet_pos, be_espionage_user, be_espionage_planet_pos}` |
| 7 | battle | `event_info`, `notify_message`, `attack_result` (see below) |
| 13 | battle against a pirate base | like 7 |
| 1 | expedition (`message_type` 5) | `event_info`, `notify_message`, `explore_data: {curr, max}`, `expedition_result: {situation, capacity, found_resources?, resources?, fleets?, dark_matter?, attack_report?}` with `situation` one of `get_resources`, `get_fleets`, `get_dark_matter`, `encounter_pirates`, `encounter_shaped`, `return`, `encounter_black_hole` |
| 3 | recycling | `event_info` (collected amounts in `resource_*`), `total_garbage: {garbage_metal, garbage_crystal, garbage_deuterium}` (field before collecting) |

`event_info`: `{mission, mission_name, start_planet_name, start_planet_pos, end_planet_name,
end_planet_pos, finish_time, resource_metal, resource_crystal, resource_deuterium,
hanging_fleets: [{fleet_type, fleet_count}]}`.

`probe_result`: `{metal, crystal, deuterium, energy_max, krypton_stone, pirate_krypton_level: [n],
fleets?, defenses?, buildings?, radar_able?, debris_*?}`. `pirate_krypton_level` must be present
(the client reads index 1); `fleets`/`defenses` are `[{fleet_type, fleet_name, amount}]`,
`buildings` `[{building_type, building_name, building_level}]`. A `pirate_name` switches to the
pirate layout.

### Battle reports

`body.attack_result`:

```
{attacker_name, defender_name, win ("win"/"lose"/"draw", from the attacker's view), winner_name,
 attack_start_fleets_count, attack_end_fleets_count, defend_start_fleets_count, defend_end_fleets_count,
 attack_bonus_info: {tech_bonus: {attack_bonus_percent, shield_bonus_percent, armour_bonus_percent}},
 defend_bonus_info: {...}, attack_bonus: {...}, defend_bonus: {...},
 round: [{attack_attack_count, attack_attack_value, attack_shield_absorb,
          defend_attack_count, defend_attack_value, defend_shield_absorb,
          attack_survived_fleets: [{fleet_type, amount}], defend_survived_fleets: [...]}],
 debirs: {metal, crystal}}
```

- The mail's `to` must be the recipient's username: the client compares it with
  `attacker_name` to decide whether to show VICTORY or DEFEAT.
- A trailing round entry with `attack_recovered_fleets` / `defend_recovered_fleets` lists
  repaired defense; it is not shown as a round.
- `debirs` (sic) is the debris field.
- With `attack_result.booty` the client switches to a detailed layout that also needs fleet
  types 23–26, 28, 29 and 31 in the player's fleet list (missiles, special ships). Supernova does
  not send `booty` and puts the loot into `notify_message` instead.

Incoming attacks appear in the defender's `fleet_hanging_events` with `mission` 11; while one is
on its way the heartbeat sets `empire: 1` and the planet has `is_underattack: true`.

### Several planets

| Request | Meaning |
|---------|---------|
| `GET users/<empire_id>/my_planets.json` | planet list: `{planets_info: [...], empire: {...}}` |
| `GET planets/<planet_id>.json` | switch to that planet; the answer is the planet (merged into `main_planet`) plus `researches` |

`empire.main_planet` is always the planet the player is looking at. Actions name the planet
they act on (`planet_id`, `start_p_id`, `current_planet_id`).

A `planets_info` entry: `{planet_id, planet_name, planet_type (2 capital, 1 colony, 3 moon),
galaxy_position, system_position, planet_position, field_current, field_max, planet_activity,
radar_level, is_radar_on, moon_array, is_protected, protected_time, attacked_time, attacked_num,
auto_explore}`.

Colonization report: `body.mission_type` 2 with `event_info`.

### Rankings

`GET ranks?do=<kind>&user_id=<id>` answers `{"rank": [...]}`.

| `do` | List | Entry fields |
|------|------|--------------|
| `top_users` / `my_rank` | points | `id, rank, username, alliance_name, score` |
| `top_fight_score` / `my_fight_score` | strength | `id, rank, username, alliance_name, score` |
| `top_fight_users` / `my_fight_rank` | battle statistics | `id, fight_rank, username, alliance_name, fight_win_count, fight_lose_count, fight_draw_count` |
| `top_alliances` / `my_alliance` | alliances | `id, rank, alliance_name, president_name, score` |

`my_*` lists show the entries around the player. `empire.rank` and `empire.score` appear in the
top bar.

### Alliances

| Request | Meaning |
|---------|---------|
| `GET alliances.json` (`do=search&keyword=ENC2`) | list or search: `{"alliances": [...]}` |
| `POST alliances.json` `alliance_name, description` | found an alliance |
| `GET alliances/<id>.json` | overview (president, vice presidents, members, texts, `bag`) |
| `POST alliances/<id>.json` `_method=put` / `_method=delete` | change texts / dissolve |
| `POST alliances/<id>/auto_in.json` `status=0/1` | accept applications automatically |
| `GET alliance_memberships.json?alliance_id=` | member list |
| `POST alliance_memberships/<id>.json` `_method=put&level=` / `_method=delete` | change rank / kick or leave |
| `GET alliance_applications.json?alliance_id=` | open applications |
| `POST alliance_applications.json` `alliance_id` | apply |
| `POST alliance_applications/<id>.json` `do=approve/reject/cancel` | decide or withdraw |
| `POST messages.json` `message_type=4` | mail to all members |

Ranks (`level`): 0 member, 1 vice president, 2 president. The empire carries `alliance_id`,
`alliance_name`, `alliance_membership_id` and `alliance_application_id`. The treasury
(`bag_log`) and coordinated attacks (`acses`) answer empty lists.

### Tutorial quests

On login the client shows the tutorial choice while `empire.quests_complete` is false and
`empire.quests_newbie_guide` is 0. `quests_new_ver = 0` selects the client's "old" quest
numbering, which decides where it shows arrows and hints.

- `GET quest_events?user_id=` answers
  `{"update": {"cur_quest": {...}, "reward_info": {...}}, "next_quest": {...}, "empire", "planet"}`.
  `cur_quest` has `quest_index, quest_title, quest_description, quest_state` (goals reached),
  `quest_targets: [{target_title, finished}]` (the client drops the first four characters of
  each title), `quest_rewards: [{reward_type, reward_subtype, reward_count, reward_name}]`,
  `congra_text, hints, is_last`. Reward types: 1 resource (subtype 1 metal, 2 crystal,
  3 deuterium), 2 ships, 4 money, 7 building.
- `reward_info = {"code": 0, "data": [{type, subtype, amount}]}`; `amount` is the **new total**
  the client writes into its planet data. A non-zero `code` shows `data` as an error text.
- `POST quest_event/skip_all_newbie_guide` answers `{"update": {"rewards_all": text}, "empire",
  "planet"}`; the client then hides the quest button.

### Planet info

- `POST planets/<id>/change_name.json` `name` answers `{"planet_name"}`.
- `POST planets/<id>` `_method=delete` gives the planet up and answers
  `{"empire_info", "planet_info"}` (the planet the client switches to).

### Chat

- `GET chat_messages.json?alliance_id=&last_id=0&count=50` answers `{"chat_messages": [...]}`,
  newest first (the client shows the first one on the planet screen). `alliance_id` 0 is the
  world channel. Entries: `message_id, channel (1 world, 2 alliance), alliance_id,
  from_user_name, words, created_at, account_level` (0 guest, 1 registered, 2 VIP, 3 GM,
  4 system; the client colours 2-4).
- `POST chat_messages.json` `words, channel, alliance_id, from_username` sends a message.
- `POST reports` `message_id, additiona_notes, username=ENC2` reports a message.
- `main_planet.last_chat` (`"name:words"`) is the line on the planet screen before the chat was
  opened.

### Moons

A moon is a planet entry with `planet_type = 3` and its own `planet_id`; the client enters it
with `GET planets/<id>.json` and then shows the moon screen. Moons are listed inside their
planet: `planets_info[].moon_array = [{moon_id, moon_name, moon_field_current, moon_field_max,
attacked_time, attacked_num}]`, and system views put `moon_array = [{moon_id, moon_name}]` on the
planet slot. The client always starts on a planet after login.

The moon screen (`Moon.tfs`) has one place per building type and finds it with
`TT_<building_position_index>`, so moon buildings send their **type** as
`building_position_index`: 18 moon robotics factory, 17 moon shipyard, 19 moon base,
15 sensor array, 16 transmit portal, 21 pioneer lab. New moon buildings are started without a
`building_index`.

Fleets sent to a moon carry `planet_type=moon` (otherwise `planet`) in
`fleet_hanging_events/prepare_hanging` and `fleet_hanging_events.json`.

- **Sensor array:** `POST sensor_arrays/scan` `moon_id, planet_id` answers `{"data": [...]}`, the
  flights from and to the planet: `mission, start_planet_name, start_position, end_planet_name,
  end_position, start_time, finish_time, is_returning` (**0/1**, the client compares with 0),
  `amount, fleets: [{name, fleet_count}]`. `main_planet.senor_array_range` (sic) is shown on the
  building. `POST sensor_arrays/save_report_mail` (same parameters) stores the scan as a mail of
  `message_type` 6 with body `{head, head_name, body: [{title, mission, mission_type, time_left,
  from_name, from_position, to_name, to_position, fleets}]}`.
- **Transmit portal:** mission 16 (`FM_TRANSMIT`), sent from a moon to another own moon with
  `planet_type=moon`. Both portals then recharge; `main_planet.transmit_recharge_event` holds
  `[{start_time, finish_time, total_time}]` while they do.

### Errors

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
