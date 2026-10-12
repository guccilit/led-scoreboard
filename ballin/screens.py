"""Simple full-screen pages: clock and friendly status messages.

Text masks are cached, so each frame only does a few small numpy blits.
"""
import datetime
import functools
import math

from . import fonts, gfx
from .gfx import H, W

WHITE = (235, 235, 245)
GOLD = (255, 185, 20)
BLUE = (40, 100, 255)
DIM = (90, 90, 110)


@functools.lru_cache(maxsize=256)
def text(s, font):
    return fonts.text_mask(s, font)[0]


def centered(frame, s, font, y, color):
    m = text(s, font)
    gfx.blit(frame, m, (W - m.shape[1]) // 2, y, color)
    return m.shape[0]


@functools.lru_cache(maxsize=2)
def _clock(minute_key):
    return _draw_clock(datetime.datetime.strptime(minute_key, "%Y-%m-%d %H:%M"))


def clock(now=None):
    """Clock screen; drawn once per minute and reused."""
    now = now or datetime.datetime.now()
    return _clock(now.strftime("%Y-%m-%d %H:%M"))


def _draw_clock(now):
    frame = gfx.new_frame()
    hm = now.strftime("%I:%M").lstrip("0")
    ampm = now.strftime("%p")
    date = now.strftime("%a %b %d").upper().replace(" 0", " ")

    big = text(hm, "7x13B")
    small = text(ampm, "4x6")
    width = big.shape[1] + 2 + small.shape[1]
    x = (W - width) // 2
    y = 5
    gfx.blit(frame, big, x, y, WHITE)
    gfx.blit(frame, small, x + big.shape[1] + 2, y + big.shape[0] - small.shape[0], DIM)
    centered(frame, date, "5x7", y + big.shape[0] + 5, GOLD)
    return frame


def status(title, subtitle, t, color=GOLD):
    """A message with three pulsing dots, e.g. ("NO INTERNET", "CHECKING")."""
    frame = gfx.new_frame()
    y = 7
    y += centered(frame, title, "5x7", y, color) + 4
    y += centered(frame, subtitle, "4x6", y, WHITE) + 4
    for i in range(3):
        b = 0.25 + 0.75 * max(0.0, math.sin(t * 4 - i * 0.9))
        x = W // 2 - 4 + i * 4
        frame[y:y + 2, x:x + 2] = (b * BLUE[0], b * BLUE[1], b * BLUE[2])
    return frame


GREEN = (40, 230, 90)
RED = (240, 45, 45)


def hold_to_shutdown(progress):
    """Shown while the button is held: let go to cancel."""
    frame = gfx.new_frame()
    centered(frame, "HOLD TO", "5x7", 4, WHITE)
    centered(frame, "SHUT DOWN", "5x7", 13, GOLD)
    x0, x1 = 8, W - 8
    frame[24:27, x0:x1] = (40, 40, 50)
    fill = int((x1 - x0) * max(0.0, min(1.0, progress)))
    frame[24:27, x0:x0 + fill] = RED
    return frame


def shutting_down(t):
    frame = gfx.new_frame()
    centered(frame, "SHUTTING", "5x7", 6, WHITE)
    centered(frame, "DOWN", "5x7", 15, GOLD)
    for i in range(3):
        b = 0.25 + 0.75 * max(0.0, math.sin(t * 5 - i * 0.9))
        x = W // 2 - 4 + i * 4
        frame[25:27, x:x + 2] = (b * BLUE[0], b * BLUE[1], b * BLUE[2])
    return frame


@functools.lru_cache(maxsize=40)
def safe_to_unplug(seconds_left=0):
    """Countdown while the Pi finishes shutting down, then "SAFE TO UNPLUG"."""
    frame = gfx.new_frame()
    if seconds_left > 0:
        centered(frame, "SAFE TO UNPLUG", "4x6", 2, WHITE)
        centered(frame, str(seconds_left), "7x13B", 10, GOLD)
        centered(frame, "SECONDS", "4x6", 25, DIM)
        return frame
    centered(frame, "SAFE TO", "5x7", 5, WHITE)
    centered(frame, "UNPLUG", "7x13B", 15, GREEN)
    return frame


@functools.lru_cache(maxsize=1)
def sleeping():
    """Shown for a moment when the button turns the screen off."""
    frame = gfx.new_frame()
    centered(frame, "SLEEPING", "5x7", 6, BLUE)
    centered(frame, "PRESS TO WAKE", "4x6", 19, DIM)
    return frame


CYAN = (40, 220, 255)


@functools.lru_cache(maxsize=4)
def _wifi_page(lines):
    frame = gfx.new_frame()
    y = 1
    for s, font, color, gap in lines:
        y += centered(frame, s, font, y, color) + gap
    return frame


def wifi_setup(t, error=False):
    """WiFi setup mode: tell the user how to connect a phone to the board."""
    pages = [
        (("WIFI SETUP", "5x7", GOLD, 3), ("JOIN WIFI:", "4x6", WHITE, 2),
         ("BALLIN BOARD", "5x7", CYAN, 2), ("SETUP", "5x7", CYAN, 0)),
        (("WIFI SETUP", "5x7", GOLD, 3), ("THEN TAP THE", "4x6", WHITE, 2),
         ("SIGN-IN", "5x7", CYAN, 2), ("POP-UP", "5x7", CYAN, 0)),
        # Phones on a network without internet often fall back to cellular data, so
        # "scoreboard.local" doesn't work here; the address does.
        (("WIFI SETUP", "5x7", GOLD, 3), ("NO POP-UP? OPEN", "4x6", WHITE, 3),
         ("10.42.0.1", "5x7", CYAN, 2), ("IN A BROWSER", "4x6", WHITE, 0)),
    ]
    if error:
        pages.append((("WIFI SETUP", "5x7", GOLD, 3), ("COULDN'T JOIN", "4x6", RED, 2),
                      ("CHECK THE", "5x7", WHITE, 2), ("PASSWORD", "5x7", WHITE, 0)))
    return _wifi_page(pages[int(t / 4) % len(pages)])


def factory_reset_countdown(remaining):
    frame = gfx.new_frame()
    centered(frame, "FACTORY RESET", "4x6", 2, RED)
    centered(frame, "IN %d" % max(0, int(math.ceil(remaining))), "7x13B", 10, WHITE)
    centered(frame, "LET GO TO CANCEL", "4x6", 25, DIM)
    return frame


@functools.lru_cache(maxsize=1)
def factory_reset_done():
    frame = gfx.new_frame()
    centered(frame, "FACTORY", "5x7", 4, WHITE)
    centered(frame, "RESET DONE", "5x7", 13, GREEN)
    centered(frame, "SETTING UP WIFI", "4x6", 24, DIM)
    return frame


@functools.lru_cache(maxsize=4)
def _address_page(ip, name="scoreboard"):
    frame = gfx.new_frame()
    if ip:
        # The friendly name (owner's choice), with the number underneath for the few phones
        # that can't open .local addresses
        centered(frame, "SETTINGS PAGE:", "4x6", 1, GOLD)
        centered(frame, (name + ".local").upper(), "4x6", 8, WHITE)
        centered(frame, ip, "4x6", 15, (150, 150, 165))
        centered(frame, "3 PRESSES = WIFI", "4x6", 25, (120, 120, 135))
    else:
        centered(frame, "NO WIFI?", "5x7", 4, GOLD)
        centered(frame, "PRESS BUTTON", "4x6", 15, WHITE)
        centered(frame, "3 TIMES", "4x6", 23, WHITE)
    return frame


SETTINGS_CARD_SECS = 6.0


def settings_card(t, ip, name="scoreboard"):
    """How to reach the settings page: the board's address and the WiFi-setup tip.
    (A QR code was tried: phones couldn't reliably scan it off a 64x32 panel.)"""
    return _address_page(ip, name)
