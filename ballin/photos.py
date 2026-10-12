"""Photos uploaded on the simple photo page
(http://scoreboard.local/photos, saved to the phone's home screen).

Each upload picks "only" (the board shows just this photo) or "slides" (it joins the rotation)
and a time from 1 minute to 24 hours, after which the photo is removed from the board.

The phone makes the 64x32 panel picture itself (it can read iPhone HEIC photos; the Pi can't),
so the board only ever decodes a small PNG.
"""
import io
import json
import os
import re
import threading
import time
import uuid

import numpy as np

from .settings import DATA_DIR

PHOTO_DIR = os.path.join(DATA_DIR, "photos")
INDEX = os.path.join(PHOTO_DIR, "index.json")
MIN_MINUTES, MAX_MINUTES = 1, 24 * 60
MAX_ORIGINAL = 60 * 1024 * 1024
_lock = threading.Lock()


def load():
    try:
        with open(INDEX) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _save(rows):
    os.makedirs(PHOTO_DIR, exist_ok=True)
    tmp = INDEX + ".tmp"
    with open(tmp, "w") as f:
        json.dump(rows, f)
    os.replace(tmp, INDEX)


def active(now=None):
    now = now or time.time()
    return [r for r in load() if r["start"] <= now < r["end"]]


def add(panel_png, mode, minutes, source=""):
    """A new photo on the board: its 64x32 panel picture (PNG bytes) and how to show it."""
    from PIL import Image
    img = Image.open(io.BytesIO(panel_png))
    if img.size != (64, 32):
        raise ValueError("panel picture must be 64x32")
    minutes = max(MIN_MINUTES, min(MAX_MINUTES, int(minutes)))
    pid = time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:6]
    folder = os.path.join(PHOTO_DIR, pid)
    os.makedirs(folder, exist_ok=True)
    img.convert("RGB").save(os.path.join(folder, "panel.png"))
    now = time.time()
    with _lock:
        rows = load()
        rows.append({"id": pid, "mode": "only" if mode == "only" else "slides", "minutes": minutes,
                     "start": now, "end": now + minutes * 60, "source": source})
        _save(rows)
    return pid



def stop(pid):
    """Take a photo off the board now."""
    with _lock:
        rows = load()
        for r in rows:
            if r["id"] == pid and r["end"] > time.time():
                r["end"] = time.time()
        _save(rows)



def reap(now=None):
    """Remove every photo whose time is up: delete its folder and its index row. Returns how
    many were removed."""
    import shutil
    now = now or time.time()
    with _lock:
        rows = load()
        gone = [r for r in rows if r["end"] <= now]          # only photos whose time is already up
        for r in gone:
            if re.fullmatch(r"[0-9-]+[0-9a-f]{6}", r["id"]):
                shutil.rmtree(os.path.join(PHOTO_DIR, r["id"]), ignore_errors=True)
            for k in [k for k in _frames if k[0] == r["id"]]:
                _frames.pop(k, None)
        if gone:
            _save([r for r in rows if r not in gone])
    return len(gone)



# ------------------------------------------------------------------ on the board
_frames = {}


PHOTO_MAX_LOAD = 0.38     # power cap for photos: 90 s of a bright full-screen photo at 0.38 had no supply
                          # dips, 0.45 dipped (2026-10-04). At 0.30 photos were dimmed to mush (owner).
KNEE = 0.55               # over the cap, only brightness above this is pulled down


def frame(pid, brightness=100):
    """The panel picture exactly as the phone made it (the phone already stretched contrast and
    saturation: what its preview shows). If it would go over the power cap at this brightness,
    only the highlights (whites, sky) are pulled down, so colors and faces keep full strength;
    the board's even dimming would mute everything (owner, 2026-10-04). Cached."""
    key = (pid, brightness)
    if key not in _frames:
        from PIL import Image
        a = np.asarray(Image.open(os.path.join(PHOTO_DIR, pid, "panel.png")).convert("RGB"), np.float32) / 255
        b = max(brightness, 1) / 100

        def squeeze(t):
            v = a.max(-1, keepdims=True)
            v2 = np.where(v > KNEE, KNEE + (v - KNEE) * t, v)
            return a * (v2 / np.maximum(v, 1e-6))
        if a.mean() * b > PHOTO_MAX_LOAD:
            lo, hi = 0.0, 1.0
            for _ in range(16):                      # gentlest squeeze that fits under the cap
                t = (lo + hi) / 2
                lo, hi = (t, hi) if squeeze(t).mean() * b <= PHOTO_MAX_LOAD else (lo, t)
            a = squeeze(max(lo, 0.35))               # any rest: the board's even dimming
        _frames[key] = a * 255
    return _frames[key]


def slides(brightness=100):
    """(priority slides, rotation slides) for the photos on the board right now. Removes any photo
    whose time is up first (reap)."""
    from .slides import StaticSlide
    reap()
    only, rotation = [], []
    for r in active():
        try:
            s = StaticSlide("photo:%s" % r["id"], frame(r["id"], brightness), duration=8.0)
            s.max_load = PHOTO_MAX_LOAD
        except Exception:
            continue
        (only if r["mode"] == "only" else rotation).append(s)
    return only, rotation
