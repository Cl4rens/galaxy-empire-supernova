# Running the client on current Android versions

The last Galaxy Empire client (1.9.36) was built in 2018 for Android 4–7: `targetSdkVersion` 24,
native code for 32-bit ARM (`armeabi-v7a`) only. This page lists what breaks on newer devices
and what the patcher does about it.

## What the patcher changes (`tools/compat.py`)

| Problem | Effect on the original client | Patch |
|---------|-------------------------------|-------|
| Crash at the first start on Android 6+: `onStart()` starts a Flurry session before the Flurry key is set, which only happens after the permission dialog | "Galaxy Empire has stopped" until the player grants the permissions in the system settings | The setup of the tracking SDKs (Flurry, MobileAppTracker, AppsFlyer, Umeng) becomes empty and all calls into them (and into Facebook app events) are removed. Their servers are gone anyway. |
| Game data on the shared storage (`/sdcard/tap4fun/galaxy_empire`), which needs `WRITE_EXTERNAL_STORAGE` | Permission dialog; on newer Android versions access to `/sdcard` keeps getting more restricted | All uses of `Environment.getExternalStorageDirectory()` in the game code point to the app's own folder `Android/data/com.tap4fun.ge/files`, which needs no permission. |
| The client asks for "phone" (`READ_PHONE_STATE`) for the IMEI of a tracking SDK | Permission dialog; refusing it closes the game | No permission dialog any more. The device ID the server sees is still `ANDROID_ID`. |

The patches work on the disassembled `classes.dex` (smali, via
[apktool](https://github.com/iBotPeaches/Apktool), downloaded on first use). The patcher
checks that every patch found its place and stops otherwise. `--no-compat` builds the APK with
the original code.

Moving the data folder means a patched client starts like a fresh install: it unpacks its game
data again and does not know the last guest account. Registered accounts log in with name and
password as usual.

## Tested

| Device | Android | Result |
|--------|---------|--------|
| BlueStacks 5 (Nougat32), all runtime permissions revoked, data cleared | 7.1.1 | starts, no permission dialog, plays |

Reports from real devices are welcome (see "Contributing" in the README).

## Known limits

- **64-bit-only devices cannot run the client.** The game engine (`libgalaxyempire.so`) exists
  only for 32-bit ARM. Phones whose CPU has no 32-bit mode (for example Google Pixel 7 and
  newer, and many phones with chips from 2023 on) cannot load it, and no patch can change that
  without the engine's source. Check a device with
  `adb shell getprop ro.product.cpu.abilist`: it needs `armeabi-v7a` in the list.
  `data1_64.pak` in the assets shows that tap4fun had a 64-bit engine (iOS); an Android build of
  it is not known. On a PC, emulators such as BlueStacks run the client.
- **`targetSdkVersion` 24** is the lowest version Android 15 and 16 still install (Android 14:
  23). Apps already installed before a system update keep working. If a future Android version
  raises that limit (install error `INSTALL_FAILED_DEPRECATED_SDK_VERSION`), the manifest has to be raised too, which changes how Android treats
  the app (cleartext HTTP, background services, notifications) and needs more patches.
- The server connection is plain HTTP. Android allows that for apps with `targetSdkVersion`
  below 28, so it works; a server behind HTTPS needs a valid certificate.
