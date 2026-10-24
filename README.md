# Project Supernova — Galaxy Empire Revival

A fan-made effort to bring the discontinued mobile game **Galaxy Empire** (tap4fun) back to life
with a reimplemented game server.

> **Status: early development.** A patched client logs in as a guest, builds and upgrades
> buildings, produces resources, researches technologies, builds ships and defense, and sees the
> other players on the galaxy map, sends fleets on transport and espionage missions, and gets
> reports and player mail in the mailbox, and attacks other players. Colonization is not there yet.

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
   dead official servers.
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

Server operators can manage players from the command line (in Docker:
`docker compose exec supernova python -m supernova.admin ...`):

```sh
python -m supernova.admin list
python -m supernova.admin show <username>
python -m supernova.admin give <username> metal=10000 crystal=5000 deuterium=2000
python -m supernova.admin set-building <username> <building_type> <level> [lot]
python -m supernova.admin give-fleet <username> 9=5 1=10      # fleet_type=count
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

The patcher downloads [uber-apk-signer](https://github.com/patrickfav/uber-apk-signer) on first
use to sign the APK. The result is `build/galaxy-empire-supernova.apk`.

### 5. Install and play

Install the patched APK from `build/` on your device, e.g. with
`adb install build/galaxy-empire-supernova.apk`.
Uninstall any old copy of the original game first, because the signatures differ.

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
- [ ] Recycling, colonization, pirates, expeditions
- [ ] Tutorial quests
- [ ] Support for 64-bit-only devices
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
