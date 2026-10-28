"""Encoder: every decoded item must re-encode to the original bytes, plus synthetic cases."""

import json
import os

import pytest

from gph import GphFile, decode_item, encode_item
from gph.container import iter_gph_files
from gph.decode import Link
from gph.encode import EncodeError, to_color, to_link

HERE = os.path.dirname(__file__)
SAMPLES = os.environ.get("GPH_SAMPLES", os.path.join(HERE, "..", "samples"))
FILES = sorted(iter_gph_files([SAMPLES])) if os.path.isdir(SAMPLES) else []


@pytest.mark.skipif(not FILES, reason="no sample GPH files")
@pytest.mark.parametrize("path", FILES, ids=[os.path.relpath(p, SAMPLES) for p in FILES])
def test_reencode_every_item_byte_exact(path):
    f = GphFile.load(path)
    bad = []
    for sec in f.sections:
        for idx, raw in sec.used():
            it = decode_item(sec.name, idx, raw)
            out = encode_item(sec.name, it.fields)
            if out != raw:
                bad.append(f"{sec.name}[{idx}]")
    assert not bad, bad[:10]


@pytest.mark.skipif(not FILES, reason="no sample GPH files")
def test_reencode_from_json_form():
    """The JSON export form (links as dicts, colours with hex) must encode identically too,
    except for blobs longer than 64 bytes which the JSON only describes."""
    path = next(p for p in FILES if "GEN_common" in p)
    f = GphFile.load(path)
    for sec in f.sections:
        for idx, raw in sec.used()[:50]:
            it = decode_item(sec.name, idx, raw)
            js = json.loads(json.dumps(it.to_json()))
            if any(isinstance(v, dict) and "bytes" in v for v in js.values()):
                continue
            assert encode_item(sec.name, js) == raw, f"{sec.name}[{idx}]"


def test_text_item():
    assert encode_item("TEXT2006", {"text": "Film"}) == b"\x00\x00\x00\x00" + "Film".encode("utf-16-be") + b"\x00\x00\x00\x00"
    # NUL terminator + padding to 4
    assert encode_item("TEXT2006", {"text": "Ab"}) == b"\x00\x00\x00\x00\x00A\x00b\x00\x00\x00\x00"
    assert decode_item("TEXT2006", 0, encode_item("TEXT2006", {"text": "a\rb"})).fields["text"] == "a\rb"


def test_color_item_forms():
    for v in ("#ffcc00", "ffcc00ff", {"r": 255, "g": 204, "b": 0, "a": 255}, (255, 204, 0), {"hex": "ffcc00ff"}):
        assert encode_item("TXCO2000", {"color": v}) == b"\x00\x00\x00\x00\xff\xcc\x00\xff"
    assert to_color("#ff000080") == b"\xff\x00\x00\x80"


def test_link_forms():
    assert to_link("LRG:12") == Link("LRG", 12)
    assert to_link({"module": "GEN", "index": 5}) == Link("GEN", 5)
    assert to_link(None).is_null and to_link(None).module == "GEN "
    assert to_link(("FNT", 1)) == Link("FNT", 1)


def test_pointer_table_layout_and_counts():
    fields = {"level": 0, "font": "FNT:0", "text_index": "GEN:10", "x": 1, "y": 2, "text_dx": 3, "text_dy": 4,
              "style": 0x17, "alignment": 0,
              "images": ["LRG:1", None, "LRG:3"], "text_colors": ["GEN:1"]}
    raw = encode_item("FBTN2006", fields)
    assert fields["image_count"] == 3 and fields["text_color_count"] == 1
    it = decode_item("FBTN2006", 0, raw)
    assert not it.check_relocation()
    assert it.reloc == 36 and it.nreloc == 2 + 3 + 1
    assert it.fields["images"] == [Link("LRG ", 1), None, Link("LRG ", 3)]
    assert it.fields["text_colors"] == [Link("GEN ", 1)]
    assert len(raw) % 4 == 0


def test_empty_tables_are_null_pointers():
    raw = encode_item("LIST2001", {"column_position": [], "column_size": None, "column_alignment": [1, 2, 3]})
    it = decode_item("LIST2001", 0, raw)
    assert it.fields["column_count"] == 3  # the last list given wins the shared count field
    assert it.fields["column_alignment"] == [1, 2, 3]
    assert raw[it.reloc:it.reloc + 8] == b"\xff" * 8  # two null pointers


def test_scalar_tables_padded_to_four():
    raw = encode_item("LIST2001", {"column_alignment": [1]})
    assert len(raw) % 4 == 0


def test_blob_and_fixed_count_table():
    canvas = bytes(range(64)) * 2
    fields = {"canvas_height": 8, "canvas_width": 16, "char_height": 8, "unicode_high_byte": 0, "gl_type": 0x1401,
              "canvas": canvas, "chars": [None] * 65 + [{"baseline": 0, "width": 8, "x": 0, "y": 0}]}
    raw = encode_item("BFNT2000", fields)
    assert fields["length_bytes"] == 128
    it = decode_item("BFNT2000", 0, raw)
    assert not it.check_relocation()
    assert it.fields["canvas"] == canvas
    assert len(it.fields["chars"]) == 256 and it.fields["chars"][65] == {"baseline": 0, "width": 8, "x": 0, "y": 0}


def test_errors():
    with pytest.raises(EncodeError):
        encode_item("TXCO2000", {"color": "nonsense"})
    with pytest.raises(EncodeError):
        encode_item("TXUR2008", {"size": 3, "data": {"bytes": 3}})
    with pytest.raises(EncodeError):
        encode_item("FBTN2006", {"images": ["LRG:1"] * 300})  # u8 count overflow
