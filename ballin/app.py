"""Main program: render loop on the main thread, data workers in the background.

The render loop never waits on the network, disk or parsing: it only reads
State and draws. Run with:  python -m ballin
"""
import logging
import math
import os
import signal
import threading
import time

import numpy as np

from . import alerts, api, gfx, screens, settings as settings_mod, splash
from .button import HOLD_SHOW, HOLD_SHUTDOWN, RESET_HOLD, Button
from .display import Display
from .leagues import LEAGUES
from .fantasy import FantasyWorker
from .lights import LightsWorker
from .live import LiveFetcher, LivePresenter
from .slides import SlidesWorker
from .sports import LeagueWorker
from .state import State
from .workers import NetModeWatcher, NetworkWatcher, PowerGuard, SettingsWatcher

log = logging.getLogger("ballin")

FPS = 40
MAX_LOAD = 0.13   # mean panel load (fraction of all LEDs full white x brightness); splash flash peaks ~0.16
SHOW_MAX_LOAD = 0.14   # during shows (startup, intros, songs): 0.10 after brief supply dips; 0.14 so the
                       # daylight scenes aren't dimmed to dusk (owner, 2026-10-02). PowerGuard still dims on dips.
WIPE_BAR = (235, 235, 245)   # line that sweeps down when switching sports (owner: white, not league colors)
SHUTDOWN_COUNTDOWN = 30   # "SAFE TO UNPLUG IN n" after a button shutdown starts powering off
# Both live in /run, which is wiped at power-off.
SPLASH_MARKER = "/run/ballin-board-splash-played"   # splash plays once per boot
RUN_MARKER = "/run/ballin-board-running"            # still there at start = we crashed


class Rotator:
    """Cycles through state.slides with a transition between slides.

    "push" (default): within a league the new slide pushes the old one off to the left;
    into a new league a wipe sweeps down behind a bar in the league's color.
    "fade": a short crossfade.
    """

    TIME = {"push": 0.45, "fade": 0.35}
    LEAGUE_BAR = {"nhl": (210, 210, 225), "mlb": (235, 35, 50), "nfl": (50, 100, 255), "nba": (255, 120, 20)}


    def __init__(self):
        self.cur = None
        self.prev = None
        self.start = 0.0
        self.prev_t = 0.0

    def frame(self, slides, now, style="push", pace=1.0):
        if not slides:
            self.cur = self.prev = None
            return None
        keys = [s.key for s in slides]
        if self.cur is not None and self.cur.key in keys:
            # Same slide, possibly rebuilt with fresh data (live game views): swap it in.
            self.cur = slides[keys.index(self.cur.key)]
        gone = self.cur is not None and self.cur.key not in keys   # e.g. an announcement started/ended
        if self.cur is None or gone or now - self.start >= self.cur.duration:
            i = (keys.index(self.cur.key) + 1) % len(slides) if self.cur and self.cur.key in keys else 0
            if self.cur is not None and slides[i].key == self.cur.key:
                self.start = now            # only one slide: keep showing it, no transition
                self.prev = None            # (and don't replay the last transition into it)
            else:
                self.prev, self.prev_t = self.cur, (now - self.start)
                self.cur, self.start = slides[i], now
        t = now - self.start
        frame = self.cur.render(t)
        span = self.TIME.get(style, 0.4) * pace
        if self.prev is None or t >= span:
            return frame
        old = self.prev.render(self.prev_t + t)
        u = gfx.ease_out_cubic(t / span)
        if style == "fade":
            return old * (1 - t / span) + frame * (t / span)
        league, prev_league = self.cur.key.split(":")[0], self.prev.key.split(":")[0]
        # The wipe only marks switching sports in the regular slides; live games, announcements,
        # one sport and birthdays push.
        regular = ":live:" not in self.cur.key and ":live:" not in self.prev.key
        if regular and league != prev_league and league in self.LEAGUE_BAR and prev_league in self.LEAGUE_BAR:
            # New league: wipe down, new slide above a soft white bar.
            y = int(round(u * (gfx.H + 2)))
            out = np.array(old, np.float32)
            out[:y] = frame[:y]
            bar = np.array(WIPE_BAR, np.float32)
            for dy, level in ((0, 1.0), (1, 0.35), (-1, 0.35)):
                if 0 <= y + dy < gfx.H:
                    out[y + dy] = bar * level
            return out
        dx = int(round(u * gfx.W))
        out = np.empty((gfx.H, gfx.W, 3), np.float32)
        out[:, :gfx.W - dx] = old[:, dx:]
        out[:, gfx.W - dx:] = frame[:, :dx]
        return out


IDLE_GRACE = 30.0       # seconds after start before "nothing to show" means the clock
BLANK = gfx.new_frame()


def _idle_screen(state, t):
    if state.online is False:
        return screens.status("NO INTERNET", "RECONNECTING", t)
    if state.online is None:
        return screens.status("STARTING UP", "ONE MOMENT", t)
    if time.monotonic() - state.started < IDLE_GRACE:
        return BLANK        # slides are still loading: don't flash the clock first
    return screens.clock()


class App:
    def __init__(self):
        self.stop = threading.Event()
        self.state = State(settings_mod.load())
        self.brightness = self.state.settings["brightness"]
        self.display = Display(self.brightness)
        self.rotator = Rotator()
        self.last_src = None
        self.splash = None
        self.splash_t0 = time.monotonic()
        self.boot_splash = False
        if not os.path.exists(SPLASH_MARKER):
            self.splash = splash.create(self.state.settings.get("splash"))
            self.boot_splash = True
            self._touch(SPLASH_MARKER)
        if os.path.exists(RUN_MARKER):
            alerts.alert("crash", "Board program crashed and restarted",
                         "The scoreboard program stopped unexpectedly and systemd restarted it. "
                         "See the recent log below.")
        self._touch(RUN_MARKER)

    @staticmethod
    def _touch(path):
        try:
            open(path, "w").close()
        except OSError:
            log.warning("Could not write %s", path)

    def run(self):
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, self._on_signal)
        workers = [NetworkWatcher(self.state, self.stop), SettingsWatcher(self.state, self.stop),
                   NetModeWatcher(self.state, self.stop), PowerGuard(self.state, self.stop)]
        workers += [LeagueWorker(self.state, self.stop, key) for key in LEAGUES]
        workers += [SlidesWorker(self.state, self.stop), LiveFetcher(self.state, self.stop),
                    LivePresenter(self.state, self.stop), alerts.AlertWorker(self.state, self.stop),
                    alerts.HealthWorker(self.state, self.stop), FantasyWorker(self.state, self.stop),
                    LightsWorker(self.state, self.stop)]
        for w in workers:
            w.start()
        Button(self.state, self.stop).start()

        start = time.monotonic()
        frame_no = 0
        blank = gfx.to_uint8(gfx.new_frame())
        next_stats = time.monotonic() + 3600
        sched_on, next_sched = True, 0.0
        try:
            while not self.stop.is_set():
                now = time.monotonic()
                t = frame_no / FPS
                self._apply_settings()
                st = self.state
                if now >= next_sched:          # on/off schedule, checked once a second
                    was, next_sched = sched_on, now + 1.0
                    sched_on = settings_mod.screen_on(st.settings, self._favorite_live())
                    if sched_on != was:
                        st.screen_override = None      # a scheduled change ends a button override
                st.screen_visible = sched_on if st.screen_override is None else st.screen_override
                if st.show_ready:                      # settings page button or a game starting
                    show, st.show_ready = st.show_ready, None
                    # Only the power-up show itself, while it's actually playing, holds a request off.
                    booting = self.boot_splash and self.splash is not None and \
                        now - self.splash_t0 <= self.splash.duration
                    if st.screen_visible and not booting:
                        self.boot_splash = False
                        self.splash, self.splash_t0 = show, now
                if st.reset_started:
                    frame = screens.factory_reset_countdown(RESET_HOLD - (now - st.reset_started))
                elif now < st.reset_done_until:
                    frame = screens.factory_reset_done()
                elif st.shutting_down:
                    if st.safe_to_unplug:
                        left = SHUTDOWN_COUNTDOWN - (now - st.safe_to_unplug)
                        if left < -2:
                            break        # countdown done: step aside so the Pi finishes powering off
                        frame = screens.safe_to_unplug(max(0, math.ceil(left)))
                    else:
                        frame = screens.shutting_down(now - st.shutting_down)
                elif st.hold_started:
                    held = now - st.hold_started
                    frame = screens.hold_to_shutdown((held - HOLD_SHOW) / (HOLD_SHUTDOWN - HOLD_SHOW))
                elif self.splash and now - self.splash_t0 <= self.splash.duration:
                    frame = self.splash.render(now - self.splash_t0)
                elif not st.screen_visible:
                    frame = screens.sleeping() if now < st.sleep_msg_until else blank
                elif st.netmode.get("mode") == "setup":
                    self.splash, self.boot_splash = None, False
                    frame = screens.wifi_setup(t, bool(st.netmode.get("last_error")))
                else:
                    if self.splash is not None:
                        self.splash = None
                        if self.boot_splash:
                            self.boot_splash = False
                            st.card_since = now      # after the power-up show: how to reach settings
                    if st.card_since is not None and now - st.card_since < screens.SETTINGS_CARD_SECS:
                        frame = screens.settings_card(now - st.card_since, st.lan_ip, st.settings.get("web_name") or "scoreboard")
                    else:
                        showing = st.photo_only or ([st.live_alert] if st.live_alert else st.live_slides or st.slides)
                        f = self.rotator.frame(showing, now, st.settings.get("transition", "push"),
                                               settings_mod.transition_pace(st.settings))
                        frame = f if f is not None else _idle_screen(self.state, t)
                # Static screens hand back the very same array each frame: nothing new to
                # draw, the panel keeps refreshing the last picture by itself (saves CPU/heat).
                if frame is not self.last_src:
                    self.last_src = frame
                    self.display.show(self._limit(frame if frame.dtype == np.uint8 else gfx.to_uint8(frame)))
                frame_no += 1
                if now > next_stats:
                    next_stats = now + 3600
                    log.info("ESPN requests: %s", api.client.stats())
                delay = start + frame_no / FPS - time.monotonic()
                if delay > 0:
                    time.sleep(delay)
                elif delay < -1.0:
                    # Fell far behind (e.g. a busy power-up): resync instead of racing. Shows
                    # keep playing (dropping them here cut the startup show short; owner).
                    start, frame_no = time.monotonic(), 0
        finally:
            self.display.clear()
            try:
                os.unlink(RUN_MARKER)
            except OSError:
                pass
            log.info("Stopped")

    def _limit(self, frame):
        """Keep very bright frames (big white flashes) under a safe power draw for the 5 V
        supply the panel shares with the Pi. Normal slides are far below the limit."""
        # A show can ask for more (the daylight home run, tested with no supply dips)
        if self.splash is not None:
            cap = getattr(self.splash, "max_load", SHOW_MAX_LOAD)
        else:                                        # e.g. a home run announcement during a game
            cap = getattr(self.rotator.cur, "max_load", None) or MAX_LOAD
        load = frame.mean() / 255 * self.brightness / 100
        if load > cap:
            frame = (frame * (cap / load)).astype(np.uint8)
        return frame

    def _on_signal(self, *_):
        # During a button shutdown, keep the countdown on screen; the loop ends by itself.
        if not self.state.shutting_down:
            self.stop.set()

    def _favorite_live(self):
        """True while a favorite team (or a watch-today team) is in a game that's in progress."""
        st = self.state
        for tr in st.live_tracked.values():
            g = tr.visible.game if tr.visible else None
            if g is not None and tr.final_at is None and getattr(g, "state", "") == "in":
                favs = set(settings_mod.favorite_ids(st.settings, tr.league))
                if favs & {g.home.team_id, g.away.team_id}:
                    return True
        return False

    def _apply_settings(self):
        # Set brightness, lowered by PowerGuard while the supply is sagging.
        b = max(1, round(self.state.settings.get("brightness", 60) * self.state.power_cap))
        if b != self.brightness:
            self.brightness = b
            self.display.set_brightness(b)
            self.last_src = None   # brightness applies when drawing: redraw now


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    handler = alerts.AlertLogHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
    logging.getLogger().addHandler(handler)
    log.info("Starting LED Sports Scoreboard")
    App().run()
