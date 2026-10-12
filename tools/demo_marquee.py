#!/usr/bin/env python3
"""Show every team's celebration marquee (team-color border on a "clinched" slide).

On the panel (stop the service first; needs root):
  sudo /opt/ballin-board/venv/bin/python tools/demo_marquee.py [nhl mlb nfl] [--seconds 4]
As a preview sheet instead:
  python tools/demo_marquee.py nfl --preview OUT.png
"""
import argparse
import json
import os
import sys
import time

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ballin import espn, gfx, logos, slides  # noqa: E402
from ballin.settings import DATA_DIR  # noqa: E402

FPS = 40


def team_slides(league):
    with open(os.path.join(DATA_DIR, "teams_%s.json" % league)) as f:
        teams = [espn.Team(**t) for t in json.load(f)["teams"]]
    out = []
    for team in sorted(teams, key=lambda t: t.location + t.name):
        lines = slides.news_lines(("CLINCHED", "PLAYOFFS!"))
        inner = slides.CelebrationSlide("demo:" + team.abbr, lines, logos.get(league, team.id),
                                        title=(team.name.upper(), slides.team_color(team)))
        out.append((league.upper() + " " + team.abbr, slides.BorderSlide(inner, slides.team_colors(team))))
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("leagues", nargs="*", default=["nhl", "mlb", "nfl"])
    ap.add_argument("--seconds", type=float, default=4.0)
    ap.add_argument("--preview")
    args = ap.parse_args()
    items = [x for lg in args.leagues for x in team_slides(lg)]

    if args.preview:
        cols, tw, th = 4, 64 * 4, 32 * 4 + 12
        from tools.preview_splash import cie
        sheet = Image.new("RGB", (cols * (tw + 6), ((len(items) + cols - 1) // cols) * (th + 6)), (40, 40, 40))
        draw = ImageDraw.Draw(sheet)
        for k, (label, s) in enumerate(items):
            frame = cie(gfx.to_uint8(s.render(1.0)).astype("float32"))
            img = Image.fromarray(frame).resize((tw, th - 12), Image.NEAREST)
            x, y = (k % cols) * (tw + 6) + 3, (k // cols) * (th + 6) + 3
            draw.text((x, y), label, fill=(220, 220, 220))
            sheet.paste(img, (x, y + 12))
        sheet.save(args.preview)
        print(args.preview)
        return

    from ballin.display import Display
    display = Display(60)
    try:
        for label, s in items:
            print(label, flush=True)
            start = time.monotonic()
            while (t := time.monotonic() - start) < args.seconds:
                display.show(gfx.to_uint8(s.render(t)))
                time.sleep(max(0.0, 1 / FPS - (time.monotonic() - start - t)))
    finally:
        display.clear()


if __name__ == "__main__":
    main()
