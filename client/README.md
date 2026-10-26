# Supernova client engine

Galaxy Empire's native engine (`libgalaxyempire.so`) exists only as 32-bit ARM code, so the
game cannot run on phones that only run 64-bit apps (many phones from 2023 on, and every
Android 16 phone we tried). The game itself, though, is written in Lua: 177 scripts in the
APK's `data1_64.pak`, plus Flash files (`.tfs`) for every screen. This folder is an engine of
our own that runs those original files, read from the player's own APK at run time. Nothing
from the APK is stored here.

## Stage 1: the engine core (done)

`supernova_client` runs the game's scripts in a real Lua 5.1 VM (64-bit, via
[lupa](https://github.com/scoder/lupa)) and implements the native API they call:

| Native part | Status |
|-------------|--------|
| script loading (`require`, `loadfile`, `dofile` on the pak) | done |
| `ext.http.requestBasic`: GET/POST, cookies, AES responses, downloads | done |
| `ext.ENC1`/`ENC2`/`ENC4` (request signature, encrypted names) | done |
| storage (`ext.SaveGameData`, saved `.tfl` files) | done |
| game texts (`LuaLoader`, `data2/text/*.idx` + language files) | done |
| device information, app version, platform | done |
| Flash objects (`ext.RegisteFlashClass`, `Load`, `InvokeASCallback`, `fscommand`) | recorded only, no picture yet |
| audio, local notifications, Game Center | not here (logged, ignored); audio is in `engine-rs` |
| touch input | through `fscommand` only |

With it the original game boots against a Supernova server: update check, loading screen,
title screen, guest login, the city screen with all its objects, the heartbeat, and actions such
as ordering buildings. Run it like this (start a Supernova server first):

```sh
cd client
pip install -r requirements.txt
python -m supernova_client --apk ../apk/galaxy-empire-1-9-36.apk --server http://127.0.0.1:8000 --build 1
```

```
Commander13 on Homeworld: metal 465, crystal 497, deuterium 0, dark matter 200, 0 building order(s)
Commander13 on Homeworld: metal 417, crystal 473, deuterium 0, dark matter 200, 1 building order(s)
```

Things we learned on the way (also useful for a native implementation):

- The APK's `ofl.tfl`/`fl.tfl` file lists are obfuscated with an unknown cipher; the updater only
  needs the resource version (6507), so the engine provides a minimal list.
- `ext.ENC2(key, text)` is AES-256-CBC with the first 32 bytes of `key` (passed by the scripts),
  zero IV, base64.
- The scripts call tap4fun's gateway (`b.portal-platform.tap4fun.com/ge/url_get/online/world/`);
  the engine sends those requests to the configured server.
- Without a renderer, the driver sends the commands a screen would send: `LoadingOver` at the
  end of the loading animation, `btnStartPressed` on the title screen, `btn_build_clicked`, ...
- lupa sometimes resolved attributes of Python objects to the wrong method; the native API is
  therefore handed to Lua as a plain table of functions.

Tests (`python -m pytest tests`) run the engine on a small fake game with its own scripts and a
local HTTP server, so they need no game files.

## Stage 2: drawing the screens (playable on the desktop)

The `.tfs` screens are ordinary SWF files with ActionScript 2 (they open in JPEXS), except for
their pictures, which gameswf loads from `data2/` at run time:

- Each placeholder bitmap is replaced by the PNG/JPEG it is exported as (146 screens).
- The large atlases (`main_city_res.png`, ...) are PVR v2 textures in ETC1 with the alpha channel
  in a separate `_alpha.pak`.
- Pictures named `NA_*` start empty: the game fills them at run time with `ReplaceTexture`.

`supernova_client.screens` (Python) does that conversion for inspection:

```sh
python -m supernova_client.screens --apk ../apk/galaxy-empire-1-9-36.apk --out screens
```

The engine itself is now [`engine-rs`](engine-rs/README.md): the Lua side in Rust with
[mlua](https://github.com/mlua-rs/mlua), and every Flash object a [Ruffle](https://ruffle.rs)
player, connected like gameswf did it (`InvokeASCallback`, `fscommand`, `ReplaceTexture`,
native `btn...` buttons, text with `data2/Arial.ttf`). It shows the screens' 16:9 layouts (as
the original did on phones), plays the game's music and sounds, takes text input from the
keyboard and two-finger gestures (zooming the city), and frees a screen when the game lets go
of it. Compared side by side with the original app, the screens tried so far (title, news,
city, store, chat, mail and battle reports, galaxy map and solar systems, dispatch and
missions, shipyard, rankings, alliance) look and behave the same.

## Stage 3: an Android app (runs)

[`engine-rs/android`](engine-rs/README.md#android-app) builds the engine as an Android app for
arm64 phones and x86_64 emulators, with nothing from the game in it. `tools/patch_apk.py --64bit`
adds the game files from the player's own APK, so the player installs one APK. Tested on
Android 13 (BlueStacks, x86_64): login over the LAN, news, city, tutorial dialogs, going to the
background and back, typing with the on-screen keyboard (the screen moves up so the field stays
visible), sound. The app needs Android 8.0 or newer.
