#!/usr/bin/env python3
"""Flicker test pattern for the 64x32 panel.

Run on the Pi (needs root for GPIO):
  sudo /opt/ballin-board/venv/bin/python tools/test_pattern.py --slowdown 2

Shows a white border, color bars, a gradient and scrolling text, drawn on an
offscreen canvas and swapped with SwapOnVSync.
"""
import argparse
import os
import time

from rgbmatrix import RGBMatrix, RGBMatrixOptions, graphics

FONT_DIR = os.path.expanduser("~dacino/rpi-rgb-led-matrix/fonts")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--slowdown", type=int, default=2)
    ap.add_argument("--brightness", type=int, default=60)
    ap.add_argument("--seconds", type=int, default=60)
    args = ap.parse_args()

    opts = RGBMatrixOptions()
    opts.rows = 32
    opts.cols = 64
    opts.hardware_mapping = "adafruit-hat-pwm"
    opts.gpio_slowdown = args.slowdown
    opts.brightness = args.brightness
    opts.drop_privileges = False
    matrix = RGBMatrix(options=opts)

    font = graphics.Font()
    font.LoadFont(os.path.join(FONT_DIR, "5x7.bdf"))
    white = graphics.Color(255, 255, 255)
    yellow = graphics.Color(255, 200, 0)

    canvas = matrix.CreateFrameCanvas()
    text = "BALLIN' BOARD"
    x = canvas.width
    end = time.time() + args.seconds
    while time.time() < end:
        canvas.Clear()
        # Border
        for i in range(64):
            canvas.SetPixel(i, 0, 255, 255, 255)
            canvas.SetPixel(i, 31, 255, 255, 255)
        for j in range(32):
            canvas.SetPixel(0, j, 255, 255, 255)
            canvas.SetPixel(63, j, 255, 255, 255)
        # Color bars
        bars = [(255, 0, 0), (0, 255, 0), (0, 0, 255), (255, 255, 0),
                (0, 255, 255), (255, 0, 255), (255, 255, 255)]
        w = 62 // len(bars)
        for n, (r, g, b) in enumerate(bars):
            for xx in range(1 + n * w, 1 + (n + 1) * w):
                for yy in range(2, 10):
                    canvas.SetPixel(xx, yy, r, g, b)
        # Gradient (shows color depth / banding)
        for xx in range(1, 63):
            v = int(xx * 255 / 62)
            for yy in range(11, 15):
                canvas.SetPixel(xx, yy, v, 0, 255 - v)
        # Slowdown label + scrolling text
        graphics.DrawText(canvas, font, 3, 22, yellow, "slowdown %d" % args.slowdown)
        length = graphics.DrawText(canvas, font, x, 29, white, text)
        x -= 1
        if x + length < 1:
            x = canvas.width
        canvas = matrix.SwapOnVSync(canvas)
        time.sleep(0.03)
    matrix.Clear()


if __name__ == "__main__":
    main()
