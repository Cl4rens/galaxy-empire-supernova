"""Game script tweaks, on a small Lua chunk shaped like GameUICityUIUp."""

import struct
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import client_tweaks  # noqa: E402
import lua51  # noqa: E402

HEADER = b"\x1bLuaQ\x00\x01\x04\x04\x04\x08\x00"


def ins(op: int, a: int, b: int = 0, c: int = 0) -> bytes:
    return struct.pack("<I", op | a << 6 | c << 14 | b << 23)


def chunk(source: bytes, set_visible_code: bytes) -> bytes:
    """main() defines one function like SetTap4funButtonVisible: self:CallAS(nil, name, false)."""
    fn = lua51.Function(None, 145, 147, 0, 1, 0, 6, set_visible_code, [b"CallAS", b"setTap4funButtonVisible"])
    other = lua51.Function(None, 10, 12, 0, 1, 0, 2, ins(2, 1) + ins(30, 0, 1), [b"Other"])  # LOADBOOL false
    main = lua51.Function(source, 0, 0, 0, 0, 2, 2, ins(30, 0, 1), [], [other, fn])
    return lua51.dump(HEADER, main)


ORIGINAL = ins(11, 1, 0, 256) + ins(3, 3, 3) + ins(1, 4, 1) + ins(2, 5, 0, 0) + ins(28, 1, 5, 1) + ins(30, 0, 1)


def test_daily_tasks_button_is_shown():
    patched = client_tweaks.apply(chunk(b"@./GameUICityUIUp.lua", ORIGINAL))
    _, main = lua51.load(patched)
    other, fn = main.protos
    loadbool = struct.unpack_from("<I", fn.code, 12)[0]
    assert loadbool & 0x3F == 2 and loadbool >> 23 == 1  # true now
    assert fn.code[:12] == ORIGINAL[:12] and fn.code[16:] == ORIGINAL[16:]
    assert other.code == ins(2, 1) + ins(30, 0, 1)  # other functions keep their false


def test_other_scripts_are_untouched_and_changes_are_detected():
    original = chunk(b"@./GameUIMail.lua", ORIGINAL)
    assert client_tweaks.apply(original) == original
    with pytest.raises(RuntimeError):  # the expected function is missing
        client_tweaks.apply(chunk(b"@./GameUICityUIUp.lua", ins(30, 0, 1)).replace(b"setTap4fun", b"setTap5fun"))


def jmp(offset: int) -> bytes:
    return struct.pack("<I", 22 | (offset + 131071) << 14)


def test_arena_shop_skips_missing_icons():
    """`if id ~= nil or tonumber(id) ~= -1 then self:UpdateFlashTexture(...) end` becomes `and`."""
    constants = [b"tonumber", None, -1.0, b"UpdateFlashTexture", b"updateExList"]
    code = (ins(23, 0, 3, 256 + 1) + jmp(4)  # 0: EQ id nil, 1: JMP -> 6 (the call)
            + ins(5, 4, 0, 0) + ins(28, 4, 2, 2)  # 2-3: tonumber(id)
            + ins(23, 1, 4, 256 + 2) + jmp(3)  # 4: EQ id -1, 5: JMP -> 9 (after the if)
            + ins(11, 5, 0, 256 + 3) + ins(0, 7, 3) + ins(28, 5, 3, 1)  # 6-8: self:UpdateFlashTexture(id)
            + ins(30, 0, 1))  # 9: return
    fn = lua51.Function(None, 1, 9, 0, 3, 0, 8, code, constants)
    main = lua51.Function(b"@./GameUITop.lua", 0, 0, 0, 0, 2, 2, ins(30, 0, 1), [], [fn])
    _, patched = lua51.load(client_tweaks.apply(lua51.dump(HEADER, main)))
    new = [struct.unpack_from("<I", patched.protos[0].code, i)[0] for i in range(0, len(code), 4)]
    assert client_tweaks._decode(new[0])[:4] == (23, 1, 3, 257)  # nil: run the JMP
    assert client_tweaks._decode(new[1])[4] == 7  # ... past the if (instruction 9)
    assert patched.protos[0].code[8:] == code[8:]


def test_replaced_screens_run_supernova_source():
    original = chunk(b"@./GameUIFacebookChests.lua", ORIGINAL)
    replaced = client_tweaks.apply(original)
    header, main = lua51.load(replaced)
    assert header == HEADER and main.constants[:2] == [b"assert", b"loadstring"]
    assert main.constants[2] == (client_tweaks.SCRIPTS_DIR / "GameUIFacebookChests.lua").read_bytes()
    assert main.constants[3] == b"@supernova/GameUIFacebookChests.lua"
    ops = [struct.unpack_from("<I", main.code, i)[0] & 0x3F for i in range(0, len(main.code), 4)]
    assert ops == [5, 5, 1, 1, 28, 28, 28, 30]  # GETGLOBAL x2, LOADK x2, CALL x3, RETURN
