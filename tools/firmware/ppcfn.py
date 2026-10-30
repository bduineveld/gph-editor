"""Rough PowerPC ELF function reader: prints calls (via relocations) and immediate loads for one function."""
import struct, sys
path, fname = sys.argv[1], sys.argv[2]
d = open(path, "rb").read()
shoff, shentsize, shnum, shstrndx = struct.unpack(">I", d[32:36])[0], *struct.unpack(">HHH", d[46:52])
secs = []
for i in range(shnum):
    o = shoff + i * shentsize
    name, typ, flags, addr, off, size, link, info, align, entsize = struct.unpack(">IIIIIIIIII", d[o:o+40])
    secs.append(dict(name=name, type=typ, off=off, size=size, link=link, info=info, entsize=entsize))
strtab = secs[shstrndx]
def sname(s): 
    o = strtab["off"] + s["name"]; return d[o:d.index(b"\0", o)].decode()
for s in secs: s["sname"] = sname(s)
sym = next(s for s in secs if s["type"] == 2)
symstr = secs[sym["link"]]
def symname(i):
    o = symstr["off"] + i; return d[o:d.index(b"\0", o)].decode()
syms = []
for i in range(sym["size"] // 16):
    o = sym["off"] + i * 16
    n, v, sz, info, other, shndx = struct.unpack(">IIIBBH", d[o:o+16])
    syms.append((symname(n), v, sz, shndx))
text = next(s for s in secs if s["sname"] == ".text")
textidx = secs.index(text)
rela = next((s for s in secs if s["sname"] == ".rela.text"), None)
relocs = {}
if rela:
    for i in range(rela["size"] // 12):
        o = rela["off"] + i * 12
        roff, rinfo, add = struct.unpack(">IIi", d[o:o+12])
        relocs[roff] = (syms[rinfo >> 8][0], rinfo & 0xff, add)
fn = next((s for s in syms if s[0] == fname and s[3] == textidx), None)
if not fn: sys.exit(f"{fname} not found in .text")
name, addr, size, _ = fn
if size == 0:  # size unknown: until next symbol
    nxt = min((s[1] for s in syms if s[3] == textidx and s[1] > addr), default=addr + 0x400); size = nxt - addr
print(f"{name} @ {addr:#x} size {size}")
regs = {}
for pc in range(addr, addr + size, 4):
    w = struct.unpack(">I", d[text["off"] + pc: text["off"] + pc + 4])[0]
    op = w >> 26; rD = (w >> 21) & 31; rA = (w >> 16) & 31; imm = w & 0xffff; simm = imm - 0x10000 if imm & 0x8000 else imm
    line = None
    if op == 14 and rA == 0: regs[rD] = simm; line = f"li   r{rD}, {simm}"
    elif op == 15 and rA == 0: regs[rD] = imm << 16; line = f"lis  r{rD}, {imm:#x}"
    elif op == 24 and rA in regs: regs[rD] = regs[rA] | imm; line = f"ori  r{rD}, r{rA}, {imm:#x} -> {regs[rD]:#x}"
    elif op == 14 and rA in regs and rA != 1: regs[rD] = regs[rA] + simm; line = f"addi r{rD}, r{rA}, {simm}"
    if pc in relocs:
        rs, rt, ra = relocs[pc]
        if op == 18: line = f"bl   {rs}   args r3..r7 = {[regs.get(r) for r in range(3, 8)]}"
        else: line = (line or f"op{op}") + f"   [{rs}+{ra}]"
    if line: print(f"  {pc:#06x}: {line}")
