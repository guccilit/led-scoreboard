"""The LED panel. Only the render loop talks to this.

Frames are uint8 numpy arrays shaped (32, 64, 3). Every frame is drawn to an
offscreen canvas and swapped in on vsync, never drawn to the visible canvas.
"""
from PIL import Image

# Confirmed flicker-free on the real hardware (see docs/pi-setup-notes.md).
ROWS, COLS = 32, 64
HARDWARE_MAPPING = "adafruit-hat-pwm"
GPIO_SLOWDOWN = 2
# Fixed refresh rate: keeps brightness steady when the Pi is busy (WiFi, SD card).
# Uncapped it averages 220 Hz but dips to ~108 Hz under load (visible flicker);
# capped at 150 it held exactly 150 Hz under heavy WiFi + SD load.
REFRESH_LIMIT_HZ = 150


class Display:
    def __init__(self, brightness=60):
        from rgbmatrix import RGBMatrix, RGBMatrixOptions

        opts = RGBMatrixOptions()
        opts.rows = ROWS
        opts.cols = COLS
        opts.hardware_mapping = HARDWARE_MAPPING
        opts.gpio_slowdown = GPIO_SLOWDOWN
        opts.brightness = brightness
        opts.limit_refresh_rate_hz = REFRESH_LIMIT_HZ
        opts.drop_privileges = False
        self.matrix = RGBMatrix(options=opts)
        self.canvas = self.matrix.CreateFrameCanvas()

    def show(self, frame):
        self.canvas.SetImage(Image.fromarray(frame, "RGB"))
        self.canvas = self.matrix.SwapOnVSync(self.canvas)

    def set_brightness(self, value):
        # Applied as pixels are drawn, so it takes effect on the next frame.
        self.matrix.brightness = max(1, min(100, int(value)))

    def clear(self):
        self.canvas.Clear()
        self.canvas = self.matrix.SwapOnVSync(self.canvas)
