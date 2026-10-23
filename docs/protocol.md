# Network protocol

Notes on how the client talks to the server. Observed with client 1.9.36 running in
BlueStacks 5 (Android 7.1.1, ARM translation via houdini).

## Startup sequence

1. The client extracts `assets/tap4fun.zip` to
   `/storage/emulated/0/tap4fun/galaxy_empire/AppOriginalData/Android/` on first start.
2. It tries to load the Lua files `NEWUSER.tfl` and `lfl.tfl` from that folder (missing on a
   fresh install, which is not an error).
3. It requests the **server list / update info** over HTTPS (see below).
4. The official endpoint now answers `404 Not Found`. The client shows the error
   *"Server data update failed!"* and stops there.

## `GET /ge/url_get/online/world/servers/auto_update`

- **Host:** `b.portal-platform.tap4fun.com`
- **Scheme:** HTTPS. The engine's HTTP module logs `EXT_HTTP_MODE_NOT_ENCRYPT`, so the body
  itself is not encrypted on top of TLS.
- **Timeout:** 20 s
- **Response format:** unknown so far (`EXT_HTTP_UNKNOWN`, i.e. raw text handed to Lua).
  The expected body has to be derived from the Lua scripts.

Request headers (values are examples):

| Header            | Example                            | Notes |
|-------------------|------------------------------------|-------|
| `openUDID`        | 32 hex chars                       | device identifier |
| `DeviceToken`     | same as `openUDID`                 | |
| `AndroidId`       | 16 hex chars                       | |
| `MACAddress`      | `02:00:00:00:00:00`                | always this value on modern Android |
| `Locale`          | `de`                               | game language |
| `BundleId`        | `com.tap4fun.ge`                   | |
| `platform`        | `Android`                          | |
| `Appid`           | `500099330`                        | |
| `clientv`         | `1.9.36`                           | client version |
| `resv`            | `6507`                             | resource version |
| `IosVersion`      | `7.1.1`                            | actually the Android version |
| `Model`           | `<maker>_<model>_<device>`         | |
| `shiqu`           | `Europe/Berlin`                    | time zone (Chinese *shíqū*) |
| `sig`             | 32 hex chars                       | probably an MD5 signature, algorithm unknown |
| `requestindex`    | URL-encoded Base64, 16 bytes       | probably AES-encrypted counter or nonce |
| `User-Agent`      | `Tap4fun client`                   | |
| `Accept-Encoding` | `gzip`                             | |

## Open questions

- What body does `auto_update` have to return? (Lua scripts)
- How are `sig` and `requestindex` computed, and does the server need to check them?
- After the server list: which host/port does the game connect to, and is that a raw TCP socket
  (`ext.socket`) with its own framing?
- Redirecting HTTPS: the client must either trust our certificate or be patched to use plain
  HTTP / a different host.
