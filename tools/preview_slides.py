#!/usr/bin/env python3
"""Fetch live data and render every slide to a contact sheet (no panel needed).

  BALLIN_DATA_DIR=/tmp/bbdata python tools/preview_slides.py OUT.png [--demo] [nfl=3,9 mlb=16,4]

--demo also adds a fake clinch announcement and a birthday slide.
"""
import copy
import datetime
import logging
import os
import sys
import threading
import time

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ballin import gfx, settings as settings_mod, slides  # noqa: E402
from ballin.sports import LeagueWorker  # noqa: E402
from ballin.state import State  # noqa: E402
from tools.preview_splash import led_image  # noqa: E402


def main():
    logging.basicConfig(level=logging.INFO)
    out = sys.argv[1]
    demo = "--demo" in sys.argv
    st = State(settings_mod.load())
    # --favs nfl=3,9,21 mlb=16,4 : test several favorite teams per league
    favs = {a.split("=")[0]: a.split("=")[1].split(",") for a in sys.argv if "=" in a and not a.startswith("-")}
    if favs:
        st.settings = dict(st.settings, favorites=dict(st.settings["favorites"], **favs))
    for key in ("nhl", "mlb", "nfl"):
        LeagueWorker(st, threading.Event(), key).step()
    for k, d in [(k, d) for k, ds in st.leagues.items() for d in ds]:
        print(k, d.team.abbr, "in_window", d.in_window, "elim", d.eliminated, "type", d.season_type,
              "last", d.last and d.last.detail, "next", d.next and d.next.detail, "series", d.series)
    if demo:
        st.settings = dict(st.settings, events=[{"name": "Alex", "date": datetime.date.today().strftime("%m-%d"),
                                                 "type": "birthday"}])
        nfl = copy.copy(st.leagues["nfl"][0])
        nfl.announcement = ("CLINCHED", "PLAYOFFS!")
        mlb = copy.copy(st.leagues["mlb"][0])
        mlb.celebrate_until = time.time() + 3600
        st.leagues = dict(st.leagues, nfl=[nfl] + st.leagues["nfl"][1:], mlb=[mlb] + st.leagues["mlb"][1:])
    shots = []
    for s in slides.build(st):
        times = [0.0] if isinstance(s, slides.StaticSlide) else [0.0, s.duration * 0.5, s.duration - 0.1]
        for t in times:
            shots.append(("%s t=%.1f" % (s.key, t), gfx.to_uint8(s.render(t))))
    cols = 3
    tw, th = 64 * 6, 32 * 6 + 14
    rows = (len(shots) + cols - 1) // cols
    sheet = Image.new("RGB", (cols * (tw + 8), rows * (th + 8)), (40, 40, 40))
    draw = ImageDraw.Draw(sheet)
    for k, (label, frame) in enumerate(shots):
        x, y = (k % cols) * (tw + 8) + 4, (k // cols) * (th + 8) + 4
        draw.text((x, y), label, fill=(200, 200, 200))
        sheet.paste(led_image(frame), (x, y + 14))
    sheet.save(out)
    print(out)


if __name__ == "__main__":
    main()
