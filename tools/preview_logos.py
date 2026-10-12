#!/usr/bin/env python3
"""Download and preview a league's logos as LEDs.  python tools/preview_logos.py nfl OUT.png [size]"""
import os
import sys

import numpy as np
from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ballin import espn, logos  # noqa: E402
from ballin.leagues import LEAGUES  # noqa: E402
from tools.preview_splash import led_image  # noqa: E402


def main():
    league = LEAGUES[sys.argv[1]]
    size = int(sys.argv[3]) if len(sys.argv) > 3 else logos.SIZE
    teams = espn.fetch_teams(league)
    tiles = []
    for t in teams:
        logos.download(league.key, t, size)
        arr = logos.get(league.key, t.id, size)
        if arr is not None:
            tiles.append(arr)
    per_row = 64 // size
    frames = []
    for i in range(0, len(tiles), per_row * (32 // size)):
        f = np.zeros((32, 64, 3), np.float32)
        for k, tile in enumerate(tiles[i:i + per_row * (32 // size)]):
            x, y = (k % per_row) * size, (k // per_row) * size
            f[y:y + size, x:x + size] = tile
        frames.append(led_image(f.astype(np.uint8)))
    sheet = Image.new("RGB", (frames[0].width * 2 + 8, (frames[0].height + 8) * ((len(frames) + 1) // 2)), (40, 40, 40))
    for k, im in enumerate(frames):
        sheet.paste(im, ((k % 2) * (im.width + 8), (k // 2) * (im.height + 8)))
    sheet.save(sys.argv[2])


if __name__ == "__main__":
    main()
