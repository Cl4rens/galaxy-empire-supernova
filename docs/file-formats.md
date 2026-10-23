# File formats

## APK layout (client 1.9.36)

| Path                                | Content |
|-------------------------------------|---------|
| `classes.dex`                       | Java glue: activity, HTTP client (`ExtHttpConnection`), crypto helpers (`SecurityUtils`), SDKs |
| `lib/armeabi-v7a/libgalaxyempire.so`| tap4fun engine (C++), embeds Lua 5.1, gameswf (Flash UI), Irrlicht-style renderer |
| `assets/tap4fun.zip`                | all game data, extracted to `/storage/emulated/0/tap4fun/galaxy_empire/` on first start |
| `assets/alipay_plugin.apk`          | Alipay payment plugin (China only) |

Inside `tap4fun.zip` → `tap4fun/galaxy_empire/AppOriginalData/Android/`:

| File                  | Content |
|-----------------------|---------|
| `data1.pak`           | the game's Lua scripts, 32-bit bytecode |
| `data1_64.pak`        | the same scripts compiled for 64-bit (`ext.IsArm64()`) |
| `data2/*.pak`         | binary resources (textures etc.) |
| `data2/*.tfs`         | Flash UI files (gameswf) |
| `data2/text/*`        | localized text tables, one file per language |
| `ofl.tfl`, `fl.tfl`   | file lists for the auto-updater (loaded with `dofile`, obfuscated) |
| `shader/`, `sound/`   | GLSL shaders, MP3 audio |

## `.pak` archive

Little-endian. Extracted by [`tools/unpak.py`](../tools/unpak.py).

```
0x00  u32 magic       0x9D7FE2AB  (bytes AB E2 7F 9D)
0x04  u32 first_data  offset of the init entry (= start of the data area)
0x08  u32 names_off   offset of the name table (= end of the index)
0x0C  u32 count       number of entries including the init entry
0x10  u32 init_off    offset of the init entry
0x14  u32 init_size   size of the init entry
0x18  index           (names_off - 0x18) / 16 entries:
                        u32 name_off   offset into the name table
                        u32 flags      0x0C for all entries seen so far
                        u32 data_off
                        u32 data_size
      names           NUL-separated, obfuscated (not needed: see below)
      data            each entry: u32 raw_size, then a zlib stream
```

- In `data1.pak` the init entry is `AutoUpdateInit.lua`, the first script the engine runs.
- Entries are **not encrypted**. Lua entries are plain Lua 5.1 bytecode
  (`1B 4C 75 61 51`, 4-byte int/size_t/instruction, 8-byte double) and contain their own
  source name (`@./GameUtils.lua`), so the obfuscated name table can be ignored.
- The bytecode decompiles cleanly with [unluac](https://sourceforge.net/projects/unluac/).
