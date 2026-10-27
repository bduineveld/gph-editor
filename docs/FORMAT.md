# GPH file format

GPH ("graphic database") files hold the skin of the Magneti Marelli RT4 / RT5 / RT6 (RNEG)
navigation units used in Peugeot and Citroën cars: textures, image definitions, fonts,
colours, every text string and the layout parameters of every widget.

Everything in this document was derived from the files themselves, cross-checked with the
behaviour of mira308sw's GPH_EDITOR (v01.13/v01.16) and its manual.  Nothing here is
official.  All integers are **big-endian** unless stated otherwise.  The reader in
`src/gph/` implements exactly this description and is verified against ~40 factory files:
re-serialising them is byte identical, and every item of every file passes the
relocation-header check described in §3.

## 1. Files and where they live

On the unit (RT6) the files are under `/SDC/Data_Base/Graphics/RNEG/` (RT4/RT5:
`/C/Data_Base/Graphics/`, with an `800X446` sub tree for high resolution).  Files are
bz2 compressed on the RT6 (`*.gph.bz2`) and come with an `.inf` side-car (§7).

| file | internal name | content |
| --- | --- | --- |
| `Common/GEN_common.gph` | `GEN COMM` | all widget definitions (menus, buttons, texts, lists ...) and the text index table |
| `Common/FNT_common.gph` | `FNT COMM` | fonts: `FONT2007` tables and `BFNT2000` bitmaps |
| `<LL>/GEN_<LL>.gph` | `GEN DU  `, `GEN DE  `, ... | every text string of one language (`TEXT2006`) |
| `HARMnn/GEN_HAnn.gph` | `GEN HA00` ... | colour theme *nn*: colours, clock, textures and image definitions |
| `HARMnn/LRG_HAnn.gph` | `LRG HA00` ... | second texture/image set of the theme (large images) |
| `HARMnn/$$$$_HA00.gph` | `$$$$HA00` | tiny stub with one colour; `TXUR2000` section (older texture format) |
| `Day/MapGfx.gph`, `Night/MapGfx.gph` | | map symbols (`RAWD2000`); not available to us yet |

Themes: HA00 Titanium, HA01 Blue Road, HA02 Infinite, HA03 Red Flame, HA04 Carbon.

## 2. Container

```
offset  size  field
0       8     "GDBF2000"              database type / magic
8       8     internal name           space padded, e.g. "GEN HA00"; first 4 chars = module
16      1     0
17      3     stamp                   see §2.1
20      4     u32 length              whole file length in 4-byte blocks
24      ...   sections, back to back, until end of file
```

Section:

```
0       8     name                    4 letter kind + 4 digit version, e.g. "TXUR2008"
8       4     u32 length              section length in 4-byte blocks, header included
12      4     u32 count               number of item slots
16      4*(count+1)                   index (absent when count == 0)
...                                   item data
```

The index holds, per slot, the byte offset of the item relative to the first byte of item
data; `0xFFFFFFFF` marks an empty slot.  The extra last entry equals the total item data
length, so the length of item *i* is `next_non_empty_entry - entry[i]`.  Every item is a
multiple of 4 bytes.  An empty section is exactly 16 bytes.

Items are referenced *by slot number* from other files (§4), so slot numbers must never
shift when editing: keep empty slots empty, append new items at the end.

Section order inside the files is fixed (see any sample); `GEN_common` even contains
empty `BFNT2000`/`FONT2007` sections and `FNT_common` contains all the empty widget
sections.

### 2.1 Stamp bytes 17..19

Factory files from 2011 carry values like `21 01 37`, `21 01 38`, `21 01 20` (and older
units `19 02 03`, `18 04 13`) which do not look like a date.  Files written by the PHP
tool (2016) carry `dd mm yy` in BCD-ish hex (`06 0b 10` = 6 Nov 2016) and GPH_EDITOR
shows this field as a "creation date" and lets you edit it.  Whether the unit checks it is
unknown; keep the original bytes when editing.

## 3. Items and the relocation header

Every item starts with

```
u16 reloc    byte offset, inside the item, of the first pointer field
u16 nreloc   number of consecutive u32 pointer fields starting at reloc
```

A *pointer* is a u32 byte offset relative to the start of the item (`0xFFFFFFFF` = null).
When the unit loads a file it turns exactly these `nreloc` consecutive words into real
memory addresses.  Consequently, in every item type, the fixed header is followed by all
pointer fields *contiguously*: first the pointers to tables, then the entries of pointer
tables (which are themselves pointers), and only after those the data they point to
(8 byte links, u16 arrays, pixel data ...).  Items without pointers have `reloc = nreloc = 0`.

This makes `reloc/nreloc` a built-in checksum of our understanding of each layout: the
decoder records where it found pointer fields and compares with the header
(`gph check`).  All 42 000 items in the sample set pass.

Example, `FBTN2006` item 0 of `GEN_common` (140 bytes):

```
00 24 00 0a   reloc=36 nreloc=10
00 00 00 00   level
46 4e 54 20 00 00 00 00   link font      = FNT:0
47 45 4e 20 0a fa 00 00   link text_index= GEN:2810 (TXIN2000)
02 12 01 5e   x=530 y=350
00 20 00 0a   text_dx=32 text_dy=10
17 04 04 00   style=0x17 image_count=4 text_color_count=4 alignment=0
00 00 00 2c   ptr -> image table      (offset 36)  \
00 00 00 3c   ptr -> text colour table(offset 40)   | 10 pointers = nreloc
00 00 00 4c  00 00 00 54  00 00 00 5c  00 00 00 64   | image table: 4 ptrs to links
00 00 00 6c  00 00 00 74  00 00 00 7c  00 00 00 84  /  colour table: 4 ptrs to links
4c 52 47 20 00 3c 00 00   link LRG:60   (image)
...                       3 more image links, 4 colour links
```

### 3.1 Links (8 bytes)

```
char[4] module   "GEN ", "LRG ", "FNT "
u16     index    slot number in the target section, 0xFFFF = none
u16     0
```

Which file and section a link targets depends on the *field*, see §4.

### 3.2 Field layouts

The authoritative, machine-readable layouts are in [`src/gph/schema.py`](../src/gph/schema.py).
Summary (after the 4 byte relocation header; `ptr→` = pointer to):

| section | layout |
| --- | --- |
| `VRPI2000` variable picture | u32 level, u16 x, u16 y |
| `BKGD2000` background | link image, u16 x, u16 y |
| `FXPI2000` fixed picture | u32 level, u32 is_synchronized, u16 x, u16 y, link image |
| `DYPI2007` dynamic picture | u32 overall, u16 width, u16 height, link image, u16 dependencies, u16 enlargement |
| `ITXT2000` indexed text | u32 level, u32 has_background, u32 blinking_period, link text_color, link font, link background, u16 x, y, text_width, interline, bg_h_offset, bg_v_offset, u8 hor_align, u8 ver_align, pad2 |
| `DTXT2000` dynamic text | u32 level, is_synchronized, blinking_period, has_background, then as ITXT from the links on |
| `FXTX2006` fixed text | u32 level, u32 blinking_period, link font, link text_color, link background, link text_index, u16 x, y, bg_h_offset, bg_v_offset, text_width, u8 interline, u8 style |
| `TXIN2000` text index | u8 style, u8 unit_dependance, u8 text_count, pad1, char[4] module, ptr→ u16[text_count] text slots |
| `TEXT2006` text | UTF-16BE string, NUL terminated, NUL padded to 4 bytes; `U+000D` = line break |
| `TXCO2000` colour | u8 r, g, b, a |
| `LIST2001` list | u32 level, 9 links (background, arrow, font, selected/not selected/not selectable colour, cursor, scrollbar_bg, scrollbar), 13 × u16 geometry, u8 column_count, style, scroll_behavior, pad3, 3 ptr→ column tables |
| `EDTB2006` keyboard | u32 level, 4 links, u16 x, y, u8 key_count, style, image_count, key_by_line, 7 ptr→ tables (key_dx, key_dy, images, texts, unknown, bg_selected, bg_not_selectable) |
| `VWTX2000` view text | u32 level, link font, 11 × u16, u8 style + 4 counts, pad1, 4 ptr→ link tables |
| `FBAR2000` fixed bar | u32 level, 6 links, 4 bytes `FF`, 5 × u16, u8 style + 3 counts, pad2, 3 ptr→ (third_values u16[], third_ranges u16[], cursor_texts) |
| `SEBR2000` selectable bar | u32 level, link cursor_font, char[4] text_module, 5 × u16, 7 × u8, pad3, 8 ptr→ tables |
| `CRZO2002` crossroad | u32 overall, u16 x, y, zoom_width, zoom_height, u32 map_context, link bg_image, link bg_color, u16 bg_presence, pad2 |
| `FBTN2006` / `CBTN2006` buttons | u32 level, link font, link text_index, u16 x, y, text_dx, text_dy, u8 style, image_count, text_color_count, alignment, 2 ptr→ link tables |
| `DBTN2001` dynamic button | u32 level, link font, link arrow_image, 8 × u16, u8 style, image_count, text_color_count, alignment, scroll_behavior, pad3, 2 ptr→ link tables |
| `DCBT2000` | u32 level, link font, 5 × u16, 4 × u8, pad2, 2 ptr→ link tables |
| `ICBT2000` | u32 level, link font, 5 × u16, 6 × u8, 2 ptr→ link tables |
| `EDTX2000` edited text | u32 level, link font, 5 × u16, 5 × u8, pad1, 4 ptr→ link tables |
| `MCTX2006` multiple choice | u32 level, link font, 5 × u16, 6 × u8, u16 text_width, pad2, 3 ptr→ link tables (colours, images, text indexes) |
| `MENU2006` menu | u32 level, link image, link cursor_image, link font, 5 × u16, u8 style, text_color_count, text_alignment, item_count, pad2, ptr→ colour links, ptr→ text index links |
| `IMEN2000` indexed menu | as MENU with an extra u8 unit_code and only the colour table |
| `ACLK2000` clock | u32 overall, 6 colours, link background_image, link center_image, u16 x, y, s16 shadow dx, dy, u16 border widths, u16 point counts, 4 ptr→ s16[] hand polygons |
| `BFNT2000` bitmap font | see §5 |
| `FONT2007` font | u16 font_scale, u8 block_count, u8 font_style, ptr→ 256 × ptr→ {u16 high_byte, u16 bfnt slot, "FNT "} |
| `TXUR2008` texture | see §6 |
| `IMAG2000` image | u8 flag, u8 image_count, u16 time_ms, u16 width, u16 height, u8 h_stretch, u8 v_stretch, pad2, ptr→ image_count × ptr→ {u32 width-1, u32 height-1, u16 texture slot, pad2} |
| `PALE2000`, `TXUR2000`, `RAWD2000` | no content in our samples (`TXUR2000` is the RT4 texture format with power-of-two sizes) |

`IMAG2000` flags: 0 none, 1 loop, 2 autostart, 3 loop+autostart.  Stretch: 0 direct,
1 third, 4 bilinear.  `TXCO2000` slot meanings (from GPH_EDITOR): 0 popup/frame title,
1 normal text, 3 highlighted/selected, 4 MP3 copy, 5 selected button, 6 not selectable,
7 temperature and time, 0x12 map zoom value, 0x13 small left map window background.

## 4. Cross references

| field meaning | module | target file | target section |
| --- | --- | --- | --- |
| image | `GEN` | `GEN_HAnn` | `IMAG2000` → `TXUR2008` of the same file |
| image | `LRG` | `LRG_HAnn` | `IMAG2000` → `TXUR2008` of the same file |
| colour | `GEN` | `GEN_HAnn` | `TXCO2000` |
| font | `FNT` | `FNT_common` | `FONT2007` → `BFNT2000` of the same file |
| text index | `GEN` | `GEN_common` | `TXIN2000` → text slots |
| text slot | (`TXIN2000.module` = `GEN`) | `GEN_<language>` | `TEXT2006` |

So a menu entry is: `MENU2006.texts[i]` → `TXIN2000` slot → list of `TEXT2006` slots (more
than one when the text depends on units, e.g. km/h and mph) → the string in the active
language file.  Images never point to textures directly: `IMAG2000` defines the displayed
size, the (sub)rectangle of each texture and animation parameters.

## 5. Fonts

`FONT2007` is a 256 entry table indexed by the *high* byte of a UTF-16 code point; each
used entry names the `BFNT2000` slot holding the glyphs for that 256 code point block.
`font_scale` is the size in some internal unit (587, 489, 730 ... in the samples).

`BFNT2000`:

```
u16 canvas_height, u16 canvas_width
u8  char_height, u8 unicode_high_byte, u16 0
u32 length_bytes          = canvas_width * canvas_height
u32 gl_type               0x1401 GL_UNSIGNED_BYTE
ptr→ canvas               8 bit per pixel coverage (255 = ink), row-major
ptr→ 256 × ptr→ glyph record {u16 baseline(0 in samples), u16 width, u16 x, u16 y}
```

A glyph is the canvas rectangle `(x, y, width, char_height)`.  Layout order of an item:
header, char index (256 pointers), canvas, glyph records.

## 6. Textures (`TXUR2008`)

The 40 byte header is an OpenGL ES texture descriptor:

```
u16 reloc=36, u16 nreloc=1
u32 0
u16 0, u16 internal_format
u32 format        0x1908 GL_RGBA, 0x1907 GL_RGB, 0x80E1 GL_BGRA (used with S3TC)
u32 type          0x1401 GL_UNSIGNED_BYTE
u32 0
u32 size          payload bytes
u16 width, u16 height
u32 flags         0x01030000 in every factory file (0x01000000 in files written by the PHP tool)
ptr→ payload      always 40
```

| internal_format | GL name | GPH_EDITOR name | payload |
| --- | --- | --- | --- |
| `0x8B96` | `GL_PALETTE8_RGBA8_OES` | BMP8.4 | 256 × RGBA palette (1024 B) + w·h indices |
| `0x8B95` | `GL_PALETTE8_RGB8_OES` | BMP8.3 | 256 × RGB palette (768 B) + w·h indices |
| `0x83F0` | `..._RGB_S3TC_DXT1` | DXT1 | 8 B per 4×4 block, opaque |
| `0x83F1` | `..._RGBA_S3TC_DXT1` | DXT1A | 8 B per block, 1 bit alpha |
| `0x83F2` | `..._RGBA_S3TC_DXT3` | DXT2 (sic) | 16 B per block, 4 bit alpha |
| `0x83F3` | `..._RGBA_S3TC_DXT5` | DXT5 | 16 B per block, interpolated alpha |

S3TC colours are little-endian RGB565 as in every DDS file.  Width and height are
multiples of 4 for compressed textures (GPH_EDITOR rounds up).  GPH_EDITOR also knows a
"BMP 32bit" type (raw RGBA, 4·w·h) that does not occur in our samples; it would be
`internal_format = 0x1908`.

## 7. `.inf` side-car

```
7fe71346             8 hex digit checksum
VER:08.08.10.10.10   yy.mm.dd.hh.mm
TYPE:DATA
COMPRESSED:NO
SIZE:31403           byte size of the file it accompanies (the .bz2)
ENTRY:NO
SUBVER:
```

`SIZE` is the size of the `.gph.bz2`.  The first line is **two 16-bit checksums**, not one
32-bit CRC (which is why no CRC-32 variant matched).  Recovered by decompiling
`GPH_EDITOR.exe` with Ghidra (functions `0x4cb6b0`, `0x4cb750`, `0x4cb7b0`; table at
`0x634ab8`) and verified on factory `.inf` files from 2012/2015/2016 and on Magneti
Marelli's own `builtinsRNEG.out.inf`:

* hex digits 5–8: `crc16(data)` over the complete data file (the `.bz2` bytes);
* hex digits 1–4: `crc16(inf[4:])` over the `.inf` file itself from byte 4 to the end
  (so over the data checksum digits and every following line, line endings included).

`crc16` is MSB-first, initial value 0, no final xor, with a 256-entry table `T`:

```
crc = T[crc >> 8] ^ T[byte] ^ (crc << 8)      (16 bit)
```

`T` is linear and equals the *reflected* CRC-16 table for polynomial `0xD415` with each
entry byte-swapped (`T[1] = 0xDF2B`, `T[128] = 0x15D4`).  GPH_EDITOR writes the `.inf` in
three steps: compute the data checksum and patch digits 5–8, replace the `SIZE:` line,
then compute the text checksum over bytes 4..end and patch digits 1–4.  When no `.inf`
exists it starts from `00000000 / VER:08.08.10.10.10 / TYPE:DATA / COMPRESSED:NO / SIZE:0 /
ENTRY:NO / SUBVER:`.  Factory files use LF line endings, GPH_EDITOR's template CRLF; both
verify because the text checksum covers whatever bytes are there.

Implementation: [`src/gph/inf.py`](../src/gph/inf.py) (`crc16`, `InfFile.update`,
`write_inf_for`).

## 8. Open questions

* Meaning of the stamp bytes (§2.1) and whether the unit validates them.
* `FBAR2000` bytes 56..59 (`FF FF FF FF`) and the element type of its third (always null) table.
* `BFNT2000` glyph record first word ("baseline"?) is 0 everywhere; GPH_EDITOR's export
  marks a baseline, so it may live elsewhere.
* `PALE2000`, `TXUR2000`, `RAWD2000` content.
* Exact meaning of `level`, `overall`, `style` bit fields, `pb_style`, `unit_dependance`.
