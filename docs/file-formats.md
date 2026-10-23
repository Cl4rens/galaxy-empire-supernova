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

Little-endian. Read/written by [`tools/pak.py`](../tools/pak.py), extracted by
[`tools/unpak.py`](../tools/unpak.py). Reader in the engine: `CCustomPakReader::scanLocalHeader`
and `CCustomPakReader::openFile` in `libgalaxyempire.so`.

```
0x00  u32 magic       0x9D7FE2AB  (bytes AB E2 7F 9D)
0x04  u32 names_end   end of the name table (= start of the data area)
0x08  u32 names_off   start of the name table (= end of the index)
0x0C  u32 count       number of entries
0x10  index           count entries of 16 bytes:
                        u32 data_off
                        u32 data_size
                        u32 name_off   offset into the name table
                        u32 flags
      names           NUL-separated, obfuscated, lower-cased by the engine after decoding
      data
```

Flags (`flags & 0xD` = 0 means stored as-is):

| Bit   | Meaning |
|-------|---------|
| `0x1` | encrypted with the same cipher as the name table (not used in 1.9.36) |
| `0x4` | zlib: `u32 raw_size` followed by a zlib stream |
| `0x8` | set on all script entries, meaning unknown |

- In `data1.pak` entry 0 is `AutoUpdateInit.lua`, the first script the engine runs.
- Lua entries are plain Lua 5.1 bytecode (`1B 4C 75 61 51`, 4-byte int/instruction, 8-byte
  double; size_t is 4 bytes in `data1.pak` and 8 bytes in `data1_64.pak`). They contain their
  own source name (`@./GameUtils.lua`), so the obfuscated name table can be ignored.
- The bytecode decompiles cleanly with [unluac](https://sourceforge.net/projects/unluac/).
  [`tools/lua51.py`](../tools/lua51.py) parses and rewrites it byte-for-byte, which the patcher
  uses to change string constants.
