"""Command line interface: ``gph info|dump|check|export|roundtrip``."""

from __future__ import annotations

import argparse
import json
import os
import sys

from . import fonts, textures
from .container import GphFile, iter_gph_files
from .decode import Link, decode_item
from .schema import GL_ENUMS, IMAG_FLAGS, IMAG_STRETCH, SECTION_DESCRIPTIONS, TXCO_DESCRIPTIONS
from .workspace import Workspace


# ---------------------------------------------------------------------------
def cmd_info(args):
    for p in iter_gph_files(args.paths):
        try:
            f = GphFile.load(p)
        except Exception as e:  # noqa: BLE001
            print(f"{p}: ERROR {e}")
            continue
        print(f.summary())


def _fmt_value(ws: Workspace | None, name: str, v):
    if isinstance(v, Link):
        return ws.describe_link(name, v) if ws else repr(v)
    if isinstance(v, dict) and "hex" in v and "r" in v:
        return f"#{v['hex']}"
    if isinstance(v, (bytes, bytearray)):
        return f"<{len(v)} bytes>"
    if isinstance(v, list):
        if v and all(isinstance(x, Link) or x is None for x in v):
            return "[" + ", ".join(_fmt_value(ws, name[:-1] if name.endswith("s") else name, x) for x in v) + "]"
        if v and all(isinstance(x, dict) or x is None for x in v):
            shown = [x for x in v if x is not None]
            if len(shown) > 8:
                return f"[{len(shown)} records, e.g. {shown[0]}]"
            return "[" + ", ".join(str(x) for x in shown) + "]"
        if len(v) > 32:
            return f"[{len(v)} values]"
    return repr(v)


def _print_item(ws, sec_name, idx, raw, verbose):
    it = decode_item(sec_name, idx, raw)
    extra = ""
    if sec_name == "TXCO2000":
        extra = "  " + TXCO_DESCRIPTIONS.get(idx, "")
    print(f"  [{idx:04X}] {len(raw)} bytes, reloc={it.reloc} nreloc={it.nreloc}{extra}")
    for k, v in it.fields.items():
        if k.startswith("_") and not verbose:
            continue
        if sec_name == "TXUR2008" and k in ("internal_format", "format", "type"):
            v = f"0x{v:04x} {GL_ENUMS.get(v, '?')}" + (f" ({textures.texture_type_name(v)})" if k == "internal_format" else "")
        elif sec_name == "IMAG2000" and k == "flag":
            v = f"{v} ({IMAG_FLAGS.get(v, '?')})"
        elif sec_name == "IMAG2000" and k in ("h_stretch", "v_stretch"):
            v = f"{v} ({IMAG_STRETCH.get(v, '?')})"
        else:
            v = _fmt_value(ws, k, v)
        print(f"      {k:28} {v}")
    for p in it.problems + it.check_relocation():
        print(f"      !! {p}")


def cmd_dump(args):
    ws = Workspace.from_paths(args.workspace, language=args.language, theme=args.theme) if args.workspace else None
    for p in iter_gph_files(args.paths):
        f = GphFile.load(p)
        print(f"== {p}  ({f.name!r})")
        for sec in f.sections:
            if args.section and sec.name != args.section and sec.kind != args.section:
                continue
            used = sec.used()
            print(f"-- {sec.name}  {SECTION_DESCRIPTIONS.get(sec.kind, '')}  ({len(used)} of {len(sec)} slots used)")
            if args.item is not None:
                if args.item < len(sec.items) and sec.items[args.item] is not None:
                    _print_item(ws, sec.name, args.item, sec.items[args.item], args.verbose)
                continue
            for idx, raw in used[: args.limit] if args.limit else used:
                _print_item(ws, sec.name, idx, raw, args.verbose)
            if args.limit and len(used) > args.limit:
                print(f"  ... {len(used) - args.limit} more")


def cmd_check(args):
    """Decode every item and verify the schema against the relocation header."""
    total = bad = 0
    for p in iter_gph_files(args.paths):
        f = GphFile.load(p)
        assert f.to_bytes() == (open(p, "rb").read() if not f.compressed else __import__("bz2").decompress(open(p, "rb").read())), "round trip"
        problems_by_section: dict[str, list[str]] = {}
        for sec in f.sections:
            for idx, raw in sec.used():
                total += 1
                it = decode_item(sec.name, idx, raw)
                probs = it.problems + it.check_relocation()
                if sec.name == "TXUR2008" and not it.problems:
                    exp = textures.expected_payload_size(it.fields["internal_format"], it.fields["width"], it.fields["height"])
                    if exp is not None and exp != it.fields["size"]:
                        probs.append(f"TXUR2008[{idx}]: payload size {it.fields['size']} != expected {exp}")
                    if it.fields["_header_end"] != 40:
                        probs.append(f"TXUR2008[{idx}]: header end {it.fields['_header_end']}")
                if probs:
                    bad += 1
                    problems_by_section.setdefault(sec.name, []).extend(probs)
        status = "OK " if not problems_by_section else "BAD"
        print(f"{status} {p}")
        for s, probs in problems_by_section.items():
            print(f"     {s}: {len(probs)} problems, e.g. {probs[0]}")
            if args.verbose:
                for pr in probs[:20]:
                    print(f"        {pr}")
    print(f"{total} items checked, {bad} with problems")
    return 1 if bad else 0


def cmd_roundtrip(args):
    rc = 0
    for p in iter_gph_files(args.paths):
        raw = open(p, "rb").read()
        if raw[:3] == b"BZh":
            import bz2
            raw = bz2.decompress(raw)
        f = GphFile.from_bytes(raw, p)
        out = f.to_bytes()
        if out == raw:
            print(f"OK  {p} ({len(raw)} bytes)")
        else:
            rc = 1
            n = next((i for i, (a, b) in enumerate(zip(out, raw)) if a != b), min(len(out), len(raw)))
            print(f"BAD {p}: first difference at 0x{n:x} (out {len(out)} vs in {len(raw)} bytes)")
    return rc


# ---------------------------------------------------------------------------
def cmd_export(args):
    os.makedirs(args.out, exist_ok=True)
    ws = Workspace.from_paths(args.workspace, language=args.language, theme=args.theme) if args.workspace else None
    for p in iter_gph_files(args.paths):
        f = GphFile.load(p)
        base = os.path.join(args.out, f.name.strip().replace(" ", "_").replace("$", "S"))
        os.makedirs(base, exist_ok=True)
        print(f"== {p} -> {base}")
        meta = {"source": p, "name": f.name, "stamp": f.stamp.hex(), "sections": {}}
        for sec in f.sections:
            used = sec.used()
            items = []
            for idx, raw in used:
                it = decode_item(sec.name, idx, raw)
                items.append(it.to_json())
            meta["sections"][sec.name] = {"slots": len(sec), "used": len(used)}
            if items:
                with open(os.path.join(base, f"{sec.name}.json"), "w", encoding="utf-8") as fh:
                    json.dump(items, fh, indent=1, ensure_ascii=False)
            if sec.name == "TEXT2006":
                _export_texts(sec, base)
            elif sec.name == "TXUR2008" and not args.no_images:
                _export_textures(sec, base)
            elif sec.name == "BFNT2000" and not args.no_images:
                _export_fonts(sec, base)
            elif sec.name == "IMAG2000" and ws is not None and not args.no_images:
                _export_images(sec, f, base)
        with open(os.path.join(base, "file.json"), "w", encoding="utf-8") as fh:
            json.dump(meta, fh, indent=1)


def _export_texts(sec, base):
    path = os.path.join(base, "TEXT2006.txt")
    with open(path, "w", encoding="utf-8") as fh:
        for idx, raw in sec.used():
            t = decode_item(sec.name, idx, raw).fields["text"].replace("\r", "|").replace("\n", "|")
            fh.write(f"{idx:04X}\t{t}\n")
    print(f"   texts -> {path}")


def _export_textures(sec, base):
    d = os.path.join(base, "TXUR2008")
    os.makedirs(d, exist_ok=True)
    n = 0
    for idx, raw in sec.used():
        it = decode_item(sec.name, idx, raw)
        try:
            img = textures.texture_image(it.fields)
        except Exception as e:  # noqa: BLE001
            print(f"   TXUR{idx:04X}: {e}")
            continue
        name = textures.texture_type_name(it.fields["internal_format"])
        img.save(os.path.join(d, f"TXUR{idx:04X}_{name}_{img.width}x{img.height}.png"))
        n += 1
    print(f"   {n} textures -> {d}")


def _export_fonts(sec, base):
    d = os.path.join(base, "BFNT2000")
    os.makedirs(d, exist_ok=True)
    for idx, raw in sec.used():
        it = decode_item(sec.name, idx, raw)
        hb = it.fields["unicode_high_byte"]
        fonts.canvas_image(it.fields).save(os.path.join(d, f"BFNT{idx:04X}_U+{hb:02X}xx_canvas.png"))
        fonts.font_sheet(it.fields).save(os.path.join(d, f"BFNT{idx:04X}_U+{hb:02X}xx_sheet.png"))
        glyphs = {f"U+{cp:04X}": rec for cp, rec in fonts.glyph_table(it.fields).items()}
        with open(os.path.join(d, f"BFNT{idx:04X}_U+{hb:02X}xx_glyphs.json"), "w") as fh:
            json.dump({"char_height": it.fields["char_height"], "glyphs": glyphs}, fh, indent=1)
    print(f"   {len(sec.used())} fonts -> {d}")


def _export_images(sec, f, base):
    """IMAG2000: compose each image definition from its texture(s) at display size."""
    from PIL import Image
    tex = f.section("TXUR2008")
    if tex is None:
        return
    d = os.path.join(base, "IMAG2000")
    os.makedirs(d, exist_ok=True)
    cache: dict[int, Image.Image] = {}
    n = 0
    for idx, raw in sec.used():
        it = decode_item(sec.name, idx, raw)
        pics = [p for p in (it.fields.get("pictures") or []) if p]
        if not pics:
            continue
        frames = []
        for p in pics:
            t = p["texture"]
            if t not in cache:
                if t >= len(tex.items) or tex.items[t] is None:
                    continue
                cache[t] = textures.texture_image(decode_item("TXUR2008", t, tex.items[t]).fields)
            src = cache[t].crop((0, 0, p["width_minus_1"] + 1, p["height_minus_1"] + 1))
            w, h = it.fields["width"], it.fields["height"]
            if (w, h) != src.size and w > 0 and h > 0:
                src = src.resize((w, h), Image.NEAREST)
            frames.append(src)
        if not frames:
            continue
        if len(frames) == 1:
            frames[0].save(os.path.join(d, f"IMAG{idx:04X}.png"))
        else:
            frames[0].save(os.path.join(d, f"IMAG{idx:04X}_anim{len(frames)}.gif"), save_all=True,
                           append_images=frames[1:], duration=max(20, it.fields["time"]), loop=0, disposal=2)
        n += 1
    print(f"   {n} images -> {d}")


# ---------------------------------------------------------------------------
def main(argv=None):
    ap = argparse.ArgumentParser(prog="gph", description="Inspect and export Magneti Marelli GPH files")
    sub = ap.add_subparsers(dest="cmd", required=True)

    s = sub.add_parser("info", help="header and section overview")
    s.add_argument("paths", nargs="+")
    s.set_defaults(fn=cmd_info)

    s = sub.add_parser("dump", help="decode items")
    s.add_argument("paths", nargs="+")
    s.add_argument("-s", "--section", help="only this section (TXUR2008 or TXUR)")
    s.add_argument("-i", "--item", type=int, help="only this item index")
    s.add_argument("-n", "--limit", type=int, default=20, help="items per section (0 = all)")
    s.add_argument("-w", "--workspace", nargs="*", help="files/folders with the related GPH files, to resolve links")
    s.add_argument("--language", help="language code to pick, e.g. DU")
    s.add_argument("--theme", help="theme to pick, e.g. HA00")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_dump)

    s = sub.add_parser("check", help="verify schemas against relocation headers")
    s.add_argument("paths", nargs="+")
    s.add_argument("-v", "--verbose", action="store_true")
    s.set_defaults(fn=cmd_check)

    s = sub.add_parser("roundtrip", help="parse and re-serialise; must be byte identical")
    s.add_argument("paths", nargs="+")
    s.set_defaults(fn=cmd_roundtrip)

    s = sub.add_parser("export", help="export JSON, texts, textures and fonts")
    s.add_argument("paths", nargs="+")
    s.add_argument("-o", "--out", required=True)
    s.add_argument("-w", "--workspace", nargs="*")
    s.add_argument("--language")
    s.add_argument("--theme")
    s.add_argument("--no-images", action="store_true")
    s.set_defaults(fn=cmd_export)

    args = ap.parse_args(argv)
    rc = args.fn(args)
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
