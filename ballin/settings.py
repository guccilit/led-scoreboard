"""User settings, stored as JSON and saved atomically (safe against power loss)."""
import copy
import datetime
import json
import re
import logging
import os
import tempfile

log = logging.getLogger(__name__)

DATA_DIR = os.environ.get("BALLIN_DATA_DIR", "/var/lib/ballin-board")
SETTINGS_PATH = os.path.join(DATA_DIR, "settings.json")

DEFAULTS = {
    # Favorite teams per league: lists of ESPN team ids (ids are stable, abbreviations can change)
    "favorites": {"nhl": ["4"], "mlb": ["16"], "nfl": ["3"], "nba": ["4"]},   # Blackhawks, Cubs, Bears, Bulls
    "brightness": 60,
    # TV delay in seconds, per sport (an older single number applies to every sport)
    "delay_seconds": {"nhl": 0, "mlb": 0, "nfl": 0, "nba": 0},
    # Birthdays / anniversaries: {"name": ..., "date": "MM-DD", "type": "birthday"|"anniversary"}
    "events": [],
    # On/off schedule; times are "HH:MM"
    "schedule": {
        "weekday": {"enabled": False, "on": "07:00", "off": "23:00"},
        "weekend": {"enabled": False, "on": "08:00", "off": "23:59"},
        "stay_on_live": True,     # keep the screen on past the off-time while a favorite is playing
    },
    # Slide speed (how long each slide stays up) 1 (slowest) to 10 (fastest); 7 is the original pace
    "speed": 7,
    # Transition speed (push/wipe/fade) 1 (slowest) to 10 (fastest); 7 is the original pace
    "transition_speed": 7,
    # Same, for switching between live games and announcements
    "live_game_seconds": 10,       # how long each live game stays up when several are on
    "final_minutes": 15,           # how long a final score stays up
    "show_rivals": {"nfl": True, "mlb": True, "nhl": True, "nba": True},     # rivals' last / next game slides
    "rival_live": {"nfl": False, "mlb": False, "nhl": False, "nba": False},  # rivals' live games too
    "watch_today": [],             # [{"league", "team", "until"}]: extra teams for a day (several: owner, 2026-10-04)
    "web_name": "scoreboard",      # the settings page is also at http://NAME.local (owner)
    "fantasy": {},                 # fantasy football (fantasy.DEFAULTS fills in the rest)
    "lights": {},                  # smart lights (lights.DEFAULTS fills in the rest)
    # How favorite teams stand out in standings: "gold" (gold text, rest white), "bar", "tint", "arrow", "band"
    "highlight": "gold",
    # Slide transition: "push" (slide/wipe) or "fade"
    "transition": "push",
    # Startup screen for this unit (see ballin/splash.py SPLASHES)
    "splash": "jackson40",
    # BCM pin driving the button's light, or null when the light isn't wired
    "button_led_gpio": None,
}


def _merge(defaults, saved):
    out = copy.deepcopy(defaults)
    for key, value in saved.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _merge(out[key], value)
        else:
            out[key] = value
    return out


def load(path=SETTINGS_PATH):
    """Settings merged over defaults. A missing or damaged file gives the defaults."""
    try:
        with open(path, encoding="utf-8") as f:
            saved = json.load(f)
        if not isinstance(saved, dict):
            raise ValueError("settings file is not an object")
        return _merge(DEFAULTS, saved)
    except FileNotFoundError:
        return copy.deepcopy(DEFAULTS)
    except (OSError, ValueError) as exc:
        log.warning("Could not read %s (%s); using defaults", path, exc)
        return copy.deepcopy(DEFAULTS)


def save(settings, path=SETTINGS_PATH):
    """Write to a temp file, fsync, then rename over the old file."""
    folder = os.path.dirname(path)
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".settings-", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(settings, f, indent=2, sort_keys=True)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        dirfd = os.open(folder, os.O_DIRECTORY)
        try:
            os.fsync(dirfd)
        finally:
            os.close(dirfd)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def mtime(path=SETTINGS_PATH):
    try:
        return os.stat(path).st_mtime_ns
    except OSError:
        return None


def pace(settings):
    """Time multiplier for slide durations and transitions from the 1-10 "speed" setting.

    7 -> 1.0 (original pace, 8 s slides); 1 -> 2.3x longer; 10 -> 0.49x (about 4 s).
    """
    try:
        speed = min(10, max(1, int(settings.get("speed", 7))))
    except (TypeError, ValueError):
        speed = 7
    # Slower side: ~15% per step (1 -> 2.3x). Faster side is steeper (8 -> 0.79x, 10 -> 0.49x).
    return 1.15 ** (7 - speed) if speed <= 7 else 0.79 ** (speed - 7)


def _time(v, default):
    try:
        h, m = (int(x) for x in str(v).split(":"))
        if 0 <= h < 24 and 0 <= m < 60:
            return "%02d:%02d" % (h, m)
    except ValueError:
        pass
    return default


def _int(v, lo, hi, default):
    try:
        return min(hi, max(lo, int(v)))
    except (TypeError, ValueError):
        return default


def clean(current, update):
    """Merge an update from the settings page into current settings, keeping only valid values."""
    out = copy.deepcopy(current)
    if isinstance(update.get("favorites"), dict):
        for league, teams in update["favorites"].items():
            if league in DEFAULTS["favorites"] or league in out.get("favorites", {}):
                if not isinstance(teams, (list, tuple)):
                    teams = [teams] if teams else []
                ids = []
                for t in teams[:8]:
                    if t and str(t) not in ids:
                        ids.append(str(t)[:12])
                out["favorites"][league] = ids
    if "brightness" in update:
        out["brightness"] = _int(update["brightness"], 5, 100, out["brightness"])
    for opt in ("show_rivals", "rival_live"):    # per league (owner); a plain true/false sets all
        if opt not in update:
            continue
        cur = out.get(opt)
        vals = dict(cur) if isinstance(cur, dict) else {k: bool(cur) for k in DEFAULTS[opt]}
        new = update[opt]
        if isinstance(new, dict):
            for league, on in new.items():
                if league in DEFAULTS[opt]:
                    vals[league] = bool(on)
        else:
            vals = {k: bool(new) for k in DEFAULTS[opt]}
        out[opt] = vals
    if isinstance(update.get("folder_order"), list):      # settings page folders, his order
        out["folder_order"] = [str(t)[:40] for t in update["folder_order"]][:30]
    if "web_name" in update:
        name = str(update["web_name"] or "").strip().lower()
        if re.fullmatch(r"[a-z][a-z0-9-]{1,9}", name) and not name.endswith("-"):
            out["web_name"] = name
    if isinstance(update.get("fantasy"), dict):
        from . import fantasy                 # (imported here: fantasy imports settings)
        out["fantasy"] = fantasy.clean(out.get("fantasy"), update["fantasy"])
    if isinstance(update.get("lights"), dict):
        from . import lights
        out["lights"] = lights.clean(out.get("lights"), update["lights"])
    if "watch_today" in update:
        # {"add": {league, team}}, {"remove": {league, team}}, or None to stop them all
        # (a bare {league, team} also adds, as the page used to send)
        w = update["watch_today"]
        rows = _watch_rows(out.get("watch_today"))
        if not w:
            rows = []
        elif isinstance(w, dict):
            add, rm = w.get("add") or (w if "team" in w else None), w.get("remove")
            if rm:
                rows = [r for r in rows if (r["league"], r["team"]) != (rm.get("league"), str(rm.get("team")))]
            if add and add.get("league") in DEFAULTS["favorites"] and add.get("team"):
                team = str(add["team"])[:12]
                # Until 4 AM tomorrow, so a late game isn't cut off
                until = datetime.datetime.combine(datetime.date.today() + datetime.timedelta(days=1), datetime.time(4))
                rows = [r for r in rows if (r["league"], r["team"]) != (add["league"], team)]
                rows.append({"league": add["league"], "team": team, "until": until.isoformat()})
        out["watch_today"] = rows[-12:]
    if "final_minutes" in update:
        out["final_minutes"] = _int(update["final_minutes"], 1, 60, out.get("final_minutes", 15))
    if "live_game_seconds" in update:
        out["live_game_seconds"] = _int(update["live_game_seconds"], 5, 60, out.get("live_game_seconds", 10))
    if "transition_speed" in update:
        out["transition_speed"] = _int(update["transition_speed"], 1, 10, out.get("transition_speed", 7))
    if "speed" in update:
        out["speed"] = _int(update["speed"], 1, 10, out.get("speed", 7))
    if "delay_seconds" in update:
        cur = out.get("delay_seconds")
        delays = dict(cur) if isinstance(cur, dict) else {k: _int(cur, 0, 300, 0) for k in DEFAULTS["delay_seconds"]}
        new = update["delay_seconds"]
        if isinstance(new, dict):
            for league, secs in new.items():
                if league in DEFAULTS["delay_seconds"]:
                    delays[league] = _int(secs, 0, 300, delays.get(league, 0))
        else:
            delays = {k: _int(new, 0, 300, 0) for k in DEFAULTS["delay_seconds"]}
        out["delay_seconds"] = delays
    if update.get("highlight") in ("gold", "bar", "tint", "arrow", "band"):
        out["highlight"] = update["highlight"]
    if update.get("transition") in ("push", "fade"):
        out["transition"] = update["transition"]
    if isinstance(update.get("events"), list):
        events = []
        for ev in update["events"][:50]:
            if not isinstance(ev, dict):
                continue
            name = str(ev.get("name", "")).strip()[:40]
            date = str(ev.get("date", "")).strip()[-5:]
            try:
                month, day = (int(x) for x in date.split("-"))
                ok = 1 <= month <= 12 and 1 <= day <= 31
            except ValueError:
                ok = False
            if name and ok:
                events.append({"name": name, "date": "%02d-%02d" % (month, day),
                               "type": "anniversary" if ev.get("type") == "anniversary" else "birthday"})
        out["events"] = events
    if isinstance(update.get("schedule"), dict):
        for part in ("weekday", "weekend"):
            new = update["schedule"].get(part)
            if isinstance(new, dict):
                cur = out["schedule"][part]
                cur["enabled"] = bool(new.get("enabled", cur["enabled"]))
                cur["on"] = _time(new.get("on", cur["on"]), cur["on"])
                cur["off"] = _time(new.get("off", cur["off"]), cur["off"])
        if "stay_on_live" in update["schedule"]:
            out["schedule"]["stay_on_live"] = bool(update["schedule"]["stay_on_live"])
    return out


def scheduled_on(settings, now=None):
    """Should the screen be on right now according to the on/off schedule?"""
    import datetime
    now = now or datetime.datetime.now()
    part = "weekend" if now.weekday() >= 5 else "weekday"
    sched = (settings.get("schedule") or {}).get(part) or {}
    if not sched.get("enabled"):
        return True
    on, off, t = sched.get("on", "00:00"), sched.get("off", "23:59"), now.strftime("%H:%M")
    if on <= off:
        return on <= t < off
    return t >= on or t < off       # e.g. on 07:00, off 01:00 (past midnight)


SYNC_FLAG = "/run/systemd/timesync/synchronized"   # systemd-timesyncd touches this once the clock is set


def clock_synced():
    """True once the system clock has been set from the network. This Pi has no battery clock,
    so right after a power cut the time is wrong until NTP syncs; don't trust the schedule before then."""
    try:
        return os.path.exists(SYNC_FLAG)
    except OSError:
        return False


def screen_on(settings, favorite_live=False, now=None):
    """Whether the screen should be on, all things considered: the on/off schedule, but kept on
    until the clock is trustworthy (never dark with an unknown time), and kept on past the off-time
    while a favorite is playing when that option is set."""
    if not clock_synced():
        return True
    if scheduled_on(settings, now):
        return True
    if favorite_live and (settings.get("schedule") or {}).get("stay_on_live", True):
        return True
    return False


def transition_pace(settings):
    """Time multiplier for transitions (slides and live games alike) from the 1-10
    "transition_speed" setting: 7 -> 1.0, 1 -> ~3x slower."""
    try:
        speed = min(10, max(1, int(settings.get("transition_speed", 7))))
    except (TypeError, ValueError):
        speed = 7
    return 1.2 ** (7 - speed)


def _watch_rows(w):
    """The watch-today list still running (older settings held a single dict)."""
    rows = [w] if isinstance(w, dict) else list(w or [])
    out = []
    for r in rows:
        try:
            if datetime.datetime.now() < datetime.datetime.fromisoformat(r["until"]):
                out.append({"league": r["league"], "team": str(r["team"]), "until": r["until"]})
        except (KeyError, TypeError, ValueError):
            pass
    return out


def watching_today(settings):
    """The extra teams picked on the settings page for today: [(league, team id)]."""
    return [(r["league"], r["team"]) for r in _watch_rows(settings.get("watch_today"))]


def favorite_ids(settings, league):
    """Favorite team ids for a league as a list (older settings stored a single id), plus
    the team being watched today, if it's in this league."""
    fav = (settings.get("favorites") or {}).get(league)
    ids = [str(f) for f in fav if f] if isinstance(fav, (list, tuple)) else [str(fav)] if fav else []
    for lg, team in watching_today(settings):
        if lg == league and team not in ids:
            ids.append(team)
    return ids


def delay_for(settings, league):
    """TV delay (seconds) for one sport."""
    d = settings.get("delay_seconds") or 0
    if isinstance(d, dict):
        d = d.get(league, 0)
    return _int(d, 0, 300, 0)


def league_option(settings, opt, league):
    """A per-league on/off setting ("show_rivals", "rival_live"); older settings held one bool."""
    v = settings.get(opt, DEFAULTS.get(opt))
    if isinstance(v, dict):
        return bool(v.get(league, (DEFAULTS.get(opt) or {}).get(league, False)))
    return bool(v)
