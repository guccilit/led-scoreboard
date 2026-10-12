"""Live game mode.

LiveFetcher (worker): while a favorite team's game is on, polls the league
scoreboard and stores timestamped snapshots of that game.

LivePresenter (worker, every 0.5 s): applies the user's delay (shows the
snapshot from now - delay), detects events between visible snapshots, and
publishes slides for the render loop:
  state.live_slides  game views and recent finals (rotated by the render loop)
  state.live_alert   the event announcement on screen right now, or None

After an announcement the board doesn't go back to the game view until the
visible data reflects the play (score, bases, outs, count, down...) changed,
so it never shows the pre-play situation after announcing it.
"""
import collections
import datetime
import logging
import time
from dataclasses import dataclass, field

from . import api, espn, settings as settings_mod
from .fantasy import options as fantasy_options
from .leagues import LEAGUES, ORDER
from .workers import Worker

log = logging.getLogger(__name__)

START_EARLY = datetime.timedelta(minutes=15)   # start polling before the scheduled start
FINAL_HOLD = 15 * 60                           # final score stays this long (seconds)


def final_hold(settings):
    """How long a final score stays up, in seconds (settings page; owner, 2026-10-03)."""
    try:
        return min(60, max(1, int(settings.get("final_minutes", 15)))) * 60
    except (TypeError, ValueError):
        return FINAL_HOLD

POLL = 10.0                                    # seconds between scoreboard polls while live
KEEP = 600                                     # seconds of snapshots kept (covers delay up to ~9 min)
# Minimum time an announcement stays up, per sport: (your team, opponent). Hockey goals are
# rare and big, so they stay longest. MLB outs use OUT_MIN. A scrolling play description
# also stays until it has scrolled all the way through.
ALERT_MIN = {"nhl": (12.0, 11.0), "nfl": (9.0, 7.0), "mlb": (7.0, 5.0), "nba": (6.0, 5.0)}
OUT_MIN = 4.5
NFL_SCORE_MIN = 20.0                           # touchdowns and field goals (owner, 2026-10-01)
ALERT_MAX = 30.0                               # give up waiting for the data to catch up

HITS = {"single": "SINGLE!", "double": "DOUBLE!", "triple": "TRIPLE!", "home run": "HOME RUN!"}


@dataclass
class Snap:
    t: float
    game: espn.Game


@dataclass
class Tracked:
    league: str
    game_id: str
    snaps: collections.deque = field(default_factory=lambda: collections.deque(maxlen=200))
    visible: Snap = None
    final_at: float = None       # when the final became visible


@dataclass
class Event:
    league: str
    game_id: str
    team_id: str
    favorite: bool
    headline: str
    detail: str = ""
    before: Snap = None          # visible snapshot just before the play
    at: Snap = None              # visible snapshot that revealed the play
    kind: str = "score"          # "score", "hit", "out" or "turnover"
    next_up: str = ""            # MLB: next batter
    extra: dict = None           # fantasy big play: the alert dict (league "ffl")


def situation_key(g):
    """What the game view shows; used to decide the view has caught up with a play."""
    s = g.situation or {}
    return (g.home.score, g.away.score, g.period, s.get("outs"), s.get("balls"), s.get("strikes"),
            bool(s.get("onFirst")), bool(s.get("onSecond")), bool(s.get("onThird")),
            s.get("down"), s.get("distance"), s.get("yardLine"), (s.get("possession") or ""))


def _bases(s):
    return bool(s.get("onFirst")), bool(s.get("onSecond")), bool(s.get("onThird"))


def caught_up(ev, g):
    """Does game snapshot g show the result of the announced play?"""
    g0 = ev.before.game
    s, s0 = g.situation or {}, g0.situation or {}
    scores_moved = (g.home.score, g.away.score) != (g0.home.score, g0.away.score)
    if ev.kind == "hit":
        return _bases(s) != _bases(s0) or scores_moved
    if ev.kind == "out":
        return s.get("outs") != s0.get("outs") or g.detail != g0.detail
    if ev.kind == "period":
        return True
    if ev.kind == "turnover":
        return s.get("possession") != s0.get("possession") or scores_moved
    return situation_key(g) != situation_key(g0)


def _name(person):
    """Display name from an ESPN athlete reference (several shapes)."""
    if not isinstance(person, dict):
        return ""
    a = person.get("athlete") if isinstance(person.get("athlete"), dict) else person
    return a.get("displayName") or a.get("fullName") or a.get("shortName") or ""


def next_batter(g):
    s = g.situation or {}
    name = _name(s.get("batter"))
    if not name:
        due = s.get("dueUp") or []
        name = _name(due[0]) if due else ""
    return name


def batting_team(g, lp=None):
    team = str(((lp or {}).get("team") or {}).get("id") or "")
    if team in (g.home.team_id, g.away.team_id):
        return team
    top = "top" in (g.detail or "").lower() or "mid" in (g.detail or "").lower()
    return g.away.team_id if top else g.home.team_id


def _play(g):
    lp = (g.situation or {}).get("lastPlay") or {}
    return lp if isinstance(lp, dict) else {}


def detect(league, fav_ids, before, after):
    """Events between two visible snapshots of the same game. fav_ids: favorite team id(s)."""
    fav_ids = {fav_ids} if isinstance(fav_ids, str) else set(fav_ids)
    a, b = before.game, after.game
    events = []
    lp = _play(b)
    lp_new = bool(lp.get("id")) and lp.get("id") != _play(a).get("id")
    lp_type = ((lp.get("type") or {}).get("text") or "").lower()
    lp_text = lp.get("text") or ""
    sa, sb = a.situation or {}, b.situation or {}

    def event(team, head, kind="score", favorite=None):
        fav = team in fav_ids if favorite is None else favorite
        nxt = next_batter(b) if league == "mlb" else ""
        text = sb.get("scoreText") if kind == "score" and sb.get("scoreText") else (lp_text if lp_new else "")
        events.append(Event(league, b.id, team, fav, head, text, before, after, kind, nxt))

    # NFL turnovers: credited to the team that took the ball.
    turnover = None
    if league == "nfl" and lp_new and ("interception" in lp_type or "fumble" in lp_type):
        old, new = str(sa.get("possession") or ""), str(sb.get("possession") or "")
        if "interception" in lp_type or (old and new and old != new):
            turnover = new if new and new != old else (b.home.team_id if old == b.away.team_id else b.away.team_id)

    for side_b in (b.home, b.away):
        side_a = a.side(side_b.team_id)
        if side_a.score is None or side_b.score is None or side_b.score <= side_a.score:
            continue
        delta = side_b.score - side_a.score
        if league == "nfl":
            if turnover == side_b.team_id and delta >= 6:
                head = "PICK SIX!" if "interception" in lp_type else "FUMBLE TD!"
                turnover = None
            elif "touchdown" in lp_type or delta >= 6:
                head = "TOUCHDOWN!"
            elif "field goal" in lp_type or delta == 3:
                head = "FIELD GOAL!"
            elif "safety" in lp_type:
                head = "SAFETY!"
            elif delta == 2:
                head = "2-PT GOOD!"
            else:
                continue          # extra point: part of the touchdown, not its own announcement
        elif league == "nba":
            # Basketball scores constantly: only the favorite's 3-pointers here; lead changes,
            # runs and quarter ends are handled by nba_moments().
            three = delta == 3 and ("three" in lp_type or "three point" in lp_text.lower() or "3-pt" in lp_text.lower())
            if not (three and side_b.team_id in fav_ids):
                continue
            head = "3-POINTER!"
        elif league == "nhl":
            head = "GOAL!"
        elif league == "mlb":
            if "home run" in lp_type or "homer" in lp_text.lower():
                head = "HOME RUN!" if delta == 1 else "%d-RUN HOMER!" % delta if delta < 4 else "GRAND SLAM!"
            else:
                head = "RUN SCORES!" if delta == 1 else "%d RUNS SCORE!" % delta
        else:
            head = "SCORE!"
        event(side_b.team_id, head)

    if turnover:
        event(turnover, "PICKED OFF!" if "interception" in lp_type else "FUMBLE!", "turnover")

    if league == "mlb" and lp_new and not events:
        batting = batting_team(b, lp)
        hit = next((h for k, h in HITS.items() if lp_type.startswith(k)), None)
        outs_a, outs_b = sa.get("outs"), sb.get("outs")
        half_over = (a.detail or "").split(" ")[0] != (b.detail or "").split(" ")[0]
        if hit:
            event(batting, hit, "hit")
        elif isinstance(outs_a, int) and isinstance(outs_b, int) and (outs_b > outs_a or half_over):
            made = (outs_b - outs_a) if outs_b > outs_a else max(1, 3 - outs_a)
            total = outs_b if outs_b > outs_a else 3
            if made >= 2:
                head = "TRIPLE PLAY!" if made == 3 else "DOUBLE PLAY"
            elif "strikeout" in lp_type or "strike out" in lp_type:
                head = "STRIKEOUT"
            else:
                head = "OUT %d" % total
            event(batting, head, "out", favorite=False)   # outs use the quieter style

    # Delays and stoppages, shown with the favorite's logo in the quiet style.
    fav = next((t for t in (b.home.team_id, b.away.team_id) if t in fav_ids), b.home.team_id)
    if (b.delayed and not a.delayed) or (b.postponed and not a.postponed):
        event(fav, b.halt_label, "period", favorite=False)
    elif a.delayed and not b.delayed and b.state == "in" and not b.postponed:
        event(fav, "RESUMED", "period", favorite=False)
    return events


# Teams with a win song: (league, ESPN team id) -> (headline, song name in splash2.WIN_SONGS)
WIN_SONGS = {("mlb", "16"): ("CUBS WIN!", "gocubsgo"), ("nfl", "3"): ("BEARS WIN!", "beardown"),
             ("nhl", "4"): ("HAWKS WIN!", "hawkswin")}


def song_win(league, fav_ids, g):
    """(team id, headline) when a favorite with a win song just won this finished game."""
    if g.state != "post" or g.postponed:
        return None
    for (lg, team), (headline, _) in WIN_SONGS.items():
        if lg == league and team in fav_ids and team in (g.home.team_id, g.away.team_id) \
                and g.side(team).winner:
            return team, headline
    return None


def alert_min(ev, slide):
    if ev.kind == "out":
        base = OUT_MIN
    else:
        fav, opp = ALERT_MIN.get(ev.league, (7.0, 5.0))
        base = fav if ev.favorite else opp
        if ev.league == "nfl" and ev.kind == "score":
            base = NFL_SCORE_MIN
    scroll = getattr(slide, "min_show", None) or getattr(getattr(slide, "inner", None), "min_show", 0)
    return max(base, scroll)


def scoring_text(league, game_id):
    """The text of the latest scoring play (ESPN's game summary), e.g. a goal scorer."""
    d = espn.fetch_summary(league, game_id)
    plays = d.get("scoringPlays") or [p for p in d.get("plays") or [] if p.get("scoringPlay")]
    return (plays[-1].get("text") or "").strip() if plays else ""


class LiveFetcher(Worker):
    interval = 30.0

    def step(self):
        now = datetime.datetime.now(datetime.timezone.utc)
        tracking = dict(self.state.live_tracked)
        active = False
        boards = {}           # one scoreboard request per league per round
        seen = set()          # two favorites playing each other: one game
        rivals = [(k, r) for k in ORDER for r in self.state.rivals.get(k) or []
                  if settings_mod.league_option(self.state.settings, "rival_live", k)]
        for key, data in [(k, d) for k in ORDER for d in self.state.leagues.get(k) or []] + rivals:
            if not data.in_window:
                continue
            want = None
            for g in (data.next, data.last):
                if not g:
                    continue
                if (g.state == "in" and not g.postponed) or (g.state == "pre" and now >= g.date - START_EARLY):
                    want = g
                    break
                tr = tracking.get(g.id)
                if tr and (tr.final_at is None or time.time() - tr.final_at < final_hold(self.state.settings)):
                    want = g          # finished but still showing the final
                    break
            if not want:
                continue
            active = True
            if want.id in seen:
                continue
            seen.add(want.id)
            tr = tracking.get(want.id) or Tracked(key, want.id)
            last = tr.snaps[-1].game if tr.snaps else None
            if last is None or not last.completed:
                g = self._fetch(LEAGUES[key], want, boards)
                if g:
                    # On a score, look up what the scoring play actually was (the scoreboard's
                    # "last play" may already be the extra point or a timeout).
                    if last and key != "nba" and (g.home.score, g.away.score) != (last.home.score, last.away.score):
                        try:
                            text = scoring_text(LEAGUES[key], g.id)
                            if text:
                                g.situation = dict(g.situation or {}, scoreText=text)
                        except Exception as exc:
                            log.warning("scoring play lookup failed: %s", exc)
                    tr.snaps.append(Snap(time.time(), g))
            tracking[want.id] = tr
        # Forget games whose final has been shown long enough.
        for gid, tr in list(tracking.items()):
            if tr.final_at and time.time() - tr.final_at > final_hold(self.state.settings) + 60:
                del tracking[gid]
        self.state.live_tracked = tracking
        in_play = any(tr.snaps and tr.snaps[-1].game.state == "in" for tr in tracking.values())
        self.interval = api.interval(POLL if in_play else 30.0) if active else 30.0

    def _fetch(self, league, game, boards):
        if league.key not in boards:
            boards[league.key] = espn.fetch_scoreboard(league)
        games = boards[league.key]
        found = next((g for g in games if g.id == game.id), None)
        if found is None:
            day = game.date.astimezone().strftime("%Y%m%d")
            games = espn.fetch_scoreboard(league, day)
            found = next((g for g in games if g.id == game.id), None)
        if found is None:
            log.warning("%s: game %s not on the scoreboard", league.name, game.id)
        return found


class LivePresenter(Worker):
    interval = 0.5

    def __init__(self, state, stop):
        super().__init__(state, stop)
        self.queue = []          # events waiting to be shown
        self.alert = None        # (event, slide, shown_at)
        self.version = None
        self.pending_lights = [] # win light shows waiting for the last announcements
        self.ffl_on, self.ffl_next, self.ffl_off_at = False, 0.0, 0.0   # fantasy matchup in the live rotation
        self.ffl_secs = 10.0

    def step(self):
        from . import live_slides
        now = time.time()
        changed = False
        for tr in self.state.live_tracked.values():
            cutoff = now - settings_mod.delay_for(self.state.settings, tr.league)
            vis = None
            for s in tr.snaps:
                if s.t <= cutoff:
                    vis = s
            if vis is None or vis is tr.visible:
                continue
            favs = {d.team.id for d in self.state.leagues.get(tr.league) or []}
            if tr.visible is not None and tr.visible.game.id == vis.game.id:
                found = detect(tr.league, favs, tr.visible, vis)
                if tr.league == "nba":
                    found += nba_moments(tr, favs, tr.visible, vis)
                if not favs & {vis.game.home.team_id, vis.game.away.team_id}:
                    found = [ev for ev in found if ev.kind == "score"]   # rivals' games: scoring only
                for ev in found:
                    log.info("Event %s %s: %s %s", tr.league, ev.team_id, ev.headline, ev.detail)
                    self.queue.append(ev)
            if tr.visible is not None and vis.game.state == "in" and tr.visible.game.state == "pre":
                self._game_started(tr.league, vis.game)      # pregame -> under way
            won = song_win(tr.league, favs, vis.game)
            if won and tr.visible is not None and tr.visible.game.state == "in":
                # Watched the Cubs or Bears win: their song, then the result (owner, 2026-10-01).
                self.queue.append(Event(tr.league, vis.game.id, won[0], True, won[1], "",
                                        tr.visible, vis, "period", extra={"win": True}))
            elif tr.visible is not None and tr.visible.game.state == "in" and vis.game.state == "post":
                # Any other favorite winning: lights once its last announcements have played
                for side in (vis.game.home, vis.game.away):
                    if side.team_id in favs and side.winner:
                        self.pending_lights.append(self._light("win", tr.league, side.team_id))
            tr.visible = vis
            if (vis.game.state == "post" or vis.game.postponed) and tr.final_at is None:
                tr.final_at = now
            changed = True

        # Current announcement: keep it until shown long enough AND the game view has
        # caught up with the play (or we've waited too long).
        if self.alert:
            ev, slide, shown = self.alert
            tr = self.state.live_tracked.get(ev.game_id)
            ready = ev.league == "ffl" or bool(tr and tr.visible and tr.visible.t >= ev.at.t and caught_up(ev, tr.visible.game))
            long_enough = now - shown >= alert_min(ev, slide)
            if (long_enough and ready) or now - shown > max(ALERT_MAX, alert_min(ev, slide) + 5):
                # Paged play text finishes the pass it's on before the board moves on.
                paged = slide                    # the PlaySlide, possibly wrapped (border, goal clip)
                while paged is not None and not hasattr(paged, "closing"):
                    paged = getattr(paged, "inner", None)
                if hasattr(paged, "closing") and len(paged.pages) > 1:
                    paged.closing = True
                    if paged.done or now - shown > ALERT_MAX + paged.min_show + 1:
                        self.alert = None
                else:
                    self.alert = None
        self._fantasy_alerts(now)
        if self.alert is None and self.queue:
            ev = self.queue.pop(0)
            if ev.league == "ffl":                         # a fantasy big play: its own slide
                from . import fantasy_slides
                self.alert = (ev, fantasy_slides.alert_slide(ev.extra), now)
            else:
                data = self._data_for(ev.league, ev.at.game)
                if data:
                    self.alert = (ev, live_slides.event_slide(data, ev), now)
                    # Smart lights flash with the celebration (same TV delay): favorites' scores
                    # (not every NBA basket) and the win songs
                    if (ev.extra or {}).get("win"):
                        self.state.light_events.append(self._light("win", ev.league, ev.team_id))
                    elif ev.favorite and ev.kind == "score" and ev.league != "nba":
                        self.state.light_events.append(self._light("score", ev.league, ev.team_id))
        if self.alert is None and not self.queue and self.pending_lights:
            self.state.light_events.extend(self.pending_lights)
            self.pending_lights = []
        self.state.live_alert = self.alert[1] if self.alert else None
        glow = None                                    # lights "glow" mode: a favorite playing now
        for tr in self.state.live_tracked.values():
            if tr.final_at is None and tr.visible and tr.visible.game.state == "in":
                favs = {d.team.id for d in self.state.leagues.get(tr.league) or []}
                tid = next((s.team_id for s in (tr.visible.game.home, tr.visible.game.away) if s.team_id in favs), None)
                if tid:
                    glow = self._light("glow", tr.league, tid)["colors"][0]
                    break
        self.state.light_glow = glow

        # How often his fantasy matchup cuts in during live games (owner, 2026-10-04): every time
        # through, or once every N minutes; and how long it stays each time (live_seconds, else the
        # normal live-game time). After showing for that long it steps out until the interval passes.
        fopts = fantasy_options(self.state.settings)
        every = fopts["live_every"] * 60
        self.ffl_secs = float(fopts["live_seconds"] or self.state.settings.get("live_game_seconds", 10))
        if every <= 0:
            self.ffl_on = True
        elif self.ffl_on and now >= self.ffl_off_at:
            self.ffl_on, self.ffl_next = False, now + every
        elif not self.ffl_on and now >= self.ffl_next:
            self.ffl_on, self.ffl_off_at = True, now + self.ffl_secs + 1
        stamp = (self.state.data_version, self.state.flip_field, id(self.state.settings), self.ffl_on)
        if changed or self.version != stamp:
            self.version = stamp
            views = []
            for tr in sorted(self.state.live_tracked.values(), key=lambda x: ORDER.index(x.league)):
                data = self._data_for(tr.league, tr.visible.game) if tr.visible else None
                if data:
                    s = live_slides.game_slide(data, tr.visible.game, final=tr.final_at is not None,
                                               flip=self.state.flip_field)
                    if s:                      # owner: how long each game stays up
                        s.duration = float(self.state.settings.get("live_game_seconds", 10))
                        views.append(s)
            # After a Cubs or Bears win, their song follows the final score for the final's 15 minutes,
            # unless another game is still going (then just the final; owner, 2026-10-01).
            going = [tr for tr in self.state.live_tracked.values() if tr.final_at is None]
            for tr in self.state.live_tracked.values():
                favs = {d.team.id for d in self.state.leagues.get(tr.league) or []}
                if tr.final_at is not None and tr.visible and not going and \
                        song_win(tr.league, favs, tr.visible.game):
                    if getattr(self, "song", (None,))[0] != tr.game_id:
                        from . import splash2
                        name = WIN_SONGS[(tr.league, song_win(tr.league, favs, tr.visible.game)[0])][1]
                        self.song = (tr.game_id, splash2.WIN_SONGS[name]())
                    i = next((k for k, v in enumerate(views) if v.key.endswith(":" + tr.game_id)), len(views) - 1)
                    views.insert(i + 1, self.song[1])
            # His fantasy matchups ride along in the live rotation: always while NFL games are on, and
            # on matchup days whatever else is live (owner, 2026-10-04). Only alongside live games, so
            # the regular slides (which have them too) take over when nothing is live.
            from . import fantasy_slides
            nfl_live = any(tr.league == "nfl" and tr.final_at is None for tr in self.state.live_tracked.values())
            if views and self.ffl_on and fantasy_options(self.state.settings)["live"] and (
                    nfl_live or fantasy_slides.matchup_day(self.state.settings)):
                for s in fantasy_slides.slides(self.state, live=True):
                    s.duration = self.ffl_secs
                    views.append(s)
            keys = [v.key for v in views]
            if keys != getattr(self, "last_keys", None):
                self.last_keys = keys
                log.info("Live screens: %s", ", ".join(keys) or "none")
            self.state.live_slides = views

    def _light(self, kind, league, team_id):
        """A smart-light moment in that favorite team's colors."""
        from .lights import team_rgb
        team = next((d.team for d in self.state.leagues.get(league) or [] if d.team.id == team_id), None)
        return {"kind": kind, "what": "%s %s" % (league, getattr(team, "abbr", team_id)), "colors": team_rgb(team)}

    def _game_started(self, league, game):
        """A favorite's game just started (as seen with the TV delay): play that team's
        intro, if it has one (Cubs, Blackhawks, Bears)."""
        from .splash2 import INTROS
        favs = {d.team.id for d in self.state.leagues.get(league) or []}
        for side in (game.home, game.away):
            name = INTROS.get((league, side.team_id))
            if name and side.team_id in favs:
                log.info("Game started: %s intro", name)
                self.state.show_request = name
                return

    def _fantasy_alerts(self, now):
        """Queue his fantasy big plays, each held back by the NFL TV delay so the board never
        announces a play before his TV shows it. Plays from before the board started are skipped."""
        if not hasattr(self, "ffl_seen"):
            self.ffl_seen = now
        delay = settings_mod.delay_for(self.state.settings, "nfl")
        for a in list(self.state.fantasy_alerts):
            if a["at"] > self.ffl_seen and now - a["at"] >= delay:
                self.queue.append(Event("ffl", "ffl", "", a["mine"], "FANTASY", kind="fantasy", extra=a))
                self.ffl_seen = a["at"]

    def _data_for(self, league, game):
        """The favorite team's data for a game (any favorite in it), else a rival's (division
        rivals' live games), else any favorite in the league."""
        teams = self.state.leagues.get(league) or []
        ids = (game.home.team_id, game.away.team_id)
        for d in teams + (self.state.rivals.get(league) or []):
            if d.team.id in ids:
                return d
        return teams[0] if teams else None


RUN_STEPS = (8, 12, 16, 20)


def nba_moments(tr, fav_ids, before, after):
    """Basketball events that need memory of the game so far: lead changes, scoring runs
    (8-0 and up, tracked on tr), and the end of each quarter."""
    a, b = before.game, after.game
    events = []
    if None in (a.home.score, a.away.score, b.home.score, b.away.score):
        return events

    def ev(team, head, kind="score", detail="", favorite=None):
        fav = team in fav_ids if favorite is None else favorite
        events.append(Event("nba", b.id, team, fav, head, detail, before, after, kind))

    dh, da = b.home.score - a.home.score, b.away.score - a.away.score
    # Scoring run: points by one team while the other scores nothing.
    run_team, run_pts = getattr(tr, "run", (None, 0))
    if dh > 0 and da == 0 or da > 0 and dh == 0:
        scorer = b.home.team_id if dh > 0 else b.away.team_id
        pts = dh or da
        new = run_pts + pts if scorer == run_team else pts
        crossed = [s for s in RUN_STEPS if run_pts < s <= new] if scorer == run_team else \
            [s for s in RUN_STEPS if s <= new]
        if crossed:
            ev(scorer, "%d-0 RUN!" % new)
        tr.run = (scorer, new)
    elif dh > 0 and da > 0:
        tr.run = (None, 0)

    # Lead change (not just tying it).
    lead_a = (a.home.score > a.away.score) - (a.home.score < a.away.score)
    lead_b = (b.home.score > b.away.score) - (b.home.score < b.away.score)
    if lead_b != 0 and lead_b != lead_a:
        ev(b.home.team_id if lead_b > 0 else b.away.team_id, "TAKE LEAD!")

    # End of a quarter / halftime.
    da_, db_ = (a.detail or "").lower(), (b.detail or "").lower()
    if ("end of" in db_ or "half" in db_) and db_ != da_:
        head = "HALFTIME" if "half" in db_ else "END OF Q%d" % b.period if b.period <= 4 else "END OF OT"
        leader = b.home.team_id if b.home.score >= b.away.score else b.away.team_id
        score = "%s %d  %s %d" % (b.away.abbr, b.away.score, b.home.abbr, b.home.score)
        ev(leader, head, "period", score, favorite=False)
    return events
