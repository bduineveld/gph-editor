"""List the hidden codes of the unit: the table C_BCM_Cheat_Code::m_code_db in Application/BCM/cheat_code/bcm_cheat_code.gz
(unpacked: skip the first byte, then zlib).  usage: cheatcodes.py bcm_cheat_code.elf"""
import struct, sys
d = open(sys.argv[1], "rb").read()
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
    syms.append((cstr(strsec["off"] + n), v, sz, shndx))
ro = next(s for s in secs if s["sname"] == ".rodata")
db = next(s for s in syms if s[0].endswith("m_code_db")); sec = secs[db[3]]
for i in range(db[2] // 12):
    a, ev, b = struct.unpack(">III", d[sec["off"]+db[1]+i*12: sec["off"]+db[1]+i*12+12])
    print(f"{i:2d} ev={ev:3d} {cstr(ro['off']+a)!r:14} {cstr(ro['off']+b)!r}")
