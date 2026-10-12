"""Slides for slides mode. Built in a worker thread; rendered by the render loop.

Each slide pre-renders its static picture when it's built, so render(t) only
does cheap work (copying, scrolling, small effects).
"""
import datetime
import logging
import math
import time

import numpy as np

from . import fonts, gfx, logos, screens, settings as settings_mod
from .gfx import H, W
from .leagues import LEAGUES, ORDER
from .workers import Worker

log = logging.getLogger(__name__)

WHITE = (235, 235, 245)
GRAY = (110, 110, 125)
LABEL = (195, 195, 210)     # small info text (dates, AM/PM, notes): readable but quieter than white
DIM = (60, 60, 75)
GOLD = (255, 185, 20)
WIN = (40, 230, 90)
LOSS = (240, 45, 45)
HILITE_BG = (10, 22, 70)
CUT = (120, 20, 20)

LOGO = logos.SIZE

# The fonts' tiny @ reads like a "c"; this one is drawn by hand (7x7).
AT_SIGN = np.array([[c == "#" for c in row] for row in (
    ".#####.",
    "#.....#",
    "#.###.#",
    "#.#.#.#",
    "#.####.",
    "#......",
    ".#####.",
)])


# ---------------------------------------------------------------- helpers
def text(s, font):
    return screens.text(s, font)


def put(frame, s, font, x, y, color, align="left"):
    """Draw text; x is the left edge, centre or right edge depending on align. Returns width."""
    m = text(s, font)
    w = m.shape[1]
    if align == "center":
        x = x - w // 2
    elif align == "right":
        x = x - w + 1
    gfx.blit(frame, m, x, y, color)
    return w


def fit(s, fonts_, width):
    """First font in the list that fits the text in width, else the last one (truncated)."""
    for f in fonts_:
        if text(s, f).shape[1] <= width:
            return s, f
    f = fonts_[-1]
    while len(s) > 1 and text(s, f).shape[1] > width:
        s = s[:-1]
    return s, f


def team_color(team):
    """Team color bright enough to read on black LEDs."""
    options = []
    for hexc in (team.color, team.alt_color) if team else ():
        try:
            options.append(np.array([int(hexc[i:i + 2], 16) for i in (0, 2, 4)], np.float32))
        except (ValueError, TypeError, IndexError):
            pass
    if not options:
        return np.array(WHITE, np.float32)
    # Primary color unless it's too dark to read, then the alternate. But a dark *colorful*
    # primary (Jets green) beats a plain white/gray alternate: it's brightened below.
    def colorful(c):
        return c.max() >= 25 and (c.max() - c.min()) / c.max() > 0.4
    best = next((c for c in options if c.max() >= 110), options[0])
    if not colorful(best):
        best = next((c for c in options if colorful(c)), best)
    if best.max() < 170:
        best = best * (200 / max(best.max(), 1)) + 30
    return np.clip(best, 0, 255)


def _visible(rgb):
    """Brighten a team color so it reads on black LEDs, keeping its hue."""
    if rgb.max() < 170:
        rgb = rgb * (200 / max(rgb.max(), 1)) + 30
    return np.clip(rgb, 0, 255)


def team_colors(team):
    """The team's two colors (primary, alternate), both LED-visible and clearly different."""
    out = []
    for hexc in (team.color, team.alt_color) if team else ():
        try:
            rgb = np.array([int(hexc[i:i + 2], 16) for i in (0, 2, 4)], np.float32)
        except (ValueError, TypeError, IndexError):
            continue
        if rgb.max() < 25:          # black: invisible on the panel
            continue
        rgb = _visible(rgb)
        if all(np.abs(rgb - o).sum() > 120 for o in out):
            out.append(rgb)
    if not out:
        out = [np.array(GOLD, np.float32)]
    if len(out) == 1:
        out.append(np.array(WHITE, np.float32))
    return out


def band_color(team):
    """Background for a highlighted standings row: a deep shade of the team's primary
    color, dark enough that white text stays easy to read, rich enough to say whose row it is."""
    c = team_colors(team)[0]
    return c * (70.0 / max(c.max(), 1))


def draw_logo(frame, league, team_id, abbr, x, y, fallback_color=WHITE):
    img = logos.get(league, team_id)
    if img is not None:
        gfx.blit_rgb(frame, img, x, y)
    else:
        s, f = fit(abbr, ["5x7", "4x6"], LOGO)
        put(frame, s, f, x + LOGO // 2, y + LOGO // 2 - 3, fallback_color, "center")


def local(dt):
    return dt.astimezone()


def when_label(dt, now=None):
    now = now or datetime.datetime.now().astimezone()
    d = local(dt).date()
    days = (d - now.date()).days
    if days == 0:
        return "TONIGHT" if local(dt).hour >= 17 else "TODAY"
    if days == 1:
        return "TOMORROW"
    if 1 < days < 7:
        return local(dt).strftime("%A").upper()
    return local(dt).strftime("%a %b %d").upper().replace(" 0", " ")


def time_label(game):
    if "TBD" in game.detail.upper():
        return "TBD", ""
    t = local(game.date)
    return t.strftime("%I:%M").lstrip("0"), t.strftime("%p")


def short_note(note):
    """'NLWC - Game 2' -> 'NLWC G2'."""
    return note.upper().replace(" - GAME ", " G").replace("GAME ", "G") if note else ""


# ---------------------------------------------------------------- slide types
class Slide:
    duration = 8.0
    key = ""

    def apply_pace(self, pace):
        """Stretch (pace > 1) or shorten how long this slide stays up."""
        self.duration = self.duration * pace
        return self

    def render(self, t):
        raise NotImplementedError


class StaticSlide(Slide):
    def __init__(self, key, frame, duration=8.0):
        self.key, self.frame, self.duration = key, frame, duration

    def render(self, t):
        return self.frame


class ListSlide(Slide):
    """Fixed header with a list underneath that scrolls if it's too tall."""

    HOLD = 2.5
    SPEED = 6.0  # px/s

    def __init__(self, key, header, body, start_row_px=0):
        self.view = H - header.shape[0]
        if body.shape[0] < self.view:                 # a short list (e.g. 3 rivals): pad it out
            body = np.concatenate([body, np.zeros((self.view - body.shape[0],) + body.shape[1:], body.dtype)])
        self.key, self.header, self.body = key, header, body
        self.travel = max(0, body.shape[0] - self.view)
        self.offset0 = min(start_row_px, self.travel)
        self.apply_pace(1.0)

    def apply_pace(self, pace):
        self.hold, self.speed = self.HOLD * pace, self.SPEED / pace
        self.duration = max(8.0 * pace, self.hold * 2 + (self.travel - self.offset0) / self.speed)
        return self

    def render(self, t):
        moving = max(0.0, t - self.hold) * self.speed
        off = int(min(self.travel, self.offset0 + moving))
        frame = gfx.new_frame()
        frame[:self.header.shape[0]] = self.header
        frame[self.header.shape[0]:] = self.body[off:off + self.view]
        return frame


class CelebrationSlide(Slide):
    """Text over looping fireworks/confetti (clinches, birthdays...)."""

    def __init__(self, key, lines, logo=None, duration=9.0, seed=7, title=None):
        """lines: [(text, font, color)]. title: optional (text, color) drawn full width on
        top (team names), with the logo and lines underneath."""
        from .splash import Firework
        self.key, self.duration = key, duration
        self.fg = gfx.new_frame()
        self.mask = np.zeros((H, W), bool)

        def place(m, x, y, color):
            gfx.blit(self.fg, m, x, y, color)
            lit = np.zeros((H, W, 3), np.float32)
            gfx.blit(lit, m, x, y, (1, 1, 1))
            self.mask |= lit[..., 0] > 0

        top = 0
        if title:
            s, f = fit(title[0], ["5x7", "4x6"], W - 2)
            m = text(s, f)
            place(m, (W - m.shape[1]) // 2, 2, title[1])
            top = 2 + m.shape[0] + 2
        area = H - top
        heights = [text(s, f).shape[0] for s, f, _ in lines]
        total = sum(heights) + 3 * (len(lines) - 1)
        x0 = 0
        if logo is not None:
            ly = top + (area - LOGO) // 2
            gfx.blit_rgb(self.fg, logo, 1, ly)
            self.mask[ly:ly + LOGO, 1:1 + LOGO] = logo.max(-1) > 0
            x0 = LOGO + 1
        y = top + (area - total + 1) // 2
        cx = x0 + (W - x0) // 2
        for (s, f, color), h in zip(lines, heights):
            m = text(s, f)
            place(m, cx - m.shape[1] // 2, y, color)
            y += h + 3
        grown = self.mask.copy()
        for dy, dx in ((-1, 0), (1, 0), (0, -1), (0, 1), (-1, -1), (1, 1), (-1, 1), (1, -1)):
            grown |= np.roll(np.roll(self.mask, dy, 0), dx, 1)
        self.halo = grown

        # Fireworks go where the screen is empty: alternate the open space left and right of
        # the text (falls back to anywhere if the text fills the width).
        rng = np.random.default_rng(seed)
        palette = [(235, 20, 35), (255, 185, 20), (40, 100, 255), (60, 255, 90), (255, 50, 200), (40, 220, 255)]
        cols = np.nonzero(self.mask.any(0))[0]
        sides = []
        if len(cols):
            if cols[0] >= 7:
                sides.append((2, cols[0] - 3))
            if W - 1 - cols[-1] >= 7:
                sides.append((cols[-1] + 3, W - 3))
        sides = sides or [(8, W - 8)]
        self.fireworks = []
        for i in range(int(duration / 0.55) + 2):
            lo, hi = sides[i % len(sides)]
            self.fireworks.append(Firework(rng, i * 0.55, rng.uniform(lo, hi), rng.uniform(5, 16),
                                           palette[i % len(palette)], n=24))

    def render(self, t):
        frame = gfx.new_frame()
        for fw in self.fireworks:
            fw.draw(frame, t, 1.0)
        frame[self.halo] = 0
        frame[self.mask] = self.fg[self.mask]
        pulse = 0.85 + 0.15 * math.sin(t * 4)
        frame[self.mask] *= pulse
        return frame


class IntroSlide(Slide):
    """League title card (league logo with a shine) before each league's slides."""

    duration = 3.0

    def __init__(self, league_key):
        from .splash import _shine
        self.key = league_key + ":intro"
        self._shine = _shine
        self.base = gfx.new_frame()
        logo = logos.get_league(league_key)
        if logo is not None:
            gfx.blit_rgb(self.base, logo, (W - logo.shape[1]) // 2, (H - logo.shape[0]) // 2)
        else:
            put(self.base, LEAGUES[league_key].name, "9x18B", W // 2, 8, GOLD, "center")
        self.lit = self.base.max(-1) > 0

    def render(self, t):
        u = (t - 0.6) / 1.0
        if not 0 <= u <= 1:
            return self.base
        frame = self.base.copy()
        self._shine(frame, self.lit, gfx.lerp(-12, W + 22, u), width=3.0, amount=0.7)
        return frame


class BorderSlide(Slide):
    """Wraps a slide in chase lights in the team's colors (after a series win). The border
    sits behind the slide's own pixels so nothing gets covered."""

    SEGMENT = 5      # px per color band
    SPEED = 10.0     # px/s around the edge

    def __init__(self, inner, colors):
        from .splash import _perimeter
        self.inner, self.key, self.duration = inner, inner.key, inner.duration
        self.perim = _perimeter()
        self.idx = np.arange(len(self.perim))
        self.colors = np.array(colors, np.float32)

    def apply_pace(self, pace):
        self.inner.apply_pace(pace)
        self.duration = self.inner.duration
        return self

    def render(self, t):
        frame = np.array(self.inner.render(t), np.float32)
        pos = self.idx + t * self.SPEED
        band = (pos // self.SEGMENT).astype(int) % len(self.colors)
        # Soft ends on each band so it glides instead of flickering.
        within = (pos % self.SEGMENT) / self.SEGMENT
        level = 0.45 + 0.55 * np.sin(within * math.pi)
        cols = self.colors[band] * level[:, None]
        xs, ys = self.perim[:, 0], self.perim[:, 1]
        empty = frame[ys, xs].max(-1) == 0
        frame[ys[empty], xs[empty]] = cols[empty]
        return frame


# ---------------------------------------------------------------- builders
# Compact digits for records under the logos: 3 LEDs wide, 5 tall, a 1-LED dash, so even a
# hockey record (25-15-5) fits beside the date (owner, 2026-10-02).
TINY = {
    "0": ["###", "#.#", "#.#", "#.#", "###"], "1": [".#.", "##.", ".#.", ".#.", "###"],
    "2": ["###", "..#", "###", "#..", "###"], "3": ["###", "..#", ".##", "..#", "###"],
    "4": ["#.#", "#.#", "###", "..#", "..#"], "5": ["###", "#..", "###", "..#", "###"],
    "6": ["###", "#..", "###", "#.#", "###"], "7": ["###", "..#", "..#", ".#.", ".#."],
    "8": ["###", "#.#", "###", "#.#", "###"], "9": ["###", "#.#", "###", "..#", "###"],
    "-": [".", ".", "#", ".", "."],
    ".": [".", ".", ".", ".", "#"],    # fantasy points
}


def tiny_text(s):
    """A record like 25-15-5 in the compact digits (5 rows), 1 LED between characters."""
    parts = []
    for i, ch in enumerate(s):
        if ch not in TINY:
            continue
        if parts:
            parts.append(np.zeros((5, 1), bool))
        parts.append(np.array([[c == "#" for c in r] for r in TINY[ch]], bool))
    return np.concatenate(parts, axis=1) if parts else np.zeros((5, 1), bool)


def _records(data):
    """Every team's current record from the standings, or None outside the regular season
    (preseason: no records yet; playoffs: the series slide covers it)."""
    st = data.standings
    if not st or data.season_type != 2:
        return None
    league = LEAGUES[data.key]
    rows = {r.team_id: r for rs in st.conferences.values() for r in rs}
    rows.update({r.team_id: r for rs in st.divisions.values() for r in rs})
    if not rows or max(r.games for r in rows.values()) == 0:
        return None
    return {tid: _record(league, r) for tid, r in rows.items()}


def _game_frame(data, game, header, header_color, center_fn, bottom_center="", bottom_color=LABEL, names=True,
                header_y=0, records=None):
    """Shared layout: header on top, logos at both sides, abbreviations underneath
    (names=False for live games: the logos say who's playing, the bottom is free).
    records: {team id: "W-L"} shows each team's record under its logo instead (owner)."""
    frame = gfx.new_frame()
    s, f = fit(header, ["5x7", "4x6"], W - 2 * header_y)
    put(frame, s, f, W // 2, header_y, header_color, "center")
    ly = 8
    draw_logo(frame, data.key, game.away.team_id, game.away.abbr, 0, ly, team_color(data.teams.get(game.away.team_id)))
    draw_logo(frame, data.key, game.home.team_id, game.home.abbr, W - LOGO, ly, team_color(data.teams.get(game.home.team_id)))
    center_fn(frame)
    by = 26
    if records and game.away.team_id in records and game.home.team_id in records:
        for side, home in ((game.away, False), (game.home, True)):
            m = tiny_text(records[side.team_id])
            w = m.shape[1]
            cx = W - LOGO // 2 if home else LOGO // 2
            x = cx - w // 2 if w <= LOGO else (W - w if home else 0)   # wide ones hug the edge
            gfx.blit(frame, m, x, by + 1, (215, 215, 225))
    elif names:
        put(frame, game.away.abbr, "4x6", LOGO // 2, by, team_color(data.teams.get(game.away.team_id)), "center")
        put(frame, game.home.abbr, "4x6", W - LOGO // 2, by, team_color(data.teams.get(game.home.team_id)), "center")
    if bottom_center == "@":
        gfx.blit(frame, AT_SIGN, W // 2 - 3, by - 1, bottom_color)
    elif bottom_center:
        s, f = fit(bottom_center, ["4x6"], 30)
        put(frame, s, f, W // 2, by, bottom_color, "center")
    return frame


def last_game_slide(data):
    g = data.last
    me = g.side(data.team.id)
    opp = g.opponent(data.team.id)
    if me.score is None or opp.score is None:
        return None
    color = WIN if me.winner else LOSS if opp.winner else WHITE
    header = g.detail.upper() if g.detail else "FINAL"

    def center(frame):
        narrow = 10 <= max(g.away.score, g.home.score) < 100   # two digits: tall narrow digits
        for side, cx in ((g.away, 24), (g.home, 40)):
            col = WHITE if side.winner or not (g.home.winner or g.away.winner) else GRAY
            if narrow:
                m = narrow_text(str(side.score))
                gfx.blit(frame, m, cx - m.shape[1] // 2, 12, col)
                continue
            put(frame, str(side.score), "7x13B" if side.score < 100 else "6x10", cx, 11, col, "center")
        frame[16, 31:33] = GRAY

    when = local(g.date).strftime("%-m/%-d")
    bottom = short_note(g.note) if g.season_type == 3 and g.note else when
    if g.season_type == 1:
        bottom = "PRE " + when
    return StaticSlide("%s:last:%s" % (data.key, data.team.id),
                       _game_frame(data, g, header, color, center, bottom, records=_records(data)))


def next_game_slide(data):
    g = data.next
    if g.state == "in":
        return None   # live mode shows games in progress
    hm, ampm = time_label(g)

    def center(frame):
        put(frame, hm, "6x10" if len(hm) <= 4 else "5x7", W // 2, 10, WHITE, "center")
        if ampm:
            put(frame, ampm, "4x6", W // 2, 19, LABEL, "center")   # one LED clear of the @

    bottom = short_note(g.note) if g.season_type == 3 and g.note else ("PRE" if g.season_type == 1 else "@")
    head, color = (g.halt_label, LOSS) if g.delayed else (when_label(g.date), GOLD)   # e.g. RAIN DELAY
    return StaticSlide("%s:next:%s" % (data.key, data.team.id),
                       _game_frame(data, g, head, color, center, bottom, records=_records(data)))


def _header(label, color=GOLD):
    hdr = np.zeros((8, W, 3), np.float32)
    s, f = fit(label, ["5x7", "4x6"], W)
    put(hdr, s, f, W // 2, 0, color, "center")
    hdr[7, :] = DIM
    return hdr


def _record(league, r):
    if league.points:
        return "%d-%d-%d" % (r.wins, r.losses, r.ties)
    if league.key == "nfl" and r.ties:
        return "%d-%d-%d" % (r.wins, r.losses, r.ties)
    return "%d-%d" % (r.wins, r.losses)


ARROW = np.array([[c == "#" for c in row] for row in ("#..", "##.", "###", "##.", "#..")])
ABBR_X = 5                   # team names start here; the clinch dot sits in front (x 1-2)
CLINCH = (240, 40, 40)


def _streak(r):
    s = (r.streak or "").upper()
    return s if s[:1] in ("W", "L", "T") and s[1:].isdigit() else "-"


def _columns(league, rows, last_col):
    """Right edges for the record, last and streak columns, spread evenly across the free
    space after the team names. The streak column is only added when everything fits."""
    def width(texts):
        return max([text(t, "4x6").shape[1] for t in texts] or [0])
    name_end = ABBR_X + width([r.abbr for r in rows])
    cols = [width([_record(league, r) for r in rows]), width([last_col(r) for r in rows]),
            width([_streak(r) for r in rows])]
    for n in (3, 2):
        free = (W - 1) - name_end - sum(cols[:n])
        gap = free // n
        if gap >= 4:
            xs, right = [], W - 2
            for w in reversed(cols[:n]):
                xs.insert(0, right)
                right -= w + gap
            return xs + [None] * (3 - n), n == 3
    return [46, 62, None], False
HIGHLIGHTS = ("gold", "bar", "tint", "arrow", "band")


def _rows(data, rows, last_col, favs, cut_after=None, style="gold"):
    """Render standings rows (6 px each), highlighting every favorite team.

    style: "bar"   team-color bar at the left edge, favorite bright, other rows dimmed
           "tint"  background band in the team's own color, white text
           "arrow" gold arrow at the left, gold text
           "band"  dark blue band, gold text (original)
    """
    league = LEAGUES[data.key]
    img = np.zeros((len(rows) * 6, W, 3), np.float32)   # exactly 6 px per row: 4 rows fit with no scroll
    any_fav = any(r.team_id in favs for r in rows)
    xs, show_streak = _columns(league, rows, last_col)
    for i, r in enumerate(rows):
        y = i * 6 + 1
        me = r.team_id in favs
        tc = team_color(data.teams.get(r.team_id))
        x = ABBR_X
        abbr_c, rec_c, last_c = tc, WHITE, GRAY
        if style == "bar":
            x = 4
            if me:
                img[y:y + 5, 0:2] = tc
                rec_c = last_c = WHITE
            elif any_fav:
                abbr_c, rec_c, last_c = tc * 0.5, (120, 120, 130), (70, 70, 80)
        elif style == "tint" and me:
            img[y - 1:y + 6, :] = tc * 0.22
            abbr_c = rec_c = last_c = WHITE
        elif style == "arrow":
            x = 5
            if me:
                gfx.blit(img, ARROW, 0, y, GOLD)
                abbr_c = rec_c = last_c = GOLD
        elif style == "gold":   # favorites in gold, everything else plain white
            abbr_c, rec_c, last_c = (GOLD, GOLD, GOLD) if me else (WHITE, WHITE, WHITE)
        elif me:   # "band": whole row in a deep shade of the team's own color, white text
            img[y - 1:y + 6, :] = band_color(data.teams.get(r.team_id))
            abbr_c = rec_c = last_c = WHITE
        put(img, r.abbr, "4x6", x, y, abbr_c)
        if r.clincher and r.clincher != "e" and style not in ("bar", "arrow"):
            img[y + 2:y + 4, 1:3] = CLINCH   # red 2x2 dot in front of the name: clinched a playoff spot
        put(img, _record(league, r), "4x6", xs[0], y, rec_c, "right")
        put(img, last_col(r), "4x6", xs[1], y, last_c, "right")
        if show_streak:
            s = _streak(r)
            put(img, s, "4x6", xs[2], y, WIN if s.startswith("W") else LOSS if s.startswith("L") else GRAY, "right")
        cuts = cut_after if isinstance(cut_after, (list, tuple)) else [cut_after]
        if i + 1 in cuts:
            img[y + 5, :] = CUT if i + 1 == cuts[0] else (90, 60, 10)   # NBA: amber play-in line
    return img


def division_slide(data, favs, div, style="gold"):
    """One division's standings (shown once, even with several favorites in it)."""
    st = data.standings
    if not st or data.season_type != 2:   # regular season only: no standings once playoffs start
        return None
    rows = st.divisions.get(div) or []
    if not rows or max(r.games for r in rows) == 0:
        return None
    league = LEAGUES[data.key]
    label = div.upper().replace(" DIVISION", "").replace("NATIONAL LEAGUE", "NL").replace("AMERICAN LEAGUE", "AL")
    last = (lambda r: str(r.points)) if league.points else (lambda r: r.gb if r.gb not in ("0", "") else "-")
    return ListSlide("%s:div:%s" % (data.key, div), _header(label), _rows(data, rows, last, favs, style=style))


def wildcard_slide(data, favs, conf, style="gold"):
    """A conference's wild card race (shown once), when it matters for at least one favorite."""
    st = data.standings
    league = LEAGUES[data.key]
    if not st or data.season_type != 2 or league.wildcards == 0:
        return None
    leaders = set()
    for div, rows in st.divisions.items():
        if rows and st.division_of.get(rows[0].team_id, ("",))[0] == conf:
            leaders.update(r.team_id for r in rows[:league.division_spots])
    race = [r for r in st.conferences.get(conf, []) if r.team_id not in leaders]
    mine = [r for r in race if r.team_id in favs and r.clincher != "e"]
    if not mine or max(r.games for r in race) == 0:
        return None
    # Only once the race matters: two-thirds of the season played, or anyone has clinched.
    played = max(r.games for r in st.conferences.get(conf, race)) / league.season_games
    clinched = any(r.clincher and r.clincher != "e" for rows in st.conferences.values() for r in rows)
    if played < 2 / 3 and not clinched:
        return None
    cut = race[league.wildcards - 1] if len(race) >= league.wildcards else race[-1]

    def behind(r):
        if league.points:
            d = cut.points - r.points
            return "+%d" % -d if d < 0 else "-" if d == 0 else str(d)
        d = ((cut.wins - r.wins) + (r.losses - cut.losses)) / 2
        if d <= 0:
            return "+%g" % -d if d < 0 else "-"
        return "%g" % d

    lowest = max(race.index(r) for r in mine)
    cuts = [league.wildcards] + ([league.playin] if league.playin else [])
    shown = race[:max(cuts[-1] + 3, lowest + 2)]
    # NBA: the whole conference race (top 6 playoffs, 7-10 play-in); others: the wild card race.
    label = st.short.get(conf, conf).upper() + (" STANDINGS" if league.playin else " WILD CARD")
    return ListSlide("%s:wc:%s" % (data.key, conf), _header(label),
                     _rows(data, shown, behind, favs, cut_after=cuts, style=style))


ROUND_NAMES = {
    "NLWC": "NL WILD CARD", "ALWC": "AL WILD CARD", "NLDS": "NL DIVISION", "ALDS": "AL DIVISION",
    "WS": "WORLD SERIES",
}


def series_slide(data):
    s = data.series
    g = data.last
    if not s or not g or g.season_type != 3:
        return None
    me = data.team.id
    opp = g.opponent(me).team_id
    mine, theirs = s.wins.get(me, 0), s.wins.get(opp, 0)
    if s.completed:
        status, color = ("WON", WIN) if mine > theirs else ("LOST", LOSS)
    else:
        status, color = ("LEADS", GOLD) if mine > theirs else ("TRAILS", GOLD) if mine < theirs else ("TIED", GOLD)
    a, h = s.wins.get(g.away.team_id, 0), s.wins.get(g.home.team_id, 0)
    title = ROUND_NAMES.get(data.series_round, data.series_round or "PLAYOFFS")

    def center(frame):
        put(frame, "%d-%d" % (a, h), "7x13B", W // 2, 8, WHITE, "center")
        put(frame, "SERIES", "4x6", W // 2, 19, LABEL, "center")

    frame = _game_frame(data, g, title, color if s.completed else GOLD, center, status, color)
    return StaticSlide("%s:series:%s" % (data.key, data.team.id), frame)


def announcement_slide(data):
    if not data.announcement:
        return None
    return CelebrationSlide("%s:announce:%s" % (data.key, data.team.id), news_lines(data.announcement),
                            logos.get(data.key, data.team.id), title=(data.team.name.upper(), team_color(data.team)))


def news_lines(announcement):
    lines = []
    for i, ln in enumerate(announcement):
        s, f = fit(ln, ["5x7", "4x6"], W - LOGO - 2)
        lines.append((s, f, GOLD if i == len(announcement) - 1 else WHITE))
    return lines


def _safe(build, *args):
    try:
        return build(*args)
    except Exception:
        log.exception("%s failed", build.__name__)
        return None


def called_off_slide(data):
    """Today's game was postponed / cancelled (who it was against), or suspended (with the
    score so far)."""
    g = getattr(data, "called_off", None)
    if not g:
        return None
    suspended = g.status == "STATUS_SUSPENDED"

    def center(frame):
        if suspended:
            for side, cx in ((g.away, 24), (g.home, 40)):
                put(frame, str(side.score or 0), "7x13B", cx, 11, WHITE, "center")
            frame[16, 31:33] = GRAY

    return StaticSlide("%s:off:%s" % (data.key, data.team.id),
                       _game_frame(data, g, g.halt_label, LOSS, center, "" if suspended else "@"))


def team_slides(data):
    """One favorite team's own slides: news, a called-off game, last game, next game, playoff series."""
    out = []
    for build in (announcement_slide, called_off_slide, last_game_slide, next_game_slide, series_slide):
        if (build is last_game_slide and not data.last) or (build is next_game_slide and not data.next):
            continue
        s = _safe(build, data)
        if s:
            celebrate = any(":%s:" % kind in s.key for kind in ("announce", "last", "series"))
            if celebrate and time.time() < data.celebrate_until:
                s = BorderSlide(s, team_colors(data.team))
            out.append(s)
    return out


def league_slides(teams, style="gold", rivals=None):
    """All slides for one league's favorite teams, in a fixed order with no repeats:
    teams grouped by conference and division; each team's own slides, then that
    division rivals' last / next games, then that division's standings once; then the
    conference wild card race once. A team watched just for today gets its game slides
    but no standings (owner, 2026-10-02)."""
    teams = [d for d in teams if d.in_window]
    if not teams:
        return []
    st = teams[0].standings
    favs = {d.team.id for d in teams if getattr(d, "role", "fav") == "fav"}   # highlighted in standings
    rivals = [r for r in (rivals or []) if r.in_window]
    shown = {g.id for d in teams for g in (d.last, d.next) if g}   # games already on a favorite's slides

    def where(d):
        return st.division_of.get(d.team.id, ("~", "~")) if st else ("~", "~")

    # Follow the owner's order of favorites: the first team's conference and division come
    # first, and teams sharing a division stay together.
    idx = {d.team.id: i for i, d in enumerate(teams)}
    conf_rank = {}
    div_rank = {}
    for d in teams:
        conf_rank.setdefault(where(d)[0], idx[d.team.id])
        div_rank.setdefault(where(d), idx[d.team.id])
    ordered = sorted(teams, key=lambda d: (conf_rank[where(d)[0]], div_rank[where(d)], idx[d.team.id]))
    out = []
    for conf in dict.fromkeys(where(d)[0] for d in ordered):
        in_conf = [d for d in ordered if where(d)[0] == conf]
        for div in dict.fromkeys(where(d)[1] for d in in_conf):
            in_div = [d for d in in_conf if where(d)[1] == div]
            for d in in_div:
                out += team_slides(d)
            if div != "~" and any(d.team.id in favs for d in in_div):
                for r in rivals:                  # the rest of the division, like favorites
                    if where(r) != (conf, div):
                        continue
                    if r.last and r.last.id not in shown:
                        s = _safe(last_game_slide, r)
                        if s:
                            out.append(s)
                    if r.next and r.next.id not in shown:
                        s = _safe(next_game_slide, r)
                        if s:
                            out.append(s)
                s = _safe(division_slide, in_div[0], favs, div, style)   # standings after all the games
                if s:
                    out.append(s)
        if conf != "~" and any(d.team.id in favs for d in in_conf):
            s = _safe(wildcard_slide, in_conf[0], favs, conf, style)
            if s:
                out.append(s)
    if out:
        out.insert(0, IntroSlide(teams[0].key))
    return out


def occasion_slides(settings, today=None):
    """Birthday / anniversary slides on the day."""
    today = today or datetime.date.today()
    out = []
    for i, ev in enumerate(settings.get("events") or []):
        try:
            month, day = (int(x) for x in str(ev.get("date", ""))[-5:].split("-"))
        except ValueError:
            continue
        if (month, day) != (today.month, today.day):
            continue
        name = str(ev.get("name", "")).upper()
        if ev.get("type") == "anniversary":
            lines = [("HAPPY", "5x7", WHITE), ("ANNIVERSARY", "5x7", GOLD)]
        else:
            lines = [("HAPPY", "5x7", WHITE), ("BIRTHDAY", "5x7", GOLD)]
        s, f = fit(name, ["7x13B", "6x10", "5x7", "4x6"], W - 2)
        lines.append((s, f, (40, 220, 255)))
        out.append(CelebrationSlide("occasion:%d" % i, lines, seed=11 + i))
    return out


def build(state):
    slides = occasion_slides(state.settings)
    try:                                          # photos from the photo page
        from . import photos
        state.photo_only, rotation = photos.slides(state.settings.get("brightness", 60))
        slides += rotation
    except Exception:
        log.exception("photo slides")
    for key in ORDER:
        rivals = state.rivals.get(key) if settings_mod.league_option(state.settings, "show_rivals", key) else None
        slides += league_slides(state.leagues.get(key) or [], state.settings.get("highlight", "gold"), rivals)
        if key == "nfl":                          # his fantasy matchups, results, championships
            try:
                from . import fantasy_slides
                slides += fantasy_slides.slides(state)
            except Exception:
                log.exception("fantasy slides")
    pace = settings_mod.pace(state.settings)
    return [s.apply_pace(pace) for s in slides]


class SlidesWorker(Worker):
    """Rebuilds the slide list when league data, settings or the date change."""

    interval = 3.0

    def __init__(self, state, stop):
        super().__init__(state, stop)
        self.seen = None

    def step(self):
        from . import photos
        stamp = (self.state.data_version, id(self.state.settings), datetime.date.today(),
                 datetime.datetime.now().hour, tuple(r["id"] for r in photos.active()))   # photos come and go
        if stamp != self.seen:
            self.seen = stamp
            self.state.slides = build(self.state)
            log.info("Slides: %s", ", ".join(s.key for s in self.state.slides) or "none")


# Tall narrow digits for big scores (owner, 2026-10-03: two-digit NFL scores crowded each
# other in the bold font): 11 LEDs tall, 5 wide, 2-LED strokes.
NARROW = {
    "0": [".###.", "##.##", "##.##", "##.##", "##.##", "##.##", "##.##", "##.##", "##.##", "##.##", ".###."],
    "1": ["..##.", ".###.", "####.", "..##.", "..##.", "..##.", "..##.", "..##.", "..##.", "..##.", ".####"],
    "2": [".###.", "##.##", "...##", "...##", "..##.", "..##.", ".##..", ".##..", "##...", "##...", "#####"],
    "3": ["####.", "...##", "...##", "...##", ".###.", ".###.", "...##", "...##", "...##", "...##", "####."],
    "4": ["##.##", "##.##", "##.##", "##.##", "##.##", "#####", "#####", "...##", "...##", "...##", "...##"],
    "5": ["#####", "##...", "##...", "##...", "####.", "...##", "...##", "...##", "...##", "...##", "####."],
    "6": [".###.", "##...", "##...", "##...", "####.", "##.##", "##.##", "##.##", "##.##", "##.##", ".###."],
    "7": ["#####", "...##", "...##", "...##", "..##.", "..##.", "..##.", ".##..", ".##..", ".##..", ".##.."],
    "8": [".###.", "##.##", "##.##", "##.##", ".###.", "##.##", "##.##", "##.##", "##.##", "##.##", ".###."],
    "9": [".###.", "##.##", "##.##", "##.##", "##.##", ".####", "...##", "...##", "...##", "...##", ".###."],
}


def narrow_text(s):
    """A score in the tall narrow digits (11 rows), 1 LED between digits."""
    parts = []
    for ch in s:
        if ch in NARROW:
            if parts:
                parts.append(np.zeros((11, 1), bool))
            parts.append(np.array([[c == "#" for c in r] for r in NARROW[ch]], bool))
    return np.concatenate(parts, axis=1) if parts else np.zeros((11, 1), bool)
