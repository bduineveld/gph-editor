"""Small PowerPC disassembler for ELF32 relocatables (enough opcodes to follow control flow), relocations resolved.
usage: dis.py FILE FUNCTION [start end]"""
import struct, sys
path, fname = sys.argv[1], sys.argv[2]
d = open(path, "rb").read()
shoff = struct.unpack(">I", d[32:36])[0]; shentsize, shnum, shstrndx = struct.unpack(">HHH", d[46:52])
secs = []
for i in range(shnum):
    f = struct.unpack(">IIIIIIIIII", d[shoff+i*shentsize:shoff+i*shentsize+40])
    secs.append(dict(name=f[0], type=f[1], off=f[4], size=f[5], link=f[6], info=f[7]))
def cstr(off): return d[off:d.index(b"\0", off)].decode("latin1")
for s in secs: s["sname"] = cstr(secs[shstrndx]["off"] + s["name"])
symsec = next(s for s in secs if s["type"] == 2); strsec = secs[symsec["link"]]
syms = []
for i in range(symsec["size"] // 16):
    n, v, sz, info, other, shndx = struct.unpack(">IIIBBH", d[symsec["off"]+i*16:symsec["off"]+i*16+16])
    syms.append((cstr(strsec["off"] + n), v, sz, shndx, info & 15))
text = next(s for s in secs if s["sname"] == ".text"); ti = secs.index(text)
rela = next(s for s in secs if s["sname"] == ".rela.text")
rel = {}
for i in range(rela["size"] // 12):
    roff, rinfo, add = struct.unpack(">IIi", d[rela["off"]+i*12:rela["off"]+i*12+12])
    rel[roff & ~3] = (syms[rinfo >> 8], rinfo & 255, add)
def target(s, add):
    if s[0]: return s[0] + (f"+{add}" if add else "")
    sec = secs[s[3]]
    if sec["sname"].startswith(".rodata"):
        try:
            t = cstr(sec["off"] + s[1] + add)
            if len(t) >= 2 and all(32 <= ord(c) < 127 or c in "\n\t" for c in t): return f'"{t[:70]}"'
        except Exception: pass
    if sec["sname"] == ".text":
        best = max((x for x in syms if x[3] == s[3] and x[0] and x[1] <= s[1] + add), key=lambda x: x[1], default=None)
        if best: return f"{best[0]}+{s[1]+add-best[1]:#x}"
    return f"{sec['sname']}+{s[1]+add:#x}"
LOAD = {32: "lwz", 33: "lwzu", 34: "lbz", 35: "lbzu", 36: "stw", 37: "stwu", 38: "stb", 39: "stbu", 40: "lhz", 42: "lha", 44: "sth", 46: "lmw", 47: "stmw"}
X31 = {266: "add", 40: "subf", 444: "or", 28: "and", 316: "xor", 235: "mullw", 24: "slw", 536: "srw", 824: "srawi", 23: "lwzx", 87: "lbzx", 279: "lhzx", 151: "stwx", 215: "stbx", 104: "neg", 124: "nor", 8: "subfc", 10: "addc", 138: "adde", 136: "subfe", 202: "addze", 200: "subfze", 234: "addme", 26: "cntlzw", 954: "extsb", 922: "extsh", 491: "divw", 459: "divwu", 60: "andc"}
CC = {(12, 0): "blt", (4, 0): "bge", (12, 1): "bgt", (4, 1): "ble", (12, 2): "beq", (4, 2): "bne"}
fn = next(s for s in syms if s[0] == fname and s[3] == ti)
a0, a1 = fn[1], fn[1] + fn[2]
if len(sys.argv) > 4: a0, a1 = int(sys.argv[3], 16), int(sys.argv[4], 16)
print(f"{fname} @ {fn[1]:#x} size {fn[2]}")
for pc in range(a0, a1, 4):
    w = struct.unpack(">I", d[text["off"]+pc:text["off"]+pc+4])[0]
    op = w >> 26; rD = (w >> 21) & 31; rA = (w >> 16) & 31; rB = (w >> 11) & 31; imm = w & 0xffff; simm = imm - 0x10000 if imm & 0x8000 else imm
    r = rel.get(pc); rs = f"   <{target(r[0], r[2])}>" if r else ""
    if op == 18:
        li = w & 0x3fffffc; li = li - 0x4000000 if li & 0x2000000 else li
        t = f"b{'l' if w & 1 else ''}   " + (target(r[0], r[2]) if r else f"{pc+li:#x}"); rs = ""
    elif op == 16:
        bd = w & 0xfffc; bd = bd - 0x10000 if bd & 0x8000 else bd
        t = f"{CC.get((rD & 28, rA & 3), f'bc{rD},{rA}')} cr{rA >> 2} -> {pc+bd:#x}"
    elif op == 14: t = f"li   r{rD}, {simm}" if rA == 0 else f"addi r{rD}, r{rA}, {simm}"
    elif op == 15: t = f"lis  r{rD}, {imm:#x}" if rA == 0 else f"addis r{rD}, r{rA}, {imm:#x}"
    elif op == 11: t = f"cmpwi cr{rD >> 2}, r{rA}, {simm}"
    elif op == 10: t = f"cmplwi cr{rD >> 2}, r{rA}, {imm}"
    elif op in LOAD: t = f"{LOAD[op]} r{rD}, {simm}(r{rA})"
    elif op == 24: t = f"ori  r{rA}, r{rD}, {imm:#x}" if w != 0x60000000 else "nop"
    elif op == 25: t = f"oris r{rA}, r{rD}, {imm:#x}"
    elif op == 28: t = f"andi. r{rA}, r{rD}, {imm:#x}"
    elif op == 26: t = f"xori r{rA}, r{rD}, {imm:#x}"
    elif op == 12: t = f"addic r{rD}, r{rA}, {simm}"
    elif op == 13: t = f"addic. r{rD}, r{rA}, {simm}"
    elif op == 8: t = f"subfic r{rD}, r{rA}, {simm}"
    elif op == 7: t = f"mulli r{rD}, r{rA}, {simm}"
    elif op == 21: t = f"rlwinm{'.' if w & 1 else ''} r{rA}, r{rD}, sh={rB}, mb={(w >> 6) & 31}, me={(w >> 1) & 31}"
    elif op == 19:
        x = (w >> 1) & 1023
        t = {16: "blr" if rD == 20 else f"b{CC.get((rD & 28, rA & 3), '?')[1:]}lr", 528: "bctr" + ("l" if w & 1 else "")}.get(x, f"op19/{x}")
    elif op == 31:
        x = (w >> 1) & 1023
        if x == 0: t = f"cmpw cr{rD >> 2}, r{rA}, r{rB}"
        elif x == 32: t = f"cmplw cr{rD >> 2}, r{rA}, r{rB}"
        elif x == 444 and rD == rB: t = f"mr   r{rA}, r{rD}"
        elif x == 339: t = f"mfspr r{rD}"
        elif x == 467: t = f"mtspr r{rD}"
        elif x in (444, 28, 316, 24, 536, 124, 60): t = f"{X31[x]}{'.' if w & 1 else ''} r{rA}, r{rD}, r{rB}"
        elif x == 824: t = f"srawi r{rA}, r{rD}, {rB}"
        elif x in (26, 954, 922): t = f"{X31[x]} r{rA}, r{rD}"
        elif x in X31 or (x & 511) in X31: t = f"{X31.get(x, X31.get(x & 511))} r{rD}, r{rA}, r{rB}"
        else: t = f"op31/{x} r{rD}, r{rA}, r{rB}"
    else: t = f"op{op} {w:08x}"
    print(f"  {pc:#06x}: {t}{rs}")
