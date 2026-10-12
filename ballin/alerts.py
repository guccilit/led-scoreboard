"""Email alerts to the owner when something is wrong with the board.

alert(key, subject, body) can be called from anywhere. Alerts are de-duplicated
by key (same problem at most once every REPEAT_AFTER), batched, capped per day,
and sent by AlertWorker when the internet is up. Any ERROR logged anywhere in
the app also becomes an alert (AlertLogHandler).

Credentials live in DATA_DIR/secrets.json (never in git):
  {"smtp_user": "board@gmail.com", "smtp_password": "<gmail app password>",
   "alert_to": "owner@example.com"}
"""
import datetime
import json
import logging
import os
import smtplib
import socket
import subprocess
import threading
import time
from email.message import EmailMessage

from .settings import DATA_DIR
from .workers import Worker

log = logging.getLogger(__name__)

SECRETS_PATH = os.path.join(DATA_DIR, "secrets.json")
SENT_PATH = os.path.join(DATA_DIR, "alerts_sent.json")
REPEAT_AFTER = 6 * 3600
DAILY_MAX = 12

_lock = threading.Lock()
_queue = []          # [(time, key, subject, body)]
_sent = None         # key -> last sent time (persisted)


def _load_sent():
    global _sent
    if _sent is None:
        try:
            with open(SENT_PATH) as f:
                _sent = json.load(f)
        except Exception:
            _sent = {}
    return _sent


def alert(key, subject, body=""):
    """Report a problem. Safe to call often: repeats are dropped."""
    with _lock:
        sent = _load_sent()
        if time.time() - sent.get(key, 0) < REPEAT_AFTER or any(q[1] == key for q in _queue):
            return
        _queue.append((time.time(), key, subject, body))
    log.warning("ALERT %s: %s", key, subject)


class AlertLogHandler(logging.Handler):
    """Turns ERROR log records (incl. exceptions in workers) into alerts."""

    def __init__(self):
        super().__init__(logging.ERROR)

    def emit(self, record):
        if record.name.startswith(__name__):
            return
        try:
            msg = record.getMessage()
            body = self.format(record)
            alert("log:%s:%s" % (record.name, msg.splitlines()[0][:80]), "Error: " + msg[:100], body)
        except Exception:
            pass


def _secrets():
    try:
        with open(SECRETS_PATH) as f:
            s = json.load(f)
        if s.get("smtp_user") and s.get("smtp_password"):
            return s
    except Exception:
        pass
    return None


def _recent_log(lines=40):
    try:
        return subprocess.run(["journalctl", "-u", "ballin-board", "-n", str(lines), "--no-pager", "-o", "short"],
                              capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return "(log unavailable)"


def send_email(subject, body, secrets=None):
    s = secrets or _secrets()
    if not s:
        raise RuntimeError("email not configured (%s missing)" % SECRETS_PATH)
    msg = EmailMessage()
    msg["From"] = s["smtp_user"]
    msg["To"] = s.get("alert_to") or s["smtp_user"]
    msg["Subject"] = subject
    msg.set_content(body)
    with smtplib.SMTP_SSL(s.get("smtp_host", "smtp.gmail.com"), int(s.get("smtp_port", 465)), timeout=20) as smtp:
        smtp.login(s["smtp_user"], s["smtp_password"])
        smtp.send_message(msg)


class AlertWorker(Worker):
    interval = 60.0

    def __init__(self, state, stop):
        super().__init__(state, stop)
        self.warned_unconfigured = False

    def step(self):
        with _lock:
            pending = list(_queue)
        if not pending or not self.state.online:
            return
        secrets = _secrets()
        if not secrets:
            if not self.warned_unconfigured:
                log.info("Alerts pending but email isn't set up yet (%s)", SECRETS_PATH)
                self.warned_unconfigured = True
            return
        sent = _load_sent()
        today = datetime.date.today().isoformat()
        if sent.get("_day") != today:
            sent["_day"], sent["_count"] = today, 0
        if sent["_count"] >= DAILY_MAX:
            return
        from .api import UNIT_ID   # late import: api imports this module
        host = socket.gethostname()
        subject = "[Ballin' Board %s] %s" % (host, pending[0][2] if len(pending) == 1
                                             else "%d issues: %s" % (len(pending), pending[0][2]))
        parts = ["Board: %s (unit %s)" % (host, UNIT_ID), ""]
        for t, key, subj, body in pending:
            when = datetime.datetime.fromtimestamp(t).strftime("%a %b %d %I:%M %p")
            parts += ["== %s  [%s]" % (subj, when), body.strip(), ""]
        parts += ["-- Recent log --", _recent_log()]
        send_email(subject, "\n".join(parts), secrets)
        log.info("Sent alert email (%d issue%s)", len(pending), "s" if len(pending) > 1 else "")
        now = time.time()
        with _lock:
            for item in pending:
                _queue.remove(item)
                sent[item[1]] = now
        sent["_count"] += 1
        tmp = SENT_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(sent, f)
        os.replace(tmp, SENT_PATH)


SAG_ALERT = 4          # supply dips in a day before emailing


class HealthWorker(Worker):
    """Power, temperature, disk space, internet outages and stale data."""

    interval = 300.0

    def __init__(self, state, stop):
        super().__init__(state, stop)
        self.down_since = None

    def step(self):
        try:
            out = subprocess.run(["vcgencmd", "get_throttled"], capture_output=True, text=True, timeout=5).stdout
            bits = int(out.strip().split("=")[1], 16)
            # Only repeated dips are worth an email: a single brief one (e.g. at power-up) is
            # harmless, and PowerGuard already dims the panel for it (owner, 2026-10-02).
            day = [t for t in self.state.sags if time.monotonic() - t < 86400]
            if len(day) >= SAG_ALERT or bits & 0x4:
                alert("power", "Power supply problem (under-voltage)",
                      "The supply dipped %d times in the last day%s (get_throttled=%s). "
                      "Check the 5V 4A supply and cable, or lower the brightness."
                      % (len(day), ", and the Pi is slowing itself down now" if bits & 0x4 else "", hex(bits)))
        except Exception:
            pass
        try:
            with open("/sys/class/thermal/thermal_zone0/temp") as f:
                temp = int(f.read()) / 1000
            if temp >= 80:
                alert("hot", "Pi is running hot (%.0f C)" % temp,
                      "CPU temperature is %.0f C. Check ventilation in the case." % temp)
        except Exception:
            pass
        try:
            st = os.statvfs(DATA_DIR)
            if st.f_bavail / max(1, st.f_blocks) < 0.1:
                alert("disk", "Storage almost full", "Less than 10% free in %s." % DATA_DIR)
        except Exception:
            pass

        if self.state.online is False and self.down_since is None:
            # Monotonic: right after power-up the clock is hours behind until the internet
            # sets it, which made a short outage look like an all-night one.
            self.down_since = time.monotonic()
        elif self.state.online and self.down_since:
            gone = time.monotonic() - self.down_since
            if gone > 1800:
                alert("offline", "Internet was down for %d minutes" % (gone // 60),
                      "The board had no internet from %s until now."
                      % datetime.datetime.fromtimestamp(time.time() - gone).strftime("%a %I:%M %p"))
            self.down_since = None

        if self.state.online:
            for key, teams in self.state.leagues.items():
                # Measured with the monotonic clock from the last good refresh (or from the
                # program's start): the wall clock jumps hours at power-up, which made these fire
                # falsely after every boot.
                age = time.monotonic() - self.state.fresh.get(key, self.state.started)
                if any(d.in_window for d in teams) and age > 2 * 3600:
                    alert("stale:" + key, "%s data hasn't updated in %d hours" % (key.upper(), age // 3600),
                          "The board is online but hasn't received fresh %s data from ESPN." % key.upper())
