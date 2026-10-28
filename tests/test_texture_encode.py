"""Texture encoding: palettes, S3TC blocks, round trips through the decoder, image replacement."""

import math
import os
import struct

import pytest
from PIL import Image, ImageDraw

from gph import GphFile, decode_item, encode_item
from gph.container import Section, iter_gph_files
from gph.edit import get_fields, set_image, set_texture, texture_users
from gph.s3tc import encode_block, fit_block_colors
from gph.textures import (decode_texture, encode_palette, encode_texture, expected_payload_size, padded_size,
                          parse_format, texture_image)

HERE = os.path.dirname(__file__)
SAMPLES = os.environ.get("GPH_SAMPLES", os.path.join(HERE, "..", "samples"))
FILES = sorted(iter_gph_files([SAMPLES])) if os.path.isdir(SAMPLES) else []


def _psnr(a: Image.Image, b: Image.Image, opaque_only=False) -> float:
    pa, pb = a.convert("RGBA").tobytes(), b.convert("RGBA").tobytes()
    se = n = 0
    for i in range(0, len(pa), 4):
        if opaque_only and pa[i + 3] < 128:
            continue
        se += sum((pa[i + k] - pb[i + k]) ** 2 for k in range(3))
        n += 3
    return 99.0 if se == 0 else 10 * math.log10(255 ** 2 / (se / n))


def _test_image(w=64, h=48, alpha=True) -> Image.Image:
    img = Image.new("RGBA", (w, h))
    px = img.load()
    for y in range(h):
        for x in range(w):
            px[x, y] = (255 * x // w, 255 * y // h, 128 + int(60 * math.sin(x / 5)),
                        255 if (not alpha or x < w // 2) else 255 * y // h)
    ImageDraw.Draw(img).ellipse((10, 10, 40, 40), fill=(255, 0, 0, 255))
    return img


def test_parse_format_and_padding():
    assert parse_format("dxt5") == 0x83F3 and parse_format("BMP8.4") == 0x8B96 and parse_format(0x8B95) == 0x8B95
    assert parse_format("0x83f0") == 0x83F0
    with pytest.raises(ValueError):
        parse_format("JPEG")
    assert padded_size(0x83F1, 105, 31) == (108, 32)
    assert padded_size(0x8B96, 105, 31) == (105, 31)


def test_palette_exact_when_few_colours():
    img = Image.new("RGBA", (10, 6), (10, 20, 30, 255))
    ImageDraw.Draw(img).rectangle((0, 0, 4, 2), fill=(200, 100, 0, 128))
    for with_alpha, fmt in ((True, 0x8B96), (False, 0x8B95)):
        data = encode_palette(img, with_alpha)
        assert len(data) == expected_payload_size(fmt, 10, 6)
        back = decode_texture(fmt, 10, 6, data)
        want = img if with_alpha else img.convert("RGB").convert("RGBA")
        assert back.tobytes() == want.tobytes()


def test_palette_quantised_when_many_colours():
    img = _test_image(alpha=False)
    fl = encode_texture(img, "BMP8.3")
    assert fl["size"] == expected_payload_size(0x8B95, 64, 48) and fl["format"] == 0x1907
    assert _psnr(img, texture_image(fl)) > 28
    img = _test_image(alpha=True)
    fl = encode_texture(img, "BMP8.4")
    assert _psnr(img, texture_image(fl), opaque_only=True) > 25


def test_flat_block_is_exact_or_close():
    for colour in ((255, 255, 255), (0, 0, 0), (123, 45, 67), (4, 250, 129)):
        px = [colour + (255,)] * 16
        blk = encode_block(px, 0x83F0)
        back = decode_texture(0x83F0, 4, 4, blk)
        got = back.getpixel((0, 0))[:3]
        assert max(abs(a - b) for a, b in zip(got, colour)) <= 2, (colour, got)


def test_fit_modes():
    c0, c1, idx = fit_block_colors([(255, 0, 0), (0, 0, 255)] * 8, four=True)
    assert c0 > c1 and set(idx) <= {0, 1}
    c0, c1, idx = fit_block_colors([(255, 0, 0), (0, 0, 255)] * 4, four=False)
    assert c0 <= c1 and set(idx) <= {0, 1, 2}


def test_dxt1a_transparency_exact():
    img = _test_image()
    fl = encode_texture(img, "DXT1A")
    back = texture_image(fl)
    a, b = img.tobytes(), back.tobytes()
    assert all((a[i + 3] >= 128) == (b[i + 3] == 255) for i in range(0, len(a), 4))
    assert _psnr(img, back, opaque_only=True) > 30


@pytest.mark.parametrize("fmt", ["DXT1", "DXT1A", "DXT3", "DXT5"])
def test_s3tc_roundtrip_quality(fmt):
    img = _test_image(alpha=fmt != "DXT1")
    fl = encode_texture(img, fmt)
    assert fl["width"] == 64 and fl["height"] == 48 and fl["format"] == 0x80E1
    assert fl["size"] == expected_payload_size(fl["internal_format"], 64, 48)
    back = texture_image(fl)
    assert _psnr(img, back, opaque_only=True) > 30
    if fmt == "DXT5":
        assert max(abs(x - y) for x, y in zip(img.tobytes()[3::4], back.tobytes()[3::4])) <= 2
    if fmt == "DXT3":
        assert max(abs(x - y) for x, y in zip(img.tobytes()[3::4], back.tobytes()[3::4])) <= 8
    # the item encodes and decodes
    item = encode_item("TXUR2008", dict(fl))
    assert decode_item("TXUR2008", 0, item).fields["data"] == fl["data"]


def test_s3tc_padding():
    img = _test_image(w=61, h=45)
    fl = encode_texture(img, "DXT5")
    assert (fl["width"], fl["height"]) == (64, 48)
    back = texture_image(fl).crop((0, 0, 61, 45))
    assert _psnr(img, back, opaque_only=True) > 30  # same quality as the unpadded 64x48 image


def test_dxt5_alpha_block_modes():
    # pure 0/255 alpha with a few mid values: 6 level mode or 8 level mode must both decode within tolerance
    alphas = [0, 255, 0, 255, 100, 150, 0, 255, 255, 0, 120, 130, 0, 0, 255, 255]
    px = [(0, 0, 0, a) for a in alphas]
    blk = encode_block(px, 0x83F3)
    back = decode_texture(0x83F3, 4, 4, blk).tobytes()[3::4]
    assert max(abs(a - b) for a, b in zip(alphas, back)) <= 6


def test_set_texture_and_set_image_in_memory():
    f = GphFile(name="GEN HA00")
    f.sections += [Section("TXUR2008"), Section("IMAG2000")]
    img = _test_image(w=20, h=12)
    set_texture(f, 0, img, "BMP8.4")
    assert get_fields(f, "TXUR2008", 0)["width"] == 20
    info = set_image(f, 3, [img, img.rotate(180)], fmt="DXT5")
    assert info["slots"] == [1, 2] and info["new_slots"] == [1, 2]  # slot 0 was not used by image 3
    im = get_fields(f, "IMAG2000", 3)
    assert im["image_count"] == 2 and (im["width"], im["height"]) == (20, 12)
    assert im["pictures"][1] == {"width_minus_1": 19, "height_minus_1": 11, "texture": 2}
    assert get_fields(f, "TXUR2008", 2)["width"] == 20  # padded texture
    # replace again: same slots are reused (not shared), display size follows
    small = _test_image(w=8, h=8)
    info = set_image(f, 3, [small])
    assert info["slots"] == [1] and info["new_slots"] == []
    im = get_fields(f, "IMAG2000", 3)
    assert im["image_count"] == 1 and (im["width"], im["height"]) == (8, 8)
    assert get_fields(f, "TXUR2008", 1)["internal_format"] == 0x83F3  # format kept
    # a shared texture gets a new slot
    set_image(f, 4, [small])
    set_image(f, 5, [small])
    f.section("IMAG2000").items[5] = encode_item("IMAG2000", {**get_fields(f, "IMAG2000", 5), "pictures": [{"width_minus_1": 7, "height_minus_1": 7, "texture": 1}]})
    assert texture_users(f)[1] == [3, 5]
    info = set_image(f, 5, [img])
    assert info["new_slots"] and info["slots"][0] != 1
    # set_texture fixes picture sizes of its users
    set_texture(f, 1, _test_image(w=5, h=6))
    assert get_fields(f, "IMAG2000", 3)["pictures"][0]["width_minus_1"] == 4
    # the whole file still serialises
    GphFile.from_bytes(f.to_bytes())


@pytest.mark.skipif(not FILES, reason="no sample GPH files")
def test_reencode_factory_textures():
    path = next(p for p in FILES if "GEN_HA00" in p)
    f = GphFile.load(path)
    sec = f.section("TXUR2008")
    seen = {}
    for idx, raw in sec.used():
        fl = decode_item(sec.name, idx, raw).fields
        fmt = fl["internal_format"]
        if seen.get(fmt, 0) >= 5:
            continue
        seen[fmt] = seen.get(fmt, 0) + 1
        img = texture_image(fl)
        nf = encode_texture(img, fmt, fl["flags"])
        assert (nf["width"], nf["height"], nf["size"], nf["format"]) == (fl["width"], fl["height"], fl["size"], fl["format"])
        assert _psnr(img, texture_image(nf), opaque_only=True) > (99 - 1 if fmt in (0x8B95, 0x8B96) else 38)
