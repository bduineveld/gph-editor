"""Decode raw item bytes into Python dicts using :mod:`gph.schema`."""

from __future__ import annotations

import struct
from dataclasses import dataclass, field
from typing import Any

from .schema import SCHEMAS

NULL = 0xFFFFFFFF


@dataclass
class Link:
    """An 8 byte cross reference: 4 char module + u16 index (+ u16 padding)."""
    module: str
    index: int
    pad: int = 0

    @property
    def is_null(self) -> bool:
        return self.index == 0xFFFF

    def __repr__(self) -> str:
        if self.is_null:
            return f"Link({self.module.strip()}:-)"
        return f"Link({self.module.strip()}:{self.index})"

    def to_json(self):
        if self.is_null:
            return None
        return {"module": self.module.strip(), "index": self.index}


@dataclass
class DecodedItem:
    section: str
    index: int
    size: int
    reloc: int
    nreloc: int
    fields: dict[str, Any] = field(default_factory=dict)
    pointer_offsets: list[int] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def check_relocation(self) -> list[str]:
        """Compare the pointer offsets implied by the schema with reloc/nreloc."""
        expected = list(range(self.reloc, self.reloc + 4 * self.nreloc)) if self.nreloc else []
        expected = expected[::4]
        got = sorted(self.pointer_offsets)
        problems = []
        if got != expected:
            problems.append(
                f"{self.section}[{self.index}]: relocation mismatch: header says {expected[:6]}{'...' if len(expected) > 6 else ''} "
                f"({self.nreloc} ptrs @ {self.reloc}), schema found {got[:6]}{'...' if len(got) > 6 else ''} ({len(got)} ptrs)"
            )
        return problems

    def to_json(self) -> dict:
        return {"index": self.index, "size": self.size, **_jsonable(self.fields)}


def _jsonable(v):
    if isinstance(v, Link):
        return v.to_json()
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    if isinstance(v, (bytes, bytearray)):
        return {"bytes": len(v)} if len(v) > 64 else v.hex()
    return v


class _Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.ptrs: list[int] = []
        self.problems: list[str] = []

    def scalar(self, kind: str, off: int):
        fmt = {"u8": ">B", "u16": ">H", "s16": ">h", "u32": ">I", "s32": ">i"}[kind]
        return struct.unpack_from(fmt, self.data, off)[0]

    def link(self, off: int) -> Link:
        module = self.data[off:off + 4].decode("latin-1")
        idx, pad = struct.unpack_from(">HH", self.data, off + 4)
        return Link(module, idx, pad)

    def pointer(self, off: int) -> int | None:
        self.ptrs.append(off)
        v = struct.unpack_from(">I", self.data, off)[0]
        return None if v == NULL else v

    def record(self, schema: list, off: int) -> tuple[dict, int]:
        """Decode a flat record (no tables) at ``off``; return (fields, end offset)."""
        out: dict[str, Any] = {}
        tables = []
        pos = off
        for f in schema:
            kind = f[0]
            if kind in ("u8", "u16", "s16", "u32", "s32"):
                out[f[1]] = self.scalar(kind, pos)
                pos += {"u8": 1, "u16": 2, "s16": 2, "u32": 4, "s32": 4}[kind]
            elif kind == "pad":
                padbytes = self.data[pos:pos + f[1]]
                if any(padbytes):
                    out.setdefault("_nonzero_padding", []).append((pos, padbytes.hex()))
                pos += f[1]
            elif kind == "chars":
                out[f[1]] = self.data[pos:pos + f[2]].decode("latin-1")
                pos += f[2]
            elif kind == "raw":
                out[f[1]] = self.data[pos:pos + f[2]].hex()
                pos += f[2]
            elif kind == "color":
                r, g, b, a = self.data[pos:pos + 4]
                out[f[1]] = {"r": r, "g": g, "b": b, "a": a, "hex": self.data[pos:pos + 4].hex()}
                pos += 4
            elif kind == "link":
                out[f[1]] = self.link(pos)
                pos += 8
            elif kind == "ptr":
                out[f[1]] = self.pointer(pos)
                pos += 4
            elif kind == "blob":
                p = self.pointer(pos)
                pos += 4
                tables.append(("blob", f, p))
            elif kind == "table":
                p = self.pointer(pos)
                pos += 4
                tables.append(("table", f, p))
            elif kind == "utf16":
                out[f[1]] = decode_utf16(self.data[pos:])
                pos = len(self.data)
            else:
                raise ValueError(f"unknown schema field kind {kind!r}")
        # resolve pointed-to data after the flat part so count fields are known
        for what, f, p in tables:
            name = f[1]
            if what == "blob":
                length = out[f[2]] if isinstance(f[2], str) else f[2]
                out[name] = None if p is None else self.data[p:p + length]
                if p is not None and p + length > len(self.data):
                    self.problems.append(f"blob {name} runs past item end")
            else:
                count = out[f[2]] if isinstance(f[2], str) else f[2]
                out[name] = self.array(f[3], p, count)
        return out, pos

    def array(self, elem, p: int | None, count: int):
        if p is None:
            return None if count == 0 else []  # null pointer
        if isinstance(elem, str):
            size = {"u8": 1, "u16": 2, "s16": 2, "u32": 4}[elem]
            fmt = {"u8": "B", "u16": "H", "s16": "h", "u32": "I"}[elem]
            if p + size * count > len(self.data):
                self.problems.append(f"array at {p} ({count} x {elem}) runs past item end")
                count = max(0, (len(self.data) - p) // size)
            return list(struct.unpack_from(f">{count}{fmt}", self.data, p))
        tag, sub = elem
        assert tag == "ptr"
        out = []
        for i in range(count):
            q = self.pointer(p + 4 * i)
            if q is None:
                out.append(None)
            elif sub == "link":
                out.append(self.link(q))
            else:
                rec, _ = self.record(sub, q)
                out.append(rec)
        return out


def decode_utf16(raw: bytes) -> str:
    """TEXT2006 payload: UTF-16BE, NUL terminated, padded with NULs to 4 bytes."""
    end = len(raw)
    for i in range(0, len(raw) - 1, 2):
        if raw[i] == 0 and raw[i + 1] == 0:
            end = i
            break
    return raw[:end].decode("utf-16-be", errors="replace")


def encode_utf16(text: str) -> bytes:
    """Inverse of :func:`decode_utf16` (without the relocation header)."""
    raw = text.encode("utf-16-be") + b"\x00\x00"
    while len(raw) % 4:
        raw += b"\x00"
    return raw


def decode_item(section: str, index: int, data: bytes) -> DecodedItem:
    reloc, nreloc = struct.unpack_from(">HH", data, 0)
    item = DecodedItem(section=section, index=index, size=len(data), reloc=reloc, nreloc=nreloc)
    schema = SCHEMAS.get(section)
    if schema is None:
        item.fields["raw"] = data[4:].hex()
        item.problems.append(f"no schema for {section}")
        return item
    r = _Reader(data)
    try:
        fields, end = r.record(schema, 4)
    except (struct.error, IndexError) as e:
        item.fields["raw"] = data[4:].hex()
        item.problems.append(f"decode error: {e}")
        return item
    item.fields = fields
    item.fields["_header_end"] = end
    item.pointer_offsets = r.ptrs
    item.problems = r.problems
    return item


def decode_section(section_name: str, items: list[bytes | None]) -> list[DecodedItem | None]:
    return [None if b is None else decode_item(section_name, i, b) for i, b in enumerate(items)]
