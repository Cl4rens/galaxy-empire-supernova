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
