"""Team logos: downloaded once, shrunk for the panel, cached as small PNGs.

download() runs in worker threads. get() is cheap (memory cache) and is what
slide builders use; it returns None when a logo isn't available yet.
"""
import io
import logging
import os
import threading

import numpy as np
from PIL import Image, ImageEnhance

from . import api
from .settings import DATA_DIR

log = logging.getLogger(__name__)

SIZE = 16
LOGO_DIR = os.path.join(DATA_DIR, "logos")
_cache = {}
_lock = threading.Lock()


def path(league, team_id, size=SIZE):
    return os.path.join(LOGO_DIR, league, "%s_%d.png" % (team_id, size))


def _shrink(raw, size):
    img = Image.open(io.BytesIO(raw)).convert("RGBA")
    bbox = img.getchannel("A").point(lambda a: 255 if a > 40 else 0).getbbox()
    if bbox:
        img = img.crop(bbox)
    img.thumbnail((size, size), Image.LANCZOS)
    canvas = Image.new("RGBA", (size, size), (0, 0, 0, 255))
    canvas.alpha_composite(img, ((size - img.width) // 2, (size - img.height) // 2))
    rgb = canvas.convert("RGB")
    # Dark team colors (navy, forest green) nearly vanish on LEDs: lift the logo so its
    # brightest parts reach full brightness, keeping the hues.
    a = np.asarray(rgb, np.float32)
    v = a.max(-1)
    lit = v > 12
    if lit.any():
        top = np.percentile(v[lit], 95)
        if 0 < top < 215:
            a = np.clip(a * min(3.0, 230.0 / top), 0, 255)
            rgb = Image.fromarray(a.astype(np.uint8))
    # LEDs read flatter than screens: a little extra colour and contrast helps.
    rgb = ImageEnhance.Color(rgb).enhance(1.3)
    return ImageEnhance.Contrast(rgb).enhance(1.15)


def _score(img):
    """(colorfulness, visible area) of a shrunk logo on black."""
    a = np.asarray(img, np.float32) / 255
    v = a.max(-1)
    s = np.where(v > 0, (v - a.min(-1)) / np.maximum(v, 1e-6), 0)
    lit = v > 0.25
    if not lit.any():
        return 0.0, 0.0
    return float((s * v)[lit].mean()), float(lit.mean())


LEAGUE_SIZE = 28
LEAGUE_URL = "https://a.espncdn.com/i/teamlogos/leagues/500-dark/%s.png"


def _fetch(league, key, url, size, force):
    p = path(league, key, size)
    if os.path.exists(p) and not force:
        return True
    if not url:
        return False
    try:
        # ESPN has a dark-background version (usually best on black LEDs) and the regular
        # one. Some dark versions are nearly all white (e.g. the Jets), so shrink both and
        # keep whichever shows more color without losing parts of the logo to black.
        candidates = []
        urls = [url] + ([url.replace("/500-dark/", "/500/")] if "/500-dark/" in url else [])
        for u in urls:
            try:
                candidates.append(_shrink(api.get_bytes(u), size))
            except Exception:
                pass
        if not candidates:
            raise RuntimeError("no logo image available")
        img = candidates[0]
        if len(candidates) == 2:
            dark, regular = (_score(c) for c in candidates)
            if regular[0] > dark[0] * 1.4 + 0.02 and regular[1] >= dark[1] * 0.8:
                img = candidates[1]
                log.info("Logo %s %s: using the regular (more colorful) version", league, key)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        img.save(tmp, "PNG")
        os.replace(tmp, p)
        with _lock:
            _cache.pop((league, str(key), size), None)
        return True
    except Exception as exc:
        log.warning("Logo %s %s failed: %s", league, key, exc)
        return False


def download(league, team, size=SIZE, force=False):
    """Fetch and cache one team's logo. Returns True if it's available."""
    return _fetch(league, team.id, team.logo_url, size, force)


def download_league(league, force=False):
    return _fetch(league, "league", LEAGUE_URL % league, LEAGUE_SIZE, force)


def get_league(league):
    return get(league, "league", LEAGUE_SIZE)


def get(league, team_id, size=SIZE):
    """Logo as float32 (size, size, 3) array, or None."""
    key = (league, str(team_id), size)
    with _lock:
        if key in _cache:
            return _cache[key]
    p = path(league, team_id, size)
    arr = None
    if os.path.exists(p):
        try:
            arr = np.asarray(Image.open(p).convert("RGB"), np.float32)
        except OSError:
            arr = None
    with _lock:
        _cache[key] = arr
    return arr


def fantasy_key(url):
    """Cache name for a fantasy team's logo: changes when the owner picks a new logo."""
    import hashlib
    return hashlib.md5(url.encode()).hexdigest()[:12]


def download_fantasy(url, cookies=None, size=SIZE):
    """A fantasy team's logo, cached under fantasy_key(url). ESPN's are often SVG (drawn with
    rsvg-convert, from librsvg2-bin); its custom uploads need his ESPN login cookies.
    Runs in the fantasy worker thread. Returns the key, or None."""
    import subprocess
    import requests
    key = fantasy_key(url)
    p = path("ffl", key, size)
    if os.path.exists(p):
        return key
    try:
        r = requests.get(url, cookies=cookies, timeout=20, headers={"User-Agent": "Mozilla/5.0 (ballin-board)"})
        r.raise_for_status()
        raw = r.content
        if url.lower().split("?")[0].endswith(".svg") or b"<svg" in raw[:1000]:
            raw = subprocess.run(["rsvg-convert", "-w", "128", "-h", "128", "--keep-aspect-ratio"],
                                 input=raw, capture_output=True, timeout=30, check=True).stdout
        img = _shrink(raw, size)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        tmp = p + ".tmp"
        img.save(tmp, "PNG")
        os.replace(tmp, p)
        with _lock:
            _cache.pop(("ffl", key, size), None)
        return key
    except Exception as exc:
        log.warning("Fantasy logo %s failed: %s", url[:80], exc)
        return None
