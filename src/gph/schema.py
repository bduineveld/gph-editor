"""Declarative field layouts for every known section type.

Every item starts with a 4 byte *relocation header*::

    u16 reloc    byte offset (inside the item) of the first pointer field
    u16 nreloc   number of consecutive u32 pointer fields starting at ``reloc``

"Pointers" are u32 byte offsets relative to the start of the item (``0xFFFFFFFF`` = null).
When the RT loads a file into memory it rewrites exactly these ``nreloc`` consecutive words
into absolute addresses, so every pointer field (table pointers *and* the entries of pointer
tables) must be contiguous and must be covered by ``reloc``/``nreloc``.  The decoder checks
this and the tests enforce it on all sample files; it is the strongest evidence that the
layouts below are right.

The schema below describes everything *after* the relocation header.

Field tuple forms (``name`` may be ``None`` for padding)::

    ("u8"|"u16"|"s16"|"u32"|"s32", name)
    ("pad", nbytes)
    ("chars", name, nbytes)              fixed ASCII, e.g. a 4 letter module name
    ("color", name)                      4 bytes RGBA
    ("link", name)                       8 bytes: 4 char module + u16 index + u16 pad
    ("ptr", name)                        bare u32 pointer (rare; usually via "table"/"blob")
    ("blob", name, length=<field>)       pointer to raw bytes of the given length
    ("table", name, count=<field|int>, elem=<elem>)
                                         pointer to an array of ``count`` elements

Table element types::

    "u8" | "u16" | "s16" | "u32"         inline scalar array
    ("ptr", <schema>)                    array of u32 pointers, each to a record laid out
                                         by <schema>; 0xFFFFFFFF entries decode to None
    ("ptr", "link")                      shorthand: pointer array to 8 byte links

Names follow the field names found inside GPH_EDITOR.exe (mira308sw) where known, in
snake_case.
"""

from __future__ import annotations

# ---------------------------------------------------------------------------
# tiny constructors to keep the tables readable
def u8(n): return ("u8", n)
def u16(n): return ("u16", n)
def s16(n): return ("s16", n)
def u32(n): return ("u32", n)
def s32(n): return ("s32", n)
def pad(n): return ("pad", n)
def chars(n, size=4): return ("chars", n, size)
def color(n): return ("color", n)
def link(n): return ("link", n)
def ptr(n): return ("ptr", n)
def blob(n, length): return ("blob", n, length)
def table(n, count, elem): return ("table", n, count, elem)


LINKPTR = ("ptr", "link")

# records used inside pointer tables
IMAG_PICTURE = [u32("width_minus_1"), u32("height_minus_1"), u16("texture"), pad(2)]
BFNT_CHAR = [u16("baseline"), u16("width"), u16("x"), u16("y")]
FONT_BLOCK = [u16("high_byte"), u16("bfnt"), chars("module")]

SCHEMAS: dict[str, list] = {}

# --- pictures ---------------------------------------------------------------
SCHEMAS["VRPI2000"] = [u32("level"), u16("x"), u16("y")]
SCHEMAS["BKGD2000"] = [link("image"), u16("x"), u16("y")]
SCHEMAS["FXPI2000"] = [u32("level"), u32("is_synchronized"), u16("x"), u16("y"), link("image")]
SCHEMAS["DYPI2007"] = [u32("overall"), u16("width"), u16("height"), link("image"),
                       u16("dependencies"), u16("enlargement")]

# --- texts ------------------------------------------------------------------
SCHEMAS["ITXT2000"] = [u32("level"), u32("has_background"), u32("blinking_period"),
                       link("text_color"), link("font"), link("background"),
                       u16("x"), u16("y"), u16("text_width"), u16("interline"),
                       u16("bg_h_offset"), u16("bg_v_offset"),
                       u8("text_hor_alignment"), u8("text_ver_alignment"), pad(2)]
SCHEMAS["DTXT2000"] = [u32("level"), u32("is_synchronized"), u32("blinking_period"), u32("has_background"),
                       link("text_color"), link("font"), link("background"),
                       u16("x"), u16("y"), u16("text_width"), u16("interline"),
                       u16("bg_h_offset"), u16("bg_v_offset"),
                       u8("text_hor_alignment"), u8("text_ver_alignment"), pad(2)]
SCHEMAS["FXTX2006"] = [u32("level"), u32("blinking_period"),
                       link("font"), link("text_color"), link("background"), link("text_index"),
                       u16("x"), u16("y"), u16("bg_h_offset"), u16("bg_v_offset"), u16("text_width"),
                       u8("interline"), u8("style")]
SCHEMAS["TXIN2000"] = [u8("style"), u8("unit_dependance"), u8("text_count"), pad(1),
                       chars("module"),
                       table("texts", "text_count", "u16")]
SCHEMAS["TEXT2006"] = [("utf16", "text")]
SCHEMAS["TXCO2000"] = [color("color")]
SCHEMAS["PALE2000"] = []          # never seen with content

# --- lists / tables ----------------------------------------------------------
SCHEMAS["LIST2001"] = [u32("level"),
                       link("background"), link("arrow"), link("font"),
                       link("selected_color"), link("not_selected_color"), link("not_selectable_color"),
                       link("cursor"), link("scrollbar_bg"), link("scrollbar"),
                       u16("x"), u16("y"), u16("visible_width"), u16("visible_height"),
                       u16("vertical_spacing"), u16("top_margin"), u16("left_margin"), s16("text_image_shift"),
                       u16("scrollbar_dx"), u16("scrollbar_dy"), u16("scrollbar_shift"), u16("scrollbar_min_size"),
                       u16("scrollbar_period"),
                       u8("column_count"), u8("style"), u8("scroll_behavior"), pad(3),
                       table("column_position", "column_count", "u16"),
                       table("column_size", "column_count", "u16"),
                       table("column_alignment", "column_count", "u8")]
SCHEMAS["EDTB2006"] = [u32("level"),
                       link("selected_color"), link("not_selected_color"), link("not_selectable_color"), link("font"),
                       u16("x"), u16("y"),
                       u8("key_count"), u8("style"), u8("image_count"), u8("key_by_line"),
                       table("key_dx", "key_count", "u16"),
                       table("key_dy", "key_count", "u16"),
                       table("images", "image_count", LINKPTR),
                       table("texts", "key_count", LINKPTR),
                       table("unknown", "key_count", "u8"),
                       table("bg_selected", "key_count", "u8"),
                       table("bg_not_selectable", "key_count", "u8")]
SCHEMAS["VWTX2000"] = [u32("level"), link("font"),
                       u16("x"), u16("y"), u16("scrollbar_dx"), u16("scrollbar_dy"),
                       u16("scrollbar_shift"), u16("scrollbar_min_size"),
                       u16("visible_width"), u16("visible_height"), u16("left_margin"), u16("top_margin"),
                       u16("vertical_spacing"),
                       u8("style"), u8("bg_image_count"), u8("scrollbar_bg_count"), u8("cursor_image_count"),
                       u8("text_color_count"), pad(1),
                       table("backgrounds", "bg_image_count", LINKPTR),
                       table("scrollbar_bgs", "scrollbar_bg_count", LINKPTR),
                       table("scrollbars", "cursor_image_count", LINKPTR),
                       table("text_colors", "text_color_count", LINKPTR)]

# --- bars -------------------------------------------------------------------
SCHEMAS["FBAR2000"] = [u32("level"),
                       link("cursor_font"), link("background"), link("foreground"),
                       link("cursor_bg_image"), link("cursor_pointer_image"), link("cursor_text_color"),
                       ("raw", "unknown_1", 4),
                       u16("x"), u16("y"), u16("fg_shift"), u16("pb_style"), u16("shift_2nd"),
                       u8("style"), u8("third_value_count"), u8("cursor_text_count"), u8("unit_dependance"), pad(2),
                       table("third_values", "third_value_count", "u16"),
                       table("third_ranges", "third_value_count", "u16"),
                       # third pointer confirmed by reloc header; always null in samples,
                       # element type assumed like SEBR2000 cursor texts
                       table("cursor_texts", "cursor_text_count", LINKPTR)]
SCHEMAS["SEBR2000"] = [u32("level"), link("cursor_font"), chars("text_module"),
                       u16("x"), u16("y"), u16("fg_shift"), u16("pb_style"), u16("shift_2nd"),
                       u8("style"), u8("third_value_count"), u8("cursor_text_count"), u8("unit_dependance"),
                       u8("image_count"), u8("cursor_image_count"), u8("cursor_text_color_count"), pad(3),
                       table("backgrounds", "image_count", LINKPTR),
                       table("foregrounds", "image_count", LINKPTR),
                       table("third_values", "third_value_count", "u16"),
                       table("third_ranges", "third_value_count", "u16"),
                       table("cursor_backgrounds", "cursor_image_count", LINKPTR),
                       table("cursor_images", "cursor_image_count", LINKPTR),
                       table("text_indexes", "cursor_text_count", "u16"),
                       table("cursor_text_colors", "cursor_text_color_count", LINKPTR)]
SCHEMAS["CRZO2002"] = [u32("overall"), u16("x"), u16("y"), u16("zoom_width"), u16("zoom_height"),
                       u32("map_context"), link("bg_image"), link("bg_color"), u16("bg_presence"), pad(2)]

# --- buttons ----------------------------------------------------------------
SCHEMAS["FBTN2006"] = [u32("level"), link("font"), link("text_index"),
                       u16("x"), u16("y"), u16("text_dx"), u16("text_dy"),
                       u8("style"), u8("image_count"), u8("text_color_count"), u8("alignment"),
                       table("images", "image_count", LINKPTR),
                       table("text_colors", "text_color_count", LINKPTR)]
SCHEMAS["CBTN2006"] = SCHEMAS["FBTN2006"]
SCHEMAS["DBTN2001"] = [u32("level"), link("font"), link("arrow_image"),
                       u16("x"), u16("y"), u16("text_dx"), u16("text_dy"), u16("text_width"),
                       u16("arrow_dx"), u16("arrow_dy"), u16("scrolling_period"),
                       u8("style"), u8("image_count"), u8("text_color_count"), u8("alignment"),
                       u8("scroll_behavior"), pad(3),
                       table("images", "image_count", LINKPTR),
                       table("text_colors", "text_color_count", LINKPTR)]
SCHEMAS["DCBT2000"] = [u32("level"), link("font"),
                       u16("x"), u16("y"), u16("text_dx"), u16("text_dy"), u16("text_width"),
                       u8("style"), u8("image_count"), u8("text_color_count"), u8("alignment"), pad(2),
                       table("images", "image_count", LINKPTR),
                       table("text_colors", "text_color_count", LINKPTR)]
SCHEMAS["ICBT2000"] = [u32("level"), link("font"),
                       u16("x"), u16("y"), u16("text_dx"), u16("text_dy"), u16("text_width"),
                       u8("interline"), u8("style"), u8("image_count"), u8("text_color_count"),
                       u8("text_hor_alignment"), u8("text_ver_alignment"),
                       table("images", "image_count", LINKPTR),
                       table("text_colors", "text_color_count", LINKPTR)]
SCHEMAS["EDTX2000"] = [u32("level"), link("font"),
                       u16("x"), u16("y"), u16("text_dx"), u16("text_dy"), u16("text_width"),
                       u8("style"), u8("not_selected_text_color_count"), u8("selected_text_color_count"),
                       u8("cursor_count"), u8("background_count"), pad(1),
                       table("not_selected_text_colors", "not_selected_text_color_count", LINKPTR),
                       table("selected_text_colors", "selected_text_color_count", LINKPTR),
                       table("cursors", "cursor_count", LINKPTR),
                       table("backgrounds", "background_count", LINKPTR)]
SCHEMAS["MCTX2006"] = [u32("level"), link("font"),
                       u16("x"), u16("y"), u16("text_dx"), u16("text_dy"), u16("scrolling_period"),
                       u8("alignment"), u8("style"), u8("image_count"), u8("text_color_count"),
                       u8("choice_count"), u8("scroll_behavior"),
                       u16("text_width"), pad(2),
                       table("text_colors", "text_color_count", LINKPTR),
                       table("images", "image_count", LINKPTR),
                       table("text_indexes", "choice_count", LINKPTR)]

# --- menus ------------------------------------------------------------------
SCHEMAS["MENU2006"] = [u32("level"), link("image"), link("cursor_image"), link("font"),
                       u16("x"), u16("y"), u16("text_dx"), u16("text_dy"), u16("space_evenly"),
                       u8("style"), u8("text_color_count"), u8("text_alignment"), u8("item_count"), pad(2),
                       table("text_colors", "text_color_count", LINKPTR),
                       table("texts", "item_count", LINKPTR)]
SCHEMAS["IMEN2000"] = [u32("level"), link("image"), link("cursor_image"), link("font"),
                       u16("x"), u16("y"), u16("text_dx"), u16("text_dy"), u16("space_evenly"),
                       u8("style"), u8("text_color_count"), u8("text_alignment"), u8("unit_code"),
                       u8("item_count"), pad(1),
                       table("text_colors", "text_color_count", LINKPTR)]

# --- clock ------------------------------------------------------------------
SCHEMAS["ACLK2000"] = [u32("overall"),
                       color("big_hand_internal_color"), color("big_hand_border_color"), color("big_hand_shadow_color"),
                       color("small_hand_internal_color"), color("small_hand_border_color"), color("small_hand_shadow_color"),
                       link("background_image"), link("center_image"),
                       u16("x"), u16("y"), s16("shadow_h_shift"), s16("shadow_v_shift"),
                       u16("big_hand_border_width"), u16("small_hand_border_width"),
                       u16("big_hand_points_count"), u16("small_hand_points_count"),
                       table("big_hand_point_x", "big_hand_points_count", "s16"),
                       table("big_hand_point_y", "big_hand_points_count", "s16"),
                       table("small_hand_point_x", "small_hand_points_count", "s16"),
                       table("small_hand_point_y", "small_hand_points_count", "s16")]

# --- fonts ------------------------------------------------------------------
SCHEMAS["BFNT2000"] = [u16("canvas_height"), u16("canvas_width"), u8("char_height"), u8("unicode_high_byte"), pad(2),
                       u32("length_bytes"), u32("gl_type"),
                       blob("canvas", "length_bytes"),
                       table("chars", 256, ("ptr", BFNT_CHAR))]
SCHEMAS["FONT2007"] = [u16("font_scale"), u8("block_count"), u8("font_style"),
                       table("blocks", 256, ("ptr", FONT_BLOCK))]

# --- images -----------------------------------------------------------------
SCHEMAS["TXUR2008"] = [u32("zero_1"), u16("zero_2"), u16("internal_format"), u32("format"), u32("type"),
                       u32("zero_3"), u32("size"), u16("width"), u16("height"), u32("flags"),
                       blob("data", "size")]
SCHEMAS["IMAG2000"] = [u8("flag"), u8("image_count"), u16("time"), u16("width"), u16("height"),
                       u8("h_stretch"), u8("v_stretch"), pad(2),
                       table("pictures", "image_count", ("ptr", IMAG_PICTURE))]

# ---------------------------------------------------------------------------
SECTION_DESCRIPTIONS = {
    "GDBF": "Graphic database file", "BKGD": "Background", "DYPI": "Dynamic picture",
    "FXPI": "Fixed picture", "VRPI": "Variable picture", "IMAG": "Image (texture usage)",
    "TXUR": "Texture (pixel data)", "MENU": "Menu", "IMEN": "Indexed menu", "LIST": "List",
    "EDTB": "Edition table (keyboard)", "TEXT": "Text", "TXCO": "Text colour", "DTXT": "Dynamic text",
    "EDTX": "Edited text", "FXTX": "Fixed text", "TXIN": "Text indexes", "ITXT": "Indexed text",
    "MCTX": "Multiple choice text", "VWTX": "View text", "DBTN": "Dynamic button",
    "FBTN": "Fixed button", "CBTN": "Check button", "DCBT": "Dynamic check button",
    "ICBT": "Indexed check button", "FBAR": "Fixed bar", "SEBR": "Selectable bar",
    "CRZO": "Crossroad (map zoom window)", "BFNT": "Base font (bitmap)", "FONT": "Font",
    "PALE": "Palette", "ACLK": "Analog clock", "RAWD": "Raw data (map symbols)",
}

# OpenGL ES enums used in TXUR2008 / BFNT2000 headers
GL_ENUMS = {
    0x1401: "GL_UNSIGNED_BYTE",
    0x1907: "GL_RGB",
    0x1908: "GL_RGBA",
    0x80E1: "GL_BGRA",
    0x83F0: "GL_COMPRESSED_RGB_S3TC_DXT1_EXT",
    0x83F1: "GL_COMPRESSED_RGBA_S3TC_DXT1_EXT",
    0x83F2: "GL_COMPRESSED_RGBA_S3TC_DXT3_EXT",
    0x83F3: "GL_COMPRESSED_RGBA_S3TC_DXT5_EXT",
    0x8B95: "GL_PALETTE8_RGB8_OES",
    0x8B96: "GL_PALETTE8_RGBA8_OES",
}

# names GPH_EDITOR uses for the texture storage types
TEXTURE_TYPE_NAMES = {
    0x8B95: "BMP8.3",   # 8 bit indices + 256 x RGB palette (768 bytes)
    0x8B96: "BMP8.4",   # 8 bit indices + 256 x RGBA palette (1024 bytes)
    0x83F0: "DXT1",     # opaque
    0x83F1: "DXT1A",    # 1 bit alpha
    0x83F2: "DXT3",     # explicit 4 bit alpha (GPH_EDITOR calls this DXT2)
    0x83F3: "DXT5",     # interpolated alpha
}

IMAG_FLAGS = {0: "none", 1: "loop", 2: "autostart", 3: "loop+autostart"}
IMAG_STRETCH = {0: "direct", 1: "third", 4: "bilinear"}

TXCO_DESCRIPTIONS = {
    0: "Title of popup window or frame",
    1: "Normal text",
    3: "Brightest text or selected text",
    4: "MP3 files copy in background",
    5: "Selected button",
    6: "Button or menu option not available",
    7: "Temperature and time in main page",
    0x12: "Zoom/scale value in map view",
    0x13: "Little left map window background",
}
