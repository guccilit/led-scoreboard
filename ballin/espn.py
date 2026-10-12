"""ESPN public API: fetching and parsing into small plain objects.

Everything here runs in worker threads. Functions raise on network/HTTP
errors, and raise SchemaError when ESPN's data doesn't look the way the board
expects (a changed format or ordering); callers keep their last good data and
the error is emailed to the owner.
"""
import datetime
import logging
from dataclasses import dataclass, field

import requests

from . import alerts, api

log = logging.getLogger(__name__)

SITE = "https://site.api.espn.com/apis/site/v2/sports"
CORE = "https://site.api.espn.com/apis/v2/sports"
TIMEOUT = api.TIMEOUT


class SchemaError(Exception):
    """ESPN's response doesn't match what the board relies on."""


def require(cond, what):
    if not cond:
        raise SchemaError(what)


def get_json(url, params=None):
    data = api.get_json(url, params)
    require(isinstance(data, dict), "%s: response is not a JSON object" % url)
    return data


# ---------------------------------------------------------------- models
@dataclass
class Team:
    id: str
    abbr: str
    name: str           # "Bears"
    location: str       # "Chicago"
    color: str = ""     # hex, no '#'
    alt_color: str = ""
    logo_url: str = ""


@dataclass
class Side:
    team_id: str
    abbr: str
    score: int = None
    winner: bool = False
    home: bool = False
    record: str = ""
    stats: dict = field(default_factory=dict)    # live stats, e.g. NHL goalie "saves"


@dataclass
class Series:
    summary: str
    completed: bool
    wins: dict          # team_id -> wins
    total: int = 0


@dataclass
class Game:
    id: str
    league: str
    date: datetime.datetime   # aware, UTC
    state: str                # "pre", "in", "post"
    status: str               # e.g. STATUS_FINAL, STATUS_POSTPONED
    detail: str               # ESPN short detail, e.g. "Final/OT", "10/1 - 9:30 PM EDT"
    completed: bool
    season_type: int          # 1 pre, 2 regular, 3 post
    home: Side
    away: Side
    period: int = 0
    clock: str = ""
    note: str = ""            # e.g. "NLWC - Game 2"
    series: Series = None
    situation: dict = field(default_factory=dict)

    def side(self, team_id):
        return self.home if self.home.team_id == team_id else self.away

    def opponent(self, team_id):
        return self.away if self.home.team_id == team_id else self.home

    @property
    def postponed(self):
        return self.status in ("STATUS_POSTPONED", "STATUS_CANCELED", "STATUS_SUSPENDED")

    @property
    def delayed(self):
        """Held up but still on: a rain/weather delay during the game or before the start."""
        return not self.completed and not self.postponed and \
            ("DELAY" in self.status or "delay" in (self.detail or "").lower())

    @property
    def halt_label(self):
        """RAIN DELAY / DELAYED / POSTPONED / SUSPENDED / CANCELED, or "" when nothing's wrong."""
        if self.postponed:
            return self.status.replace("STATUS_", "")
        if self.delayed:
            d = (self.detail or "").upper()
            if "DELAY" in d and len(d) <= 13:
                return d                                  # e.g. RAIN DELAY, WEATHER DELAY
            return "RAIN DELAY" if "RAIN" in self.status else "DELAYED"
        return ""


@dataclass
class StandRow:
    team_id: str
    abbr: str
    seed: int
    wins: int
    losses: int
    ties: int           # OT losses (NHL) or ties (NFL)
    points: int
    gb: str             # games behind ("-" for leader)
    pct: str
    clincher: str       # "", "x", "y", "z", "*", "e", ...
    games: int
    streak: str = ""    # e.g. "W3"


@dataclass
class Standings:
    season_year: int
    divisions: dict      # division name -> [StandRow] best first
    conferences: dict    # conference name -> [StandRow] best first
    division_of: dict    # team_id -> (conference name, division name)
    short: dict          # group name -> short label, e.g. "NFC NORTH"


# ---------------------------------------------------------------- parsing
def parse_date(s):
    s = s.replace("Z", "+00:00")
    if len(s) == 22 and s[16] == "+":   # "2026-10-01T23:00+00:00" (no seconds)
        s = s[:16] + ":00" + s[16:]
    return datetime.datetime.fromisoformat(s)


def _int(v, default=None):
    try:
        return int(float(v))
    except (TypeError, ValueError):
        return default


def parse_team(t):
    logos = t.get("logos") or []
    dark = [l["href"] for l in logos if "500-dark" in l.get("href", "") and "scoreboard" not in l["href"]]
    any_logo = [l["href"] for l in logos if l.get("href")]
    return Team(
        id=str(t["id"]), abbr=t.get("abbreviation", "?"), name=t.get("name") or t.get("shortDisplayName", ""),
        location=t.get("location", ""), color=t.get("color", ""), alt_color=t.get("alternateColor", ""),
        logo_url=(dark or any_logo or [""])[0],
    )


def _side(c):
    score = c.get("score")
    if isinstance(score, dict):
        score = score.get("value", score.get("displayValue"))
    rec = ""
    for r in c.get("records") or c.get("record") or []:
        if r.get("type") in ("total", None) or r.get("name") == "overall":
            rec = r.get("summary") or r.get("displayValue") or ""
            break
    stats = {s.get("name"): s.get("displayValue") for s in c.get("statistics") or [] if isinstance(s, dict)}
    return Side(team_id=str(c["team"]["id"]), abbr=c["team"].get("abbreviation", "?"), score=_int(score),
                winner=bool(c.get("winner")), home=c.get("homeAway") == "home", record=rec, stats=stats)


def parse_series(s):
    if isinstance(s, list):   # game summaries give a list of series
        s = next((x for x in s if isinstance(x, dict) and x.get("type") == "playoff"), None)
    if not s or s.get("type") != "playoff":
        return None
    wins = {str(c["id"]): _int(c.get("wins"), 0) for c in s.get("competitors", [])}
    return Series(summary=s.get("summary", ""), completed=bool(s.get("completed")), wins=wins,
                  total=_int(s.get("totalCompetitions"), 0))


def parse_event(e, league):
    try:
        c = e["competitions"][0]
        require(len(c["competitors"]) == 2, "event %s has %d competitors" % (e.get("id"), len(c["competitors"])))
        st = c.get("status") or e.get("status") or {}
        stype = st.get("type", {})
        sides = [_side(x) for x in c["competitors"]]
        require(sum(s.home for s in sides) == 1, "event %s: can't tell home from away" % e.get("id"))
        home = next(s for s in sides if s.home)
        away = next(s for s in sides if not s.home)
        season_type = _int((e.get("seasonType") or {}).get("type")) or _int((e.get("season") or {}).get("type")) or 2
        notes = c.get("notes") or []
        state = stype.get("state", "")
        require(state in ("pre", "in", "post"), "event %s: unknown game state %r" % (e.get("id"), state))
        return Game(
            id=str(e["id"]), league=league, date=parse_date(e.get("date") or c.get("date")),
            state=state, status=stype.get("name", ""),
            detail=stype.get("shortDetail") or stype.get("detail", ""), completed=bool(stype.get("completed")),
            season_type=season_type, home=home, away=away, period=_int(st.get("period"), 0),
            clock=st.get("displayClock", ""), note=notes[0].get("headline", "") if notes else "",
            series=parse_series(c.get("series")), situation=c.get("situation") or {},
        )
    except SchemaError:
        raise
    except (KeyError, IndexError, TypeError, ValueError, AttributeError) as exc:
        raise SchemaError("%s event %s: unexpected format (%s: %s)" % (league, e.get("id"), type(exc).__name__, exc))


def _stats(entry):
    out = {}
    for s in entry.get("stats", []):
        out[s.get("name") or s.get("type")] = s
    return out


def _row(entry):
    st = _stats(entry)

    def num(name, default=0):
        s = st.get(name)
        return _int(s.get("value"), default) if s else default

    def disp(name, default=""):
        s = st.get(name)
        return (s.get("displayValue") or default) if s else default

    ties = num("otLosses") if "otLosses" in st else num("ties")
    return StandRow(
        team_id=str(entry["team"]["id"]), abbr=entry["team"].get("abbreviation", "?"),
        seed=num("playoffSeed", 99) or 99, wins=num("wins"), losses=num("losses"), ties=ties,
        points=num("points"), gb=disp("gamesBehind", "-"), pct=disp("winPercent"),
        clincher=disp("clincher").strip(), games=num("gamesPlayed", num("wins") + num("losses") + ties),
        streak=disp("streak"),
    )


def _groups(node, path=()):
    """Yield (path_of_names, abbreviation, entries) for every group with entries."""
    entries = node.get("standings", {}).get("entries", [])
    here = path + (node.get("name", ""),)
    if entries:
        yield here, node.get("abbreviation", ""), entries
    for ch in node.get("children", []):
        yield from _groups(ch, here)


# ---------------------------------------------------------------- fetching
def fetch_teams(league):
    d = get_json(f"{SITE}/{league.sport}/{league.key}/teams")
    try:
        teams = [parse_team(t["team"]) for t in d["sports"][0]["leagues"][0]["teams"]]
    except (KeyError, IndexError, TypeError) as exc:
        raise SchemaError("%s teams: unexpected format (%s)" % (league.key, exc))
    teams = [t for t in teams if t.abbr and t.id]
    lo, hi = league.team_count
    require(lo <= len(teams) <= hi, "%s teams: got %d teams, expected %d-%d" % (league.key, len(teams), lo, hi))
    return teams


def _events(d, url):
    events = d.get("events")
    require(isinstance(events, list), "%s: no events list" % url)
    return events


def fetch_schedule(league, team_id):
    """All games for the team's current season (preseason, regular, playoffs).

    Returns (season_year, current_season_type, [Game] sorted by date).
    """
    url = f"{SITE}/{league.sport}/{league.key}/teams/{team_id}/schedule"
    games = {}
    first = get_json(url)
    season = first.get("season") or {}
    year, stype = _int(season.get("year")), _int(season.get("type"))
    require(year and stype in (1, 2, 3, 4), "%s schedule: missing season info (%r)" % (league.key, season))
    for e in _events(first, url):
        g = parse_event(e, league.key)
        games[g.id] = g
    for st in {1, 2, 3} - {stype}:
        try:
            d = get_json(url, {"seasontype": st, "season": year})
        except requests.HTTPError:
            continue
        for e in d.get("events") or []:
            g = parse_event(e, league.key)
            games.setdefault(g.id, g)
    for g in games.values():
        require(team_id in (g.home.team_id, g.away.team_id),
                "%s schedule: game %s doesn't include team %s" % (league.key, g.id, team_id))
    return year, stype, sorted(games.values(), key=lambda g: g.date)


def fetch_next_season(league, team_id, year):
    """Games already published for next season (used in the off-season)."""
    url = f"{SITE}/{league.sport}/{league.key}/teams/{team_id}/schedule"
    out = []
    for st in (1, 2):
        try:
            d = get_json(url, {"season": year + 1, "seasontype": st})
        except requests.HTTPError:
            continue
        out += [parse_event(e, league.key) for e in d.get("events") or []]
    return sorted(out, key=lambda g: g.date)


def _ordered(league, name, rows):
    """Rank rows by playoff seed, then sanity-check the order against the records.

    If ESPN's ordering stops making sense (e.g. a changed seed field), alert and
    fall back to ranking by record ourselves.
    """
    rows = sorted(rows, key=lambda r: r.seed)

    def strength(r):
        if league.points:
            return r.points
        played = r.wins + r.losses + r.ties
        return (r.wins + 0.5 * r.ties) / played if played else 0

    if rows and max(r.games for r in rows) > 0:
        if strength(rows[0]) + 1e-9 < max(strength(r) for r in rows):
            alerts.alert("order:%s:%s" % (league.key, name), "%s standings order looks wrong (%s)" % (league.name, name),
                         "ESPN listed %s first in %s, but another team has a better record. The board is "
                         "ranking by record instead. ESPN may have changed its data.\n\n%s"
                         % (rows[0].abbr, name, "\n".join("%s seed=%s %d-%d-%d pts=%d" % (
                             r.abbr, r.seed, r.wins, r.losses, r.ties, r.points) for r in rows)))
            rows = sorted(rows, key=lambda r: -strength(r))
    return rows


def fetch_standings(league, year):
    """Regular-season standings (never preseason)."""
    params = {"season": year, "seasontype": 2} if year else {"seasontype": 2}
    divs = get_json(f"{CORE}/{league.sport}/{league.key}/standings", dict(params, level=3))
    confs = get_json(f"{CORE}/{league.sport}/{league.key}/standings", params)

    divisions, conferences, division_of, short = {}, {}, {}, {}
    try:
        for path, abbr, entries in _groups(divs):
            conf, div = path[-2], path[-1]
            rows = _ordered(league, div, [_row(e) for e in entries])
            divisions[div] = rows
            short[div] = div
            for r in rows:
                division_of[r.team_id] = (conf, div)
        for path, abbr, entries in _groups(confs):
            conferences[path[-1]] = _ordered(league, path[-1], [_row(e) for e in entries])
            short[path[-1]] = abbr
    except (KeyError, IndexError, TypeError) as exc:
        raise SchemaError("%s standings: unexpected format (%s)" % (league.key, exc))
    require(len(divisions) == league.divisions,
            "%s standings: %d divisions, expected %d" % (league.key, len(divisions), league.divisions))
    require(len(conferences) == 2, "%s standings: %d conferences, expected 2" % (league.key, len(conferences)))
    lo, hi = league.team_count
    require(lo <= len(division_of) <= hi, "%s standings: %d teams" % (league.key, len(division_of)))
    return Standings(year, divisions, conferences, division_of, short)


def fetch_summary(league, event_id):
    return get_json(f"{SITE}/{league.sport}/{league.key}/summary", {"event": event_id})


def fetch_scoreboard(league, day=None):
    """Games on the league scoreboard (today by default, or day="YYYYMMDD")."""
    url = f"{SITE}/{league.sport}/{league.key}/scoreboard"
    d = get_json(url, {"dates": day} if day else None)
    return [parse_event(e, league.key) for e in _events(d, url)]
