"""Screenshots from spy 21403 dumps (gph.spy)."""

import gzip
import struct

from PIL import Image

from gph.cli import main
from gph.spy import convert, screenshots, spy_files


def tga(w, h, pixel):
    """An uncompressed 32-bit TGA as the unit's graphics driver writes it; ``pixel(x, y)`` gives (r, g, b)."""
    head = b"\0\0\2" + b"\0" * 9 + struct.pack("<HHBB", w, h, 32, 0x2F)
    body = bytearray()
    for y in range(h):
        for x in range(w):
            r, g, b = pixel(x, y)
            body += bytes((b, g, r, 255))
    return head + bytes(body)


def shade(x, y):
    return (x * 3 % 256, y * 5 % 256, (x + y) % 256)


def test_screenshot_from_gzip():
    data = gzip.compress(tga(80, 48, shade), 1)
    (img,) = screenshots(data)
    assert img.size == (80, 48) and img.mode == "RGB"
    assert img.getpixel((0, 0)) == shade(0, 0)          # first line is the top line
    assert img.getpixel((79, 47)) == shade(79, 47)
    assert img.getpixel((10, 3)) == shade(10, 3)


def test_plain_cut_off_and_noise():
    raw = tga(40, 20, shade)
    assert screenshots(raw)[0].size == (40, 20)                       # not gzipped
    assert screenshots(b"junk" * 7 + raw)[0].getpixel((5, 5)) == shade(5, 5)   # something in front
    cut = screenshots(raw[:18 + 40 * 4 * 7 + 13])                     # stops in the middle of line 8
    assert cut[0].size == (40, 7)
    assert screenshots(gzip.compress(raw)[:-40])                      # cut-off gzip stream still gives a picture
    assert screenshots(b"\0" * 64) == [] and screenshots(b"") == []
    two = screenshots(raw + tga(8, 4, lambda x, y: (1, 2, 3)))
    assert [i.size for i in two] == [(40, 20), (8, 4)]


def test_convert_folder(tmp_path, capsys):
    d = tmp_path / "SPY" / "21403"
    d.mkdir(parents=True)
    (d / "20261119_1200.bin.gz").write_bytes(gzip.compress(tga(16, 8, shade)))
    (d / "._20261119_1200.bin.gz").write_bytes(b"resource fork")
    other = tmp_path / "SPY" / "20900"
    other.mkdir()
    (other / "x.bin.gz").write_bytes(gzip.compress(b"not a picture"))
    assert [p.rsplit("/", 1)[-1] for p in spy_files(str(tmp_path))] == ["20261119_1200.bin.gz"]
    out = tmp_path / "png"
    written = convert(str(tmp_path), str(out))
    assert [p.rsplit("/", 1)[-1] for p in written] == ["20261119_1200.png"]
    assert Image.open(written[0]).size == (16, 8)
    # command line
    try:
        main(["spy-screenshot", str(d)])
    except SystemExit as e:
        assert e.code == 0
    assert (d / "20261119_1200.png").exists()
    assert "20261119_1200.png" in capsys.readouterr().out
    try:
        main(["spy-screenshot", str(other)])
    except SystemExit as e:
        assert e.code == 1
