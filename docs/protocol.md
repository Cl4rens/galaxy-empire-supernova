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

## Game requests

`GameUtils:SendMsg(path, params, ...)` → `ext.http.request` → `ExtHttpConnection`.

- **URL:** `<server><path>` where `<server>` is
  - `AutoUpdate.gameGateway` for `servers`, `users/get_user_server` and `faqs/game_faq`
    (mode `notencrypt`),
  - otherwise `GameGlobalData:GetCurrentServerURL()`, the game world the player picked
    (mode `normal`).
- **Method:** `GET` by default, parameters as query string `k=v&k=v`. For `POST` the
  parameters go into the body as `application/x-www-form-urlencoded`.
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

### Error handling

Any status other than 200 shows an error box. If the body is JSON with a `message` field, that
text is shown with the status code appended.

## Open questions

- Login and registration flow: which endpoints, which fields.
- `ENC1` / `ENC2`: exact algorithms (MD5? AES?) in case we want to validate requests.
- Are request parameters encrypted in mode `normal` too? (Native code looks up
  `aesDefaultEncrypt`; logcat prints `NetUtils src: ... key: ...` when it is called.)
