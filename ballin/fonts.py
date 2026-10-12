"""Bitmap (BDF) fonts rendered to numpy masks.

Masks are bool arrays shaped (height, width). Fonts are parsed once and cached.
"""
import os

import numpy as np

FONT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "fonts")

_cache = {}


class Glyph:
    __slots__ = ("dwidth", "xoff", "yoff", "bits")

    def __init__(self, dwidth, xoff, yoff, bits):
        self.dwidth = dwidth
        self.xoff = xoff
        self.yoff = yoff
        self.bits = bits


class BdfFont:
    def __init__(self, path):
        self.glyphs = {}
        self.ascent = 0
        self.descent = 0
        with open(path, encoding="latin-1") as f:
            lines = iter(f.read().splitlines())
        for line in lines:
            if line.startswith("FONT_ASCENT"):
                self.ascent = int(line.split()[1])
            elif line.startswith("FONT_DESCENT"):
                self.descent = int(line.split()[1])
            elif line.startswith("STARTCHAR"):
                self._read_glyph(lines)
        self.height = self.ascent + self.descent

    def _read_glyph(self, lines):
        enc = dwidth = bbx = None
        for line in lines:
            parts = line.split()
            if not parts:
                continue
            key = parts[0]
            if key == "ENCODING":
                enc = int(parts[1])
            elif key == "DWIDTH":
                dwidth = int(parts[1])
            elif key == "BBX":
                bbx = [int(p) for p in parts[1:5]]
            elif key == "BITMAP":
                w, h, xoff, yoff = bbx
                bits = np.zeros((h, w), bool)
                for r in range(h):
                    hexrow = next(lines).strip()
                    value = int(hexrow, 16)
                    nbits = len(hexrow) * 4
                    for c in range(w):
                        bits[r, c] = (value >> (nbits - 1 - c)) & 1
                if enc is not None and enc >= 0:
                    self.glyphs[enc] = Glyph(dwidth, xoff, yoff, bits)
                return

    def _glyph(self, ch):
        return self.glyphs.get(ord(ch)) or self.glyphs.get(ord("?"))

    def width(self, text, spacing=0):
        if not text:
            return 0
        return sum(self._glyph(ch).dwidth for ch in text) + spacing * (len(text) - 1)

    def render(self, text, spacing=0):
        """Full-height mask; the baseline is at row self.ascent."""
        mask = np.zeros((self.height, max(1, self.width(text, spacing))), bool)
        pen = 0
        for ch in text:
            g = self._glyph(ch)
            h, w = g.bits.shape
            top = self.ascent - (g.yoff + h)
            x = pen + g.xoff
            y0, y1 = max(0, top), min(self.height, top + h)
            x0, x1 = max(0, x), min(mask.shape[1], x + w)
            if y1 > y0 and x1 > x0:
                mask[y0:y1, x0:x1] |= g.bits[y0 - top:y1 - top, x0 - x:x1 - x]
            pen += g.dwidth + spacing
        return mask


# Characters redrawn for the panel: the 4x6 font's zero has a bar through it, which just
# looks cluttered in scores and standings.
GLYPH_FIXES = {
    ("4x6", "0"): [".#..", "#.#.", "#.#.", "#.#.", ".#..", "...."],
}


def load(name):
    font = _cache.get(name)
    if font is None:
        font = _cache[name] = BdfFont(os.path.join(FONT_DIR, name + ".bdf"))
        for (fname, ch), rows in GLYPH_FIXES.items():
            g = font.glyphs.get(ord(ch))
            if fname == name and g is not None:
                g.bits = np.array([[c == "#" for c in row] for row in rows])
    return font


def crop(mask):
    """Trim empty rows/columns. Returns (mask, x0, y0) where x0/y0 is the trimmed offset."""
    ys, xs = np.nonzero(mask)
    if len(ys) == 0:
        return mask[:0, :0], 0, 0
    y0, y1, x0, x1 = ys.min(), ys.max() + 1, xs.min(), xs.max() + 1
    return mask[y0:y1, x0:x1], int(x0), int(y0)


def text_mask(text, font_name, spacing=0):
    """Rendered text trimmed to its ink. Returns (mask, char_right_edges)."""
    font = load(font_name)
    full = font.render(text, spacing)
    mask, x0, _ = crop(full)
    edges = [font.width(text[:i + 1], spacing) - x0 for i in range(len(text))]
    return mask, edges
