"""Find PowerPC instructions carrying a given 16-bit immediate in .text and name the enclosing function."""
import struct, sys
path, val = sys.argv[1], int(sys.argv[2])
d = open(path, "rb").read()
shoff = struct.unpack(">I", d[32:36])[0]; shentsize, shnum, shstrndx = struct.unpack(">HHH", d[46:52])
secs = []
for i in range(shnum):
    o = shoff + i * shentsize; f = struct.unpack(">IIIIIIIIII", d[o:o+40])
    secs.append(dict(name=f[0], type=f[1], off=f[4], size=f[5], link=f[6]))
def cstr(off): return d[off:d.index(b"\0", off)].decode(errors="replace")
for s in secs: s["sname"] = cstr(secs[shstrndx]["off"] + s["name"])
symsec = next(s for s in secs if s["type"] == 2); strsec = secs[symsec["link"]]
text = next(s for s in secs if s["sname"] == ".text"); ti = secs.index(text)
funcs = []
for i in range(symsec["size"] // 16):
    o = symsec["off"] + i * 16; n, v, sz, info, other, shndx = struct.unpack(">IIIBBH", d[o:o+16])
    if shndx == ti and (info & 0xf) == 2: funcs.append((v, cstr(strsec["off"] + n)))
funcs.sort()
import bisect
names = {7: "mulli", 8: "subfic", 10: "cmplwi", 11: "cmpwi", 12: "addic", 14: "addi/li", 15: "addis/lis", 24: "ori", 28: "andi"}
for pc in range(0, text["size"], 4):
    w = struct.unpack(">I", d[text["off"] + pc: text["off"] + pc + 4])[0]
    op = w >> 26; imm = w & 0xffff
    if op in names and imm == (val & 0xffff):
        k = bisect.bisect_right(funcs, (pc, "\xff")) - 1
        print(f"{pc:#07x} {names[op]:9s} r{(w>>21)&31},r{(w>>16)&31}  in {funcs[k][1] if k >= 0 else '?'}")
