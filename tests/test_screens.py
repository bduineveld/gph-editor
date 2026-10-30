"""gph.screens: the firmware's screen database (GraphicBase.RNEG)."""

import gzip
import json
import os
import struct
import threading
import urllib.request
import zlib

import pytest

from gph.screens import Elf, ScreenDatabase, ScreenError, find_graphic_base, unpack_module

HERE = os.path.dirname(__file__)
REAL = os.path.join(HERE, "..", "tools", "firmware", "GraphicBase.RNEG.elf")


# ---------------------------------------------------------------------------
# a tiny big-endian ELF32 relocatable with one .data section, like the firmware's data modules
def build_elf(objects: list[tuple[str, bytes, list[tuple[int, str]]]]) -> bytes:
    """objects: (symbol name, bytes, [(offset inside the bytes, symbol it points at)])."""
    data = bytearray()
    syms = []     # (name, value, size)
    relocs = []   # (offset in .data, symbol index, addend)
    index_of = {}
    for name, blob, _ in objects:
        while len(data) % 4:
            data.append(0)
        index_of[name] = len(syms) + 1  # 0 is the null symbol
        syms.append((name, len(data), len(blob)))
        data += blob
    for name, _blob, ptrs in objects:
        base = syms[index_of[name] - 1][1]
        for off, target in ptrs:
            relocs.append((base + off, index_of[target], 0))
    strtab = bytearray(b"\0")
    symtab = bytearray(bytes(16))
    for name, value, size in syms:
        noff = len(strtab)
        strtab += name.encode() + b"\0"
        symtab += struct.pack(">IIIBBH", noff, value, size, 0x11, 0, 1)  # global object in section 1
    rela = bytearray()
    for off, si, add in relocs:
        rela += struct.pack(">IIi", off, (si << 8) | 1, add)  # R_PPC_ADDR32
    shstr = bytearray(b"\0")
    names = {}
    for n in (".data", ".rela.data", ".symtab", ".strtab", ".shstrtab"):
        names[n] = len(shstr)
        shstr += n.encode() + b"\0"
    blobs = [bytes(data), bytes(rela), bytes(symtab), bytes(strtab), bytes(shstr)]
    offsets, pos = [], 52
    for b in blobs:
        offsets.append(pos)
        pos += len(b)
        while pos % 4:
            pos += 1
    shoff = pos
    body = bytearray()
    for b in blobs:
        body += b
        while (52 + len(body)) % 4:
            body.append(0)
    def sh(name, typ, off, size, link=0, info=0, align=4, entsize=0):
        return struct.pack(">IIIIIIIIII", name, typ, 0, 0, off, size, link, info, align, entsize)
    shdrs = sh(0, 0, 0, 0, align=0)
    shdrs += sh(names[".data"], 1, offsets[0], len(blobs[0]))
    shdrs += sh(names[".rela.data"], 4, offsets[1], len(blobs[1]), link=3, info=1, entsize=12)
    shdrs += sh(names[".symtab"], 2, offsets[2], len(blobs[2]), link=4, info=1, entsize=16)
    shdrs += sh(names[".strtab"], 3, offsets[3], len(blobs[3]), align=1)
    shdrs += sh(names[".shstrtab"], 3, offsets[4], len(blobs[4]), align=1)
    ident = b"\x7fELF" + bytes([1, 2, 1, 0]) + bytes(8)
    header = ident + struct.pack(">HHIIIIIHHHHHH", 1, 20, 1, 0, 0, shoff, 0, 52, 0, 0, 40, 6, 5)
    return bytes(header) + bytes(body) + shdrs


def ref(typ, sub, index):
    return (typ << 24) | (sub << 16) | index


def sample_elf() -> bytes:
    entry = lambda oid, r: struct.pack(">III", oid, r, 0)
    screen7 = entry(7, ref(10, 0, 0)) + entry(7, ref(12, 0, 5)) + entry(20003, ref(16, 2, 3)) + entry(180004, ref(28, 19, 1)) + bytes(12)
    screen8 = entry(8, ref(10, 0, 1)) + entry(99, ref(43, 13, 2)) + bytes(12)
    index = struct.pack(">III", 7, 0x15010000, 0) + struct.pack(">III", 8, 0x15010000, 0)
    return build_elf([
        ("pIndexOfScreenDataBase", index, [(8, "pDataBase000007"), (20, "pDataBase000008")]),
        ("pDataBase000007", screen7, [(32, "List_GDO_it_1_CLOCK_F3_Z5_2")]),
        ("pDataBase000008", screen8, []),
        ("List_GDO_it_1_CLOCK_F3_Z5_2", struct.pack(">HHH", 2, 108, 109), []),
        ("EXP_BuildId", struct.pack(">I", 42), []),
    ])


def packed(elf: bytes) -> bytes:
    return b"\x08" + zlib.compress(elf)


# ---------------------------------------------------------------------------
def test_unpack_module():
    elf = sample_elf()
    assert unpack_module(elf) == elf
    assert unpack_module(packed(elf)) == elf
    assert unpack_module(gzip.compress(elf)) == elf
    with pytest.raises(ScreenError):
        unpack_module(b"not a module at all")


def test_elf_reader():
    e = Elf(sample_elf())
    assert e.by_name[".data"] == 1
    assert e.symbol["pDataBase000007"]["size"] == 5 * 12
    assert e.target(e.symbol["pIndexOfScreenDataBase"], 8) == "pDataBase000007"
    assert e.target(e.symbol["pIndexOfScreenDataBase"], 0) is None


def test_parse_screens():
    db = ScreenDatabase.from_bytes(packed(sample_elf()), "x.gz")
    assert sorted(db.screens) == [7, 8]
    assert db.build == 42
    s7 = db.screens[7]
    assert s7.name == "CLOCK_F3_Z5_2"
    assert [(o.section, o.index) for o in s7.objects] == [("BKGD2000", 0), ("FXTX2006", 5), ("ITXT2000", 3), ("MENU2006", 1)]
    assert [o.fixed for o in s7.objects] == [True, True, False, False]
    assert s7.objects[2].values == [108, 109] and s7.objects[2].list_name == "List_GDO_it_1_CLOCK_F3_Z5_2"
    assert s7.objects[3].sub == 19 and s7.objects[3].obj_id == 180004
    s8 = db.screens[8]
    assert s8.name is None
    assert s8.objects[1].section is None and s8.objects[1].to_json()["kind"] == "text?"
    assert db.uses("FXTX2006", 5) == [7] and db.uses("BKGD2000", 1) == [8] and db.uses("BKGD2000", 9) == []
    assert db.summary()["count"] == 2 and db.summary()["named"] == 1
    j = s7.to_json()
    assert j["count"] == 4 and j["sections"] == ["BKGD2000", "FXTX2006", "ITXT2000", "MENU2006"]


@pytest.mark.parametrize("symbol, name", [
    ("List_GDO_Rneg2010_it_1_TUNER_F1_0_Name_Z0_3", "TUNER_F1_0_Name_Z0_3"),
    ("List_GDO_it_JBX_MCpstop_Confirm_Z3_2", "JBX_MCpstop_Confirm_Z3_2"),
    ("List_GDO_Rneg2010_it_NAV_F15_1_JV_Z0_Full_1_diststeer", "NAV_F15_1_JV_Z0_Full_1"),
    ("List_GDO_Rneg2010_vp_PARK_VIDEO_Full_2_AVG", "PARK_VIDEO_Full_2"),
    ("List_GDO_menu_5l_MAP_M1_Z1_0_l5", "MAP_M1_Z1_0"),
    ("List_GDO_8_10_it_AUDIO_F11_Z2", "AUDIO_F11_Z2"),
    ("List_GDO_ctx_menu_3l_AIRC_C1_Z4_1_l2", "AIRC_C1_Z4_1"),
    ("List_GDO_vp_eco2", None),
    ("List_GDO_vp_cdc_rack_z0", None),
])
def test_name_from_list(symbol, name):
    assert ScreenDatabase.name_from_list(symbol) == name


def test_names_json_round_trip(tmp_path):
    db = ScreenDatabase.from_bytes(sample_elf())
    db.screens[8].name, db.screens[8].module, db.screens[8].class_name = "MENU_X_Z1_0", "menu", "C_Menu_MENU_X_Z1_0"
    names = db.names_json()
    assert names == {"7": {"name": "CLOCK_F3_Z5_2", "module": None, "class": None},
                     "8": {"name": "MENU_X_Z1_0", "module": "menu", "class": "C_Menu_MENU_X_Z1_0"}}
    fresh = ScreenDatabase.from_bytes(sample_elf())
    assert fresh.screens[8].name is None
    assert fresh.apply_names(json.loads(json.dumps(names))) == 2
    assert fresh.screens[8].name == "MENU_X_Z1_0" and fresh.screens[8].module == "menu"
    # the server picks the side-car up when no firmware modules sit next to the database
    from gph.server import Editor
    gz = tmp_path / "GraphicBase.RNEG.gz"
    gz.write_bytes(packed(sample_elf()))
    (tmp_path / "GraphicBase.RNEG.gz.names.json").write_text(json.dumps(names))
    ed = Editor()
    ed.open_screens(str(gz))
    assert ed.screens.screens[8].name == "MENU_X_Z1_0"


def test_find_graphic_base(tmp_path):
    assert find_graphic_base(tmp_path) is None
    d = tmp_path / "Data_Base" / "Graphics"
    d.mkdir(parents=True)
    (d / "GraphicBase.RNEG.gz").write_bytes(packed(sample_elf()))
    assert find_graphic_base(tmp_path) == str(d / "GraphicBase.RNEG.gz")
    assert find_graphic_base(d / "GraphicBase.RNEG.gz") == str(d / "GraphicBase.RNEG.gz")


# ---------------------------------------------------------------------------
def test_server_routes(tmp_path):
    from gph.server import make_server

    d = tmp_path / "Data_Base" / "Graphics"
    d.mkdir(parents=True)
    (d / "GraphicBase.RNEG.gz").write_bytes(packed(sample_elf()))
    srv = make_server([], port=0, screens=str(d / "GraphicBase.RNEG.gz"))
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    try:
        def get(path):
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}") as r:
                return json.load(r)
        def post(path, body):
            req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", json.dumps(body).encode(), {"Content-Type": "application/json"})
            with urllib.request.urlopen(req) as r:
                return json.load(r)
        st = get("/api/settings")
        assert st["screens"]["count"] == 2
        lst = get("/api/screens")
        assert [s["id"] for s in lst["screens"]] == [7, 8] and lst["screens"][0]["name"] == "CLOCK_F3_Z5_2"
        det = get("/api/screens/7")
        assert det["count"] == 4 and det["common_file"] is None
        assert det["objects"][2]["values"] == [108, 109] and "fields" not in det["objects"][2]  # no GEN_common open
        with pytest.raises(urllib.error.HTTPError) as ei:
            get("/api/screens/99")
        assert ei.value.code == 404
        # opening the folder again finds the database too; browse lists the file
        r = post("/api/open", {"path": str(tmp_path)})
        assert r["opened"] == [] and r["screens"] is None  # same file: already loaded
        (d / "GraphicBase.RNEG.gz").write_bytes(packed(sample_elf()))
        r = post("/api/screens/open", {"path": str(d / "GraphicBase.RNEG.gz")})
        assert r["screens"]["count"] == 2
        b = post("/api/browse", {"path": str(d)})
        assert [f["name"] for f in b["files"]] == ["GraphicBase.RNEG.gz"]
    finally:
        srv.shutdown()
        srv.server_close()


@pytest.mark.skipif(not os.path.exists(REAL), reason="no firmware GraphicBase (tools/firmware/GraphicBase.RNEG.elf)")
def test_real_database():
    db = ScreenDatabase.load(REAL)
    assert len(db.screens) == 615
    s39 = db.screens[39]
    assert [(o.section, o.index) for o in s39.objects[:4]] == [("BKGD2000", 4), ("FXTX2006", 350), ("FXPI2000", 122), ("MENU2006", 1)]
    assert db.uses("MENU2006", 1) == [39]
    assert db.summary()["named"] > 200
    # every typed reference points inside its section's slot range (sizes of the factory GEN_common)
    slots = {"BKGD2000": 38, "FXPI2000": 161, "FXTX2006": 728, "DTXT2000": 350, "ITXT2000": 180, "FBTN2006": 224, "CBTN2006": 157,
             "MENU2006": 63, "VRPI2000": 202, "FBAR2000": 34, "DBTN2001": 56, "DCBT2000": 17, "IMEN2000": 45, "LIST2001": 52,
             "MCTX2006": 33, "CRZO2002": 2, "SEBR2000": 4, "VWTX2000": 23, "EDTX2000": 49, "EDTB2006": 18, "ICBT2000": 49}
    for sc in db.screens.values():
        for o in sc.objects:
            if o.section in slots:
                assert o.index < slots[o.section], (sc.id, o)
