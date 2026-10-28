"""Encode field dicts back into item bytes (the inverse of :mod:`gph.decode`).

``encode_item(section, fields)`` accepts the dict produced by :func:`gph.decode.decode_item`
(or its JSON form) and rebuilds the item byte for byte, relocation header included.  The
layout follows what every factory file does:

1. the fixed header (scalars, links, colours ...), with one u32 pointer per ``blob``/``table``
   field at the position the schema gives it;
2. the entries of every *pointer table* (``("ptr", ...)`` element type), in schema order;
3. the data of every scalar table and blob, in schema order, each padded to 4 bytes;
4. the records of every pointer table, in schema order, in element order.

Steps 1-2 are the contiguous pointer area described by ``reloc``/``nreloc``.  A table whose
list is empty or ``None`` gets a null pointer (``0xFFFFFFFF``); a ``None`` entry inside a
pointer table is a null pointer as well.  Count fields are filled in from the list lengths,
so callers only have to edit the lists.

Accepted value forms (lenient on purpose, for hand edited JSON):

* scalars: ``int``
* ``chars``: ``str``
* ``raw``: hex ``str`` or ``bytes``
* ``color``: ``{"r","g","b","a"}``, ``{"hex": "rrggbbaa"}``, ``"#rrggbbaa"``/``"#rrggbb"``,
  or a 3/4 tuple
* ``link``: :class:`gph.decode.Link`, ``{"module","index"}``, ``"GEN:12"``, ``("GEN", 12)``
  or ``None`` (null link = ``GEN`` + ``0xFFFF``, as in the factory files)
* ``blob``: ``bytes`` (``None`` = null pointer)
* ``table``: ``list`` or ``None``
* ``utf16``: ``str``
"""

from __future__ import annotations

import struct
from typing import Any

from .decode import Link, encode_utf16
from .schema import SCHEMAS

NULL = 0xFFFFFFFF
_SCALAR_FMT = {"u8": ">B", "u16": ">H", "s16": ">h", "u32": ">I", "s32": ">i"}
_SCALAR_SIZE = {"u8": 1, "u16": 2, "s16": 2, "u32": 4, "s32": 4}


class EncodeError(ValueError):
    pass


# ---------------------------------------------------------------------------
# value coercion
def to_link(v) -> Link:
    if v is None:
        return Link("GEN ", 0xFFFF)
    if isinstance(v, Link):
        return v
    if isinstance(v, dict):
        if v.get("index") is None:
            return Link("GEN ", 0xFFFF)
        return Link(v.get("module", "GEN"), int(v["index"]), int(v.get("pad", 0)))
    if isinstance(v, str):
        if ":" not in v:
            raise EncodeError(f"bad link {v!r}, expected 'MODULE:index'")
        m, i = v.split(":", 1)
        if i.strip() in ("", "-"):
            return Link("GEN ", 0xFFFF)
        return Link(m, int(i, 0))
    if isinstance(v, (tuple, list)) and len(v) in (2, 3):
        return Link(v[0], int(v[1]), int(v[2]) if len(v) == 3 else 0)
    raise EncodeError(f"cannot interpret {v!r} as a link")


def to_color(v) -> bytes:
    if isinstance(v, dict):
        if all(k in v for k in "rgba"):
            return bytes([v["r"], v["g"], v["b"], v["a"]])
        if "hex" in v:
            v = v["hex"]
        else:
            raise EncodeError(f"bad colour {v!r}")
    if isinstance(v, str):
        s = v.strip().lstrip("#")
        if len(s) == 6:
            s += "ff"
        if len(s) != 8:
            raise EncodeError(f"bad colour {v!r}, expected rrggbb or rrggbbaa")
        try:
            return bytes.fromhex(s)
        except ValueError:
            raise EncodeError(f"bad colour {v!r}, expected rrggbb or rrggbbaa") from None
    if isinstance(v, (tuple, list)):
        if len(v) == 3:
            v = (*v, 255)
        if len(v) != 4:
            raise EncodeError(f"bad colour {v!r}")
        return bytes(int(x) & 0xFF for x in v)
    if isinstance(v, (bytes, bytearray)) and len(v) == 4:
        return bytes(v)
    raise EncodeError(f"cannot interpret {v!r} as a colour")


def _to_bytes(v, what: str) -> bytes:
    if isinstance(v, (bytes, bytearray)):
        return bytes(v)
    if isinstance(v, str):
        return bytes.fromhex(v)
    if isinstance(v, dict) and "bytes" in v:
        raise EncodeError(f"{what}: only the length of the data is known ({v['bytes']} bytes); "
                          "merge the edit over the decoded original (see gph.edit.update_item)")
    raise EncodeError(f"{what}: cannot interpret {type(v).__name__} as bytes")


def _pad4(b: bytes) -> bytes:
    return b + b"\x00" * (-len(b) % 4)


# ---------------------------------------------------------------------------
class _Writer:
    """Builds one item.  Pointer values are patched in once all offsets are known."""

    def __init__(self):
        self.buf = bytearray()
        self.ptr_offsets: list[int] = []          # every pointer word, in order
        self.fixups: list[tuple[int, int | None]] = []   # (offset of pointer word, target offset)

    def ptr_word(self) -> int:
        off = len(self.buf)
        self.ptr_offsets.append(off)
        self.buf += b"\xff\xff\xff\xff"
        return off

    def set_ptr(self, where: int, target: int | None) -> None:
        struct.pack_into(">I", self.buf, where, NULL if target is None else target)

    # -- flat record --------------------------------------------------------
    def record(self, schema: list, fields: dict, what: str) -> list[tuple]:
        """Write the fixed part of ``schema``; return deferred tables ``(kind, field, ptr_off, value)``."""
        deferred = []
        for f in schema:
            kind = f[0]
            if kind in _SCALAR_FMT:
                v = fields.get(f[1], 0)
                try:
                    self.buf += struct.pack(_SCALAR_FMT[kind], int(v))
                except struct.error as e:
                    raise EncodeError(f"{what}.{f[1]} = {v!r}: {e}") from None
            elif kind == "pad":
                self.buf += b"\x00" * f[1]
            elif kind == "chars":
                s = str(fields.get(f[1], "")).encode("latin-1")
                self.buf += s[: f[2]].ljust(f[2], b" ")
            elif kind == "raw":
                b = _to_bytes(fields.get(f[1], b"\x00" * f[2]), f"{what}.{f[1]}")
                if len(b) != f[2]:
                    raise EncodeError(f"{what}.{f[1]}: expected {f[2]} bytes, got {len(b)}")
                self.buf += b
            elif kind == "color":
                self.buf += to_color(fields.get(f[1], (0, 0, 0, 255)))
            elif kind == "link":
                lk = to_link(fields.get(f[1]))
                self.buf += lk.module.encode("latin-1")[:4].ljust(4, b" ")
                self.buf += struct.pack(">HH", lk.index & 0xFFFF, lk.pad & 0xFFFF)
            elif kind == "ptr":
                raise EncodeError(f"{what}.{f[1]}: bare pointers are not supported")
            elif kind in ("blob", "table"):
                deferred.append((kind, f, self.ptr_word(), fields.get(f[1])))
            elif kind == "utf16":
                self.buf += encode_utf16(str(fields.get(f[1], "")))
            else:
                raise EncodeError(f"unknown schema field kind {kind!r}")
        return deferred

    def write_array(self, elem: str, values, what: str) -> int:
        fmt = {"u8": "B", "u16": "H", "s16": "h", "u32": "I"}[elem]
        start = len(self.buf)
        try:
            self.buf += _pad4(struct.pack(f">{len(values)}{fmt}", *[int(v) for v in values]))
        except struct.error as e:
            raise EncodeError(f"{what}: {e}") from None
        return start


def _count_of(fields: dict, f: tuple, value) -> int:
    """Resolve the count of a table, filling the count field in from the list length."""
    count = f[2]
    if isinstance(count, int):
        return count
    if value is not None:
        fields[count] = len(value)
    return int(fields.get(count, 0))


def _length_of(fields: dict, f: tuple, value) -> int:
    length = f[2]
    if isinstance(length, int):
        return length
    if value is not None:
        fields[length] = len(value)
    return int(fields.get(length, 0))


def encode_item(section: str, fields: dict[str, Any]) -> bytes:
    """Rebuild the bytes of one item (relocation header included) from a field dict.

    ``fields`` is updated in place: count/length fields are set from the lists/blobs.
    """
    schema = SCHEMAS.get(section)
    if schema is None:
        raw = fields.get("raw")
        if raw is None:
            raise EncodeError(f"no schema for {section} and no 'raw' field")
        return struct.pack(">HH", 0, 0) + _to_bytes(raw, f"{section}.raw")
    if not schema:  # e.g. PALE2000: header only
        return struct.pack(">HH", 0, 0)

    # count/length fields live in the header, so fix them from the lists first
    for f in schema:
        if f[0] == "table":
            _count_of(fields, f, fields.get(f[1]))
        elif f[0] == "blob":
            v = fields.get(f[1])
            _length_of(fields, f, None if v is None else _to_bytes(v, f"{section}.{f[1]}"))

    w = _Writer()
    w.buf += b"\x00\x00\x00\x00"  # relocation header, patched below
    deferred = w.record(schema, fields, section)

    # 2. entries of pointer tables
    entries: dict[int, list[int]] = {}
    for kind, f, off, value in deferred:
        if kind == "table" and isinstance(f[3], tuple):
            count = _count_of(fields, f, value)
            lst = list(value or [])
            if len(lst) > count:
                raise EncodeError(f"{section}.{f[1]}: {len(lst)} elements but count is {count}")
            if not lst:
                w.set_ptr(off, None)
                continue
            w.set_ptr(off, len(w.buf))
            entries[off] = [w.ptr_word() for _ in range(count)]

    # 3. scalar tables and blobs
    for kind, f, off, value in deferred:
        if kind == "blob":
            if value is None:
                w.set_ptr(off, None)
                continue
            b = _to_bytes(value, f"{section}.{f[1]}")
            length = _length_of(fields, f, b)
            if len(b) != length:
                raise EncodeError(f"{section}.{f[1]}: {len(b)} bytes but length field says {length}")
            w.set_ptr(off, len(w.buf))
            w.buf += _pad4(b)
        elif kind == "table" and isinstance(f[3], str):
            count = _count_of(fields, f, value)
            if not value:
                w.set_ptr(off, None)
                continue
            if len(value) != count:
                raise EncodeError(f"{section}.{f[1]}: {len(value)} elements but count is {count}")
            w.set_ptr(off, w.write_array(f[3], value, f"{section}.{f[1]}"))

    # 4. records of pointer tables
    for kind, f, off, value in deferred:
        if kind == "table" and isinstance(f[3], tuple) and off in entries:
            sub = f[3][1]
            lst = list(value or [])
            lst += [None] * (len(entries[off]) - len(lst))
            for slot, elem in zip(entries[off], lst):
                if elem is None:
                    w.set_ptr(slot, None)
                    continue
                w.set_ptr(slot, len(w.buf))
                if sub == "link":
                    lk = to_link(elem)
                    w.buf += lk.module.encode("latin-1")[:4].ljust(4, b" ")
                    w.buf += struct.pack(">HH", lk.index & 0xFFFF, lk.pad & 0xFFFF)
                else:
                    if not isinstance(elem, dict):
                        raise EncodeError(f"{section}.{f[1]}: element {elem!r} is not a record")
                    nested = w.record(sub, elem, f"{section}.{f[1]}")
                    if nested:
                        raise EncodeError(f"{section}.{f[1]}: nested tables are not supported")

    # relocation header
    if w.ptr_offsets:
        first = w.ptr_offsets[0]
        if w.ptr_offsets != list(range(first, first + 4 * len(w.ptr_offsets), 4)):
            raise EncodeError(f"{section}: pointer words are not contiguous (schema bug)")
        struct.pack_into(">HH", w.buf, 0, first, len(w.ptr_offsets))
    if len(w.buf) % 4:
        raise EncodeError(f"{section}: item length {len(w.buf)} not a multiple of 4 (schema bug)")
    return bytes(w.buf)
