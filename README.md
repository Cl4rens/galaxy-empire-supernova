# Project Supernova — Galaxy Empire Revival

A fan-made effort to bring the discontinued mobile game **Galaxy Empire** (tap4fun) back to life
with a reimplemented game server.

> **Status: early research.** Nothing is playable yet. Right now we are taking the client apart
> to understand how it talks to the server.

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

What the patcher does (planned):

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

> Most of these steps describe the planned workflow. The tools do not exist yet.

### Requirements

- [Git](https://git-scm.com/)
- [Python](https://www.python.org/) 3.10 or newer (analysis tools and patcher)
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

### 3. Build the patched APK *(planned)*

```sh
python tools/patch_apk.py apk/galaxy-empire-1-9-36.apk --server <host>:<port>
```

The result is written to `build/`.

### 4. Run a server *(planned)*

Instructions will follow once the server exists.

### 5. Install and play

Install the patched APK from `build/` on your device, e.g. with
`adb install build/galaxy-empire-supernova.apk`.
Uninstall any old copy of the original game first, because the signatures differ.

## Roadmap

- [x] Set up the repository
- [ ] Understand the client's asset container format (`.pak`) and extract the Lua game scripts
- [ ] Document the network protocol (connection, login, message format, encryption)
- [ ] Redirect the client to a custom server
- [ ] Patcher: verify → patch → re-sign
- [ ] Minimal server: login and entering the game
- [ ] Game logic: buildings, research, fleets, combat, alliances

## What we know so far

- The game runs on tap4fun's own engine. Native code is in `libgalaxyempire.so`
  (32-bit ARM only), and the game logic is written in **Lua**.
- The engine exposes `socket` (TCP), `http`, `json`, `AES` and `md5` modules to Lua.
  That suggests a JSON-based protocol over TCP, possibly encrypted.
- Game data ships in `assets/tap4fun.zip`, which contains `.pak` archives
  (magic bytes `AB E2 7F 9D`), PNG textures, MP3 audio and localized text files.

Detailed findings will be collected in [`docs/`](docs/).

## Repository layout

```
apk/      your original APK goes here (git-ignored)
docs/     research notes: file formats, protocol, game data   (planned)
tools/    analysis tools and the APK patcher                  (planned)
server/   the reimplemented game server                       (planned)
```

## Contributing

Contributions are welcome, especially from people who played the game back then.
Screenshots, videos, or memories of how things worked help us rebuild the server-side logic.

Ground rules:

- **Never commit original game files** (APKs, extracted assets, decompiled scripts).
- Document what you find in `docs/` so others can build on it.
