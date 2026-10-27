"""BFNT2000 bitmap fonts and FONT2007 font tables.

BFNT2000 item::

    u16 reloc=20, u16 nreloc=258
    u16 canvas_height, u16 canvas_width
    u8  char_height, u8 unicode_high_byte, u16 0
    u32 length_bytes          = canvas_width * canvas_height
    u32 gl_type               GL_UNSIGNED_BYTE (0x1401)
    u32 ptr -> canvas         8 bit coverage/alpha, one byte per pixel, row-major
    u32 ptr -> char index     256 x u32 ptr (one per low byte of the code point, 0xFFFFFFFF = absent)
                              each -> record {u16 baseline?, u16 width, u16 x, u16 y}

A glyph is the canvas rectangle (x, y, width, char_height).  The canvas value is the
glyph coverage (255 = foreground), which the RT uses as transparency of the text colour.

FONT2007 item::

    u16 reloc=8, u16 nreloc=257
    u16 font_scale, u8 block_count, u8 font_style
    u32 ptr -> 256 x u32 ptr (one per high byte of the code point)
               each -> record {u16 high_byte, u16 bfnt index, "FNT "}
"""

from __future__ import annotations

from PIL import Image


def canvas_image(fields: dict) -> Image.Image:
    w, h = fields["canvas_width"], fields["canvas_height"]
    return Image.frombytes("L", (w, h), bytes(fields["canvas"][: w * h]))


def glyph_image(fields: dict, code_low: int, canvas: Image.Image | None = None) -> Image.Image | None:
    rec = fields["chars"][code_low]
    if rec is None:
        return None
    canvas = canvas or canvas_image(fields)
    x, y, w, h = rec["x"], rec["y"], rec["width"], fields["char_height"]
    return canvas.crop((x, y, x + w, y + h))


def glyph_table(fields: dict) -> dict[int, dict]:
    """{code point: record} for the glyphs present in this BFNT."""
    high = fields["unicode_high_byte"] << 8
    return {high + i: rec for i, rec in enumerate(fields["chars"]) if rec is not None}


def font_sheet(fields: dict, spacing: int = 1) -> Image.Image:
    """All glyphs side by side on one row (debug / export helper)."""
    canvas = canvas_image(fields)
    glyphs = [(i, glyph_image(fields, i, canvas)) for i in range(256)]
    glyphs = [(i, g) for i, g in glyphs if g is not None]
    width = sum(g.width + spacing for _, g in glyphs) or 1
    sheet = Image.new("L", (width, fields["char_height"]), 0)
    x = 0
    for _, g in glyphs:
        sheet.paste(g, (x, 0))
        x += g.width + spacing
    return sheet
