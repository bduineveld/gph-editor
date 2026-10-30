"""Parse GraphicBase.RNEG (ELF data) -> screens with their graphic objects. Prints stats or JSON."""
import struct, sys, json, collections
path = sys.argv[1]; mode = sys.argv[2] if len(sys.argv) > 2 else "stats"
d = open(path, "rb").read()
shoff = struct.unpack(">I", d[32:36])[0]; shentsize, shnum, shstrndx = struct.unpack(">HHH", d[46:52])
secs = []
for i in range(shnum):
    o = shoff + i * shentsize; f = struct.unpack(">IIIIIIIIII", d[o:o+40])
    secs.append(dict(name=f[0], type=f[1], off=f[4], size=f[5], link=f[6], info=f[7]))
def cstr(off): return d[off:d.index(b"\0", off)].decode(errors="replace")
for s in secs: s["sname"] = cstr(secs[shstrndx]["off"] + s["name"])
symsec = next(s for s in secs if s["type"] == 2); strsec = secs[symsec["link"]]
syms = []
for i in range(symsec["size"] // 16):
    o = symsec["off"] + i * 16; n, v, sz, info, other, shndx = struct.unpack(">IIIBBH", d[o:o+16])
    syms.append(dict(name=cstr(strsec["off"] + n), value=v, size=sz, shndx=shndx))
data = next(s for s in secs if s["sname"] == ".data"); di = secs.index(data)
rel = {}
for s in secs:
    if s["type"] == 4 and s["info"] == di:
        for i in range(s["size"] // 12):
            o = s["off"] + i * 12; roff, rinfo, add = struct.unpack(">IIi", d[o:o+12])
            rel[roff] = (syms[rinfo >> 8]["name"], add)
byname = {s["name"]: s for s in syms if s["shndx"] == di}
def w32(a): return struct.unpack(">I", d[data["off"] + a: data["off"] + a + 4])[0]
def target(a):
    r = rel.get(a); return r[0] if r else None
# screen index: 12-byte entries (screen_id, ?, ptr)
idx = byname["pIndexOfScreenDataBase"]
screens = {}
for a in range(idx["value"], idx["value"] + idx["size"], 12):
    sid, x, _ = w32(a), w32(a + 4), w32(a + 8); ptr = target(a + 8)
    if ptr is None: continue
    s = byname[ptr]; objs = []
    for b in range(s["value"], s["value"] + s["size"], 12):
        oid, ref, _ = w32(b), w32(b + 4), w32(b + 8)
        if oid == 0 and ref == 0: continue
        objs.append(dict(obj=oid, type=ref >> 24, sub=(ref >> 16) & 0xff, index=ref & 0xffff, list=target(b + 8)))
    screens[sid] = dict(x=x, table=ptr, objects=objs)
if mode == "stats":
    print("screens:", len(screens))
    bytype = collections.defaultdict(list)
    for sid, s in screens.items():
        for o in s["objects"]: bytype[o["type"]].append(o)
    for t in sorted(bytype):
        os_ = bytype[t]; subs = collections.Counter(o["sub"] for o in os_)
        prefixes = collections.Counter(o["obj"] // 10000 for o in os_ if o["obj"] >= 10000)
        print(f"type {t:3d}: n={len(os_):5d} max_index={max(o['index'] for o in os_):5d} subs={dict(subs.most_common(4))} obj_prefix={dict(prefixes.most_common(3))} lists={sum(1 for o in os_ if o['list'])}")
    xs = collections.Counter(s["x"] for s in screens.values()); print("index 2nd word values:", dict(xs.most_common(8)))
elif mode == "json":
    json.dump(screens, open(sys.argv[3], "w"), indent=0)
    print("written", sys.argv[3])
elif mode == "screen":
    sid = int(sys.argv[3]); s = screens[sid]; print(sid, s["table"], "x=", s["x"])
    for o in s["objects"]: print(f"  obj {o['obj']:7d}  type {o['type']:3d} sub {o['sub']:3d} index {o['index']:5d}  {o['list'] or ''}")
