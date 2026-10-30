# gph-editor

Reader and editor for the **GPH graphic database files** of the Magneti Marelli
RT4 / RT5 / RT6 (RNEG) navigation units found in Peugeot and Citroën cars.  These files
contain the complete skin of the unit: textures, image definitions, bitmap fonts, colour
themes, every text string and the layout of every menu, button and list.

The format description lives in [docs/FORMAT.md](docs/FORMAT.md).  The code is a clean
room Python implementation; it builds on earlier PHP/Python experiments and on what
mira308sw's GPH_EDITOR (2009–2015) made public, but shares no code with either.

## Status

* Container parsing and writing: byte-exact round trip on all ~40 factory files we have.
* All 30 section types of `GEN_common`, `FNT_common`, `GEN_<lang>`, `GEN_HAnn`, `LRG_HAnn`
  and `$$$$_HA00` are decoded field by field; the layouts are validated against the
  relocation headers of 42 000 items (`gph check`).
* Textures (palettised and DXT1/DXT1A/DXT3/DXT5), bitmap fonts and UTF-16 texts export
  to PNG/GIF/text.
* Links between files are resolved (menu → text index → language text, colours, fonts,
  image → texture).
* The `.inf` side-car checksum is understood (two 16-bit CRCs, recovered from GPH_EDITOR.exe)
  and can be verified and written: `gph inf verify FILE.gph.bz2`, `gph inf write FILE.gph.bz2`.
* **Writing**: every item type is re-encoded from its fields (byte identical to the factory
  files for all 42 000 items), so texts, colours and any widget field can be changed and the
  result saved as a valid `.gph.bz2` with a matching `.inf`.
* **Textures**: PNG/GIF images encode to BMP8.3/BMP8.4 (palettised, exact up to 256 colours)
  and DXT1/DXT1A/DXT3/DXT5 (pure Python S3TC compressor); `gph set-image` replaces an image
  definition including animations, `gph set-texture` a single texture.
* **Web UI**: `gph serve` opens a local editor in the browser: browse every section, edit
  texts and colours, replace images and textures (with a quality preview), change any
  widget field as JSON, undo, and save packages.
* **Screens**: the firmware's screen database (`GraphicBase.RNEG.gz` from the unit or its
  software update) is decoded, so the editor can show the 615 screens of the unit with every
  widget in place and tell for each widget on which screens it appears (`docs/FORMAT.md` §8).
* Not yet tried on a real unit.

## Install

```bash
python3 -m venv .venv
.venv/bin/pip install -e ".[dev]"
```

Python 3.11+ and Pillow.

## Use

```bash
gph info  path/to/GEN_common.gph.bz2            # header + section table
gph dump  GEN_HA00.gph -s TXCO2000               # decoded items of one section
gph dump  GEN_common.gph -s MENU2006 -i 1 -w Common DU HARM00   # resolve links through a workspace
gph check Graphics/                              # validate every item against its relocation header
gph roundtrip Graphics/                          # parse + re-serialise, must be identical
gph export GEN_HA00.gph -o out -w Graphics/      # JSON per section, PNG textures, GIF animations, fonts, texts
```

A *workspace* is the set of related files (common, font, one language, one theme).  Point
`-w` at the files or at the `Graphics` folder and use `--language DU --theme HA00` when it
contains several.

### Web UI

```bash
gph serve Graphics/            # opens http://127.0.0.1:8765/ with every GPH file below Graphics/
```

The page lists the open files on the left (open more with *Open…* or by dropping files on
the page).  Texts, colours, images, textures, fonts and the widget sections each have their
own view; the panel on the right edits the selected item.  Image and texture replacement
takes PNG/GIF files (several files or an animated GIF become an animation), shows a quality
preview for the chosen format, and keeps the display size and texture format like
`gph set-image` does.  Every edit stays in memory (with undo) until *Save…* writes a
`.gph.bz2` with its `.inf` to a folder of your choice; the original file is never
overwritten.  Open the whole `Graphics` folder and the widget sections of `GEN_common`
come alive: every link is resolved through the chosen theme and language (selectors in
the top bar), so a button shows its font, its text, its four state images and its text
colours, each one a click away, and the item is drawn at its `x`/`y` on an 800×480 stage
over the theme's background (menus with their items, the keyboard with its keys, lists
with their visible area).  On its own the stage places what the file says; which widgets
the firmware shows together comes from the unit's screen database:

```bash
gph serve Graphics/ --screens RT6-Soft/Data_Base/Graphics/GraphicBase.RNEG.gz
```

(or open that file, or a folder containing it, with *Open…*).  A *Screens* entry then lists
every screen of the unit by name and draws it complete: background, fixed texts and
pictures, buttons, menus with the lines the firmware attaches to them, texts with their
font and colour.  Each widget links to its item, and each item says on which screens it
appears.  When the software update's `Application/MMI` folder sits next to the database,
the screen names and owning modules are read from the firmware modules as well.  The
server binds to localhost only and can read and write files on this machine, like any
local tool; `--port` and `--host` change the address.

### Editing from the command line

```bash
gph set-text  DU/GEN_DU.gph.bz2 1 "Film" 0x2A 'Regel een\nregel twee' -o out/DU/
gph set-color HARM00/GEN_HA00.gph.bz2 1 '#ffcc00' 3 '#ff0000' -o out/HARM00/
gph set GEN_common.gph.bz2 -s FBTN2006 -i 0 x=100 y=200 font=FNT:2 'images=["LRG:1","LRG:2"]' -o out/Common/
gph export DU/GEN_DU.gph.bz2 -o exp            # edit exp/GEN_DU/TEXT2006.txt or any <SECTION>.json ...
gph import DU/GEN_DU.gph.bz2 exp/GEN_DU -o out/DU/   # ... and apply it
gph set-image   HARM00/GEN_HA00.gph.bz2 0x1A logo.png -o out/HARM00/          # replace an image (IMAG2000)
gph set-image   HARM00/GEN_HA00.gph.bz2 5 anim.gif -f DXT5 -o out/HARM00/     # animation: GIF or several PNGs
gph set-texture HARM00/LRG_HA00.gph.bz2 0x2C bg.png -f BMP8.4 -o out/HARM00/  # replace one texture (TXUR2008)
gph preview photo.png -f DXT1 -o check.png     # see what a format does to an image (prints PSNR)
gph save GEN_HA00.gph -o out/HARM00/           # .gph -> .gph.bz2 + .inf
```

Use `gph dump FILE -s IMAG2000 -w Graphics/` or the PNGs from `gph export` to find the image
index to replace.  `set-image` keeps the texture format of the image it replaces unless
`-f` says otherwise, pads S3TC textures to multiples of 4, keeps the display size when the
unit stretches the image (`--display WxH` overrides), and allocates new texture slots when
the old ones are shared with other images (`--share` overwrites anyway).

`-o` takes a file or a folder; with a folder the file gets the name the unit expects
(`GEN_DU.gph.bz2`).  Output ending in `.bz2` is compressed and gets its `.inf` side-car
(fields other than size/checksum are copied from the source's `.inf`; `--version` sets
`VER`).  Indexes are decimal or `0x..`, as `gph dump` prints them in hex.  In texts `\n`
is a line break (the unit uses U+000D).  Count fields (`image_count` ...) follow the lists
automatically.

## Library

```python
from gph import GphFile, decode_item, Workspace
from gph.textures import texture_image

f = GphFile.load("GEN_HA00.gph.bz2")
tex = f.section("TXUR2008")
item = decode_item(tex.name, 0, tex.items[0])
texture_image(item.fields).save("TXUR0000.png")

ws = Workspace.from_paths(["Graphics"], language="DU", theme="HA00")
menu = ws.item(ws.common, "MENU2006", 1)
print([ws.text_index(l) for l in menu.fields["texts"]])

from gph.edit import set_text, set_color, update_item, write_package
lang = GphFile.load("Graphics/DU/GEN_DU.gph.bz2")
set_text(lang, 1, "Film")
write_package(lang, "out/DU/GEN_DU.gph.bz2")          # + GEN_DU.gph.bz2.inf
theme = GphFile.load("Graphics/HARM00/GEN_HA00.gph.bz2")
set_color(theme, 1, "#ffcc00")
update_item(theme, "IMAG2000", 5, {"time": 200, "pictures[0].texture": 7})
write_package(theme, "out/HARM00/")
```

## Tests

`pytest` runs against sample files in `samples/` (not part of the repository: GPH files are
copyrighted PSA/Magneti Marelli content).  Set `GPH_SAMPLES` to point at your own copy of
the `Graphics` folder.

## Credits

* mira308sw for GPH_EDITOR, its manual and the section/field names it exposes.
* The rt4.wikidot.com, planete-citroen.com and passionepeugeot.it communities.

## License

MIT.  No affiliation with Magneti Marelli, PSA, Peugeot or Citroën.  Flashing modified
files to a navigation unit can brick it; the GPH_EDITOR manual's advice applies: never
replace the *active* theme, switch themes first and test the modified one afterwards.
