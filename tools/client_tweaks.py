"""Small changes to the game's Lua scripts that a Supernova server needs.

Each tweak finds one function by the string constants it uses and changes single bytecode
instructions, so it does not depend on entry numbers inside the pak.
"""

import struct
from pathlib import Path

import lua51

# Supernova's own versions of whole screens; they replace the script of the same name.
SCRIPTS_DIR = Path(__file__).resolve().parent / "client_scripts"

OP_LOADBOOL, OP_SELF, OP_JMP, OP_EQ = 2, 11, 22, 23
RK_CONSTANT = 256  # B/C operands from this value on name constants


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


OP_LOADK, OP_GETGLOBAL, OP_CALL, OP_RETURN = 1, 5, 28, 30


def _abc(op: int, a: int, b: int = 0, c: int = 0) -> bytes:
    return struct.pack("<I", op | a << 6 | c << 14 | b << 23)


def _abx(op: int, a: int, bx: int) -> bytes:
    return struct.pack("<I", op | a << 6 | bx << 14)


def source_chunk(header: bytes, name: bytes, source: bytes) -> bytes:
    """A bytecode chunk that runs Lua source: `assert(loadstring(source, name))()`.

    The engine also accepts source text in its paks, but its reader hands the text over again
    and again, so the parser never sees the end. Bytecode ends by itself, so a small compiled
    main function carries Supernova's scripts as a string."""
    code = (_abx(OP_GETGLOBAL, 0, 0) + _abx(OP_GETGLOBAL, 1, 1) + _abx(OP_LOADK, 2, 2) + _abx(OP_LOADK, 3, 3)
            + _abc(OP_CALL, 1, 3, 0)  # loadstring(source, name), all results
            + _abc(OP_CALL, 0, 0, 2)  # assert(...)
            + _abc(OP_CALL, 0, 1, 1)  # run it
            + _abc(OP_RETURN, 0, 1))
    main = lua51.Function(name, 0, 0, 0, 0, 2, 4, code, [b"assert", b"loadstring", source, name])
    return lua51.dump(header, main)


def _decode(ins: int) -> tuple[int, int, int, int, int]:
    """(op, A, B, C, sBx)"""
    return ins & 0x3F, ins >> 6 & 0xFF, ins >> 23, ins >> 14 & 0x1FF, (ins >> 14) - 131071


def arena_shop_icons(fn: lua51.Function) -> bool:
    """GameUITop:hanglde_updateExchangeItem() checks `if textureId ~= nil or tonumber(textureId)
    ~= -1 then self:UpdateFlashTexture(textureId, ...)`, which is always true. On tall screens
    the shop list has more entries than icon slots, an entry gets no texture id and the client
    stops with an error. Make the first test skip the icon when the id is nil (`and`)."""
    if b"updateExList" not in fn.constants or b"UpdateFlashTexture" not in fn.constants:
        return False
    nil_k = RK_CONSTANT + fn.constants.index(None)
    update_k = RK_CONSTANT + fn.constants.index(b"UpdateFlashTexture")
    code = [struct.unpack_from("<I", fn.code, pos)[0] for pos in range(0, len(fn.code), 4)]
    for i in range(len(code) - 6):
        op, a, b, c, _ = _decode(code[i])
        jop, _, _, _, jump = _decode(code[i + 1])
        if not (op == OP_EQ and a == 0 and c == nil_k and jop == OP_JMP):
            continue
        body = i + 2 + jump  # where `textureId ~= nil` jumps: the call of UpdateFlashTexture
        if _decode(code[body])[0] != OP_SELF or _decode(code[body])[3] != update_k:
            continue
        # the second test's JMP skips the body; its target is the end of the if
        second = next(j for j in range(i + 2, body) if _decode(code[j])[0] == OP_JMP)
        end = second + 1 + _decode(code[second])[4]
        code[i] |= 1 << 6  # EQ A=1: fall through to the second test when textureId is not nil
        code[i + 1] = OP_JMP | (end - (i + 2) + 131071) << 14  # nil: jump past the if
        fn.code = b"".join(struct.pack("<I", x) for x in code)
        return True
    return False


VIP_MAX_LEVEL = 6  # Supernova's VIP has 6 levels (server/supernova/vip.py), the client 8


def _number(marker: bytes, old: float, new: float):
    """A tweak that changes the number constant `old` to `new` in the function that also has
    the string constant `marker`."""
    def tweak(fn: lua51.Function) -> bool:
        if marker not in fn.constants or old not in fn.constants:
            return False
        fn.constants[fn.constants.index(old)] = new
        return True
    tweak.__name__ = f"{marker.decode()}: {old:g} -> {new:g}"
    return tweak


# The VIP screens show levels 1 to 8; Supernova has 6: the highest level (the main chunk's
# MAX_vip_level), the main list (entries 3 to 10: levels 2 to 9, with spacers), the level a list
# entry opens, and the pages of the level view.
VIP_MAIN = [
    _number(b"UpdateInterval", 8.0, float(VIP_MAX_LEVEL)),
    _number(b"VIP INFO 22222", 10.0, float(VIP_MAX_LEVEL + 1)),
    _number(b"VIP_View", 8.0, float(VIP_MAX_LEVEL)),
]
VIP_VIEW = [_number(b"MoveIn", 8.0, float(VIP_MAX_LEVEL))]

# source file -> tweaks for its functions
TWEAKS = {"GameUICityUIUp.lua": [show_daily_tasks], "GameUITop.lua": [arena_shop_icons],
          "GameUIVIPMain.lua": VIP_MAIN, "GameUIVIPView.lua": VIP_VIEW}


def replacement(chunk: bytes) -> Path | None:
    name = _source(chunk).rsplit("/", 1)[-1]
    path = SCRIPTS_DIR / name
    return path if name.endswith(".lua") and path.is_file() else None


def apply(chunk: bytes) -> bytes:
    """Apply the tweaks that belong to this script; other scripts come back unchanged."""
    script = replacement(chunk)
    if script is not None:
        return source_chunk(chunk[:12], f"@supernova/{script.name}".encode(), script.read_bytes())
    tweaks = next((t for name, t in TWEAKS.items() if _source(chunk).endswith(name)), None)
    if tweaks is None:
        return chunk
    header, main = lua51.load(chunk)
    done = {tweak for fn in main.walk() for tweak in tweaks if tweak(fn)}
    missing = [t.__name__ for t in tweaks if t not in done]
    if missing:
        raise RuntimeError(f"{_source(chunk)} not as expected: {', '.join(missing)}")
    return lua51.dump(header, main)
