""".inf side-car files.

Every ``*.gph.bz2`` on the RT6 is accompanied by ``*.gph.bz2.inf``::

    7fe71346              <- 8 hex digit checksum (algorithm not yet identified, see FORMAT.md)
    VER:08.08.10.10.10    <- yy.mm.dd.hh.mm
    TYPE:DATA
    COMPRESSED:NO
    SIZE:31403            <- size in bytes of the file next to it (the .bz2 itself)
    ENTRY:NO
    SUBVER:
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field


@dataclass
class InfFile:
    checksum: str = "00000000"
    fields: dict[str, str] = field(default_factory=dict)

    @classmethod
    def parse(cls, text: str) -> "InfFile":
        lines = [l.rstrip("\r\n") for l in text.splitlines()]
        inf = cls()
        if lines:
            inf.checksum = lines[0].strip()
        for l in lines[1:]:
            if ":" in l:
                k, v = l.split(":", 1)
                inf.fields[k.strip()] = v.strip()
        return inf

    @classmethod
    def load(cls, path: str | os.PathLike) -> "InfFile":
        with open(path, "r", encoding="latin-1") as fh:
            return cls.parse(fh.read())

    def to_text(self) -> str:
        order = ["VER", "TYPE", "COMPRESSED", "SIZE", "ENTRY", "SUBVER"]
        keys = order + [k for k in self.fields if k not in order]
        lines = [self.checksum] + [f"{k}:{self.fields.get(k, '')}" for k in keys if k in self.fields or k in order]
        return "\r\n".join(lines) + "\r\n"

    @property
    def size(self) -> int | None:
        v = self.fields.get("SIZE")
        return int(v) if v and v.isdigit() else None
