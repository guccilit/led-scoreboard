"""Small numpy drawing helpers for the 64x32 panel.

Frames are float32 arrays shaped (32, 64, 3) with values 0-255.
"""
import numpy as np

W, H = 64, 32


def new_frame():
    return np.zeros((H, W, 3), np.float32)


def to_uint8(frame):
    return np.clip(frame, 0, 255).astype(np.uint8)


def clamp01(v):
    return np.clip(v, 0.0, 1.0)


def smoothstep(v):
    v = clamp01(v)
    return v * v * (3 - 2 * v)


def ease_out_cubic(v):
    v = clamp01(v)
    return 1 - (1 - v) ** 3


def ease_in_quad(v):
    v = clamp01(v)
    return v * v


def ease_out_quad(v):
    v = clamp01(v)
    return 1 - (1 - v) ** 2


def ease_out_back(v, s=1.9):
    v = clamp01(v) - 1
    return 1 + (s + 1) * v ** 3 + s * v ** 2


def lerp(a, b, t):
    return a + (b - a) * t


def hsv(h, s=1.0, v=1.0):
    """Vectorised HSV -> RGB (0-255). h, s, v may be arrays."""
    h = np.asarray(h, np.float32) % 1.0
    i = np.floor(h * 6).astype(int) % 6
    f = h * 6 - np.floor(h * 6)
    p, q, t = v * (1 - s), v * (1 - f * s), v * (1 - (1 - f) * s)
    v = np.broadcast_to(v, h.shape)
    p, q, t = (np.broadcast_to(x, h.shape) for x in (p, q, t))
    r = np.choose(i, [v, q, p, p, t, v])
    g = np.choose(i, [t, v, v, q, p, p])
    b = np.choose(i, [p, p, t, v, v, q])
    return np.stack([r, g, b], -1) * 255


def _region(frame, h, w, x, y):
    """Clip a (h, w) block placed at (x, y). Returns frame slices and source slices, or None."""
    fx0, fy0 = max(0, x), max(0, y)
    fx1, fy1 = min(frame.shape[1], x + w), min(frame.shape[0], y + h)
    if fx1 <= fx0 or fy1 <= fy0:
        return None
    return (slice(fy0, fy1), slice(fx0, fx1)), (slice(fy0 - y, fy1 - y), slice(fx0 - x, fx1 - x))


def blit(frame, mask, x, y, color, alpha=1.0):
    """Paint mask pixels at (x, y). color is (3,) or an array shaped like mask + (3,)."""
    x, y = int(round(x)), int(round(y))
    h, w = mask.shape
    reg = _region(frame, h, w, x, y)
    if reg is None:
        return
    (fs, ms) = reg
    m = mask[ms]
    col = np.asarray(color, np.float32)
    if col.ndim == 3:
        col = col[ms][m]
    dst = frame[fs]
    dst[m] = dst[m] * (1 - alpha) + col * alpha


def blit_rgb(frame, img, x, y, alpha=1.0, key=None):
    """Paint an (h, w, 3) image; pixels equal to 0 (or key mask False) are transparent."""
    m = img.max(-1) > 0 if key is None else key
    blit(frame, m, x, y, img, alpha)


def clear_behind(frame, mask, x, y, pad=1):
    """Black out a halo around a mask so text stays readable over busy backgrounds."""
    h, w = mask.shape
    grown = np.zeros((h + 2 * pad, w + 2 * pad), bool)
    for dy in range(2 * pad + 1):
        for dx in range(2 * pad + 1):
            grown[dy:dy + h, dx:dx + w] |= mask
    blit(frame, grown, x - pad, y - pad, (0, 0, 0))


def splat(frame, xs, ys, cols):
    """Additively draw points at sub-pixel positions (bilinear), for smooth particles."""
    xs = np.asarray(xs, np.float32)
    ys = np.asarray(ys, np.float32)
    cols = np.asarray(cols, np.float32).reshape(-1, 3)
    x0 = np.floor(xs).astype(int)
    y0 = np.floor(ys).astype(int)
    fx, fy = xs - x0, ys - y0
    for dx, dy, wgt in ((0, 0, (1 - fx) * (1 - fy)), (1, 0, fx * (1 - fy)),
                        (0, 1, (1 - fx) * fy), (1, 1, fx * fy)):
        xi, yi = x0 + dx, y0 + dy
        ok = (xi >= 0) & (xi < frame.shape[1]) & (yi >= 0) & (yi < frame.shape[0])
        np.add.at(frame, (yi[ok], xi[ok]), cols[ok] * wgt[ok, None])


def scale_mask(mask, s):
    """Nearest-neighbour scale of a mask around its own size."""
    h, w = mask.shape
    oh, ow = max(1, int(round(h * s))), max(1, int(round(w * s)))
    # Sample at pixel centers: at s just above 1, flooring (i / s) would repeat the first
    # row and drop the last one.
    ri = np.minimum(((np.arange(oh) + 0.5) / s).astype(int), h - 1)
    ci = np.minimum(((np.arange(ow) + 0.5) / s).astype(int), w - 1)
    return mask[ri][:, ci]


def sprite(rows, palette):
    """Build an RGB image from pixel-art strings. '.' is transparent."""
    h, w = len(rows), max(len(r) for r in rows)
    img = np.zeros((h, w, 3), np.float32)
    for y, row in enumerate(rows):
        for x, ch in enumerate(row):
            if ch != ".":
                img[y, x] = palette[ch]
    return img
