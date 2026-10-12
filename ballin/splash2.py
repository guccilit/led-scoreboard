"""Startup show v2: Cubs - Blackhawks - Bears - logo, then a 40th birthday finale
(shown through BIRTHDAY_UNTIL; afterwards the show ends with the logo).

  Cubs        Wrigley Field from across the street at night, red marquee sign; the view
              zooms over the stands into the green center-field scoreboard, where the line
              scores become a bouncing-ball sing-along: HEY CHICAGO, WHAT DO YOU SAY, THE CUBS
              ARE GONNA WIN TODAY
  Blackhawks  red goal light spinning, "DA DA DA / DA DA" on the beat (the goal song chant),
              then the logo
  Bears       a navy board; claw marks rip through it, BEAR DOWN shows through the tears,
              the board falls away
  Logo        BEAR DOWN bursts into sparks that build JACKSON'S BALLIN' BOARD, gold marquee
  Birthday    stadium spotlights + gold/white/red/blue fireworks, HAPPY 40TH BIRTHDAY in
              metallic gold, then JACKSON with two beer mugs clinking: CHEERS TO 40!

Show(...).render(t) returns a uint8 (32, 64, 3) frame; call with increasing t.
"""
import datetime
import math

import numpy as np
from PIL import Image

from . import fonts, gfx, logos
from .gfx import H, W
from .splash import Firework, _build_logo, _gradient, _perimeter, _shine, _typed

BIRTHDAY_UNTIL = datetime.date(2026, 11, 25)     # removed from 2026-11-26 on (owner)

WHITE = (240, 240, 245)
GOLD = (255, 185, 20)
CUBS_BLUE = (30, 70, 200)
HAWKS_RED = (220, 20, 30)
BEARS_NAVY = (12, 22, 58)
BEARS_ORANGE = (235, 85, 15)

# Scene start times (seconds)
T_HAWKS = 14.0         # Cubs scene: Wrigley, then the "Go Cubs Go" sing-along on the scoreboard
T_BEARS = 19.0         # Hawks scene: goal light, the DO-DO-DO chant, the logo
T_LOGO = 25.0          # Bears scene (6 s): claws, BEAR DOWN swallowed by the bear face, GOOD BETTER BEST
SPARKS = 1.6          # sparks fly into the logo for this long
T_LOGO_END = T_LOGO + 6.5      # the logo holds ~5 s
CRUMBLE = 0.7
BDAY_LEN = 9.6

# Cubs sing-along ("Go Cubs Go") on Wrigley's scoreboard, seen whole: one line at a time
# flips in panel by panel like the hand-turned board, a baseball bounces from syllable to
# syllable (each one pops gold as it lands), the pennants pulse on the beat, and it ends
# with the W flag flipping up on the board, waving.
SING_AT = 7.0
LYRICS = ("HEY JACKSON", "WHAT DO", "YOU SAY", "THE CUBS", "ARE GONNA", "WIN TODAY")
SYLLABLES = ((0, "HEY", 0.0), (0, "JACK", 0.31), (0, "SON", 0.55),          # (line, text, seconds after SING_AT)
             (1, "WHAT", 1.06), (1, "DO", 1.31),
             (2, "YOU", 1.79), (2, "SAY", 2.03),
             (3, "THE", 2.55), (3, "CUBS", 2.79),
             (4, "ARE", 3.26), (4, "GON", 3.51), (4, "NA", 3.71),
             (5, "WIN", 4.2), (5, "TO", 4.48), (5, "DAY", 4.71))
LINE_AT = (0.0, 0.69, 1.41, 2.18, 2.89, 3.83)  # when each line flips in
FLAG_AT = 5.25                                 # the W flag flips in
SING_END = 6.95
FLIP = 0.2                                     # one panel's flip
STAGGER = 0.01                                 # panels go left to right
LYRIC_Y = 21                                   # 5x7 line on the board; the ball bounces above
BODY = (slice(13, 31), slice(3, 61))           # the board's face on the panel
UNSUNG, SUNG = (95, 110, 100), WHITE
STARTUP_UNSUNG = (150, 165, 150)               # startup sing-along: brighter, reads against the board
CUBS_GAMMA, CUBS_GAIN = 1.35, 1.4               # contrast lift for the startup show's Wrigley scene

_YY, _XX = np.mgrid[0:H, 0:W].astype(np.float32)
# Spotlight beams from the bottom corners: angle and distance falloff for every pixel.
SPOT_GRIDS = [(np.arctan2(_YY - 33, _XX - ox), np.clip(1 - np.hypot(_XX - ox, _YY - 33) / 70, 0, 1))
              for ox in (0.0, 63.0)]


def birthday_on(today=None):
    return (today or datetime.date.today()) <= BIRTHDAY_UNTIL


def _t(s, font):
    return fonts.text_mask(s, font)[0]


def _put(frame, s, font, cx, y, color):
    m = _t(s, font)
    gfx.blit(frame, m, cx - m.shape[1] // 2, y, color)
    return m


def _metal(h, w):
    """Metallic gold, light at the top, deep at the bottom."""
    t = np.linspace(0, 1, h, dtype=np.float32)[:, None]
    top, mid, bot = np.array((255, 238, 160)), np.array((245, 180, 35)), np.array((170, 100, 10))
    col = np.where(t < 0.5, top + (mid - top) * (t / 0.5), mid + (bot - mid) * ((t - 0.5) / 0.5))
    return np.broadcast_to(col[:, None, :], (h, w, 3)).astype(np.float32).copy()


# ------------------------------------------------------------------- Cubs
VW, VH = 192, 96          # the Wrigley scene is drawn 3x the panel size, then zoomed


BOARD_GREEN = (12, 72, 40)
BOARD_EDGE = (6, 40, 22)
FLAG_COLORS = [(220, 30, 40), (40, 80, 220), (255, 190, 20), (240, 240, 240), (20, 140, 70),
               (255, 110, 20), (120, 40, 160), (200, 200, 60)]


def _classic_scoreboard(c, cx, rng, scores=True):
    """Wrigley's center-field scoreboard as it looked in the early 2000s, 60 px wide:
    a wide dark-green board, the clock on a raised center section, team pennants flying
    on poles along the top (the standings flags), and rows of line scores (a white
    team-name plate, then inning numbers), NL games on the left, AL on the right."""
    x0, x1, top, bot = cx - 30, cx + 30, 14, 34
    # Flagpoles with stacked pennants
    for k, px in enumerate((cx - 26, cx - 21, cx - 16, cx + 15, cx + 20, cx + 25)):
        c[2:top, px] = (120, 120, 130)
        for j, fy in enumerate((2, 5, 8)):
            col = FLAG_COLORS[(k * 3 + j) % len(FLAG_COLORS)]
            c[fy:fy + 2, px + 1:px + 4] = col
    # Raised center section with the clock
    c[7:top, cx - 8:cx + 8] = BOARD_GREEN
    c[7, cx - 8:cx + 8] = BOARD_EDGE
    for y in range(5, 15):
        for x in range(cx - 6, cx + 6):
            d = (x - cx + 0.5) ** 2 + (y - 9.5) ** 2
            if d <= 13:
                c[y, x] = (238, 238, 228)
            elif d <= 18:
                c[y, x] = (30, 30, 30)
    c[9, cx:cx + 3] = (20, 20, 20)            # clock hands
    c[7:10, cx] = (20, 20, 20)
    # Main board
    c[top:bot, x0:x1] = BOARD_GREEN
    c[top, x0:x1] = c[bot - 1, x0:x1] = BOARD_EDGE
    c[top:bot, x0] = c[top:bot, x1 - 1] = BOARD_EDGE
    c[top + 1:bot - 1, cx] = BOARD_EDGE        # NL | AL
    for half in (x0 + 2, cx + 2):
        for i in range(9):                     # inning numbers header
            c[top + 2, half + 5 + i * 2] = (200, 200, 200)
        for r in range(5 if scores else 0):      # five games per side
            y = top + 4 + r * 3
            c[y, half:half + 4] = (240, 240, 240)         # team-name plate
            for i in range(9):
                if rng.random() < 0.75:
                    c[y, half + 5 + i * 2] = (245, 210, 70) if rng.random() < 0.8 else (240, 240, 240)


def _wrigley(scores=True):
    rng = np.random.default_rng(1876)
    c = np.zeros((VH, VW, 3), np.float32)
    for y in range(VH):
        c[y, :] = gfx.lerp(np.array((1, 2, 8.)), np.array((6, 6, 16.)), min(1, y / 60))   # night sky, near black on LEDs
    for _ in range(40):                                   # stars
        c[rng.integers(0, 30), rng.integers(0, VW)] = rng.uniform(50, 110)
    # Glow of the field lights over the stands
    yy, xx = np.mgrid[0:VH, 0:VW]
    glow = np.exp(-(((xx - 96) / 90.0) ** 2 + ((yy - 34) / 16.0) ** 2))
    c += glow[..., None] * np.array((12, 12, 9))
    # Stands
    c[34:62, 6:186] = (7, 7, 11)
    c[33, 6:186] = (40, 40, 48)
    for x in range(10, 184, 6):
        c[36:60:4, x:x + 3] = (14, 14, 20)
    # Light towers
    for x in (14, 176):
        c[18:62, x:x + 2] = (70, 70, 80)
        c[12:18, x - 6:x + 8] = (255, 250, 225)
    _classic_scoreboard(c, 96, rng, scores)
    # Brick facade with arches, street
    c[62:92, :] = (105, 38, 28)
    for x in range(6, 190, 16):
        c[72:90, x:x + 9] = (12, 5, 4)
        c[71, x + 1:x + 8] = (12, 5, 4)
    c[92:, :] = (10, 10, 12)
    # The red marquee sign on its post, exactly panel-wide so it's crisp when zoomed to 1:1
    c[63:92, 92:100] = (45, 45, 50)
    c[42:63, 64:128] = (205, 24, 34)
    c[42, 64:128] = c[62, 64:128] = (250, 250, 250)
    c[42:63, 64] = c[42:63, 127] = (250, 250, 250)
    wr, fi = _t("WRIGLEY", "5x7"), _t("FIELD", "5x7")
    x = 96 - (wr.shape[1] + 3 + fi.shape[1]) // 2
    gfx.blit(c, wr, x, 45, (250, 250, 250))
    gfx.blit(c, fi, x + wr.shape[1] + 3, 45, (250, 250, 250))
    c[53:61, 66:126] = (8, 8, 8)                         # message board (text added per frame)
    return np.clip(c, 0, 255)


# ------------------------------------------------------------------- Bears
def _claw_paths():
    """Three parallel slashes from top right to bottom left, slightly curved."""
    paths = []
    for i, x0 in enumerate((30, 39, 48)):
        pts = []
        for k in range(60):
            u = k / 59
            x = x0 - 20 * u + 2.5 * math.sin(u * math.pi)
            y = -2 + 36 * u
            pts.append((x, y, 0.9 + 1.4 * math.sin(u * math.pi)))   # half-width: thin ends, wide middle
        paths.append(pts)
    return paths


BEAR_NAVY = (28, 44, 105)
BEAR_TOP = (40, 62, 135)
BEAR_EDGE = (235, 85, 15)


def ditka():
    """Da Coach, as just the essentials: aviator sunglasses (silver frame, dark lenses with
    a glare) and the mustache. Full panel size; black is transparent."""
    yy, xx = np.mgrid[0:H, 0:W].astype(np.float32) + 0.5
    img = gfx.new_frame()

    def ell(cx, cy, rx, ry):
        return ((xx - cx) / rx) ** 2 + ((yy - cy) / ry) ** 2 <= 1

    frame_c, lens_c, glare_c = (190, 190, 200), (35, 45, 70), (250, 250, 255)
    for cx in (20.5, 43.5):                                   # aviator lenses: flat top, round bottom
        lens = ell(cx, 8.2, 9.0, 6.4) & (yy >= 3.5)
        rim = lens & ~(np.roll(lens, 1, 0) & np.roll(lens, -1, 0) & np.roll(lens, 1, 1) & np.roll(lens, -1, 1))
        img[lens] = lens_c
        img[rim] = frame_c
        glare = lens & ~rim & (np.abs((xx - cx) + (yy - 8) * 0.9 + 2.5) < 0.9)
        img[glare] = glare_c
    img[3, 26:38] = frame_c                                   # bridge / brow bar
    img[4, 29:35] = frame_c
    img[4, 1:12] = img[4, 52:63] = frame_c                    # temple arms
    # The mustache: a thick horseshoe under the nose
    stache = (ell(32, 20.5, 14.0, 3.6) & ~ell(32, 17.2, 4.0, 2.2)) | \
        ((np.abs(xx - 18.8) < 2.2) & (yy > 20) & (yy < 27.5)) | ((np.abs(xx - 45.2) < 2.2) & (yy > 20) & (yy < 27.5))
    img[stache] = (150, 100, 55)
    img[stache & (yy < 19.5)] = (175, 125, 75)                # lighter on top
    img[stache & (np.abs(xx - 32) % 3 < 0.8) & (yy > 20.5)] = (115, 75, 40)   # bristles
    return img


DITKA = ditka()


# Bears scene (seconds into it): claws, BEAR DOWN, the bear head eats it, then the chant.
EAT = 2.4             # BEAR DOWN gets smashed (name kept for the team intro)
DITKA_IN, DITKA_LAND, DITKA_OUT = 2.75, 3.0, 3.85
CHANT = (("GOOD", "5x7", WHITE, 2, 4.1), ("BETTER", "6x10", BEARS_ORANGE, 11, 4.5),
         ("BEST", "7x13B", (255, 125, 30), 21, 4.95))         # word, font, color, y, when
SLAM = 0.12           # how long a chant word takes to drop into place
BEARS_LEN = 6.0


MUG = gfx.sprite([
    ".wwwwwww...",
    "wwwwwwwww..",
    "gaaaaaaag..",
    "gaaaaaaaggg",
    "gaayaaaag.g",
    "gaaaaaaag.g",
    "gaaaayaag.g",
    "gaayaaaag.g",
    "gaaaaaaaggg",
    "gaaaaaaag..",
    "ggggggggg..",
], {"w": (250, 250, 245), "g": (170, 170, 185), "a": (235, 150, 15), "y": (255, 225, 120)})


# The Blackhawks goal chant (Chelsea Dagger): DO-DO-DO, DO-DO-DO, then a run of seven, on the
# beat (owner, 2026-10-02). Seconds into the Hawks scene; the logo follows the chant.
HAWKS_BEATS = (0.30, 0.50, 0.70, 1.10, 1.30, 1.50, 1.90, 2.08, 2.26, 2.44, 2.62, 2.80, 2.98)
HAWKS_LOGO_AT = 3.35


class Show:
    WRIGLEY_LOAD = 0.14     # owner found Wrigley muted (2026-10-04), but the supply already dips here at 0.14
                            # (3 s; 5 s at 0.22 and 0.30): stays until a stronger power supply

    @property
    def max_load(self):
        """The power cap for the frame just drawn: more light for the Wrigley sing-along only;
        the rest of the show (white flashes, sparks) keeps the shows' usual cap."""
        if self.last_t is not None and self.last_t < self.t_hawks:
            return self.WRIGLEY_LOAD
        from .app import SHOW_MAX_LOAD
        return SHOW_MAX_LOAD

    def __init__(self, birthday=None, seed=40):
        self.birthday = birthday_on() if birthday is None else birthday
        self.duration = T_LOGO_END + (CRUMBLE + BDAY_LEN if self.birthday else 0)
        rng = np.random.default_rng(seed)
        self.rng = rng

        # Cubs
        self.wrigley = _wrigley()
        self._morph_setup(rng)
        # The Cubs scene ends when the sing-along does (it follows the owner's Go Cubs Go taps);
        # the rest of the show shifts to match.
        self.t_hawks = SING_AT + self.sing_end + 0.05
        self.shift = self.t_hawks - T_HAWKS
        self.duration += self.shift
        self.welcome, self.gocubs = _t("WELCOME JACKSON", "4x6"), _t("GO CUBS GO", "4x6")

        # Blackhawks
        self.da = _t("DO", "7x13B")
        self.da_small = _t("DO", "6x10")
        self.beats = list(HAWKS_BEATS)
        self.hawks_logo = logos.get("nhl", "4")
        self.beam_angle = np.arctan2(_YY - 1, _XX - 31.5)
        self.beam_dist = np.hypot(_YY - 1, _XX - 31.5)

        # Bears
        self.bd = _t("BEAR DOWN", "7x13B")
        self.bd_x, self.bd_y = (W - self.bd.shape[1]) // 2, (H - self.bd.shape[0]) // 2
        self.bears_back = gfx.new_frame()
        gfx.blit(self.bears_back, self.bd, self.bd_x + 1, self.bd_y + 1, (90, 30, 5))
        gfx.blit(self.bears_back, self.bd, self.bd_x, self.bd_y, BEARS_ORANGE)
        self.claws = _claw_paths()
        self.claw_start = [0.45, 0.58, 0.71]
        # Each slash as it grows, worked out once (drawing them live was too slow).
        self.claw_torn, self.claw_edge = [], []
        for path in self.claws:
            torn, edge, ts, es = np.zeros((H, W), bool), np.zeros((H, W), np.float32), [], []
            for x, y, hw in path:
                d = np.abs(_XX - x) * 0.9 + np.abs(_YY - y) * 0.45
                torn = torn | (d < hw * 0.8)
                edge = np.maximum(edge, np.clip(1.6 - np.abs(d - hw), 0, 1))
                ts.append(torn)
                es.append(edge)
            self.claw_torn.append(ts)
            self.claw_edge.append(es)
        self.fall_delay = rng.uniform(0, 0.35, (H, W))
        self.fall_v = rng.uniform(-6, 6, (H, W))
        # The bear head that eats BEAR DOWN, then the coach's chant: GOOD / BETTER / BEST
        by, bx = np.nonzero(self.bears_back.max(-1) > 0)            # BEAR DOWN shatters
        self.sh_x, self.sh_y = bx.astype(np.float32), by.astype(np.float32)
        self.sh_col = self.bears_back[by, bx]
        self.sh_vx = (bx - W / 2) * rng.uniform(0.6, 1.6, len(bx)) + rng.uniform(-8, 8, len(bx))
        self.sh_vy = -rng.uniform(8, 30, len(bx))
        self.ditka_lens = (DITKA.max(-1) > 0) & (DITKA[..., 2] > DITKA[..., 0]) & (_YY < 16)
        self.chant = []
        self.chant_frame = gfx.new_frame()
        for word, font, color, y, when in CHANT:
            m = _t(word, font)
            x = (W - m.shape[1]) // 2
            self.chant.append((m, x, y, np.array(color, np.float32), when))
            gfx.blit(self.chant_frame, m, x, y, color)

        # Logo, built from sparks that burst out of the chant
        self.logo, self.ballin_lit = _build_logo(_metal, shadow=(75, 40, 0))   # metallic gold BALLIN'
        self.logo_lit = self.logo.max(-1) > 0
        ty, tx = np.nonzero(self.logo_lit)
        self.ty, self.tx = ty.astype(np.float32), tx.astype(np.float32)
        self.tcol = self.logo[ty, tx]
        cy_, cx_ = np.nonzero(self.chant_frame.max(-1) > 0)
        pick = rng.integers(0, len(cx_), len(tx))
        self.sx, self.sy = cx_[pick].astype(np.float32), cy_[pick].astype(np.float32)
        ang = rng.uniform(0, 2 * math.pi, len(tx))
        spd = rng.uniform(10, 45, len(tx))
        self.pvx, self.pvy = np.cos(ang) * spd, np.sin(ang) * spd
        self.pdelay = 0.25 + 0.6 * (self.tx / W) + rng.uniform(0, 0.12, len(tx))
        self.pcol = self.chant_frame[cy_[pick], cx_[pick]]
        self.perim = _perimeter()
        self.crumble_delay = rng.uniform(0, 0.3, len(tx))

        if self.birthday:
            self._birthday_setup(rng)
        self.prev = gfx.new_frame()
        self.last_t = None

    # ------------------------------------------------------------- render
    def render(self, t):
        dt = 1 / 40 if self.last_t is None else max(1e-3, t - self.last_t)
        self.last_t = t
        frame = gfx.new_frame()
        trail = 0.0
        if t >= self.t_hawks:                    # after the Cubs, the show runs on its usual clock
            t -= self.shift
        else:
            self._cubs(frame, t)
            t = -1.0
        if t < 0:
            pass
        elif t < T_HAWKS:
            self._cubs(frame, t)
        elif t < T_BEARS:
            self._hawks(frame, t - T_HAWKS)
            trail = 0.35
        elif t < T_LOGO:
            frame = self._bears(t - T_BEARS)
        elif t < T_LOGO_END:
            self._logo(frame, t - T_LOGO)
            trail = 0.5 if t - T_LOGO < SPARKS else 0.0
        elif self.birthday and t < T_LOGO_END + CRUMBLE:
            self._crumble(frame, t - T_LOGO_END)
            trail = 0.4
        elif self.birthday:
            self._birthday(frame, t - T_LOGO_END - CRUMBLE)
            trail = 0.35
        else:
            self._logo(frame, t - T_LOGO)
        if trail:
            frame = np.maximum(frame, self.prev * trail ** (dt * 40))
        self.prev = frame
        return gfx.to_uint8(frame)

    # ------------------------------------------------------------- Cubs
    def _morph_setup(self, rng):
        """The scoreboard at 1:1 (the camera's final view): its line-score dots will fly
        together into the first line of the sing-along."""
        clean = _wrigley(scores=False)
        view = (slice(2, 34), slice(64, 128))            # the panel-sized view of the scoreboard
        self.board_view = clean[view].copy()
        # For the sing-along the board's face is cleared (no NL|AL divider or inning numbers).
        self.sing_back = self.board_view.copy()
        self.sing_back[BODY] = BOARD_GREEN
        # The pennants (colored pixels above the board, not the clock) pulse on the beat.
        top = self.board_view[:12]
        sat = top.max(-1) - top.min(-1)
        self.pennants = np.zeros((H, W), bool)
        self.pennants[:12] = (sat > 60) | (top.min(-1) > 200)
        self.pennants[:, 25:40] = False
        full = self.wrigley[view]
        dy, dx = np.nonzero(np.abs(full - self.board_view).max(-1) > 20)
        src_col = full[dy, dx]
        self._sing_setup()
        target = gfx.new_frame()
        m, x, _ = self.lines[0]
        gfx.blit(target, m, x, LYRIC_Y, UNSUNG)          # the first line, not yet sung
        ty, tx = np.nonzero(target.max(-1) > 0)
        # Pair targets with dots left to right, so the letters build as a sweep.
        src_order = np.argsort(dx + rng.uniform(0, 3, len(dx)))
        tgt_order = np.argsort(tx + rng.uniform(0, 3, len(tx)))
        pick = src_order[(np.arange(len(tx)) * len(dx)) // len(tx)]
        self.m_sx, self.m_sy = dx[pick].astype(np.float32), dy[pick].astype(np.float32)
        self.m_scol = src_col[pick]
        self.m_tx, self.m_ty = tx[tgt_order].astype(np.float32), ty[tgt_order].astype(np.float32)
        self.m_tcol = target[ty[tgt_order], tx[tgt_order]]
        self.m_delay = 0.55 * (self.m_tx / W) + rng.uniform(0, 0.12, len(tx))
        self.m_dots = (dy, dx, src_col)
        self.m_lit = target.max(-1) > 0

    def _sing_setup(self):
        """Each lyric line (mask, x, char edges); where the ball lands on each syllable:
        (x, row, time, line, column span); and the W flag."""
        self.lines = []
        for text_ in LYRICS:
            m, edges = fonts.text_mask(text_, "5x7")
            self.lines.append((m, (W - m.shape[1]) // 2, edges))
        self._sing_timing()
        self.syl = []
        cursor = {}
        for (ln, part, _), at in zip(SYLLABLES, self.syl_times):
            m, x, edges = self.lines[ln]
            k = LYRICS[ln].index(part, cursor.get(ln, 0))
            cursor[ln] = k + len(part)
            a = edges[k - 1] if k else 0
            b = min(m.shape[1], edges[k + len(part) - 1])
            self.syl.append((x + (a + b) / 2 - 1.5, LYRIC_Y - 4, at, ln, (a, b)))
        # The W flag: white with a big blue W, filling the middle of the board.
        flag = np.zeros((18, W, 3), np.float32)
        flag[1:17, 16:48] = (250, 250, 250)
        w = gfx.scale_mask(_t("W", "7x13B"), 1.15)
        w = np.pad(w, ((0, 0), (0, 1))) | np.pad(w, ((0, 0), (1, 0)))      # extra bold
        gfx.blit(flag, w, 32 - w.shape[1] // 2, 9 - w.shape[0] // 2, (25, 60, 255))
        self.w_flag = flag
        rng = np.random.default_rng(7)
        self.spark_v = rng.uniform(-1, 1, (6, 2)) * (14, 10) - (0, 6)

    def _band(self, ln, u):
        """One lyric line as an 18-row band (the board face), colored by how far the song is."""
        band = np.zeros((18, W, 3), np.float32)
        m, x, _ = self.lines[ln]
        y = LYRIC_Y - 13
        gfx.blit(band, m, x, y, STARTUP_UNSUNG)
        for i, (_, _, at, sl, (a, b)) in enumerate(self.syl):
            if sl != ln or u < at:
                continue
            nxt = self.syl[i + 1][2] if i + 1 < len(self.syl) and self.syl[i + 1][3] == ln else at + 0.25
            band[y:y + m.shape[0], x + a:x + b] = 0
            if u < at + 0.08:                                      # pops up as the ball lands
                gfx.blit(band, m[:, a:b], x + a, y - 1, (255, 225, 90))
            else:
                gfx.blit(band, m[:, a:b], x + a, y, GOLD if u < nxt else SUNG)
        return band

    @staticmethod
    def _flip(old, new, u):
        """Hand-turned panels: 5-column panels turn over left to right, old face out, new
        face in (a vertical squash through edge-on)."""
        h = old.shape[0]
        out = np.zeros_like(old)
        c = (h - 1) / 2
        rows = np.arange(h)
        for k, x0 in enumerate(range(0, W, 5)):
            p = gfx.clamp01((u - k * STAGGER) / FLIP)
            src = old if p < 0.5 else new
            scale = abs(math.cos(math.pi * p))
            if scale < 0.08:
                out[int(c):int(c) + 2, x0:x0 + 4] = (30, 70, 45)     # the panel edge-on
                continue
            sr = np.round(c + (rows - c) / scale).astype(int)
            ok = (sr >= 0) & (sr < h)
            out[rows[ok], x0:x0 + 5] = src[sr[ok], x0:x0 + 5] * (0.55 + 0.45 * scale)
        return out

    def _sing_timing(self):
        """When each syllable lands: the owner's Go Cubs Go taps for the same line (first pass)
        when saved, with JACK on CHI and SON on GO so JACKSON spans CHICAGO (owner, 2026-10-03);
        otherwise the built-in timing. Sets syl_times, line_at, flag_at, sing_end."""
        times = [at for _, _, at in SYLLABLES]
        try:
            from .song_timing import GOCUBSGO as taps
            if taps and len(taps) == len(gocubsgo_units()):
                n_slams = len(SONG_SLAMS)
                n_syl = len(gocubsgo_units()) // SONG_PASSES - n_slams
                line = taps[n_slams:n_slams + n_syl]          # the sung lines, first pass
                rel = [x - line[0] for x in line]
                mapped = [rel[0], rel[1], rel[3]] + rel[4:]   # HEY, JACK = CHI, SON = GO, then the rest
                if len(mapped) == len(times):
                    times = mapped
        except Exception:
            pass
        self.syl_times = times
        firsts, seen = [], set()
        for (ln, _, _), at in zip(SYLLABLES, times):
            if ln not in seen:
                seen.add(ln)
                firsts.append(at)
        self.line_at = [0.0] + [max(firsts[i - 1] + 0.1, f - 0.3) for i, f in enumerate(firsts) if i]
        self.flag_at = times[-1] + 0.54
        self.sing_end = self.flag_at + 1.7

    def _sing(self, frame, u):
        """The sing-along at u seconds after SING_AT (u may be slightly negative: the ball
        bounces in while the line scores are still settling into the first line)."""
        if u >= 0:
            frame[:] = self.sing_back
            beat = min((u - at for _, _, at, _, _ in self.syl if u >= at), default=9.0)
            frame[self.pennants] *= 1 + 0.9 * math.exp(-beat / 0.15)
            ln = max(i for i, at in enumerate(self.line_at) if u >= at)
            if u >= self.flag_at:                                 # the W flag, waving
                v = u - self.flag_at
                flag = self.w_flag
                if v >= FLIP + 0.3:
                    wave = np.zeros_like(flag)
                    for x in range(16, 48):
                        ph = x * 0.35 - v * 9
                        dy = int(round(1.2 * math.sin(ph)))
                        wave[max(0, dy):18 + min(0, dy), x] = flag[max(0, -dy):18 - max(0, dy), x] * \
                            (0.9 + 0.1 * math.cos(ph))
                    band = wave
                else:
                    band = self._flip(self._gold_line(), flag, v)
            elif ln > 0 and u < self.line_at[ln] + FLIP + 13 * STAGGER:
                band = self._flip(self._band(ln - 1, u), self._band(ln, u), u - self.line_at[ln])
            else:
                band = self._band(ln, u)
                last = self.syl[-1][2]
                if ln == len(LYRICS) - 1 and u >= last + 0.25:     # ... WIN TODAY! in gold, with a shine
                    full = gfx.new_frame()                 # (the shine works on whole frames)
                    full[BODY[0]] = self._gold_line()
                    _shine(full, full.max(-1) > 0, gfx.lerp(-12, W + 22, (u - last - 0.25) / 0.5))
                    band = full[BODY[0]]
            face = frame[BODY[0]]
            lit = band.max(-1) > 0
            face[lit] = band[lit]
            # Sparks where the ball just landed
            for cx, by, at, _, _ in self.syl:
                g = u - at
                if 0 <= g < 0.3:
                    px = cx + 1 + self.spark_v[:, 0] * g
                    py = by + 3 + self.spark_v[:, 1] * g + 30 * g * g
                    gfx.splat(frame, px, py, np.array([(255, 210, 90)] * len(px)) * (1 - g / 0.3))
        # The ball, a baseball: in from the left, a hop to each syllable, then off right.
        pts = [(-4.0, self.syl[0][1], -0.3)] + [(cx, by, at) for cx, by, at, _, _ in self.syl] + \
              [(W + 4.0, self.syl[-1][1], self.syl[-1][2] + 0.35)]
        for trail, fade in ((0.06, 0.25), (0.03, 0.5), (0.0, 1.0)):
            t = u - trail
            for (x0, y0, t0), (x1, y1, t1) in zip(pts, pts[1:]):
                if t0 <= t < t1:
                    w = (t - t0) / (t1 - t0)
                    bx = gfx.lerp(x0, x1, w)
                    by = gfx.lerp(y0, y1, w) - 6 * math.sin(math.pi * w)
                    xs = np.array([bx + 1, bx, bx + 1, bx + 2, bx + 1])
                    ys = np.array([by, by + 1, by + 1, by + 1, by + 2])
                    cols = np.array([WHITE, WHITE, (230, 50, 40), WHITE, WHITE], np.float32) * fade
                    gfx.splat(frame, xs, ys, cols)
                    break

    def _gold_line(self):
        band = np.zeros((18, W, 3), np.float32)
        m, x, _ = self.lines[-1]
        gfx.blit(band, m, x, LYRIC_Y - 13, GOLD)
        return band

    def _cubs(self, frame, s):
        BOARD = 5.0          # camera arrives on the whole scoreboard
        MORPH = 5.4          # line scores start flying into the first lyric line
        if s < BOARD:
            scene = self.wrigley.copy()
            # Message board under the sign: WELCOME JACKSON / GO CUBS GO
            msg = self.welcome if int(s / 0.9) % 2 == 0 else self.gocubs
            board = np.zeros((8, 60, 3), np.float32)
            gfx.blit(board, msg, (60 - msg.shape[1]) // 2, 1, (255, 160, 20))
            scene[53:61, 66:126] = np.maximum(scene[53:61, 66:126], board)
            # Street view -> zoom to the sign (crisp at 1:1) -> hold -> tilt up to the scoreboard
            if s < 1.8:
                u = gfx.smoothstep((s - 0.6) / 1.2)
                vw, cy = gfx.lerp(float(VW), 64.0, u), gfx.lerp(48.0, 52.0, u)
            elif s < 3.4:
                vw, cy = 64.0, 52.0
            else:
                vw, cy = 64.0, gfx.lerp(52.0, 18.0, gfx.smoothstep((s - 3.4) / 1.6))
            cx, vh = 96.0, vw / 2
            box = (cx - vw / 2, cy - vh / 2, cx + vw / 2, cy + vh / 2)
            img = Image.fromarray(scene.astype(np.uint8)).resize((W, H), Image.BOX, box=box)
            frame[:] = np.asarray(img, np.float32)
        else:
            # The divider and inning numbers fade off the board face for the sing-along.
            w = gfx.smoothstep((s - (SING_AT - 1.0)) / 0.8)
            frame[:] = self.board_view * (1 - w) + self.sing_back * w
            m = s - MORPH
            if m < 0:                                     # the board as it is: line scores
                dy, dx, col = self.m_dots
                frame[dy, dx] = col
            else:
                w = gfx.smoothstep((m - self.m_delay) / 0.75)
                px = gfx.lerp(self.m_sx, self.m_tx, w)
                py = gfx.lerp(self.m_sy, self.m_ty, w) - 4 * np.sin(np.pi * w)   # a little hop
                col = self.m_scol * (1 - w)[:, None] + self.m_tcol * w[:, None]
                done = w >= 1
                frame[self.m_ty[done].astype(int), self.m_tx[done].astype(int)] = self.m_tcol[done]
                gfx.splat(frame, px[~done], py[~done], col[~done])
            if s >= SING_AT - 0.3:
                self._sing(frame, s - SING_AT)
        # More punch (owner: the night scene felt flat): deeper darks, brighter highlights
        np.clip(255 * (np.clip(frame, 0, 255) / 255) ** CUBS_GAMMA * CUBS_GAIN, 0, 255, out=frame)
        if s > self.t_hawks - 0.25:                  # quick fade to black
            frame *= 1 - gfx.smoothstep((s - (self.t_hawks - 0.25)) / 0.25)

    # ------------------------------------------------------------- Blackhawks
    def _hawks(self, frame, s):
        # Spinning goal light: a red beam sweeping round from the light at the top.
        ang = s * 2 * math.pi * 1.4
        d = np.angle(np.exp(1j * (self.beam_angle - ang)))
        beam = np.exp(-(d / 0.32) ** 2) * np.clip(1.2 - self.beam_dist / 60, 0.2, 1)
        on = min(1.0, s / 0.3)
        frame += beam[..., None] * np.array((150, 8, 8)) * on
        # Red flash on every "DO"
        hit = [s - b for b in self.beats if s >= b]
        if hit:
            frame += np.array((110, 0, 0)) * math.exp(-min(hit) / 0.18)
        # The goal light itself
        frame[0:3, 28:36] = (255, 40, 30) if int(s * 8) % 2 == 0 else (200, 10, 10)
        frame[3, 26:38] = (60, 60, 60)
        logo_at = HAWKS_LOGO_AT
        if s < logo_at + 0.2:
            fade = 1 - gfx.smoothstep((s - logo_at) / 0.2)
            run = s >= self.beats[6]                     # the run of seven replaces the two threes
            for i, b in enumerate(self.beats):
                if s < b or (run and i < 6):
                    continue
                if i < 6:                                # DO DO DO on top, DO DO DO below
                    m, n, k, gap = self.da, 3, i % 3, 6
                    y0 = 5 if i < 3 else 18
                else:                                    # the run: four on top, three below, smaller
                    j = i - 6
                    m, gap = self.da_small, 3
                    n, k = (4, j) if j < 4 else (3, j - 4)
                    y0 = 6 if j < 4 else 19
                x0 = (W - (n * m.shape[1] + (n - 1) * gap)) // 2 + k * (m.shape[1] + gap)
                pop = 1 + 0.35 * math.exp(-(s - b) / 0.08)
                mm = gfx.scale_mask(m, pop)
                x = x0 + (m.shape[1] - mm.shape[1]) // 2
                y = y0 + (m.shape[0] - mm.shape[0]) // 2
                gfx.clear_behind(frame, mm, x, y)
                gfx.blit(frame, mm, x, y, np.array(WHITE) * fade)
        else:
            u = gfx.ease_out_back((s - logo_at - 0.2) / 0.4)
            if self.hawks_logo is not None:
                lg = Image.fromarray(self.hawks_logo.astype(np.uint8))
                size = max(2, int(16 * max(0.1, u)))
                lg = np.asarray(lg.resize((size, size), Image.NEAREST), np.float32)
                x, y = (W - size) // 2, 4 + (16 - size) // 2
                mask = lg.max(-1) > 10
                gfx.clear_behind(frame, mask, x, y)
                gfx.blit(frame, mask, x, y, lg)
            m = _t("BLACKHAWKS", "5x7")
            gfx.clear_behind(frame, m, (W - m.shape[1]) // 2, 23)
            _put(frame, "BLACKHAWKS", "5x7", W // 2, 23, (255, 70, 70))
        if s > T_BEARS - T_HAWKS - 0.2:
            frame *= 1 - gfx.smoothstep((s - (T_BEARS - T_HAWKS - 0.2)) / 0.2)

    # ------------------------------------------------------------- Bears
    def _bears(self, s):
        front = gfx.new_frame()
        front[:] = BEARS_NAVY
        front[::4, :] += 4                                   # faint texture
        torn = np.zeros((H, W), bool)
        edge = np.zeros((H, W), np.float32)
        hot = []
        for c, (path, start) in enumerate(zip(self.claws, self.claw_start)):
            u = (s - start) / 0.13
            if u <= 0:
                continue
            n = int(len(path) * min(1.0, u))
            if n:
                torn |= self.claw_torn[c][n - 1]          # precomputed: no per-frame geometry
                edge = np.maximum(edge, self.claw_edge[c][n - 1])
            if u < 1.6 and n:
                hot.append(path[n - 1])
        frame = np.where(torn[..., None], self.bears_back, front)
        frame[~torn] += (edge[~torn][:, None] * np.array((90, 110, 170)))
        for x, y, _ in hot:                                   # white-hot claw tips
            g = np.exp(-((_XX - x) ** 2 + (_YY - y) ** 2) / 6)
            frame += g[..., None] * 255
        # Shake on each strike
        shake = sum(math.exp(-(s - st) / 0.06) for st in self.claw_start if s >= st)
        if shake > 0.05:
            frame = np.roll(frame, int(round(math.sin(s * 90) * 1.4 * shake)), axis=1)
        # The torn board falls away
        FALL = 1.1
        if s > FALL:
            tau = np.maximum(0, s - FALL - self.fall_delay)
            dy = 0.5 * 120 * tau * tau
            out = self.bears_back.copy()
            ys, xs = np.nonzero(~torn & (dy < H + 2))
            ny = (ys + dy[ys, xs]).astype(int)
            nx = np.clip((xs + self.fall_v[ys, xs] * tau[ys, xs]).astype(int), 0, W - 1)
            ok = ny < H
            out[ny[ok], nx[ok]] = np.maximum(out[ny[ok], nx[ok]], front[ys[ok], xs[ok]] * 0.9)
            frame = out
        if s > 1.8:                                           # one shine once BEAR DOWN is revealed
            u = (s - 1.85) / 0.5
            if 0 <= u <= 1:
                lit = self.bears_back.max(-1) > 100
                _shine(frame, lit, gfx.lerp(-12, W + 22, u))
        if EAT <= s < CHANT[0][4]:
            frame = gfx.new_frame()
            shake = 0.0
            # BEAR DOWN is smashed: a jolt, then the letters break apart and fall away.
            tau = s - EAT
            fade = max(0.0, 1 - tau / 0.9)
            if fade > 0:
                gfx.splat(frame, self.sh_x + self.sh_vx * tau,
                          self.sh_y + self.sh_vy * tau + 0.5 * 90 * tau * tau, self.sh_col * fade)
            shake += 3.0 * math.exp(-tau / 0.08)
            frame += np.array((255, 255, 255)) * 0.7 * math.exp(-tau / 0.05)
            # Da Coach drops in: aviators and the mustache, landing with a thud.
            if s >= DITKA_IN:
                if s < DITKA_LAND:
                    off = -H * (1 - gfx.ease_in_quad((s - DITKA_IN) / (DITKA_LAND - DITKA_IN)))
                elif s < DITKA_OUT:
                    off = 0.0
                else:
                    off = -H * gfx.ease_in_quad((s - DITKA_OUT) / (CHANT[0][4] - DITKA_OUT))
                gfx.blit_rgb(frame, DITKA, 0, off)
                if s >= DITKA_LAND:
                    land = s - DITKA_LAND
                    shake += 3.5 * math.exp(-land / 0.09)
                    frame += np.array((255, 255, 255)) * 0.5 * math.exp(-land / 0.06)
                    r = 4 + 70 * land                                   # orange shockwave
                    if land < 0.4:
                        ring = np.abs(np.hypot(_XX - W / 2, (_YY - 16) * 1.8) - r) < 1.6
                        frame[ring] += np.array(BEARS_ORANGE) * (1 - land / 0.4)
                    u = (s - (DITKA_LAND + 0.3)) / 0.4                   # glint across the shades
                    if 0 <= u <= 1 and off == 0:
                        _shine(frame, self.ditka_lens, gfx.lerp(-12, W + 22, u), width=1.6, amount=0.9)
            frame = _shake(frame, shake, s)
        elif s >= CHANT[0][4]:                                  # GOOD! BETTER! BEST! slam in
            frame = gfx.new_frame()
            shake = 0.0
            for i, (m, x, y, color, when) in enumerate(self.chant):
                if s < when:
                    continue
                age = s - when
                if age < SLAM:                                  # stamped down: oversized, slamming to size
                    sc = 1 + 0.8 * (1 - gfx.ease_in_quad(age / SLAM))
                    mm = gfx.scale_mask(m, sc)
                    band = gfx.new_frame()
                    gfx.blit(band, mm, x + (m.shape[1] - mm.shape[1]) / 2, y + (m.shape[0] - mm.shape[0]) / 2, color)
                    rows = slice(max(0, y - 2), min(H, y + m.shape[0] + 2))   # stays in its own line
                    frame[rows] = np.maximum(frame[rows], band[rows])
                    continue
                hit = age - SLAM
                power = 1.0 + 0.6 * i                           # each word lands harder
                hot = math.exp(-hit / 0.1)
                gfx.blit(frame, m, x, y, color * (1 - hot) + 255 * hot)
                shake += 1.6 * power * math.exp(-hit / 0.08)
                frame += np.array(color) * 0.25 * power * math.exp(-hit / 0.07)
                if hit < 0.5:                                   # dust kicked out sideways
                    k = np.arange(10)
                    side = np.where(k % 2, 1, -1)
                    px = x + m.shape[1] / 2 + side * (m.shape[1] / 2 + 2 + hit * (20 + k * 3))
                    py = y + m.shape[0] - 1 - hit * (4 + k % 3) + 18 * hit * hit
                    gfx.splat(frame, px, py, np.outer(np.full(10, 1 - hit / 0.5), (190, 170, 150)))
            u = (s - (CHANT[-1][4] + SLAM + 0.3)) / 0.45
            if 0 <= u <= 1:
                _shine(frame, self.chant_frame.max(-1) > 0, gfx.lerp(-12, W + 22, u))
            frame = _shake(frame, shake, s)
        return frame

    # ------------------------------------------------------------- logo
    def _logo(self, frame, s):
        if s < SPARKS:
            k = 3.0
            d = (1 - math.exp(-k * s)) / k
            ox, oy = self.sx + self.pvx * d, self.sy + self.pvy * d
            w = gfx.smoothstep((s - self.pdelay) / 0.5)
            px, py = gfx.lerp(ox, self.tx, w), gfx.lerp(oy, self.ty, w)
            tw = 0.7 + 0.3 * np.sin(self.tx * 7 + self.ty * 3 + s * 40)
            col = self.pcol * (tw * (1 - w))[:, None] + self.tcol * w[:, None]
            done = w >= 1
            frame[self.ty[done].astype(int), self.tx[done].astype(int)] = self.tcol[done]
            gfx.splat(frame, px[~done], py[~done], col[~done])
            return
        s -= SPARKS
        self._spotlights(frame, s + 2.0, fade=min(1.0, s / 0.6))   # stadium lights sweep behind the logo
        frame[self.logo_lit] = self.logo[self.logo_lit]
        f = math.exp(-s / 0.15)
        if f > 0.01:
            frame[self.logo_lit] = frame[self.logo_lit] * (1 - f * 0.9) + 255 * f * 0.9
        for start in (0.5, 2.2):
            u = (s - start) / 0.8
            if 0 <= u <= 1:
                _shine(frame, self.ballin_lit, gfx.lerp(-12, W + 22, u))
        n = len(self.perim)
        shown = int(np.clip((s - 0.1) * 450, 0, n))
        if shown:
            idx = np.arange(shown)
            lit = (idx + int(s * 12)) % 3 == 0
            cols = np.where(lit[:, None], np.array(GOLD, np.float32), np.array((40, 18, 0), np.float32))
            pts = self.perim[:shown]
            frame[pts[:, 1], pts[:, 0]] = cols

    def _crumble(self, frame, s):
        tau = np.maximum(0, s - self.crumble_delay)
        gfx.splat(frame, self.tx + tau * np.sin(self.tx) * 6, self.ty + 0.5 * 110 * tau * tau, self.tcol)

    # ------------------------------------------------------------- birthday
    def _birthday_setup(self, rng):
        self.happy = fonts.text_mask("HAPPY", "5x7")
        self.forty = _t("40", "9x18B")
        self.th = _t("TH", "5x7")
        self.bday = fonts.text_mask("BIRTHDAY", "5x7")
        self.forty_col = _metal(*self.forty.shape)
        self.th_col = _metal(*self.th.shape)
        self.jack = _t("JACKSON", "7x13B")
        self.jack_col = _metal(*self.jack.shape)
        self.cheers = fonts.text_mask("CHEERS TO 40!", "4x6")
        palette = [GOLD, WHITE, HAWKS_RED, CUBS_BLUE, (255, 220, 120)]
        shows = [(0.2, 12, 8), (0.6, 52, 6), (1.6, 30, 5), (2.6, 8, 9), (3.2, 56, 9), (4.0, 20, 6),
                 (5.0, 44, 7), (6.6, 10, 6), (7.3, 54, 7), (8.0, 32, 5)]
        self.fireworks = [Firework(rng, t0, x, y, palette[i % len(palette)], n=28)
                          for i, (t0, x, y) in enumerate(shows)]

    def _spotlights(self, frame, s, fade=1.0):
        for (ox, phase), (a, falloff) in zip(((0.0, 0.0), (63.0, 1.7)), SPOT_GRIDS):
            ang = -math.pi / 2 + 0.55 * math.sin(s * 0.9 + phase) + (0.35 if ox == 0 else -0.35)
            d = (a - ang + math.pi) % (2 * math.pi) - math.pi
            beam = np.exp(-(d / 0.12) ** 2) * falloff
            frame += beam[..., None] * np.array((40, 38, 28)) * fade

    def _birthday(self, frame, s):
        self._spotlights(frame, s)
        for fw in self.fireworks:
            fw.draw(frame, s, 0.75)
        if s < 5.0:
            lift = -34 * gfx.ease_in_quad((s - 4.6) / 0.4)
            hm, hedges = self.happy
            vis, col = _typed(hm, hedges, WHITE, 0.6, 0.08, s)
            hx = (W - hm.shape[1]) // 2
            gfx.clear_behind(frame, vis, hx, 1 + lift)
            gfx.blit(frame, vis, hx, 1 + lift, col)
            if s >= 1.1:
                sc = max(0.05, gfx.ease_out_back((s - 1.1) / 0.45))
                m = gfx.scale_mask(self.forty, sc)
                col = _metal(*m.shape)
                total = self.forty.shape[1] + 1 + self.th.shape[1]
                fx = (W - total) // 2 + (self.forty.shape[1] - m.shape[1]) // 2
                fy = 9 + (self.forty.shape[0] - m.shape[0]) // 2 + lift
                gfx.clear_behind(frame, m, fx, fy)
                gfx.blit(frame, m, fx, fy, col)
                if sc >= 0.95:
                    tx = (W - total) // 2 + self.forty.shape[1] + 1
                    gfx.clear_behind(frame, self.th, tx, 9 + lift)
                    gfx.blit(frame, self.th, tx, 9 + lift, self.th_col)
                for start in (2.2, 3.6):
                    u = (s - start) / 0.7
                    if 0 <= u <= 1:
                        lit = np.zeros((H, W), bool)
                        y0 = int(9 + lift)
                        if 0 <= y0 < H:
                            lit[max(0, y0):y0 + self.forty.shape[0], :] = frame[max(0, y0):y0 + self.forty.shape[0]].max(-1) > 150
                        _shine(frame, lit, gfx.lerp(-12, W + 22, u))
            bm, bedges = self.bday
            vis, col = _typed(bm, bedges, GOLD, 1.7, 0.07, s)
            bx = (W - bm.shape[1]) // 2
            gfx.clear_behind(frame, vis, bx, 25 + lift)
            gfx.blit(frame, vis, bx, 25 + lift, col)
        else:
            c = s - 5.0
            off = 30 * (1 - gfx.ease_out_back(c / 0.5)) if c < 0.5 else 0
            jx = (W - self.jack.shape[1]) // 2
            gfx.clear_behind(frame, self.jack, jx, 1 - off)
            gfx.blit(frame, self.jack, jx, 1 - off, self.jack_col)
            for start in (1.0, 2.8):
                u = (c - start) / 0.7
                if 0 <= u <= 1:
                    lit = np.zeros((H, W), bool)
                    lit[1:1 + self.jack.shape[0], jx:jx + self.jack.shape[1]] = self.jack
                    _shine(frame, lit, gfx.lerp(-12, W + 22, u))
            # Two beer mugs slide in and clink
            CLINK = 0.95
            u = gfx.ease_in_quad(min(1.0, c / CLINK))
            bump = 1 if c > CLINK and c < CLINK + 0.25 else 0
            lx = gfx.lerp(-12, 20, u) + bump
            rx = gfx.lerp(W + 1, 33, u) - bump
            my = 13 - (1 if bump else 0)
            left, right = MUG[:, ::-1], MUG
            gfx.clear_behind(frame, left.max(-1) > 0, lx, my)
            gfx.blit_rgb(frame, left, lx, my)
            gfx.clear_behind(frame, right.max(-1) > 0, rx, my)
            gfx.blit_rgb(frame, right, rx, my)
            if c > CLINK:                                   # foam splash
                tau = c - CLINK
                if tau < 0.9:
                    k = np.arange(14)
                    ang = -math.pi / 2 + (k / 13 - 0.5) * 2.2
                    sp = 9 + (k % 4) * 3
                    px = 31.5 + np.cos(ang) * sp * tau
                    py = 13 + np.sin(ang) * sp * tau + 14 * tau * tau
                    gfx.splat(frame, px, py, np.outer(np.full(14, max(0.0, 1 - tau / 0.9)), (250, 250, 240)))
            cm, cedges = self.cheers
            vis, col = _typed(cm, cedges, GOLD, 1.3, 0.06, c)
            cx0 = (W - cm.shape[1]) // 2
            gfx.clear_behind(frame, vis, cx0, 26)
            gfx.blit(frame, vis, cx0, 26, col)
        if s > BDAY_LEN - 0.6:
            frame *= 1 - gfx.smoothstep((s - (BDAY_LEN - 0.6)) / 0.6)


# ------------------------------------------------------------------- team intros
INTRO_LEN = 15.0
INTROS = {("mlb", "16"): "cubs", ("nhl", "4"): "hawks", ("nfl", "3"): "bears"}   # (league, ESPN team id)


class TeamIntro:
    """One team's part of the startup show, stretched to ~15 s: played when that team's
    game starts (and from the settings page)."""

    def __init__(self, team, show=None):
        self.team = team
        self.show = show or Show(birthday=False)
        self.duration = INTRO_LEN
        self.prev = gfx.new_frame()

    def render(self, t):
        sh = self.show
        frame = gfx.new_frame()
        if self.team == "cubs":
            # The whole Wrigley scene, slowed down to fill the time.
            sh._cubs(frame, t * (sh.t_hawks / INTRO_LEN))
        elif self.team == "hawks":
            # The goal-song chant, again and again, then the logo.
            CHANT, LOOPS = HAWKS_LOGO_AT, 3
            hold = HAWKS_LOGO_AT - 0.1
            if t < CHANT * LOOPS:
                sh._hawks(frame, t % CHANT if t % CHANT < hold else hold)
            else:
                rest = (t - CHANT * LOOPS) / (INTRO_LEN - CHANT * LOOPS)
                sh._hawks(frame, HAWKS_LOGO_AT + rest * (T_BEARS - T_HAWKS - HAWKS_LOGO_AT))
        else:
            # Claws, BEAR DOWN held with a shine every couple of seconds, the bear head
            # eats it, then GOOD / BETTER / BEST held with shines.
            EAT_AT, HOLD_FROM = 8.0, 2.2
            chant_done = EAT_AT + (BEARS_LEN - EAT) - 0.1
            if t < HOLD_FROM:
                frame = sh._bears(t)
            elif t < EAT_AT:
                frame = sh.bears_back.copy()
                u = ((t - HOLD_FROM) % 2.4) / 0.6
                if u <= 1:
                    _shine(frame, sh.bears_back.max(-1) > 100, gfx.lerp(-12, W + 22, u))
            elif t < chant_done:
                frame = sh._bears(EAT + (t - EAT_AT))
            else:
                frame = sh.chant_frame.copy()
                u = ((t - chant_done) % 1.6) / 0.5
                if u <= 1:
                    _shine(frame, sh.chant_frame.max(-1) > 0, gfx.lerp(-12, W + 22, u))
        if t > INTRO_LEN - 0.4:
            frame = frame * (1 - gfx.smoothstep((t - (INTRO_LEN - 0.4)) / 0.4))
        return gfx.to_uint8(frame)


def _shake(frame, amount, s):
    """Jolt the picture by up to `amount` pixels (impacts)."""
    if amount < 0.3:
        return frame
    dx = int(round(math.sin(s * 97.0) * amount))
    dy = int(round(math.cos(s * 71.0) * amount * 0.6))
    return np.roll(np.roll(frame, dx, axis=1), dy, axis=0)


# ------------------------------------------------------------------- goal celebration
GOAL_LEN = 3.6          # the goal light and the DO-DO-DO chant, before the goal details
_goal_frames = None


def hawks_goal_frames(fps=40):
    """The Blackhawks goal light and DA DA DA chant (the start of their part of the show),
    rendered once and kept: played before a Blackhawks goal announcement."""
    global _goal_frames
    if _goal_frames is None:
        from types import SimpleNamespace
        parts = SimpleNamespace(da=_t("DO", "7x13B"), da_small=_t("DO", "6x10"), beats=list(HAWKS_BEATS),
                                hawks_logo=None, beam_angle=np.arctan2(_YY - 1, _XX - 31.5),
                                beam_dist=np.hypot(_YY - 1, _XX - 31.5))
        out, prev = [], gfx.new_frame()
        for i in range(int(GOAL_LEN * fps)):
            frame = gfx.new_frame()
            Show._hawks(parts, frame, i / fps)
            frame = np.maximum(frame, prev * 0.35)        # same light trail as the show
            prev = frame
            out.append(np.clip(frame, 0, 255))
        _goal_frames = out
    return _goal_frames


# ------------------------------------------------------------------- after a Cubs win
# The "Go Cubs Go" chorus, sung through twice the way the crowd does at Wrigley after a
# win, on the scoreboard: GO / CUBS / GO slammed onto the board one word at a time, then
# the lines flip in panel by panel with each word lighting up gold as it's sung. Ends with
# the W flag waving. Times are seconds into one pass of the chorus.
SONG_SLAMS = (("GO", 0.0), ("CUBS", 0.5), ("GO", 1.0), ("GO", 2.0), ("CUBS", 2.5), ("GO", 3.0))
SONG_LINES = (("HEY CHICAGO", 3.6, (("HEY", 3.9), ("CHICAGO", 4.25))),
              ("WHAT DO", 4.75, (("WHAT", 5.0), ("DO", 5.3))),
              ("YOU SAY", 5.55, (("YOU", 5.8), ("SAY", 6.1))),
              ("THE CUBS", 6.45, (("THE", 6.7), ("CUBS", 7.0))),
              ("ARE GONNA", 7.25, (("ARE", 7.5), ("GONNA", 7.8))),
              ("WIN TODAY", 8.15, (("WIN", 8.4), ("TODAY", 8.8))))
SONG_PASS = 9.8            # one pass of the chorus
SONG_PASSES = 2
SONG_FLAG = 2.6            # the W flag waves this long at the end
SLAM_COLORS = {"GO": (60, 115, 255), "CUBS": (240, 45, 45)}


def _scoreboard_face():
    """Wrigley's scoreboard at 1:1 with a cleared face, and a mask of its pennants."""
    board = _wrigley(scores=False)[2:34, 64:128].copy()
    back = board.copy()
    back[BODY] = BOARD_GREEN
    top = board[:12]
    pennants = np.zeros((H, W), bool)
    pennants[:12] = ((top.max(-1) - top.min(-1)) > 60) | (top.min(-1) > 200)
    pennants[:, 25:40] = False
    return back, pennants


def _w_flag():
    flag = np.zeros((18, W, 3), np.float32)
    flag[1:17, 16:48] = (250, 250, 250)
    w = gfx.scale_mask(_t("W", "7x13B"), 1.15)
    w = np.pad(w, ((0, 0), (0, 1))) | np.pad(w, ((0, 0), (1, 0)))      # extra bold
    gfx.blit(flag, w, 32 - w.shape[1] // 2, 9 - w.shape[0] // 2, (25, 60, 255))
    return flag


def _wave(flag, v):
    out = np.zeros_like(flag)
    for x in range(16, 48):
        ph = x * 0.35 - v * 9
        dy = int(round(1.2 * math.sin(ph)))
        out[max(0, dy):18 + min(0, dy), x] = flag[max(0, -dy):18 - max(0, dy), x] * (0.9 + 0.1 * math.cos(ph))
    return out


def _line_syllables(text_, words):
    """Each sung word of a line as its syllables: [(word, [(start, end) in the line], ...]."""
    out, cursor = [], 0
    for word, _ in words:
        k = text_.index(word, cursor)
        cursor = k + len(word)
        out.append((word, [(k + a, k + b) for a, b in syllables(word)]))
    return out


def gocubsgo_groups():
    """What the tap page steps through, both passes of the chorus: each GO / CUBS chant word,
    then each sung line, tapped by syllable (the words the owner gave for the song; 2026-10-03)."""
    out = []
    for _ in range(SONG_PASSES):
        out += [w for w, _ in SONG_SLAMS]
        out += [text_ for text_, _, _ in SONG_LINES]
    return out


def gocubsgo_units():
    """Every tap, as (group index, start, end) inside gocubsgo_groups(): a chant word whole,
    a sung line syllable by syllable."""
    lines = {text_: words for text_, _, words in SONG_LINES}
    units = []
    for gi, g in enumerate(gocubsgo_groups()):
        if g in lines:
            units += [(gi, a, b) for _, syl in _line_syllables(g, lines[g]) for a, b in syl]
        else:
            units.append((gi, 0, len(g)))
    return units


class GoCubsGo:
    """The Go Cubs Go chorus on the scoreboard (see SONG_*), twice, then the W flag. Follows
    the owner's tapped timing (`gocubsgo_timing`) when saved. Renders float frames live."""

    def __init__(self, timing=None):
        self.back, self.pennants = _scoreboard_face()
        self.flag = _w_flag()
        self.slams = {w: _t(w, "7x13B") for w in ("GO", "CUBS")}
        self.lines = []                                  # (mask, x, [(col0, col1)] per syllable)
        self.syl_count = []                              # syllables per word, per line
        for text_, _, words in SONG_LINES:
            m, edges = fonts.text_mask(text_, "5x7")
            spans, counts = [], []
            for _, syl in _line_syllables(text_, words):
                counts.append(len(syl))
                for a, b in syl:
                    spans.append((edges[a - 1] if a else 0, min(m.shape[1], edges[b - 1])))
            self.lines.append((m, (W - m.shape[1]) // 2, spans))
            self.syl_count.append(counts)
        if timing is None:
            from .song_timing import GOCUBSGO as timing     # the owner's taps
        per = len(SONG_SLAMS) + sum(len(sp) for _, _, sp in self.lines)
        # Each pass: its start, the chant words\x27 times, each line\x27s flip time and word times
        self.passes = []
        if timing and len(timing) == per * SONG_PASSES:
            for p in range(SONG_PASSES):
                tp = timing[p * per:(p + 1) * per]
                start = tp[0]
                rel = [x - start for x in tp]
                slams = [(w, rel[i]) for i, (w, _) in enumerate(SONG_SLAMS)]
                k, words = len(SONG_SLAMS), []
                for _, _, sp in self.lines:               # syllable times, line by line
                    words.append(rel[k:k + len(sp)])
                    k += len(sp)
                nxt = timing[(p + 1) * per] if p + 1 < SONG_PASSES else tp[-1] + 0.9
                self.passes.append({"start": start - timing[0], "slams": slams, "words": words,
                                    "flips": [max(0.0, w[0] - 0.25) for w in words], "length": nxt - start})
        else:
            for p in range(SONG_PASSES):
                self.passes.append({"start": p * SONG_PASS, "slams": list(SONG_SLAMS),
                                    "words": [[wt + 0.12 * j for (_, wt), n in zip(lw, cnt) for j in range(n)]
                                              for (_, _, lw), cnt in zip(SONG_LINES, self.syl_count)],
                                    "flips": [at for _, at, _ in SONG_LINES], "length": SONG_PASS})
        for P in self.passes:
            P["beats"] = sorted([a for _, a in P["slams"]] + [x for w in P["words"] for x in w])
        last = self.passes[-1]
        self.song_end = last["start"] + last["length"]
        self.key, self.duration = "song:gocubsgo", self.song_end + SONG_FLAG

    def _slam_band(self, u, P):
        """GO / CUBS / GO: each word stamped onto the board, oversized then snapping to size."""
        band = np.zeros((18, W, 3), np.float32)
        word, at = max(((w, a) for w, a in P["slams"] if u >= a), key=lambda q: q[1], default=(None, 0))
        if word is None:
            return band
        age = u - at
        m = self.slams[word]
        mm = gfx.scale_mask(m, 1 + 1.2 * math.exp(-age / 0.06))
        col = np.array(SLAM_COLORS[word], np.float32) * (1 + 0.6 * math.exp(-age / 0.1))
        gfx.blit(band, mm, (W - mm.shape[1]) // 2, 9 - mm.shape[0] // 2, np.minimum(col, 255))
        return band

    def _line_band(self, i, u, P):
        band = np.zeros((18, W, 3), np.float32)
        m, x, spans = self.lines[i]
        times = P["words"][i]
        y = 6
        gfx.blit(band, m, x, y, UNSUNG)
        for j, ((a, b), wt) in enumerate(zip(spans, times)):
            if u < wt:
                continue
            band[y:y + m.shape[0], x + a:x + b] = 0
            nxt = times[j + 1] if j + 1 < len(times) else wt + 0.4
            if u < nxt:                                   # the word being sung: gold, raised, glowing
                age = u - wt
                lift = 1 if age < 0.15 else 0
                glow = np.zeros((18, W), bool)
                for dy in (-1, 0, 1):
                    for dx in (-1, 0, 1):
                        glow[y - lift + dy:y - lift + dy + m.shape[0], x + a + dx:x + b + dx] |= m[:, a:b]
                band[glow] = np.maximum(band[glow], np.array((120, 70, 0)) * (0.6 + 0.4 * math.exp(-age / 0.2)))
                gfx.blit(band, m[:, a:b], x + a, y - lift, (255, 215, 60))
            else:
                gfx.blit(band, m[:, a:b], x + a, y, SUNG)
        return band

    def _band(self, u, P):
        """The board face at u seconds into a pass of the chorus."""
        flips = [(-1, 0.0)] + list(enumerate(P["flips"]))
        cur, at = max((f for f in flips if u >= f[1]), key=lambda f: f[1])
        draw = (lambda i: self._slam_band(u, P) if i < 0 else self._line_band(i, u, P))
        if cur >= 0 and u < at + FLIP + 13 * STAGGER:
            return Show._flip(draw(cur - 1), draw(cur), u - at)
        return draw(cur)

    def render(self, t):
        frame = self.back.copy()
        if t < self.song_end:
            P = max((q for q in self.passes if t >= q["start"]), key=lambda q: q["start"])
            u = t - P["start"]
            beat = min((u - b for b in P["beats"] if u >= b), default=9.0)
            frame[self.pennants] *= 1 + 0.9 * math.exp(-beat / 0.15)
            band = self._band(u, P)
            # A white flash across the board as each GO / CUBS lands
            slam = min((u - a for _, a in P["slams"] if u >= a), default=9.0)
            if u < P["flips"][0]:
                frame[BODY] += 70 * math.exp(-slam / 0.08)
        else:
            v = t - self.song_end
            P = self.passes[-1]
            gold = self._line_band(len(self.lines) - 1, 1e9, P)
            band = Show._flip(gold, self.flag, v) if v < FLIP + 13 * STAGGER else _wave(self.flag, v)
        face = frame[BODY[0]]
        lit = band.max(-1) > 0
        face[lit] = band[lit]
        if t > self.duration - 0.4:
            frame *= 1 - gfx.smoothstep((t - (self.duration - 0.4)) / 0.4)
        return np.clip(frame, 0, 255)


# ------------------------------------------------------------------- after a Bears win
# "Bear Down, Chicago Bears" at Soldier Field at night: the colonnade along the bottom and
# light towers flashing on the beat. The chant is stamped across the sky one big word at a
# time (claw slashes on BEAR, sparks on BEARS), then the whole song is sung in the sky, a
# page of lines at a time flipping in, each word lighting up orange as it's sung. Then the
# wishbone C swings in. Words are from the owner (2026-10-01).
BD_OPEN = 10.0             # BEARS / WIN! stamped in and flashing first (owner, 2026-10-01)
BD_OPENING = (("BEARS", 2, BEARS_ORANGE, 0.15), ("WIN!", 17, WHITE, 0.65))   # word, y, color, when
BD_WORDS = (("BEAR", 0.0), ("DOWN", 0.55), ("CHICAGO", 1.3), ("BEARS", 1.9))
BD_INTRO = 2.8             # the chant slams, then the song
BD_LYRICS = (
    "Bear down, Chicago Bears, make every play clear the way to victory;",
    "Bear down, Chicago Bears, put up a fight with a might so fearlessly.",
    "We'll never forget the way you thrilled the nation with your T-formation.",
    "Bear down, Chicago Bears, and let them know why you're wearing the crown.",
    "You're the pride and joy of Illinois, Chicago Bears, bear down!",
)
BD_WORD_T = 0.3            # seconds per word
BD_PAUSE = 0.3             # extra after a comma or the end of a line
BD_LINE_Y = (1, 8, 15)     # up to three 4x6 lines in the sky
BD_FINALE = 4.0
BD_COLORS = {"BEAR": BEARS_ORANGE, "DOWN": WHITE, "CHICAGO": WHITE, "BEARS": BEARS_ORANGE}
BD_UNSUNG = (80, 95, 140)
COLONNADE_TOP = 21


def _soldier_field():
    """Night sky over Soldier Field's colonnade, with a light tower at each side."""
    img = gfx.new_frame()
    for y in range(COLONNADE_TOP):
        img[y, :] = np.array(BEARS_NAVY) * (0.55 + 0.45 * y / COLONNADE_TOP)
    img[COLONNADE_TOP:, :] = (25, 14, 6)                       # glow from inside the stadium
    img[COLONNADE_TOP:COLONNADE_TOP + 2, :] = (150, 148, 138)  # the entablature
    img[COLONNADE_TOP + 1, ::3] = (110, 108, 100)
    for x in range(1, W, 6):                                    # columns with capitals
        img[COLONNADE_TOP + 2, x - 1:x + 3] = (170, 168, 158)
        img[COLONNADE_TOP + 3:H - 1, x:x + 2] = (140, 138, 128)
        img[COLONNADE_TOP + 3:H - 1, x] = (165, 163, 152)
    img[H - 1, :] = (90, 88, 80)
    for tx in (3, 60):                                          # light towers
        img[4:COLONNADE_TOP, tx] = (70, 70, 80)
    return img


def _bears_c(r_out=12.5, r_in=6.8):
    """The wishbone C: orange, outlined in white, open to the right. (mask, outline) at 32x32."""
    yy, xx = np.mgrid[0:32, 0:32].astype(np.float32) + 0.5
    dx, dy = xx - 16, yy - 16
    r = np.hypot(dx, dy)
    ang = np.abs(np.arctan2(dy, dx))
    body = (r <= r_out) & (r >= r_in) & (ang > 0.72)
    grown = np.zeros_like(body)
    for oy in (-1, 0, 1):
        for ox in (-1, 0, 1):
            grown |= np.roll(np.roll(body, oy, 0), ox, 1)
    return body, grown & ~body


def song_words(lyrics):
    """Every sung word in order."""
    return [w for verse in lyrics for w in verse.upper().replace(";", ",").split()]


_ONSETS = {"BL", "BR", "CL", "CR", "DR", "FL", "FR", "GL", "GR", "PL", "PR", "SC", "SK", "SM", "SN",
           "SP", "ST", "SW", "TR", "TH", "CH", "SH", "WH", "PH", "THR", "STR", "SCR", "SPR", "QU"}


def syllables(word):
    """Rough English syllables of an uppercase word, as (start, end) character ranges that
    cover the whole word (punctuation stays with its neighbor). Good enough to tap along to."""
    parts, base = [], 0
    for chunk in word.split("-"):                       # T-FORMATION: T | FORMATION
        parts += [(base + a, base + b) for a, b in _syl(chunk)]
        base += len(chunk) + 1
    if len(parts) > 1:                                  # hyphens ride with the part before them
        parts = [(a, parts[i + 1][0] if i + 1 < len(parts) else len(word)) for i, (a, _) in enumerate(parts)]
    return parts or [(0, len(word))]


_SYL_FIX = {"EVERY": [(0, 2), (2, 5)]}      # sung "ev-ry"


def _syl(w):
    if w in _SYL_FIX:
        return _SYL_FIX[w]
    letters = [(i, c) for i, c in enumerate(w) if c.isalpha()]
    if not letters:
        return [(0, len(w))]
    s = "".join(c for _, c in letters)
    vowel = [c in "AEIOU" or (c == "Y" and i > 0) for i, c in enumerate(s)]
    groups, i = [], 0                                    # vowel groups: (first, last) letter index
    while i < len(s):
        if vowel[i]:
            j = i
            while j + 1 < len(s) and vowel[j + 1]:
                j += 1
            groups.append((i, j))
            i = j + 1
        else:
            i += 1
    if len(groups) > 1:                                  # silent endings: PRIDE, YOU'RE, THRILLED
        a, b = groups[-1]
        if a == b == len(s) - 1 and s[a] == "E" and s[a - 1] != "L":
            groups.pop()
        elif s.endswith("ED") and a == len(s) - 2 and s[a - 1] not in "TD":
            groups.pop()
    if len(groups) <= 1:
        return [(0, len(w))]
    starts = [0]
    for (_, end), (nxt, _) in zip(groups, groups[1:]):
        cons = s[end + 1:nxt]
        if len(cons) <= 1:                               # V-CV: the consonant starts the next one
            cut = end + 1
        else:                                            # keep a natural onset together (FOR-MA, VIC-TO)
            cut = nxt - 1
            for k in (3, 2):
                if len(cons) >= k and cons[-k:] in _ONSETS:
                    cut = nxt - k
                    break
        starts.append(cut)
    pos = [letters[k][0] for k in starts]                # letter index -> character index
    pos[0] = 0
    return [(a, pos[k + 1] if k + 1 < len(pos) else len(w)) for k, a in enumerate(pos)]


def song_units(lyrics):
    """What the tap-along page times: every syllable, as (word index, start, end)."""
    return [(i, a, b) for i, w in enumerate(song_words(lyrics)) for a, b in syllables(w)]


def _song_pages(lyrics, width=W - 14, font="4x6", per_page=3, timing=None):   # clear of the light towers
    """Word-wrap each song line onto the panel and time every word. Returns pages:
    [(start, [(mask, x, y, [(col0, col1, time), ...]), ...])] and the time it all ends.
    timing: seconds for each syllable (song_units) or each word, tapped along on the
    settings site with the first one at 0; otherwise words are spaced evenly. With
    syllable timing every syllable lights up on its own."""
    words_all = song_words(lyrics)
    per_word = None                                     # word index -> [(start, end, time), ...]
    if timing and len(timing) == len(song_units(lyrics)):
        per_word = [[] for _ in words_all]
        for (wi, a, b), tm in zip(song_units(lyrics), timing):
            per_word[wi].append((a, b, tm))
    elif timing and len(timing) == len(words_all):
        per_word = [[(0, len(w), tm)] for w, tm in zip(words_all, timing)]
    pages, t, idx = [], 0.0, 0
    for verse in lyrics:
        words = verse.upper().replace(";", ",").split()
        lines, cur = [], []
        for w in words:
            if cur and _t(" ".join(cur + [w]), font).shape[1] > width:
                lines.append(cur)
                cur = [w]
            else:
                cur.append(w)
        lines.append(cur)
        # Spread a verse's lines evenly over its pages (no last page with one lone word).
        n = len(lines)
        k = -(-n // per_page)
        sizes = [n // k + (1 if j < n % k else 0) for j in range(k)]
        chunks, i = [], 0
        for size in sizes:
            chunks.append(lines[i:i + size])
            i += size
        for chunk in chunks:
            start = per_word[idx][0][2] if per_word else t
            page = []
            for ln, y in zip(chunk, BD_LINE_Y):
                text_ = " ".join(ln)
                m, edges = fonts.text_mask(text_, font)
                spans, k = [], 0
                for w in ln:
                    for s0, s1, tm in (per_word[idx] if per_word else [(0, len(w), t)]):
                        a = edges[k + s0 - 1] if k + s0 else 0
                        b = min(m.shape[1], edges[k + s1 - 1])
                        spans.append((a, b, tm))
                    t += BD_WORD_T + (BD_PAUSE if w[-1] in ",.!" else 0)
                    idx += 1
                    k += len(w) + 1
                page.append((m, (W - m.shape[1]) // 2, y, spans))
            pages.append((start, page))
        t += BD_PAUSE
    return pages, (per_word[-1][-1][2] + BD_WORD_T + BD_PAUSE) if per_word else t


class BearDown:
    """The Bears' win celebration (see BD_*). Renders float frames live."""

    def __init__(self):
        self.back = _soldier_field()
        self.words = {w: _t(w, "7x13B") for w in BD_COLORS}
        self.c_body, self.c_edge = _bears_c()
        yy, xx = np.mgrid[0:32, 0:32]
        self.c_ang = (np.arctan2(yy - 16, xx - 16) + math.pi) % (2 * math.pi)
        self.side = (_t("BEAR", "4x6"), _t("DOWN", "4x6"))
        rng = np.random.default_rng(85)
        self.sparks = rng.uniform(-1, 1, (40, 2)) * (30, 22)
        # Syllable timing the owner tapped along to the real song
        from .song_timing import BEARDOWN
        self.pages, lyric_len = _song_pages(BD_LYRICS, timing=list(BEARDOWN))
        self.song_end = BD_INTRO + lyric_len + 0.6
        self.key, self.duration = "song:beardown", BD_OPEN + self.song_end + BD_FINALE
        self.open_words = []
        for word, y, color, when in BD_OPENING:
            m = _t(word, "7x13B")
            self.open_words.append((m, (W - m.shape[1]) // 2, y, np.array(color, np.float32), when))
        self.open_lit = np.zeros((H, W), bool)
        for m, x, y, _, _ in self.open_words:
            self.open_lit[y:y + m.shape[0], x:x + m.shape[1]] |= m
        self.beats = sorted([a for _, a in BD_WORDS] +
                            [BD_INTRO + wt for _, page in self.pages for *_, sp in page for *_, wt in sp])

    def _word(self, frame, word, age):
        """A chant word stamped down, oversized to size."""
        m = self.words[word]
        col = np.array(BD_COLORS[word], np.float32)
        mm = gfx.scale_mask(m, 1 + 1.3 * math.exp(-age / 0.06))
        x, y = W // 2 - mm.shape[1] // 2, 9 - mm.shape[0] // 2
        gfx.clear_behind(frame, mm, x, y)
        gfx.blit(frame, mm, x, y, np.minimum(col * (1 + 0.5 * math.exp(-age / 0.1)), 255))

    @staticmethod
    def _claws(frame, age):
        """Three claw slashes ripping diagonally across the sky."""
        if age > 0.35:
            return
        grow = gfx.clamp01(age / 0.1)
        fade = 1 - gfx.clamp01((age - 0.1) / 0.25)
        n = min(int(18 * grow), COLONNADE_TOP - 1)
        if n <= 0:
            return
        i = np.arange(n, dtype=np.float32)
        xs = np.concatenate([np.concatenate([14 + k * 13 + i * 0.75, 15 + k * 13 + i * 0.75]) for k in range(3)])
        ys = np.tile(np.concatenate([1 + i, 1 + i]), 3)
        cols = np.tile(np.concatenate([np.tile((255, 230, 190), (n, 1)), np.tile(BEARS_ORANGE, (n, 1))]), (3, 1)) * fade
        gfx.splat(frame, xs, ys, cols)   # all three slashes in one go

    def _page(self, i, u, all_sung=False):
        """Page i of the song as a sky band (rows above the colonnade), by what's been sung."""
        band = np.zeros((COLONNADE_TOP, W, 3), np.float32)
        spans_all = [s for *_, sp in self.pages[i][1] for s in sp]
        last = spans_all[-1][2]
        for m, x, y, spans in self.pages[i][1]:
            gfx.blit(band, m, x, y, BD_UNSUNG)
            for j, (a, b, wt) in enumerate(spans):
                if not all_sung and u < wt:
                    continue
                band[y:y + m.shape[0], x + a:x + b] = 0
                nxt = next((s[2] for s in spans_all if s[2] > wt), last + BD_WORD_T)
                if not all_sung and u < nxt:              # the word being sung: orange, raised, glowing
                    age = u - wt
                    lift = 1 if age < 0.12 else 0
                    pad = np.zeros((COLONNADE_TOP + 4, W + 4), bool)      # word mask, 2 px margin
                    pad[y - lift + 2:y - lift + 2 + m.shape[0], x + a + 2:x + b + 2] = m[:, a:b]
                    grown = pad.copy()
                    for dy in (-1, 0, 1):
                        for dx in (-1, 0, 1):
                            grown |= np.roll(np.roll(pad, dy, 0), dx, 1)
                    glow = grown[2:-2, 2:-2]
                    band[glow] = np.maximum(band[glow], np.array((110, 40, 0)) * (0.6 + 0.4 * math.exp(-age / 0.2)))
                    gfx.blit(band, m[:, a:b], x + a, y - lift, (255, 120, 30))
                else:
                    gfx.blit(band, m[:, a:b], x + a, y, WHITE)
        return band

    def render(self, t):
        frame = self._opening(t) if t < BD_OPEN else self._song(t - BD_OPEN)
        if t > self.duration - 0.4:
            frame *= 1 - gfx.smoothstep((t - (self.duration - 0.4)) / 0.4)
        return np.clip(frame, 0, 255)

    def _opening(self, t):
        """BEARS / WIN! stamped down like GOOD / BETTER / BEST in the startup show, then
        flashing: colors trading places on the beat, shines, the panel pulsing orange."""
        frame = gfx.new_frame()
        frame[:] = np.array(BEARS_NAVY) * 0.6
        shake = 0.0
        settled = t > BD_OPENING[-1][3] + SLAM + 0.4
        swap = settled and int((t - 1.2) / 0.45) % 2 == 1
        for i, (m, x, y, color, when) in enumerate(self.open_words):
            if t < when:
                continue
            age = t - when
            if age < SLAM:                                  # stamped down: oversized, slamming to size
                sc = 1 + 0.8 * (1 - gfx.ease_in_quad(age / SLAM))
                mm = gfx.scale_mask(m, sc)
                gfx.blit(frame, mm, x + (m.shape[1] - mm.shape[1]) / 2, y + (m.shape[0] - mm.shape[0]) / 2, color)
                continue
            hit = age - SLAM
            hot = math.exp(-hit / 0.1)
            col = self.open_words[1 - i][3] if swap else color
            gfx.blit(frame, m, x, y, col * (1 - hot) + 255 * hot)
            shake += 1.6 * (1.3 + 0.6 * i) * math.exp(-hit / 0.08)
            frame += np.array(color) * 0.3 * math.exp(-hit / 0.07)
            if hit < 0.5:                                   # dust kicked out sideways
                k = np.arange(10)
                side = np.where(k % 2, 1, -1)
                px = x + m.shape[1] / 2 + side * (m.shape[1] / 2 + 2 + hit * (20 + k * 3))
                py = y + m.shape[0] - 1 - hit * (4 + k % 3) + 18 * hit * hit
                gfx.splat(frame, px, py, np.outer(np.full(10, 1 - hit / 0.5), (190, 170, 150)))
        if settled:
            beat = ((t - 1.2) % 0.45) / 0.45
            frame[~self.open_lit] += np.array(BEARS_ORANGE) * 0.22 * math.exp(-beat * 4)
            u = ((t - 1.2) % 1.6) / 0.5
            if u <= 1:
                _shine(frame, self.open_lit, gfx.lerp(-12, W + 22, u))
        if t > BD_OPEN - 0.35:                              # into Soldier Field
            a = gfx.smoothstep((t - (BD_OPEN - 0.35)) / 0.35)
            frame = frame * (1 - a) + self.back * a
        return _shake(frame, shake, t)

    def _song(self, t):
        frame = self.back.copy()
        beat = min((t - b for b in self.beats if t >= b), default=9.0)
        flash = math.exp(-beat / 0.12) if t < self.song_end else 0.0
        for tx in (3, 60):                                         # light towers flash on the beat
            frame[2:4, tx - 2:tx + 3] = np.array((255, 245, 215)) * (0.55 + 0.45 * flash)
        frame[COLONNADE_TOP + 3:H - 1] += np.array((90, 45, 5)) * flash * \
            (frame[COLONNADE_TOP + 3:H - 1].max(-1, keepdims=True) < 60)
        if t < BD_INTRO:                                           # BEAR / DOWN / CHICAGO / BEARS
            word, at = max(((w, a) for w, a in BD_WORDS if t >= a), key=lambda q: q[1])
            age = t - at
            self._word(frame, word, age)
            if word == "BEAR":
                self._claws(frame, age)
            if word == "BEARS" and age < 0.6:                      # orange and white sparks
                cols = np.where(np.arange(len(self.sparks))[:, None] % 2 == 0, BEARS_ORANGE, WHITE)
                gfx.splat(frame, 32 + self.sparks[:, 0] * age * 2.2, 10 + self.sparks[:, 1] * age * 2.2 + 25 * age * age,
                          cols * (1 - age / 0.6))
            frame = _shake(frame, 2.0 * math.exp(-age / 0.07), t)
        elif t < self.song_end:                                    # the song, page by page
            u = t - BD_INTRO
            i = max(k for k, (start, _) in enumerate(self.pages) if u >= start - 0.25 or k == 0)
            band = self._page(i, u)
            flip_at = self.pages[i][0] - 0.25
            if i > 0 and u < flip_at + FLIP + 13 * STAGGER:
                band = Show._flip(self._page(i - 1, u, all_sung=True), band, u - flip_at)
            sky = frame[:COLONNADE_TOP]
            lit = band.max(-1) > 0
            sky[lit] = band[lit]
        else:
            v = t - self.song_end
            # The wishbone C swings round into place, then shines; BEAR and DOWN either side
            sweep = gfx.smoothstep(v / 0.5) * 2 * math.pi
            show = self.c_ang <= sweep
            c = gfx.new_frame()[:, :32]
            c[self.c_edge & show] = WHITE
            c[self.c_body & show] = BEARS_ORANGE
            ox = 16
            sub = frame[:, ox:ox + 32]
            lit = c.max(-1) > 0
            sub[lit] = c[lit]
            if 0.6 <= v <= 1.2:
                full = gfx.new_frame()
                full[:, ox:ox + 32] = c
                mask = full.max(-1) > 0
                _shine(frame, mask, gfx.lerp(-12, W + 22, (v - 0.6) / 0.6))
            if v > 0.5:
                self._claws(frame, v - 0.5)
            if v > 0.9:                                            # BEAR | C | DOWN
                a = gfx.smoothstep((v - 0.9) / 0.3)
                for m, cx, col in ((self.side[0], 8, BEARS_ORANGE), (self.side[1], 56, WHITE)):
                    gfx.blit(frame, m, cx - m.shape[1] // 2, 8, np.array(col) * a)
        return frame


WIN_SONGS = {"gocubsgo": GoCubsGo, "beardown": BearDown}     # played after a win (live.WIN_SONGS)


# ------------------------------------------------------------------- Blackhawks chant
# The goal chant as the crowd does it: an opening OH, then lines of DO-DO-DO, DO-DO-DO and
# a run (7, or 5 on some lines), one DO per beat (owner, 2026-10-02). Timing is tapped along
# to the real song on /tap?song=hawks; without it the beats are evenly spaced.
HAWKS_LINES = (7, 5, 7, 7, 7, 5, 7, 7)          # length of each line's run after the two threes
HAWKS_TAIL = 2.5                                 # the spinning light fading out after the last DO
WIN_TAIL = 30.0                                  # after a win: the light sweeping HAWKS WIN! (owner)
OH_LEN = 2.5                                     # the opening OHHHHHH
OH_HS = 6                                        # H's it grows to


def hawks_groups():
    """The chant as groups: ["OH", "DO-DO-DO", "DO-DO-DO", "DO-...-DO", ...]."""
    out = ["OH"]
    for run in HAWKS_LINES:
        out += ["DO-DO-DO", "DO-DO-DO", "-".join(["DO"] * run)]
    return out


def hawks_units():
    """Every beat to tap, as (group index, start, end) inside hawks_groups()."""
    units = []
    for gi, g in enumerate(hawks_groups()):
        for k in range(0, len(g), 3):            # "DO" every 3 characters (DO-)
            units.append((gi, k, min(len(g), k + 2) if g != "OH" else 2))
    return units


def _even_hawks_timing():
    t, out = 0.0, []
    for gi, g in enumerate(hawks_groups()):
        n = len(hawks_units_by_group()[gi])
        for _ in range(n):
            out.append(round(t, 3))
            t += 0.2
        t += 0.6 if gi == 0 else (0.5 if gi % 3 == 0 else 0.2)
    return out


def hawks_units_by_group():
    groups = [[] for _ in hawks_groups()]
    for i, (gi, _, _) in enumerate(hawks_units()):
        groups[gi].append(i)
    return groups


class HawksChant:
    """The Blackhawks chant on the beat over the spinning goal light, ending on a rotating
    red goal light (owner, 2026-10-02)."""

    def __init__(self, timing=None, finale="fade"):
        if timing is None:
            from .song_timing import HAWKS as timing        # the owner's taps
        self.finale = finale                        # "fade", or "win": the light sweeps HAWKS WIN!
        if not timing or len(timing) != len(hawks_units()):
            timing = _even_hawks_timing()
        self.beats = [t + 0.3 for t in timing]      # a moment of goal light first
        gap = self.beats[1] - self.beats[0]         # OHHHHHH gets its full length; the DOs wait
        if gap < OH_LEN + 0.1:
            self.beats = self.beats[:1] + [b + OH_LEN + 0.1 - gap for b in self.beats[1:]]
        self.key = "song:hawks"
        self.end_at = self.beats[-1] + 0.45
        self.tail = WIN_TAIL if finale == "win" else HAWKS_TAIL
        self.duration = self.end_at + self.tail
        self.big, self.small = _t("DO", "7x13B"), _t("DO", "6x10")
        if finale == "win":                         # HAWKS / WIN! for the light to sweep across
            self.sign = np.zeros((H, W), bool)
            for text_, y in (("HAWKS", 5), ("WIN!", 19)):
                m = _t(text_, "7x13B")
                x = (W - m.shape[1]) // 2
                self.sign[y:y + m.shape[0], x:x + m.shape[1]] |= m
            self.sign_edge = _grow(self.sign) & ~self.sign
        # "Ohhhhhh": the H's keep adding on to the right for about 2.5 s (owner, 2026-10-02)
        self.ohs = [_t("O" + "H" * k, "7x13B") for k in range(1, OH_HS + 1)]
        self.oh_x = (W - self.ohs[-1].shape[1]) // 2
        self.oh_len = min(OH_LEN, self.beats[1] - self.beats[0] - 0.1)
        # Where each beat's DO goes: the two threes as big rows, the run smaller on two rows,
        # with a dash before every DO that follows another on its row (DO-DO-DO).
        self.place = []                             # per beat: (group, mask, x, y, dash or None)
        for gi, idxs in enumerate(hawks_units_by_group()):
            n = len(idxs)
            for k, _ in enumerate(idxs):
                if gi == 0:
                    self.place.append((gi, None, self.oh_x, 9, None))
                    continue
                kind = (gi - 1) % 3                 # 0, 1: the threes; 2: the run
                if kind < 2:
                    m, gap, row, cnt, col = self.big, 5, 5 if kind == 0 else 18, 3, k
                else:
                    top = (n + 1) // 2
                    m, gap = self.small, 3
                    row, cnt, col = (6, top, k) if k < top else (19, n - top, k - top)
                x0 = (W - (cnt * m.shape[1] + (cnt - 1) * gap)) // 2 + col * (m.shape[1] + gap)
                dash = None
                if col > 0:                         # (x, y, width, height) in the gap
                    dw = 3 if kind < 2 else 2
                    dash = (x0 - gap + (gap - dw) // 2, row + m.shape[0] // 2 - 1, dw, 2)
                self.place.append((gi, m, x0, row, dash))

    @staticmethod
    def _light(frame, ang, ly, level, t, flash=True):
        """The spinning red goal light at (31.5, ly): its beam sweeping round the panel."""
        dx, dy = _XX - 31.5, _YY - ly
        d = np.angle(np.exp(1j * (np.arctan2(dy, dx) - ang)))
        beam = np.exp(-(d / 0.32) ** 2) * np.clip(1.2 - np.hypot(dx, dy) / 60, 0.2, 1)
        frame += beam[..., None] * np.array((150, 8, 8)) * level
        HawksChant._beam = beam
        y = int(round(ly))
        lamp = (255, 40, 30) if (not flash or int(t * 8) % 2 == 0) else (200, 10, 10)
        frame[max(0, y - 1):y + 2, 28:36] = np.array(lamp) * max(0.45, level)
        if y + 2 < H:
            frame[y + 2, 26:38] = np.array((60, 60, 60)) * max(0.45, level)

    def render(self, t):
        frame = gfx.new_frame()
        if t >= self.end_at:
            # The finish: the same spinning light, then it simply fades out (owner, 2026-10-02).
            # After a win the light sweeps across HAWKS WIN! for 30 s first: dim red
            # letters that flare white-hot as the beam passes over them.
            u = t - self.end_at
            self._light(frame, t * 2 * math.pi * 1.4, 1, 1.0, t)
            if self.finale == "win":
                show = gfx.smoothstep(u / 0.5)
                lit = HawksChant._beam[self.sign]
                frame[self.sign_edge] = 0
                pulse = 0.85 + 0.15 * math.sin(u * 2 * math.pi * 1.4)      # breathes with the spin
                frame[self.sign] = (np.array((160, 8, 6)) * pulse * show +
                                    np.minimum(1.0, lit * 2.2)[:, None] * np.array((255, 225, 215)) * show)
                frame *= 1 - gfx.smoothstep((u - (self.tail - 1.2)) / 1.2)
            else:
                frame *= 1 - gfx.smoothstep(u / self.tail)
            return np.clip(frame, 0, 255)
        self._light(frame, t * 2 * math.pi * 1.4, 1, min(1.0, t / 0.3), t)
        hit = [t - b for b in self.beats if t >= b]
        if hit:
            frame += np.array((110, 0, 0)) * math.exp(-min(hit) / 0.18)
        fade = 1 - gfx.smoothstep((t - (self.end_at - 0.2)) / 0.2)
        cur = max((i for i, b in enumerate(self.beats) if t >= b), default=-1)
        if cur >= 0:
            gi = self.place[cur][0]
            kind = (gi - 1) % 3 if gi else -1
            # The current group, plus the first three while the second three go up
            for i, b in enumerate(self.beats[:cur + 1]):
                g, m, x0, y0, dash = self.place[i]
                if not (g == gi or (kind == 1 and g == gi - 1)):
                    continue
                if g == 0:                          # OHHHHHH, growing to the right
                    u = gfx.clamp01((t - b) / max(0.3, self.oh_len))
                    k = min(OH_HS - 1, int(u * OH_HS))
                    m = self.ohs[k]
                    hk = (t - b) - k * max(0.3, self.oh_len) / OH_HS
                    gfx.clear_behind(frame, m, x0, y0)
                    gfx.blit(frame, m, x0, y0, np.array(WHITE) * fade)
                    if k:                           # the newest H flashes as it lands
                        prev = self.ohs[k - 1].shape[1]
                        gfx.blit(frame, m[:, prev:], x0 + prev, y0, np.minimum(255, np.array(WHITE) * (1 + 2 * math.exp(-hk / 0.06))) * fade)
                    frame += np.array((40, 0, 0)) * math.exp(-hk / 0.1) if k else 0
                    continue
                pop = 1 + 0.35 * math.exp(-(t - b) / 0.08)
                mm = gfx.scale_mask(m, pop)
                x = x0 + (m.shape[1] - mm.shape[1]) // 2
                y = y0 + (m.shape[0] - mm.shape[0]) // 2
                gfx.clear_behind(frame, mm, x, y)
                gfx.blit(frame, mm, x, y, np.array(WHITE) * fade)
                if dash:
                    dx, dy, dw, dh = dash
                    frame[dy:dy + dh, dx:dx + dw] = np.array(WHITE) * fade
        return np.clip(frame, 0, 255)


# ------------------------------------------------------------------- Blackhawks goal
# A Blackhawks goal, arena-scoreboard style (owner, 2026-10-02): GOAL over pulsing horizontal
# lines, choreographed on an eighth-note grid (lines burst out, letters slam in, red fill
# wipes through, three layers split and snap back, a strobe chase through the letters, a
# shine and a final hit); the goal horn (no speaker, so you see it: two gray air horns
# facing each other, blasting waves that crash in the middle and blow up); then the DO chant on the
# tapped timing. Black, white, red, a little yellow.
HG_GOAL = 4.2
HG_HORN = 4.0
HG_RED, HG_YELLOW = np.array((235, 20, 30), np.float32), np.array((255, 205, 30), np.float32)
HG_WHITE = np.array(WHITE, np.float32)
HG_ROWS = np.arange(H, dtype=np.float32)


def _grow(mask, r=1):
    out = mask.copy()
    for dy in range(-r, r + 1):
        for dx in range(-r, r + 1):
            out |= np.roll(np.roll(mask, dy, 0), dx, 1)
    return out


class HawksGoal:
    """GOAL, the horn, then the chant (see HG_*)."""

    LINES, SCALE, KEY, FINALE = ("GOAL",), 1.6, "song:hawksgoal", "fade"
    C1, C2, CBAR = HG_RED, HG_YELLOW, (95, 0, 6)     # main, accent, background-line colors

    def __init__(self, chant=True):
        # The lettering, one or more centered lines, scaled up
        rendered = []
        for text_ in self.LINES:
            m, edges = fonts.text_mask(text_, "7x13B")
            rendered.append((gfx.scale_mask(m, self.SCALE), [int(round(e * self.SCALE)) for e in edges]))
        gap = 2
        total = sum(m.shape[0] for m, _ in rendered) + gap * (len(rendered) - 1)
        y = (H - total) // 2
        self.word = np.zeros((H, W), bool)
        self.letters, self.crops = [], []                 # each letter: its own mask, and a crop to zoom
        xs0, xs1 = [], []
        for m, cuts in rendered:
            x0 = (W - m.shape[1]) // 2
            line = np.zeros((H, W), bool)
            line[y:y + m.shape[0], x0:x0 + m.shape[1]] = m
            self.word |= line
            xs0.append(x0)
            xs1.append(x0 + m.shape[1])
            for a, b in zip([0] + cuts, cuts):
                col = np.zeros((H, W), bool)
                col[:, x0 + a:x0 + b + 1] = True
                letter = line & col
                if letter.any():
                    self.letters.append(letter)
                    self.crops.append(fonts.crop(letter))
            y += m.shape[0] + gap
        self.outline = _grow(self.word) & ~self.word
        self.halo = _grow(self.word, 2)
        self.wx0, self.wx1 = min(xs0), max(xs1)
        edge = np.zeros((H, W), bool)
        edge[0, :] = edge[-1, :] = edge[:, 0] = edge[:, -1] = True
        ys, xs = np.nonzero(edge)
        order = np.argsort(np.arctan2(ys - H / 2, xs - W / 2))
        self.edge_pts = (ys[order], xs[order])            # border lights, in order round the panel
        self._horn_setup()
        self.chant = HawksChant(finale=self.FINALE) if chant else None
        self.key = self.KEY
        self.duration = HG_GOAL + HG_HORN + (self.chant.duration if chant else 0.0)

    # --- GOAL
    def _chase(self, frame, t, colors=None):
        colors = colors or (self.C1, HG_WHITE)
        ys, xs = self.edge_pts
        k = (np.arange(len(ys)) + int(t * 30)) // 4 % len(colors)
        frame[ys, xs] = np.array(colors)[k]

    def _bars(self, frame, t, level=1.0, spread=1.0, speed=14.0):
        """Horizontal lines behind everything, each growing and shrinking in a wave."""
        off = np.abs(HG_ROWS - (H - 1) / 2)
        if spread < 1:
            keep = off <= spread * H / 2
        else:
            keep = np.ones(H, bool)
        thick = 0.5 + 1.5 * (0.5 + 0.5 * np.sin(t * speed - off * 0.7))
        lit = ((HG_ROWS % 4) < thick) & keep
        col = np.where(((HG_ROWS // 4) % 3 == 0)[:, None], (120, 120, 125), self.CBAR)
        frame[lit] = np.maximum(frame[lit], col[lit][:, None, :] * level)

    def _letter(self, frame, i, scale, color, outline=True):
        lm, lx, ly = self.crops[i]
        mm = gfx.scale_mask(lm, scale)
        x = lx + (lm.shape[1] - mm.shape[1]) // 2
        y = ly + (lm.shape[0] - mm.shape[0]) // 2
        if outline:
            grown = np.zeros((mm.shape[0] + 2, mm.shape[1] + 2), bool)
            for dy in range(3):
                for dx in range(3):
                    grown[dy:dy + mm.shape[0], dx:dx + mm.shape[1]] |= mm
            ring = grown.copy()
            ring[1:-1, 1:-1] &= ~mm
            gfx.blit(frame, grown, x - 1, y - 1, (0, 0, 0))
            gfx.blit(frame, ring, x - 1, y - 1, HG_WHITE)
        if color is not None:
            gfx.blit(frame, mm, x, y, color)

    def _goal(self, t):
        frame = gfx.new_frame()
        n = len(self.letters)
        shake = 0.0
        if t < 0.4:                                       # lines burst out from the middle
            u = gfx.ease_out_cubic(t / 0.4)
            self._bars(frame, t, 1.6 - u, spread=u, speed=30)
            frame[int(H / 2) - 1:int(H / 2) + 1] = HG_WHITE * (1 - u)
        else:
            self._bars(frame, t, 0.9 if t < 3.35 else 0.9 * max(0.0, 1 - (t - 3.35) / 0.6))
        if 0.4 <= t < 1.2:                                # each letter slams in, hollow
            for i in range(n):
                at = 0.4 + i * (0.8 / n)
                if t < at:
                    continue
                age = t - at
                self._letter(frame, i, 1 + 1.4 * (1 - gfx.ease_out_cubic(age / 0.1)) if age < 0.1 else 1.0, None)
                if age < 0.12:
                    shake += 2.0 * (1 - age / 0.12)
                    frame += np.array((70, 70, 70)) * (1 - age / 0.12)
        elif 1.2 <= t < 1.55:                             # red fills in, wiping left to right
            edge = gfx.lerp(self.wx0 - 2, self.wx1 + 2, gfx.smoothstep((t - 1.2) / 0.35))
            frame[self.halo] = 0
            frame[self.outline] = HG_WHITE
            filled = self.word & (_XX < edge)
            frame[filled] = self.C1
            frame[self.word & (np.abs(_XX - edge) < 1.0)] = HG_WHITE
        elif 1.55 <= t < 2.35:                            # three layers split out and snap back, twice
            u = t - 1.55
            if u < 0.4:
                off = 3 * math.sin(math.pi * min(1.0, u / 0.25))
            else:
                off = 5 * math.sin(math.pi * min(1.0, (u - 0.4) / 0.3))
            frame[_grow(self.word, 2)] = 0
            for (dx, dy), colr in (((-off, -off * 0.6), self.C2), ((off, off * 0.6), HG_WHITE), ((0, 0), self.C1)):
                gfx.blit(frame, self.word, int(round(dx)), int(round(dy)), colr)
            frame[self.outline] = np.maximum(frame[self.outline], HG_WHITE * (1 - off / 5))
            if off < 0.5 and u > 0.2:
                shake += 1.5
        elif 2.35 <= t < 3.35:                            # strobe chase through the letters
            order = list(range(n)) + list(range(n - 1, -1, -1))   # through the letters and back
            k = int((t - 2.35) / (1.0 / len(order)))
            if k in (len(order) // 2, len(order) - 1):   # full inversions on the accents
                frame[:] = HG_WHITE
                frame[self.halo] = 0
                frame[self.word] = self.C1
            else:
                frame[self.halo] = 0
                frame[self.outline] = HG_WHITE
                frame[self.word] = self.C1
                hot = self.letters[order[k % len(order)]]
                frame[hot] = self.C2 if k == len(order) // 2 + 1 else HG_WHITE
        elif t >= 3.35:                                   # hold, shine, final hit
            frame[self.halo] = 0
            frame[self.outline] = HG_WHITE
            frame[self.word] = self.C1
            u = (t - 3.45) / 0.45
            if 0 <= u <= 1:
                _shine(frame, self.word | self.outline, gfx.lerp(-12, W + 22, u))
            if t > 3.95:
                frame += 255 * 0.5 * math.exp(-(t - 3.95) / 0.07)
                shake += 2.5 * math.exp(-(t - 3.95) / 0.08)
        if t >= 0.4:
            self._chase(frame, t, (self.C1, HG_WHITE, self.C1, HG_WHITE, self.C2) if t > 1.55 else (self.C1, HG_WHITE))
        if t > HG_GOAL - 0.15:
            frame *= 1 - gfx.smoothstep((t - (HG_GOAL - 0.15)) / 0.15)
        return _shake(frame, shake, t)

    # --- the horns: two gray air horns facing each other, blasting into the middle
    HORN_L = 15                                   # horn length (px), from the edge in
    HORN_PULSE = 6.0                              # blasts per second
    HORN_V = 46.0                                 # sound-wave speed (px/s)
    HORN_FINAL = 2.55                             # the last, biggest blast
    HORN_HIT = HORN_FINAL + (W / 2 - 15) / 46.0   # when it crashes in the middle
    HORN_BLOW = 0.16                              # the core swells this long before it bursts

    def _horn_setup(self):
        rng = np.random.default_rng(1926)
        ang = rng.uniform(0, 2 * np.pi, 70)
        spd = rng.uniform(14, 48, 70)
        self.burst_v = np.stack([np.cos(ang) * spd, np.sin(ang) * spd * 0.8], -1)
        self.burst_col = np.where((np.arange(70) % 5 == 0)[:, None], HG_YELLOW,
                                  np.where((np.arange(70) % 2 == 0)[:, None], HG_WHITE, HG_RED))
        # Spray from each collision: thrown up and down, a little sideways
        a2 = rng.uniform(-0.6, 0.6, 10) + np.where(np.arange(10) % 2, np.pi / 2, -np.pi / 2)
        s2 = rng.uniform(18, 40, 10)
        self.spray_v = np.stack([np.cos(a2) * s2, np.sin(a2) * s2], -1)
        self.r_center = np.hypot(_XX - (W - 1) / 2, (_YY - (H - 1) / 2) * 1.25)
        mouth = self.HORN_L + 2
        reach = ((W - 1) / 2 - mouth) / self.HORN_V
        self.blasts = []                              # (born, crash) for every regular blast
        k = 0
        while 0.35 + k / self.HORN_PULSE < self.HORN_FINAL:
            born = 0.35 + k / self.HORN_PULSE
            self.blasts.append((born, born + reach))
            k += 1

    def _draw_horn(self, frame, right, slide, pulse):
        """One horn, side-on: a driver box at the edge, a gray flare widening toward the
        middle, ending in a rectangular mouth (bright rim, dark opening)."""
        cy = (H - 1) / 2
        L = self.HORN_L
        bell = 8.0 * pulse
        for x in range(L):
            u = x / (L - 1)
            half = 2.0 + (bell - 2.0) * u ** 1.7
            if x < 3:                                 # the driver housing
                half = 4.5
            top, bot = int(round(cy - half)), int(round(cy + half))
            rows = np.arange(max(0, top), min(H, bot + 1))
            if not len(rows):
                continue
            k = (rows - top) / max(1, bot - top)      # 0 at the top edge, 1 at the bottom
            g = 215 - 135 * k                         # brushed metal: lit from above
            if x < 3:
                g = g * 0.6
            if x in (5, 10):                          # bands round the flare
                g = g * 0.7
            col = np.stack([g, g, g * 1.05], -1)
            col[0] = (235, 235, 240)                  # highlight along the top edge
            px = x + slide
            sx = int(round(W - 1 - px if right else px))
            if 0 <= sx < W:
                frame[rows, sx] = col
        # The mouth: a rectangle seen slightly from the side, white rim, dark opening
        top, bot = int(round(cy - bell)), int(round(cy + bell))
        for dx in range(3):
            px = L + dx + slide
            sx = int(round(W - 1 - px if right else px))
            if not 0 <= sx < W:
                continue
            frame[max(0, top):min(H, bot + 1), sx] = (35, 35, 40) if dx == 1 else (245, 245, 250)
            if dx == 1:
                frame[max(0, top), sx] = frame[min(H - 1, bot), sx] = (245, 245, 250)

    def _wave(self, frame, x_front, right, height, thick, color, bend=3.0):
        """A wave front: a ')'-shaped band racing toward the middle."""
        cy = (H - 1) / 2
        rows = np.arange(H)
        dy = (rows - cy) / max(1.0, height)
        inside = np.abs(dy) <= 1
        back = bend * dy ** 2                        # the ends trail behind the middle
        for th in range(thick):
            xs = x_front - back - th
            sx = np.round(W - 1 - xs if right else xs).astype(int)
            ok = inside & (sx >= 0) & (sx < W)
            frame[rows[ok], sx[ok]] = np.maximum(frame[rows[ok], sx[ok]], color * (1 - th / (thick + 1)))

    def _horn(self, t):
        frame = gfx.new_frame()
        mid = (W - 1) / 2
        cy = (H - 1) / 2
        mouth = self.HORN_L + 2
        # Slide in from the edges with an overshoot, then pulse with every blast
        slide = -18 * (1 - gfx.ease_out_back(min(1.0, t / 0.35), 2.5))
        beat = (t * self.HORN_PULSE) % 1.0
        blasting = 0.35 <= t < self.HORN_FINAL + 0.1
        pulse = 1 + 0.16 * math.exp(-beat * 5) if blasting else 1.0
        if self.HORN_FINAL <= t < self.HORN_FINAL + 0.4:
            pulse = 1.3 - 0.3 * gfx.smoothstep((t - self.HORN_FINAL) / 0.4)
        # How much has crashed in the middle so far: the build-up (0..1)
        crashed = sum(1 for _, c in self.blasts if t >= c)
        build = crashed / len(self.blasts)
        # Background: dark red, throbbing harder as the pressure builds
        frame[:] = HG_RED * (0.08 + (0.1 + 0.15 * build) * math.exp(-beat * 4)) * (1 if blasting else 0.6)
        self._bars(frame, t, 0.45 + 0.4 * math.exp(-beat * 4), speed=14 + 16 * build)
        shake = (0.8 + 1.6 * build) * math.exp(-beat * 4) if blasting else 0.0
        # Every blast: a wave from each horn; they meet in the middle, squash into a splash,
        # throw spray up and down and bounce back, and each crash feeds the core.
        for k, (born, crash) in enumerate(self.blasts):
            if t < born:
                break
            col = HG_YELLOW if k % 4 == 3 else (HG_WHITE if k % 2 == 0 else HG_RED * 1.1)
            if t < crash:
                x = mouth + (t - born) * self.HORN_V
                h = 7 + (x - mouth) * 0.45
                for right in (False, True):
                    self._wave(frame, x, right, h, 2, col)
                continue
            age = t - crash
            if age < 0.3:                              # the splash: squashed flat, spreading tall
                v = age / 0.3
                hh = 6 + 14 * gfx.ease_out_cubic(v)
                ww = 2.6 * (1 - v) + 0.6
                splash = np.exp(-((_XX - mid) / ww) ** 2 - ((_YY - cy) / hh) ** 6)
                frame += splash[..., None] * (col * 0.5 + HG_WHITE * 0.5) * (1 - v)
                xb = mid - 16 * age                    # the bounce, fading back out
                for right in (False, True):
                    self._wave(frame, xb, not right, 9, 1, col * 0.45 * (1 - v), bend=-2.5)
                pts = np.array([mid, cy]) + self.spray_v * age + np.array([0, 30]) * age * age
                gfx.splat(frame, pts[:, 0], pts[:, 1], np.tile(col * (1 - v), (len(pts), 1)))
        # The core: grows with every crash, pulsing; it is what blows up at the end
        if crashed or t >= self.HORN_HIT:
            if t < self.HORN_HIT:
                r0 = 1.5 + 4.5 * build
                if t >= self.HORN_HIT - self.HORN_BLOW:   # the final fronts arrive: it swells
                    r0 += 4 * gfx.ease_in_quad((t - (self.HORN_HIT - self.HORN_BLOW)) / self.HORN_BLOW)
                r0 *= 1 + 0.2 * math.exp(-beat * 5)
                core = np.exp(-(self.r_center / r0) ** 2)
                heat = 0.55 + 0.45 * build
                frame += core[..., None] * (HG_RED * 0.6 + HG_WHITE * 0.4 * heat + HG_YELLOW * 0.2 * build) * heat
            else:                                      # ...and bursts: the same core blown outward
                u = t - self.HORN_HIT
                r0 = 10.0 + 75 * gfx.ease_out_cubic(min(1.0, u / 0.6))
                shell = np.exp(-((self.r_center - r0) / (3.0 + 4 * u)) ** 2) * max(0.0, 1 - u / 0.75)
                inner = np.exp(-(self.r_center / max(1.0, r0 * 0.7)) ** 2) * math.exp(-u / 0.12)
                frame += shell[..., None] * (HG_WHITE * 0.75 + HG_RED * 0.5)
                frame += inner[..., None] * (HG_WHITE * 1.2)
                ring2 = np.exp(-((self.r_center - r0 * 0.65) / 2.0) ** 2) * max(0.0, 1 - u / 0.8)
                frame += ring2[..., None] * HG_RED
                pts = np.array([mid, cy]) + self.burst_v * u + np.array([0, 20]) * u * u
                gfx.splat(frame, pts[:, 0], pts[:, 1], self.burst_col * max(0.0, 1 - u / 0.9))
                shake = 4.0 * math.exp(-u / 0.15)
        # The final blast: thick white fronts from both horns, straight into the core
        if self.HORN_FINAL <= t < self.HORN_HIT:
            x = mouth + (t - self.HORN_FINAL) * self.HORN_V
            for right in (False, True):
                self._wave(frame, x, right, 12 + (x - mouth) * 0.3, 4, HG_WHITE, bend=4.5)
            shake += 1.5
        # The horns themselves, on top (thrown off the panel by the blast)
        if t < self.HORN_HIT + 0.3:
            gone = max(0.0, t - self.HORN_HIT) * 90
            for right in (False, True):
                self._draw_horn(frame, right, slide - gone, pulse)
        self._chase(frame, t * (2 + 2 * build), (HG_RED, HG_WHITE, HG_RED, HG_YELLOW) if t >= 0.35 else (HG_RED, HG_WHITE))
        out = _shake(frame, shake, t)
        if t > HG_HORN - 0.3:                       # into the song
            out = out * (1 - gfx.smoothstep((t - (HG_HORN - 0.3)) / 0.3))
        return out

    def render(self, t):
        if t < HG_GOAL:
            frame = self._goal(t)
        elif t < HG_GOAL + HG_HORN:
            frame = self._horn(t - HG_GOAL)
        else:
            return self.chant.render(t - HG_GOAL - HG_HORN)
        return np.clip(frame, 0, 255)


class HawksWin(HawksGoal):
    """A Blackhawks win: the goal celebration with HAWKS / WIN! instead of GOAL, ending with
    the spinning red light sweeping across HAWKS WIN! for 30 seconds (owner, 2026-10-02)."""

    LINES, SCALE, KEY, FINALE = ("HAWKS", "WIN!"), 1.0, "song:hawkswin", "win"


WIN_SONGS["hawkswin"] = HawksWin


# ------------------------------------------------------------------- Cubs home runs
# Broadcast-quality, not retro (owner, 2026-10-02). HOME RUN! / 2-RUN HOMER! / 3-RUN HOMER!:
#  1. The pitch, from the ball's path: it races at you out of the stadium lights, spinning,
#     speed lines rushing past; the bat sweeps in and crashes into it: an explosion.
#  2. Inside the park at night, looking out: grass, the ivy wall, packed bleachers and the
#     lit Waveland Avenue rooftops behind. The ball flies away over the ivy and the bleachers
#     and lands out on Waveland; the camera tilts up to follow.
#  3. The title stamped in over Cubs-colored fireworks.
# The grand slam (GrandSlam) adds a broadcast-style diamond with four glowing runners
# coming home, GRAND / SLAM built arena-style in Cubs colors, and a fireworks finale.
HR_PITCH, HR_FLY, HR_TITLE = 1.7, 3.6, 7.6      # scene lengths
HR_HIT = 1.12                                   # the bat meets the ball
CUBS_BRIGHT = np.array((60, 110, 255), np.float32)
CUBS_RED = np.array((235, 35, 45), np.float32)
PARK_H = 64                                     # the park scene is drawn twice the panel height
_park = {}                                      # night (True) / day (False) -> the scene
WRIGLEY_LAT, WRIGLEY_LON = 41.948, -87.656


def sun_times(day=None, lat=WRIGLEY_LAT, lon=WRIGLEY_LON):
    """Sunrise and sunset (aware UTC datetimes) at Wrigley on a date (NOAA formulas)."""
    day = day or datetime.date.today()
    g = 2 * math.pi / 365 * (day.timetuple().tm_yday - 1)
    eqt = 229.18 * (0.000075 + 0.001868 * math.cos(g) - 0.032077 * math.sin(g)
                    - 0.014615 * math.cos(2 * g) - 0.040849 * math.sin(2 * g))
    decl = (0.006918 - 0.399912 * math.cos(g) + 0.070257 * math.sin(g) - 0.006758 * math.cos(2 * g)
            + 0.000907 * math.sin(2 * g) - 0.002697 * math.cos(3 * g) + 0.00148 * math.sin(3 * g))
    la = math.radians(lat)
    ha = math.degrees(math.acos(math.cos(math.radians(90.833)) / (math.cos(la) * math.cos(decl))
                                - math.tan(la) * math.tan(decl)))
    midnight = datetime.datetime.combine(day, datetime.time(), datetime.timezone.utc)
    rise = midnight + datetime.timedelta(minutes=720 - 4 * (lon + ha) - eqt)
    sset = midnight + datetime.timedelta(minutes=720 - 4 * (lon - ha) - eqt)
    return rise, sset


def is_night(now=None):
    """Past sunset (or before sunrise) at Wrigley: the night versions of the park scenes."""
    now = now or datetime.datetime.now(datetime.timezone.utc)
    rise, sset = sun_times(now.astimezone().date())          # (the board runs on Chicago time)
    return not (rise <= now < sset)


def _park_scene(night=True):
    """Wrigley from inside, looking out to center field (64 x 64; cached), at night or in
    daylight: the rooftop bleachers on the brick buildings across the street, the classic
    center-field scoreboard over the bleachers, the ivy wall and the outfield grass."""
    if night in _park:
        return _park[night]
    rng = np.random.default_rng(1914)
    img = np.zeros((PARK_H, W, 3), np.float32)
    for y in range(PARK_H):                     # night sky, or a clear blue day sky
        if night:
            img[y] = gfx.lerp(np.array((2, 3, 12.)), np.array((12, 11, 26.)), min(1.0, y / 22))
        else:
            img[y] = gfx.lerp(np.array((4, 36, 150.)), np.array((40, 100, 205.)), min(1.0, y / 22))   # vivid, not pale
    for _ in range(24 if night else 0):
        img[rng.integers(0, 10), rng.integers(0, W)] = rng.uniform(60, 130)
    day = 0.0 if night else 1.0                     # how much sunlight brightens things
    fans = np.array([(40, 80, 210), (230, 230, 235), (200, 40, 40), (110, 110, 115), (215, 175, 135)])
    # The rooftops across the street, left and right of the scoreboard: brick three-flats,
    # each with stepped bleachers on the roof, a rail, and a light pole
    for x0, x1 in ((0, 9), (10, 19), (45, 54), (55, 64)):
        top = int(rng.integers(17, 20))                       # roofline
        for y in range(top, 30):                              # brick, mortar lines
            img[y, x0:x1] = (np.array((88, 38, 26)) if (y - top) % 3 else np.array((60, 26, 18))) * (1 + 0.45 * day)
        img[top, x0:x1] = (130, 120, 110)                     # stone cornice
        for wy in range(top + 2, 29, 3):                      # warm windows
            for wx in range(x0 + 1, min(W, x1 - 1), 3):
                if rng.random() < 0.7:                        # lit at night, glass by day
                    glow = rng.uniform(0.55, 1.0)
                    img[wy, wx:wx + 2] = np.array((235, 175, 85)) * glow if night else np.array((60, 80, 110)) * glow
        # Rooftop bleachers: four tiers stepping up toward the back, packed with fans
        for k in range(4):
            y = top - 1 - k
            a, b = x0 + 1 + k // 2, x1 - 1 - k // 2
            img[y, a:b] = (55, 55, 62)
            seats = np.arange(a, b)
            full = rng.random(len(seats)) < 0.8
            img[y, seats[full]] = fans[rng.integers(0, len(fans), full.sum())] * (0.75 + 0.25 * day)
        img[top - 5, x0 + 1:x1 - 1] = (150, 150, 160)         # the rail along the back
        px = x0 + 1 if x0 < 32 else x1 - 2                    # a light pole at the corner
        img[top - 9:top - 5, px] = (90, 90, 100)
        img[top - 10, max(0, px - 1):px + 2] = (255, 245, 210) if night else (150, 150, 155)
    img[30, :] = (32, 30, 36) if night else (95, 92, 98)       # the street
    if night:
        img[30, ::6] = (190, 160, 90)                         # street lights
    # The center-field scoreboard: dark green, the clock on a raised center section,
    # pennants flying on poles, rows of line scores
    sx0, sx1, st, sb = 20, 45, 17, 30
    for px in (21, 24, 41, 44):                               # flag poles with pennants
        img[9:st, px] = (130, 130, 140)
        for j, fy in enumerate((9, 11, 13)):
            img[fy:fy + 2, px + (1 if px < 32 else -2):px + (3 if px < 32 else 0)] = \
                FLAG_COLORS[(px + j * 3) % len(FLAG_COLORS)]
    img[12:st, 27:38] = BOARD_GREEN                           # raised clock section
    yy, xx = np.mgrid[0:PARK_H, 0:W]
    face = (xx - 32) ** 2 + (yy - 14) ** 2
    img[face <= 5] = (240, 240, 230)
    img[(face > 5) & (face <= 8)] = (30, 30, 30)
    img[14, 32:34] = img[12:15, 32] = (20, 20, 20)           # clock hands
    img[st:sb, sx0:sx1] = BOARD_GREEN
    img[st, sx0:sx1] = img[sb - 1, sx0:sx1] = BOARD_EDGE
    img[st:sb, sx0] = img[st:sb, sx1 - 1] = BOARD_EDGE
    img[st + 1:sb - 1, 32] = BOARD_EDGE
    for half in (sx0 + 2, 34):
        for r in range(4):                                    # line scores: name plate, innings
            y = st + 2 + r * 3
            img[y, half:half + 3] = (235, 235, 235)
            for i in range(4):
                if rng.random() < 0.8:
                    img[y, half + 4 + i * 2] = (235, 235, 210) if rng.random() < 0.8 else (250, 200, 60)
    # The bleachers in front: steep, packed, lit from the field
    for y in range(31, 40):
        k = (y - 31) / 8
        img[y] = np.array((24 + 30 * k, 24 + 28 * k, 34 + 30 * k)) * (1 + 0.3 * day)
        crowd = rng.random(W) < 0.78
        pick = rng.choice(len(fans), W, p=(0.42, 0.22, 0.12, 0.12, 0.12))
        img[y, crowd] = fans[pick[crowd]] * ((0.35 + 0.5 * k) if night else (0.55 + 0.25 * k))
    # The ivy wall: deep green leaves on brick, a yellow line along the top
    img[40] = (210, 175, 40)
    for y in range(41, 45):
        img[y] = np.outer(rng.uniform(0.6, 1.25, W), (22, 92, 32)) * (1 + 0.4 * day)
    img[45] = (60, 40, 25)                                     # warning track
    img[46] = (90, 62, 38)
    # Outfield grass, mowed in stripes, brighter toward us under the lights
    for y in range(47, PARK_H):
        k = (y - 47) / (PARK_H - 47)
        stripe = ((np.arange(W) + (y - 47) // 2) // 6) % 2
        light = (0.55 + 0.6 * k) if night else (0.8 + 0.3 * k)
        img[y] = np.where(stripe[:, None] == 0, (20, 90, 34), (28, 110, 42)) * light
    # Light towers at the sides: glowing at night, off by day
    for lx in (2, 61):
        img[31:40, lx] = (70, 70, 80) if night else (120, 120, 130)
        img[29:31, lx - 2:lx + 3] = (255, 248, 225) if night else (170, 170, 175)
    _park[night] = img
    return img


class HomeRun:
    """HOME RUN! / 2-RUN HOMER! / 3-RUN HOMER! (see HR_*)."""

    max_load = 0.30        # bright enough for daylight; 3 plays at full brightness, no supply dips (2026-10-02)
    TITLE_COLS = (CUBS_BRIGHT, CUBS_RED)          # the two title lines
    FW_COLS = (CUBS_BLUE, HAWKS_RED, WHITE)        # the fireworks behind the title
    CLEAN_RING = False                              # (home runs keep the outline as approved)

    def __init__(self, title="HOME RUN!", night=None):
        self.night = is_night() if night is None else night   # the night or day version (owner)
        self.title = title.upper()
        words = self.title.split()
        lines = (words[0], " ".join(words[1:])) if len(words) > 1 else (self.title, "")
        self.lines = []
        for text_, y in zip(lines, (2, 17)):
            m = _t(text_, "7x13B")
            if m.shape[1] > W - 2:
                m = _t(text_, "6x10")
            self.lines.append((m, (W - m.shape[1]) // 2, y))
        self.lit = np.zeros((H, W), bool)
        for m, x, y in self.lines:
            self.lit[y:y + m.shape[0], x:x + m.shape[1]] |= m
        self.park = _park_scene(self.night)
        rng = np.random.default_rng(16)
        self.fireworks = [Firework(rng, 0.25 + i * 0.42, rng.uniform(6, 58), rng.uniform(5, 14),
                                   self.FW_COLS[i % len(self.FW_COLS)], n=26) for i in range(17)]
        ang = rng.uniform(0, 2 * np.pi, 60)
        self.lines_ang = rng.uniform(0, 2 * np.pi, 28)          # speed lines rushing past
        self.lines_ph = rng.uniform(0, 1, 28)
        self.debris_v = np.stack([np.cos(ang), np.sin(ang)], -1) * rng.uniform(15, 55, 60)[:, None]
        self.debris_c = np.where((np.arange(60) % 3 == 0)[:, None], (255, 190, 90), (255, 250, 235))
        self.r_center = np.hypot(_XX - 31.5, (_YY - 15.5) * 1.15)
        self.key = "song:homerun"
        self.duration = HR_PITCH + HR_FLY + HR_TITLE

    # 1) the pitch and the crash
    def _pitch(self, t):
        frame = gfx.new_frame()
        if self.night:                               # the stadium lights behind the pitch
            frame += np.exp(-self.r_center / 18)[..., None] * np.array((85, 85, 110))   # brighter lights (owner)
            frame[0:3, 4:14] = frame[0:3, 50:60] = (150, 150, 160)                     # light banks up top
        else:                                        # a bright day sky
            frame += (np.array((4, 26, 105)) + np.exp(-self.r_center / 22)[..., None] * np.array((45, 65, 95)))
        cx, cy = 31.5, 15.5
        if t < HR_HIT:
            u = t / HR_HIT
            # Speed lines streaking outward, faster as the ball gets close (all in one pass)
            p = (self.lines_ph + t * (1.2 + 3 * u)) % 1.0
            rr = (6 + 40 * p)[:, None] + np.linspace(0, 1, 6)[None, :] * (3 + 12 * u)
            ca, sa = np.cos(self.lines_ang)[:, None], np.sin(self.lines_ang)[:, None]
            cols = np.repeat(np.outer((0.5 + 0.8 * u) * p, (210, 220, 250)), 6, axis=0)
            gfx.splat(frame, (cx + ca * rr).ravel(), (cy + sa * rr * 0.7).ravel(), cols)
            # The ball coming at you: tiny, then huge; seams spinning
            r = 0.8 + 11 * u ** 3
            d = np.hypot(_XX - cx, _YY - cy)
            ball = d <= r
            halo = np.exp(-np.maximum(0, d - r) / (1.5 + r * 0.4))       # a soft glow round the ball
            frame += halo[..., None] * np.array((90, 90, 80)) * (0.5 + 0.5 * u)
            shade = np.clip(1 - (d / max(r, 0.5)) ** 2 * 0.3 - ((_XX - cx) + (_YY - cy)) / max(r, 1) * 0.08, 0.7, 1)
            frame[ball] = (np.array((255, 253, 245)) * shade[..., None])[ball]
            if r > 3:
                spin = t * 40
                for sgn in (-1, 1):                              # two curved seams
                    a = np.linspace(-1.2, 1.2, 30) + spin
                    sx = cx + sgn * r * 0.55 * np.cos(a * 0.6) + np.sin(a) * r * 0.25
                    sy = cy + r * 0.8 * np.sin(a * 0.9)
                    ok = np.hypot(sx - cx, sy - cy) < r * 0.95
                    gfx.splat(frame, sx[ok], sy[ok], np.tile((210, 30, 35), (ok.sum(), 1)))
            # The bat sweeping in from the left just before contact, motion-blurred
            if t > HR_HIT - 0.3:                     # the bat whipping round into the ball
                v = (t - (HR_HIT - 0.3)) / 0.3
                xs, ys, cs = [], [], []
                rr = np.linspace(4, 60, 80)
                width = 1.2 + rr / 16                    # handle thin, barrel thick
                for k, fade in ((0.0, 1.0), (0.08, 0.5), (0.16, 0.25)):   # motion blur behind it
                    ang = gfx.lerp(2.3, 0.2, gfx.ease_in_quad(max(0.0, v - k)))
                    bx_, by_ = -14 + np.cos(ang) * rr, 34 - np.sin(ang) * rr
                    for w in (-1.0, -0.5, 0.0, 0.5, 1.0):
                        xs.append(bx_ + w * width * np.sin(ang))
                        ys.append(by_ + w * width * np.cos(ang))
                        tone = (240, 190, 115) if w <= -0.5 else (205, 150, 85) if w < 0.5 else (140, 95, 50)
                        cs.append(np.tile(np.array(tone) * fade, (80, 1)))
                gfx.splat(frame, np.concatenate(xs), np.concatenate(ys), np.concatenate(cs))
            return frame
        # The crash: a white blast, shock rings, sparks and splinters
        u = t - HR_HIT
        frame += 255 * math.exp(-u / 0.07)
        for k, (spd, col) in enumerate(((80, WHITE), (55, (255, 180, 70)), (34, CUBS_BRIGHT))):
            ring = np.exp(-((self.r_center - spd * u) / (1.6 + 2 * u)) ** 2) * max(0.0, 1 - u / 0.55)
            frame += ring[..., None] * np.array(col, np.float32)
        core = np.exp(-(self.r_center / (3 + 10 * u)) ** 2) * math.exp(-u / 0.2)
        frame += core[..., None] * np.array((255, 230, 180))
        pts = np.array([cx, cy]) + self.debris_v * u + np.array([0, 25]) * u * u
        gfx.splat(frame, pts[:, 0], pts[:, 1], self.debris_c * max(0.0, 1 - u / 0.6))
        frame = _shake(frame, 4.5 * math.exp(-u / 0.1), t)
        if t > HR_PITCH - 0.2:
            frame *= 1 - gfx.smoothstep((t - (HR_PITCH - 0.2)) / 0.2)
        return frame

    # 2) out of the park
    def _fly(self, t):
        u = t / HR_FLY
        cam = int(round(gfx.lerp(PARK_H - H, 4, gfx.smoothstep(min(1.0, u / 0.8)))))   # tilt up
        frame = self.park[cam:cam + H].copy()
        if t < 0.25:                                     # coming in from the white flash
            frame = frame + 255 * (1 - t / 0.25) ** 2
        # The ball's flight in scene coordinates: from just in front of us, away and up,
        # over the wall (row 39) and the bleachers, down onto Waveland (row ~26)
        def path(v):
            x = gfx.lerp(30, 51, v)                 # past the scoreboard, out to the street
            y = gfx.lerp(62, 29, v) - 30 * math.sin(math.pi * min(1.0, v)) * 0.9
            return x, y, gfx.lerp(3.2, 0.7, v ** 0.6)
        v = min(1.0, u / 0.82)
        trail = np.array([path(max(0.0, v - k * 0.012)) for k in range(14)])
        cols = np.outer(1 - np.arange(14) / 14, (255, 245, 210)) * 0.55
        gfx.splat(frame, trail[:, 0], trail[:, 1] - cam, cols)
        bx, by, br = path(v)
        by -= cam
        glow = np.exp(-((_XX - bx) ** 2 + (_YY - by) ** 2) / (br * br * 4 + 3))
        frame += glow[..., None] * np.array((110, 110, 90))
        frame[(_XX - bx) ** 2 + (_YY - by) ** 2 <= max(0.5, br * br)] = (255, 252, 240)
        if u >= 0.82:                                    # it lands on Waveland: a flash and sparkle
            w = (u - 0.82) / 0.18
            s = np.exp(-((_XX - bx) ** 2 + ((_YY - by) * 1.6) ** 2) / (4 + 30 * w))
            frame += s[..., None] * np.array((255, 220, 140)) * (1 - w)
            frame[max(0, 31 - cam):max(0, 40 - cam)] *= 1 + 0.25 * math.exp(-w * 3)   # the bleachers light up
        if t > HR_FLY - 0.25:
            frame *= 1 - gfx.smoothstep((t - (HR_FLY - 0.25)) / 0.25)
        return frame

    # 3) the title
    def _title(self, t):
        frame = gfx.new_frame()
        for fw in self.fireworks:
            fw.draw(frame, t, 0.8)
        shake = 0.0
        for i, (m, x, y) in enumerate(self.lines):
            at = 0.05 + i * 0.35
            if t < at:
                continue
            age = t - at
            col = np.array(self.TITLE_COLS[min(i, len(self.TITLE_COLS) - 1)], np.float32)
            if age < 0.12:                               # stamped down
                mm = gfx.scale_mask(m, 1 + 1.2 * (1 - age / 0.12))
                gfx.blit(frame, mm, x + (m.shape[1] - mm.shape[1]) // 2, y + (m.shape[0] - mm.shape[0]) // 2, col)
                continue
            hot = math.exp(-(age - 0.12) / 0.1)
            pm = np.pad(m, 1)
            ring = _clean_outline(pm) if self.CLEAN_RING else _grow(pm) & ~pm
            gfx.blit(frame, pm | ring, x - 1, y - 1, (0, 0, 0))
            gfx.blit(frame, ring, x - 1, y - 1, WHITE)
            gfx.blit(frame, m, x, y, col * (1 - hot) + 255 * hot)
            shake += 2.5 * math.exp(-(age - 0.12) / 0.08)
        u = (t - 1.2) / 0.5
        if 0 <= u <= 1:
            _shine(frame, self.lit, gfx.lerp(-12, W + 22, u))
        if t > HR_TITLE - 0.4:
            frame *= 1 - gfx.smoothstep((t - (HR_TITLE - 0.4)) / 0.4)
        return _shake(frame, shake, t)

    def render(self, t):
        if t < HR_PITCH:
            frame = self._pitch(t)
        elif t < HR_PITCH + HR_FLY:
            frame = self._fly(t - HR_PITCH)
        else:
            frame = self._title(t - HR_PITCH - HR_FLY)
        return np.clip(frame, 0, 255)


# ------------------------------------------------------------------- Cubs grand slam
GS_RUN, GS_LETTERS, GS_FINALE = 5.2, 4.2, 6.4
_infield = {}                                   # night (True) / day (False) -> the scene


def _proj(X, Y):
    """Field coordinates (feet: X right, Y out from home plate) seen from a camera high
    behind home plate -> (screen x, screen y, scale)."""
    d = 1.0 / (1.0 + Y / 120.0)
    return 32 + X * 0.30 * d, 6 + 24 * d, d


def _infield_scene(night=True):
    """The TV shot from behind home plate, at night or by day (cached): packed stands and
    the ivy at the back, outfield grass in stripes, the dirt base paths and mound, chalk lines."""
    if night in _infield:
        return _infield[night]
    rng = np.random.default_rng(1908)
    img = np.zeros((H, W, 3), np.float32)
    fans = np.array([(40, 80, 210), (230, 230, 235), (200, 40, 40), (110, 110, 115), (215, 175, 135)])
    for y in range(0, 9):                                   # the stands, dim, packed
        k = y / 8
        img[y] = np.array((14 + 10 * k, 14 + 10 * k, 24 + 10 * k)) * (1 if night else 1.6)
        crowd = rng.random(W) < 0.7
        img[y, crowd] = fans[rng.integers(0, len(fans), crowd.sum())] * ((0.25 + 0.25 * k) if night else (0.45 + 0.25 * k))
    img[9] = (200, 170, 40)                                 # yellow line atop the wall
    img[10:12] = np.outer(rng.uniform(0.6, 1.2, W), (22, 88, 30))   # the ivy
    for sy in range(12, H):
        d = (sy + 0.5 - 6) / 24
        Y = 120 * (1 / d - 1)
        for sx in range(W):
            X = (sx + 0.5 - 32) / (0.30 * d)
            u, v = (Y + X) / 1.414, (Y - X) / 1.414          # along the 1st- and 3rd-base lines
            light = (0.55 + 0.55 * d) if night else (0.8 + 0.3 * d)
            col = (24, 104, 38) if int(Y / 14) % 2 else (30, 120, 46)       # mowed stripes
            if abs(X) > Y + 4:
                col = (18, 80, 30)                                           # foul ground
            grass_sq = 9 < u < 81 and 9 < v < 81
            skin = np.hypot(X, Y - 60.5) < 95 and abs(X) <= Y + 10 and Y > -14 and not grass_sq
            if skin or np.hypot(X, Y - 60.5) < 9 or np.hypot(X, Y) < 14:
                col = (120, 78, 45)                                          # the dirt
            if Y > 0 and abs(abs(X) - Y) < 1.6 / d * 0.6 and not (7 < u < 83 and 7 < v < 83):
                col = (225, 225, 220)                                        # foul lines
            img[sy, sx] = np.array(col) * light
    _infield[night] = img
    return img


class _CubsLetters(HawksGoal):
    """GRAND / SLAM built like the Hawks goal lettering, in Cubs blue with red accents."""

    LINES, SCALE, KEY = ("GRAND", "SLAM"), 1.0, "song:grandslam-letters"
    C1, C2, CBAR = CUBS_BRIGHT, CUBS_RED, (0, 10, 80)

    def __init__(self):
        super().__init__(chant=False)


class GrandSlam(HomeRun):
    """The home run's pitch and flight, then the TV shot from behind the plate as four
    runners come home, GRAND / SLAM built arena-style in Cubs colors, and a fireworks finale."""

    BASES = ((0.0, 0.0), (63.6, 63.6), (0.0, 127.3), (-63.6, 63.6))    # home, 1st, 2nd, 3rd (feet)
    START, SPEED = 0.45, 1.25                        # runners take off; bases per second

    def __init__(self, night=None):
        super().__init__("GRAND SLAM", night)
        self.letters = _CubsLetters()
        self.field = _infield_scene(self.night)
        rng = np.random.default_rng(44)
        self.finale = [Firework(rng, i * 0.22, rng.uniform(4, 60), rng.uniform(4, 16),
                                (CUBS_BLUE, HAWKS_RED, WHITE, (255, 200, 60))[i % 4], n=30) for i in range(28)]
        self.key = "song:grandslam"
        self.t_run = HR_PITCH + HR_FLY
        self.t_letters = self.t_run + GS_RUN
        self.t_finale = self.t_letters + GS_LETTERS
        self.duration = self.t_finale + GS_FINALE
        self.digits = {str(n): _t(str(n), "6x10") for n in range(5)}
        self.runs_label = _t("RUNS", "4x6")
        self.scores = [self.START + (4 - b0) / self.SPEED for b0 in (3, 2, 1, 0)]
        self.flash_rng = np.random.default_rng(7)

    def _field_pos(self, s):
        """Along the base path in feet: 0 home, 1 first, 2 second, 3 third, 4 home."""
        i = min(3, int(s))
        a, b = self.BASES[i], self.BASES[(i + 1) % 4]
        f = s - i
        return a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f

    def _runner(self, frame, s, t, k):
        """A runner in home whites and a blue helmet, legs and arms pumping, leaning into
        the run, kicking up dust; bigger as he comes toward the camera."""
        X, Y = self._field_pos(s)
        x, y, d = _proj(X, Y)
        X2, Y2 = self._field_pos(min(4.0, s + 0.02))
        x2, _, _ = _proj(X2, Y2)
        dirx = 1.0 if x2 >= x else -1.0
        h = 2.5 + 4.5 * d                                # height in pixels
        ph = t * 14 + k * 1.7                            # stride
        lean = 0.18 * h * dirx
        hip = (x + lean * 0.3, y - h * 0.45)
        neck = (x + lean, y - h * 0.82)
        pts, cols = [], []

        def seg(a, b, col, n=8):
            for f in np.linspace(0, 1, n):
                pts.append((a[0] + (b[0] - a[0]) * f, a[1] + (b[1] - a[1]) * f))
                cols.append(col)
        sw = math.sin(ph)
        for sgn in (1, -1):                              # legs: thigh and shin
            knee = (hip[0] + dirx * sgn * sw * h * 0.22, hip[1] + h * 0.25)
            foot = (knee[0] - dirx * sgn * max(0.0, -sw * sgn) * h * 0.18 + dirx * sgn * sw * h * 0.08, y)
            seg(hip, knee, (215, 215, 225))
            seg(knee, foot, (40, 70, 190))               # blue socks
        seg(hip, neck, (240, 240, 245), 10)              # jersey
        for sgn in (1, -1):                              # arms pumping opposite the legs
            hand = (neck[0] - dirx * sgn * sw * h * 0.25, neck[1] + h * 0.3)
            seg((neck[0], neck[1] + 0.5), hand, (230, 230, 238), 6)
        head = (neck[0] + 0.2 * dirx, neck[1] - h * 0.13)
        pts += [head, (head[0] + 0.6, head[1]), (head[0], head[1] - 0.6)]
        cols += [(30, 60, 210)] * 3                     # the helmet
        # Dust kicked up behind (soft), then the runner himself drawn crisp on top
        ages = ((t * 3 + np.arange(6) / 6 + k * 0.37) % 1.0) * 0.5
        back = np.array([_proj(*self._field_pos(max(0.0, s - a * self.SPEED))) for a in ages])
        gfx.splat(frame, back[:, 0] - dirx * ages * 4, back[:, 1] - ages * 6 * back[:, 2],
                  np.outer((1 - ages / 0.5) * 0.6, (170, 130, 90)))
        p = np.round(np.array(pts)).astype(int)
        ok = (p[:, 0] >= 0) & (p[:, 0] < W) & (p[:, 1] >= 0) & (p[:, 1] < H)
        frame[p[ok, 1], p[ok, 0]] = np.array(cols, np.float32)[ok]

    def _diamond(self, t):
        frame = self.field.copy()
        on = min(1.0, t / 0.3)
        # Camera flashes popping in the stands, more after every run
        scored = sum(1 for sc in self.scores if t >= sc)
        last = max((sc for sc in self.scores if t >= sc), default=None)
        rng = np.random.default_rng(int(t * 24))
        n = 3 + 4 * scored + (12 if last is not None and t - last < 0.4 else 0)
        frame[rng.integers(0, 9, n), rng.integers(0, W, n)] = (255, 255, 245)
        for bx, by in self.BASES[1:]:                    # the bags
            x, y, d = _proj(bx, by)
            frame[int(y), int(x) - 1:int(x) + 1] = (245, 245, 245)
        hx, hy, _ = _proj(0, 0)
        frame[int(hy) - 1, int(hx) - 1:int(hx) + 2] = (245, 245, 245)       # home plate
        for k, b0 in enumerate((3, 2, 1, 0)):            # 3rd, 2nd, 1st, then the batter
            if t >= self.scores[k]:
                continue
            s = min(4.0, b0 + max(0.0, t - self.START) * self.SPEED)
            self._runner(frame, s, t, k)
        if last is not None:                             # the plate lights up, a ring goes out
            age = t - last
            ell = np.hypot((_XX - hx) / 1.0, (_YY - hy) / 0.35)
            ring = np.exp(-((ell - 45 * age) / 1.6) ** 2) * max(0.0, 1 - age / 0.6)
            frame += ring[..., None] * np.array((255, 255, 255))
            pillar = np.exp(-((_XX - hx) / 1.5) ** 2) * np.clip(1 - (hy - _YY) / 26, 0, 1) * (_YY <= hy)
            frame += pillar[..., None] * np.array((200, 220, 255)) * math.exp(-age / 0.25)
            frame += 70 * math.exp(-age / 0.08)
        # The broadcast RUNS badge, top right: the number rolls up on every run
        frame[0:18, 44:64] = (6, 12, 48)                # badge: navy, a Cubs-blue frame
        frame[0, 44:64] = frame[17, 44:64] = CUBS_BRIGHT
        frame[0:18, 44] = frame[0:18, 63] = CUBS_BRIGHT
        gfx.blit(frame, self.runs_label, 54 - self.runs_label.shape[1] // 2, 2, (170, 190, 240))
        win = np.zeros((10, 14, 3), np.float32)          # the digit window (rows 6..15)
        roll = gfx.smoothstep((t - last) / 0.22) if last is not None else 1.0
        new, old = str(scored), str(max(0, scored - 1))
        col = CUBS_RED if scored == 4 else np.array(WHITE, np.float32)
        for digit, dy in ((new, (1 - roll) * 10), (old, -roll * 10)) if last is not None else ((new, 0.0),):
            m = self.digits[digit]
            gfx.blit(win, m, (14 - m.shape[1]) // 2, int(round(dy)), col)
        frame[7:17, 47:61] = win
        frame *= on
        if t > GS_RUN - 0.25:
            frame *= 1 - gfx.smoothstep((t - (GS_RUN - 0.25)) / 0.25)
        return frame

    def _final(self, t):
        frame = gfx.new_frame()
        for fw in self.finale:
            fw.draw(frame, t, 0.9)
        lit = self.letters.word
        frame[self.letters.halo] = 0
        frame[self.letters.outline] = HG_WHITE
        frame[lit] = CUBS_BRIGHT
        u = (t % 1.2) / 0.5
        if u <= 1:
            _shine(frame, lit | self.letters.outline, gfx.lerp(-12, W + 22, u))
        if t > GS_FINALE - 0.6:
            frame *= 1 - gfx.smoothstep((t - (GS_FINALE - 0.6)) / 0.6)
        return frame

    def render(self, t):
        if t < self.t_run:
            return HomeRun.render(self, t)
        if t < self.t_letters:
            frame = self._diamond(t - self.t_run)
        elif t < self.t_finale:
            frame = self.letters._goal(t - self.t_letters)
        else:
            frame = self._final(t - self.t_finale)
        return np.clip(frame, 0, 255)


# ------------------------------------------------------------------- Bears touchdown
# Just the words, TOUCH / DOWN!, for 20 seconds, as intense as possible (owner, 2026-10-02:
# no football scenes, no fireworks, no claws, no Ditka). Six stylings cut together with
# white-hot hits: arena build, speed run, neon, shatter, glitch strobe, hero finish.
# Pick six: the same with PICK / SIX!.
BEARS_BRIGHT = np.array((255, 115, 15), np.float32)      # brighter, hotter (owner)
BEARS_NAVY_LIT = np.array((30, 65, 210), np.float32)     # a bright royal blue (owner)
TD_SECTIONS = (3.0, 3.0, 3.0, 3.0, 3.8)            # speed, hammer, split, tunnel, hero (arena build removed: owner)
TD_BEAT = 0.25


def _sky(frame_rows, night):
    """A sky gradient for the top rows: deep night, or a vivid day blue."""
    out = np.zeros((frame_rows, W, 3), np.float32)
    for y in range(frame_rows):
        k = min(1.0, y / max(1, frame_rows - 1))
        out[y] = gfx.lerp(np.array((2, 3, 14.)), np.array((12, 12, 30.)), k) if night else \
            gfx.lerp(np.array((4, 36, 150.)), np.array((40, 100, 205.)), k)
    return out


def _clean_outline(word):
    """A 1-LED outline round lettering that leaves narrow gaps black: nothing in the 1-2 LED
    spaces between letters or in small holes (O, D, A), so letters never run together."""
    o = _grow(word) & ~word
    def near(dy, dx):
        return np.roll(np.roll(word, dy, 0), dx, 1)
    left = near(0, 1) | near(0, 2)
    right = near(0, -1) | near(0, -2)
    up = near(1, 0) | near(2, 0)
    down = near(-1, 0) | near(-2, 0)
    return o & ~((left & right) | (up & down))


class BearsTouchdown:
    """TOUCH / DOWN! for about 16 seconds through five stylings (see TD_SECTIONS): the speed
    run, then hammer, split screen, tunnel and the hero finish in the custom display font, in
    bright royal blue and hot orange (owner, 2026-10-02)."""

    max_load = 0.30
    TOP, BOTTOM = (255, 205, 95), (255, 100, 0)          # display lettering: hot orange gradient

    def __init__(self, lines=("TOUCH", "DOWN!"), night=None):
        # The speed run: the whole thing on one line, huge
        run = "".join(lines) if lines == ("TOUCH", "DOWN!") else " ".join(lines)
        self.run = gfx.scale_mask(_t(run, "7x13B"), 2.1)
        self.run_y = (H - self.run.shape[0]) // 2
        rng = np.random.default_rng(6)
        self.streaks = np.stack([rng.integers(0, H, 26), rng.uniform(0, 1, 26), rng.uniform(4, 14, 26)], -1)
        # The display-font lines and each letter on its own (for the hammer)
        self.dlines = []
        for text_, y in zip(lines, (2, 17)):
            m = display_text(text_)
            if m.shape[1] > W - 2:
                m = display_text(text_, 1)
            if m.shape[1] > W - 2:
                m = gfx.scale_mask(m, (W - 2) / m.shape[1])
            self.dlines.append((m, (W - m.shape[1]) // 2, y))
        self.dword = np.zeros((H, W), bool)
        for m, x, y in self.dlines:
            self.dword[y:y + m.shape[0], x:x + m.shape[1]] |= m
        self.dletters = []                                  # (mask, x, y) per letter
        for (m, x, y), text_ in zip(self.dlines, lines):
            cols = np.nonzero(m.any(0))[0]
            runs = np.split(cols, np.nonzero(np.diff(cols) > 1)[0] + 1)
            for r in runs:
                self.dletters.append((m[:, r[0]:r[-1] + 1], x + r[0], y))
        # The tunnel: the whole two-line word, ready to scale
        cm, self.cx0, self.cy0 = fonts.crop(self.dword)
        self.cword = cm
        self.ang = np.arctan2(_YY - 15.5, _XX - 31.5)
        self.dist = np.hypot(_XX - 31.5, (_YY - 15.5) * 1.3)
        self.cheb = np.maximum(np.abs(_XX - 31.5) / 2.0, np.abs(_YY - 15.5))   # rectangular rings
        self.sparkle = np.stack([rng.integers(0, W, 30), rng.integers(0, H, 30), rng.uniform(0, 1, 30)], -1)
        self.starts = np.cumsum((0,) + TD_SECTIONS)
        self.duration = float(self.starts[-1])
        self.key = "song:bearstd"

    def _paint(self, frame, light=1.0):
        for m, x, y in self.dlines:
            _styled(frame, m, x, y, self.TOP, self.BOTTOM, light)

    # 2) the speed run
    def _speed(self, t):
        frame = gfx.new_frame()
        frame[:] = BEARS_NAVY_LIT * 0.35
        frame[11:21] += BEARS_BRIGHT * 0.14                       # a hot band through the middle
        for y, ph, ln in self.streaks:                            # speed lines tearing past
            x0 = W - ((ph * 1.7 + t * 2.2) % 1.0) * (W + 40)
            frame[int(y), max(0, int(x0)):min(W, int(x0 + ln))] = (170, 190, 255)
        span = W + self.run.shape[1]
        x = W - span * (t / TD_SECTIONS[0]) ** 0.9
        for k, fade in ((12, 0.15), (7, 0.3), (3, 0.55)):         # motion trail
            gfx.blit(frame, self.run, int(x + k), self.run_y, BEARS_BRIGHT * fade)
        gfx.blit(frame, self.run, int(x), self.run_y, BEARS_BRIGHT)
        gfx.blit(frame, self.run[:2], int(x), self.run_y, HG_WHITE)   # a hot top edge
        return _shake(frame, 1.6, t)

    # 3) hammer: every letter slams down with a shockwave
    def _hammer(self, t):
        frame = gfx.new_frame()
        n = len(self.dletters)
        step = 2.0 / n
        landed = [i for i in range(n) if t >= 0.15 + i * step + 0.1]
        last = (0.15 + max(landed) * step + 0.1) if landed else None
        if last is not None:                                      # the background flashes on every hit
            age = t - last
            col = BEARS_NAVY_LIT if len(landed) % 2 else BEARS_BRIGHT
            frame += np.array(col) * (0.25 + 0.6 * math.exp(-age / 0.12))
            frame[:] = np.maximum(frame, BEARS_NAVY_LIT * 0.3)
        shake = 0.0
        for i, (m, x, y) in enumerate(self.dletters):
            at = 0.15 + i * step
            if t < at:
                continue
            age = t - at
            if age < 0.1:                                         # dropping from three times the size
                s = 1 + 2.0 * (1 - gfx.ease_in_quad(age / 0.1))
                mm = gfx.scale_mask(m, s)
                gfx.blit(frame, mm, x + (m.shape[1] - mm.shape[1]) // 2, y + (m.shape[0] - mm.shape[0]) // 2,
                         np.array((255, 190, 110)) * 0.8)
                continue
            _styled(frame, m, x, y, self.TOP, self.BOTTOM)
            hit = age - 0.1
            if hit < 0.45:                                        # the shockwave out of the letter
                cx, cy = x + m.shape[1] / 2, y + 6.5
                r = np.hypot(_XX - cx, (_YY - cy) * 1.2)
                ring = np.exp(-((r - 60 * hit) / 1.6) ** 2) * (1 - hit / 0.45)
                frame += ring[..., None] * (HG_WHITE * 0.7 + (BEARS_BRIGHT if i % 2 else BEARS_NAVY_LIT * 2) * 0.5)
                shake += 2.6 * math.exp(-hit / 0.07)
        if t > 2.3:                                               # all in: a hard pulse on the beat
            beat = (t % TD_BEAT) / TD_BEAT
            self._paint(frame, 0.85 + 0.3 * math.exp(-beat * 5))
        return _shake(frame, shake, t)

    # 4) split screen
    def _split(self, t):
        if t < 2.0:                                               # the seam sweeps across, twice
            u = (t % 1.0) / 1.0
            pos = gfx.lerp(-30, W + 30, gfx.smoothstep(u))
            flip = int(t) % 2 == 1
        else:                                                     # then hard swaps on the beat
            pos = W / 2
            flip = int((t - 2.0) / (TD_BEAT / 2)) % 2 == 1
        side = (_XX + (_YY - 15.5) * 0.8) < pos                   # a slanted seam
        if flip:
            side = ~side
        frame = gfx.new_frame()
        frame[side] = np.array((40, 90, 255))                     # royal blue half
        frame[~side] = np.array((255, 110, 15)) * 0.9             # orange half
        for m, x, y in self.dlines:
            mm = np.zeros((H, W), bool)
            mm[y:y + m.shape[0], x:x + m.shape[1]] = m
            shadow = np.roll(np.roll(mm, 1, 0), 1, 1) & ~mm
            frame[shadow] = (10, 15, 40)
            frame[mm & side] = (255, 140, 30)                     # orange letters on blue
            frame[mm & ~side] = (25, 60, 230)                     # blue letters on orange
            bevel = mm & ~np.roll(mm, 1, 0)
            frame[bevel] = (255, 240, 220)
        seam = np.abs((_XX + (_YY - 15.5) * 0.8) - pos) < 0.9
        frame[seam] = HG_WHITE
        return _shake(frame, 1.2 if t >= 2.0 else 0.4, t)

    # 5) tunnel
    def _tunnel(self, t):
        frame = gfx.new_frame()
        speed = 26 + 10 * t
        for k in range(6):                                        # rectangles rushing out past you
            r = ((t * speed + k * 6) % 36)
            ring = np.abs(self.cheb - r) < 0.7
            col = BEARS_NAVY_LIT * 2.2 if k % 2 else BEARS_BRIGHT
            frame[ring] = np.minimum(255, np.array(col) * (0.3 + 0.7 * r / 36))
        frame += np.exp(-self.dist / 6)[..., None] * HG_WHITE * 0.25
        # The words rocketing in from far away, twice: the second time they punch past you
        cyc = t % 1.5
        if cyc < 0.9:
            s = gfx.lerp(0.15, 1.0, gfx.ease_in_quad(cyc / 0.9))
        else:
            s = 1.0 + (0.35 if t >= 1.5 else 0.12) * math.exp(-(cyc - 0.9) / 0.15)
        mm = gfx.scale_mask(self.cword, s)
        x = int(31.5 - mm.shape[1] / 2)
        y = int(15.5 - mm.shape[0] / 2)
        gfx.blit(frame, _grow(mm), x - 1, y - 1, (0, 0, 0))
        grad = np.zeros(mm.shape + (3,), np.float32)
        for r in range(mm.shape[0]):
            grad[r] = gfx.lerp(np.array(self.TOP, np.float32), np.array(self.BOTTOM, np.float32), r / max(1, mm.shape[0] - 1))
        gfx.blit(frame, mm, x, y, grad)
        if 0.9 <= cyc < 1.05:                                     # the punch-through
            frame += 255 * (1 - (cyc - 0.9) / 0.15) * 0.8
        return _shake(frame, 2.5 if 0.9 <= cyc < 1.1 else 0.5, t)

    # 6) the hero finish
    def _hero(self, t):
        frame = gfx.new_frame()
        frame += (np.array((20, 45, 160)) + np.exp(-self.dist / 14)[..., None] * np.array((40, 80, 200)))
        rays = np.clip(np.sin(self.ang * 9 + t * 1.3) * 3 - 1.8, 0, 1) * np.clip(1 - self.dist / 50, 0, 1)
        frame += rays[..., None] * np.array((255, 140, 30)) * 0.8
        for x, y, ph in self.sparkle:                             # sparkles twinkling
            tw = max(0.0, math.sin(t * 6 + ph * 6.28))
            frame[int(y), int(x)] += np.array((255, 240, 210)) * tw ** 4
        frame[_grow(self.dword, 1)] *= 0.25                       # a dark rim so the letters pop
        beat = (t % (TD_BEAT * 2)) / (TD_BEAT * 2)
        self._paint(frame, 0.9 + 0.2 * math.exp(-beat * 4))
        u = (t % 1.2) / 0.45
        if u <= 1:
            _shine(frame, self.dword, gfx.lerp(-12, W + 22, u))
        shake = 0.0
        if t > 3.0:                                               # the last, biggest hit
            frame += 255 * math.exp(-(t - 3.0) / 0.12)
            shake = 3.5 * math.exp(-(t - 3.0) / 0.12)
        if t > TD_SECTIONS[4] - 0.5:
            frame *= 1 - gfx.smoothstep((t - (TD_SECTIONS[4] - 0.5)) / 0.5)
        return _shake(frame, shake, t)

    def render(self, t):
        i = min(4, int(np.searchsorted(self.starts, t, side="right")) - 1)
        u = t - self.starts[i]
        frame = (self._speed, self._hammer, self._split, self._tunnel, self._hero)[i](u)
        if i > 0 and u < 0.1:                                     # every cut lands on a white-hot hit
            frame = frame + 255 * (1 - u / 0.1)
        return np.clip(frame, 0, 255)


class BearsFieldGoal(HomeRun):
    """A Bears field goal (simpler): the goalposts against the sky, the kick sailing through,
    the uprights flashing gold; then FIELD / GOAL! over orange and white fireworks."""

    TITLE_COLS = (BEARS_BRIGHT, BEARS_BRIGHT)        # both lines orange, white outline (owner)
    CLEAN_RING = True
    FW_COLS = (BEARS_ORANGE, WHITE, (70, 100, 230))
    KICK = 2.6

    def __init__(self, night=None):
        super().__init__("FIELD GOAL!", night)
        self.key = "song:bearsfg"
        self.fg_lines = (display_text("FIELD"), display_text("GOAL!"))   # custom display font (owner)
        self.fg_back = gfx.new_frame()
        for y in range(H):
            self.fg_back[y] = gfx.lerp(np.array((4, 8, 30.)), np.array((10, 18, 55.)), abs(y - 15.5) / 16)
        self.duration = self.KICK + HR_TITLE

    def _kick(self, t):
        frame = gfx.new_frame()
        frame[:] = _sky(H, self.night)
        frame[24:] = (24, 105, 40) if not self.night else (18, 80, 30)
        frame[24] = (40, 130, 55)
        u = t / 1.7
        hit = max(0.0, t - 1.7)
        flash = math.exp(-hit / 0.2) if t >= 1.7 else 0.0
        gold = np.array((255, 205, 40)) * (1 + 1.2 * flash)
        frame[3:15, 21] = frame[3:15, 42] = np.minimum(255, gold)          # the uprights
        frame[15, 21:43] = np.minimum(255, gold)                            # the crossbar
        frame[15:25, 31:33] = np.minimum(255, gold * 0.85)                 # the post
        if t < 1.9:                                                         # the kick, end over end
            bx, by = 31.5, gfx.lerp(31, 8, u) + 10 * u * (1 - u)
            r = gfx.lerp(3.0, 1.0, min(1.0, u))
            ell = ((_XX - bx) / r) ** 2 + ((_YY - by) / (r * (0.6 + 0.4 * abs(math.sin(t * 18))))) ** 2 <= 1
            frame[ell] = (150, 80, 35)
        if t >= 1.7:                                                        # it's good: a gold burst
            frame += np.exp(-np.hypot(_XX - 31.5, _YY - 9) / (3 + 25 * hit))[..., None] * np.array((255, 190, 60)) * flash
        if t > self.KICK - 0.25:
            frame *= 1 - gfx.smoothstep((t - (self.KICK - 0.25)) / 0.25)
        return frame

    def _title(self, t):
        """FIELD slams in from the left and GOAL! from the right in the display font: an impact
        flash, gold goalpost bars glowing at the edges, a gold shine, a few fireworks behind."""
        frame = self.fg_back.copy()                          # a soft navy backdrop
        for fw in self.fireworks[:8]:
            fw.draw(frame, t + 0.5, 0.45)
        beat = (t % 0.8) / 0.8                               # goalpost bars at the edges, pulsing gold
        gold = np.array((255, 200, 40)) * (0.55 + 0.45 * math.exp(-beat * 4))
        frame[:, 0:2] = frame[:, 62:64] = gold
        top, bottom = (255, 175, 70), (225, 70, 10)
        lines = ((self.fg_lines[0], 2, -1), (self.fg_lines[1], 17, 1))
        shake = 0.0
        for i, (m, y, side) in enumerate(lines):
            x_home = (W - m.shape[1]) // 2
            at = 0.0 if i == 0 else 0.18
            u = gfx.clamp01((t - at) / 0.32)
            x = x_home + side * (W + 4) * (1 - gfx.ease_out_back(u, 1.4))
            _styled(frame, m, int(round(x)), y, top, bottom)
        hit = t - 0.5                                        # they lock in: a white-hot impact
        if hit >= 0:
            frame += 255 * 0.85 * math.exp(-hit / 0.07)
            shake = 3.2 * math.exp(-hit / 0.1)
        u = ((t - 0.9) % 1.8) / 0.55                         # a gold shine sweeping across
        if t > 0.9 and u <= 1:
            lit = np.zeros((H, W), bool)
            for m, y, _ in lines:
                x = (W - m.shape[1]) // 2
                lit[y:y + 13, x:x + m.shape[1]] |= m
            _shine(frame, lit, gfx.lerp(-12, W + 22, u))
        if t > HR_TITLE - 0.5:
            frame *= 1 - gfx.smoothstep((t - (HR_TITLE - 0.5)) / 0.5)
        return _shake(frame, shake, t)

    def render(self, t):
        if t < self.KICK:
            return np.clip(self._kick(t), 0, 255)
        return np.clip(self._title(t - self.KICK), 0, 255)


# ------------------------------------------------------------------- display font
# A custom heavy display face for big titles (owner, 2026-10-02: the stock pixel font looked
# poor). 13 LEDs tall, 3-LED strokes, squared shapes with clipped corners and open counters
# so letters stay crisp on the panel. Add letters as needed.
DISPLAY_FONT = {
    "F": ["#########", "#########", "#########", "###......", "###......", "#######..", "#######..",
          "#######..", "###......", "###......", "###......", "###......", "###......"],
    "I": ["###"] * 13,
    "E": ["#########", "#########", "#########", "###......", "###......", "#######..", "#######..",
          "#######..", "###......", "###......", "#########", "#########", "#########"],
    "L": ["###......"] * 10 + ["#########"] * 3,
    "D": ["########..", "#########.", "##########", "###...####", "###....###", "###....###", "###....###",
          "###....###", "###....###", "###...####", "##########", "#########.", "########.."],
    "G": ["..#######.", ".#########", "##########", "###....###", "###.......", "###.......", "###..#####",
          "###..#####", "###..#####", "###....###", "##########", ".#########", "..#######."],
    "O": ["..######..", ".########.", "##########", "###....###", "###....###", "###....###", "###....###",
          "###....###", "###....###", "###....###", "##########", ".########.", "..######.."],
    "A": ["..######..", ".########.", "##########", "###....###", "###....###", "###....###", "##########",
          "##########", "##########", "###....###", "###....###", "###....###", "###....###"],
    "!": ["###"] * 8 + ["..."] * 2 + ["###"] * 3,
    "T": ["#########"] * 3 + ["...###..."] * 10,
    "U": ["###....###"] * 10 + ["##########", ".########.", "..######.."],
    "C": ["..#######", ".########", "#########"] + ["###......"] * 7 + ["#########", ".########", "..#######"],
    "H": ["###....###"] * 5 + ["##########"] * 3 + ["###....###"] * 5,
    "W": ["###......###"] * 7 + ["###..##..###", "###.####.###", "############", "############",
                                "####....####", "###......###"],
    "N": ["####...###", "#####..###", "######.###", "##########", "###.######", "###..#####", "###...####"]
         + ["###....###"] * 6,
    "P": ["########..", "#########.", "##########"] + ["###....###"] * 3 + ["##########", "#########.",
                                                                         "########.."] + ["###......."] * 4,
    "K": ["###....###", "###...###.", "###..###..", "###.###...", "######....", "#####.....", "#####.....",
          "######....", "###.###...", "###..###..", "###...###.", "###....###", "###....###"],
    "S": [".#########", "##########", "###.......", "###.......", "###.......", "##########", "##########",
          ".......###", ".......###", ".......###", ".......###", "##########", "#########."],
    "X": ["###....###", "###....###", ".###..###.", "..######..", "...####...", "...####...", "...####...",
          "..######..", ".###..###.", "###....###", "###....###", "###....###", "###....###"],
    "M": ["####....####", "#####..#####", "############", "###.####.###", "###..##..###"] + ["###......###"] * 8,
    "B": ["########..", "#########.", "##########", "###....###", "###....###", "#########.", "#########.",
          "###....###", "###....###", "###....###", "##########", "#########.", "########.."],
    " ": ["...."] * 13,
}


def display_text(text_, gap=2):
    """A line in the display font as a mask (13 tall)."""
    cols = []
    for i, ch in enumerate(text_.upper()):
        rows = DISPLAY_FONT[ch]
        g = np.array([[c == "#" for c in r] for r in rows], bool)
        if i:
            cols.append(np.zeros((13, gap), bool))
        cols.append(g)
    return np.concatenate(cols, axis=1)


_GRADS = {}


def _styled(frame, m, x, y, top, bottom, light=1.0):
    """Paint display lettering: a navy drop shadow, a top-to-bottom gradient fill and a bright
    bevel along every top edge. No outline, so neighboring letters never run together."""
    h = m.shape[0]
    gfx.blit(frame, m, x + 1, y + 1, np.array((10, 18, 60)))              # drop shadow
    key = (h, tuple(top), tuple(bottom))
    if key not in _GRADS:                                                  # the gradient, built once
        _GRADS[key] = np.stack([gfx.lerp(np.array(top, np.float32), np.array(bottom, np.float32), r / (h - 1))
                                for r in range(h)])
    grad = np.broadcast_to(_GRADS[key][:, None, :], m.shape + (3,))
    gfx.blit(frame, m, x, y, grad * light)
    bevel = m & ~np.roll(m, 1, 0)
    bevel[0] = m[0]
    gfx.blit(frame, bevel, x, y, np.minimum(255, np.array((255, 235, 200)) * light))
