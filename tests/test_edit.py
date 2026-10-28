"""Editing helpers and package output (``.gph.bz2`` + ``.inf``)."""

import bz2
import os

import pytest

from gph import GphFile
from gph.container import Section, iter_gph_files
from gph.decode import Link
from gph.edit import (default_filename, escape_text, export_texts, get_color, get_fields, get_text, import_dir,
                      import_json, import_texts, parse_value, set_color, set_text, unescape_text, update_item,
                      write_package)
from gph.inf import InfFile

HERE = os.path.dirname(__file__)
SAMPLES = os.environ.get("GPH_SAMPLES", os.path.join(HERE, "..", "samples"))
FILES = sorted(iter_gph_files([SAMPLES])) if os.path.isdir(SAMPLES) else []


def _lang_file() -> GphFile:
    f = GphFile(name="GEN DU  ")
    f.sections.append(Section("TEXT2006"))
    set_text(f, 0, "De CD is\ronleesbaar.")
    set_text(f, 1, "Video")
    set_text(f, 3, "A|B\\C")
    return f


def test_default_filename():
    assert default_filename(GphFile(name="GEN COMM")) == "GEN_common.gph.bz2"
    assert default_filename(GphFile(name="GEN DU  "), compressed=False) == "GEN_DU.gph"
    assert default_filename(GphFile(name="LRG HA01")) == "LRG_HA01.gph.bz2"
    assert default_filename(GphFile(name="$$$$HA00")) == "$$$$_HA00.gph.bz2"


def test_set_get_text_grows_section():
    f = _lang_file()
    sec = f.section("TEXT2006")
    assert len(sec.items) == 4 and sec.items[2] is None
    assert get_text(f, 1) == "Video" and get_text(f, 2) is None
    set_text(f, 1, "Film\nTwee")  # LF is normalised to the unit's CR
    assert get_text(f, 1) == "Film\rTwee"
    # the container still serialises and re-parses
    g = GphFile.from_bytes(f.to_bytes())
    assert get_text(g, 0) == "De CD is\ronleesbaar."


def test_text_escape_roundtrip():
    for t in ("a\rb", "back\\slash", "pipe|x", "", "\\n literal", "l\u00e4uft \x85", "tab\there"):
        assert unescape_text(escape_text(t)) == t
    assert escape_text("x\x85") == "x\\u0085"
    assert unescape_text("a\\rb") == "a\rb"  # \r accepted as well


def test_export_import_texts(tmp_path):
    f = _lang_file()
    txt = export_texts(f)
    assert txt.splitlines()[0] == "0000\tDe CD is\\nonleesbaar."
    g = GphFile(name="GEN DU  ")
    n = import_texts(g, txt.split("\n"))
    assert n == 3
    assert get_text(g, 0) == "De CD is\ronleesbaar." and get_text(g, 3) == "A|B\\C"
    p = tmp_path / "TEXT2006.txt"
    p.write_text("# comment\n0001\tHallo\n\n", encoding="utf-8")
    assert import_texts(g, str(p)) == 1 and get_text(g, 1) == "Hallo"


def test_set_color():
    f = GphFile(name="GEN HA00")
    set_color(f, 1, "#ffcc00")
    set_color(f, 3, (255, 0, 0, 128))
    assert get_color(f, 1)["hex"] == "ffcc00ff"
    assert get_color(f, 3)["hex"] == "ff000080"


def test_update_item_paths_and_counts():
    f = GphFile(name="GEN HA00")
    update_item(f, "IMAG2000", 4, {"width": 32, "height": 32,
                                  "pictures": [{"width_minus_1": 31, "height_minus_1": 31, "texture": 7}]})
    update_item(f, "IMAG2000", 4, {"pictures[0].texture": 9, "pictures[1]": {"width_minus_1": 1, "height_minus_1": 1, "texture": 2}})
    fl = get_fields(f, "IMAG2000", 4)
    assert fl["image_count"] == 2
    assert [p["texture"] for p in fl["pictures"]] == [9, 2]


def test_import_json_keeps_unexportable_blobs():
    f = GphFile(name="GEN HA00")
    payload = bytes(range(256)) * 5  # > 64 bytes: the JSON export only records its length
    update_item(f, "TXUR2008", 0, {"internal_format": 0x8B96, "format": 0x1908, "type": 0x1401,
                                  "width": 16, "height": 16, "flags": 0x01030000, "data": payload})
    import_json(f, "TXUR2008", [{"index": 0, "width": 8, "data": {"bytes": len(payload)}}])
    fl = get_fields(f, "TXUR2008", 0)
    assert fl["width"] == 8 and fl["data"] == payload


def test_parse_value():
    assert parse_value("12") == 12 and parse_value("0x1f") == 31
    assert parse_value("GEN:5") == Link("GEN", 5)
    assert parse_value("#ff8800") == b"\xff\x88\x00\xff"
    assert parse_value('[1, 2]') == [1, 2]
    assert parse_value("none") is None
    assert parse_value("Hallo wereld") == "Hallo wereld"


def test_write_package(tmp_path):
    f = _lang_file()
    out = tmp_path / "DU" / "GEN_DU.gph.bz2"
    inf = write_package(f, out, version="26.10.28.00.00")
    assert inf == str(out) + ".inf"
    assert InfFile.verify_files(out) == (True, True)
    assert InfFile.load(inf).fields["VER"] == "26.10.28.00.00"
    assert InfFile.load(inf).size == out.stat().st_size
    g = GphFile.load(out)
    assert g.compressed and get_text(g, 1) == "Video"
    # uncompressed output, no side-car by default
    raw = tmp_path / "GEN_DU.gph"
    assert write_package(f, raw) is None
    assert bz2.decompress(out.read_bytes()) == raw.read_bytes()


@pytest.mark.skipif(not FILES, reason="no sample GPH files")
def test_edit_factory_file_changes_only_that_item(tmp_path):
    src = next(p for p in FILES if p.endswith("GEN_DE.gph.bz2") or p.endswith("GEN_DE_orig.gph"))
    f = GphFile.load(src)
    before = {i: b for i, b in f.section("TEXT2006").used()}
    set_text(f, 1, "Film")
    after = {i: b for i, b in f.section("TEXT2006").used()}
    changed = [i for i in before if before[i] != after[i]]
    assert changed == [1]
    out = tmp_path / "GEN_DE.gph.bz2"
    write_package(f, out)
    assert InfFile.verify_files(out) == (True, True)
    if os.path.exists(src + ".inf"):  # fields other than SIZE/checksum are copied from the source side-car
        assert InfFile.load(str(out) + ".inf").fields["VER"] == InfFile.load(src + ".inf").fields["VER"]
    g = GphFile.load(out)
    assert get_text(g, 1) == "Film"
    # import the whole export of the edited file into a fresh copy: same bytes
    d = tmp_path / "exp"
    d.mkdir()
    (d / "TEXT2006.txt").write_text(export_texts(g), encoding="utf-8")
    h = GphFile.load(src)
    import_dir(h, str(d))
    assert h.to_bytes() == g.to_bytes()
