#!/usr/bin/env python3
"""Dead-LED check: fills the whole 64x32 panel with one solid color at a time.

Run on the Pi with the board stopped (needs root for GPIO):
  sudo systemctl stop ballin-board
  sudo /opt/ballin-board/venv/bin/python tools/solid_colors.py
  sudo systemctl start ballin-board

Any dark dot or line in a solid color is an LED (or a column of them) that isn't lighting.
White runs dimmer: a full white panel draws the most power.
"""
import argparse
import time

from rgbmatrix import RGBMatrix, RGBMatrixOptions

COLORS = (("white", (255, 255, 255), 35), ("red", (255, 0, 0), 60),
          ("green", (0, 255, 0), 60), ("blue", (0, 0, 255), 60))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--seconds", type=float, default=15, help="per color")
    args = ap.parse_args()
    opts = RGBMatrixOptions()
    opts.rows, opts.cols = 32, 64
    opts.hardware_mapping = "adafruit-hat-pwm"
    opts.gpio_slowdown = 2
    opts.limit_refresh_rate_hz = 150
    opts.drop_privileges = False
    matrix = RGBMatrix(options=opts)
    canvas = matrix.CreateFrameCanvas()
    for name, (r, g, b), bright in COLORS:
        print("showing", name, flush=True)
        matrix.brightness = bright
        canvas.Fill(r, g, b)
        canvas = matrix.SwapOnVSync(canvas)
        time.sleep(args.seconds)
    matrix.Clear()


if __name__ == "__main__":
    main()
