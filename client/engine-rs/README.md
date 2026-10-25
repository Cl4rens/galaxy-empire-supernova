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

`ruffle.patch` adds `Player::call_avm1_function(target, function, args)`: what gameswf's
`InvokeASCallback` does, calling an ActionScript function on a movie clip. Flash commands
come back through Ruffle's `FsCommandProvider`.

## Try a screen

```sh
python -m supernova_client.screens --apk ../../apk/galaxy-empire-1-9-36.apk --out screens   # in ../
./target/release/screen-test screens/Login.swf login.png 30 moveIn
```

## Play without a window

The engine runs the game's own Lua scripts from your APK (`engine.rs`, a port of
`../supernova_client`) and shows every Flash object the scripts load as its own Ruffle player
(`screens.rs`). The screens are converted on load (`convert.rs`): placeholder pictures are
filled from the ETC1 textures, and text fields draw with the game's `data2/Arial.ttf`.

```sh
./target/release/headless ../../apk/galaxy-empire-1-9-36.apk http://127.0.0.1:8000 save
./target/release/screenshots ../../apk/galaxy-empire-1-9-36.apk http://127.0.0.1:8000 save shots
```

`screenshots` logs in and saves the title screen and the first screen after the login.
`SUPERNOVA_LAYERS=1` also saves every Flash object on its own, `SUPERNOVA_DUMP_SCREENS=<dir>`
writes the converted screens as SWF files, and `RUST_LOG=supernova_engine=debug` shows the
requests and ActionScript calls.
