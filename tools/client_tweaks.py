"""Small changes to the game's Lua scripts that a Supernova server needs.

Each tweak finds one function by the string constants it uses and changes single bytecode
instructions, so it does not depend on entry numbers inside the pak.
"""

import struct

import lua51

OP_LOADBOOL = 2


def _source(chunk: bytes) -> str:
    size_t = chunk[8]
    length = int.from_bytes(chunk[12:12 + size_t], "little")
    return chunk[12 + size_t:12 + size_t + length].rstrip(b"\0").decode("utf-8", "replace")


def show_daily_tasks(fn: lua51.Function) -> bool:
    """GameUICityUIUp:SetTap4funButtonVisible() always hides the daily tasks button
    (`setTap4funButtonVisible(false)`); make it pass true."""
    if fn.constants != [b"CallAS", b"setTap4funButtonVisible"]:
        return False
    code = bytearray(fn.code)
    for pos in range(0, len(code), 4):
        ins = struct.unpack_from("<I", code, pos)[0]
        if ins & 0x3F == OP_LOADBOOL and ins >> 23 == 0:
            struct.pack_into("<I", code, pos, ins | 1 << 23)  # B = 1: true
            fn.code = bytes(code)
            return True
    return False


# source file -> tweaks for its functions
TWEAKS = {"GameUICityUIUp.lua": [show_daily_tasks]}


def apply(chunk: bytes) -> bytes:
    """Apply the tweaks that belong to this script; other scripts come back unchanged."""
    tweaks = next((t for name, t in TWEAKS.items() if _source(chunk).endswith(name)), None)
    if tweaks is None:
        return chunk
    header, main = lua51.load(chunk)
    done = {tweak for fn in main.walk() for tweak in tweaks if tweak(fn)}
    missing = [t.__name__ for t in tweaks if t not in done]
    if missing:
        raise RuntimeError(f"{_source(chunk)} not as expected: {', '.join(missing)}")
    return lua51.dump(header, main)
