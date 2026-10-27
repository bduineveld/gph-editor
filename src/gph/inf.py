""".inf side-car files and their checksum.

Every ``*.gph.bz2`` on the RT6 is accompanied by ``*.gph.bz2.inf``::

    7fe71346              <- two 16 bit checksums as 8 hex digits, see below
    VER:08.08.10.10.10    <- yy.mm.dd.hh.mm
    TYPE:DATA
    COMPRESSED:NO
    SIZE:31403            <- size in bytes of the file next to it (the .bz2 itself)
    ENTRY:NO
    SUBVER:

Checksum (recovered from GPH_EDITOR.exe, functions at 0x4cb6b0 / 0x4cb750 / 0x4cb7b0, and
verified against factory ``.inf`` files and Magneti Marelli's ``builtinsRNEG.out.inf``):

* the *last* four hex digits are ``crc16`` of the complete data file (the ``.bz2`` bytes);
* the *first* four hex digits are ``crc16`` of the ``.inf`` file itself from byte 4 to the
  end, i.e. over the second half of line 1 and all following lines, line endings included.

``crc16`` is a table driven 16 bit CRC, MSB first, initial value 0, no final xor, with the
256 entry table ``T`` defined by ``crc = T[crc >> 8] ^ T[byte] ^ (crc << 8)`` (the table is
linear, so this equals the usual ``T[(crc >> 8) ^ byte] ^ (crc << 8)``).  The table equals
the reflected CRC-16 table for polynomial ``0xD415`` with every entry byte swapped.
"""

from __future__ import annotations

import datetime as _dt
import os
from dataclasses import dataclass, field


def _build_table() -> tuple[int, ...]:
    out = []
    for i in range(256):
        c = i
        for _ in range(8):
            c = (c >> 1) ^ 0xD415 if c & 1 else c >> 1
        out.append(((c & 0xFF) << 8) | (c >> 8))
    return tuple(out)


CRC16_TABLE = _build_table()
assert CRC16_TABLE[1] == 0xDF2B and CRC16_TABLE[128] == 0x15D4


def crc16(data: bytes, crc: int = 0) -> int:
    """The RT's 16 bit checksum (see module docstring)."""
    t = CRC16_TABLE
    for b in data:
        crc = (t[crc >> 8] ^ t[b] ^ (crc << 8)) & 0xFFFF
    return crc


def version_stamp(when: _dt.datetime | None = None) -> str:
    when = when or _dt.datetime.now()
    return when.strftime("%y.%m.%d.%H.%M")


@dataclass
class InfFile:
    checksum: str = "00000000"
    fields: dict[str, str] = field(default_factory=dict)
    newline: str = "\n"

    DEFAULT_FIELDS = {"VER": "08.08.10.10.10", "TYPE": "DATA", "COMPRESSED": "NO",
                      "SIZE": "0", "ENTRY": "NO", "SUBVER": ""}

    # -- reading ------------------------------------------------------------
    @classmethod
    def parse(cls, text: str | bytes) -> "InfFile":
        if isinstance(text, bytes):
            text = text.decode("latin-1")
        newline = "\r\n" if "\r\n" in text else "\n"
        lines = [l.rstrip("\r\n") for l in text.splitlines()]
        inf = cls(newline=newline)
        if lines:
            inf.checksum = lines[0].strip()
        for l in lines[1:]:
            if ":" in l:
                k, v = l.split(":", 1)
                inf.fields[k.strip()] = v.strip()
        return inf

    @classmethod
    def load(cls, path: str | os.PathLike) -> "InfFile":
        with open(path, "rb") as fh:
            return cls.parse(fh.read())

    @classmethod
    def new(cls, version: str | None = None) -> "InfFile":
        inf = cls(fields=dict(cls.DEFAULT_FIELDS))
        inf.fields["VER"] = version or version_stamp()
        return inf

    # -- writing ------------------------------------------------------------
    def to_text(self) -> str:
        order = ["VER", "TYPE", "COMPRESSED", "SIZE", "ENTRY", "SUBVER"]
        keys = order + [k for k in self.fields if k not in order]
        lines = [self.checksum] + [f"{k}:{self.fields.get(k, '')}" for k in keys if k in self.fields or k in order]
        return self.newline.join(lines) + self.newline

    def to_bytes(self) -> bytes:
        return self.to_text().encode("latin-1")

    def update(self, data: bytes) -> "InfFile":
        """Set SIZE and both checksum halves for the given data file contents."""
        self.fields["SIZE"] = str(len(data))
        self.checksum = "0000%04x" % crc16(data)
        self.checksum = "%04x%04x" % (crc16(self.to_bytes()[4:]), crc16(data))
        return self

    def save(self, path: str | os.PathLike) -> None:
        with open(path, "wb") as fh:
            fh.write(self.to_bytes())

    # -- checking -----------------------------------------------------------
    @property
    def size(self) -> int | None:
        v = self.fields.get("SIZE")
        return int(v) if v and v.isdigit() else None

    @staticmethod
    def verify_bytes(inf_bytes: bytes, data: bytes) -> tuple[bool, bool]:
        """(text checksum ok, data checksum ok) for raw .inf bytes and the data file."""
        first = inf_bytes.split(b"\n")[0].rstrip(b"\r").decode("latin-1").strip().lower()
        if len(first) != 8:
            return (False, False)
        text_ok = first[:4] == "%04x" % crc16(inf_bytes[4:])
        data_ok = first[4:] == "%04x" % crc16(data)
        return (text_ok, data_ok)

    @classmethod
    def verify_files(cls, data_path: str | os.PathLike, inf_path: str | os.PathLike | None = None) -> tuple[bool, bool]:
        data_path = os.fspath(data_path)
        inf_path = os.fspath(inf_path) if inf_path else data_path + ".inf"
        with open(data_path, "rb") as fh:
            data = fh.read()
        with open(inf_path, "rb") as fh:
            inf_bytes = fh.read()
        return cls.verify_bytes(inf_bytes, data)


def write_inf_for(data_path: str | os.PathLike, version: str | None = None) -> InfFile:
    """Create or update ``<data_path>.inf`` so that it matches the data file.

    An existing .inf keeps its VER/TYPE/... fields (like GPH_EDITOR does); ``version``
    overrides VER.
    """
    data_path = os.fspath(data_path)
    inf_path = data_path + ".inf"
    inf = InfFile.load(inf_path) if os.path.exists(inf_path) else InfFile.new(version)
    if version:
        inf.fields["VER"] = version
    with open(data_path, "rb") as fh:
        inf.update(fh.read())
    inf.save(inf_path)
    return inf
