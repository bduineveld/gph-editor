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


def render_line(text: str, blocks: dict[int, dict], spacing: int = 0, missing: str = "?") -> Image.Image:
    """Draw one line of ``text`` with the bitmap glyphs: ``blocks`` maps the high byte of a code
    point to the decoded ``BFNT2000`` fields.  Returns an ``L`` image (coverage), unscaled: the
    unit multiplies by ``FONT2007.font_scale / 1000``.  Unknown characters use ``missing``."""
    glyphs = []
    height = 1
    for ch in text:
        code = ord(ch)
        fields = blocks.get(code >> 8)
        rec = fields["chars"][code & 0xFF] if fields else None
        if rec is None and missing:
            fields = blocks.get(ord(missing) >> 8)
            rec = fields["chars"][ord(missing) & 0xFF] if fields else None
        if rec is None:
            continue
        height = max(height, fields["char_height"])
        glyphs.append((fields, rec))
    width = max(1, sum(rec["width"] + spacing for _, rec in glyphs))
    out = Image.new("L", (width, height), 0)
    x = 0
    canvases: dict[int, Image.Image] = {}
    for fields, rec in glyphs:
        canvas = canvases.setdefault(id(fields), canvas_image(fields))
        g = canvas.crop((rec["x"], rec["y"], rec["x"] + rec["width"], rec["y"] + fields["char_height"]))
        out.paste(g, (x, 0))
        x += rec["width"] + spacing
    return out


def render_text(text: str, blocks: dict[int, dict], scale: int = 1000, color=(255, 255, 255, 255),
                interline: int = 0, spacing: int = 0) -> Image.Image:
    """Multi-line (``\r`` separated) text as an RGBA image in ``color`` at ``scale``/1000."""
    lines = [render_line(ln, blocks, spacing) for ln in text.replace("\n", "\r").split("\r")] or [render_line("", blocks)]
    factor = max(1, scale) / 1000
    scaled = []
    for im in lines:
        w, h = max(1, round(im.width * factor)), max(1, round(im.height * factor))
        scaled.append(im.resize((w, h), Image.BILINEAR) if factor != 1 else im)
    width = max(im.width for im in scaled)
    height = sum(im.height for im in scaled) + interline * (len(scaled) - 1)
    out = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    y = 0
    rgb = tuple(color[:3])
    for im in scaled:
        layer = Image.new("RGBA", im.size, rgb + (0,))
        alpha = im if len(color) < 4 or color[3] == 255 else im.point(lambda v: v * color[3] // 255)
        layer.putalpha(alpha)
        out.paste(layer, (0, y))
        y += im.height + interline
    return out
