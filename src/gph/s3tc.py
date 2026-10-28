"""Pure Python S3TC (DXT1 / DXT1A / DXT3 / DXT5) block compressor.

Good enough for skin graphics: principal-axis range fit followed by a few rounds of
least-squares endpoint refinement (the approach of stb_dxt / libsquish's range fit), an
exact single-colour path, and both DXT5 alpha modes.  Block and bit layouts mirror the
decoder in :mod:`gph.textures`.

Speed: roughly 20-40k blocks per second, i.e. a full 800x480 screen takes a few seconds.
"""

from __future__ import annotations

import struct
from typing import Sequence

RGBA = tuple[int, int, int, int]


# ---------------------------------------------------------------------------
# 565 helpers
def _expand5(v: int) -> int: return (v << 3) | (v >> 2)
def _expand6(v: int) -> int: return (v << 2) | (v >> 4)


_EXP5 = [_expand5(i) for i in range(32)]
_EXP6 = [_expand6(i) for i in range(64)]
_Q5 = [min(31, max(0, (v * 31 + 127) // 255)) for v in range(256)]
_Q6 = [min(63, max(0, (v * 63 + 127) // 255)) for v in range(256)]


def _quant565(r: float, g: float, b: float) -> int:
    r = _Q5[min(255, max(0, int(r + 0.5)))]
    g = _Q6[min(255, max(0, int(g + 0.5)))]
    b = _Q5[min(255, max(0, int(b + 0.5)))]
    return (r << 11) | (g << 5) | b


def _unpack565(c: int) -> tuple[int, int, int]:
    return _EXP5[c >> 11], _EXP6[(c >> 5) & 63], _EXP5[c & 31]


def _best_pair_table(expand, quant, nbits):
    """For every 8 bit target the (hi, lo) pair whose 2/3·hi + 1/3·lo is closest (used for flat blocks)."""
    n = 1 << nbits
    out = []
    for target in range(256):
        best = None
        for hi in range(n):
            ehi = expand[hi]
            lo = quant[min(255, max(0, 3 * target - 2 * ehi))]
            for cand in (lo - 1, lo, lo + 1):
                if 0 <= cand < n:
                    v = (2 * ehi + expand[cand]) // 3
                    err = abs(v - target) * 4 + abs(ehi - target) // 4  # prefer hi close to target too
                    if best is None or err < best[0]:
                        best = (err, hi, cand)
        out.append((best[1], best[2]))
    return out


_PAIR5 = _best_pair_table(_EXP5, _Q5, 5)
_PAIR6 = _best_pair_table(_EXP6, _Q6, 6)


# ---------------------------------------------------------------------------
def _palette(c0: int, c1: int, four: bool):
    p0 = _unpack565(c0)
    p1 = _unpack565(c1)
    if four:
        return [p0, p1,
                tuple((2 * a + b) // 3 for a, b in zip(p0, p1)),
                tuple((a + 2 * b) // 3 for a, b in zip(p0, p1))]
    return [p0, p1, tuple((a + b) // 2 for a, b in zip(p0, p1))]


def _assign(pts, pal):
    """Nearest palette index per point and the total squared error."""
    idx = []
    err = 0
    for r, g, b in pts:
        best = 0
        bd = 1 << 30
        for i, (pr, pg, pb) in enumerate(pal):
            d = (r - pr) ** 2 + (g - pg) ** 2 + (b - pb) ** 2
            if d < bd:
                bd = d
                best = i
        idx.append(best)
        err += bd
    return idx, err


def _principal_axis(pts):
    n = len(pts)
    mr = sum(p[0] for p in pts) / n
    mg = sum(p[1] for p in pts) / n
    mb = sum(p[2] for p in pts) / n
    crr = crg = crb = cgg = cgb = cbb = 0.0
    for r, g, b in pts:
        dr, dg, db = r - mr, g - mg, b - mb
        crr += dr * dr; crg += dr * dg; crb += dr * db
        cgg += dg * dg; cgb += dg * db; cbb += db * db
    best = None
    for start in ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0)):
        # power iteration from each axis: a single start can be orthogonal to the main axis
        vr, vg, vb = start
        m = 0.0
        for _ in range(8):
            nr = crr * vr + crg * vg + crb * vb
            ng = crg * vr + cgg * vg + cgb * vb
            nb = crb * vr + cgb * vg + cbb * vb
            m = max(abs(nr), abs(ng), abs(nb))
            if m < 1e-9:
                break
            vr, vg, vb = nr / m, ng / m, nb / m
        if best is None or m > best[0]:
            best = (m, (vr, vg, vb))
    return best[1] if best[0] >= 1e-9 else (1.0, 1.0, 1.0)


def _refine(pts, idx, weights):
    """Least squares endpoints given the index of every point; ``weights[i]`` = share of c0."""
    a2 = ab = b2 = 0.0
    sr0 = sg0 = sb0 = sr1 = sg1 = sb1 = 0.0
    for (r, g, b), i in zip(pts, idx):
        w0 = weights[i]
        w1 = 1.0 - w0
        a2 += w0 * w0; ab += w0 * w1; b2 += w1 * w1
        sr0 += w0 * r; sg0 += w0 * g; sb0 += w0 * b
        sr1 += w1 * r; sg1 += w1 * g; sb1 += w1 * b
    det = a2 * b2 - ab * ab
    if abs(det) < 1e-9:
        return None
    ia2, iab, ib2 = b2 / det, -ab / det, a2 / det
    c0 = (ia2 * sr0 + iab * sr1, ia2 * sg0 + iab * sg1, ia2 * sb0 + iab * sb1)
    c1 = (iab * sr0 + ib2 * sr1, iab * sg0 + ib2 * sg1, iab * sb0 + ib2 * sb1)
    return _quant565(*c0), _quant565(*c1)


_W4 = (1.0, 0.0, 2 / 3, 1 / 3)
_W3 = (1.0, 0.0, 0.5)


def fit_block_colors(pts: Sequence[tuple[int, int, int]], four: bool) -> tuple[int, int, list[int]]:
    """Fit ``pts`` (the opaque pixels of a block) with two 565 endpoints.

    Returns ``(c0, c1, indices)`` with ``c0 > c1`` for 4-colour mode (or c0 == c1 with all
    indices 0) and ``c0 <= c1`` for 3-colour mode.
    """
    if not pts:
        return 0, 0, []
    first = pts[0]
    if all(p == first for p in pts):
        r, g, b = first
        if four:
            (rh, rl), (gh, gl), (bh, bl) = _PAIR5[r], _PAIR6[g], _PAIR5[b]
            c0 = (rh << 11) | (gh << 5) | bh
            c1 = (rl << 11) | (gl << 5) | bl
            if c0 == c1:
                return c0, c1, [0] * len(pts)
            if c0 > c1:
                return c0, c1, [2] * len(pts)
            return c1, c0, [3] * len(pts)
        c = _quant565(r, g, b)
        return c, c, [0] * len(pts)

    axis = _principal_axis(pts)
    proj = [p[0] * axis[0] + p[1] * axis[1] + p[2] * axis[2] for p in pts]
    lo = pts[proj.index(min(proj))]
    hi = pts[proj.index(max(proj))]
    if lo == hi:  # degenerate projection: take the most distant pair instead
        hi, lo = max(((a, b) for a in pts for b in pts),
                     key=lambda ab: sum((x - y) ** 2 for x, y in zip(ab[0], ab[1])))
    c0, c1 = _quant565(*hi), _quant565(*lo)
    weights = _W4 if four else _W3
    best = None
    seen = set()
    for _ in range(6):
        if (c0, c1) in seen:
            break
        seen.add((c0, c1))
        pal = _palette(c0, c1, four)
        idx, err = _assign(pts, pal)
        if best is None or err < best[0]:
            best = (err, c0, c1, idx)
            if err == 0:
                break
        refined = _refine(pts, idx, weights)
        if refined is None:
            break
        c0, c1 = refined
    err, c0, c1, idx = best
    # mode bookkeeping
    if four:
        if c0 < c1:
            c0, c1 = c1, c0
            idx = [i ^ 1 for i in idx]          # 0<->1, 2<->3
        elif c0 == c1:
            idx = [0] * len(idx)
    else:
        if c0 > c1:
            c0, c1 = c1, c0
            idx = [1 if i == 0 else 0 if i == 1 else i for i in idx]
    return c0, c1, idx


def encode_color_block(pixels: Sequence[RGBA], one_bit_alpha: bool) -> bytes:
    """8 byte DXT1 colour block for 16 RGBA pixels (row-major 4x4).

    With ``one_bit_alpha`` pixels whose alpha is below 128 become transparent (3-colour mode).
    """
    if one_bit_alpha and any(p[3] < 128 for p in pixels):
        opaque = [i for i, p in enumerate(pixels) if p[3] >= 128]
        pts = [pixels[i][:3] for i in opaque]
        c0, c1, sub = fit_block_colors(pts, four=False)
        idx = [3] * 16
        for i, k in zip(opaque, sub):
            idx[i] = k
    else:
        c0, c1, idx = fit_block_colors([p[:3] for p in pixels], four=True)
    bits = 0
    for i, k in enumerate(idx):
        bits |= k << (2 * i)
    return struct.pack("<HHI", c0, c1, bits)


def encode_dxt3_alpha(pixels: Sequence[RGBA]) -> bytes:
    bits = 0
    for i, p in enumerate(pixels):
        bits |= ((p[3] + 8) // 17) << (4 * i)
    return struct.pack("<Q", bits)


def _alpha_palette(a0: int, a1: int) -> list[int]:
    if a0 > a1:
        return [a0, a1] + [((7 - i) * a0 + i * a1) // 7 for i in range(1, 7)]
    return [a0, a1] + [((5 - i) * a0 + i * a1) // 5 for i in range(1, 5)] + [0, 255]


def _alpha_fit(alphas, a0, a1):
    pal = _alpha_palette(a0, a1)
    idx = []
    err = 0
    for a in alphas:
        best = min(range(8), key=lambda i: abs(pal[i] - a))
        idx.append(best)
        err += (pal[best] - a) ** 2
    return err, idx


def encode_dxt5_alpha(pixels: Sequence[RGBA]) -> bytes:
    alphas = [p[3] for p in pixels]
    amin, amax = min(alphas), max(alphas)
    if amin == amax:
        return bytes([amax, amax]) + b"\x00" * 6
    # 8 level mode (a0 > a1), with least squares refinement
    a0, a1 = amax, amin
    best = None
    seen = set()
    for _ in range(4):
        if (a0, a1) in seen or a0 <= a1:
            break
        seen.add((a0, a1))
        err, idx = _alpha_fit(alphas, a0, a1)
        if best is None or err < best[0]:
            best = (err, a0, a1, idx)
        w = [1.0, 0.0] + [(7 - i) / 7 for i in range(1, 7)]
        a2 = ab = b2 = s0 = s1 = 0.0
        for a, i in zip(alphas, idx):
            w0 = w[i]; w1 = 1 - w0
            a2 += w0 * w0; ab += w0 * w1; b2 += w1 * w1; s0 += w0 * a; s1 += w1 * a
        det = a2 * b2 - ab * ab
        if abs(det) < 1e-9:
            break
        n0 = (b2 * s0 - ab * s1) / det
        n1 = (a2 * s1 - ab * s0) / det
        a0 = min(255, max(0, int(n0 + 0.5)))
        a1 = min(255, max(0, int(n1 + 0.5)))
    # 6 level mode (a0 <= a1) with explicit 0 and 255
    inner = [a for a in alphas if 0 < a < 255]
    if inner:
        b0, b1 = min(inner), max(inner)
        err6, idx6 = _alpha_fit(alphas, b0, b1)
        if err6 < best[0]:
            best = (err6, b0, b1, idx6)
    _err, a0, a1, idx = best
    bits = 0
    for i, k in enumerate(idx):
        bits |= k << (3 * i)
    return bytes([a0, a1]) + bits.to_bytes(6, "little")


def encode_block(pixels: Sequence[RGBA], internal_format: int) -> bytes:
    if internal_format == 0x83F0:
        return encode_color_block(pixels, False)
    if internal_format == 0x83F1:
        return encode_color_block(pixels, True)
    if internal_format == 0x83F2:
        return encode_dxt3_alpha(pixels) + encode_color_block(pixels, False)
    if internal_format == 0x83F3:
        return encode_dxt5_alpha(pixels) + encode_color_block(pixels, False)
    raise ValueError(f"not an S3TC format: 0x{internal_format:04x}")


def encode_s3tc(rgba: bytes, width: int, height: int, internal_format: int) -> bytes:
    """Compress a raw RGBA buffer (``width``/``height`` multiples of 4) to S3TC blocks."""
    if width % 4 or height % 4:
        raise ValueError("S3TC needs width and height that are multiples of 4")
    out = bytearray()
    stride = width * 4
    for by in range(height // 4):
        rows = [rgba[(by * 4 + j) * stride:(by * 4 + j + 1) * stride] for j in range(4)]
        for bx in range(width // 4):
            o = bx * 16
            px = []
            for j in range(4):
                row = rows[j]
                px.extend((row[o + 4 * i], row[o + 4 * i + 1], row[o + 4 * i + 2], row[o + 4 * i + 3]) for i in range(4))
            out += encode_block(px, internal_format)
    return bytes(out)
