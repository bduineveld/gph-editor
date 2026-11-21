"""Where a press leads: the screen transitions in the firmware's MMI modules.

The screen database (:mod:`gph.screens`) says which widgets make up a screen, not what happens
when one is pressed.  That is code, in the MMI modules (``Application/MMI/<x>/mmi_<x>.gz``,
PowerPC ELF relocatables that kept their C++ symbol names).  Every menu screen is a class
``C_Menu_<screen>`` derived from ``C_MENU_STATE``, and the classes are generated from one
pattern, which makes them readable by a program:

* the constructor passes the screen id to ``C_MENU_STATE::C_MENU_STATE(zone, kbd, int, id)``
  (in r7), so a class can be tied to a screen of the database;
* ``Verify(object id, event)`` returns the number of the action that handles an event on one
  of the screen's objects (0 = not handled).  The object ids are those of the database;
  event 337 is "selected" (the OK press on a menu line, button or list);
* ``InvokeAction(number)`` jumps through a table to ``Action_EVT_<widget>_<screen>_<item>_SI``;
* an action that opens another screen calls ``C_MENU_STATE::ChangeState(screen id, ...)``;
  ``ChangeState(short)`` with -1 goes back to the previous screen.

So for every object of a screen: run ``Verify`` with the select event, run ``InvokeAction``
with the answer, note which action it reaches and collect the ``ChangeState`` calls in it.
``Verify`` and ``InvokeAction`` are run in a small PowerPC interpreter (they are straight
compare-and-branch code plus a jump table); calls out of them are not followed but answered
with 1.  The actions are not run: their calls are listed with the constants loaded just
before, which finds every target they *can* open.  Which one the unit takes when an action has
several (a route is active or not ...) depends on its state and is not decided here.

Not covered: screens opened by a message to another module (the radio's "Audio settings"),
the hard keys of the unit, and whether a line is shown at all.

Only the standard library is used.
"""

from __future__ import annotations

import bisect
import os
import struct
import zlib

from .screens import Elf, ScreenDatabase, ScreenError, unpack_module

SELECT = 337                      #: TYPE_CUM_SCRN_EVT value of "object selected"
VERIFY = "Ul17TYPE_CUM_SCRN_EVT"  #: mangled argument list of Verify(unsigned long, TYPE_CUM_SCRN_EVT)
M32 = 0xFFFFFFFF
# made-up addresses for the interpreter: sections of the module, the object, the stack, anything external
TEXT, RODATA, DATA, THIS, STACK, EXTERN = 0x100000, 0x800000, 0xA00000, 0x4000000, 0x7000000, 0x9000000


class Unsupported(Exception):
    """The interpreter met something it does not know; the object is skipped."""


def _sx(v: int, bits: int) -> int:
    return v - (1 << bits) if v & (1 << (bits - 1)) else v


def _mask(mb: int, me: int) -> int:
    m, i = 0, mb
    while True:
        m |= 1 << (31 - i)
        if i == me:
            return m
        i = (i + 1) & 31


def _rotl(v: int, n: int) -> int:
    n &= 31
    return ((v << n) | (v >> (32 - n))) & M32 if n else v


class Module:
    """The code of one MMI module: functions by name, and the relocations with their type."""

    def __init__(self, elf: Elf, name: str):
        self.elf, self.name = elf, name
        self.text = elf.by_name[".text"]
        sec = elf.sections[self.text]
        self.code = elf.data[sec["off"]: sec["off"] + sec["size"]]
        self.funcs: dict[str, tuple[int, int]] = {s["name"]: (s["value"], s["size"]) for s in elf.symbols
                                                  if s["name"] and s["shndx"] == self.text and s["type"] == 2}
        self._starts = sorted((v, n) for n, (v, _size) in self.funcs.items())
        self.text_rel = {off & ~3: r for off, r in self._rela(".rela.text").items()}
        self.ro_rel = self._rela(".rela.rodata")
        ro = elf.by_name.get(".rodata")
        rosec = elf.sections[ro] if ro is not None else None
        self.rodata = elf.data[rosec["off"]: rosec["off"] + rosec["size"]] if rosec else b""
        self.base = {i: (TEXT if s["sname"] == ".text" else RODATA if s["sname"].startswith(".rodata") else DATA)
                     for i, s in enumerate(elf.sections) if s["sname"] == ".text" or s["sname"].startswith(".rodata") or s["sname"] == ".data"}

    def _rela(self, section: str) -> dict[int, tuple[dict, int, int]]:
        """offset -> (symbol, relocation type, addend)"""
        idx = self.elf.by_name.get(section)
        out: dict[int, tuple[dict, int, int]] = {}
        if idx is None:
            return out
        sec, data = self.elf.sections[idx], self.elf.data
        for i in range(sec["size"] // 12):
            off, info, add = struct.unpack_from(">IIi", data, sec["off"] + i * 12)
            out[off] = (self.elf.symbols[info >> 8], info & 0xFF, add)
        return out

    def word(self, pc: int) -> int:
        return struct.unpack_from(">I", self.code, pc)[0]

    def at(self, addr: int) -> str:
        """Name of the function that holds ``addr``."""
        i = bisect.bisect_right(self._starts, (addr, "\U0010ffff")) - 1
        return self._starts[i][1] if i >= 0 else hex(addr)

    def address(self, sym: dict, add: int) -> int | None:
        """Interpreter address of a relocation target; None when it lies outside the module."""
        base = self.base.get(sym["shndx"])
        return None if base is None else (base + sym["value"] + add) & M32

    def callee(self, pc: int, w: int) -> str:
        """The function a ``bl`` at ``pc`` calls."""
        rel = self.text_rel.get(pc)
        if rel is None:
            return self.at(pc + _sx(w & 0x3FFFFFC, 26))
        sym, _type, add = rel
        if sym["name"]:
            return sym["name"]
        if sym["shndx"] == self.text:
            return self.at(sym["value"] + add)
        return f"{self.elf.sections[sym['shndx']]['sname']}+{sym['value'] + add:#x}"

    def calls(self, fname: str):
        """Every call in a function, with the constants that are in the registers at that point:
        ``(callee, {register: value or None})``.  Straight-line tracking, good for ``li r4, 36; bl``."""
        start, size = self.funcs[fname]
        reg: dict[int, int | None] = {}
        for pc in range(start, start + size, 4):
            w = self.word(pc)
            op, rd, ra, rb, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31, w & 0xFFFF
            rel = pc in self.text_rel
            if op == 18 and w & 1:
                yield self.callee(pc, w), dict(reg)
                for k in (0, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12):   # volatile registers
                    reg.pop(k, None)
            elif op == 14 and not rel:                            # addi / li
                reg[rd] = _sx(imm, 16) if ra == 0 else (reg[ra] + _sx(imm, 16) if reg.get(ra) is not None else None)
            elif op == 15 and not rel:                            # addis / lis
                reg[rd] = (imm << 16) if ra == 0 else None
            elif op == 24 and not rel:                            # ori
                reg[ra] = (reg[rd] | imm) if reg.get(rd) is not None else None
            elif op == 31 and (w >> 1) & 1023 == 444 and rd == rb:  # mr
                reg[ra] = reg.get(rd)
            elif op in (32, 34, 40, 42, 14, 15):                  # loads, and the relocated forms of the above
                reg[rd] = None
            elif op in (24, 25, 28, 20, 21):
                reg[ra] = None
            elif op == 31:
                reg[ra if (w >> 1) & 1023 in (444, 28, 316, 24, 536, 824, 124, 954, 922, 26, 60) else rd] = None


class Cpu:
    """Just enough of a 32-bit PowerPC to run ``Verify`` and ``InvokeAction`` of a screen class."""

    def __init__(self, mod: Module):
        self.mod = mod
        self.mem: dict[int, int] = {}

    def load(self, addr: int, n: int) -> int:
        m = self.mod
        if RODATA <= addr < RODATA + len(m.rodata):
            off = addr - RODATA
            rel = m.ro_rel.get(off)
            if rel and n == 4:                                   # a pointer in a table (jump tables)
                v = m.address(rel[0], rel[2])
                if v is None:
                    return 0
                return (v - addr) & M32 if rel[1] == 26 else v   # R_PPC_REL32: relative to the entry
            return int.from_bytes(m.rodata[off:off + n], "big")
        return self.mem.get(addr, 0) & ((1 << (8 * n)) - 1)

    def run(self, fname: str, args: list[int], on_call, limit: int = 4000) -> int:
        """Run a function and return r3.  ``on_call(callee, registers)`` answers every call."""
        m = self.mod
        start, size = m.funcs[fname]
        r = [0] * 32
        r[1] = STACK
        for i, a in enumerate(args):
            r[3 + i] = a & M32
        self.mem = {}
        cr = [0] * 8
        ca = ctr = 0
        lr = 0xDEAD0000
        pc = start
        steps = 0

        def setcr(f, a, b):
            cr[f] = 8 if a < b else 4 if a > b else 2

        def crbit(i):
            return 1 if cr[i >> 2] & (8 >> (i & 3)) else 0

        while True:
            steps += 1
            if steps > limit:
                raise Unsupported("too many steps")
            if not start <= pc < start + size:
                raise Unsupported(f"left the function at {pc:#x}")
            w = m.word(pc)
            op, D, A, B, imm = w >> 26, (w >> 21) & 31, (w >> 16) & 31, (w >> 11) & 31, w & 0xFFFF
            simm = _sx(imm, 16)
            rel = m.text_rel.get(pc)
            nxt = pc + 4
            if rel and op != 18:                                 # an address of the module in the immediate
                v = m.address(rel[0], rel[2])
                if v is None:
                    v = EXTERN
                imm = {4: v & 0xFFFF, 5: (v >> 16) & 0xFFFF, 6: ((v + 0x8000) >> 16) & 0xFFFF}.get(rel[1])
                if imm is None:
                    raise Unsupported(f"relocation type {rel[1]}")
                simm = _sx(imm, 16)
            if op == 18:                                         # b / bl
                if w & 1:
                    r[3] = on_call(m.callee(pc, w), r) & M32
                else:
                    nxt = pc + _sx(w & 0x3FFFFFC, 26)
            elif op == 16:                                       # bc
                ok = True
                if not D & 4:
                    ctr = (ctr - 1) & M32
                    ok = (ctr != 0) != bool(D & 2)
                if not D & 16:
                    ok = ok and bool(crbit(A)) == bool(D & 8)
                if ok:
                    nxt = pc + _sx(w & 0xFFFC, 16)
            elif op == 19:
                x = (w >> 1) & 1023
                if x == 16:                                      # blr (conditional), blrl = virtual call
                    ok = bool(D & 16) or bool(crbit(A)) == bool(D & 8)
                    if ok and w & 1:
                        r[3] = on_call("(virtual)", r) & M32
                    elif ok:
                        return r[3]
                elif x == 528 and w & 1:                         # bctrl = virtual call
                    r[3] = on_call("(virtual)", r) & M32
                elif x == 528:                                   # bctr = jump table
                    nxt = (ctr - TEXT) & M32
                elif x in (193, 289, 449, 257, 33, 129, 225, 417):   # condition register logic
                    a, b = crbit(A), crbit(B)
                    v = {193: a ^ b, 289: 1 - (a ^ b), 449: a | b, 257: a & b, 33: 1 - (a | b), 129: a & (1 - b), 225: 1 - (a & b), 417: a | (1 - b)}[x]
                    cr[D >> 2] = (cr[D >> 2] & ~(8 >> (D & 3))) | ((8 >> (D & 3)) if v else 0)
                else:
                    raise Unsupported(f"opcode 19/{x}")
            elif op == 14:
                r[D] = (simm if A == 0 else r[A] + simm) & M32
            elif op == 15:
                r[D] = ((imm << 16) if A == 0 else r[A] + (imm << 16)) & M32
            elif op in (12, 13):
                v = r[A] + (simm & M32)
                ca, r[D] = v >> 32, v & M32
                if op == 13:
                    setcr(0, _sx(r[D], 32), 0)
            elif op == 8:
                v = (~r[A] & M32) + (simm & M32) + 1
                ca, r[D] = v >> 32, v & M32
            elif op == 7:
                r[D] = (_sx(r[A], 32) * simm) & M32
            elif op == 11:
                setcr(D >> 2, _sx(r[A], 32), simm)
            elif op == 10:
                setcr(D >> 2, r[A], imm)
            elif op == 24:
                r[A] = r[D] | imm
            elif op == 25:
                r[A] = r[D] | (imm << 16)
            elif op == 26:
                r[A] = r[D] ^ imm
            elif op == 27:
                r[A] = r[D] ^ (imm << 16)
            elif op == 28:
                r[A] = r[D] & imm
                setcr(0, _sx(r[A], 32), 0)
            elif op == 29:
                r[A] = r[D] & (imm << 16)
                setcr(0, _sx(r[A], 32), 0)
            elif op == 21:                                       # rlwinm
                r[A] = _rotl(r[D], B) & _mask((w >> 6) & 31, (w >> 1) & 31)
                if w & 1:
                    setcr(0, _sx(r[A], 32), 0)
            elif op in (32, 33, 34, 35, 40, 41, 42, 43):         # loads
                ea = ((r[A] if A else 0) + simm) & M32
                v = self.load(ea, 4 if op < 34 else 1 if op < 40 else 2)
                r[D] = _sx(v, 16) & M32 if op in (42, 43) else v
                if op & 1:
                    r[A] = ea
            elif op in (36, 37, 38, 39, 44, 45):                 # stores
                ea = ((r[A] if A else 0) + simm) & M32
                self.mem[ea] = r[D]
                if op & 1:
                    r[A] = ea
            elif op in (46, 47):                                 # lmw / stmw: registers saved on the stack
                pass
            elif op == 31:
                x = (w >> 1) & 1023
                a, b, d = r[A], r[B], r[D]
                out, tgt = None, D
                if x == 0: setcr(D >> 2, _sx(a, 32), _sx(b, 32))
                elif x == 32: setcr(D >> 2, a, b)
                elif x == 266: out = a + b
                elif x == 40: out = b - a
                elif x == 8: out = (~a & M32) + b + 1; ca = out >> 32
                elif x == 10: out = a + b; ca = out >> 32
                elif x == 138: out = a + b + ca; ca = out >> 32
                elif x == 136: out = (~a & M32) + b + ca; ca = out >> 32
                elif x == 202: out = a + ca; ca = out >> 32
                elif x == 200: out = (~a & M32) + ca; ca = out >> 32
                elif x == 234: out = a + ca + M32; ca = out >> 32
                elif x == 232: out = (~a & M32) + ca + M32; ca = out >> 32
                elif x == 104: out = -a
                elif x == 235: out = _sx(a, 32) * _sx(b, 32)
                elif x == 444: tgt, out = A, d | b
                elif x == 28: tgt, out = A, d & b
                elif x == 316: tgt, out = A, d ^ b
                elif x == 124: tgt, out = A, ~(d | b)
                elif x == 60: tgt, out = A, d & ~b
                elif x == 412: tgt, out = A, d | ~b
                elif x == 284: tgt, out = A, ~(d ^ b)
                elif x == 24: tgt, out = A, (d << (b & 63)) if b & 63 < 32 else 0
                elif x == 536: tgt, out = A, (d >> (b & 63)) if b & 63 < 32 else 0
                elif x == 824:                                   # srawi
                    sv = _sx(d, 32)
                    ca = 1 if sv < 0 and d & ((1 << B) - 1) else 0
                    tgt, out = A, sv >> B
                elif x == 954: tgt, out = A, _sx(d & 0xFF, 8)
                elif x == 922: tgt, out = A, _sx(d & 0xFFFF, 16)
                elif x == 26: tgt, out = A, 32 - d.bit_length()
                elif x == 23: out = self.load(((r[A] if A else 0) + b) & M32, 4)
                elif x == 87: out = self.load(((r[A] if A else 0) + b) & M32, 1)
                elif x == 279: out = self.load(((r[A] if A else 0) + b) & M32, 2)
                elif x == 151: self.mem[((r[A] if A else 0) + b) & M32] = d
                elif x == 339: out = lr if ((w >> 16) & 31) | ((w >> 6) & 0x3E0) == 8 else ctr
                elif x == 467:
                    if ((w >> 16) & 31) | ((w >> 6) & 0x3E0) == 8: lr = d
                    else: ctr = d
                elif x == 19: out = sum(cr[i] << (28 - 4 * i) for i in range(8))
                elif x == 144: pass
                else:
                    raise Unsupported(f"opcode 31/{x}")
                if out is not None:
                    r[tgt] = out & M32
                    if w & 1:
                        setcr(0, _sx(r[tgt], 32), 0)
            else:
                raise Unsupported(f"opcode {op}")
            pc = nxt


# ---------------------------------------------------------------------------
def _change(callee: str, value: int | None) -> tuple[str, int | None] | None:
    """A ``ChangeState`` call as ("screen", id) or ("back", steps); None for any other call."""
    if not callee.startswith("ChangeState__12C_MENU_STATE"):
        return None
    if value is not None and value >= 1 << 31:
        value -= 1 << 32
    return ("back" if "sbT2" in callee else "screen", value)


def _action_targets(mod: Module, fname: str, depth: int = 0, seen: set | None = None) -> list[tuple[str, int | None]]:
    """The ``ChangeState`` calls an action can make, following its helpers in the same class."""
    seen = seen if seen is not None else set()
    seen.add(fname)
    out = []
    for callee, reg in mod.calls(fname):
        ch = _change(callee, reg.get(4))
        if ch:
            out.append(ch)
        elif callee in mod.funcs and callee not in seen and depth < 2 and (
                callee.startswith("Action_") or callee.split("__")[-1] == fname.split("__")[-1]):
            out += _action_targets(mod, callee, depth + 1, seen)
    return out


def _screen_id(mod: Module, cls: str) -> int | None:
    """The screen id a class's constructor passes to ``C_MENU_STATE`` (``cls`` is mangled: ``19C_Menu_...``)."""
    sid = None
    for f in mod.funcs:
        if f == "__" + cls or (f.startswith("__" + cls) and not f.startswith("__" + cls + "_")):
            for callee, reg in mod.calls(f):
                if callee.startswith("__12C_MENU_STATE14"):
                    sid = reg.get(7)
    return sid


def module_links(db: ScreenDatabase, mod: Module) -> dict[int, dict[int, dict]]:
    """``{screen id: {object id: link}}`` for the screen classes of one module.

    A link is ``{"action": name, "to": [screen ids], "back": bool, "var": bool}``: the action
    function the select event reaches, the screens it can open, whether it can go back to the
    previous screen and whether it opens a screen whose id is computed (unknown here).  An
    action with none of these does its work on the screen itself.
    """
    cpu = Cpu(mod)
    out: dict[int, dict[int, dict]] = {}
    for f in list(mod.funcs):
        if not (f.startswith("Verify__") and f.endswith(VERIFY)):
            continue
        cls = f[len("Verify__"):-len(VERIFY)]
        invoke = f"InvokeAction__{cls}Ui"
        sid = _screen_id(mod, cls)
        if sid is None or sid not in db.screens or invoke not in mod.funcs:
            continue
        items = out.setdefault(sid, {})
        for obj in dict.fromkeys(o.obj_id for o in db.screens[sid].objects if o.obj_id != sid):
            try:
                number = cpu.run(f, [THIS, obj, SELECT], lambda _c, _r: 1)
                if not number:
                    continue
                called: list[tuple[str, int]] = []
                cpu.run(invoke, [THIS, number], lambda c, r: called.append((c, r[4])) or 1)
            except Unsupported:
                continue
            actions = [c for c, _a in called if c in mod.funcs and c.startswith("Action_")]
            targets = [ch for ch in (_change(c, a) for c, a in called) if ch]
            if not actions and not targets:
                continue
            for a in actions:
                targets += _action_targets(mod, a)
            items[obj] = {"action": actions[0].split("__")[0] if actions else None,
                          "to": list(dict.fromkeys(v for k, v in targets if k == "screen" and v is not None)),
                          "back": any(k == "back" for k, _v in targets),
                          "var": any(k == "screen" and v is None for k, v in targets)}
    return out


def read_links(db: ScreenDatabase, folder: str | os.PathLike) -> dict[int, dict[int, dict]]:
    """The links of every MMI module under ``folder`` (``Application/MMI``)."""
    out: dict[int, dict[int, dict]] = {}
    for root, _dirs, files in os.walk(os.fspath(folder)):
        for fn in sorted(files):
            low = fn.lower()
            if fn.startswith("._") or not (low.startswith(("mmi_", "lib_")) and low.endswith(".gz")):
                continue
            try:
                with open(os.path.join(root, fn), "rb") as fh:
                    elf = Elf(unpack_module(fh.read()))
            except (ScreenError, OSError, zlib.error):
                continue
            if ".text" not in elf.by_name:
                continue
            for sid, items in module_links(db, Module(elf, low[:-3])).items():
                out.setdefault(sid, {}).update(items)
    return {sid: items for sid, items in sorted(out.items()) if items}


def links_to_json(links: dict[int, dict[int, dict]]) -> dict:
    return {str(sid): {str(obj): ln for obj, ln in items.items()} for sid, items in links.items()}


def links_from_json(data: dict) -> dict[int, dict[int, dict]]:
    out: dict[int, dict[int, dict]] = {}
    for sid, items in data.items():
        if isinstance(items, dict):
            out[int(sid)] = {int(obj): {"action": ln.get("action"), "to": [int(t) for t in ln.get("to", [])],
                                        "back": bool(ln.get("back")), "var": bool(ln.get("var"))}
                             for obj, ln in items.items() if isinstance(ln, dict)}
    return out
