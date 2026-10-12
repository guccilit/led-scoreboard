"""Slides for live game mode: game views, finals and event announcements."""

import dataclasses
import re

import numpy as np

from . import gfx, logos
from .gfx import H, W
from .slides import (GOLD, LABEL, LOGO, LOSS, WHITE, WIN, BorderSlide, CelebrationSlide, Slide, StaticSlide, narrow_text,
                     _game_frame, fit, news_lines, put, team_color, team_colors, text)

ORDINAL = {1: "1ST", 2: "2ND", 3: "3RD", 4: "4TH"}
DIM_BASE = (45, 45, 60)
FOOTBALL = (200, 110, 40)


class LiveSlide(StaticSlide):
    """A game view. Same key for the whole game, so updates swap in without a transition."""


def _key(data, g):
    return "%s:live:%s" % (data.key, g.id)


def _scores(g, y=11):
    """Both scores, set apart from a centre dash: away score ends left of it, home score
    starts right of it, so even two-digit scores never touch the dash."""
    def center(frame):
        scores = [s.score if s.score is not None else 0 for s in (g.away, g.home)]
        narrow = 10 <= max(scores) < 100          # two digits: tall narrow digits so they don't crowd
        for side, align, score in ((g.away, "right", scores[0]), (g.home, "left", scores[1])):
            x = W // 2 - 4 if align == "right" else W // 2 + 3
            if narrow:
                m = narrow_text(str(score))
                top = y + 1 if y >= 11 else y           # football: snug under the clock, clear of the downs
                gfx.blit(frame, m, x - m.shape[1] + 1 if align == "right" else x, top, WHITE)
                continue
            font = "7x13B" if score < 100 else "6x10"
            put(frame, str(score), font, x, y, WHITE, align)
        frame[y + 4, W // 2 - 1:W // 2 + 1] = LABEL
    return center


# Football: the usual live layout (logos, score, down & distance) with a flat side view of the
# field along the bottom: end zones in team colors, goalposts
# at both ends, the ball at the line of scrimmage and an arrow showing
# which way the offense is going.
FIELD_ROWS = slice(29, 32)
FIELD_X0, FIELD_X1 = 3, 60                        # back of one end zone to the other
FIELD_GREEN = ((22, 118, 36), (16, 92, 28))       # 10-yard mowed stripes
SCRIMMAGE = (70, 130, 255)
POST = (255, 215, 40)
BALL = gfx.sprite([".bbb.", "bwwwb", ".bbb."], {"b": (170, 88, 25), "w": (245, 245, 240)})
TRI_L = np.array([[0, 1], [1, 1], [0, 1]], bool)


def _fx(yard, flip):
    """Panel column of a yard line (-10..110, end zones included)."""
    t = (yard + 10) / 120.0
    if flip:
        t = 1 - t
    return int(round(FIELD_X0 + t * (FIELD_X1 - FIELD_X0)))


def _field(frame, data, g, flip):
    """ESPN's yardLine is yards from the home goal line (0) to the away goal line (100).
    Without flip the home end zone is on the left."""
    s = g.situation or {}
    home_c = team_color(data.teams.get(g.home.team_id))
    away_c = team_color(data.teams.get(g.away.team_id))
    for x in range(FIELD_X0, FIELD_X1 + 1):
        t = (x - FIELD_X0) / (FIELD_X1 - FIELD_X0)
        yard = (1 - t if flip else t) * 120 - 10
        if yard < 0:
            frame[FIELD_ROWS, x] = home_c * 0.7
        elif yard > 100:
            frame[FIELD_ROWS, x] = away_c * 0.7
        else:
            frame[FIELD_ROWS, x] = FIELD_GREEN[min(9, int(yard // 10)) % 2]
    frame[FIELD_ROWS, _fx(50, flip)] = (170, 170, 170)  # midfield
    for bx in (FIELD_X0 - 1, FIELD_X1 + 1):             # goalposts at the back of the end zones
        frame[24:26, bx - 1] = POST
        frame[24:26, bx + 1] = POST
        frame[26, bx - 1:bx + 2] = POST
        frame[27:29, bx] = POST
    yard = s.get("yardLine")
    if not isinstance(yard, (int, float)):
        return
    yard = min(100, max(0, yard))
    poss = str(s.get("possession") or "")
    toward = 1 if poss == g.home.team_id else -1 if poss == g.away.team_id else 0   # +: toward the away goal
    bx = _fx(yard, flip)
    frame[FIELD_ROWS, bx] = SCRIMMAGE                   # line of scrimmage
    gfx.blit_rgb(frame, BALL, bx - 2, 26)
    d = toward * (-1 if flip else 1)                    # screen direction of travel
    ax = bx - 5 if d < 0 else bx + 4
    if d and FIELD_X0 + 2 <= ax <= FIELD_X1 - 3:       # arrow beside the ball, the way they're going
        tri = TRI_L if d < 0 else TRI_L[:, ::-1]        # (left out right at the goalposts)
        gfx.blit(frame, tri, ax, 26, WHITE)


def _detail_header(g, fallback):
    d = (g.detail or "").upper()
    if any(w in d for w in ("HALF", "END", "DELAY", "INTERMISSION", "SUSP")):
        return d.replace("END OF ", "END ")
    return fallback


def nfl_view(data, g, flip=False):
    s = g.situation or {}
    q = "Q%d" % g.period if g.period <= 4 else "OT"
    header = _detail_header(g, "%s %s" % (q, g.clock) if g.clock else q)
    frame = _game_frame(data, g, header, WHITE, _scores(g, y=8), names=False)
    # Who has the ball: the other team's logo is dimmed (owner's pick, 2026-10-01).
    poss = str(s.get("possession") or "")
    if poss in (g.home.team_id, g.away.team_id):
        x0 = W - LOGO if poss == g.away.team_id else 0
        frame[8:8 + LOGO, x0:x0 + LOGO] *= 0.25
    dd =(s.get("shortDownDistanceText") or "").upper()
    if dd:                                     # down & distance under the score
        dd, font = fit(dd, ["4x6"], 32) if len(dd) <= 8 else fit(dd.replace(" & ", "&").replace("GOAL", "G"), ["4x6"], 32)
        put(frame, dd, font, W // 2, 20, LOSS if s.get("isRedZone") else LABEL, "center")   # clear of tall scores
    # The teams switch ends every quarter; a button double-press flips it to match the TV.
    _field(frame, data, g, flip ^ (g.period in (2, 4)))
    return frame


def nhl_view(data, g):
    p = ORDINAL.get(g.period, "OT") if g.period <= 4 else "OT"
    if g.period > 4:
        p = "SO" if "SO" in (g.detail or "").upper() else "%dOT" % (g.period - 3)
    header = _detail_header(g, "%s %s" % (p, g.clock) if g.clock else p)
    frame = _game_frame(data, g, header, WHITE, _scores(g), names=False)
    # Shots on goal: a team's shots = the other goalie's saves + that team's goals.
    try:
        away = int(g.home.stats.get("saves")) + (g.away.score or 0)
        home = int(g.away.stats.get("saves")) + (g.home.score or 0)
    except (TypeError, ValueError):
        return frame
    put(frame, "SHOTS", "4x6", W // 2, 26, LABEL, "center")
    put(frame, str(away), "4x6", LOGO // 2, 26, WHITE, "center")
    put(frame, str(home), "4x6", W - LOGO // 2, 26, WHITE, "center")
    return frame


def _bases_outs(frame, s):
    """Base diamond, ball-strike count and out dots across the bottom centre."""
    x, y = 16, 26
    on = {"second": s.get("onSecond"), "third": s.get("onThird"), "first": s.get("onFirst")}
    for base, (bx, by) in {"second": (3, 0), "third": (0, 3), "first": (6, 3)}.items():
        frame[y + by:y + by + 2, x + bx:x + bx + 2] = GOLD if on[base] else DIM_BASE
    balls, strikes = s.get("balls"), s.get("strikes")
    if balls is not None and strikes is not None:
        put(frame, "%d-%d" % (balls, strikes), "4x6", 32, y, LABEL, "center")
    outs = int(s.get("outs") or 0)
    for i in range(3):
        frame[y + 2:y + 4, 41 + i * 3:43 + i * 3] = LOSS if i < outs else DIM_BASE


def mlb_view(data, g):
    s = g.situation or {}
    header = (g.detail or "").upper().replace("BOTTOM", "BOT")
    frame = _game_frame(data, g, header, WHITE, _scores(g), names=False)
    if s and not any(w in header for w in ("MID", "END")):
        _bases_outs(frame, s)
    return frame


def nba_view(data, g):
    q = "Q%d" % g.period if g.period <= 4 else "OT" if g.period == 5 else "%dOT" % (g.period - 4)
    header = _detail_header(g, "%s %s" % (q, g.clock) if g.clock else q)
    return _game_frame(data, g, header, WHITE, _scores(g), names=False)


VIEWS = {"nfl": nfl_view, "nhl": nhl_view, "mlb": mlb_view, "nba": nba_view}


def game_slide(data, g, final=False, flip=False):
    key = _key(data, g)
    if g.postponed:
        # Postponed / cancelled: just who it was; suspended games keep their score.
        suspended = g.status == "STATUS_SUSPENDED"
        frame = _game_frame(data, g, g.halt_label, LOSS, _scores(g) if suspended else (lambda f: None),
                            "" if suspended else "@")
        return LiveSlide(key, frame, duration=10.0)
    if final or g.state == "post":
        me, opp = g.side(data.team.id), g.opponent(data.team.id)
        # ESPN's winner flag can lag the final: the score decides too.
        won = bool(me.winner) or (g.state == "post" and None not in (me.score, opp.score) and me.score > opp.score)
        lost = bool(opp.winner) or (g.state == "post" and None not in (me.score, opp.score) and me.score < opp.score)
        if getattr(data, "role", "fav") == "rival":    # a division rival's game: just the final
            won = lost = False
        color = WIN if won else LOSS if lost else WHITE
        # A win: team-color border, the header one LED down inside it, "BROWNS WIN" at the
        # bottom (no team names; owner, 2026-10-01).
        bottom = ("%s WIN" % data.team.name.upper()) if won and data.team.name else ""
        frame = _game_frame(data, g, (g.detail or "FINAL").upper(), color, _scores(g), names=False,
                            header_y=1 if won else 0)
        if bottom:
            s, f = fit(bottom, ["4x6"], W - 4)
            put(frame, s, f, W // 2, 25, GOLD, "center")
        slide = LiveSlide(key, frame, duration=10.0)
        return BorderSlide(slide, team_colors(data.team)) if won else slide
    if g.state == "pre":
        t = g.date.astimezone()
        frame = _game_frame(data, g, g.halt_label or "GAME TIME", LOSS if g.delayed else GOLD, _scores(g),
                            t.strftime("%I:%M").lstrip("0"), LABEL)
        return LiveSlide(key, frame, duration=10.0)
    view = VIEWS.get(data.key)
    if not view:
        return None
    frame = view(data, g, flip) if data.key == "nfl" else view(data, g)
    if g.delayed:                       # weather/other delay: say so in red where the clock was
        frame[:8] = 0
        s, f = fit(g.halt_label, ["5x7", "4x6"], W)
        put(frame, s, f, W // 2, 0, LOSS, "center")
    return LiveSlide(key, frame, duration=10.0)


_FILLER = re.compile(r"\((?:no huddle|shotgun|no huddle, shotgun|shotgun, no huddle)\)\s*", re.I)


def _wrap(text_, width=W - 2, font="4x6"):
    """Word-wrap into lines that fit the panel."""
    lines, cur = [], ""
    for word in text_.split():
        trial = (cur + " " + word).strip()
        if cur and text(trial, font).shape[1] > width:
            lines.append(cur)
            cur = word
        else:
            cur = trial
    if cur:
        lines.append(cur)
    return lines


class PlaySlide(Slide):
    """Announcement with the play spelled out: logo, headline and team name on top, and the
    description underneath, two lines at a time (pages held long enough to read), ending
    with "NEXT: <batter>" for baseball."""

    TEXT_Y = (19, 26)
    PAGE = 3.0     # seconds per page

    def __init__(self, key, logo, headline, name, color, headline_color, detail, next_up, celebrate):
        from .splash import Firework
        self.key, self.duration = key, 60.0
        self.fg = gfx.new_frame()
        x = 1
        if logo is not None:
            gfx.blit_rgb(self.fg, logo, 1, 2)
            x = LOGO + 3
        s, f = fit(headline, ["5x7", "4x6"], W - x - 1)
        put(self.fg, s, f, x, 3, headline_color)
        s, f = fit(name, ["4x6"], W - x - 1)
        put(self.fg, s, f, x, 13, color)
        self.lit = self.fg.max(-1) > 0
        lines = []
        if detail:
            clean = _FILLER.sub("", detail).strip().rstrip(".").upper()
            lines += [(ln, LABEL) for ln in _wrap(clean)]
        if next_up:
            lines += [(ln, GOLD) for ln in _wrap("NEXT: " + next_up.upper())]
        self.pages = [lines[i:i + 2] for i in range(0, len(lines), 2)] or [[]]
        # The announcement stays until every page has been shown.
        self.min_show = len(self.pages) * self.PAGE if lines else 0.0
        self.closing, self.close_pass, self.done = False, None, False
        rng = np.random.default_rng(len(headline))
        self.fireworks = [Firework(rng, i * 0.8, rng.uniform(30, 60), rng.uniform(4, 12), color, n=22)
                          for i in range(30)] if celebrate else []

    def render(self, t):
        frame = gfx.new_frame()
        for fw in self.fireworks:
            fw.draw(frame, t, 0.45)
        top = self.TEXT_Y[0] - 1
        frame[:top][self.lit[:top]] = 0
        frame[self.lit] = self.fg[self.lit]
        frame[top:] = 0
        # Once the presenter asks to close (closing), finish the pass in progress, then hold
        # the last page and report done, so the text is never cut off mid-way.
        n = len(self.pages)
        k = int(t / self.PAGE)
        if self.closing and self.close_pass is None:
            self.close_pass = k // n
        if self.close_pass is not None and k // n > self.close_pass:
            k, self.done = n - 1, True
        page = self.pages[k % n]
        for (ln, col), y in zip(page, self.TEXT_Y):
            put(frame, ln, "4x6", W // 2, y, col, "center")
        return frame


class IntroThen(Slide):
    """A clip first (prerendered frames, or anything with render(t) and duration), then
    the announcement itself."""

    def __init__(self, frames, inner, min_after, fps=40):
        self.frames, self.inner, self.fps = frames, inner, fps
        self.intro = frames.duration if hasattr(frames, "render") else len(frames) / fps
        self.max_load = getattr(frames, "max_load", None)    # a clip that needs more light (home runs)
        self.key, self.duration = inner.key, inner.duration + self.intro
        self.min_show = self.intro + min_after

    def render(self, t):
        if t < self.intro:
            if hasattr(self.frames, "render"):
                return self.frames.render(t)
            return self.frames[min(len(self.frames) - 1, int(t * self.fps))]
        return self.inner.render(t - self.intro)


def _goal_clip(ev):
    """Blackhawks goals get GOAL, the horn and the DO chant first; a Cubs or Bears win
    gets their song (owner, 2026-10-01)."""
    from .live import WIN_SONGS
    song = WIN_SONGS.get((ev.league, ev.team_id))
    if song and ev.headline == song[0]:
        from . import splash2
        return splash2.WIN_SONGS[song[1]]()
    if ev.league == "mlb" and ev.team_id == "16" and ev.favorite and ev.kind == "score":
        from . import splash2             # Cubs home runs (owner, 2026-10-02)
        if ev.headline == "GRAND SLAM!":
            return splash2.GrandSlam()
        if ev.headline in ("HOME RUN!", "2-RUN HOMER!", "3-RUN HOMER!"):
            return splash2.HomeRun(ev.headline)
    if ev.league == "nfl" and ev.team_id == "3" and ev.favorite and ev.kind == "score":
        from . import splash2             # Bears scores (owner, 2026-10-02)
        if ev.headline == "FIELD GOAL!":
            return splash2.BearsFieldGoal()
        titles = {"TOUCHDOWN!": ("TOUCH", "DOWN!"), "PICK SIX!": ("PICK", "SIX!"), "FUMBLE TD!": ("FUMBLE", "TD!")}
        if ev.headline in titles:
            return splash2.BearsTouchdown(titles[ev.headline])
    if ev.league == "nhl" and ev.team_id == "4" and ev.favorite and ev.kind == "score":
        from .splash2 import HawksGoal
        return HawksGoal()          # GOAL, the horn, the DO chant (owner, 2026-10-02)
    return None


def event_slide(data, ev):
    slide = _event_slide(data, ev)
    clip = _goal_clip(ev)
    if slide is not None and clip:
        from .live import alert_min
        after = alert_min(ev, slide)
        if ev.league == "nfl":                # the video already ran 10-20 s: details ~9 s after
            text = getattr(slide, "min_show", None) or getattr(getattr(slide, "inner", None), "min_show", 0)
            after = max(text or 0.0, 9.0)
        return IntroThen(clip, slide, after)
    return slide


def _event_slide(data, ev):
    team = data.teams.get(ev.team_id)
    name = (team.name if team else "").upper()
    color = team_color(team)
    if not ev.favorite:
        # Opponents' plays are stated, not celebrated: no exclamation mark.
        ev = dataclasses.replace(ev, headline=ev.headline.rstrip("!"))
    if ev.detail or ev.next_up:
        quiet = not ev.favorite
        slide = PlaySlide("alert:%s" % id(ev), logos.get(data.key, ev.team_id), ev.headline, name, color,
                          color if quiet else GOLD, ev.detail, ev.next_up, celebrate=ev.favorite)
        return BorderSlide(slide, team_colors(team)) if ev.favorite else slide
    lines = news_lines((ev.headline,))
    if ev.favorite:
        inner = CelebrationSlide("alert:%s" % id(ev), lines, logos.get(data.key, ev.team_id),
                                 duration=60.0, title=(name, color))
        return BorderSlide(inner, team_colors(team))
    # Opponent: same layout, no fireworks, in their color.
    inner = CelebrationSlide("alert:%s" % id(ev), [(s, f, color) for s, f, _ in lines],
                             logos.get(data.key, ev.team_id), duration=60.0, title=(name, color))
    inner.fireworks = []
    return inner
