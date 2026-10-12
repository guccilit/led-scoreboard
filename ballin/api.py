"""Polite HTTP client for ESPN's public (unofficial, undocumented) API.

ESPN publishes no rate limit, so we hold ourselves to conservative rules:
  * a hard ceiling on requests per hour (HOURLY_CAP)
  * a minimum gap between any two requests (MIN_GAP)
  * never re-fetch the same URL while ESPN's own cache (max-age) is fresh
  * exponential back-off when ESPN errors or says "slow down" (429/403/5xx)
  * each unit has its own id; its polling intervals are scaled by a small
    unit-specific factor plus random jitter, so several boards never hit ESPN
    in lock-step, and it identifies itself honestly in the User-Agent.
"""
import collections
import logging
import os
import random
import threading
import time
import uuid

import requests

from . import alerts
from .settings import DATA_DIR

log = logging.getLogger(__name__)

TIMEOUT = 12
MIN_GAP = 1.0           # s between requests
HOURLY_CAP = 900        # hard ceiling; normal use is ~70/h idle, ~400/h during a live game
MIN_AGE = 5             # s; ESPN's CDN caches 5 s, faster polling is pointless
BACKOFF_START = 60
BACKOFF_MAX = 1800


class Throttled(Exception):
    """We chose not to make the request (backing off or over budget)."""


def _unit_id():
    path = os.path.join(DATA_DIR, "unit_id")
    try:
        with open(path) as f:
            uid = f.read().strip()
            if uid:
                return uid
    except OSError:
        pass
    uid = uuid.uuid4().hex[:8]
    try:
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(path + ".tmp", "w") as f:
            f.write(uid)
            f.flush()
            os.fsync(f.fileno())
        os.replace(path + ".tmp", path)
    except OSError:
        pass
    return uid


UNIT_ID = _unit_id()
# Unit-specific pacing factor between 0.92 and 1.08.
UNIT_FACTOR = 0.92 + (int(UNIT_ID, 16) % 1000) / 1000 * 0.16
USER_AGENT = "BallinBoard/1.0 (home LED scoreboard; unit %s)" % UNIT_ID


def interval(base):
    """A polling interval adjusted for this unit, with a little randomness."""
    return base * UNIT_FACTOR * random.uniform(0.95, 1.05)


class _Client:
    def __init__(self):
        self.lock = threading.Lock()
        self.last = 0.0
        self.recent = collections.deque()
        self.backoff = 0
        self.backoff_until = 0.0
        self.cache = {}
        self.total = 0
        self.errors = 0
        self.local = threading.local()

    def _session(self):
        s = getattr(self.local, "session", None)
        if s is None:
            s = self.local.session = requests.Session()
            s.headers["User-Agent"] = USER_AGENT
        return s

    def get(self, url, params=None, binary=False):
        key = url + "?" + "&".join("%s=%s" % kv for kv in sorted((params or {}).items()))
        with self.lock:
            now = time.time()
            hit = self.cache.get(key)
            if hit and now - hit[0] < hit[1]:
                return hit[2]
            if now < self.backoff_until:
                raise Throttled("backing off %.0fs more" % (self.backoff_until - now))
            while self.recent and now - self.recent[0] > 3600:
                self.recent.popleft()
            if len(self.recent) >= HOURLY_CAP:
                alerts.alert("api-cap", "Hit hourly ESPN request cap",
                             "The board made %d requests in the last hour and paused itself." % HOURLY_CAP)
                raise Throttled("hourly cap reached")
            wait = self.last + MIN_GAP + random.uniform(0, 0.3) - now
            if wait > 0:
                time.sleep(wait)
            self.last = time.time()
            self.recent.append(self.last)
            self.total += 1
            for k in [k for k, v in self.cache.items() if self.last - v[0] > 600]:
                del self.cache[k]

        try:
            r = self._session().get(url, params=params, timeout=TIMEOUT)
        except requests.RequestException:
            with self.lock:
                self.errors += 1
            raise
        if r.status_code == 429 or r.status_code == 403 or r.status_code >= 500:
            self._back_off(r)
            r.raise_for_status()
        r.raise_for_status()
        data = r.content if binary else r.json()
        with self.lock:
            self.backoff = 0
            self.cache[key] = (time.time(), self._max_age(r), data)
        return data

    @staticmethod
    def _max_age(r):
        cc = r.headers.get("Cache-Control", "")
        for part in cc.split(","):
            part = part.strip()
            if part.startswith("max-age="):
                try:
                    return max(MIN_AGE, min(int(part[8:]), 300))
                except ValueError:
                    pass
        return MIN_AGE

    def _back_off(self, r):
        with self.lock:
            self.errors += 1
            self.backoff = min(BACKOFF_MAX, max(BACKOFF_START, self.backoff * 2))
            wait = self.backoff
            ra = r.headers.get("Retry-After", "")
            if ra.isdigit():
                wait = max(wait, min(int(ra), BACKOFF_MAX * 2))
            self.backoff_until = time.time() + wait
        log.warning("ESPN returned %s; backing off %ds", r.status_code, wait)
        if r.status_code in (429, 403):
            alerts.alert("api-limited", "ESPN is limiting the board (HTTP %s)" % r.status_code,
                         "ESPN answered %s for %s. The board is backing off for %d seconds."
                         % (r.status_code, r.url, wait))

    def stats(self):
        with self.lock:
            now = time.time()
            return {"last_hour": sum(1 for t in self.recent if now - t <= 3600), "total": self.total,
                    "errors": self.errors, "backing_off": max(0, int(self.backoff_until - now)),
                    "unit": UNIT_ID}


client = _Client()


def get_json(url, params=None):
    return client.get(url, params)


def get_bytes(url):
    return client.get(url, binary=True)
