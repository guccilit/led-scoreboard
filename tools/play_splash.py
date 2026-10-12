#!/usr/bin/env python3
"""Play the startup animation on the panel (needs root).

  sudo /opt/ballin-board/venv/bin/python tools/play_splash.py [--name jackson40] [--no-birthday] [--loops N]
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ballin.display import Display  # noqa: E402
from ballin import splash as splash_mod  # noqa: E402

FPS = 40


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--birthday", action="store_true", default=True)
    ap.add_argument("--no-birthday", dest="birthday", action="store_false")
    ap.add_argument("--loops", type=int, default=1)
    ap.add_argument("--name", default=splash_mod.DEFAULT_SPLASH, help="startup show (see splash.SPLASHES)")
    ap.add_argument("--brightness", type=int, default=60)
    args = ap.parse_args()

    display = Display(args.brightness)
    worst = 0.0
    for _ in range(args.loops):
        splash = splash_mod.create(args.name if args.birthday else args.name.rstrip("40"))
        start = time.monotonic()
        frame_no = 0
        while True:
            t = frame_no / FPS
            if t > splash.duration + 1.0:
                break
            t0 = time.perf_counter()
            frame = splash.render(t)
            worst = max(worst, time.perf_counter() - t0)
            display.show(frame)
            frame_no += 1
            delay = start + frame_no / FPS - time.monotonic()
            if delay > 0:
                time.sleep(delay)
    display.clear()
    print("slowest frame: %.1f ms" % (worst * 1000))


if __name__ == "__main__":
    main()
