"""High level editing: change items, texts and colours, import exports, write packages.

Everything here works on :class:`gph.container.GphFile` objects in memory; nothing is written
until :func:`write_package` is called.

Typical use::

    from gph import GphFile
    from gph.edit import set_text, set_color, write_package

    lang = GphFile.load("Graphics/DU/GEN_DU.gph.bz2")
    set_text(lang, 0x0001, "Film")
    write_package(lang, "out/DU/GEN_DU.gph.bz2")        # writes the .bz2 and its .inf

    theme = GphFile.load("Graphics/HARM00/GEN_HA00.gph.bz2")
    set_color(theme, 1, "#ffcc00")                       # normal text colour
    write_package(theme, "out/HARM00/GEN_HA00.gph.bz2")
"""

from __future__ import annotations

import json
import os
import re
from typing import Any, Iterable

from .container import GphFile, Section
from .decode import Link, decode_item
from .encode import EncodeError, encode_item, to_color, to_link
from .inf import InfFile, write_inf_for

__all__ = [
    "default_filename", "get_section", "set_item", "update_item", "get_fields",
    "get_text", "set_text", "set_color", "get_color",
    "escape_text", "unescape_text", "export_texts", "import_texts", "import_json", "import_dir",
    "parse_value", "write_package",
    "load_frames", "texture_users", "set_texture", "set_image",
]


# ---------------------------------------------------------------------------
# file names
def default_filename(f: GphFile, compressed: bool = True) -> str:
    """The file name the unit expects for this internal name: ``GEN COMM`` -> ``GEN_common.gph.bz2``."""
    module = f.module.strip() or "GEN"
    variant = f.variant.strip()
    if variant == "COMM":
        variant = "common"
    return f"{module}_{variant}.gph" + (".bz2" if compressed else "")


# ---------------------------------------------------------------------------
# items
def get_section(f: GphFile, name: str, create: bool = False) -> Section:
    sec = f.section(name)
    if sec is None:
        if not create or len(name) != 8:
            raise KeyError(f"{f.name!r} has no section {name!r}")
        sec = Section(name)
        f.sections.append(sec)
    return sec


def set_item(f: GphFile, section: str, index: int, data: bytes | None) -> None:
    """Store raw item bytes (``None`` empties the slot); the section grows when needed."""
    sec = get_section(f, section, create=True)
    if index < 0:
        raise IndexError(index)
    while len(sec.items) <= index:
        sec.items.append(None)
    sec.items[index] = data


def get_fields(f: GphFile, section: str, index: int) -> dict[str, Any] | None:
    """Decoded fields of an item, or ``None`` for an empty/missing slot."""
    sec = f.section(section)
    if sec is None or index >= len(sec.items) or sec.items[index] is None:
        return None
    return decode_item(sec.name, index, sec.items[index]).fields


def update_item(f: GphFile, section: str, index: int, changes: dict[str, Any]) -> bytes:
    """Merge ``changes`` over the decoded item and store the re-encoded bytes.

    Lists replace the whole list; a change for a pointer-table element can also be given
    as ``{"pictures[2]": {...}}`` or ``{"pictures[2].texture": 5}``.  Returns the new bytes.
    """
    sec = get_section(f, section, create=True)
    fields = get_fields(f, sec.name, index) or {}
    _apply_changes(fields, changes)
    data = encode_item(sec.name, fields)
    set_item(f, sec.name, index, data)
    return data


_PATH_RE = re.compile(r"([A-Za-z_][A-Za-z0-9_]*)(?:\[(\d+)\])?")


def _apply_changes(fields: dict, changes: dict) -> None:
    for key, value in changes.items():
        if key in ("index", "size") or key.startswith("_"):
            continue
        if isinstance(value, dict) and "bytes" in value and len(value) == 1:
            continue  # JSON placeholder for a blob that was too long to export: keep original
        parts = key.split(".")
        target = fields
        for i, part in enumerate(parts):
            m = _PATH_RE.fullmatch(part)
            if not m:
                raise EncodeError(f"bad field path {key!r}")
            name, idx = m.group(1), m.group(2)
            last = i == len(parts) - 1
            if idx is None:
                if last:
                    target[name] = value
                else:
                    target = target.setdefault(name, {})
            else:
                lst = target.setdefault(name, [])
                if lst is None:
                    lst = target[name] = []
                idx = int(idx)
                while len(lst) <= idx:
                    lst.append(None)
                if last:
                    lst[idx] = value
                else:
                    if lst[idx] is None:
                        lst[idx] = {}
                    target = lst[idx]


# ---------------------------------------------------------------------------
# texts (TEXT2006 in GEN_<lang>)
def get_text(f: GphFile, index: int) -> str | None:
    fields = get_fields(f, "TEXT2006", index)
    return None if fields is None else fields.get("text")


def set_text(f: GphFile, index: int, text: str) -> None:
    """Set a language text.  Use ``"\\r"`` (U+000D) for a line break, as the factory files do."""
    update_item(f, "TEXT2006", index, {"text": text.replace("\n", "\r")})


def escape_text(text: str) -> str:
    """One line form used by the text export: ``\\`` -> ``\\\\``, line break -> ``\\n``, other
    control characters (the factory files contain e.g. U+0085) -> ``\\uXXXX``."""
    out = []
    for ch in text:
        if ch == "\\":
            out.append("\\\\")
        elif ch in "\r\n":
            out.append("\\n")
        elif ord(ch) < 0x20 or 0x7F <= ord(ch) <= 0x9F or ch in "\u2028\u2029":
            out.append(f"\\u{ord(ch):04x}")
        else:
            out.append(ch)
    return "".join(out)


def unescape_text(line: str) -> str:
    out = []
    i = 0
    while i < len(line):
        ch = line[i]
        i += 1
        if ch != "\\" or i >= len(line):
            out.append(ch)
            continue
        nxt = line[i]
        i += 1
        if nxt in ("n", "r"):
            out.append("\r")
        elif nxt == "u" and len(line) >= i + 4:
            out.append(chr(int(line[i:i + 4], 16)))
            i += 4
        else:
            out.append(nxt)
    return "".join(out)


def export_texts(f: GphFile) -> str:
    sec = f.section("TEXT2006")
    lines = []
    if sec is not None:
        for idx, raw in sec.used():
            lines.append(f"{idx:04X}\t{escape_text(decode_item(sec.name, idx, raw).fields['text'])}")
    return "\n".join(lines) + "\n"


def import_texts(f: GphFile, source: str | Iterable[str]) -> int:
    """Apply a text export (``XXXX<TAB>text`` per line, hex index) to ``f``; returns the count."""
    if isinstance(source, str):
        with open(source, encoding="utf-8", newline="") as fh:
            lines = fh.read().split("\n")   # not splitlines(): texts may contain U+0085 etc.
    else:
        lines = list(source)
    n = 0
    for ln, line in enumerate(lines, 1):
        line = line.rstrip("\r")
        if not line.strip() or line.startswith("#"):
            continue
        if "\t" not in line:
            raise ValueError(f"line {ln}: expected 'XXXX<TAB>text'")
        idx, text = line.split("\t", 1)
        set_text(f, int(idx.strip(), 16), unescape_text(text))
        n += 1
    return n


# ---------------------------------------------------------------------------
# colours (TXCO2000 in GEN_HAxx)
def get_color(f: GphFile, index: int) -> dict | None:
    fields = get_fields(f, "TXCO2000", index)
    return None if fields is None else fields.get("color")


def set_color(f: GphFile, index: int, color) -> None:
    """Set a theme colour; ``color`` is ``"#rrggbb[aa]"``, an (r, g, b[, a]) tuple or a dict."""
    update_item(f, "TXCO2000", index, {"color": to_color(color)})


# ---------------------------------------------------------------------------
# imports of `gph export` output
def import_json(f: GphFile, section: str, items: list[dict] | str) -> int:
    """Apply a ``<SECTION>.json`` export (list of ``{"index": n, ...fields}``) to ``f``."""
    if isinstance(items, str):
        with open(items, encoding="utf-8") as fh:
            items = json.load(fh)
    n = 0
    for rec in items:
        if "index" not in rec:
            raise ValueError(f"{section}: record without 'index': {str(rec)[:60]}")
        update_item(f, section, int(rec["index"]), rec)
        n += 1
    return n


def import_dir(f: GphFile, path: str) -> dict[str, int]:
    """Apply every ``<SECTION>.json`` and ``TEXT2006.txt`` found in an export folder."""
    done = {}
    for fn in sorted(os.listdir(path)):
        full = os.path.join(path, fn)
        if fn == "TEXT2006.txt":
            done[fn] = import_texts(f, full)
        elif fn.endswith(".json") and len(fn) == 13 and fn[:4].isalpha() and fn[4:8].isdigit():
            done[fn] = import_json(f, fn[:8], full)
    return done


# ---------------------------------------------------------------------------
# command line style values: "12", "0x1f", "GEN:12", "#ff8800", '[1, 2]', "text"
def parse_value(text: str):
    t = text.strip()
    if t.lower() in ("none", "null", "-"):
        return None
    if re.fullmatch(r"[+-]?(0x[0-9a-fA-F]+|\d+)", t):
        return int(t, 0)
    if re.fullmatch(r"(GEN|LRG|FNT|\$\$\$\$)\s*:\s*(\d+|0x[0-9a-fA-F]+|-)", t, re.I):
        return to_link(t.upper() if not t.startswith("$") else t)
    if re.fullmatch(r"#[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", t):
        return to_color(t)
    if t[:1] in "[{":
        return json.loads(t)
    return text


# ---------------------------------------------------------------------------
# output
def write_package(f: GphFile, out_path: str | os.PathLike, version: str | None = None,
                  inf: bool | None = None) -> str | None:
    """Write ``f`` to ``out_path``; ``.bz2`` paths are compressed and get an ``.inf`` side-car.

    ``version`` is the ``VER`` field (``yy.mm.dd.HH.MM``; default: the existing one or now).
    ``inf=None`` writes the side-car only for compressed output.  Returns the ``.inf`` path.
    """
    out_path = os.fspath(out_path)
    os.makedirs(os.path.dirname(os.path.abspath(out_path)), exist_ok=True)
    compressed = out_path.lower().endswith(".bz2")
    f.save(out_path, compress=compressed)
    if inf is None:
        inf = compressed
    if not inf:
        return None
    # keep the VER/… fields of the side-car the source file came with, when there is one
    src_inf = (f.source_path or "") + ".inf"
    if f.source_path and os.path.exists(src_inf) and not os.path.exists(out_path + ".inf"):
        template = InfFile.load(src_inf)
        if version:
            template.fields["VER"] = version
        template.update(open(out_path, "rb").read())
        template.save(out_path + ".inf")
    else:
        write_inf_for(out_path, version=version)
    return out_path + ".inf"


# ---------------------------------------------------------------------------
# textures and images (TXUR2008 / IMAG2000 in GEN_HAxx / LRG_HAxx)
def load_frames(paths: Iterable[str]) -> list:
    """Open image files; animated GIF/APNG/WebP files contribute all their frames."""
    from PIL import Image, ImageSequence
    frames = []
    for p in paths:
        with Image.open(p) as im:
            if getattr(im, "n_frames", 1) > 1:
                frames += [fr.convert("RGBA") for fr in ImageSequence.Iterator(im)]
            else:
                frames.append(im.convert("RGBA"))
    return frames


def texture_users(f: GphFile) -> dict[int, list[int]]:
    """texture slot -> IMAG2000 items whose pictures use it."""
    users: dict[int, list[int]] = {}
    sec = f.section("IMAG2000")
    for idx, raw in (sec.used() if sec else []):
        for pic in decode_item(sec.name, idx, raw).fields.get("pictures") or []:
            if pic:
                users.setdefault(pic["texture"], []).append(idx)
    return users


def set_texture(f: GphFile, index: int, image, fmt=None, flags: int | None = None,
                fix_pictures: bool = True) -> dict:
    """Replace texture ``index`` with ``image`` (Pillow image or path).

    ``fmt`` is ``"BMP8.4"``, ``"DXT5"`` ... (default: the slot's current format, else BMP8.4);
    ``flags`` defaults to the slot's current flags.  With ``fix_pictures`` every IMAG2000
    picture record that uses the slot is updated to the image size.  Returns the new fields.
    """
    from PIL import Image
    from .textures import DEFAULT_FLAGS, encode_texture
    if isinstance(image, (str, os.PathLike)):
        image = Image.open(image).convert("RGBA")
    old = get_fields(f, "TXUR2008", index)
    if fmt is None:
        fmt = old["internal_format"] if old else 0x8B96
    if flags is None:
        flags = old["flags"] if old else DEFAULT_FLAGS
    fields = encode_texture(image, fmt, flags)
    set_item(f, "TXUR2008", index, encode_item("TXUR2008", fields))
    if fix_pictures:
        for imag_index in texture_users(f).get(index, []):
            pics = get_fields(f, "IMAG2000", imag_index)["pictures"]
            for pic in pics:
                if pic and pic["texture"] == index:
                    pic["width_minus_1"] = image.width - 1
                    pic["height_minus_1"] = image.height - 1
            update_item(f, "IMAG2000", imag_index, {"pictures": pics})
    return fields


def set_image(f: GphFile, index: int, frames, fmt=None, display: tuple[int, int] | None = None,
              share: bool = False, flags: int | None = None) -> dict:
    """Replace the picture(s) of IMAG2000 item ``index`` with ``frames`` (Pillow images or paths).

    Each frame goes into the texture slot the corresponding picture used, unless that slot is
    also used by another image (then a new slot is appended, so the other image keeps its
    look; ``share=True`` overwrites anyway).  Extra frames get new slots; surplus pictures are
    dropped.  ``fmt`` defaults to the format of the texture being replaced.  The display size
    (IMAG width/height) follows the frame size when it equalled the old picture size, else it
    is kept (the unit stretches the texture to it); ``display`` overrides.
    Returns ``{"slots": [...], "new_slots": [...], "display": (w, h)}``.
    """
    from PIL import Image
    if isinstance(frames, (str, os.PathLike, Image.Image)):
        frames = [frames]
    frames = [Image.open(x).convert("RGBA") if isinstance(x, (str, os.PathLike)) else x.convert("RGBA") for x in frames]
    if not frames:
        raise ValueError("no frames")
    imag = get_fields(f, "IMAG2000", index) or {"flag": 0, "time": 0, "width": frames[0].width,
                                                 "height": frames[0].height, "h_stretch": 0, "v_stretch": 0,
                                                 "pictures": []}
    old_pics = [p for p in (imag.get("pictures") or [])]
    users = texture_users(f)
    tex = get_section(f, "TXUR2008", create=True)
    slots, new_slots, pics = [], [], []
    for i, frame in enumerate(frames):
        old = old_pics[i] if i < len(old_pics) else None
        slot = old["texture"] if old else None
        if slot is not None and not share and any(u != index for u in users.get(slot, [])):
            slot = None
        if slot is None:
            slot = len(tex.items)
            tex.items.append(None)
            new_slots.append(slot)
        old_tex = get_fields(f, "TXUR2008", old["texture"]) if old else None
        use_fmt = fmt if fmt is not None else (old_tex["internal_format"] if old_tex else 0x8B96)
        use_flags = flags if flags is not None else (old_tex["flags"] if old_tex else None)
        set_texture(f, slot, frame, use_fmt, use_flags, fix_pictures=False)
        slots.append(slot)
        pics.append({"width_minus_1": frame.width - 1, "height_minus_1": frame.height - 1, "texture": slot})
    if display is None:
        first = old_pics[0] if old_pics and old_pics[0] else None
        if first is None or (imag["width"], imag["height"]) == (first["width_minus_1"] + 1, first["height_minus_1"] + 1):
            display = frames[0].size
        else:
            display = (imag["width"], imag["height"])
    changes = {"pictures": pics, "width": display[0], "height": display[1]}
    for k in ("flag", "time", "h_stretch", "v_stretch"):
        changes.setdefault(k, imag.get(k, 0))
    update_item(f, "IMAG2000", index, changes)
    return {"slots": slots, "new_slots": new_slots, "display": tuple(display)}
