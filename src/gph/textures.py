"""Decode TXUR2008 texture payloads to Pillow images, and encode images to payloads.

The 40 byte TXUR2008 item header is an OpenGL ES texture descriptor::

    u16 reloc=36, u16 nreloc=1
    u32 0
    u16 0, u16 internal_format   GL_PALETTE8_RGBA8_OES (0x8B96), GL_PALETTE8_RGB8_OES (0x8B95),
                                 GL_COMPRESSED_*_S3TC_DXT1/3/5 (0x83F0..0x83F3)
    u32 format                   GL_RGBA (0x1908), GL_RGB (0x1907) or GL_BGRA (0x80E1) for S3TC
    u32 type                     GL_UNSIGNED_BYTE (0x1401)
    u32 0
    u32 size                     payload size in bytes
    u16 width, u16 height
    u32 flags                    0x01030000 in all factory files
    u32 ptr -> payload           always 40

Payloads:

* ``0x8B96`` 256 x RGBA palette (1024 bytes) followed by width*height 8 bit indices.
* ``0x8B95`` 256 x RGB palette (768 bytes) followed by width*height 8 bit indices.
* ``0x83F0``..``0x83F3`` standard S3TC blocks, 4x4 pixels, row-major; width and height
  are multiples of 4.

Encoding (``encode_texture``) produces the same layouts; palettised images with at most
256 colours are stored exactly, S3TC goes through :mod:`gph.s3tc`.
"""

from __future__ import annotations

import struct

from PIL import Image

from .schema import TEXTURE_TYPE_NAMES


def texture_type_name(internal_format: int) -> str:
    return TEXTURE_TYPE_NAMES.get(internal_format, f"0x{internal_format:04x}")


def expected_payload_size(internal_format: int, width: int, height: int) -> int | None:
    if internal_format == 0x8B96:
        return 1024 + width * height
    if internal_format == 0x8B95:
        return 768 + width * height
    if internal_format in (0x83F0, 0x83F1):
        return max(1, width // 4) * max(1, height // 4) * 8
    if internal_format in (0x83F2, 0x83F3):
        return max(1, width // 4) * max(1, height // 4) * 16
    return None


def decode_texture(internal_format: int, width: int, height: int, payload: bytes) -> Image.Image:
    """Return an RGBA Pillow image for the texture payload."""
    if internal_format == 0x8B96:
        pal = payload[:1024]
        idx = payload[1024:1024 + width * height]
        img = Image.frombytes("P", (width, height), idx)
        img.putpalette(pal, rawmode="RGBA")
        return img.convert("RGBA")
    if internal_format == 0x8B95:
        pal = payload[:768]
        idx = payload[768:768 + width * height]
        img = Image.frombytes("P", (width, height), idx)
        img.putpalette(pal, rawmode="RGB")
        return img.convert("RGBA")
    if internal_format in (0x83F0, 0x83F1, 0x83F2, 0x83F3):
        return decode_s3tc(internal_format, width, height, payload)
    raise ValueError(f"unsupported texture format 0x{internal_format:04x}")


# ---------------------------------------------------------------------------
# S3TC / DXT decoding (pure Python, small images so speed is fine)

def _rgb565(v: int) -> tuple[int, int, int]:
    r = (v >> 11) & 0x1F
    g = (v >> 5) & 0x3F
    b = v & 0x1F
    return ((r << 3) | (r >> 2), (g << 2) | (g >> 4), (b << 3) | (b >> 2))


def _dxt1_block(block: bytes, one_bit_alpha: bool):
    c0, c1, bits = struct.unpack("<HHI", block)
    p0 = _rgb565(c0)
    p1 = _rgb565(c1)
    if c0 > c1 or not one_bit_alpha:
        p2 = tuple((2 * a + b) // 3 for a, b in zip(p0, p1))
        p3 = tuple((a + 2 * b) // 3 for a, b in zip(p0, p1))
        pal = [p0 + (255,), p1 + (255,), p2 + (255,), p3 + (255,)]
    else:
        p2 = tuple((a + b) // 2 for a, b in zip(p0, p1))
        pal = [p0 + (255,), p1 + (255,), p2 + (255,), (0, 0, 0, 0)]
    return [pal[(bits >> (2 * i)) & 3] for i in range(16)]


def _dxt3_alpha(block: bytes) -> list[int]:
    (bits,) = struct.unpack("<Q", block)
    return [((bits >> (4 * i)) & 0xF) * 17 for i in range(16)]


def _dxt5_alpha(block: bytes) -> list[int]:
    a0, a1 = block[0], block[1]
    bits = int.from_bytes(block[2:8], "little")
    if a0 > a1:
        pal = [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
    else:
        pal = [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]
    return [pal[(bits >> (3 * i)) & 7] for i in range(16)]


def decode_s3tc(internal_format: int, width: int, height: int, payload: bytes) -> Image.Image:
    bw = max(1, (width + 3) // 4)
    bh = max(1, (height + 3) // 4)
    block_size = 8 if internal_format in (0x83F0, 0x83F1) else 16
    buf = bytearray(width * height * 4)
    pos = 0
    for by in range(bh):
        for bx in range(bw):
            block = payload[pos:pos + block_size]
            pos += block_size
            if len(block) < block_size:
                break
            if block_size == 8:
                px = _dxt1_block(block, internal_format == 0x83F1)
            else:
                px = _dxt1_block(block[8:], False)
                alpha = _dxt3_alpha(block[:8]) if internal_format == 0x83F2 else _dxt5_alpha(block[:8])
                px = [(p[0], p[1], p[2], a) for p, a in zip(px, alpha)]
            for i, (r, g, b, a) in enumerate(px):
                x = bx * 4 + (i & 3)
                y = by * 4 + (i >> 2)
                if x < width and y < height:
                    o = (y * width + x) * 4
                    buf[o:o + 4] = bytes((r, g, b, a))
    return Image.frombytes("RGBA", (width, height), bytes(buf))


def texture_image(fields: dict) -> Image.Image:
    """Convenience: decode from a decoded TXUR2008 item's fields."""
    return decode_texture(fields["internal_format"], fields["width"], fields["height"], fields["data"])


# ---------------------------------------------------------------------------
# encoding

FORMAT_BY_NAME = {v: k for k, v in TEXTURE_TYPE_NAMES.items()}
FORMAT_BY_NAME.update({"DXT2": 0x83F2, "BMP8": 0x8B96, "PAL": 0x8B96, "PALETTE": 0x8B96})

#: ``format`` header word per internal format
GL_FORMAT_FOR = {0x8B96: 0x1908, 0x8B95: 0x1907, 0x83F0: 0x80E1, 0x83F1: 0x80E1, 0x83F2: 0x80E1, 0x83F3: 0x80E1}
DEFAULT_FLAGS = 0x01030000


def parse_format(name: str | int) -> int:
    """``"DXT5"`` / ``"BMP8.4"`` / ``0x83f3`` -> internal_format."""
    if isinstance(name, int):
        return name
    key = name.strip().upper()
    if key in FORMAT_BY_NAME:
        return FORMAT_BY_NAME[key]
    try:
        return int(key, 0)
    except ValueError:
        raise ValueError(f"unknown texture format {name!r}; use one of {', '.join(TEXTURE_TYPE_NAMES.values())}") from None


def padded_size(internal_format: int, width: int, height: int) -> tuple[int, int]:
    """Texture size for an image of ``width`` x ``height``: S3TC rounds up to multiples of 4."""
    if internal_format in (0x83F0, 0x83F1, 0x83F2, 0x83F3):
        return (width + 3) // 4 * 4, (height + 3) // 4 * 4
    return width, height


def _pad_image(img: Image.Image, size: tuple[int, int]) -> Image.Image:
    """Grow ``img`` to ``size`` by repeating its last row/column (keeps block fits clean)."""
    if img.size == size:
        return img
    out = Image.new("RGBA", size)
    out.paste(img, (0, 0))
    w, h = img.size
    if size[0] > w:
        col = img.crop((w - 1, 0, w, h)).resize((size[0] - w, h), Image.NEAREST)
        out.paste(col, (w, 0))
    if size[1] > h:
        row = out.crop((0, h - 1, size[0], h)).resize((size[0], size[1] - h), Image.NEAREST)
        out.paste(row, (0, h))
    return out


def encode_palette(img: Image.Image, with_alpha: bool) -> bytes:
    """``GL_PALETTE8_RGBA8``/``RGB8`` payload: 256 entry palette followed by 8 bit indices.

    Images with at most 256 distinct colours are stored exactly; others are quantised.
    """
    img = img.convert("RGBA")
    if not with_alpha:
        img = img.convert("RGB").convert("RGBA")
    colors = img.getcolors(256)
    if colors is not None:
        # exact: build the palette ourselves (most used colour first)
        colors.sort(key=lambda c: -c[0])
        pal = [c[1] for c in colors]
        lut = {c: i for i, c in enumerate(pal)}
        pixels = img.get_flattened_data() if hasattr(img, "get_flattened_data") else img.getdata()
        indices = bytes(lut[px] for px in pixels)
    else:
        opaque = img.getextrema()[3][0] == 255
        if opaque:
            # median cut (RGB only in Pillow) gives clearly better palettes than the octree
            q = img.convert("RGB").quantize(256, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.NONE)
            raw = q.getpalette("RGB")
            pal = [(raw[3 * i], raw[3 * i + 1], raw[3 * i + 2], 255) for i in range(len(raw) // 3)]
        else:
            try:
                q = img.quantize(256, method=Image.Quantize.LIBIMAGEQUANT, dither=Image.Dither.NONE)
            except ValueError:
                q = img.quantize(256, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.NONE)
            raw = q.getpalette("RGBA")
            pal = [tuple(raw[4 * i:4 * i + 4]) for i in range(len(raw) // 4)]
        indices = q.tobytes()
    out = bytearray()
    for c in pal:
        out += bytes(c[:4] if with_alpha else c[:3])
    out += b"\x00" * ((256 - len(pal)) * (4 if with_alpha else 3))
    out += indices
    return bytes(out)


def encode_texture(img: Image.Image, internal_format: int | str, flags: int = DEFAULT_FLAGS) -> dict:
    """Encode a Pillow image; returns the complete TXUR2008 field dict (ready for ``encode_item``).

    S3TC textures are padded to multiples of 4; ``width``/``height`` in the result are the
    padded size, the caller keeps the real size in the IMAG2000 picture record.
    """
    from .s3tc import encode_s3tc
    fmt = parse_format(internal_format)
    img = img.convert("RGBA")
    size = padded_size(fmt, *img.size)
    img = _pad_image(img, size)
    if fmt == 0x8B96:
        data = encode_palette(img, True)
    elif fmt == 0x8B95:
        data = encode_palette(img, False)
    elif fmt in (0x83F0, 0x83F1, 0x83F2, 0x83F3):
        data = encode_s3tc(img.tobytes(), size[0], size[1], fmt)
    else:
        raise ValueError(f"cannot encode texture format 0x{fmt:04x}")
    return {"zero_1": 0, "zero_2": 0, "internal_format": fmt, "format": GL_FORMAT_FOR[fmt], "type": 0x1401,
            "zero_3": 0, "size": len(data), "width": size[0], "height": size[1], "flags": flags, "data": data}
