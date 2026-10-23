"""Read and write Lua 5.1 bytecode chunks, enough to edit string constants.

Supports the format used by the game: little-endian, 4-byte int, 4-byte size_t,
4-byte instructions, 8-byte double numbers (32-bit build) or 8-byte size_t (64-bit build).
"""

import struct
from dataclasses import dataclass, field

SIGNATURE = b"\x1bLuaQ"

NIL, BOOLEAN, NUMBER, STRING = 0, 1, 3, 4


@dataclass
class Function:
    source: bytes | None
    line_defined: int
    last_line_defined: int
    num_upvalues: int
    num_params: int
    is_vararg: int
    max_stack: int
    code: bytes
    constants: list = field(default_factory=list)
    protos: list["Function"] = field(default_factory=list)
    line_info: bytes = b""
    loc_vars: list = field(default_factory=list)
    upvalues: list = field(default_factory=list)

    def walk(self):
        yield self
        for proto in self.protos:
            yield from proto.walk()


class _Reader:
    def __init__(self, data: bytes, size_t: int):
        self.data, self.pos, self.size_t = data, 0, size_t

    def take(self, n: int) -> bytes:
        chunk = self.data[self.pos : self.pos + n]
        self.pos += n
        return chunk

    def byte(self) -> int:
        return self.take(1)[0]

    def int(self) -> int:
        return struct.unpack("<i", self.take(4))[0]

    def string(self) -> bytes | None:
        length = int.from_bytes(self.take(self.size_t), "little")
        return self.take(length)[:-1] if length else None

    def function(self) -> Function:
        fn = Function(
            source=self.string(),
            line_defined=self.int(),
            last_line_defined=self.int(),
            num_upvalues=self.byte(),
            num_params=self.byte(),
            is_vararg=self.byte(),
            max_stack=self.byte(),
            code=self.take(self.int() * 4),
        )
        for _ in range(self.int()):
            kind = self.byte()
            if kind == NIL:
                fn.constants.append(None)
            elif kind == BOOLEAN:
                fn.constants.append(bool(self.byte()))
            elif kind == NUMBER:
                fn.constants.append(struct.unpack("<d", self.take(8))[0])
            elif kind == STRING:
                fn.constants.append(self.string())
            else:
                raise ValueError(f"unknown constant type {kind}")
        fn.protos = [self.function() for _ in range(self.int())]
        fn.line_info = self.take(self.int() * 4)
        fn.loc_vars = [(self.string(), self.int(), self.int()) for _ in range(self.int())]
        fn.upvalues = [self.string() for _ in range(self.int())]
        return fn


class _Writer:
    def __init__(self, size_t: int):
        self.parts, self.size_t = [], size_t

    def int(self, value: int) -> None:
        self.parts.append(struct.pack("<i", value))

    def string(self, value: bytes | None) -> None:
        if value is None:
            self.parts.append(bytes(self.size_t))
        else:
            self.parts.append((len(value) + 1).to_bytes(self.size_t, "little") + value + b"\0")

    def function(self, fn: Function) -> None:
        self.string(fn.source)
        self.int(fn.line_defined)
        self.int(fn.last_line_defined)
        self.parts.append(bytes([fn.num_upvalues, fn.num_params, fn.is_vararg, fn.max_stack]))
        self.int(len(fn.code) // 4)
        self.parts.append(fn.code)
        self.int(len(fn.constants))
        for const in fn.constants:
            if const is None:
                self.parts.append(bytes([NIL]))
            elif isinstance(const, bool):
                self.parts.append(bytes([BOOLEAN, const]))
            elif isinstance(const, float):
                self.parts.append(bytes([NUMBER]) + struct.pack("<d", const))
            else:
                self.parts.append(bytes([STRING]))
                self.string(const)
        self.int(len(fn.protos))
        for proto in fn.protos:
            self.function(proto)
        self.int(len(fn.line_info) // 4)
        self.parts.append(fn.line_info)
        self.int(len(fn.loc_vars))
        for name, start, end in fn.loc_vars:
            self.string(name)
            self.int(start)
            self.int(end)
        self.int(len(fn.upvalues))
        for name in fn.upvalues:
            self.string(name)


def load(chunk: bytes) -> tuple[bytes, Function]:
    """Parse a chunk into (header, main function)."""
    if not chunk.startswith(SIGNATURE):
        raise ValueError("not a Lua 5.1 chunk")
    header = chunk[:12]
    reader = _Reader(chunk, size_t=header[8])
    reader.pos = 12
    main = reader.function()
    if reader.pos != len(chunk):
        raise ValueError(f"trailing data after chunk ({len(chunk) - reader.pos} bytes)")
    return header, main


def dump(header: bytes, main: Function) -> bytes:
    writer = _Writer(size_t=header[8])
    writer.function(main)
    return header + b"".join(writer.parts)
