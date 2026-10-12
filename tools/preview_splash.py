#!/usr/bin/env python3
"""Render the splash animation to contact-sheet PNGs (no LED panel needed).

  python tools/preview_splash.py OUTDIR [--step 0.4] [--birthday]

Pixels are drawn as round LEDs with the panel's brightness curve applied, so the
sheets look roughly like the real thing.
"""
import argparse
import os
import sys

import numpy as np
from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ballin.splash import Splash  # noqa: E402

LED = 6      # screen pixels per LED
COLS, ROWS = 3, 4


def cie(v):
    """Approximate the matrix library's CIE1931 luminance correction."""
    L = v / 255.0 * 100
    y = np.where(L <= 8, L / 902.3, ((L + 16) / 116) ** 3)
    return (np.clip(y, 0, 1) ** (1 / 2.2) * 255).astype(np.uint8)


def led_image(frame):
    big = np.repeat(np.repeat(cie(frame.astype(np.float32)), LED, 0), LED, 1)
    yy, xx = np.mgrid[0:LED, 0:LED]
    c = (LED - 1) / 2
    dot = ((yy - c) ** 2 + (xx - c) ** 2) <= (LED / 2 - 0.6) ** 2
    big = big * np.tile(dot, (frame.shape[0], frame.shape[1]))[..., None]
    return Image.fromarray(big.astype(np.uint8))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("--step", type=float, default=0.4)
    ap.add_argument("--start", type=float, default=0.0)
    ap.add_argument("--birthday", action="store_true")
    ap.add_argument("--v2", action="store_true", help="the Cubs/Hawks/Bears show (splash2)")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    from ballin.splash2 import Show
    sp = Show(birthday=args.birthday) if args.v2 else Splash(birthday=args.birthday)
    fps = 40
    shots = []
    next_shot = args.start
    for i in range(int(sp.duration * fps) + 1):
        t = i / fps
        frame = sp.render(t)
        if t + 1e-6 >= next_shot:
            shots.append((t, frame))
            next_shot += args.step

    tw, th = 64 * LED, 32 * LED + 14
    per = COLS * ROWS
    for n in range(0, len(shots), per):
        sheet = Image.new("RGB", (COLS * (tw + 8), ROWS * (th + 8)), (40, 40, 40))
        draw = ImageDraw.Draw(sheet)
        for k, (t, frame) in enumerate(shots[n:n + per]):
            x, y = (k % COLS) * (tw + 8) + 4, (k // COLS) * (th + 8) + 4
            draw.text((x, y), "t=%.2fs" % t, fill=(200, 200, 200))
            sheet.paste(led_image(frame), (x, y + 14))
        path = os.path.join(args.outdir, "sheet%02d.png" % (n // per))
        sheet.save(path)
        print(path)


if __name__ == "__main__":
    main()
