#!/usr/bin/env python3
"""Dry-run live mode against real games, without the panel.

Pretends the given teams are the favorites, runs the real workers, logs every
event, and saves what the board would show (on each change) as PNGs.

  BALLIN_DATA_DIR=/tmp/bbwatch python tools/watch_live.py OUTDIR nfl=<team id> mlb=<team id> [--hours 4]
"""
import argparse
import logging
import os
import sys
import threading
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ballin import gfx, settings as settings_mod  # noqa: E402
from ballin.live import LiveFetcher, LivePresenter  # noqa: E402
from ballin.sports import LeagueWorker  # noqa: E402
from ballin.state import State  # noqa: E402
from tools.preview_splash import led_image  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("outdir")
    ap.add_argument("teams", nargs="+", help="league=team_id")
    ap.add_argument("--hours", type=float, default=4)
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(os.path.join(args.outdir, "watch.log")),
                                  logging.StreamHandler()])
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    favs = dict(t.split("=") for t in args.teams)
    st = State(dict(settings_mod.load(), favorites=favs))
    stop = threading.Event()
    for key in favs:
        LeagueWorker(st, stop, key).start()
    LiveFetcher(st, stop).start()
    LivePresenter(st, stop).start()

    last_key, n = None, 0
    end = time.time() + args.hours * 3600
    while time.time() < end:
        shown = st.live_alert or (st.live_slides[0] if st.live_slides else None)
        if shown is not None:
            frame = gfx.to_uint8(shown.render(1.5))
            key = (shown.key, frame.tobytes())
            if key != last_key:
                last_key = key
                n += 1
                led_image(frame).save(os.path.join(args.outdir, "%04d_%s.png" % (n, time.strftime("%H%M%S"))))
                for tr in st.live_tracked.values():
                    if tr.visible:
                        g = tr.visible.game
                        logging.info("VIEW %s %s %s-%s %s | %s", g.state, g.detail, g.away.score, g.home.score,
                                     {k: g.situation.get(k) for k in ("balls", "strikes", "outs", "onFirst",
                                      "onSecond", "onThird", "shortDownDistanceText", "possession", "yardLine", "possessionText", "downDistanceText", "isRedZone", "down", "distance")
                                      if k in g.situation}, ((g.situation.get("lastPlay") or {}).get("type") or {}))
        time.sleep(1)
    stop.set()


if __name__ == "__main__":
    main()
