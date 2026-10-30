"""Local web UI: a small JSON API over :mod:`gph.edit` plus a static single page front end.

Start it with ``gph serve [FILES/FOLDERS...]``; it binds to 127.0.0.1 only and opens the
browser.  Everything is kept in memory until *Save* writes a package through
:func:`gph.edit.write_package`.  Only the standard library and Pillow are used.

API (all JSON unless noted)::

    GET  /api/files                              open files with their sections
    POST /api/open        {"path": ...}          open a file or every GPH file under a folder
    POST /api/upload      {"name", "data_b64"}   open a file sent by the browser
    POST /api/browse      {"path": ...}          folder listing for the open dialog
    POST /api/files/ID/close|revert|undo
    POST /api/files/ID/save   {"out", "version", "inf"}
    GET  /api/files/ID/items?section=NAME        compact rows per item
    GET  /api/files/ID/item/SECTION/INDEX        full decoded item (+ described links)
    PUT  /api/files/ID/item/SECTION/INDEX        {"changes": {...}} -> gph.edit.update_item
    POST /api/files/ID/text/INDEX                {"text": ...}
    POST /api/files/ID/color/INDEX               {"color": "#rrggbbaa"}
    POST /api/files/ID/image/INDEX               {"frames": [b64...], "format", "display", "share"}
    POST /api/files/ID/texture/INDEX             {"data_b64", "format", "flags"}
    POST /api/preview                            {"data_b64", "format"} -> encoded preview + PSNR
    GET  /api/files/ID/texture/INDEX.png
    GET  /api/files/ID/image/INDEX.png?frame=N[&display=1]
    GET  /api/files/ID/image/INDEX.gif           animation
    GET  /api/files/ID/font/INDEX.png            BFNT glyph sheet
    GET  /api/settings                           theme/language in use, screen size, background
    GET  /api/render.png?font=N&text=..&color=.. text drawn with the unit's bitmap font (scaled)
    POST /api/settings    {"theme": "HA01", "language": "DE"}
    GET  /api/screens                            screens of the unit (needs GraphicBase.RNEG, see gph.screens)
    GET  /api/screens/ID                         one screen with its widgets, decoded and resolved
    POST /api/screens/open   {"path": ...}       load a GraphicBase.RNEG(.gz) file (``/api/open`` does it
                                                 too when it finds one under the folder)
"""

from __future__ import annotations

import base64
import copy
import errno
import io
import json
import math
import mimetypes
import os
import re
import secrets
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
import zipfile
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import parse_qs, urlsplit

from PIL import Image

from . import fonts, textures
from .container import GphError, GphFile, Section, iter_gph_files
from .decode import DecodedItem, Link, decode_item
from .edit import (default_filename, get_fields, load_frames, set_color, set_image, set_text, set_texture,
                   texture_users, update_item, write_package)
from .encode import EncodeError
from .inf import InfFile
from .schema import IMAG_FLAGS, IMAG_STRETCH, SECTION_DESCRIPTIONS, TXCO_DESCRIPTIONS
from .screens import TEXT_LIST_SECTIONS, ScreenDatabase, ScreenError, find_graphic_base, find_mmi_folder
from .workspace import Workspace

MAX_UNDO = 50
SCREEN = (800, 480)   # RT6 display; background 0 of GEN_common is an 800x480 image at (0, 0)

# Public (online) mode: every visitor gets their own Editor, selected by a cookie; nothing is read from
# or written to the server's disk (no open/browse/save, downloads instead), with these limits.
PUBLIC_MAX_FILES = 40           # open files per visitor (the server's preloaded files included)
PUBLIC_MAX_BODY = 64 << 20      # upload size
PUBLIC_SESSION_IDLE = 2 * 3600  # seconds before an idle visitor's files are dropped
PUBLIC_MAX_SESSIONS = 100
COOKIE = "gph_sid"

#: how the UI groups sections
SECTION_GROUP = {"TEXT": "texts", "TXCO": "colors", "IMAG": "images", "TXUR": "textures", "BFNT": "fonts"}


class ApiError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


# ---------------------------------------------------------------------------
@dataclass
class OpenFile:
    id: int
    file: GphFile
    path: str | None
    dirty: bool = False
    history: list = field(default_factory=list)
    shared: bool = False   #: public mode: ``file`` is the server's preloaded copy; copied before the first edit
    name: str | None = None   #: label to show when there is no path (a preloaded file keeps its file name)
    _items: dict = field(default_factory=dict, repr=False)
    _png: dict = field(default_factory=dict, repr=False)

    @property
    def label(self) -> str:
        return os.path.basename(self.path) if self.path else self.name or default_filename(self.file, self.file.compressed)

    def unshare(self) -> None:
        """Give this OpenFile its own copy of the sections (items are immutable bytes, so a list copy is enough)."""
        if self.shared:
            f = copy.copy(self.file)
            f.sections = [Section(s.name, list(s.items)) for s in self.file.sections]
            self.file = f
            self.shared = False

    def item(self, section: str, index: int) -> DecodedItem | None:
        key = (section, index)
        if key not in self._items:
            sec = self.file.section(section)
            if sec is None or index >= len(sec.items) or sec.items[index] is None:
                return None
            self._items[key] = decode_item(sec.name, index, sec.items[index])
        return self._items[key]

    def snapshot(self) -> None:
        self.unshare()
        self.history.append([(s.name, list(s.items)) for s in self.file.sections])
        del self.history[:-MAX_UNDO]

    def undo(self) -> bool:
        if not self.history:
            return False
        state = self.history.pop()
        self.file.sections = [Section(name, items) for name, items in state]
        self.touch()
        return True

    def touch(self) -> None:
        self._items.clear()
        self._png.clear()
        self.dirty = True

    def suggested_out(self) -> str:
        """``<cwd>/out/<parent folder>/<unit file name>``: never the original path."""
        parent = os.path.basename(os.path.dirname(os.path.abspath(self.path))) if self.path else self.file.variant.strip()
        return os.path.join(os.getcwd(), "out", parent, default_filename(self.file, True))

    def info(self) -> dict:
        f = self.file
        sections = []
        for s in f.sections:
            sections.append({"name": s.name, "kind": s.kind, "description": SECTION_DESCRIPTIONS.get(s.kind, ""),
                             "group": SECTION_GROUP.get(s.kind, "other"), "slots": len(s.items), "used": len(s.used())})
        return {"id": self.id, "label": self.label, "path": self.path, "name": f.name, "module": f.module.strip(),
                "variant": f.variant.strip(), "compressed": f.compressed, "stamp": f.stamp.hex(),
                "dirty": self.dirty, "undo": len(self.history), "suggested_out": self.suggested_out(),
                "sections": sections}


class Editor:
    """All state of one server: the open files and a lock around every mutation."""

    def __init__(self, public: bool = False):
        self.files: dict[int, OpenFile] = {}
        self._next = 1
        self.lock = threading.RLock()
        self.public = public               # online version: uploads and downloads only, see PUBLIC_*
        self.theme: str | None = None      # "HA00" ...; None = first theme found
        self.language: str | None = None   # "DE" ...; None = first language found
        self.screens: ScreenDatabase | None = None   # the unit's screen database, when a GraphicBase was opened

    # -- files --------------------------------------------------------------
    def open_path(self, path: str) -> tuple[list[OpenFile], list[OpenFile]]:
        """Open a file or every GPH file under a folder; returns (opened, already open).

        A ``GraphicBase.RNEG(.gz)`` file, given directly or found under the folder, is loaded
        as the screen database (see :attr:`screens`).
        """
        path = os.path.expanduser(path)
        if not os.path.exists(path):
            raise ApiError(f"{path}: not found", 404)
        if os.path.isfile(path) and os.path.basename(path).lower().startswith("graphicbase."):
            self.open_screens(path)
            return [], []
        opened, known = [], []
        gb = find_graphic_base(path) if os.path.isdir(path) else None
        if gb and (self.screens is None or self.screens.path != gb):
            try:
                self.open_screens(gb)
            except ApiError as e:
                print(f"skip {gb}: {e}", file=sys.stderr)
        for p in sorted(iter_gph_files([path]), key=str.lower):
            p = os.path.abspath(p)
            dup = next((of for of in self.files.values() if of.path == p), None)
            if dup is not None:
                known.append(dup)
                continue
            try:
                f = GphFile.load(p)
            except (GphError, OSError) as e:
                if os.path.isdir(path):
                    continue  # skip unreadable files when a folder was given
                raise ApiError(f"{p}: {e}")
            opened.append(self._add(f, p))
        if not opened and not known and not os.path.isdir(path):
            raise ApiError(f"{path}: not a GPH file")
        return opened, known

    def open_screens(self, path: str) -> ScreenDatabase:
        """Load a GraphicBase.RNEG(.gz) and, when the firmware's ``Application/MMI`` folder sits
        next to it, the screen names from the MMI modules."""
        try:
            db = ScreenDatabase.load(path)
        except (ScreenError, OSError, ValueError) as e:
            raise ApiError(f"{path}: {e}")
        mmi = find_mmi_folder(path)
        if mmi:
            db.enrich_from_modules(mmi)
        elif os.path.exists(path + ".names.json"):   # names saved earlier with `gph screens-names` (no firmware needed)
            with open(path + ".names.json", encoding="utf-8") as fh:
                db.apply_names(json.load(fh))
        self.screens = db
        return db

    def open_bytes(self, name: str, data: bytes) -> OpenFile | None:
        """A file sent by the browser.  A ``GraphicBase.RNEG(.gz)`` becomes the screen database
        (returns None); anything else must be a GPH file."""
        import bz2
        if os.path.basename(name).lower().startswith("graphicbase."):
            try:
                self.screens = ScreenDatabase.from_bytes(data, os.path.basename(name))
            except (ScreenError, ValueError) as e:
                raise ApiError(f"{name}: {e}")
            return None
        if self.public and len(self.files) >= PUBLIC_MAX_FILES:
            raise ApiError(f"at most {PUBLIC_MAX_FILES} files can be open at once; close some first")
        if data[:3] == b"BZh":
            data = bz2.decompress(data)
            compressed = True
        else:
            compressed = False
        try:
            f = GphFile.from_bytes(data)
        except GphError as e:
            raise ApiError(f"{name}: {e}")
        f.compressed = compressed
        return self._add(f, None)

    def _add(self, f: GphFile, path: str | None) -> OpenFile:
        of = OpenFile(self._next, f, path)
        self.files[self._next] = of
        self._next += 1
        return of

    def get(self, fid: int) -> OpenFile:
        try:
            return self.files[fid]
        except KeyError:
            raise ApiError(f"no open file {fid}", 404) from None

    def themes(self) -> list[str]:
        return sorted({of.file.variant.strip() for of in self.files.values() if of.file.variant.startswith("HA")})

    def languages(self) -> list[str]:
        return sorted({of.file.variant.strip() for of in self.files.values()
                       if of.file.module == "GEN " and not of.file.variant.startswith("HA") and of.file.variant.strip() != "COMM"})

    def workspace(self) -> Workspace:
        """The open files as a workspace, honouring the chosen theme and language."""
        ws = Workspace()
        theme = self.theme if self.theme in self.themes() else (self.themes() or [None])[0]
        lang = self.language if self.language in self.languages() else (self.languages() or [None])[0]
        for of in self.files.values():
            ws.add(of.file, language=lang, theme=theme)
        return ws

    def settings(self) -> dict:
        ws = self.workspace()
        theme = ws.theme_gen.variant.strip() if ws.theme_gen else (ws.theme_lrg.variant.strip() if ws.theme_lrg else None)
        lang = ws.language.variant.strip() if ws.language else None
        d = {"theme": theme, "language": lang, "themes": self.themes(), "languages": self.languages(),
             "screen": {"width": SCREEN[0], "height": SCREEN[1]}, "background": None,
             "screens": self.screens.summary() if self.screens else None, "public": self.public}
        bg = self._resolve_link(ws, "image", ws.common and self._first_background(ws.common))
        if bg and bg.get("kind") == "image":
            d["background"] = bg
        return d

    def font_blocks(self, ws: Workspace, font_index: int) -> dict[int, dict]:
        """high byte -> BFNT2000 fields for a FONT2007 entry of the font file."""
        it = ws.item(ws.font, "FONT2007", font_index)
        if it is None:
            raise ApiError(f"font {font_index} not found (is FNT_common open?)", 404)
        blocks = {}
        for b in it.fields.get("blocks") or []:
            if b is None:
                continue
            bf = ws.item(ws.font, "BFNT2000", b["bfnt"])
            if bf is not None:
                blocks[b["high_byte"]] = bf.fields
        return blocks

    def render_png(self, font_index: int, text: str, color: str, interline: int = 0) -> bytes:
        ws = self.workspace()
        it = ws.item(ws.font, "FONT2007", font_index)
        if it is None:
            raise ApiError(f"font {font_index} not found (is FNT_common open?)", 404)
        try:
            rgba = tuple(bytes.fromhex(color.lstrip("#").ljust(8, "f")[:8]))
        except ValueError:
            raise ApiError(f"bad colour {color!r}")
        img = fonts.render_text(text, self.font_blocks(ws, font_index), it.fields.get("font_scale", 1000), rgba, interline)
        return _png(img)

    @staticmethod
    def _first_background(common: GphFile) -> Link | None:
        sec = common.section("BKGD2000")
        if sec is None or not sec.items or sec.items[0] is None:
            return None
        return decode_item(sec.name, 0, sec.items[0]).fields.get("image")

    def _of_for(self, f: GphFile | None) -> OpenFile | None:
        return next((of for of in self.files.values() if of.file is f), None)

    def _resolve_link(self, ws: Workspace, name: str, link, module: str | None = None) -> dict | None:
        """Structured target of a link field: what it points at, where, and how to show it."""
        if link is None:
            return None
        if isinstance(link, int):  # bare index with the module given by the caller
            link = Link(module or "GEN ", link)
        if link.is_null:
            return None
        n = name.lower()
        mod = link.module.strip()
        base = {"module": mod, "index": link.index}
        if "color" in n:
            c = ws.color(link)
            of = self._of_for(ws.theme_gen)
            if c is None or of is None:
                return {**base, "kind": "missing", "what": "colour"}
            return {**base, "kind": "color", "file": of.id, "hex": c["hex"], "description": TXCO_DESCRIPTIONS.get(link.index, "")}
        if n == "font" or n.endswith("_font"):
            it = ws.item(ws.font, "FONT2007", link.index)
            of = self._of_for(ws.font)
            if it is None or of is None:
                return {**base, "kind": "missing", "what": "font"}
            r = {**base, "kind": "font", "file": of.id, "scale": it.fields.get("font_scale"), "style": it.fields.get("font_style")}
            blocks = [b for b in (it.fields.get("blocks") or []) if b]
            if blocks:
                bf = ws.item(ws.font, "BFNT2000", blocks[0]["bfnt"])
                if bf is not None:
                    r.update(bfnt=blocks[0]["bfnt"], height=bf.fields["char_height"], url=f"/api/files/{of.id}/font/{blocks[0]['bfnt']}.png")
            return r
        if "text" in n:
            it = ws.item(ws.common, "TXIN2000", link.index)
            of = self._of_for(ws.common)
            if it is None or of is None:
                return {**base, "kind": "missing", "what": "text index"}
            slots = it.fields.get("texts") or []
            lang_of = self._of_for(ws.language)
            return {**base, "kind": "text_index", "file": of.id, "slots": slots, "style": it.fields.get("style"),
                    "unit_dependance": it.fields.get("unit_dependance"), "language_file": lang_of.id if lang_of else None,
                    "texts": [ws.text(i) for i in slots]}
        im = ws.image(link)
        f = ws.theme_lrg if mod.startswith("LRG") else ws.theme_gen
        of = self._of_for(f)
        if im is None or of is None:
            return {**base, "kind": "missing", "what": "image"}
        pics = [p for p in (im.fields.get("pictures") or []) if p]
        return {**base, "kind": "image", "file": of.id, "width": im.fields["width"], "height": im.fields["height"],
                "frames": len(pics), "flag": im.fields["flag"], "time": im.fields["time"],
                "url": f"/api/files/{of.id}/image/{link.index}.png?display=1",
                "gif": f"/api/files/{of.id}/image/{link.index}.gif" if len(pics) > 1 else None}

    def resolve_fields(self, ws: Workspace, section: str, fields: dict) -> dict:
        out = {}
        for k, v in fields.items():
            if isinstance(v, Link):
                out[k] = self._resolve_link(ws, k, v)
            elif isinstance(v, list) and v and all(x is None or isinstance(x, Link) for x in v):
                out[k] = [self._resolve_link(ws, k[:-1] if k.endswith("s") else k, x) for x in v]
            elif section.startswith("TXIN") and k == "texts" and isinstance(v, list):
                lang_of = self._of_for(ws.language)
                out[k] = [{"kind": "text", "index": i, "file": lang_of.id if lang_of else None, "text": ws.text(i)} for i in v]
            elif section.startswith("SEBR") and k == "text_indexes" and isinstance(v, list):
                out[k] = [self._resolve_link(ws, "text_index", i, fields.get("text_module")) for i in v]
        return out

    def revert(self, of: OpenFile) -> None:
        if not of.path:
            raise ApiError("this file was uploaded; there is nothing on disk to revert to")
        of.file = GphFile.load(of.path)
        of.history.clear()
        of.touch()
        of.dirty = False

    # -- rows ---------------------------------------------------------------
    def rows(self, of: OpenFile, section: str) -> list[dict]:
        sec = of.file.section(section)
        if sec is None:
            raise ApiError(f"no section {section}", 404)
        kind = sec.kind
        users = texture_users(of.file) if kind == "TXUR" else {}
        ws = self.workspace() if kind not in ("TEXT", "TXCO", "IMAG", "TXUR", "BFNT") else None
        out = []
        for idx, _raw in sec.used():
            it = of.item(sec.name, idx)
            fl = it.fields
            row: dict[str, Any] = {"index": idx, "size": it.size}
            if kind == "TEXT":
                row["text"] = fl.get("text", "")
            elif kind == "TXCO":
                c = fl.get("color") or {}
                row.update(hex=c.get("hex", "00000000"), description=TXCO_DESCRIPTIONS.get(idx, ""))
            elif kind == "IMAG":
                pics = [p for p in (fl.get("pictures") or [])]
                row.update(width=fl["width"], height=fl["height"], flag=fl["flag"], flag_name=IMAG_FLAGS.get(fl["flag"], "?"),
                           time=fl["time"], h_stretch=fl["h_stretch"], v_stretch=fl["v_stretch"],
                           frames=[None if p is None else {"width": p["width_minus_1"] + 1, "height": p["height_minus_1"] + 1,
                                                           "texture": p["texture"]} for p in pics])
            elif kind == "TXUR":
                row.update(format=textures.texture_type_name(fl["internal_format"]), internal_format=fl["internal_format"],
                           width=fl["width"], height=fl["height"], bytes=fl["size"], flags=fl["flags"],
                           users=users.get(idx, []))
            elif kind == "BFNT":
                row.update(char_height=fl["char_height"], canvas_width=fl["canvas_width"], canvas_height=fl["canvas_height"],
                           unicode_high_byte=fl["unicode_high_byte"],
                           glyphs=sum(1 for c in fl["chars"] if c is not None))
            else:
                row["fields"] = it.to_json()
                row["problems"] = it.problems + it.check_relocation()
                row["pictures"] = self._row_pictures(ws, fl)
            out.append(row)
        return out

    def _row_pictures(self, ws: Workspace, fields: dict, limit: int = 6) -> list[dict]:
        """The images an item links to (first of each list), compact, for the section tables."""
        out = []
        for k, v in fields.items():
            n = k.lower()
            if "color" in n or "text" in n or n == "font" or n.endswith("_font"):
                continue
            link = next((x for x in v if x is not None), None) if isinstance(v, list) else v
            if not isinstance(link, Link) or link.is_null:
                continue
            r = self._resolve_link(ws, k, link)
            if r and r["kind"] == "image":
                out.append({"field": k, "module": r["module"], "index": r["index"], "file": r["file"],
                            "url": r["url"], "width": r["width"], "height": r["height"],
                            "count": len([x for x in v if x is not None]) if isinstance(v, list) else 1})
            if len(out) >= limit:
                break
        return out

    def item_detail(self, of: OpenFile, section: str, index: int) -> dict:
        it = of.item(section, index)
        if it is None:
            raise ApiError(f"{section}[{index}] is empty", 404)
        ws = self.workspace()
        described = {}
        for k, v in it.fields.items():
            if isinstance(v, Link):
                described[k] = ws.describe_link(k, v)
            elif isinstance(v, list) and v and all(x is None or isinstance(x, Link) for x in v):
                described[k] = [ws.describe_link(k[:-1] if k.endswith("s") else k, x) for x in v]
        d = {"section": section, "index": index, "size": it.size, "reloc": it.reloc, "nreloc": it.nreloc,
             "fields": it.to_json(), "described": described, "resolved": self.resolve_fields(ws, section, it.fields),
             "problems": it.problems + it.check_relocation()}
        if section.startswith("TXUR"):
            d["users"] = texture_users(of.file).get(index, [])
            d["format"] = textures.texture_type_name(it.fields["internal_format"])
        if section.startswith("IMAG"):
            d["used_by"] = self.where_used(of, index)
        if self.screens and of.file is ws.common:
            d["screens"] = [{"id": sid, "name": self.screens.screens[sid].name} for sid in self.screens.uses(section, index)]
        return d

    # -- screens of the unit -------------------------------------------------
    def screen_list(self) -> list[dict]:
        if self.screens is None:
            raise ApiError("no screen database open (open a GraphicBase.RNEG.gz of the unit's software)", 404)
        return [sc.to_json(objects=False) for _sid, sc in sorted(self.screens.screens.items())]

    def screen_detail(self, sid: int) -> dict:
        """One screen with every widget decoded (fields + resolved links) so the UI can draw it."""
        if self.screens is None:
            raise ApiError("no screen database open", 404)
        sc = self.screens.screens.get(sid)
        if sc is None:
            raise ApiError(f"no screen {sid}", 404)
        ws = self.workspace()
        of = self._of_for(ws.common)
        objects = []
        for o in sc.objects:
            j = o.to_json()
            if o.section and of is not None:
                it = ws.item(ws.common, o.section, o.index)
                if it is None:
                    j["missing"] = True
                else:
                    j.update(file=of.id, fields=it.to_json(), resolved=self.resolve_fields(ws, o.section, it.fields))
            if o.values and o.section in TEXT_LIST_SECTIONS:
                j["list_texts"] = [(ws.text_index(Link("GEN ", v)) or [None])[0] for v in o.values]
            elif o.values and o.section == "VRPI2000":
                j["list_images"] = [self._resolve_link(ws, "image", Link("GEN ", v)) for v in o.values]
            objects.append(j)
        d = sc.to_json(objects=False)
        d.update(objects=objects, common_file=of.id if of else None)
        return d

    def where_used(self, of: OpenFile, image_index: int) -> list[str]:
        """Widgets in the open files whose image links point at this IMAG item (best effort)."""
        module = of.file.module
        hits = []
        for other in self.files.values():
            for sec in other.file.sections:
                if sec.kind in ("TXUR", "IMAG", "TEXT", "TXCO", "BFNT", "TXIN"):
                    continue
                for idx, _raw in sec.used():
                    it = other.item(sec.name, idx)
                    for k, v in it.fields.items():
                        links = v if isinstance(v, list) else [v]
                        for l in links:
                            if isinstance(l, Link) and not l.is_null and l.module == module and l.index == image_index \
                                    and ("color" not in k and "text" not in k and "font" not in k):
                                hits.append(f"{other.label} {sec.name}[{idx:04X}].{k}")
                                break
                    if len(hits) > 200:
                        return hits
        return hits

    # -- pictures -----------------------------------------------------------
    def texture_png(self, of: OpenFile, index: int) -> bytes:
        key = ("tex", index)
        if key not in of._png:
            it = of.item("TXUR2008", index)
            if it is None:
                raise ApiError(f"texture {index} is empty", 404)
            of._png[key] = _png(textures.texture_image(it.fields))
        return of._png[key]

    def image_frame(self, of: OpenFile, index: int, frame: int) -> Image.Image:
        it = of.item("IMAG2000", index)
        if it is None:
            raise ApiError(f"image {index} is empty", 404)
        pics = [p for p in (it.fields.get("pictures") or []) if p is not None]
        if not pics:
            raise ApiError(f"image {index} has no pictures", 404)
        pic = pics[min(frame, len(pics) - 1)]
        tex = of.item("TXUR2008", pic["texture"])
        if tex is None:
            raise ApiError(f"image {index}: texture {pic['texture']} is empty", 404)
        img = textures.texture_image(tex.fields)
        return img.crop((0, 0, min(img.width, pic["width_minus_1"] + 1), min(img.height, pic["height_minus_1"] + 1)))

    def image_png(self, of: OpenFile, index: int, frame: int, display: bool) -> bytes:
        key = ("img", index, frame, display)
        if key not in of._png:
            img = self.image_frame(of, index, frame)
            if display:
                fl = of.item("IMAG2000", index).fields
                size = (max(1, fl["width"]), max(1, fl["height"]))
                if size != img.size:
                    img = img.resize(size, Image.NEAREST if fl["h_stretch"] == 0 else Image.BILINEAR)
            of._png[key] = _png(img)
        return of._png[key]

    def image_gif(self, of: OpenFile, index: int) -> bytes:
        it = of.item("IMAG2000", index)
        if it is None:
            raise ApiError(f"image {index} is empty", 404)
        n = len([p for p in (it.fields.get("pictures") or []) if p is not None])
        frames = [self.image_frame(of, index, i) for i in range(n)]
        if not frames:
            raise ApiError("no pictures", 404)
        w = max(f.width for f in frames)
        h = max(f.height for f in frames)
        padded = []
        for f in frames:
            c = Image.new("RGBA", (w, h))
            c.paste(f, (0, 0))
            padded.append(c)
        buf = io.BytesIO()
        padded[0].save(buf, "GIF", save_all=True, append_images=padded[1:], loop=0,
                       duration=max(20, it.fields["time"] or 100), disposal=2)
        return buf.getvalue()

    def font_png(self, of: OpenFile, index: int) -> bytes:
        key = ("font", index)
        if key not in of._png:
            it = of.item("BFNT2000", index)
            if it is None:
                raise ApiError(f"font {index} is empty", 404)
            sheet = fonts.font_sheet(it.fields, spacing=2)
            img = Image.new("RGBA", sheet.size, (255, 255, 255, 0))
            img.putalpha(sheet)
            of._png[key] = _png(img)
        return of._png[key]

    # -- edits --------------------------------------------------------------
    def edit(self, of: OpenFile, fn, *a, **kw):
        of.snapshot()
        try:
            r = fn(of.file, *a, **kw)
        except (EncodeError, ValueError, KeyError, IndexError, TypeError) as e:
            of.history.pop()
            raise ApiError(f"{type(e).__name__}: {e}")
        of.touch()
        return r

    def save(self, of: OpenFile, out: str, version: str | None, inf: bool | None) -> dict:
        out = os.path.expanduser(out.strip())
        if not out:
            raise ApiError("no output path")
        if os.path.isdir(out) or out.endswith(("/", os.sep)):
            out = os.path.join(out, default_filename(of.file, True))
        if of.path and os.path.abspath(out) == os.path.abspath(of.path):
            raise ApiError("refusing to overwrite the file that is open; choose another folder")
        try:
            inf_path = write_package(of.file, out, version=version or None, inf=inf)
        except OSError as e:
            raise ApiError(str(e))
        of.dirty = False
        r = {"path": os.path.abspath(out), "size": os.path.getsize(out), "inf": inf_path}
        if inf_path:
            r["inf_fields"] = InfFile.load(inf_path).fields
        return r

    def download(self, of: OpenFile, version: str | None) -> tuple[str, bytes]:
        """The package as a zip holding the ``.gph.bz2`` and its ``.inf`` (the online version writes
        nothing to disk).  Returns (zip file name, zip bytes)."""
        name = default_filename(of.file, True)
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, name)
            inf_path = write_package(of.file, out, version=version or None, inf=True)
            buf = io.BytesIO()
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
                z.write(out, name)
                if inf_path:
                    z.write(inf_path, os.path.basename(inf_path))
        of.dirty = False
        return name.replace(".gph.bz2", "") + ".zip", buf.getvalue()


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _frames_from_b64(items: list[str]) -> list[Image.Image]:
    try:
        return load_frames([io.BytesIO(base64.b64decode(x.split(",", 1)[-1])) for x in items])
    except Exception as e:  # noqa: BLE001 - Pillow raises many kinds
        raise ApiError(f"cannot read image: {e}")


def preview(img: Image.Image, fmt: str) -> dict:
    """Encode ``img`` to ``fmt`` and decode again; returns the PNG (base64) and the PSNR."""
    try:
        fl = textures.encode_texture(img, fmt)
    except ValueError as e:
        raise ApiError(str(e))
    back = textures.texture_image(fl).crop((0, 0, img.width, img.height))
    a, b = img.convert("RGBA").tobytes(), back.tobytes()
    se = sum((x - y) ** 2 for x, y in zip(a, b))
    psnr = 99.0 if se == 0 else 10 * math.log10(255 ** 2 / (se / len(a)))
    return {"png_b64": base64.b64encode(_png(back)).decode(), "psnr": round(psnr, 1), "width": fl["width"],
            "height": fl["height"], "bytes": fl["size"], "format": textures.texture_type_name(fl["internal_format"])}


def browse(path: str) -> dict:
    path = os.path.abspath(os.path.expanduser(path or "~"))
    if not os.path.isdir(path):
        raise ApiError(f"{path}: not a folder", 404)
    dirs, files = [], []
    try:
        names = sorted(os.listdir(path), key=str.lower)
    except OSError as e:
        raise ApiError(str(e), 403)
    for n in names:
        if n.startswith("."):
            continue
        full = os.path.join(path, n)
        if os.path.isdir(full):
            dirs.append(n)
        elif n.lower().endswith((".gph", ".gph.bz2")) or n.lower().startswith("graphicbase."):
            files.append({"name": n, "size": os.path.getsize(full)})
    return {"path": path, "parent": os.path.dirname(path) if os.path.dirname(path) != path else None,
            "dirs": dirs, "files": files}


# ---------------------------------------------------------------------------
STATIC_DIR = os.path.join(os.path.dirname(__file__), "web")


class Handler(BaseHTTPRequestHandler):
    editor_: Editor  # the one editor of a local server (set by make_server); in public mode the template
    public = False
    sessions: dict[str, tuple[Editor, float]] = {}   # public mode: cookie -> (editor, last used)
    sessions_lock = threading.Lock()
    server_version = "gph-editor"

    # -- plumbing -----------------------------------------------------------
    @property
    def editor(self) -> Editor:
        return self._session_editor if self.public else self.editor_

    def _session(self) -> Editor:
        """Public mode: the visitor's own editor, created (with a cookie) on first contact."""
        token = None
        for part in self.headers.get("Cookie", "").split(";"):
            k, _, v = part.strip().partition("=")
            if k == COOKIE and re.fullmatch(r"[A-Za-z0-9_-]{20,64}", v):
                token = v
        now = time.time()
        with self.sessions_lock:
            for t, (_ed, last) in list(self.sessions.items()):
                if now - last > PUBLIC_SESSION_IDLE:
                    del self.sessions[t]
            if token in self.sessions:
                ed = self.sessions[token][0]
            else:
                while len(self.sessions) >= PUBLIC_MAX_SESSIONS:
                    del self.sessions[min(self.sessions, key=lambda t: self.sessions[t][1])]
                token = secrets.token_urlsafe(24)
                ed = Editor(public=True)
                ed.screens = self.editor_.screens   # the unit's screen database, if the server has one
                for tpl in self.editor_.files.values():   # files preloaded on the server (GPH_PRELOAD): shared until edited
                    of = ed._add(tpl.file, None)
                    of.shared, of.name = True, tpl.label
                self._set_cookie = token
            self.sessions[token] = (ed, now)
        return ed

    def log_request(self, code="-", size="-"):  # quieter log: only failed requests
        if not str(code).startswith(("2", "3")):
            sys.stderr.write(f"{self.command} {self.path} -> {code}\n")

    def _send(self, status: int, body: bytes, ctype: str, cache: bool = False, extra: dict | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store" if not cache else "max-age=3600")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        if getattr(self, "_set_cookie", None):
            secure = "; Secure" if self.headers.get("X-Forwarded-Proto", "") == "https" else ""
            self.send_header("Set-Cookie", f"{COOKIE}={self._set_cookie}; Path=/; HttpOnly; SameSite=Lax; Max-Age={PUBLIC_SESSION_IDLE}{secure}")
            self._set_cookie = None
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, obj, status: int = 200) -> None:
        self._send(status, json.dumps(obj).encode(), "application/json; charset=utf-8")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        if n > (PUBLIC_MAX_BODY if self.public else 256 * 1024 * 1024):
            raise ApiError("request too large", 413)
        raw = self.rfile.read(n) if n else b""
        if not raw:
            return {}
        try:
            d = json.loads(raw)
        except ValueError as e:
            raise ApiError(f"bad JSON: {e}")
        if not isinstance(d, dict):
            raise ApiError("JSON object expected")
        return d

    def do_GET(self):  # noqa: N802
        self._dispatch("GET")

    def do_POST(self):  # noqa: N802
        self._dispatch("POST")

    def do_PUT(self):  # noqa: N802
        self._dispatch("PUT")

    def do_HEAD(self):  # noqa: N802
        self._dispatch("GET")

    def _dispatch(self, method: str) -> None:
        url = urlsplit(self.path)
        path = url.path
        query = {k: v[-1] for k, v in parse_qs(url.query).items()}
        try:
            if not path.startswith("/api/"):
                if method != "GET":
                    raise ApiError("not found", 404)
                return self._static(path)
            if self.public:
                self._session_editor = self._session()
            for pattern, m, fn in ROUTES:
                mo = pattern.fullmatch(path)
                if mo and m == method:
                    if self.public and fn in PUBLIC_BLOCKED:
                        raise ApiError("not available in the online version: it never touches the server's disk (upload files, download the result)", 403)
                    return fn(self, query, *mo.groups())
            raise ApiError(f"no route {method} {path}", 404)
        except ApiError as e:
            self._json({"error": str(e)}, e.status)
        except Exception as e:  # noqa: BLE001
            traceback.print_exc()
            self._json({"error": f"{type(e).__name__}: {e}"}, 500)

    def _static(self, path: str) -> None:
        if path in ("/", ""):
            path = "/index.html"
        if path == "/favicon.ico":  # the page carries an inline icon
            return self._send(204, b"", "image/x-icon")
        name = os.path.normpath(path.lstrip("/"))
        if name.startswith("..") or os.path.isabs(name):
            raise ApiError("not found", 404)
        full = os.path.join(STATIC_DIR, name)
        if not os.path.isfile(full):
            raise ApiError("not found", 404)
        ctype = mimetypes.guess_type(full)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype in ("application/javascript", "application/json"):
            ctype += "; charset=utf-8"
        with open(full, "rb") as fh:
            self._send(200, fh.read(), ctype)

    # -- routes -------------------------------------------------------------
    def r_files(self, q):
        with self.editor.lock:
            self._json({"files": [of.info() for of in self.editor.files.values()], "cwd": None if self.public else os.getcwd()})

    def r_settings(self, q):
        with self.editor.lock:
            self._json(self.editor.settings())

    def r_settings_set(self, q):
        d = self._body()
        with self.editor.lock:
            if "theme" in d:
                self.editor.theme = d["theme"] or None
            if "language" in d:
                self.editor.language = d["language"] or None
            self._json(self.editor.settings())

    def r_render(self, q):
        with self.editor.lock:
            png = self.editor.render_png(_idx(q.get("font", "0")), q.get("text", ""), q.get("color", "ffffffff"),
                                         int(q.get("interline", 0)))
            self._send(200, png, "image/png")

    def r_open(self, q):
        d = self._body()
        with self.editor.lock:
            before = self.editor.screens
            opened, known = self.editor.open_path(str(d.get("path", "")))
            self._json({"opened": [of.info() for of in opened], "already_open": [of.info() for of in known],
                        "screens": self.editor.screens.summary() if self.editor.screens is not None and self.editor.screens is not before else None})

    def r_screens(self, q):
        with self.editor.lock:
            self._json({"screens": self.editor.screen_list(), "info": self.editor.screens.summary()})

    def r_screen(self, q, sid):
        with self.editor.lock:
            self._json(self.editor.screen_detail(int(sid)))

    def r_screens_open(self, q):
        d = self._body()
        with self.editor.lock:
            db = self.editor.open_screens(os.path.expanduser(str(d.get("path", ""))))
            self._json({"screens": db.summary()})

    def r_upload(self, q):
        d = self._body()
        try:
            data = base64.b64decode(str(d.get("data_b64", "")).split(",", 1)[-1])
        except ValueError:
            raise ApiError("bad base64")
        with self.editor.lock:
            of = self.editor.open_bytes(str(d.get("name", "upload.gph")), data)
            self._json({"opened": [of.info()] if of else [],
                        "screens": self.editor.screens.summary() if of is None and self.editor.screens else None})

    def r_browse(self, q):
        d = self._body()
        self._json(browse(str(d.get("path", ""))))

    def r_preview(self, q):
        d = self._body()
        frames = _frames_from_b64([str(d.get("data_b64", ""))])
        self._json(preview(frames[0], str(d.get("format", "BMP8.4"))))

    def _of(self, fid: str) -> OpenFile:
        return self.editor.get(int(fid))

    def r_file_action(self, q, fid, action):
        d = self._body()
        with self.editor.lock:
            of = self._of(fid)
            if action == "close":
                del self.editor.files[of.id]
                return self._json({"closed": of.id})
            if action in ("revert", "save") and self.public:
                raise ApiError("not available in the online version: download the file instead", 403)
            if action == "revert":
                self.editor.revert(of)
            elif action == "undo":
                if not of.undo():
                    raise ApiError("nothing to undo")
            elif action == "save":
                r = self.editor.save(of, str(d.get("out", "")), d.get("version"), d.get("inf", True))
                return self._json({"saved": r, "file": of.info()})
            else:
                raise ApiError(f"unknown action {action}", 404)
            self._json({"file": of.info()})

    def r_download(self, q, fid):
        with self.editor.lock:
            of = self._of(fid)
            name, data = self.editor.download(of, q.get("version") or None)
        self._send(200, data, "application/zip", extra={"Content-Disposition": f'attachment; filename="{name}"'})

    def r_items(self, q, fid):
        with self.editor.lock:
            of = self._of(fid)
            section = q.get("section", "")
            self._json({"section": section, "items": self.editor.rows(of, section)})

    def r_item_get(self, q, fid, section, index):
        with self.editor.lock:
            self._json(self.editor.item_detail(self._of(fid), section, _idx(index)))

    def r_item_put(self, q, fid, section, index):
        d = self._body()
        changes = d.get("changes")
        if not isinstance(changes, dict):
            raise ApiError("'changes' object expected")
        with self.editor.lock:
            of = self._of(fid)
            self.editor.edit(of, update_item, section, _idx(index), changes)
            self._json({"item": self.editor.item_detail(of, section, _idx(index)), "file": of.info()})

    def r_text(self, q, fid, index):
        d = self._body()
        if not isinstance(d.get("text"), str):
            raise ApiError("'text' string expected")
        with self.editor.lock:
            of = self._of(fid)
            self.editor.edit(of, set_text, _idx(index), d["text"])
            self._json({"index": _idx(index), "text": get_fields(of.file, "TEXT2006", _idx(index))["text"], "file": of.info()})

    def r_color(self, q, fid, index):
        d = self._body()
        with self.editor.lock:
            of = self._of(fid)
            self.editor.edit(of, set_color, _idx(index), d.get("color"))
            self._json({"index": _idx(index), "hex": get_fields(of.file, "TXCO2000", _idx(index))["color"]["hex"],
                        "file": of.info()})

    def r_set_image(self, q, fid, index):
        d = self._body()
        frames = _frames_from_b64(list(d.get("frames") or []))
        if not frames:
            raise ApiError("no frames")
        display = d.get("display")
        if display == "image":
            display = frames[0].size
        elif display is not None:
            display = (int(display[0]), int(display[1]))
        fmt = d.get("format") or None
        with self.editor.lock:
            of = self._of(fid)
            r = self.editor.edit(of, set_image, _idx(index), frames, fmt, display, bool(d.get("share")),
                                 d.get("flags"))
            r["display"] = list(r["display"])
            self._json({"result": r, "item": self.editor.item_detail(of, "IMAG2000", _idx(index)), "file": of.info()})

    def r_set_texture(self, q, fid, index):
        d = self._body()
        frames = _frames_from_b64([str(d.get("data_b64", ""))])
        with self.editor.lock:
            of = self._of(fid)
            self.editor.edit(of, set_texture, _idx(index), frames[0], d.get("format") or None, d.get("flags"),
                             not d.get("keep_pictures"))
            self._json({"item": self.editor.item_detail(of, "TXUR2008", _idx(index)), "file": of.info()})

    def r_texture_png(self, q, fid, index):
        with self.editor.lock:
            self._send(200, self.editor.texture_png(self._of(fid), _idx(index)), "image/png")

    def r_image_png(self, q, fid, index):
        with self.editor.lock:
            png = self.editor.image_png(self._of(fid), _idx(index), int(q.get("frame", 0)), q.get("display") == "1")
            self._send(200, png, "image/png")

    def r_image_gif(self, q, fid, index):
        with self.editor.lock:
            self._send(200, self.editor.image_gif(self._of(fid), _idx(index)), "image/gif")

    def r_font_png(self, q, fid, index):
        with self.editor.lock:
            self._send(200, self.editor.font_png(self._of(fid), _idx(index)), "image/png")


def _idx(s: str) -> int:
    try:
        return int(s, 0)
    except ValueError:
        raise ApiError(f"bad index {s!r}") from None


ROUTES = [
    (re.compile(r"/api/files"), "GET", Handler.r_files),
    (re.compile(r"/api/settings"), "GET", Handler.r_settings),
    (re.compile(r"/api/settings"), "POST", Handler.r_settings_set),
    (re.compile(r"/api/render\.png"), "GET", Handler.r_render),
    (re.compile(r"/api/open"), "POST", Handler.r_open),
    (re.compile(r"/api/upload"), "POST", Handler.r_upload),
    (re.compile(r"/api/browse"), "POST", Handler.r_browse),
    (re.compile(r"/api/preview"), "POST", Handler.r_preview),
    (re.compile(r"/api/screens"), "GET", Handler.r_screens),
    (re.compile(r"/api/screens/(\d+)"), "GET", Handler.r_screen),
    (re.compile(r"/api/screens/open"), "POST", Handler.r_screens_open),
    (re.compile(r"/api/files/(\d+)/(close|revert|undo|save)"), "POST", Handler.r_file_action),
    (re.compile(r"/api/files/(\d+)/download\.zip"), "GET", Handler.r_download),
    (re.compile(r"/api/files/(\d+)/items"), "GET", Handler.r_items),
    (re.compile(r"/api/files/(\d+)/item/([A-Z$]{4}\d{4})/(\w+)"), "GET", Handler.r_item_get),
    (re.compile(r"/api/files/(\d+)/item/([A-Z$]{4}\d{4})/(\w+)"), "PUT", Handler.r_item_put),
    (re.compile(r"/api/files/(\d+)/text/(\w+)"), "POST", Handler.r_text),
    (re.compile(r"/api/files/(\d+)/color/(\w+)"), "POST", Handler.r_color),
    (re.compile(r"/api/files/(\d+)/image/(\w+)"), "POST", Handler.r_set_image),
    (re.compile(r"/api/files/(\d+)/texture/(\w+)"), "POST", Handler.r_set_texture),
    (re.compile(r"/api/files/(\d+)/texture/(\w+)\.png"), "GET", Handler.r_texture_png),
    (re.compile(r"/api/files/(\d+)/image/(\w+)\.png"), "GET", Handler.r_image_png),
    (re.compile(r"/api/files/(\d+)/image/(\w+)\.gif"), "GET", Handler.r_image_gif),
    (re.compile(r"/api/files/(\d+)/font/(\w+)\.png"), "GET", Handler.r_font_png),
]


# ---------------------------------------------------------------------------
#: routes that touch the server's disk: refused in public mode
PUBLIC_BLOCKED = {Handler.r_open, Handler.r_browse, Handler.r_screens_open}


def make_server(paths=(), host: str = "127.0.0.1", port: int = 0, screens: str | None = None,
                public: bool = False) -> ThreadingHTTPServer:
    """Create (but do not run) the server with the given files opened; ``port=0`` picks a free one.

    ``screens`` is a GraphicBase.RNEG(.gz) file (the unit's screen database) to load as well.
    ``public`` is the online mode: one editor per visitor (cookie), no access to the server's
    disk, downloads instead of saves.
    """
    editor = Editor(public=public)
    for p in paths:
        try:
            editor.open_path(p)
        except ApiError as e:
            print(f"skip {p}: {e}", file=sys.stderr)
    if screens:
        try:
            editor.open_screens(os.path.expanduser(screens))
        except ApiError as e:
            print(f"skip {screens}: {e}", file=sys.stderr)
    handler = type("GphHandler", (Handler,), {"editor_": editor, "public": public, "sessions": {}, "sessions_lock": threading.Lock()})
    try:
        srv = ThreadingHTTPServer((host, port), handler)
    except OSError as e:
        if port == 0 or e.errno not in (errno.EADDRINUSE, errno.EACCES):
            raise
        print(f"port {port} is already in use, picking a free one (use -p PORT to choose)", file=sys.stderr)
        srv = ThreadingHTTPServer((host, 0), handler)
    srv.daemon_threads = True
    srv.editor = editor  # type: ignore[attr-defined]
    return srv


def serve(paths=(), host: str = "127.0.0.1", port: int = 8765, open_browser: bool = True, screens: str | None = None,
          public: bool = False) -> None:
    srv = make_server(paths, host, port, screens, public)
    url = f"http://{host}:{srv.server_address[1]}/"
    ed = srv.editor  # type: ignore[attr-defined]
    n = len(ed.files)
    scr = f", {len(ed.screens.screens)} screens" if ed.screens else ""
    mode = "public mode: uploads/downloads only, one session per visitor" if public else "Ctrl-C stops it"
    print(f"gph editor at {url}  ({n} file{'s' if n != 1 else ''} open{scr})  {mode}", flush=True)
    if open_browser:
        threading.Timer(0.3, webbrowser.open, (url,)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")
    finally:
        srv.server_close()
