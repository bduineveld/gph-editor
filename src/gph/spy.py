"""Screenshots from the unit's own "spy" dumps.

When the unit collects its spies (docs/FORMAT.md §9) the graphics driver adds the picture that is
on the display at that moment as spy 21403.  A spy file is a plain gzip stream of what the module
wrote; for 21403 that is an uncompressed 32-bit TGA: an 18-byte header (type 2, width, height,
32 bits per pixel, descriptor 0x2F = top-left origin) followed by width x height BGRA pixels.
"""

from __future__ import annotations

import gzip
import os
import struct
import zlib

from PIL import Image

SPY_ID = 21403                        # the graphics driver's spy
MAX_SIDE = 4096


class SpyError(ValueError):
    pass


def unpack(data: bytes) -> bytes:
    """The content of a spy file: gunzip when it is a gzip stream (a cut-off stream gives what is there)."""
    if data[:2] != b"\x1f\x8b":
        return data
    try:
        return gzip.decompress(data)
    except (OSError, EOFError, zlib.error):
        d = zlib.decompressobj(31)
        try:
            return d.decompress(data)
        except zlib.error as e:
            raise SpyError(f"not a usable gzip stream: {e}") from None


def _header_at(data: bytes, pos: int) -> tuple[int, int] | None:
    """Width and height when an uncompressed 32-bit TGA header starts at ``pos``."""
    if len(data) - pos < 18 or data[pos:pos + 12] != b"\0\0\2" + b"\0" * 9:
        return None
    w, h, bpp, desc = struct.unpack_from("<HHBB", data, pos + 12)
    if bpp != 32 or desc & 0xC0 or not (0 < w <= MAX_SIDE and 0 < h <= MAX_SIDE):
        return None
    return w, h


def screenshots(data: bytes) -> list[Image.Image]:
    """Every picture found in a spy file (normally one), as RGB images."""
    data = unpack(data)
    out = []
    pos = 0
    while True:
        pos = data.find(b"\0\0\2\0\0\0\0\0\0\0\0\0", pos)
        if pos < 0:
            break
        size = _header_at(data, pos)
        if not size:
            pos += 1
            continue
        w, h = size
        need = w * h * 4
        pixels = data[pos + 18:pos + 18 + need]
        if len(pixels) < need:                       # cut off: keep the complete lines
            h = len(pixels) // (w * 4)
            if not h:
                break
            pixels = pixels[:w * h * 4]
        top_down = bool(data[pos + 17] & 0x20)
        img = Image.frombuffer("RGBA", (w, h), pixels, "raw", "BGRA", 0, 1 if top_down else -1)
        out.append(img.convert("RGB"))               # the unit writes alpha 255 everywhere
        pos += 18 + len(pixels)
    return out


def spy_files(path: str) -> list[str]:
    """The spy files under ``path`` (a file, a ``21403`` folder or a folder holding one)."""
    if os.path.isfile(path):
        return [path]
    found = []
    for dp, dirs, files in os.walk(path):
        dirs.sort()
        for f in sorted(files):
            if f.startswith("._"):
                continue
            low = f.lower()
            if low.endswith((".bin.gz", ".bin", ".tga")) and (str(SPY_ID) in dp.split(os.sep) or dp == path):
                found.append(os.path.join(dp, f))
    return found


def convert(path: str, out_dir: str | None = None) -> list[str]:
    """Write the pictures of the spy files under ``path`` as PNG; returns the files written."""
    written = []
    for src in spy_files(path):
        with open(src, "rb") as fh:
            data = fh.read()
        try:
            shots = screenshots(data)
        except SpyError:
            continue
        base = os.path.basename(src)
        for ext in (".gz", ".bin", ".tga"):
            if base.lower().endswith(ext):
                base = base[:-len(ext)]
        folder = out_dir or os.path.dirname(src)
        os.makedirs(folder, exist_ok=True)
        for n, img in enumerate(shots):
            dst = os.path.join(folder, base + (f"_{n + 1}" if len(shots) > 1 else "") + ".png")
            img.save(dst)
            written.append(dst)
    return written
