# Project Supernova — Galaxy Empire Revival

A fan-made effort to bring the discontinued mobile game **Galaxy Empire** (tap4fun) back to life
with a reimplemented game server.

> **Status: early development.** A patched client logs in as a guest, builds and upgrades
> buildings, produces resources, researches technologies, builds ships and defense, and sees the
> other players on the galaxy map, sends fleets on transport and espionage missions, and gets
> reports and player mail in the mailbox, attacks other players and colonizes new planets.

> **Disclaimer:** This is a non-commercial fan project. It is not affiliated with, endorsed by, or
> connected to tap4fun or any rights holder. All trademarks belong to their respective owners.

---

## What this project is

Galaxy Empire was an online space strategy game for Android and iOS: build up your planets,
research technologies, send fleets, and fight other players. When the official servers were
shut down, the game stopped working, because every action depends on the server.

Project Supernova aims to:

1. **Document** how the client works: file formats, network protocol, game data.
2. **Reimplement the server** so the original client can connect to it again.
3. **Provide a patcher** that turns *your own* copy of the original APK into one that connects to a
   Supernova server.

## Bring your own APK

This repository contains **no game files**: no APK, no graphics, no sounds, no scripts from the
original game. It only contains our own code and documentation.

To play, you supply the original APK yourself, and the patcher builds a modified copy locally on
your machine:

```
original APK  ──►  patcher (this repo)  ──►  patched APK  ──►  install on your device
 (yours)            verify · patch · re-sign     (stays on your machine)
```

What the patcher does:

1. **Verify** that your APK is a supported version by comparing its SHA-256 hash.
2. **Patch** the client so it connects to the server address you choose instead of the
   dead official servers, and make a few small script changes the server needs (for example,
   the daily tasks button, which the client always hid, and a crash in the arena shop on tall
   screens).
3. **Re-sign** the APK so Android will install it.

Please **do not share patched APKs**. Share this repository instead, so everyone builds their
own copy from their own original.

### Supported client versions

| Version | Package          | SHA-256                                                            |
|---------|------------------|--------------------------------------------------------------------|
| 1.9.36  | `com.tap4fun.ge` | `cfea85feeb17bc5c1573dc9856e75b783a31b5e0d85f469a475917b58abaf4c6` |

## Setup

### Requirements

- [Git](https://git-scm.com/)
- [Python](https://www.python.org/) 3.10 or newer (tools, patcher and server)
- or [Docker](https://docs.docker.com/get-docker/) for the server
- [Java](https://adoptium.net/) 17 or newer (APK re-signing)
- An Android device or emulator that can run **32-bit ARM** apps (`armeabi-v7a`).
  Real phones usually work. On PC, use an emulator with ARM translation,
  e.g. BlueStacks, LDPlayer or an Android 11+ image in Android Studio.

### 1. Get the code

```sh
git clone https://github.com/Cl4rens/galaxy-empire-supernova.git
cd galaxy-empire-supernova
```

### 2. Add your APK

Copy your original APK into the [`apk/`](apk/) folder. That folder is ignored by git, so your
file never gets committed by accident.

### 3. Run a server

**With Docker** (recommended for Linux servers):

```sh
docker compose up -d --build
```

The container reads the client key from the APK in `./apk` (mounted read-only) and keeps its
database in the `supernova-data` volume.

**Without Docker:**

```sh
cd server
python -m venv .venv
.venv/Scripts/activate          # Windows; on Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt
python -m uvicorn supernova.app:app --host 0.0.0.0 --port 8000
```

The server reads the client's response key from your APK in `apk/` (or from the path in the
`SUPERNOVA_APK` environment variable).

Optional settings: `SUPERNOVA_SPEED` (game speed, default 1), `SUPERNOVA_NEWBIE_POINTS`
(newbie protection below these points, default 5000), `SUPERNOVA_STRONG_POINTS` (from here on
the 1:5 rule no longer protects, default 500000), `SUPERNOVA_FORTRESS_BATTLE` (time of a
star fortress battle in seconds since 1970; battles repeat every week / speed, default Sunday
19:00 UTC), `SUPERNOVA_STARTING_DARK_MATTER` (dark matter of new players, default 200),
`SUPERNOVA_DARK_MATTER_PER_DAY` (dark matter every player earns per day, default 0),
`SUPERNOVA_BACKUP_HOURS` (database backup every so many hours, default 6, 0 = off),
`SUPERNOVA_BACKUP_KEEP` (backups kept, default 28), `SUPERNOVA_GC_EVERY_DAYS` /
`SUPERNOVA_ARENA_EVERY_DAYS` (start a Galaxy Challenge / arena by itself this many days after the
previous one started; default 0 = only by command).

**Running it for others:**

- Backups land in `backups/` next to the database (in Docker: inside the `supernova-data`
  volume, `/data/backups`). To restore one, stop the server, replace `supernova.db` with the
  backup file, start again. `docker compose cp supernova:/data/backups ./backups` copies them out.
- `GET /health` answers `{"status": "ok", "players": n}`; the Docker setup uses it as health check.
- `admin stats` shows players, how many were active in the last hour/day/week, alliances and
  the last backup; `admin events` shows the state of the Galaxy Challenge and the arena.
- `admin ban <username> "<reason>"` logs the player out and shows the reason at every login;
  `admin unban` lifts it.

Server operators can manage players from the command line (in Docker:
`docker compose exec supernova python -m supernova.admin ...`):

```sh
python -m supernova.admin list
python -m supernova.admin show <username>
python -m supernova.admin give <username> metal=10000 crystal=5000 deuterium=2000 credits=500 dark_matter=50
python -m supernova.admin set-building <username> <building_type> <level> [lot]
python -m supernova.admin give-fleet <username> 9=5 1=10      # fleet_type=count
python -m supernova.admin set-research <username> <research_type> <level>
python -m supernova.admin chat-reports                     # chat messages players reported
python -m supernova.admin delete-chat <message_id>
python -m supernova.admin news-add <normal|update|maintain|emergency> "<title>" "<text>"
python -m supernova.admin news-list
python -m supernova.admin news-delete <id>
python -m supernova.admin gift <username|all> "<title>" "<text>" metal=5000 9=2   # resource or fleet_type=amount
python -m supernova.admin gc-start [--in MINUTES] [--rounds 96] [--round-minutes 60]   # Galaxy Challenge
python -m supernova.admin gc-status
python -m supernova.admin gc-stop                          # ends it now and hands out the rewards
python -m supernova.admin arena-start [--in MINUTES] [--garrison-hours 24] [--challenge-days 6] [--prize-days 1]
python -m supernova.admin arena-stop                       # Conquer The Universe: on to the prize stage
python -m supernova.admin events                           # state of both events
python -m supernova.admin stats                            # players, activity, last backup
python -m supernova.admin backup                           # write a backup now
python -m supernova.admin icons-list                       # original item pictures still missing, with their wiki pages
python -m supernova.admin import-icons <folder>            # take the pictures saved from those pages (PNG or WebP)
python -m supernova.admin ban <username> "<reason>"
python -m supernova.admin unban <username>
```

### 4. Build the patched APK

```sh
python tools/patch_apk.py apk/galaxy-empire-1-9-36.apk --server <host>:<port>
```

`<host>:<port>` is the address of your server **as seen from the device**:

| Device                              | Address |
|-------------------------------------|---------|
| Android emulator / BlueStacks on the same PC | `10.0.2.2:8000` |
| Phone in the same Wi-Fi              | your PC's LAN IP, e.g. `192.168.0.10:8000` |

The patcher downloads [uber-apk-signer](https://github.com/patrickfav/uber-apk-signer) and
[apktool](https://github.com/iBotPeaches/Apktool) on first use. Besides the server address it
patches the client code so it runs on current Android versions without crashing or asking for
permissions; see [`docs/android.md`](docs/android.md). The result is
`build/galaxy-empire-supernova.apk`.

**Device requirement:** the original game engine exists only for 32-bit ARM. Phones without
32-bit support (e.g. Google Pixel 7 and newer) cannot run it; for them build the 64-bit app
below. PCs can use an emulator like BlueStacks.

#### The 64-bit app (phones without 32-bit support)

Supernova's own engine ([`client/engine-rs`](client/engine-rs/README.md)) plays the game's
scripts and screens on 64-bit phones. The patcher puts the game files from your APK into the
engine app; the result is one APK to install, with nothing to set up on the phone:

```sh
python tools/patch_apk.py apk/galaxy-empire-1-9-36.apk --64bit --server <host>:<port>
```

The result is `build/galaxy-empire-supernova-64bit.apk` (app name "Supernova"; it can be
installed next to the 32-bit game). It needs the engine app: download `supernova-engine.apk`
from the [latest release](https://github.com/Cl4rens/galaxy-empire-supernova/releases/latest)
(it holds only Supernova's engine, nothing from the game) and pass it with
`--engine-app supernova-engine.apk`, or build it yourself as described in
[`client/engine-rs`](client/engine-rs/README.md); then it is found at
`client/engine-rs/target/android/supernova.apk`. The server address is fixed in the app, as in the 32-bit APK: build it again
for another server.

### 5. Install and play

Install the patched APK from `build/` on your device, e.g. with
`adb install build/galaxy-empire-supernova.apk`.
Uninstall any old copy of the original game first, because the signatures differ.
When you install a newer patched APK over an older one, clear the app's data (or reinstall):
the game keeps the scripts it unpacked on its first start and would not see the changes.

## Roadmap

- [x] Set up the repository
- [x] Understand the client's asset container format (`.pak`) and extract the Lua game scripts
- [x] Document the network protocol (HTTP + JSON, response encryption)
- [x] Redirect the client to a custom server
- [x] Patcher: verify → patch → re-sign
- [x] Minimal server: update check and world list (client reaches the title screen)
- [x] Guest login, sessions, heartbeat, player state
- [x] Buildings and resource production (rules: [`docs/game-rules.md`](docs/game-rules.md))
- [x] Research
- [x] Shipyard: ships and defense
- [x] Galaxy map and solar systems
- [x] Fleet missions: transport, deploy, recall
- [x] Mailbox and espionage
- [x] Combat: attacks, battle reports, loot, debris fields
- [x] Recycling debris fields
- [x] Colonization: several planets per player
- [x] Pirate bases and expeditions
- [x] Rankings
- [x] Accounts: register, password login, rename, change password
- [x] Alliances: found, apply, ranks, alliance mail
- [x] Tutorial quests, renaming and giving up planets
- [x] Chat: world and alliance channels
- [x] Moons: forming after battles, moon buildings, flights to moons, sensor array, transmit portal
- [x] Bookmarks, news, daily check-in, gifts, help
- [x] Kryptons: refining, storage, planet slots, upgrades, research, merchant
- [x] Ship repair after defending
- [x] Newbie protection, unit info screen
- [x] Coordinated attacks (alliance members join an attack)
- [x] Pioneer Lab: Iron Behemoth, alloy fragments, dark energy, pioneer techs
- [x] ECM module and system lords
- [x] Alliance treasury and star fortresses
- [x] Item shop (dark matter), capsule and speed-ups
- [x] Ship modification with devices
- [x] Advanced ships: Devourer Interceptor, Titanenergetic Frigate, Proton Cannon, Annihilator, Reaper
- [x] Intercept and interplanetary missiles
- [x] Daily tasks
- [x] Officers, terraformers and credit items in the shop (credits only from the operator)
- [x] Galaxy Challenge (Fight G.E.F.), started by the operator
- [x] Conquer The Universe arena behind the star gate, started by the operator
- [x] Gift packs, invitation codes, empty purchase menu
- [x] Growth packages; chests with alliance members instead of Facebook
- [x] Operation: backups, events on a schedule, bans, statistics, health check
- [x] Runs on Android 6-16 without crashes or permission dialogs
- [x] Own client engine, stage 1: the original Lua scripts run on 64-bit and play against the
      server, without tap4fun's native code ([`client/`](client/README.md))
- [ ] Own client engine, stage 2: drawing the Flash screens (Ruffle), stage 3: Android app for
      64-bit-only phones
- [ ] Game logic: buildings, research, fleets, combat, alliances

## What we know so far

- The game runs on tap4fun's own engine. Native code is in `libgalaxyempire.so`
  (32-bit ARM only), and the game logic is written in **Lua**.
- All client/server traffic is **HTTP + JSON** against a Rails-style API (`users/<id>`,
  `planets/<id>`, ...). Responses to game requests are AES-encrypted.
- The Lua scripts ship as plain Lua 5.1 bytecode inside `.pak` archives and decompile cleanly.

Details: [`docs/protocol.md`](docs/protocol.md), [`docs/file-formats.md`](docs/file-formats.md).

## Repository layout

```
apk/      your original APK goes here (git-ignored)
docs/     research notes: file formats, protocol, game data
tools/    pak/Lua tools and the APK patcher
server/   the reimplemented game server (Python, FastAPI)
client/   our own client engine for 64-bit-only phones (stage 1: headless)
work/     local scratch space for extracted game files (git-ignored)
```

## Contributing

Contributions are welcome, especially from people who played the game back then.
Screenshots, videos, or memories of how things worked help us rebuild the server-side logic.

Ground rules:

- **Never commit original game files** (APKs, extracted assets, decompiled scripts).
- Document what you find in `docs/` so others can build on it.

## License

The code in this repository is licensed under the
[GNU Affero General Public License v3.0](LICENSE). If you run a modified version of the server
for other people, you must make your source code available to them.

The license covers only this project's own code. Galaxy Empire, its client and all of its assets
remain the property of their respective owners and are not part of this repository.
