"""Tests that run against the (non-committed) sample files in ``samples/``.

Set ``GPH_SAMPLES`` to point elsewhere.  Tests are skipped when no samples are present.
"""

import bz2
import os

import pytest

from gph import GphFile, decode_item
from gph.container import iter_gph_files
from gph.decode import decode_utf16, encode_utf16
from gph import textures

HERE = os.path.dirname(__file__)
SAMPLES = os.environ.get("GPH_SAMPLES", os.path.join(HERE, "..", "samples"))
FILES = sorted(iter_gph_files([SAMPLES])) if os.path.isdir(SAMPLES) else []

pytestmark = pytest.mark.skipif(not FILES, reason="no sample GPH files")


@pytest.mark.parametrize("path", FILES, ids=[os.path.relpath(p, SAMPLES) for p in FILES])
def test_roundtrip_byte_exact(path):
    raw = open(path, "rb").read()
    if raw[:3] == b"BZh":
        raw = bz2.decompress(raw)
    f = GphFile.from_bytes(raw, path)
    assert f.to_bytes() == raw


@pytest.mark.parametrize("path", FILES, ids=[os.path.relpath(p, SAMPLES) for p in FILES])
def test_schema_matches_relocation_header(path):
    f = GphFile.load(path)
    problems = []
    for sec in f.sections:
        for idx, raw in sec.used():
            it = decode_item(sec.name, idx, raw)
            problems += it.problems + it.check_relocation()
            if sec.name == "TXUR2008" and not it.problems:
                exp = textures.expected_payload_size(it.fields["internal_format"], it.fields["width"], it.fields["height"])
                assert exp == it.fields["size"], f"TXUR2008[{idx}] size"
                assert it.fields["_header_end"] == 40
    assert not problems, "\n".join(problems[:20])


@pytest.mark.parametrize("path", [p for p in FILES if "HA0" in p and "GEN" in p][:1])
def test_textures_decode(path):
    f = GphFile.load(path)
    sec = f.section("TXUR2008")
    seen = set()
    for idx, raw in sec.used():
        it = decode_item(sec.name, idx, raw)
        fmt = it.fields["internal_format"]
        if fmt in seen:
            continue
        seen.add(fmt)
        img = textures.texture_image(it.fields)
        assert img.size == (it.fields["width"], it.fields["height"])
        assert img.mode == "RGBA"


def test_utf16_roundtrip():
    for t in ["", "Video", "Informatie over \rhandmatige activering", "ÄÖÜ €"]:
        enc = encode_utf16(t)
        assert len(enc) % 4 == 0
        assert decode_utf16(enc) == t
