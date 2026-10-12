"""Fantasy football slides (see fantasy.py): his matchup per league, the week's result, the
League Champion celebration, and the big-play announcement."""
import datetime
import time

import numpy as np

from . import gfx
from .gfx import W
from .slides import (GOLD, GRAY, LABEL, LOGO, LOSS, WHITE, WIN, BorderSlide, CelebrationSlide, StaticSlide, draw_logo,
                     fit, narrow_text, put, tiny_text)
from . import fantasy

FFL_GOLD = np.array((255, 190, 40), np.float32)


def _score(v):
    return ("%.1f" % v) if v is not None else "-"


# Tall thin digits (4 wide, 11 high) for three-digit scores: two of them fit between the logos with
# a clear gap (owner, 2026-10-03: 140-140 ran together in the bold digits)
THIN = {
    "0": [".##.", "#..#", "#..#", "#..#", "#..#", "#..#", "#..#", "#..#", "#..#", "#..#", ".##."],
    "1": ["..#.", ".##.", "#.#.", "..#.", "..#.", "..#.", "..#.", "..#.", "..#.", "..#.", "####"],
    "2": [".##.", "#..#", "...#", "...#", "..#.", "..#.", ".#..", ".#..", "#...", "#...", "####"],
    "3": ["###.", "...#", "...#", "...#", "...#", ".##.", "...#", "...#", "...#", "...#", "###."],
    "4": ["#..#", "#..#", "#..#", "#..#", "#..#", "####", "...#", "...#", "...#", "...#", "...#"],
    "5": ["####", "#...", "#...", "#...", "###.", "...#", "...#", "...#", "...#", "#..#", ".##."],
    "6": [".##.", "#...", "#...", "#...", "###.", "#..#", "#..#", "#..#", "#..#", "#..#", ".##."],
    "7": ["####", "...#", "...#", "..#.", "..#.", "..#.", ".#..", ".#..", ".#..", ".#..", ".#.."],
    "8": [".##.", "#..#", "#..#", "#..#", ".##.", "#..#", "#..#", "#..#", "#..#", "#..#", ".##."],
    "9": [".##.", "#..#", "#..#", "#..#", "#..#", ".###", "...#", "...#", "...#", "...#", ".##."],
}


def thin_text(s):
    parts = []
    for ch in s:
        if ch in THIN:
            if parts:
                parts.append(np.zeros((11, 1), bool))
            parts.append(np.array([[c == "#" for c in r] for r in THIN[ch]], bool))
    return np.concatenate(parts, axis=1) if parts else np.zeros((11, 1), bool)


def _whole(points, thin):
    """Whole points in the tall digits: bold up to 99, thin for both sides once either reaches 100."""
    return (thin_text if thin else narrow_text)(str(int(points)))


def _game_frame(m, header, header_color, bottom, bottom_color, inset=0, small="points"):
    """Like a game slide (owner, 2026-10-03): his logo left, his opponent's right, whole points
    big in the middle (the leader white), exact points under each logo in the compact digits."""
    frame = gfx.new_frame()
    s, f = fit(header, ["5x7", "4x6"], W - 2)
    put(frame, s, f, W // 2, 0, header_color, "center")
    me, opp = m["me"], m["opp"]
    for side, x in ((me, inset), (opp, W - LOGO - inset)):
        draw_logo(frame, "ffl", side.get("logo") or "-", side.get("abbr") or side["name"][:3], x, 8,
                  GOLD if side is me else WHITE)
    lead = me["points"] - opp["points"]
    for side, cx, ahead in ((me, 24, lead >= 0), (opp, 40, lead <= 0)):
        col = WHITE if ahead else GRAY
        d = _whole(side["points"], max(me["points"], opp["points"]) >= 100)
        gfx.blit(frame, d, cx - d.shape[1] // 2, 12, col)
    frame[17, 31:33] = GRAY                          # a dash between the scores, like the game slides
    for side, right in ((me, False), (opp, True)):
        d = tiny_text("%.1f" % side[small])
        w = d.shape[1]
        cx = W - LOGO // 2 if right else LOGO // 2
        x = cx - w // 2 if w <= LOGO else (W - w - inset if right else inset)   # wide ones hug the edge
        gfx.blit(frame, d, x, 27 - inset, (215, 215, 225))
    if bottom:
        s, f = fit(bottom, ["4x6"], 26)
        put(frame, s, f, W // 2, 26 - inset, bottom_color, "center")
    return frame


def matchup_slide(m):
    live = m.get("status") == "live"
    if m["me"].get("proj") and m["opp"].get("proj"):
        # Projected totals under the logos (owner, 2026-10-03; ESPN and Yahoo update them live)
        frame = _game_frame(m, m["league"], FFL_GOLD, "PROJ", LOSS if live else LABEL, small="proj")
    else:
        frame = _game_frame(m, m["league"], FFL_GOLD, "LIVE" if live else "WK %d" % m["week"], LOSS if live else LABEL)
    return StaticSlide("ffl:match:%s" % m["league_id"], frame, duration=8.0)


def result_slide(m):
    won = m["me"]["points"] > m["opp"]["points"]
    lost = m["me"]["points"] < m["opp"]["points"]
    frame = _game_frame(m, m["league"], WIN if won else LOSS if lost else WHITE, "FINAL", LABEL, inset=1 if won else 0)
    slide = StaticSlide("ffl:result:%s" % m["league_id"], frame, duration=8.0)
    return BorderSlide(slide, [FFL_GOLD, np.array(WHITE, np.float32)]) if won else slide


def champion_slide(league_id, c):
    league, f = fit(c["league"], ["4x6"], W - 2)
    lines = [("LEAGUE", "5x7", GOLD), ("CHAMPION", "5x7", GOLD), (league, f, WHITE)]
    inner = CelebrationSlide("ffl:champ:%s" % league_id, lines, duration=10.0, seed=19)
    return BorderSlide(inner, [FFL_GOLD, np.array(WHITE, np.float32)])


def alert_slide(a):
    """A big play: his starter (gold headline) or his opponent's (quiet, no exclamation mark)."""
    frame = gfx.new_frame()
    head = ("+%.1f PTS!" % a["gain"]) if a["mine"] else ("OPP +%.1f" % a["gain"])
    put(frame, head, "5x7", W // 2, 2, FFL_GOLD if a["mine"] else LABEL, "center")
    name, f = fit(a["player"].upper(), ["5x7", "4x6"], W - 4)
    put(frame, name, f, W // 2, 10, WHITE if a["mine"] else LABEL, "center")
    league, f = fit(a["league"], ["4x6"], W - 4)
    put(frame, league, f, W // 2, 18, LABEL, "center")
    if a.get("opp") is not None:
        put(frame, "%s-%s" % (_score(a["me"]), _score(a["opp"])), "4x6", W // 2, 25, GOLD, "center")
    slide = StaticSlide("ffl:alert:%d" % int(a["at"] * 1000), frame, duration=30.0)
    return BorderSlide(slide, [FFL_GOLD, np.array(WHITE, np.float32)]) if a["mine"] else slide


def _gameday(now=None):
    return (now or datetime.datetime.now()).weekday() in (0, 3, 6)   # Mon, Thu, Sun


def matchup_day(settings):
    """Do matchup slides show today ("Matchup slides" setting: game days / all week / off)?"""
    m = fantasy.options(settings)["matchup"]
    return m == "always" or (m == "gamedays" and _gameday())


def slides(state, live=False):
    """The fantasy slides for the rotation (live=True: just the matchups, for live mode)."""
    opts = fantasy.options(state.settings)
    out = []
    if opts["matchup"] != "off" or live:
        if live or opts["matchup"] == "always" or _gameday():
            out += [matchup_slide(m) for m in state.fantasy if m.get("opp")]
    if live:
        return out
    if opts["result"]:
        out += [result_slide(m) for m in state.fantasy_results if time.time() - m.get("at", 0) < fantasy.RESULT_HOURS * 3600]
    if opts["champion"]:
        out += [champion_slide(lid, c) for lid, c in state.fantasy_champs.items()]
    return out
