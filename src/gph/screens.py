"""The screen database of the RT6 firmware: which GPH widgets make up each screen.

The GPH files only hold the widgets (a button, a text, a background ...) with their position
and looks.  Which widgets are shown together as one screen is decided by the firmware, and
that part is data too: ``Data_Base/Graphics/GraphicBase.RNEG.gz`` on the unit (and on the
software update CD).  It is a PowerPC ELF relocatable file without code, only tables:

* ``pIndexOfScreenDataBase`` -- one 12-byte entry per screen: ``screen id, flags, pointer``
  (the pointer is a relocation against ``pDataBaseNNNNNN``);
* ``pDataBaseNNNNNN`` -- the objects of screen ``NNNNNN``: 12-byte entries ``object id,
  reference, pointer`` terminated by zeros.  The reference is ``type << 24 | sub << 16 |
  index``: ``type`` selects the GPH section (see :data:`TYPE_SECTIONS`), ``index`` the slot in
  ``GEN_common``.  The first entries carry the screen id itself as object id (background,
  fixed texts, fixed pictures); the others carry the object ids the MMI code uses
  (``GetObject(180004)`` ...);
* ``List_GDO_<kind>_<screen>...`` -- a list attached to an object: a 16-bit count followed by
  16-bit values.  For texts these are ``TXIN2000`` indexes (the month names of the clock
  screen, the lines of a menu), for variable pictures ``IMAG2000`` indexes.

The files are compressed as one prefix byte (``0x08``) followed by a zlib stream; the
decompressed size is in the ``.inf`` side-car as ``USIZE``.

Screen names are not stored directly.  Most can be read off the ``List_GDO`` symbol names
(``List_GDO_it_1_CLOCK_F3_Z5_2`` belongs to screen ``CLOCK_F3_Z5_2``); the rest come from the
MMI modules (``Application/MMI/*/mmi_*.gz``), where every menu screen is a C++ class
``C_Menu_<NAME>`` whose constructor passes the screen id to ``C_MENU_STATE``, and where the
full-screen "F" screens have ``SCREEN_<NAME>_FIELDS_TABLE`` tables.  :meth:`ScreenDatabase.
enrich_from_modules` reads those.

Only the standard library is used.
"""

from __future__ import annotations

import collections
import os
import re
import struct
import zlib
from dataclasses import dataclass, field

#: reference type byte -> GPH section of GEN_common.  Derived from the factory database: for
#: every type the referenced indexes all exist in exactly this section and the highest index
#: matches the section size (BKGD 38 slots -> max 37 ...).  "probable" ones fit but are not unique.
TYPE_SECTIONS: dict[int, str] = {
    1: "FBAR2000", 2: "FBTN2006", 3: "CBTN2006", 4: "DBTN2001", 5: "DCBT2000", 8: "EDTX2000",
    9: "EDTB2006", 10: "BKGD2000", 11: "FXPI2000", 12: "FXTX2006", 15: "IMEN2000", 16: "ITXT2000",
    18: "LIST2001", 19: "MCTX2006", 21: "CRZO2002", 23: "SEBR2000", 24: "VRPI2000", 25: "VWTX2000",
    28: "MENU2006", 34: "DTXT2000", 37: "DTXT2000", 38: "ICBT2000",
}
PROBABLE_TYPES = {8, 9, 37, 38}
#: types without a GPH item behind them (map window, video, on-screen display ...): shown as boxes
TYPE_NAMES = {7: "text?", 20: "map/video", 26: "object", 27: "object", 29: "object", 31: "object", 33: "object", 43: "text?"}
#: sections whose list values are text indexes (TXIN2000); VRPI lists hold IMAG2000 indexes
TEXT_LIST_SECTIONS = {"ITXT2000", "MENU2006", "IMEN2000", "LIST2001", "MCTX2006", "ICBT2000", "EDTB2006", "CBTN2006", "FBTN2006", "DBTN2001", "DCBT2000"}

ZONE = re.compile(r"^(?:Z\d(?:_\d)?|Full_\d)$")


class ScreenError(Exception):
    pass


# ---------------------------------------------------------------------------
def unpack_module(data: bytes) -> bytes:
    """Return the ELF bytes of a firmware ``.gz`` module (prefix byte + zlib) or of a plain ELF."""
    if data[:4] == b"\x7fELF":
        return data
    if data[:2] == b"\x1f\x8b":
        import gzip
        return gzip.decompress(data)
    for off in (1, 0):
        try:
            return zlib.decompress(data[off:])
        except zlib.error:
            continue
    raise ScreenError("not a firmware module (expected a prefix byte + zlib stream, or an ELF file)")


class Elf:
    """Just enough of a big-endian ELF32 relocatable reader for the firmware's data modules."""

    def __init__(self, data: bytes):
        if data[:4] != b"\x7fELF" or data[5] != 2:
            raise ScreenError("not a big-endian ELF file")
        self.data = data
        shoff = struct.unpack(">I", data[32:36])[0]
        shentsize, shnum, shstrndx = struct.unpack(">HHH", data[46:52])
        self.sections = []
        for i in range(shnum):
            o = shoff + i * shentsize
            f = struct.unpack(">IIIIIIIIII", data[o:o + 40])
            self.sections.append(dict(name=f[0], type=f[1], addr=f[3], off=f[4], size=f[5], link=f[6], info=f[7]))
        shstr = self.sections[shstrndx]
        for s in self.sections:
            s["sname"] = self._cstr(shstr["off"] + s["name"])
        self.by_name = {s["sname"]: i for i, s in enumerate(self.sections)}
        symsec = next((s for s in self.sections if s["type"] == 2), None)
        self.symbols: list[dict] = []
        if symsec:
            strsec = self.sections[symsec["link"]]
            for i in range(symsec["size"] // 16):
                o = symsec["off"] + i * 16
                n, v, sz, info, _other, shndx = struct.unpack(">IIIBBH", data[o:o + 16])
                self.symbols.append(dict(name=self._cstr(strsec["off"] + n), value=v, size=sz, shndx=shndx, type=info & 0xF))
        self.symbol = {s["name"]: s for s in self.symbols if s["name"]}
        # relocations: (target section index, offset) -> (symbol name, addend)
        self.relocs: dict[int, dict[int, tuple[str, int]]] = {}
        for s in self.sections:
            if s["type"] == 4:  # SHT_RELA
                tgt = self.relocs.setdefault(s["info"], {})
                for i in range(s["size"] // 12):
                    o = s["off"] + i * 12
                    roff, rinfo, add = struct.unpack(">IIi", data[o:o + 12])
                    sym = self.symbols[rinfo >> 8]
                    name = sym["name"] or (self.sections[sym["shndx"]]["sname"] if sym["shndx"] < len(self.sections) else "")
                    tgt[roff] = (name, add)

    def _cstr(self, off: int) -> str:
        end = self.data.index(b"\0", off)
        return self.data[off:end].decode("latin-1")

    def bytes_of(self, sym: dict, size: int | None = None) -> bytes:
        sec = self.sections[sym["shndx"]]
        n = size if size is not None else sym["size"]
        return self.data[sec["off"] + sym["value"]: sec["off"] + sym["value"] + n]

    def target(self, sym: dict, offset: int) -> str | None:
        """Symbol a pointer at ``offset`` inside ``sym`` is relocated against (None = no pointer)."""
        r = self.relocs.get(sym["shndx"], {}).get(sym["value"] + offset)
        return r[0] if r else None

    def functions(self) -> list[dict]:
        text = self.by_name.get(".text")
        return sorted((s for s in self.symbols if s["shndx"] == text and s["type"] == 2), key=lambda s: s["value"])

    def text_words(self, start: int, size: int):
        sec = self.sections[self.by_name[".text"]]
        for pc in range(start, start + size, 4):
            yield pc, struct.unpack(">I", self.data[sec["off"] + pc: sec["off"] + pc + 4])[0]

    def text_reloc(self, pc: int) -> str | None:
        r = self.relocs.get(self.by_name.get(".text", -1), {}).get(pc)
        return r[0] if r else None


# ---------------------------------------------------------------------------
@dataclass
class GdoObject:
    obj_id: int          #: id the MMI code uses (``GetObject(180004)``); equals the screen id for fixed parts
    type: int            #: reference type byte, see TYPE_SECTIONS
    sub: int             #: sub type byte (menu 19 = the menu, 25 = an item ...)
    index: int           #: slot in the GPH section
    list_name: str | None = None
    values: list[int] = field(default_factory=list)

    @property
    def section(self) -> str | None:
        return TYPE_SECTIONS.get(self.type)

    @property
    def fixed(self) -> bool:
        """Part of the screen itself (background, fixed text/picture) rather than an MMI object."""
        return self.obj_id < 10000

    def to_json(self) -> dict:
        return {"obj": self.obj_id, "type": self.type, "sub": self.sub, "index": self.index, "section": self.section,
                "kind": TYPE_NAMES.get(self.type), "probable": self.type in PROBABLE_TYPES, "fixed": self.fixed,
                "list": self.list_name, "values": self.values}


@dataclass
class Screen:
    id: int
    flags: int
    table: str
    objects: list[GdoObject]
    name: str | None = None
    module: str | None = None      #: MMI module that owns it (tuner, nav ...), when known
    class_name: str | None = None  #: C++ class in that module, when known

    def to_json(self, objects: bool = True) -> dict:
        d = {"id": self.id, "name": self.name, "module": self.module, "class": self.class_name, "flags": self.flags,
             "count": len(self.objects), "sections": sorted({o.section or f"type {o.type}" for o in self.objects})}
        if objects:
            d["objects"] = [o.to_json() for o in self.objects]
        return d


class ScreenDatabase:
    def __init__(self, elf: Elf, path: str | None = None):
        self.path = path
        self.version = next((s["name"] for s in elf.symbols if s["name"].startswith("INFO_VERSION")), None)
        ver = elf.symbol.get("EXP_BuildId")
        self.build = struct.unpack(">I", elf.bytes_of(ver, 4))[0] if ver else None
        self.screens: dict[int, Screen] = {}
        self._parse(elf)
        self._names_from_lists()
        self._uses: dict[tuple[str, int], list[int]] | None = None

    # -- loading --------------------------------------------------------------
    @classmethod
    def from_bytes(cls, data: bytes, path: str | None = None) -> "ScreenDatabase":
        return cls(Elf(unpack_module(data)), path)

    @classmethod
    def load(cls, path: str | os.PathLike) -> "ScreenDatabase":
        path = os.fspath(path)
        with open(path, "rb") as fh:
            return cls.from_bytes(fh.read(), path)

    def _parse(self, elf: Elf) -> None:
        idx = elf.symbol.get("pIndexOfScreenDataBase")
        if idx is None:
            raise ScreenError("no pIndexOfScreenDataBase symbol: not a GraphicBase file")
        raw = elf.bytes_of(idx)
        for off in range(0, len(raw) - 11, 12):
            sid, flags = struct.unpack(">II", raw[off:off + 8])
            table = elf.target(idx, off + 8)
            if not table or table not in elf.symbol:
                continue
            tsym = elf.symbol[table]
            traw = elf.bytes_of(tsym)
            objects = []
            for o in range(0, len(traw) - 11, 12):
                oid, ref = struct.unpack(">II", traw[o:o + 8])
                if oid == 0 and ref == 0:
                    break
                lst = elf.target(tsym, o + 8)
                values = self._list_values(elf, lst) if lst else []
                objects.append(GdoObject(oid, ref >> 24, (ref >> 16) & 0xFF, ref & 0xFFFF, lst, values))
            self.screens[sid] = Screen(sid, flags, table, objects)

    @staticmethod
    def _list_values(elf: Elf, name: str) -> list[int]:
        sym = elf.symbol.get(name)
        if sym is None or sym["size"] < 2:
            return []
        raw = elf.bytes_of(sym)
        n = struct.unpack(">H", raw[:2])[0]
        avail = (len(raw) - 2) // 2
        n = min(n, avail) if n else avail
        return list(struct.unpack(f">{n}H", raw[2:2 + 2 * n]))

    # -- names ------------------------------------------------------------------
    @staticmethod
    def name_from_list(symbol: str) -> str | None:
        """``List_GDO_Rneg2010_it_1_TUNER_F1_0_Name_Z0_3`` -> ``TUNER_F1_0_Name_Z0_3``."""
        toks = symbol.split("_")
        # the name starts at the first upper-case token after a lower-case "kind" token (it, vp, menu, 5l, 1 ...)
        start = next((i for i in range(2, len(toks))
                      if toks[i][:1].isupper() and re.fullmatch(r"[a-z0-9]+", toks[i - 1]) and not ZONE.match(toks[i])), None)
        if start is None:
            return None
        for j in range(start, len(toks)):
            t, nxt = toks[j], toks[j + 1] if j + 1 < len(toks) else ""
            if re.fullmatch(r"Z\d", t):                # zone: Z2, Z1_0, Z0_3, Z0_Full_1
                end = j + 3 if (t == "Z0" and nxt == "Full") else j + 2 if nxt.isdigit() else j + 1
                return "_".join(toks[start:end])
            if t == "Full" and nxt.isdigit():          # Full_2
                return "_".join(toks[start:j + 2])
        return None

    def _names_from_lists(self) -> None:
        for sc in self.screens.values():
            votes: collections.Counter[str] = collections.Counter()
            for o in sc.objects:
                if o.list_name:
                    n = self.name_from_list(o.list_name)
                    if n:
                        votes[n] += 1
            if votes:
                sc.name = votes.most_common(1)[0][0]

    def enrich_from_modules(self, folder: str | os.PathLike) -> int:
        """Read screen names/classes from the MMI modules under ``folder`` (``Application/MMI``).

        Returns the number of screens that got a name or class.  Two sources:

        * every class constructor that calls ``C_MENU_STATE::C_MENU_STATE(zone, kbd, int, screen)``
          loads the screen id into r7 just before the call (``li r7, 39``);
        * ``SCREEN_<NAME>_SCREEN_SET`` tables: 16-byte entries ``screen id, n, 0, pointer to
          SCREEN_<NAME>_FIELDS_TABLE``.
        """
        folder = os.fspath(folder)
        found = 0
        for root, _dirs, files in os.walk(folder):
            for fn in sorted(files):
                low = fn.lower()
                if not (low.startswith(("mmi_", "lib_")) and low.endswith(".gz")):
                    continue
                module = low[4:-3] if low.startswith("mmi_") else low[:-3]
                try:
                    with open(os.path.join(root, fn), "rb") as fh:
                        elf = Elf(unpack_module(fh.read()))
                except (ScreenError, OSError, zlib.error):
                    continue
                found += self._enrich_from_module(elf, module)
        return found

    def _enrich_from_module(self, elf: Elf, module: str) -> int:
        n = 0
        if ".text" in elf.by_name:
            funcs = elf.functions()
            for k, fsym in enumerate(funcs):
                # constructors are mangled as __<len><Class><args>: "__19C_Menu_MENU_H1_Z1_0"
                m = re.match(r"^__(\d+)(C_\w+)$", fsym["name"])
                if not m or len(m.group(2)) < int(m.group(1)):
                    continue
                cls = m.group(2)[:int(m.group(1))]
                size = fsym["size"] or ((funcs[k + 1]["value"] - fsym["value"]) if k + 1 < len(funcs) else 0x400)
                r7 = None
                for pc, w in elf.text_words(fsym["value"], size):
                    op = w >> 26
                    if op == 14 and (w >> 16) & 31 == 0 and (w >> 21) & 31 == 7:  # li r7, imm
                        imm = w & 0xFFFF
                        r7 = imm - 0x10000 if imm & 0x8000 else imm
                    elif op == 18:
                        callee = elf.text_reloc(pc) or ""
                        if callee.startswith("__12C_MENU_STATE") and r7 is not None and r7 in self.screens:
                            sc = self.screens[r7]
                            if sc.class_name is None:  # exact: beats the name guessed from list symbols
                                sc.class_name, sc.module = cls, module
                                sc.name = re.sub(r"^C_(?:Menu_)?", "", cls)
                                n += 1
                        if callee.startswith("__12C_MENU_STATE"):
                            r7 = None
        for sym in elf.symbols:
            if sym["name"].endswith("_SCREEN_SET") and sym["shndx"] < len(elf.sections):
                raw = elf.bytes_of(sym)
                for off in range(0, len(raw) - 15, 16):
                    sid = struct.unpack(">I", raw[off:off + 4])[0]
                    tgt = elf.target(sym, off + 12) or ""
                    mo = re.match(r"^SCREEN_(.+)_FIELDS_TABLE$", tgt)
                    if mo and sid in self.screens:
                        sc = self.screens[sid]
                        if sc.module is None:
                            sc.module = module
                        if sc.class_name is None and sc.name != mo.group(1):  # exact as well
                            sc.name = mo.group(1)
                            n += 1
        return n

    # -- queries ------------------------------------------------------------------
    def uses(self, section: str, index: int) -> list[int]:
        """Ids of the screens that show ``section[index]`` of GEN_common."""
        if self._uses is None:
            self._uses = collections.defaultdict(list)
            for sid, sc in self.screens.items():
                for o in sc.objects:
                    if o.section and sid not in self._uses[(o.section, o.index)]:
                        self._uses[(o.section, o.index)].append(sid)
        return self._uses.get((section, index), [])

    def summary(self) -> dict:
        return {"path": self.path, "version": self.version, "build": self.build, "count": len(self.screens),
                "named": sum(1 for s in self.screens.values() if s.name)}


def find_graphic_base(path: str | os.PathLike) -> str | None:
    """``GraphicBase.RNEG.gz`` under ``path`` (a folder) or next to it, if there is one."""
    path = os.fspath(path)
    roots = [path] if os.path.isdir(path) else [os.path.dirname(path)]
    for r in roots:
        for root, dirs, files in os.walk(r):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fn in files:
                if fn.lower().startswith("graphicbase.") and fn.lower().endswith((".gz", ".elf", ".bin")):
                    return os.path.join(root, fn)
    return None


def find_mmi_folder(graphic_base: str) -> str | None:
    """``Application/MMI`` that belongs to a ``Data_Base/Graphics/GraphicBase...`` file, if present."""
    d = os.path.dirname(os.path.abspath(graphic_base))
    for up in range(0, 4):
        cand = os.path.join(d, *([".."] * up), "Application", "MMI")
        if os.path.isdir(cand):
            return os.path.normpath(cand)
    return None
