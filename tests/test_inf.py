import os

import pytest

from gph.inf import CRC16_TABLE, InfFile, crc16, write_inf_for

HERE = os.path.dirname(__file__)
SAMPLES = os.environ.get("GPH_SAMPLES", os.path.join(HERE, "..", "samples"))


def _pairs():
    out = []
    for root, _d, files in os.walk(SAMPLES) if os.path.isdir(SAMPLES) else []:
        for fn in files:
            if fn.endswith(".inf") and not fn.startswith("._") and os.path.exists(os.path.join(root, fn[:-4])):
                out.append(os.path.join(root, fn[:-4]))
    return sorted(out)


PAIRS = _pairs()


def test_table_matches_values_from_gph_editor():
    # spot checks of entries read from GPH_EDITOR.exe (DAT_00634ab8)
    assert CRC16_TABLE[0] == 0x0000
    assert CRC16_TABLE[1] == 0xDF2B
    assert CRC16_TABLE[2] == 0xBE57
    assert CRC16_TABLE[7] == 0x1DD3
    assert CRC16_TABLE[128] == 0x15D4
    assert CRC16_TABLE[255] == 0x5381
    # linear table
    assert all(CRC16_TABLE[a ^ b] == CRC16_TABLE[a] ^ CRC16_TABLE[b] for a in (1, 3, 77) for b in (2, 200, 255))


def test_crc16_basic():
    assert crc16(b"") == 0
    assert crc16(b"\x00") == 0
    assert crc16(b"\x01") == 0xDF2B


@pytest.mark.parametrize("path", PAIRS, ids=[os.path.relpath(p, SAMPLES) for p in PAIRS])
def test_factory_inf_checksums(path):
    assert InfFile.verify_files(path) == (True, True)


@pytest.mark.parametrize("path", PAIRS[:3], ids=[os.path.relpath(p, SAMPLES) for p in PAIRS[:3]])
def test_regenerated_inf_is_identical(path, tmp_path):
    """Re-computing the checksum over the original text must give the original first line."""
    orig = open(path + ".inf", "rb").read()
    inf = InfFile.parse(orig)
    data = open(path, "rb").read()
    inf.update(data)
    assert inf.to_bytes() == orig


def test_write_inf_for_new_file(tmp_path):
    p = tmp_path / "X.gph.bz2"
    p.write_bytes(b"hello world" * 100)
    inf = write_inf_for(p, version="26.10.27.23.59")
    assert inf.size == 1100
    assert InfFile.verify_files(p) == (True, True)
    assert inf.fields["VER"] == "26.10.27.23.59"
