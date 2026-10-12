"""Per-league data worker: favorite team's schedule, standings, season window.

Results are LeagueData objects stored in state.leagues[key]. Last good data is
cached on disk so the board has something to show right after a reboot, even
before the internet comes back.
"""
import datetime
import hashlib
import json
import logging
import os
import pickle
import time
from dataclasses import asdict, dataclass, field

from . import alerts, api, espn, logos
from .leagues import LEAGUES
from .settings import DATA_DIR, favorite_ids, league_option, watching_today
from .workers import Worker

log = logging.getLogger(__name__)

UTC = datetime.timezone.utc
WINDOW_LEAD = datetime.timedelta(days=14)      # show a team this long before its first game
# After elimination a team stays through the end of the next day (see _day_after_end).
CHAMP_GRACE = datetime.timedelta(days=7)       # ...or longer after a championship
PLAYOFF_WAIT = datetime.timedelta(days=10)     # clinched, waiting for the playoff schedule
ANNOUNCE_FOR = datetime.timedelta(days=1)      # news (clinch, series win, streak) stays in rotation a day
STREAK_MIN = {"nfl": 4, "nhl": 5, "mlb": 6, "nba": 6}   # winning streaks worth announcing
TEAMS_MAX_AGE = 120 * 86400                    # refresh team list/logos at least this often

CHAMPIONSHIPS = ("world series", "stanley cup final", "super bowl", "nba finals")


@dataclass
class LeagueData:
    key: str
    team: espn.Team
    teams: dict                       # team id -> Team (ids are consistent across ESPN endpoints; abbreviations are not)
    season_year: int
    season_type: int
    games: list
    last: espn.Game = None
    next: espn.Game = None
    standings: espn.Standings = None
    in_window: bool = False
    series: espn.Series = None        # current/last playoff series of the favorite
    series_round: str = ""
    announcement: tuple = None        # (headline lines...) e.g. ("CLINCHED", "PLAYOFFS")
    eliminated: bool = False
    celebrate_until: float = 0       # flashy border on results after a series win (timestamp)
    called_off: espn.Game = None      # a game postponed, suspended or cancelled in the last day
    role: str = "fav"                 # "fav", "watch" (today only: no standings) or "rival" (owner)
    updated: float = field(default_factory=time.time)


def local_date(dt):
    return dt.astimezone().date()


def _day_after_end(dt):
    """Midnight at the end of the day after dt (local time), as an aware datetime."""
    day = local_date(dt) + datetime.timedelta(days=2)
    return datetime.datetime.combine(day, datetime.time()).astimezone()


def _path(name):
    return os.path.join(DATA_DIR, name)


def _atomic_write(path, data):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def load_cached(key):
    try:
        with open(_path("cache_%s.pkl" % key), "rb") as f:
            data = pickle.load(f)
        if isinstance(data, LeagueData):      # cache from before multiple favorites
            data = [data]
        return data if isinstance(data, list) and all(isinstance(d, LeagueData) for d in data) else None
    except Exception:
        return None


def _round_name(note):
    """'NLWC - Game 2' -> 'NLWC'."""
    return note.split(" - ")[0].strip().upper() if note else ""


def _clinch_text(league, code):
    code = (code or "").lower()
    if not code or code == "e":
        return None
    if league.key in ("nfl", "nhl") and code == "y":
        return ("DIVISION", "CHAMPS!")
    if league.key == "nhl" and code == "p":
        return ("PRESIDENTS'", "TROPHY!")
    if league.key == "nfl" and code == "z":
        return ("FIRST-ROUND", "BYE!")
    return ("CLINCHED", "PLAYOFFS!")


class LeagueWorker(Worker):
    def __init__(self, state, stop, key):
        super().__init__(state, stop)
        self.name = "League-" + key
        self.league = LEAGUES[key]
        self.teams = []
        self.teams_meta = {}
        self.standings = None
        self.standings_at = 0
        self.series_cache = {}       # game id -> Series
        self.rival_cache = {}        # team id -> (fetched at, last game, next game)
        self.saved_hash = None
        cached = load_cached(key)
        if cached:
            state.leagues = dict(state.leagues, **{key: cached})
            state.data_version += 1

    # -------------------------------------------------------------- teams
    def _load_teams(self, season_year):
        path = _path("teams_%s.json" % self.league.key)
        if not self.teams:
            try:
                with open(path) as f:
                    meta = json.load(f)
                self.teams = [espn.Team(**t) for t in meta["teams"]]
                self.teams_meta = meta
            except Exception:
                pass
        stale = (not self.teams or time.time() - self.teams_meta.get("fetched", 0) > TEAMS_MAX_AGE
                 or (season_year and self.teams_meta.get("season_year") not in (None, season_year)))
        if stale:
            teams = espn.fetch_teams(self.league)
            if teams:
                self.teams = teams
                self.teams_meta = {"fetched": time.time(), "season_year": season_year,
                                   "teams": [asdict(t) for t in teams]}
                _atomic_write(path, json.dumps(self.teams_meta).encode())
                log.info("%s: refreshed %d teams for season %s", self.league.name, len(teams), season_year)
                for t in teams:
                    logos.download(self.league.key, t, force=True)
        elif season_year and self.teams_meta.get("season_year") is None:
            self.teams_meta["season_year"] = season_year
            _atomic_write(path, json.dumps(self.teams_meta).encode())
        return self.teams

    def _favorites(self):
        """Favorite teams in this league, by ESPN id (older settings may hold one id or an abbreviation)."""
        favs = favorite_ids(self.state.settings, self.league.key)
        if not favs:
            return []
        if not self.teams:
            self._load_teams(None)
        found = []
        for fav in favs:
            team = next((t for t in self.teams if t.id == fav), None) or \
                next((t for t in self.teams if t.abbr.upper() == fav.upper()), None)
            if team is None and self.teams:
                alerts.alert("favorite:%s:%s" % (self.league.key, fav), "%s favorite team not found" % self.league.name,
                             "A favorite %s team (%r) isn't in ESPN's team list any more. "
                             "Pick it again on the settings page." % (self.league.name, fav))
            elif team and team not in found:
                found.append(team)
        return found

    # -------------------------------------------------------------- series
    def _series_for(self, game):
        if game.series:
            return game.series
        if game.id not in self.series_cache:
            try:
                d = espn.fetch_summary(self.league, game.id)
                comp = (d.get("header", {}).get("competitions") or [{}])[0]
                self.series_cache[game.id] = espn.parse_series(comp.get("series"))
            except Exception as exc:
                log.warning("%s: series lookup failed: %s", self.league.name, exc)
                return None
        return self.series_cache[game.id]

    # -------------------------------------------------------------- main
    def step(self):
        key = self.league.key
        favs = self._favorites()
        if not favs:
            if self.state.leagues.get(key):
                self.state.leagues = {k: v for k, v in self.state.leagues.items() if k != key}
                self.state.data_version += 1
            self.interval = 300
            return

        now = datetime.datetime.now(UTC)
        out, soon, any_window = [], False, False
        for i, team in enumerate(favs):
            year, stype, games = espn.fetch_schedule(self.league, team.id)
            if i == 0:
                self._load_teams(year)
                logos.download_league(key)
                stale = self.standings is None or self.standings.season_year != year
                if stale or time.time() - self.standings_at > 1800:
                    try:
                        self.standings = espn.fetch_standings(self.league, year)
                        self.standings_at = time.time()
                    except Exception as exc:
                        log.warning("%s: standings failed: %s", self.league.name, exc)
            team = next((t for t in self.teams if t.id == team.id), team)
            data = self._team_data(team, year, stype, games, now)
            out.append(data)
            any_window = any_window or data.in_window
            soon = soon or bool(data.next and (data.next.date - now) < datetime.timedelta(hours=1))

        w = watching_today(self.state.settings)
        for data in out:                      # the team watched today: its games, not its standings
            if (key, data.team.id) in w and data.team.id not in favorite_ids({"favorites": self.state.settings.get("favorites")}, key):
                data.role = "watch"
        if league_option(self.state.settings, "show_rivals", key):
            rivals = _rivals_for(self, [d for d in out if d.role == "fav"], {d.team.id for d in out}, now)
        else:
            rivals = []
        self.state.rivals = dict(self.state.rivals, **{key: rivals})
        self.state.fresh = dict(self.state.fresh, **{key: time.monotonic()})   # for the stale-data alert
        self.state.leagues = dict(self.state.leagues, **{key: out})
        self.state.data_version += 1
        self._save(out)
        # Poll faster around game time; live mode polls the scoreboard itself.
        self.interval = api.interval(60 if soon else 600 if any_window else 3 * 3600)

    def _team_data(self, team, year, stype, games, now):
        """Everything the slides need about one favorite team."""
        key = self.league.key
        playable = [g for g in games if not g.postponed]
        done = [g for g in playable if g.completed]
        upcoming = [g for g in playable if not g.completed and g.date > now - datetime.timedelta(hours=8)]
        last = done[-1] if done else None
        nxt = upcoming[0] if upcoming else None
        row = self._standing_row(team.id)
        clincher = row.clincher if row else ""

        series, series_round = None, ""
        if last and last.season_type == 3 and self.league.series:
            series = self._series_for(last)
            series_round = _round_name(last.note)

        # Season window
        in_window, eliminated, announcement = False, False, None
        if playable:
            opens = min(g.date for g in playable) - WINDOW_LEAD
            if now >= opens and upcoming:
                in_window = True
            elif now >= opens and last:
                won_last = last.side(team.id).winner
                if last.season_type == 3:
                    title_game = any(c in last.note.lower() for c in CHAMPIONSHIPS)
                    if title_game and won_last:
                        in_window = now - last.date < CHAMP_GRACE
                        announcement = ("CHAMPIONS!",)
                    elif series and not series.completed:
                        in_window = now - last.date < PLAYOFF_WAIT   # next game not scheduled yet
                    elif (series and series.wins.get(team.id, 0) * 2 > series.total) or (not series and won_last):
                        in_window = now - last.date < PLAYOFF_WAIT   # won the round, waiting for the next
                    else:
                        eliminated = True
                        in_window = now < _day_after_end(last.date)
                elif clincher and clincher != "e":
                    in_window = now - last.date < PLAYOFF_WAIT       # waiting for the playoff schedule
                else:
                    eliminated = True
                    in_window = now < _day_after_end(last.date)

        # Off-season: has next season's schedule been published? Open the window early.
        if not in_window and not upcoming and year:
            ahead = [g for g in espn.fetch_next_season(self.league, team.id, year) if not g.postponed]
            future = [g for g in ahead if not g.completed]
            if future and now >= min(g.date for g in ahead) - WINDOW_LEAD:
                in_window, eliminated, last, nxt = True, False, None, future[0]
                year, stype, games = year + 1, 1, ahead

        # Team-color marquee: news gets it for a day; a playoff round won (series, or a
        # single playoff game in the NFL) through the end of the next day.
        celebrate_until = 0
        if announcement is None:
            news = self._announcement(team, row, year, series, series_round)
            if news:
                announcement, since = news
                celebrate_until = since + ANNOUNCE_FOR.total_seconds()
        else:
            celebrate_until = (last.date + CHAMP_GRACE).timestamp()   # champions
        if last and last.season_type == 3 and last.side(team.id).winner:
            advanced = (series.completed and series.wins.get(team.id, 0) * 2 > series.total) if series \
                else not self.league.series
            if advanced:
                celebrate_until = max(celebrate_until, _day_after_end(last.date).timestamp())

        data = LeagueData(
            key=key, team=team, teams={t.id: t for t in self.teams}, season_year=year, season_type=stype,
            games=games, last=last, next=nxt, standings=self.standings, in_window=in_window,
            series=series, series_round=series_round, announcement=announcement, eliminated=eliminated,
            celebrate_until=celebrate_until,
            called_off=next((g for g in reversed(games) if g.postponed
                             and abs((now - g.date).total_seconds()) < 18 * 3600), None),
        )
        for g in ([last] if last else []) + ([nxt] if nxt else []):
            for side in (g.home, g.away):
                t = data.teams.get(side.team_id)
                if t:
                    logos.download(key, t)
        return data

    def _standing_row(self, team_id):
        st = self.standings
        if not st or team_id not in st.division_of:
            return None
        div = st.divisions[st.division_of[team_id][1]]
        return next((r for r in div if r.team_id == team_id), None)

    def _announcement(self, team, row, year, series, series_round):
        """News seen while the board is running (clinches, series wins, streaks).

        Returns ((lines...), first_seen_timestamp) for the newest item still within
        ANNOUNCE_FOR, or None.
        """
        path = _path("announce.json")
        try:
            with open(path) as f:
                seen = json.load(f)
        except Exception:
            seen = {}
        key = "%s:%s:%s" % (self.league.key, team.id, year)
        events = {}
        clincher = row.clincher if row else ""
        text = _clinch_text(self.league, clincher)
        if text:
            events["clinch:" + clincher] = text
        if series and series.completed and series.wins.get(team.id, 0) * 2 > series.total:
            events["series:" + series_round] = (series_round or "SERIES", "WINNERS!")
        streak = row.streak.upper() if row else ""
        if streak.startswith("W") and streak[1:].isdigit() and int(streak[1:]) >= STREAK_MIN.get(self.league.key, 99):
            events["streak:" + streak] = ("%s STRAIGHT" % streak[1:], "WINS!")
        record = seen.setdefault(key, {})
        first_run = not record
        now = time.time()
        changed = False
        for ev in events:
            if ev not in record:
                # Status that already existed when the board first saw this season isn't news.
                record[ev] = 0 if first_run else now
                changed = True
        if first_run and not events:
            record["_seen"] = now
            changed = True
        if changed:
            _atomic_write(path, json.dumps(seen).encode())
        fresh = [(record[ev], text) for ev, text in events.items()
                 if record.get(ev) and now - record[ev] < ANNOUNCE_FOR.total_seconds()]
        if not fresh:
            return None
        since, text = max(fresh)
        return text, since

    def _save(self, data):
        """Write the disk cache only when something meaningful changed (saves SD wear)."""
        snapshot = [dict(asdict(d), updated=0) for d in data]
        h = hashlib.sha1(repr(snapshot).encode()).hexdigest()
        if h == self.saved_hash:
            return
        self.saved_hash = h
        try:
            _atomic_write(_path("cache_%s.pkl" % self.league.key), pickle.dumps(data))
        except OSError as exc:
            log.warning("cache write failed: %s", exc)


def _last_next(games, now):
    """A team's last finished game and next game (postponed ones skipped)."""
    playable = [g for g in games if not g.postponed]
    done = [g for g in playable if g.completed]
    upcoming = [g for g in playable if not g.completed and g.date > now - datetime.timedelta(hours=8)]
    return (done[-1] if done else None), (upcoming[0] if upcoming else None)


def _rivals_for(worker, favs, skip_ids, now):
    """The other teams in each favorite's division, as LeagueData (role "rival"), in standings
    order: each gets its own last / next game slides, like a favorite, but no standings
    highlight, news or celebrations. Schedules refresh every 30 min (10 min near game time)."""
    st = worker.standings
    if not st:
        return []
    out, seen = [], set()
    for fav in favs:
        where = st.division_of.get(fav.team.id)
        if not where:
            continue
        for row in st.divisions.get(where[1]) or []:
            tid = row.team_id
            if tid in skip_ids or tid in seen:
                continue
            seen.add(tid)
            cached = worker.rival_cache.get(tid)
            nxt = cached and _last_next(cached[3], now)[1]
            soon = nxt and (nxt.date - now) < datetime.timedelta(hours=4)
            if not cached or time.time() - cached[0] > (600 if soon else 1800):
                try:
                    year, stype, games = espn.fetch_schedule(worker.league, tid)
                    worker.rival_cache[tid] = cached = (time.time(), year, stype, games)
                except Exception as exc:
                    log.warning("%s: rival %s schedule failed: %s", worker.league.name, tid, exc)
                    if not cached:
                        continue
            _, year, stype, games = cached
            team = next((t for t in worker.teams if t.id == tid), None)
            if team is None:
                continue
            last, nxt = _last_next(games, now)
            window = fav.in_window and bool(last or nxt)
            if fav.season_type == 3:              # playoffs: only rivals still playing in them
                window = window and any(g and g.season_type == 3 for g in (last, nxt))
            out.append(LeagueData(key=fav.key, team=team, teams=fav.teams, season_year=year, season_type=stype,
                                  games=games, last=last, next=nxt, standings=st, in_window=window, role="rival"))
    return out
