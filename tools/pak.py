"""Read and write tap4fun .pak archives (magic AB E2 7F 9D). See docs/file-formats.md."""

import struct
import zlib
from dataclasses import dataclass

MAGIC = 0x9D7FE2AB
HEADER_SIZE = 0x10
INDEX_ENTRY_SIZE = 16

FLAG_ENCRYPTED = 0x1
FLAG_ZLIB = 0x4


@dataclass
class Entry:
    name_off: int  # offset into the (obfuscated) name table
    flags: int
    data: bytes  # decompressed content


@dataclass
class Pak:
    names: bytes  # obfuscated name table, kept as-is
    entries: list[Entry]


def read(data: bytes) -> Pak:
    magic, names_end, names_off, count = struct.unpack_from("<4I", data, 0)
    if magic != MAGIC:
        raise ValueError(f"not a tap4fun pak (magic {magic:#010x})")
    entries = []
    for i in range(count):
        off, size, name_off, flags = struct.unpack_from("<4I", data, HEADER_SIZE + i * INDEX_ENTRY_SIZE)
        if flags & FLAG_ENCRYPTED:
            raise NotImplementedError(f"entry {i} is encrypted")
        blob = data[off : off + size]
        if flags & FLAG_ZLIB:
            raw_size = struct.unpack_from("<I", blob, 0)[0]
            blob = zlib.decompress(blob[4:])
            if len(blob) != raw_size:
                raise ValueError(f"entry {i}: size mismatch {len(blob)} != {raw_size}")
        entries.append(Entry(name_off, flags, blob))
    return Pak(names=data[names_off:names_end], entries=entries)


def write(pak: Pak) -> bytes:
    names_off = HEADER_SIZE + len(pak.entries) * INDEX_ENTRY_SIZE
    names_end = names_off + len(pak.names)

    index, blobs = [], []
    offset = names_end
    for entry in pak.entries:
        blob = entry.data
        if entry.flags & FLAG_ZLIB:
            blob = struct.pack("<I", len(blob)) + zlib.compress(blob, 9)
        index.append(struct.pack("<4I", offset, len(blob), entry.name_off, entry.flags))
        blobs.append(blob)
        offset += len(blob)

    header = struct.pack("<4I", MAGIC, names_end, names_off, len(pak.entries))
    return header + b"".join(index) + pak.names + b"".join(blobs)
