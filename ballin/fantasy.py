"""Fantasy football on the board (owner, 2026-10-03): your matchups, your starters' big
plays, each week's result, and a League Champion celebration for a week when he wins one.

Sleeper (public data: only his username is needed) and ESPN (league ids; private leagues
need his two ESPN login cookies, kept in secrets.json) here; Yahoo (OAuth) in fantasy_yahoo.py.
A matchup is a plain dict the slides and the live presenter read:

  {"platform", "league_id", "league", "week", "status": "live"|"upcoming"|"final",
   "me": {"name", "points", "starters": {player id: points}},
   "opp": {...} or None}

FantasyWorker runs in its own thread (like every data worker): anything going wrong here is
logged and never touches the display. It polls every minute while NFL games are on and every
15 minutes otherwise; Sleeper allows far more, and asks for the big player list at most daily.
"""
import json
import logging
import os
import re
import time

import requests

from .settings import DATA_DIR
from .workers import Worker

log = logging.getLogger(__name__)

SLEEPER = "https://api.sleeper.app/v1/"
PLAYERS_PATH = os.path.join(DATA_DIR, "sleeper_players.json")
CHAMPS_PATH = os.path.join(DATA_DIR, "fantasy_champions.json")
CHAMP_DAYS = 7
RESULT_HOURS = 24

DEFAULTS = {
    "sleeper_username": "",
    "sleeper_leagues": [],        # league ids to follow; empty = all of his leagues
    "espn_leagues": [],           # [{"id": ESPN league id, "team": his team id or "" = found by his login}]
    "yahoo_leagues": [],          # Yahoo league keys to follow; empty = all of his (once he's signed in)
    "matchup": "gamedays",        # matchup slides: "gamedays" (Thu/Sun/Mon), "always", "off"
    "live": True,                 # his matchups in the live rotation during Bears/NFL games
    "live_every": 0,              # ...how often during live games: 0 = every time through, else minutes
    "live_seconds": 0,            # ...and how long each time it shows: 0 = the normal live-game time, else seconds
    "alerts": True,               # announce his starters' big plays
    "alert_min": 6.0,             # ...only plays worth at least this many points
    "opp_alerts": False,          # ...and his opponents' (low-key style)
    "result": True,               # the week's result, for a day
    "champion": True,             # League Champion celebration for a week
}


def options(settings):
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (settings.get("fantasy") or {}).items() if k in DEFAULTS})
    return out


def clean(current, update):
    """Merge a settings-page update into the fantasy options, keeping only valid values."""
    cur = options({"fantasy": current or {}})
    for k, v in (update or {}).items():
        if k == "sleeper_username":
            cur[k] = str(v or "").strip()[:40]
        elif k == "sleeper_leagues" and isinstance(v, list):
            cur[k] = [str(x)[:32] for x in v if str(x).isdigit()][:20]
        elif k == "yahoo_leagues" and isinstance(v, list):
            cur[k] = [str(x)[:32] for x in v if re.fullmatch(r"\d+\.l\.\d+", str(x))][:20]
        elif k == "espn_leagues" and isinstance(v, list):
            rows = []
            for x in v[:20]:
                if not isinstance(x, dict):
                    continue
                lid, team = str(x.get("id", "")).strip(), str(x.get("team") or "").strip()
                if lid.isdigit() and lid not in [r["id"] for r in rows]:
                    rows.append({"id": lid[:16], "team": team[:6] if team.isdigit() else ""})
            cur[k] = rows
        elif k == "matchup" and v in ("gamedays", "always", "off"):
            cur[k] = v
        elif k == "alert_min":
            try:
                cur[k] = min(50.0, max(0.5, float(v)))
            except (TypeError, ValueError):
                pass
        elif k == "live_every":
            try:
                cur[k] = max(0, min(180, int(v)))
            except (TypeError, ValueError):
                pass
        elif k == "live_seconds":
            try:
                cur[k] = 0 if int(v) == 0 else max(5, min(300, int(v)))
            except (TypeError, ValueError):
                pass
        elif k in ("live", "alerts", "opp_alerts", "result", "champion"):
            cur[k] = bool(v)
    return cur


# ------------------------------------------------------------------ Sleeper
_session = requests.Session()
_session.headers["User-Agent"] = "ballin-board (personal scoreboard)"


def _get(path):
    r = _session.get(SLEEPER + path, timeout=15)
    r.raise_for_status()
    return r.json()


def sleeper_user(username):
    u = _get("user/%s" % username)
    return u if isinstance(u, dict) and u.get("user_id") else None


def sleeper_leagues(user_id, season):
    """His NFL leagues this season: [{"league_id", "name", "status"}]."""
    return [{"league_id": l["league_id"], "name": l.get("name") or "League", "status": l.get("status")}
            for l in _get("user/%s/leagues/nfl/%s" % (user_id, season)) or []]


class _Players:
    """Sleeper player id -> "D. Swift" (fetched at most once a day, as Sleeper asks; kept small)."""

    def __init__(self):
        self.names, self.loaded = {}, 0.0

    def get(self, pid):
        if not self.names:
            self._load()
        return self.names.get(str(pid)) or str(pid)

    def _load(self):
        try:
            with open(PLAYERS_PATH) as f:
                d = json.load(f)
            self.names, self.loaded = d["names"], d["fetched"]
        except (OSError, ValueError, KeyError):
            pass
        if time.time() - self.loaded > 86400:
            try:
                raw = _get("players/nfl")
                names = {}
                for pid, p in raw.items():
                    if p.get("position") == "DEF" or pid.isalpha():
                        names[pid] = "%s D/ST" % (p.get("team") or pid)
                    else:
                        first, last = p.get("first_name") or "", p.get("last_name") or ""
                        names[pid] = ("%s. %s" % (first[:1], last)).strip(". ") if first else last
                self.names, self.loaded = names, time.time()
                tmp = PLAYERS_PATH + ".tmp"
                with open(tmp, "w") as f:
                    json.dump({"fetched": self.loaded, "names": names}, f)
                os.replace(tmp, PLAYERS_PATH)
                log.info("Sleeper players: %d names", len(names))
            except Exception as exc:
                log.warning("Sleeper players list: %s", exc)


players = _Players()


def _team_name(users, owner_id):
    u = users.get(owner_id) or {}
    return ((u.get("metadata") or {}).get("team_name") or u.get("display_name") or "TEAM").upper()


def initials(name):
    """A short label for a fantasy team without one (shown when its logo can't be drawn)."""
    words = [w for w in name.replace("'", "").split() if w[:1].isalnum()]
    return ("".join(w[0] for w in words)[:4] if len(words) > 1 else name[:4]).upper() or "?"


def _sleeper_logo(users, owner_id):
    u = users.get(owner_id) or {}
    url = (u.get("metadata") or {}).get("avatar")
    if url and url.startswith("http"):
        return url
    return "https://sleepercdn.com/avatars/thumbs/%s" % u["avatar"] if u.get("avatar") else None


def sleeper_matchup(league, user_id, week):
    """His matchup in one league this week, as the module docstring's dict (or None)."""
    lid = league["league_id"]
    rosters = _get("league/%s/rosters" % lid) or []
    users = {u["user_id"]: u for u in _get("league/%s/users" % lid) or []}
    mine = next((r for r in rosters if r.get("owner_id") == user_id or user_id in (r.get("co_owners") or [])), None)
    if not mine:
        return None
    owners = {r["roster_id"]: r.get("owner_id") for r in rosters}
    games = _get("league/%s/matchups/%d" % (lid, week)) or []
    me = next((m for m in games if m.get("roster_id") == mine["roster_id"]), None)
    if not me:
        return None
    opp = next((m for m in games if m.get("matchup_id") is not None and m.get("matchup_id") == me.get("matchup_id")
                and m.get("roster_id") != me.get("roster_id")), None)

    def side(m):
        starters = [p for p in (m.get("starters") or []) if p and p != "0"]
        pts = m.get("starters_points") or []
        name = _team_name(users, owners.get(m["roster_id"]))
        return {"name": name, "abbr": initials(name), "logo_url": _sleeper_logo(users, owners.get(m["roster_id"])),
                "roster_id": m["roster_id"], "points": round(float(m.get("points") or 0.0), 2),
                "starters": {p: round(float(pts[i]) if i < len(pts) else 0.0, 2) for i, p in enumerate(m.get("starters") or [])
                             if p in starters}}
    return {"platform": "sleeper", "league_id": lid, "league": (league.get("name") or "League").upper(),
            "week": week, "me": side(me), "opp": side(opp) if opp else None}


def sleeper_champion(league, user_id):
    """True when his team won this (finished) league's championship."""
    lid = league["league_id"]
    if league.get("status") != "complete":
        return False
    rosters = _get("league/%s/rosters" % lid) or []
    mine = next((r["roster_id"] for r in rosters if r.get("owner_id") == user_id), None)
    final = next((m for m in _get("league/%s/winners_bracket" % lid) or [] if m.get("p") == 1), None)
    return bool(mine and final and final.get("w") == mine)


# ------------------------------------------------------------------ the worker
class FantasyWorker(Worker):
    """Keeps state.fantasy (his matchups), state.fantasy_alerts (big plays, newest last),
    state.fantasy_results (last week's results) and state.fantasy_champs fresh."""

    interval = 30.0

    def __init__(self, state, stop):
        super().__init__(state, stop)
        self.user = (None, None)          # (username, user id)
        self.last_points = {}             # (league id, roster id, player id) -> points seen
        self.week = None
        self.espn_period = {}             # ESPN league id -> matchup period last seen
        self.yahoo_week = {}              # Yahoo league key -> week last seen
        self.yahoo_teams = (0.0, [])      # (fetched at, his Yahoo teams), refreshed hourly
        self.champs = self._load_champs()

    @staticmethod
    def _load_champs():
        try:
            with open(CHAMPS_PATH) as f:
                return json.load(f)
        except (OSError, ValueError):
            return {}

    def _save_champs(self):
        tmp = CHAMPS_PATH + ".tmp"
        with open(tmp, "w") as f:
            json.dump(self.champs, f)
        os.replace(tmp, CHAMPS_PATH)

    def step(self):
        opts = options(self.state.settings)
        nfl_live = any(tr.league == "nfl" and tr.final_at is None for tr in self.state.live_tracked.values())
        self.interval = 60 if nfl_live else 900
        out = []
        if opts["sleeper_username"]:
            try:
                out += self._sleeper(opts, nfl_live)
            except Exception as exc:
                log.warning("Sleeper: %s", exc)
                out += [m for m in self.state.fantasy if m["platform"] == "sleeper"]   # keep the last picture
        if opts["espn_leagues"]:
            out += self._espn(opts, nfl_live)
        out += self._yahoo(opts, nfl_live)
        champs = {k: v for k, v in self.champs.items() if time.time() - v["at"] < CHAMP_DAYS * 86400}
        if champs != self.state.fantasy_champs:
            self.state.fantasy_champs = champs
            self.state.data_version += 1
        if out != self.state.fantasy:
            self.state.fantasy = out
            self.state.data_version += 1

    def _add_results(self, results):
        """Last week's finished matchups (shown a day), replacing that league's older result."""
        if not results:
            return
        for m in results:
            self._logos(m)
        ids = {m["league_id"] for m in results}
        self.state.fantasy_results = [m for m in self.state.fantasy_results if m["league_id"] not in ids] + results
        self.state.data_version += 1

    def _champion(self, league_id, league_name):
        if league_id not in self.champs:
            self.champs[league_id] = {"league": league_name.upper(), "at": time.time()}
            self._save_champs()
            log.info("Fantasy: league champion in %s!", league_name)

    def _sleeper(self, opts, nfl_live):
        name = opts["sleeper_username"]
        if self.user[0] != name:
            u = sleeper_user(name)
            if not u:
                log.warning("Sleeper: no user named %r", name)
                return []
            self.user = (name, u["user_id"])
        user_id = self.user[1]
        state_nfl = _get("state/nfl")
        season, week = str(state_nfl.get("league_season") or state_nfl.get("season")), int(state_nfl.get("display_week") or state_nfl.get("week") or 1)
        leagues = sleeper_leagues(user_id, season)
        if opts["sleeper_leagues"]:
            leagues = [l for l in leagues if l["league_id"] in opts["sleeper_leagues"]]

        # A new week: last week's finished matchups become the results (shown a day)
        if self.week is not None and week != self.week and opts["result"]:
            results = []
            for lg in leagues:
                try:
                    m = sleeper_matchup(lg, user_id, self.week)
                    if m and m["opp"]:
                        m["status"], m["at"] = "final", time.time()
                        results.append(m)
                except Exception as exc:
                    log.warning("Sleeper result %s: %s", lg["league_id"], exc)
            self._add_results(results)
        self.week = week

        out = []
        for lg in leagues:
            try:
                if lg.get("status") == "complete":
                    if opts["champion"] and lg["league_id"] not in self.champs and sleeper_champion(lg, user_id):
                        self._champion(lg["league_id"], lg["name"])
                    continue
                m = sleeper_matchup(lg, user_id, week)
                if not m:
                    continue
                m["status"] = "live" if nfl_live else "week"     # live while NFL games are on
                self._logos(m)
                out.append(m)
                self._alerts(m, opts)
            except Exception as exc:
                log.warning("Sleeper league %s: %s", lg["league_id"], exc)
        return out

    def _yahoo(self, opts, nfl_live):
        from . import fantasy_yahoo as yahoo
        if not yahoo.connected():
            return []
        out = []
        try:
            fetched, mine = self.yahoo_teams
            if time.time() - fetched > 3600:
                mine = yahoo.teams()
                self.yahoo_teams = (time.time(), mine)
            if opts["yahoo_leagues"]:
                mine = [t for t in mine if t["league_key"] in opts["yahoo_leagues"]]
            for t in mine:
                lk = t["league_key"]
                try:
                    if t["finished"]:
                        if opts["champion"] and "yahoo:%s" % lk not in self.champs and yahoo.champion(t):
                            self._champion("yahoo:%s" % lk, t["league"])
                        continue
                    week = t["week"]
                    before = self.yahoo_week.get(lk)
                    if before is not None and week != before and opts["result"]:
                        old = yahoo.matchup(t, before)
                        if old and old["opp"]:
                            old["status"], old["at"] = "final", time.time()
                            self._add_results([old])
                    self.yahoo_week[lk] = week
                    m = yahoo.matchup(t, week)
                    if not m:
                        continue
                    m["status"] = "live" if nfl_live else "week"
                    self._logos(m)
                    out.append(m)
                    self._alerts(m, opts)
                except yahoo.NotConnected:
                    raise
                except Exception as exc:
                    log.warning("Yahoo league %s: %s", lk, exc)
                    out += [m for m in self.state.fantasy if m["league_id"] == "yahoo:%s" % lk]
        except yahoo.NotConnected as exc:
            log.warning("Yahoo: not connected (%s); you need to sign in again on the settings page", exc)
        except Exception as exc:
            log.warning("Yahoo: %s", exc)
            out += [m for m in self.state.fantasy if m["platform"] == "yahoo"]
        return out

    def _espn(self, opts, nfl_live):
        season = espn_season()
        out = []
        for lg in opts["espn_leagues"]:
            lid = lg["id"]
            try:
                d = espn_league(lid, season)
                team = _espn_my_team(d, lg.get("team"))
                if not team:
                    log.warning("ESPN league %s: no team picked (and his login codes don't own one)", lid)
                    continue
                name = (d.get("settings") or {}).get("name") or "League"
                if not (d.get("status") or {}).get("isActive", True):
                    if opts["champion"] and espn_champion(d, team):
                        self._champion("espn:%s" % lid, name)
                    continue
                period = (d.get("status") or {}).get("currentMatchupPeriod") or 1
                before = self.espn_period.get(lid)
                if before is not None and period != before and opts["result"]:
                    old = espn_matchup(espn_league(lid, season, before), team, before)
                    if old and old["opp"]:
                        old["status"], old["at"] = "final", time.time()
                        self._add_results([old])
                self.espn_period[lid] = period
                m = espn_matchup(espn_league(lid, season, d.get("scoringPeriodId") or period), team, period)
                if not m:
                    continue
                m["status"] = "live" if nfl_live else "week"
                self._logos(m)
                out.append(m)
                self._alerts(m, opts)
            except EspnPrivate:
                log.warning("ESPN league %s is private: his ESPN login codes are needed (or expired)", lid)
            except Exception as exc:
                log.warning("ESPN league %s: %s", lid, exc)
                out += [m for m in self.state.fantasy if m["league_id"] == "espn:%s" % lid]
        return out

    @staticmethod
    def _logos(m):
        """Download both teams' logos once (cached on disk); the slides draw them by key."""
        from . import logos
        for side in (m["me"], m["opp"]):
            if side and side.get("logo_url"):
                url = side["logo_url"]
                side["logo"] = logos.download_fantasy(url, espn_cookies() if "fantasy.espn.com" in url else None)

    def _alerts(self, m, opts):
        """A starter's points jumping by at least alert_min since the last look = a big play."""
        for who, side in (("me", m["me"]), ("opp", m["opp"])):
            if not side:
                continue
            for pid, pts in side["starters"].items():
                key = (m["league_id"], side["roster_id"], pid)
                before = self.last_points.get(key)
                self.last_points[key] = pts
                if before is None or not opts["alerts"] or (who == "opp" and not opts["opp_alerts"]):
                    continue
                gain = round(pts - before, 2)
                if gain >= opts["alert_min"]:
                    self.state.fantasy_alerts.append({
                        "at": time.time(), "mine": who == "me", "player": m.get("names", {}).get(pid) or players.get(pid), "gain": gain,
                        "league": m["league"], "me": m["me"]["points"], "opp": m["opp"]["points"] if m["opp"] else None,
                        "my_name": m["me"]["name"], "opp_name": m["opp"]["name"] if m["opp"] else ""})
                    log.info("Fantasy alert: %s +%.1f (%s)", pid, gain, m["league"])


# ------------------------------------------------------------------ ESPN
# lm-api-reads (ESPN's fantasy API). Public leagues read openly; private ones need the
# owner's two login cookies (espn_s2, SWID), kept in secrets.json (root-only, never sent
# back to the settings page). With SWID the board finds his team itself.
ESPN = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/%s/segments/0/leagues/%s"
BENCH_SLOTS = (20, 21)          # bench, injured reserve
SECRETS_PATH = os.path.join(DATA_DIR, "secrets.json")


class EspnPrivate(Exception):
    """The league is private: his ESPN login codes are needed (or are out of date)."""


def secrets():
    try:
        with open(SECRETS_PATH) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def espn_cookies():
    e = secrets().get("espn") or {}
    return {k: v for k, v in (("espn_s2", e.get("s2")), ("SWID", e.get("swid"))) if v}


def save_espn_cookies(s2, swid):
    """Store his ESPN login codes in secrets.json, alongside the alert email (atomic, root-only)."""
    swid = swid.strip()
    if swid and not swid.startswith("{"):
        swid = "{%s}" % swid.strip("{}")
    save_secret("espn", {"s2": s2.strip(), "swid": swid})


def save_secret(key, value):
    """Set (or with None, remove) one entry of secrets.json, atomically and root-only."""
    data = secrets()
    if value is None:
        data.pop(key, None)
    else:
        data[key] = value
    tmp = SECRETS_PATH + ".tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        json.dump(data, f)
    os.replace(tmp, SECRETS_PATH)


def espn_league(league_id, season, period=None):
    """One ESPN league's settings, teams and this period's matchups (raw ESPN JSON)."""
    params = [("view", v) for v in ("mSettings", "mTeam", "mMatchupScore", "mBoxscore")]
    if period:
        params.append(("scoringPeriodId", str(period)))
    r = _session.get(ESPN % (season, league_id), params=params, cookies=espn_cookies(),
                     headers={"User-Agent": "Mozilla/5.0 (ballin-board)"}, timeout=20)
    if r.status_code in (401, 403):
        raise EspnPrivate(league_id)
    r.raise_for_status()
    return r.json()


def _espn_team_name(t):
    return (t.get("name") or ("%s %s" % (t.get("location", ""), t.get("nickname", ""))).strip() or "TEAM").upper()


def espn_teams(d):
    """[{"id", "name", "mine"}] for the settings page; "mine" when his SWID owns the team."""
    swid = (espn_cookies().get("SWID") or "").upper()
    return [{"id": str(t["id"]), "name": _espn_team_name(t),
             "mine": bool(swid) and swid in [o.upper() for o in t.get("owners") or []]} for t in d.get("teams") or []]


def _espn_my_team(d, team_id):
    if team_id:
        return str(team_id)
    mine = [t["id"] for t in espn_teams(d) if t["mine"]]
    return mine[0] if mine else None


def _short(full):
    if (full or "").endswith("D/ST"):
        return full
    parts = (full or "").split()
    return ("%s. %s" % (parts[0][0], " ".join(parts[1:]))) if len(parts) > 1 else (full or "?")


def espn_matchup(d, team_id, period):
    """His matchup this period in one ESPN league (the module docstring's dict, plus "names")."""
    lid = str(d.get("id"))
    names = {str(t["id"]): _espn_team_name(t) for t in d.get("teams") or []}
    teams = {str(t["id"]): t for t in d.get("teams") or []}
    game = next((g for g in d.get("schedule") or [] if g.get("matchupPeriodId") == period and
                 team_id in (str((g.get("home") or {}).get("teamId")), str((g.get("away") or {}).get("teamId")))), None)
    if not game:
        return None
    me_side = "home" if str(game["home"]["teamId"]) == team_id else "away"
    pnames = {}

    def side(s):
        if not s:
            return None
        entries = (s.get("rosterForCurrentScoringPeriod") or {}).get("entries") or []
        starters = {}
        for e in entries:
            if e.get("lineupSlotId") in BENCH_SLOTS:
                continue
            pid = "espn:%s" % e.get("playerId")
            pool = e.get("playerPoolEntry") or {}
            starters[pid] = round(float(pool.get("appliedStatTotal") or 0.0), 2)
            pnames[pid] = _short((pool.get("player") or {}).get("fullName"))
        pts = s.get("totalPointsLive")
        t = teams.get(str(s.get("teamId"))) or {}
        name = names.get(str(s.get("teamId")), "TEAM")
        return {"name": name, "abbr": (t.get("abbrev") or initials(name)).upper()[:4], "logo_url": t.get("logo"),
                "roster_id": "espn:%s" % s.get("teamId"),
                "points": round(float(pts if pts is not None else s.get("totalPoints") or 0.0), 2),
                "proj": round(float(s.get("totalProjectedPointsLive") or s.get("totalProjectedPoints") or 0.0), 1) or None,
                "starters": starters}
    opp = game.get("away" if me_side == "home" else "home")
    return {"platform": "espn", "league_id": "espn:%s" % lid,
            "league": ((d.get("settings") or {}).get("name") or "League").upper(), "week": period,
            "me": side(game[me_side]), "opp": side(opp), "names": pnames,
            "winner": {"HOME": "home", "AWAY": "away"}.get(game.get("winner")) == me_side if game.get("winner") not in (None, "UNDECIDED") else None}


def espn_champion(d, team_id):
    """His team finished first in a completed ESPN league."""
    if (d.get("status") or {}).get("isActive", True):
        return False
    t = next((t for t in d.get("teams") or [] if str(t["id"]) == team_id), None)
    return bool(t and (t.get("rankCalculatedFinal") == 1 or t.get("rankFinal") == 1))


def espn_season(today=None):
    """The NFL season ESPN files this under: Jan-Feb belong to the previous year's season."""
    import datetime
    d = today or datetime.date.today()
    return d.year if d.month >= 3 else d.year - 1
