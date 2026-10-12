#!/usr/bin/env python3
"""Show the standings highlight styles (A-D) for comparison.

On the panel (stop the service first; needs root):
  sudo /opt/ballin-board/venv/bin/python tools/demo_highlight.py [--seconds 7] [--rounds 2]
As a preview sheet:
  sudo /opt/ballin-board/venv/bin/python tools/demo_highlight.py --preview OUT.png
"""
import argparse
import os
import sys
import time

from PIL import Image

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ballin import gfx, slides, sports  # noqa: E402

LETTERS = "ABCD"


def style_slides():
    teams = sports.load_cached("mlb")
    data = teams[0]
    conf, div = data.standings.division_of[data.team.id]
    out = []
    for letter, style in zip(LETTERS, slides.HIGHLIGHTS):
        s = slides.division_slide(data, {data.team.id}, div, style)
        s.header = slides._header("STYLE %s: %s" % (letter, style.upper()))
        out.append(s)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=7.0)
    ap.add_argument("--rounds", type=int, default=2)
    ap.add_argument("--preview")
    args = ap.parse_args()
    items = style_slides()
    if args.preview:
        from tools.preview_splash import led_image
        ims = [led_image(gfx.to_uint8(s.render(0.0))) for s in items]
        sheet = Image.new("RGB", (ims[0].width * 2 + 8, ims[0].height * 2 + 8), (40, 40, 40))
        for i, im in enumerate(ims):
            sheet.paste(im, ((i % 2) * (im.width + 8), (i // 2) * (im.height + 8)))
        sheet.save(args.preview)
        return
    from ballin.display import Display
    display = Display(60)
    try:
        for _ in range(args.rounds):
            for s in items:
                display.show(gfx.to_uint8(s.render(0.0)))
                time.sleep(args.seconds)
    finally:
        display.clear()


if __name__ == "__main__":
    main()
