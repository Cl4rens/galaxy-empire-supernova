# Supernova engine (Rust)

The client engine for 64-bit-only devices, built on [Ruffle](https://ruffle.rs) for the Flash
screens. Work in progress; see `../README.md` for the stages.

## Build

Needs Rust, Java (Ruffle's build compiles its ActionScript 3 library with it) and on Windows
the Visual Studio C++ Build Tools.

```sh
python setup_ruffle.py          # Ruffle at a fixed commit, plus ruffle.patch
cargo build --release
```

`ruffle.patch` adds to Ruffle's `Player` what gameswf let the game do to a movie from outside:

| gameswf | Player |
|---|---|
| `InvokeASCallback` | `call_avm1_function(target, function, args)` |
| `SetBtnEnable` | `set_avm1_property(target, "enabled", value)` |
| `SetCustomEffect(target, "Gray", on)` | `set_avm1_gray(target, on)` |
| `SetLineMax(target, true, min)` | `fit_avm1_text(target, min, ...)`, after every frame |
| `ReplaceTexture(slot, picture)` | `replace_bitmap(slot, pixels)` |
| native buttons (clips named `btn...`) | `find_avm1_button(x, y, "btn")`, then `focus_in`/`released` and `on_clicked` from `screens.rs` |
| names without regard to case, `"a,b,".split(",")` gives two, text not clipped to its field | `set_gameswf_compat(true)` |
| scripts of attached clips run at once | `call_avm1_function` runs the queued actions before it returns |
| text fields named `..._SLHM_...` / `..._MLHM_...` show what fits, ending with "..." | `text_field_paths(mark)`, then `truncate_avm1_text` from `screens.rs` |
| a text field turned into a string gives its text | `set_gameswf_compat(true)` |
| Flash commands reach the scripts at once | `FsCommandProvider::on_fs_command_in` with the running movie |

Text fields named after a game text (`LC_MENU_TAB_OFFICER`, also with the layout mark
`SLHM_`/`MLHM_` in it) get that text when `convert.rs` converts the screen.

Flash commands come back through Ruffle's `FsCommandProvider`.

## Try a screen

```sh
python -m supernova_client.screens --apk ../../apk/galaxy-empire-1-9-36.apk --out screens   # in ../
./target/release/screen-test screens/Login.swf login.png 30 moveIn
```

## Play

```sh
./target/release/supernova ../../apk/galaxy-empire-1-9-36.apk http://127.0.0.1:8000 save
```

A window with the game; the mouse is the finger, and the keyboard types into text fields.
Every Flash object draws into its own texture, and `compositor.rs` stacks them in the order the
game rendered them.

## Play without a window

The engine runs the game's own Lua scripts from your APK (`engine.rs`, a port of
`../supernova_client`) and shows every Flash object the scripts load as its own Ruffle player
(`screens.rs`). The screens are converted on load (`convert.rs`): placeholder pictures are
filled from the ETC1 textures, and text fields draw with the game's `data2/Arial.ttf`.

```sh
./target/release/headless ../../apk/galaxy-empire-1-9-36.apk http://127.0.0.1:8000 save
./target/release/screenshots ../../apk/galaxy-empire-1-9-36.apk http://127.0.0.1:8000 save shots
```

`screenshots` logs in and saves the title screen and the first screen after the login, then one
picture after each step in `SUPERNOVA_TAPS`, separated by `;`: `x,y` taps (in the game's
640x1136 coordinates), `drag:x1,y1,x2,y2`, `pinch:x,y,from,to`, `type:text` (`
` ends the
input) and `wait:seconds`. The test tools play no sound.
`screen-convert <apk> <dir> ItemShop.tfs a1.png` writes screens as SWF and pictures as PNG.
`SUPERNOVA_LAYERS=1` also saves every Flash object on its own, `SUPERNOVA_DUMP_SCREENS=<dir>`
writes the converted screens as SWF files, and `RUST_LOG=supernova_engine=debug` shows the
requests and ActionScript calls. `avm1-dump <screen.swf> [text]` prints a screen's
ActionScript.

## Android app

`android/` makes the engine an Android app for 64-bit phones (arm64-v8a) and emulators (x86_64).
As built here it holds nothing from the game; `tools/patch_apk.py --64bit` (see the main README)
adds the game files from the player's own APK on their computer, and that is the app to install.

- The app is `android.app.NativeActivity` running `libsupernova.so` (`android/src/lib.rs`, with
  the same window code as the desktop, `src/app.rs`); no Java code.
- It reads what the patcher put in as assets: the game files (`tap4fun.zip`, with the server
  address patched in like for the 32-bit app), `supernova/client.key` and
  `supernova/server.txt`.
- If it cannot start, it says why in logcat (tag `supernova`) and in `error.txt` in
  `Android/data/org.supernova.galaxyempire/files/`.
- It needs Android 8.0 (API 26) or newer: sound goes out through AAudio.

Build the engine app (needs the Android SDK with build-tools, a platform and the NDK, Java, and [cargo-ndk](https://github.com/bbqsrc/cargo-ndk); no Gradle):

```sh
rustup target add aarch64-linux-android x86_64-linux-android
cargo install cargo-ndk
python android/build_apk.py          # target/android/supernova.apk
```
