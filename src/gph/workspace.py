"""A workspace is the set of GPH files that reference each other.

Cross references inside items are :class:`gph.decode.Link` objects (module + index).  The
module tells which *file* holds the target, the section depends on what the field means:

===============  ==========  ===============  ====================================
field meaning    module      file             section
===============  ==========  ===============  ====================================
image            ``GEN``     GEN_HAxx         IMAG2000 (which points to TXUR2008)
image            ``LRG``     LRG_HAxx         IMAG2000 (which points to TXUR2008)
colour           ``GEN``     GEN_HAxx         TXCO2000
font             ``FNT``     FNT_common       FONT2007 (which points to BFNT2000)
text index       ``GEN``     GEN_common       TXIN2000 (which lists TEXT2006 indexes)
text             ``GEN``     GEN_<lang>       TEXT2006
===============  ==========  ===============  ====================================
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from .container import GphFile, iter_gph_files
from .decode import DecodedItem, Link, decode_item


@dataclass
class Workspace:
    files: dict[str, GphFile] = field(default_factory=dict)   # keyed by internal name, e.g. "GEN COMM"
    _cache: dict = field(default_factory=dict, repr=False)

    # -- loading ------------------------------------------------------------
    @classmethod
    def from_paths(cls, paths, language: str | None = None, theme: str | None = None) -> "Workspace":
        """Load every GPH file found in ``paths`` (files or directories).

        ``language`` (e.g. ``"DU"``) and ``theme`` (e.g. ``"HA00"``) pick one file when
        several language or theme files are found.
        """
        ws = cls()
        for p in iter_gph_files(paths):
            try:
                f = GphFile.load(p)
            except Exception as e:  # noqa: BLE001 - report and continue
                print(f"skip {p}: {e}")
                continue
            ws.add(f, language=language, theme=theme)
        return ws

    def add(self, f: GphFile, language: str | None = None, theme: str | None = None) -> None:
        key = f.name
        variant = f.variant.strip()
        if f.module == "GEN " and variant not in ("COMM",) and not variant.startswith("HA"):
            # language file
            if language and variant != language.upper():
                return
            key = "GEN LANG"
        if variant.startswith("HA"):
            if theme and variant != theme.upper():
                return
            key = f.module + "THEME"
        if key in self.files:
            return  # keep the first one found
        self.files[key] = f

    # -- file access --------------------------------------------------------
    @property
    def common(self) -> GphFile | None: return self.files.get("GEN COMM")
    @property
    def font(self) -> GphFile | None: return self.files.get("FNT COMM")
    @property
    def language(self) -> GphFile | None: return self.files.get("GEN LANG")
    @property
    def theme_gen(self) -> GphFile | None: return self.files.get("GEN THEME")
    @property
    def theme_lrg(self) -> GphFile | None: return self.files.get("LRG THEME")

    def item(self, f: GphFile | None, section: str, index: int) -> DecodedItem | None:
        if f is None or index is None or index == 0xFFFF:
            return None
        key = (id(f), section, index)
        if key not in self._cache:
            sec = f.section(section)
            if sec is None or index >= len(sec.items) or sec.items[index] is None:
                self._cache[key] = None
            else:
                self._cache[key] = decode_item(sec.name, index, sec.items[index])
        return self._cache[key]

    # -- resolution ---------------------------------------------------------
    def text(self, index: int) -> str | None:
        it = self.item(self.language, "TEXT2006", index)
        return None if it is None else it.fields.get("text")

    def text_index(self, link: Link | None) -> list[str] | None:
        """TXIN2000 -> list of texts (several when unit dependent, e.g. km/h and mph)."""
        if link is None or link.is_null:
            return None
        it = self.item(self.common, "TXIN2000", link.index)
        if it is None:
            return None
        return [self.text(i) or f"<text {i}>" for i in (it.fields.get("texts") or [])]

    def color(self, link: Link | None) -> dict | None:
        if link is None or link.is_null:
            return None
        it = self.item(self.theme_gen, "TXCO2000", link.index)
        return None if it is None else it.fields.get("color")

    def font_scale(self, link: Link | None) -> int | None:
        if link is None or link.is_null:
            return None
        it = self.item(self.font, "FONT2007", link.index)
        return None if it is None else it.fields.get("font_scale")

    def image(self, link: Link | None) -> DecodedItem | None:
        """IMAG2000 item for an image link (module decides GEN_HAxx or LRG_HAxx)."""
        if link is None or link.is_null:
            return None
        f = self.theme_lrg if link.module.startswith("LRG") else self.theme_gen
        return self.item(f, "IMAG2000", link.index)

    def texture(self, module: str, index: int) -> DecodedItem | None:
        f = self.theme_lrg if module.startswith("LRG") else self.theme_gen
        return self.item(f, "TXUR2008", index)

    def describe_link(self, name: str, link: Link | None) -> str:
        """Human readable value of a link field, used by the CLI dump."""
        if link is None or link.is_null:
            return "-"
        n = name.lower()
        if "color" in n:
            c = self.color(link)
            return f"{link} #{c['hex']}" if c else f"{link}"
        if n == "font" or n.endswith("_font"):
            s = self.font_scale(link)
            return f"{link} scale={s}" if s is not None else f"{link}"
        if "text" in n:
            t = self.text_index(link)
            return f"{link} {t!r}" if t else f"{link}"
        if "image" in n or n in ("background", "foreground", "cursor", "arrow", "scrollbar", "scrollbar_bg"):
            im = self.image(link)
            if im:
                pics = im.fields.get("pictures") or []
                tex = ",".join(f"TXUR{p['texture']:04X}" for p in pics if p)
                return f"{link} {im.fields['width']}x{im.fields['height']} [{tex}]"
            return f"{link}"
        return f"{link}"
