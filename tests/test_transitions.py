"""gph.transitions: where a press leads, read from the code of the MMI modules."""

import json
import os
import struct

import pytest

from gph import transitions
from gph.screens import Elf, ScreenDatabase, find_mmi_folder
from test_screens import packed, zones_elf

HERE = os.path.dirname(__file__)
#: a software update of the unit (the folder with Application/ and Data_Base/), for the test on real code
FIRMWARE = os.environ.get("GPH_FIRMWARE", os.path.join(HERE, "..", "..", "computer", "RT6-Soft"))
CLS = "20C_Menu_RADIO_C1_Z4_1"
CHANGE, BACK = "ChangeState__12C_MENU_STATEUlbT2", "ChangeState__12C_MENU_STATEsbT2"


class Asm:
    """A few PowerPC instructions, enough for a screen class the way the firmware's compiler writes one."""

    def __init__(self):
        self.words: list[int] = []
        self.funcs: list[tuple[str, int, int]] = []   # name, start, size
        self.calls: list[tuple[int, str]] = []         # pc, symbol
        self.labels: dict[str, int] = {}
        self.jumps: list[tuple[int, str]] = []

    def func(self, name):
        if self.funcs and self.funcs[-1][2] == 0:
            n, start, _ = self.funcs[-1]
            self.funcs[-1] = (n, start, len(self.words) * 4 - start)
        if name:
            self.funcs.append((name, len(self.words) * 4, 0))

    def emit(self, w):
        self.words.append(w & 0xFFFFFFFF)

    def li(self, rd, v): self.emit(14 << 26 | rd << 21 | (v & 0xFFFF))
    def lis(self, rd, v): self.emit(15 << 26 | rd << 21 | (v & 0xFFFF))
    def ori(self, ra, rs, v): self.emit(24 << 26 | rs << 21 | ra << 16 | v)
    def cmplwi(self, ra, v): self.emit(10 << 26 | ra << 16 | v)
    def cmpwi(self, ra, v): self.emit(11 << 26 | ra << 16 | (v & 0xFFFF))
    def cmplw(self, ra, rb): self.emit(31 << 26 | ra << 16 | rb << 11 | 32 << 1)
    def blr(self): self.emit(0x4E800020)
    def label(self, name): self.labels[name] = len(self.words) * 4

    def bne(self, label):
        self.jumps.append((len(self.words), label))
        self.emit(16 << 26 | 4 << 21 | 2 << 16)

    def bl(self, symbol):
        self.calls.append((len(self.words) * 4, symbol))
        self.emit(18 << 26 | 1)

    def elf(self) -> bytes:
        self.func(None)
        for n, label in self.jumps:
            self.words[n] |= (self.labels[label] - n * 4) & 0xFFFC
        text = b"".join(struct.pack(">I", w) for w in self.words)
        strtab, symtab, index = bytearray(b"\0"), bytearray(bytes(16)), {}
        def symbol(name, value, size, info, shndx):
            index[name] = len(symtab) // 16
            symtab.extend(struct.pack(">IIIBBH", len(strtab), value, size, info, 0, shndx))
            strtab.extend(name.encode() + b"\0")
        for name, start, size in self.funcs:
            symbol(name, start, size, 0x12, 1)          # global function in .text
        for _pc, name in self.calls:
            if name not in index:
                symbol(name, 0, 0, 0x10, 0)             # undefined: lives in another module
        rela = b"".join(struct.pack(">IIi", pc, index[name] << 8 | 10, 0) for pc, name in self.calls)   # R_PPC_REL24
        shstr, names = bytearray(b"\0"), {}
        for n in (".text", ".rela.text", ".symtab", ".strtab", ".shstrtab"):
            names[n] = len(shstr)
            shstr += n.encode() + b"\0"
        blobs = [text, rela, bytes(symtab), bytes(strtab), bytes(shstr)]
        body, offsets = bytearray(), []
        for b in blobs:
            offsets.append(52 + len(body))
            body += b + bytes(-len(b) % 4)
        sh = lambda name, typ, off, size, link=0, info=0: struct.pack(">IIIIIIIIII", name, typ, 0, 0, off, size, link, info, 4, 0)
        shdrs = (sh(0, 0, 0, 0) + sh(names[".text"], 1, offsets[0], len(text)) + sh(names[".rela.text"], 4, offsets[1], len(rela), 3, 1)
                 + sh(names[".symtab"], 2, offsets[2], len(symtab), 4, 1) + sh(names[".strtab"], 3, offsets[3], len(strtab))
                 + sh(names[".shstrtab"], 3, offsets[4], len(shstr)))
        header = b"\x7fELF" + bytes([1, 2, 1, 0]) + bytes(8) + struct.pack(">HHIIIIIHHHHHH", 1, 20, 1, 0, 0, 52 + len(body), 0, 52, 0, 0, 40, 6, 5)
        return header + bytes(body) + shdrs


def menu_module() -> bytes:
    """The class of screen 20 of ``zones_elf`` (a contextual menu with two lines and a picture):
    line 1 opens screen 10, line 2 opens screen 11 or goes back, the picture does something on the spot."""
    a = Asm()
    a.func("__" + CLS)                                   # constructor: C_MENU_STATE(zone, kbd, int, 20)
    a.li(7, 20); a.bl("__12C_MENU_STATE14MGR_DIS_Zone_t19MGR_DIS_KbdFormat_tiUl"); a.blr()
    a.func(f"Verify__{CLS}Ul17TYPE_CUM_SCRN_EVT")        # (this, object id in r4, event in r5) -> action number
    a.cmplwi(5, 337); a.bne("none")
    for number, obj in ((1, 240001), (2, 240002), (3, 20003)):
        a.lis(0, obj >> 16); a.ori(0, 0, obj & 0xFFFF); a.cmplw(4, 0); a.bne(f"not{number}")
        a.li(3, number); a.blr()
        a.label(f"not{number}")
    a.label("none"); a.li(3, 0); a.blr()
    a.func(f"InvokeAction__{CLS}Ui")                     # (this, action number in r4)
    for number, item in ((1, "Bands"), (2, "Stop_start"), (3, "Mute")):
        a.cmpwi(4, number); a.bne(f"skip{number}")
        a.bl(f"Action_EVT_MENU_ITEM_RADIO_C1_Z4_1_{item}_SI__{CLS}"); a.blr()
        a.label(f"skip{number}")
    a.blr()
    a.func(f"Action_EVT_MENU_ITEM_RADIO_C1_Z4_1_Bands_SI__{CLS}")
    a.li(4, 10); a.li(5, 0); a.li(6, 0); a.bl(CHANGE); a.blr()
    a.func(f"Action_EVT_MENU_ITEM_RADIO_C1_Z4_1_Stop_start_SI__{CLS}")
    a.bl("IsGuidanceActive__Fv"); a.cmpwi(3, 0); a.bne("active")
    a.li(4, 11); a.li(5, 0); a.li(6, 0); a.bl(CHANGE); a.blr()
    a.label("active"); a.li(4, -1); a.bl(BACK); a.blr()
    a.func(f"Action_EVT_MENU_ITEM_RADIO_C1_Z4_1_Mute_SI__{CLS}")
    a.bl("SetMute__Fb"); a.blr()
    return a.elf()


EXPECTED = {20: {240001: {"action": "Action_EVT_MENU_ITEM_RADIO_C1_Z4_1_Bands_SI", "to": [10], "back": False, "var": False},
                 240002: {"action": "Action_EVT_MENU_ITEM_RADIO_C1_Z4_1_Stop_start_SI", "to": [11], "back": True, "var": False},
                 20003: {"action": "Action_EVT_MENU_ITEM_RADIO_C1_Z4_1_Mute_SI", "to": [], "back": False, "var": False}}}


def firmware_tree(tmp_path) -> str:
    """A software update in miniature; returns the path of its GraphicBase."""
    gb = tmp_path / "Data_Base" / "Graphics"
    gb.mkdir(parents=True)
    (gb / "GraphicBase.RNEG.gz").write_bytes(packed(zones_elf()))
    mmi = tmp_path / "Application" / "MMI" / "tuner"
    mmi.mkdir(parents=True)
    (mmi / "mmi_tuner.gz").write_bytes(packed(menu_module()))
    return str(gb / "GraphicBase.RNEG.gz")


def test_interpreter_and_call_list():
    mod = transitions.Module(Elf(menu_module()), "mmi_tuner")
    cpu = transitions.Cpu(mod)
    verify = f"Verify__{CLS}Ul17TYPE_CUM_SCRN_EVT"
    run = lambda obj, event: cpu.run(verify, [transitions.THIS, obj, event], lambda _c, _r: 1)
    assert [run(240001, 337), run(240002, 337), run(20003, 337), run(180001, 337), run(240001, 178)] == [1, 2, 3, 0, 0]
    calls = list(mod.calls(f"Action_EVT_MENU_ITEM_RADIO_C1_Z4_1_Stop_start_SI__{CLS}"))
    assert [(c, reg.get(4)) for c, reg in calls] == [("IsGuidanceActive__Fv", None), (CHANGE, 11), (BACK, -1)]


def test_links_of_a_module(tmp_path):
    path = firmware_tree(tmp_path)
    db = ScreenDatabase.load(path)
    links = transitions.read_links(db, find_mmi_folder(path))
    assert links == EXPECTED
    assert transitions.links_from_json(json.loads(json.dumps(transitions.links_to_json(links)))) == links
    db.set_links(links)
    assert db.openers(10) == [(20, 240001)] and db.openers(11) == [(20, 240002)] and db.openers(20) == []
    assert db.summary()["links"] == 1


def test_server_and_side_car(tmp_path, capsys):
    from gph.cli import main
    from gph.server import Editor

    path = firmware_tree(tmp_path)
    ed = Editor()
    ed.open_screens(path)
    assert ed.screens.links == EXPECTED and ed.screens.screens[20].class_name == "C_Menu_RADIO_C1_Z4_1"
    det = ed.screen_detail(20)
    press = {o["obj"]: o["press"] for o in det["objects"] if "press" in o}
    assert press[240001] == {"action": "Action_EVT_MENU_ITEM_RADIO_C1_Z4_1_Bands_SI", "item": "Bands", "back": False, "var": False,
                             "to": [{"id": 10, "name": "RADIO_F1_Z0_3", "zone": 3, "zone_name": "Z0_3"}]}
    assert press[240002]["back"] and press[240002]["item"] == "Stop start" and press[20003]["to"] == []
    assert det["has_links"] and det["openers"] == []
    assert ed.screen_detail(10)["openers"] == [{"id": 20, "name": "RADIO_C1_Z4_1", "zone": 22, "zone_name": "Z4_1", "obj": 240001, "label": None}]
    rows = {r["id"]: (r["presses"], r["openers"]) for r in ed.screen_list()}
    assert rows[20] == (3, 0) and rows[10] == (0, 1) and rows[12] == (0, 0)
    # `gph screens-names` saves names and links; a host without the modules reads them back
    with pytest.raises(SystemExit) as done:
        main(["screens-names", path])
    assert done.value.code == 0 and "3 presses on 1 screen" in capsys.readouterr().out
    lone = tmp_path / "lone"
    lone.mkdir()
    for ext in ("", ".names.json", ".links.json"):
        (lone / ("GraphicBase.RNEG.gz" + ext)).write_bytes(open(path + ext, "rb").read())
    ed2 = Editor()
    ed2.open_screens(str(lone / "GraphicBase.RNEG.gz"))
    assert ed2.screens.links == EXPECTED and ed2.screens.screens[20].name == "RADIO_C1_Z4_1"


@pytest.mark.skipif(not os.path.isdir(os.path.join(FIRMWARE, "Application", "MMI")), reason="no firmware (set GPH_FIRMWARE to a software update folder)")
def test_real_firmware():
    path = os.path.join(FIRMWARE, "Data_Base", "Graphics", "GraphicBase.RNEG.gz")
    db = ScreenDatabase.load(path)
    links = transitions.read_links(db, find_mmi_folder(path))
    assert len(links) > 200 and sum(len(v) for v in links.values()) > 600
    nav = links[35]   # the navigation's main menu: jump table in InvokeAction, targets in the actions
    assert nav[240835] == {"action": "Action_EVT_MENU_ITEM_NAV_M1_Z1_0_Map_Management_SI", "to": [36], "back": False, "var": False}
    assert nav[240528]["to"] == [392, 1634] and nav[240255]["back"]
    assert links[1473][240991]["to"] == [2123]   # radio: reached through a virtual call in Verify
