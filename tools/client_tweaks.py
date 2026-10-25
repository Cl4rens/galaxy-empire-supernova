"""Small changes to the game's Lua scripts that a Supernova server needs.

Each tweak finds one function by the string constants it uses and changes single bytecode
instructions, so it does not depend on entry numbers inside the pak.
"""

import struct

import lua51

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


# source file -> tweaks for its functions
TWEAKS = {"GameUICityUIUp.lua": [show_daily_tasks], "GameUITop.lua": [arena_shop_icons]}


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
