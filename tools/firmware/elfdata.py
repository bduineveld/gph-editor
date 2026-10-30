"""Dump a data symbol of a big-endian ELF32 relocatable as 32-bit words with relocation annotations,
or list the places that reference a symbol.  usage: elfdata.py FILE dump SYMBOL | refs SYMBOL | list REGEX"""
import struct, sys, re
path, mode, arg = sys.argv[1], sys.argv[2], sys.argv[3]
d = open(path, "rb").read()
shoff = struct.unpack(">I", d[32:36])[0]; shentsize, shnum, shstrndx = struct.unpack(">HHH", d[46:52])
secs = []
for i in range(shnum):
    o = shoff + i * shentsize
    f = struct.unpack(">IIIIIIIIII", d[o:o+40])
    secs.append(dict(name=f[0], type=f[1], addr=f[3], off=f[4], size=f[5], link=f[6], info=f[7], entsize=f[9]))
def cstr(off): return d[off:d.index(b"\0", off)].decode(errors="replace")
for s in secs: s["sname"] = cstr(secs[shstrndx]["off"] + s["name"])
symsec = next(s for s in secs if s["type"] == 2); strsec = secs[symsec["link"]]
syms = []
for i in range(symsec["size"] // 16):
    o = symsec["off"] + i * 16
    n, v, sz, info, other, shndx = struct.unpack(">IIIBBH", d[o:o+16])
    syms.append(dict(name=cstr(strsec["off"] + n), value=v, size=sz, shndx=shndx, type=info & 0xf))
relocs = {}  # (target section index) -> {offset: (symname, type, addend)}
for s in secs:
    if s["type"] == 4:  # RELA
        tgt = s["info"]; relocs.setdefault(tgt, {})
        for i in range(s["size"] // 12):
            o = s["off"] + i * 12
            roff, rinfo, add = struct.unpack(">IIi", d[o:o+12])
            sym = syms[rinfo >> 8]
            name = sym["name"] or (secs[sym["shndx"]]["sname"] if sym["shndx"] < len(secs) else "?")
            relocs[tgt][roff] = (name, rinfo & 0xff, add)
def sym_at(shndx, addr):
    best = None
    for s in syms:
        if s["shndx"] == shndx and s["name"] and s["value"] <= addr and (best is None or s["value"] > best["value"]):
            best = s
    return best
if mode == "list":
    for s in syms:
        if re.search(arg, s["name"]) and s["shndx"] < len(secs):
            print(f"{s['size']:6d} {secs[s['shndx']]['sname']:8s} {s['name']}")
elif mode == "dump":
    s = next(x for x in syms if x["name"] == arg); sec = secs[s["shndx"]]
    size = s["size"] or 64
    print(f"{arg}: section {sec['sname']} +{s['value']:#x} size {size}")
    rel = relocs.get(s["shndx"], {})
    for a in range(s["value"], s["value"] + size, 4):
        w = struct.unpack(">I", d[sec["off"] + a: sec["off"] + a + 4])[0]
        r = rel.get(a)
        note = f"   -> {r[0]}{'+%d' % r[2] if r[2] else ''}" if r else ""
        print(f"  +{a - s['value']:04x}: {w:08x} {w:>10d}  {struct.pack('>I', w).hex(' ', 2)}{note}")
elif mode == "refs":
    for tgt, rs in relocs.items():
        for off, (name, typ, add) in rs.items():
            if name == arg:
                holder = sym_at(tgt, off)
                print(f"referenced from {secs[tgt]['sname']}+{off:#x}  in {holder['name'] if holder else '?'}+{off - holder['value'] if holder else 0:#x}")
