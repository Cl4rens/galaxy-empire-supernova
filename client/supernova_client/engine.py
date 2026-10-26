"""The Supernova client engine: runs the game's own Lua scripts (from the player's APK) in a
64-bit Lua 5.1 VM and provides the native API they expect.

This is the headless core. It has no renderer yet: Flash objects record the calls the game
makes on them (CallAS), and a driver can send the commands a Flash button would send
(fscommand). See client/README.md.
"""

import base64
import collections
import hashlib
import http.cookiejar
import json
import logging
import queue
import re
import struct
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
import zlib
from pathlib import Path

from lupa.lua51 import LuaRuntime

from .assets import Assets

log = logging.getLogger("supernova.client")
PRELUDE = (Path(__file__).with_name("prelude.lua")).read_text(encoding="utf-8")

# Where the client key is in the supported APK (see server/supernova/crypto.py)
KEY_LOCATIONS = [("lib/armeabi-v7a/libgalaxyempire.so", 0x1B1EA5, 32,
                  "9a0e7ff690dbe1cf60884aaad9cdc3c10d9637eb1b9d3e0dd9c092a431fe7b08")]
# The APK's file lists (ofl.tfl, fl.tfl) are obfuscated with an unknown cipher. The updater only
# needs the resource version of the build (the real client sends it as `resv`).
GENERATED_FILES = {"ofl.tfl": b"originfilelist = {version = 6507}", "ofl_64.tfl": b"originfilelist = {version = 6507}"}
TEXT_PREFIXES = {"menu": "LC_MENU_", "building": "LC_BUILDING_", "rank": "LC_RANK_", "contactus": "LC_CONTACTUS_"}


def client_key(apk: Path) -> bytes:
    with zipfile.ZipFile(apk) as z:
        names = set(z.namelist())
        for member, offset, length, digest in KEY_LOCATIONS:
            if member in names:
                key = z.read(member)[offset:offset + length]
                if hashlib.sha256(key).hexdigest() == digest:
                    return key
    raise ValueError(f"{apk}: no known client key")


def _aes(key: bytes, data: bytes, decrypt: bool) -> bytes:
    from cryptography.hazmat.primitives import padding
    from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
    cipher = Cipher(algorithms.AES(key), modes.CBC(bytes(16)))
    if decrypt:
        d = cipher.decryptor()
        raw = d.update(data) + d.finalize()
        u = padding.PKCS7(128).unpadder()
        return u.update(raw) + u.finalize()
    p = padding.PKCS7(128).padder()
    e = cipher.encryptor()
    return e.update(p.update(data) + p.finalize()) + e.finalize()




class Engine:
    def __init__(self, apk: Path, server: str, save_dir: Path, udid: str | None = None, language: str = "en",
                 tweaks=None, key: bytes | None = None):
        self.assets = Assets(apk)
        self.key = key or client_key(apk)  # the response key (tests pass their own)
        self.server = server.rstrip("/") + "/"
        self.save_dir = Path(save_dir)
        self.save_dir.mkdir(parents=True, exist_ok=True)
        udid_file = self.save_dir / "UDID"
        if udid is None:
            udid = udid_file.read_text().strip() if udid_file.exists() else uuid.uuid4().hex.upper()
        udid_file.write_text(udid)
        self._udid = udid
        self._language = language
        self.tweaks = tweaks
        self.texts: dict[str, str] = {}
        self.calls: collections.deque = collections.deque(maxlen=5000)  # (class, method, args)
        self.missing: collections.Counter = collections.Counter()
        self.alerts: list[tuple[str, str]] = []
        self.requests: list[tuple[str, str, int]] = []  # (method, url, status)
        self._done: queue.Queue = queue.Queue()
        self._pending = 0
        self._cookies = http.cookiejar.CookieJar()
        self._opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self._cookies))
        self.clock_ms = time.time() * 1000
        self.lua = LuaRuntime(unpack_returned_tuples=True, encoding=None)
        self.lua.execute(PRELUDE.encode("utf-8"), self._native())

    # --- the native object the prelude talks to -------------------------------------------------

    def _native(self):
        engine = self
        lua = self.lua

        class Native:
            @staticmethod
            def missing(name, *args):
                name = name.decode() if isinstance(name, bytes) else name
                if not engine.missing[name]:
                    log.info("missing native %s", name)
                engine.missing[name] += 1
                return None

            @staticmethod
            def missing_global(name):
                name = name.decode() if isinstance(name, bytes) else str(name)
                engine.missing["global " + name] += 1

            udid = staticmethod(lambda *_: engine._udid.encode())
            language = staticmethod(lambda *_: engine._language.encode())
            now = staticmethod(lambda *_: engine.clock_ms)

            @staticmethod
            def log(text):
                log.debug("lua: %s", text)

            @staticmethod
            def alert(title, text):
                engine.alerts.append((title, text))

            @staticmethod
            def save(name, text=None):
                if text is None:
                    log.warning("save(%r) without text: %s", name, engine.lua.eval("debug.traceback")())
                    return
                (engine.save_dir / _safe(name)).write_bytes(text if isinstance(text, bytes) else str(text).encode())

            @staticmethod
            def load(name):
                name = name.decode() if isinstance(name, bytes) else str(name)
                base = name.replace("\\", "/").rsplit("/", 1)[-1]
                if base in GENERATED_FILES:
                    return GENERATED_FILES[base]
                path = engine.save_dir / _safe(name)
                return path.read_bytes() if path.exists() else None

            @staticmethod
            def script(name):
                chunk = engine.assets.script(name.decode() if isinstance(name, bytes) else name)
                if chunk is not None and engine.tweaks is not None:
                    chunk = engine.tweaks(chunk)
                return chunk

            @staticmethod
            def crc(text):
                return zlib.crc32(text if isinstance(text, bytes) else str(text).encode())

            @staticmethod
            def md5(text):
                return hashlib.md5(text if isinstance(text, bytes) else str(text).encode()).hexdigest().upper().encode()

            @staticmethod
            def enc2(key, text):
                data = text if isinstance(text, bytes) else str(text).encode()
                key = key if isinstance(key, bytes) else engine.key
                return base64.b64encode(_aes(key[:32], data, False))

            @staticmethod
            def copy(text):
                engine.clipboard = text

            @staticmethod
            def audio(command, value=None):
                pass

            @staticmethod
            def keyboard(show, input_type=None):
                engine.keyboard = bool(show)

            @staticmethod
            def json_parse(text):
                return engine.to_lua(json.loads(text))

            @staticmethod
            def json_generate(value):
                return json.dumps(engine.from_lua(value)).encode()

            @staticmethod
            def http(name, url, param, body, method, mode, header, localpath):
                engine._http(name, url, param, body, method, mode, header, localpath)

            @staticmethod
            def flash(*args):
                if len(args) < 2:
                    log.warning("flash%r: %s", args, engine.lua.eval("debug.traceback")())
                    return None
                return engine._flash(args[0], args[1], args[2:])

            @staticmethod
            def load_text(locale=None, name=None):
                if locale is None or name is None:
                    log.info("load_text(%r, %r) %s", locale, name, engine.lua.eval("debug.traceback")())
                    return False
                return engine._load_text(locale, name)

            @staticmethod
            def text(key):
                key = key.decode() if isinstance(key, bytes) else key
                return engine.texts.get(key, key).encode()

        # A real Lua table of functions: lupa's attribute lookup on Python objects gave wrong
        # methods now and then (e.g. native.now() ran flash()).
        functions = {name.encode(): getattr(Native, name) for name in vars(Native) if not name.startswith("_")}
        return lua.table_from(functions)

    # --- conversions -----------------------------------------------------------------------------

    def to_lua(self, value):
        if isinstance(value, dict):
            return self.lua.table_from({(k.encode("utf-8") if isinstance(k, str) else k): self.to_lua(v)
                                        for k, v in value.items()})
        if isinstance(value, list):
            return self.lua.table_from([self.to_lua(v) for v in value])
        if isinstance(value, str):
            return value.encode("utf-8")
        return value

    def from_lua(self, value):
        if self.lua.eval("type")(value) == b"table":
            items = list(value.items())
            keys = [k for k, _ in items]
            if keys and all(isinstance(k, int) for k in keys) and sorted(keys) == list(range(1, len(keys) + 1)):
                return [self.from_lua(v) for _, v in sorted(items)]
            return {(k.decode() if isinstance(k, bytes) else str(k)): self.from_lua(v) for k, v in items}
        if isinstance(value, bytes):
            return value.decode("utf-8", "replace")
        return value

    # --- HTTP --------------------------------------------------------------------------------------

    def _rewrite(self, url: str) -> str:
        """The scripts carry tap4fun's addresses; every request goes to the Supernova server."""
        url = re.sub(r"^https?://b\.portal-platform\.(tap4fun\.com|t4f\.cn)/ge/url_get/online/world/", self.server, url)
        return re.sub(r"^https://", "http://", url)

    def _http(self, name, url, param, body, method, mode, header, localpath):
        url = self._rewrite(url.decode() if isinstance(url, bytes) else url)
        param = param.decode() if isinstance(param, bytes) else param
        body = body.decode() if isinstance(body, bytes) else body
        method = (method.decode() if isinstance(method, bytes) else method).upper()
        mode = mode.decode() if isinstance(mode, bytes) else mode
        headers = {k.decode() if isinstance(k, bytes) else str(k): v.decode() if isinstance(v, bytes) else str(v)
                   for k, v in header.items()}
        headers.pop("Content-Length", None)
        headers.pop("Accept-Encoding", None)
        localpath = localpath.decode() if isinstance(localpath, bytes) else localpath
        full = url + param if method == "GET" else url
        self._pending += 1

        def work():
            try:
                req = urllib.request.Request(full, data=body.encode() if method == "POST" else None, headers=headers,
                                             method=method)
                with self._opener.open(req, timeout=20) as r:
                    status, data = r.status, r.read()
            except urllib.error.HTTPError as e:
                status, data = e.code, e.read()
            except OSError as e:
                self._done.put((name, method, full, 0, None, str(e), mode, localpath))
                return
            self._done.put((name, method, full, status, data, None, mode, localpath))

        threading.Thread(target=work, daemon=True).start()

    def _deliver(self, item):
        name, method, url, status, data, err, mode, localpath = item
        self._pending -= 1
        self.requests.append((method, url, status))
        content = None
        if err is None:
            if mode == "download":
                path = self.save_dir / "downloads" / _safe(localpath or url.rsplit("/", 1)[-1])
                path.parent.mkdir(exist_ok=True)
                path.write_bytes(data)
                content = str(path).encode()
            elif status != 200:
                content = data
            else:
                try:
                    text = _aes(self.key, base64.b64decode(data), True) if mode == "normal" else data
                    content = self.to_lua(json.loads(text))
                except (ValueError, json.JSONDecodeError):
                    content = data
        self.lua.globals()[b"__sn_http_done"](name, status, content, err)

    # --- Flash -------------------------------------------------------------------------------------

    def _flash(self, class_name, method, args):
        class_name = class_name.decode() if isinstance(class_name, bytes) else class_name
        method = method.decode() if isinstance(method, bytes) else method
        self.calls.append((class_name, method, args))
        if method == "ReplaceTexture":
            return 1, 1  # "the picture exists": no downloads from the server
        if method in ("GetPositionX", "GetPositionY", "GetWidth", "GetHeight"):
            return 0
        return None

    def fscommand(self, class_name: str, cmd: str, arg: str = "") -> None:
        """What a Flash file does when the player presses something: it sends a command."""
        cls = self.lua.globals()[b"__sn_flash_classes"][class_name.encode()]
        if cls is None or cls.fscommand is None:
            raise KeyError(f"no Flash class {class_name} with an fscommand handler")
        cls.fscommand(self.lua.table_from({}), cmd.encode(), arg.encode())

    def press(self, object_name: str, cmd: str, arg: str = "") -> None:
        """Send a Flash command to one of the game's screen objects (LuaObjectManager name)."""
        class_name = self.lua.eval("""function(name)
            local object = LuaObjectManager:GetLuaObject(name)
            return object and object:GetFlashObjectClassName()
        end""")(object_name.encode())
        if class_name is None:
            raise KeyError(object_name)
        self.fscommand(class_name.decode(), cmd, arg)

    def active_objects(self) -> list[str]:
        """The screen objects in the current game state (top last)."""
        names = self.lua.eval("""function()
            local out = {}
            if rawget(_G, "GameStateManager") == nil then return "" end
            local state = GameStateManager:GetCurrentGameState()
            for _, object in ipairs(state and state.m_menuObjectList or {}) do
              table.insert(out, object.m_objectName or "?")
            end
            return table.concat(out, ",")
        end""")()
        return [n for n in (names or b"").decode().split(",") if n]

    def as_calls(self, class_name: str | None = None, method: str = "InvokeASCallback") -> list[list]:
        """The ActionScript calls made so far, e.g. [["_root", "setText", ...]]."""
        return [[a.decode("utf-8", "replace") if isinstance(a, bytes) else a for a in args]
                for c, m, args in self.calls if m == method and (class_name is None or c == class_name)]

    # --- texts -------------------------------------------------------------------------------------

    def _load_text(self, locale, name) -> bool:
        locale = locale.decode() if isinstance(locale, bytes) else locale
        name = name.decode() if isinstance(name, bytes) else name
        index, values = self.assets.file(f"data2/text/{name}.idx"), self.assets.file(f"data2/text/{name}.{locale}")
        if index is None or values is None:
            return False
        prefix = TEXT_PREFIXES.get(name, f"LC_{name.upper()}_")
        for key, value in zip(_strings(index), _strings(values)):
            self.texts[prefix + key] = value
        return True

    # --- running -----------------------------------------------------------------------------------

    def boot(self) -> None:
        """Run AutoUpdateInit, the script the native engine starts with."""
        chunk = self.assets.script("AutoUpdateInit")
        if self.tweaks is not None:
            chunk = self.tweaks(chunk)
        self.lua.execute(chunk)

    def step(self, dt_ms: float | None = None) -> None:
        """One frame: deliver finished requests, then let the game update. The game clock is
        real time (timers and the heartbeat depend on it)."""
        now = time.time() * 1000
        dt_ms = now - self.clock_ms if dt_ms is None else dt_ms
        self.clock_ms = now
        while True:
            try:
                item = self._done.get_nowait()
            except queue.Empty:
                break
            self._deliver(item)
        update = self.lua.globals()[b"Update"]
        if update is not None:
            update(dt_ms)

    def run(self, seconds: float, until=None, fps: int = 30) -> bool:
        """Run the game for `seconds`; stop early when `until()` is true."""
        end = time.time() + seconds
        while time.time() < end:
            self.step()
            if until is not None and until():
                return True
            time.sleep(1 / fps)
        return False

    def state_name(self) -> str | None:
        """The name of the game state the GameStateManager is in, if any."""
        found = self.lua.eval("""function()
            if rawget(_G, "GameStateManager") == nil then return nil end
            local current = GameStateManager:GetCurrentGameState()
            for k, v in pairs(GameStateManager) do
              if v == current and type(k) == "string" and string.sub(k, 1, 9) == "GameState" then return k end
            end
            return nil
        end""")()
        return found.decode() if isinstance(found, bytes) else found


def _safe(name) -> str:
    name = name.decode() if isinstance(name, bytes) else str(name)
    return re.sub(r"[^A-Za-z0-9_.-]", "_", name.replace("/", "__"))


def _strings(data: bytes) -> list[str]:
    """A text table file: u16 count, then u16-length-prefixed UTF-8 strings."""
    out, off = [], 2
    while off + 2 <= len(data):
        n = struct.unpack_from("<H", data, off)[0]
        off += 2
        out.append(data[off:off + n].decode("utf-8", "replace"))
        off += n
    return out
