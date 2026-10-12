"""Background workers. They never touch the panel; they only update State."""
import logging
import os
import socket
import threading
import time

from . import settings as settings_mod

log = logging.getLogger(__name__)


class Worker(threading.Thread):
    """Runs step() every `interval` seconds; an exception is logged, never fatal."""

    interval = 10.0

    def __init__(self, state, stop):
        super().__init__(name=type(self).__name__, daemon=True)
        self.state = state
        self.stop = stop
        self._wake = threading.Event()
        state.workers.append(self)

    def wake(self):
        """Run step() now instead of waiting for the interval."""
        self._wake.set()

    def run(self):
        while not self.stop.is_set():
            try:
                self.step()
            except Exception as exc:
                if isinstance(exc, OSError) or "requests" in type(exc).__module__:
                    log.warning("%s: %s", self.name, exc)   # network trouble: expected, keep it short
                else:
                    log.exception("%s failed", self.name)
                self.interval = min(self.interval, 60)
            self._wake.wait(self.interval)
            self._wake.clear()

    def step(self):
        raise NotImplementedError


def lan_ip():
    """This board's address on the home network (not the setup hotspot), or "" when offline."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))      # no packet is sent; picks the outgoing interface
            ip = s.getsockname()[0]
        return "" if ip.startswith(("127.", "10.42.0.")) else ip
    except OSError:
        return ""


class NetworkWatcher(Worker):
    """Is ESPN reachable?"""

    HOST = ("site.api.espn.com", 443)

    def step(self):
        self.state.lan_ip = lan_ip()
        try:
            with socket.create_connection(self.HOST, timeout=4):
                ok = True
        except OSError:
            ok = False
        if ok != self.state.online:
            log.info("Internet %s", "up" if ok else "down")
            self.state.online_since = time.monotonic()
        self.state.online = ok
        self.interval = 30.0 if ok else 5.0


class SettingsWatcher(Worker):
    """Reloads settings when the file changes, so changes apply without a restart."""

    interval = 2.0

    def __init__(self, state, stop):
        super().__init__(state, stop)
        self.seen = settings_mod.mtime()

    def step(self):
        m = settings_mod.mtime()
        if m != self.seen:
            self.seen = m
            self.state.settings = settings_mod.load()
            log.info("Settings reloaded")
            for w in self.state.workers:
                if w is not self:
                    w.wake()


class NetModeWatcher(Worker):
    """Picks up the WiFi setup hotspot status published by the settings-page service."""

    interval = 2.0

    SHOW_FILE = "/run/ballin-show.request"     # written by the settings page

    def step(self):
        from . import netsetup
        self.state.netmode = netsetup.read_mode()
        try:
            with open(self.SHOW_FILE) as f:
                what = f.read().strip()
            os.unlink(self.SHOW_FILE)
            if what:
                self.state.show_request = what
        except OSError:
            pass
        what, self.state.show_request = self.state.show_request, None
        if what:
            # Built here, not in the render loop, so the panel never stutters.
            from . import splash
            try:
                self.state.show_ready = splash.create_show(what, self.state.settings.get("splash"))
                log.info("Playing %s", what)
            except Exception:
                log.exception("Could not build %s", what)


class PowerGuard(Worker):
    """Dims the panel when the Pi reports under-voltage (the panel shares the 5 V supply),
    then eases back once the power has been steady. state.power_cap multiplies brightness."""

    interval = 2.0
    STEP_DOWN = 0.15
    FLOOR = 0.5
    STEP_UP = 0.05
    CALM = 300          # seconds without a sag before stepping back up

    def __init__(self, state, stop):
        super().__init__(state, stop)
        self.last_sag = 0.0
        self.last_up = 0.0

    def step(self):
        import subprocess
        try:
            out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=5).stdout
            bits = int(out.strip().split("=")[1], 16)
        except Exception:
            self.interval = 60
            return
        now = time.monotonic()
        cap = self.state.power_cap
        if bits & 0x1:                                  # under-voltage right now
            if now - self.last_sag > 4:
                cap = max(self.FLOOR, cap - self.STEP_DOWN)
                log.warning("Under-voltage: dimming panel to %d%% of the set brightness", cap * 100)
                self.state.sags.append(now)                # for the health email (repeated dips only)
            self.last_sag = now
        elif cap < 1.0 and now - self.last_sag > self.CALM and now - self.last_up > self.CALM:
            cap = min(1.0, cap + self.STEP_UP)
            self.last_up = now
            log.info("Power steady: panel back up to %d%%", cap * 100)
        self.state.power_cap = cap
