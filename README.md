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
* Not yet: texture encoding (replacing images with your own PNGs), a GUI.

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

### Editing

```bash
gph set-text  DU/GEN_DU.gph.bz2 1 "Film" 0x2A 'Regel een\nregel twee' -o out/DU/
gph set-color HARM00/GEN_HA00.gph.bz2 1 '#ffcc00' 3 '#ff0000' -o out/HARM00/
gph set GEN_common.gph.bz2 -s FBTN2006 -i 0 x=100 y=200 font=FNT:2 'images=["LRG:1","LRG:2"]' -o out/Common/
gph export DU/GEN_DU.gph.bz2 -o exp            # edit exp/GEN_DU/TEXT2006.txt or any <SECTION>.json ...
gph import DU/GEN_DU.gph.bz2 exp/GEN_DU -o out/DU/   # ... and apply it
gph save GEN_HA00.gph -o out/HARM00/           # .gph -> .gph.bz2 + .inf
```

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
