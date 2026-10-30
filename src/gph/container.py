"""Byte-exact container model for GPH files.

File layout (all integers big-endian)::

    offset  size  field
    0       8     db_type        "GDBF2000"
    8       8     name           internal name, space padded ("GEN COMM", "GEN DU  ", "$$$$HA00")
    16      4     stamp          1 zero byte + 3 bytes (version/date-like, see docs/FORMAT.md)
    20      4     length         total file length in 4-byte blocks
    24      ...   sections

Section::

    0       8     name           e.g. "TXUR2008" (4 letter kind + 4 digit version)
    8       4     length         section length in 4-byte blocks, header included
    12      4     count          number of item slots
    16      4*(count+1)          index: offset of every item relative to the start of the
                                 item data, 0xFFFFFFFF for an empty slot; the extra last
                                 entry is the total length of the item data.  When count is
                                 0 there is no index at all.
    ...                          item data, each item a multiple of 4 bytes

Parsing keeps the raw bytes of every item so that ``GphFile.to_bytes()`` reproduces the
input exactly (verified byte-for-byte on all sample files).
"""

from __future__ import annotations

import bz2
import os
import struct
from dataclasses import dataclass, field
from typing import Iterable, Iterator

MAGIC = b"GDBF2000"
EMPTY = 0xFFFFFFFF


class GphError(ValueError):
    pass


@dataclass
class Section:
    name: str
    items: list[bytes | None] = field(default_factory=list)

    # -- identity -----------------------------------------------------------
    @property
    def kind(self) -> str:
        """Four letter section kind, e.g. ``"TXUR"``."""
        return self.name[:4]

    @property
    def version(self) -> str:
        return self.name[4:]

    # -- helpers ------------------------------------------------------------
    def __len__(self) -> int:
        return len(self.items)

    def __iter__(self) -> Iterator[bytes | None]:
        return iter(self.items)

    def used(self) -> list[tuple[int, bytes]]:
        """(index, bytes) for every non-empty slot."""
        return [(i, b) for i, b in enumerate(self.items) if b is not None]

    def item_data_length(self) -> int:
        return sum(len(b) for b in self.items if b is not None)

    def byte_length(self) -> int:
        n = 16
        if self.items:
            n += 4 * (len(self.items) + 1) + self.item_data_length()
        return n

    # -- (de)serialisation ------------------------------------------------
    @classmethod
    def from_bytes(cls, data: bytes, pos: int) -> tuple["Section", int]:
        """Parse the section starting at ``pos``; return (section, next_pos)."""
        if pos + 16 > len(data):
            raise GphError(f"truncated section header at 0x{pos:x}")
        name = data[pos:pos + 8].decode("latin-1")
        length_blocks, count = struct.unpack_from(">II", data, pos + 8)
        end = pos + length_blocks * 4
        if end > len(data):
            raise GphError(f"section {name!r} at 0x{pos:x} runs past end of file")
        items: list[bytes | None] = []
        if count:
            index = struct.unpack_from(f">{count + 1}I", data, pos + 16)
            base = pos + 16 + 4 * (count + 1)
            if base + index[-1] != end:
                raise GphError(
                    f"section {name!r}: index end {index[-1]} does not match section length "
                    f"({end - base} bytes of item data)"
                )
            for j in range(count):
                start = index[j]
                if start == EMPTY:
                    items.append(None)
                    continue
                k = j + 1
                while index[k] == EMPTY:
                    k += 1
                stop = index[k]
                if stop < start:
                    raise GphError(f"section {name!r}: item {j} has negative length")
                items.append(data[base + start:base + stop])
        return cls(name, items), end

    def to_bytes(self) -> bytes:
        out = bytearray()
        out += self.name.encode("latin-1").ljust(8, b" ")[:8]
        out += struct.pack(">II", self.byte_length() // 4, len(self.items))
        if self.items:
            pos = 0
            index = []
            body = bytearray()
            for b in self.items:
                if b is None:
                    index.append(EMPTY)
                else:
                    if len(b) % 4:
                        raise GphError(f"section {self.name!r}: item length {len(b)} not a multiple of 4")
                    index.append(pos)
                    body += b
                    pos += len(b)
            index.append(pos)
            out += struct.pack(f">{len(index)}I", *index)
            out += body
        return bytes(out)


@dataclass
class GphFile:
    name: str = "        "
    stamp: bytes = b"\x00\x00\x00\x00"
    sections: list[Section] = field(default_factory=list)
    db_type: str = "GDBF2000"
    source_path: str | None = None
    compressed: bool = False

    # -- loading ------------------------------------------------------------
    @classmethod
    def from_bytes(cls, data: bytes, source_path: str | None = None) -> "GphFile":
        if data[:8] != MAGIC:
            raise GphError(f"not a GPH file (magic {data[:8]!r}, expected {MAGIC!r})")
        name = data[8:16].decode("latin-1")
        stamp = bytes(data[16:20])
        (length_blocks,) = struct.unpack_from(">I", data, 20)
        if length_blocks * 4 != len(data):
            raise GphError(f"file length field {length_blocks * 4} != actual {len(data)}")
        f = cls(name=name, stamp=stamp, db_type=MAGIC.decode(), source_path=source_path)
        pos = 24
        while pos < len(data):
            sec, pos = Section.from_bytes(data, pos)
            f.sections.append(sec)
        return f

    @classmethod
    def load(cls, path: str | os.PathLike) -> "GphFile":
        path = os.fspath(path)
        with open(path, "rb") as fh:
            raw = fh.read()
        compressed = raw[:3] == b"BZh"
        if compressed:
            raw = bz2.decompress(raw)
        f = cls.from_bytes(raw, source_path=path)
        f.compressed = compressed
        return f

    # -- saving -------------------------------------------------------------
    def to_bytes(self) -> bytes:
        body = b"".join(s.to_bytes() for s in self.sections)
        total = 24 + len(body)
        head = self.db_type.encode("latin-1")[:8].ljust(8, b" ")
        head += self.name.encode("latin-1")[:8].ljust(8, b" ")
        head += self.stamp[:4].ljust(4, b"\x00")
        head += struct.pack(">I", total // 4)
        return head + body

    def save(self, path: str | os.PathLike, compress: bool | None = None) -> None:
        """Write the file. ``compress=None`` compresses when the path ends in ``.bz2``."""
        path = os.fspath(path)
        data = self.to_bytes()
        if compress is None:
            compress = path.lower().endswith(".bz2")
        if compress:
            data = bz2.compress(data, 9)
        with open(path, "wb") as fh:
            fh.write(data)

    # -- access -------------------------------------------------------------
    def section(self, name: str) -> Section | None:
        """Find a section by full name (``"TXUR2008"``) or by kind (``"TXUR"``)."""
        for s in self.sections:
            if s.name == name or s.kind == name:
                return s
        return None

    def section_names(self) -> list[str]:
        return [s.name for s in self.sections]

    @property
    def module(self) -> str:
        """First 4 characters of the internal name: ``"GEN "``, ``"LRG "``, ``"FNT "``, ``"$$$$"``."""
        return self.name[:4]

    @property
    def variant(self) -> str:
        """Last 4 characters of the internal name: ``"COMM"``, ``"HA00"``, ``"DU  "`` ..."""
        return self.name[4:]

    def summary(self) -> str:
        lines = [
            f"{self.source_path or '<memory>'}",
            f"  db_type={self.db_type} name={self.name!r} stamp={self.stamp.hex()} "
            f"size={len(self.to_bytes())} compressed={self.compressed}",
        ]
        for s in self.sections:
            used = sum(1 for b in s.items if b is not None)
            lines.append(f"  {s.name}  slots={len(s.items):5d} used={used:5d} bytes={s.byte_length()}")
        return "\n".join(lines)


def iter_gph_files(paths: Iterable[str | os.PathLike]) -> Iterator[str]:
    """Yield every ``.gph`` / ``.gph.bz2`` file under the given files/directories."""
    for p in paths:
        p = os.fspath(p)
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for fn in sorted(files):
                    low = fn.lower()
                    if low.startswith("._"):   # AppleDouble side files on exFAT/SMB volumes
                        continue
                    if low.endswith(".gph") or low.endswith(".gph.bz2"):
                        yield os.path.join(root, fn)
        else:
            yield p
