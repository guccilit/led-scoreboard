"""Startup animation: "Jackson's Ballin' Board", with a 40th birthday finale.

Splash(...).render(t) returns a uint8 (32, 64, 3) frame for t seconds since the
start. Call it with increasing t (it keeps a motion-trail buffer).

Storyboard:
  1. Parade: a baseball, a football and a hockey puck cross the screen.
  2. A basketball drops in, bounces and bursts into sparks.
  3. The sparks fly into place to build the logo; flash, shine, marquee lights.
  4. (birthday=True) logo crumbles, fireworks, HAPPY 40 BIRTHDAY,
     then JACKSON! with confetti.

This is Jackson's unit's startup screen (plays on every power-up). Other units
get their own entry in SPLASHES, chosen by the "splash" setting.
"""
import math

import numpy as np

from . import fonts, gfx
from .gfx import H, W

WHITE = (255, 255, 255)
GOLD = (255, 185, 20)
RED = (235, 20, 35)
BLUE = (40, 100, 255)
CYAN = (40, 220, 255)
GREEN = (60, 255, 90)
MAGENTA = (255, 50, 200)

# ---------------------------------------------------------------- sprites
BASEBALL = gfx.sprite([
    "..www..",
    ".rwwwr.",
    "wrwwwrw",
    "wwrwrww",
    "wrwwwrw",
    ".rwwwr.",
    "..www..",
], {"w": (230, 230, 225), "r": (235, 25, 25)})

_FB = {"b": (150, 60, 10), "w": (240, 240, 240), "d": (90, 35, 5)}
FOOTBALL_A = gfx.sprite([
    "...bbbbb...",
    ".bbbbbbbbb.",
    "bwbwwwwwbwb",
    "bwbbwbwbbwb",
    ".bbbbbbbbb.",
    "...bbbbb...",
], _FB)
FOOTBALL_B = gfx.sprite([
    "...ddddd...",
    ".bbbbbbbbb.",
    "bwbbbbbbbwb",
    "bwbbbbbbbwb",
    ".bbbbbbbbb.",
    "...ddddd...",
], _FB)

PUCK = gfx.sprite([
    ".ggggggg.",
    "GGGGGGGGG",
    "ddddddddd",
    ".ddddddd.",
], {"g": (95, 95, 115), "G": (170, 170, 190), "d": (45, 45, 60)})

BASKETBALL = gfx.sprite([
    "...ooo...",
    ".oooOooo.",
    ".OooOooO.",
    "ooOoOoOoo",
    "OOOOOOOOO",
    "ooOoOoOoo",
    ".OooOooO.",
    ".oooOooo.",
    "...ooo...",
], {"o": (255, 110, 0), "O": (70, 20, 0)})

# ---------------------------------------------------------------- timeline
BALL_DROP = 2.25          # basketball enters
BURST = BALL_DROP + 1.2   # basketball bursts at screen centre
FLASH = BURST + 1.5       # logo fully assembled
LOGO_END = FLASH + 3.4    # end of logo hold
DISSOLVE = 0.8
BDAY_START = LOGO_END + DISSOLVE
BDAY_LEN = 10.6

_YY, _XX = np.mgrid[0:H, 0:W].astype(np.float32)


def _scale_img(img, sx, sy):
    h, w = img.shape[:2]
    oh, ow = max(1, round(h * sy)), max(1, round(w * sx))
    ri = np.minimum((np.arange(oh) / sy).astype(int), h - 1)
    ci = np.minimum((np.arange(ow) / sx).astype(int), w - 1)
    return img[ri][:, ci]


def _draw_sprite_centered(frame, img, cx, cy):
    h, w = img.shape[:2]
    gfx.blit_rgb(frame, img, cx - w / 2, cy - h / 2)


def _shine(frame, lit, pos, width=2.2, amount=0.85):
    """Diagonal white highlight band crossing lit pixels."""
    d = (_XX + 0.6 * _YY) - pos
    b = np.exp(-(d / width) ** 2) * amount
    b = np.where(lit, b, 0)[..., None]
    frame[:] = frame * (1 - b) + 255 * b


def _perimeter():
    pts = [(x, 0) for x in range(W)] + [(W - 1, y) for y in range(1, H)]
    pts += [(x, H - 1) for x in range(W - 2, -1, -1)] + [(0, y) for y in range(H - 2, 0, -1)]
    return np.array(pts)


def _gradient(h, w, top, bottom):
    t = np.linspace(0, 1, h, dtype=np.float32)[:, None, None]
    col = np.asarray(top, np.float32) * (1 - t) + np.asarray(bottom, np.float32) * t
    return np.broadcast_to(col, (h, w, 3)).copy()


def _build_logo(ballin_colors=None, shadow=(110, 30, 0)):
    """JACKSON'S / BALLIN' / BOARD. ballin_colors(h, w) -> colors for BALLIN' (default:
    yellow-to-orange gradient)."""
    jackson, _ = fonts.text_mask("JACKSON'S", "5x7")
    ballin, _ = fonts.text_mask("BALLIN'", "7x13B")
    board, _ = fonts.text_mask("BOARD", "6x10")
    gap = 2
    heights = [jackson.shape[0], ballin.shape[0] + 1, board.shape[0]]
    y = (H - sum(heights) - 2 * gap + 1) // 2
    img = gfx.new_frame()

    x = (W - jackson.shape[1]) // 2
    gfx.blit(img, jackson, x, y, (225, 230, 255))
    y += heights[0] + gap

    x = (W - ballin.shape[1]) // 2
    gfx.blit(img, ballin, x + 1, y + 1, shadow)
    colors = ballin_colors(*ballin.shape) if ballin_colors else _gradient(*ballin.shape, (255, 230, 60), (255, 70, 0))
    gfx.blit(img, ballin, x, y, colors)
    ballin_rows = (y, y + heights[1])
    y += heights[1] + gap

    x = (W - board.shape[1]) // 2
    gfx.blit(img, board, x, y, BLUE)
    mid = y + board.shape[0] // 2
    img[mid, 5:x - 3] = BLUE
    img[mid, x + board.shape[1] + 3:W - 5] = BLUE

    ballin_lit = np.zeros((H, W), bool)
    ballin_lit[ballin_rows[0]:ballin_rows[1]] = img[ballin_rows[0]:ballin_rows[1]].max(-1) > 0
    return img, ballin_lit


def _typed(mask, edges, base, t0, interval, s):
    """Typewriter reveal: letters appear one by one, flashing white as they land."""
    h, w = mask.shape
    char = np.searchsorted(np.asarray(edges), np.arange(w), side="right")
    age = s - (t0 + char * interval)
    visible = mask & (age >= 0)[None, :]
    whiten = gfx.clamp01(1 - age / 0.18)[None, :, None]
    col = np.asarray(base, np.float32)
    if col.ndim == 1:
        col = np.broadcast_to(col, (h, w, 3))
    col = col * (1 - whiten) + 255 * whiten
    return visible, col


class Firework:
    RISE = 0.55
    LIFE = 1.4

    def __init__(self, rng, t0, x, y, color, n=34):
        self.t0, self.x, self.y = t0, x, y
        self.color = np.asarray(color, np.float32)
        ang = np.linspace(0, 2 * math.pi, n, endpoint=False) + rng.uniform(0, 0.25, n)
        spd = rng.uniform(12, 19, n)
        self.vx, self.vy = np.cos(ang) * spd, np.sin(ang) * spd
        self.tw = rng.uniform(0, 2 * math.pi, n)

    def draw(self, frame, s, gain=1.0):
        tau = s - self.t0
        if tau < 0:
            return
        if tau < self.RISE:
            y = gfx.lerp(H + 1, self.y, gfx.ease_out_quad(tau / self.RISE))
            k = np.arange(4)
            cols = np.outer(1 - k / 4, (255, 210, 150))
            gfx.splat(frame, np.full(4, self.x), y + k * 1.3, cols * gain)
            return
        tb = tau - self.RISE
        if tb > self.LIFE:
            return
        d = (1 - math.exp(-2.4 * tb)) / 2.4
        px = self.x + self.vx * d
        py = self.y + self.vy * d + 6 * tb * tb
        fade = (1 - tb / self.LIFE) ** 1.6
        twinkle = 0.75 + 0.25 * np.sin(self.tw + tb * 30) if tb > 0.5 else 1.0
        hot = max(0.0, 1 - tb / 0.15)
        col = self.color * (1 - hot) + 255 * hot
        gfx.splat(frame, px, py, np.outer(fade * twinkle * np.ones_like(px), col) * gain)


class Splash:
    def __init__(self, birthday=True, seed=40):
        self.birthday = birthday
        self.duration = BDAY_START + BDAY_LEN if self.birthday else LOGO_END
        rng = np.random.default_rng(seed)

        # Logo and the sparks that build it
        self.logo, self.ballin_lit = _build_logo()
        self.logo_lit = self.logo.max(-1) > 0
        ty, tx = np.nonzero(self.logo_lit)
        self.ty, self.tx = ty.astype(np.float32), tx.astype(np.float32)
        self.tcol = self.logo[ty, tx]
        n = len(tx)
        ang = rng.uniform(0, 2 * math.pi, n)
        spd = rng.uniform(15, 60, n)
        self.pvx, self.pvy = np.cos(ang) * spd, np.sin(ang) * spd
        self.pdelay = 0.2 + 0.6 * (self.tx / W) + rng.uniform(0, 0.12, n)
        sparks = np.array([GOLD, (255, 120, 0), WHITE, (255, 220, 120)], np.float32)
        self.pcol = sparks[rng.integers(0, len(sparks), n)]
        self.fall_delay = rng.uniform(0, 0.35, n)
        self.perim = _perimeter()

        # Birthday
        if self.birthday:
            self._build_birthday(rng)

        self.prev = gfx.new_frame()
        self.last_t = None

    # ------------------------------------------------------------ birthday setup
    def _build_birthday(self, rng):
        self.happy = fonts.text_mask("HAPPY", "5x7")
        self.forty = fonts.text_mask("40", "9x18B")[0]
        self.bday = fonts.text_mask("BIRTHDAY", "5x7")
        hs = [self.happy[0].shape[0], self.forty.shape[0], self.bday[0].shape[0]]
        y = (H - sum(hs) - 4 + 1) // 2
        self.happy_pos = ((W - self.happy[0].shape[1]) // 2, y)
        y += hs[0] + 2
        self.forty_pos = (W / 2, y + hs[1] / 2)
        y += hs[1] + 2
        self.bday_pos = ((W - self.bday[0].shape[1]) // 2, y)

        self.jack = fonts.text_mask("JACKSON!", "7x13B")[0]
        self.cheers = fonts.text_mask("CHEERS TO 40!", "4x6")
        total = self.jack.shape[0] + 3 + self.cheers[0].shape[0]
        y = (H - total + 1) // 2
        self.jack_pos = ((W - self.jack.shape[1]) // 2, y)
        self.cheers_pos = ((W - self.cheers[0].shape[1]) // 2, y + self.jack.shape[0] + 3)
        self.jack_col = _gradient(*self.jack.shape, (255, 240, 120), (255, 120, 0))

        shows = [(0.0, 14, 9, RED), (0.3, 49, 7, GOLD), (0.9, 31, 5, (90, 150, 255)),
                 (2.0, 8, 8, GREEN), (2.6, 56, 10, MAGENTA), (3.3, 22, 6, CYAN),
                 (4.1, 44, 7, GOLD), (4.8, 12, 9, (255, 200, 255)), (5.6, 32, 7, RED),
                 (6.3, 9, 6, BLUE), (6.9, 54, 8, GOLD), (7.6, 30, 5, GREEN),
                 (8.3, 50, 6, MAGENTA), (8.9, 14, 7, CYAN), (9.4, 40, 6, GOLD)]
        self.fireworks = [Firework(rng, *f) for f in shows]

        n = 70
        self.cf_x = rng.uniform(0, W, n)
        self.cf_y0 = rng.uniform(-40, -2, n)
        self.cf_spd = rng.uniform(7, 14, n)
        self.cf_amp = rng.uniform(0.5, 1.6, n)
        self.cf_frq = rng.uniform(2, 4, n)
        self.cf_ph = rng.uniform(0, 2 * math.pi, n)
        pal = np.array([RED, GOLD, BLUE, GREEN, MAGENTA, CYAN, WHITE], np.float32)
        self.cf_col = pal[rng.integers(0, len(pal), n)]

    # ------------------------------------------------------------ rendering
    def render(self, t):
        dt = 1 / 40 if self.last_t is None else max(1e-3, t - self.last_t)
        self.last_t = t
        frame = gfx.new_frame()
        trail = 0.0
        if t < BURST:
            self._parade(frame, t)
            self._basketball(frame, t)
            trail = 0.35
        elif t < FLASH:
            self._sparks(frame, t - BURST)
            trail = 0.55
        elif t < LOGO_END:
            self._logo(frame, t - FLASH)
        elif self.birthday and t < BDAY_START:
            self._crumble(frame, t - LOGO_END)
            trail = 0.4
        elif self.birthday:
            self._birthday(frame, t - BDAY_START)
            trail = 0.4
        else:
            self._logo(frame, t - FLASH)

        if trail:
            decay = trail ** (dt * 40)
            frame = np.maximum(frame, self.prev * decay)
        self.prev = frame
        return gfx.to_uint8(frame)

    def _parade(self, frame, t):
        u = (t - 0.0) / 1.3
        if 0 <= u <= 1:
            x = gfx.lerp(-6, W + 6, u)
            y = 27.5 - 18 * abs(math.cos(math.pi * 2.2 * u))
            _draw_sprite_centered(frame, BASEBALL, x, y)
        u = (t - 0.45) / 1.35
        if 0 <= u <= 1:
            x = gfx.lerp(-8, W + 8, u)
            y = 24 - 15 * math.sin(math.pi * u)
            fb = FOOTBALL_A if int(t * 14) % 2 == 0 else FOOTBALL_B
            _draw_sprite_centered(frame, fb, x, y)
        u = (t - 0.95) / 0.95
        if 0 <= u <= 1:
            x = gfx.lerp(-8, W + 8, u)
            k = np.arange(1, 12)
            sx = x - 4 - k * 1.4
            sy = 29.5 + np.sin(k * 1.7 + t * 40) * 0.7 - k * 0.12
            cols = np.outer((1 - k / 12) ** 1.5, (160, 210, 255))
            gfx.splat(frame, sx, sy, cols)
            _draw_sprite_centered(frame, PUCK, x, 29)

    def _basketball(self, frame, t):
        s = t - BALL_DROP
        if s < 0:
            return
        floor, top = 26.0, -6.0
        keys = [(0.0, 0.45, top, floor, gfx.ease_in_quad),
                (0.45, 0.75, floor, 10.0, gfx.ease_out_quad),
                (0.75, 1.0, 10.0, floor, gfx.ease_in_quad),
                (1.0, 1.2, floor, 16.0, gfx.ease_out_quad)]
        cy = 16.0
        for a, b, y0, y1, ease in keys:
            if a <= s < b:
                cy = gfx.lerp(y0, y1, ease((s - a) / (b - a)))
                break
        img = np.rot90(BASKETBALL, int(s * 10) % 4)
        for impact in (0.45, 1.0):
            if abs(s - impact) < 0.05:
                img = _scale_img(img, 1.3, 0.7)
                cy = floor + 1.3
        _draw_sprite_centered(frame, img, W / 2, cy)

    def _sparks(self, frame, s):
        cx, cy = W / 2, 16.0
        # Burst ring and flash
        if s < 0.35:
            r = 3 + 45 * s
            a = np.linspace(0, 2 * math.pi, 90, endpoint=False)
            fade = (1 - s / 0.35) ** 2
            gfx.splat(frame, cx + np.cos(a) * r, cy + np.sin(a) * r * 0.8,
                      np.outer(np.full(90, fade), (255, 200, 120)))
        if s < 0.12:
            frame += 255 * (1 - s / 0.12) * np.exp(-((_XX - cx) ** 2 + (_YY - cy) ** 2) / 30)[..., None]
        k = 3.0
        d = (1 - math.exp(-k * s)) / k
        ox, oy = cx + self.pvx * d, cy + self.pvy * d
        w = gfx.smoothstep((s - self.pdelay) / 0.5)
        px = gfx.lerp(ox, self.tx, w)
        py = gfx.lerp(oy, self.ty, w)
        tw = 0.7 + 0.3 * np.sin(self.tx * 7 + self.ty * 3 + s * 40)
        col = self.pcol * (tw * (1 - w))[:, None] + self.tcol * w[:, None]
        done = w >= 1
        frame[self.ty[done].astype(int), self.tx[done].astype(int)] = self.tcol[done]
        gfx.splat(frame, px[~done], py[~done], col[~done])

    def _marquee(self, frame, s, fade=1.0):
        n = len(self.perim)
        shown = int(np.clip((s - 0.1) * 450, 0, n))
        if not shown:
            return
        idx = np.arange(shown)
        lit = (idx + int(s * 12)) % 3 == 0
        cols = np.where(lit[:, None], np.array(GOLD, np.float32), np.array((40, 18, 0), np.float32))
        pts = self.perim[:shown]
        frame[pts[:, 1], pts[:, 0]] = cols * fade

    def _logo(self, frame, s):
        frame += self.logo
        f = math.exp(-s / 0.15)
        if f > 0.01:
            frame[self.logo_lit] = frame[self.logo_lit] * (1 - f * 0.9) + 255 * f * 0.9
        for start in (0.5, 2.4):
            u = (s - start) / 0.8
            if 0 <= u <= 1:
                _shine(frame, self.ballin_lit, gfx.lerp(-12, W + 22, u))
        self._marquee(frame, s)

    def _crumble(self, frame, s):
        tau = np.maximum(0, s - self.fall_delay)
        py = self.ty + 0.5 * 110 * tau * tau
        gfx.splat(frame, self.tx + tau * np.sin(self.tx) * 6, py, self.tcol)
        self._marquee(frame, LOGO_END - FLASH + s, fade=max(0.0, 1 - s / 0.5))

    def _birthday(self, frame, s):
        gain = 1.0 if s < 0.9 else 0.55
        for fw in self.fireworks:
            fw.draw(frame, s, gain)

        if s < 6.0:
            lift = -34 * gfx.ease_in_quad((s - 5.4) / 0.4)
            hx, hy = self.happy_pos
            vis, col = _typed(*self.happy, (255, 255, 255), 0.9, 0.08, s)
            gfx.clear_behind(frame, vis, hx, hy + lift)
            gfx.blit(frame, vis, hx, hy + lift, col)

            if s >= 1.5:
                sc = max(0.05, gfx.ease_out_back((s - 1.5) / 0.45))
                m = gfx.scale_mask(self.forty, sc)
                fx = self.forty_pos[0] - m.shape[1] / 2
                fy = self.forty_pos[1] - m.shape[0] / 2 + lift
                hue = (np.arange(m.shape[1]) * 0.025 - s * 0.35)[None, :] + np.zeros((m.shape[0], 1))
                gfx.clear_behind(frame, m, fx, fy)
                gfx.blit(frame, m, fx, fy, gfx.hsv(hue, 0.9, 1.0))

            bx, by = self.bday_pos
            vis, col = _typed(*self.bday, GOLD, 2.1, 0.07, s)
            gfx.clear_behind(frame, vis, bx, by + lift)
            gfx.blit(frame, vis, bx, by + lift, col)

        if s >= 5.7:
            c = s - 5.7
            cy = (self.cf_y0 + self.cf_spd * c) % 40 - 4
            cx = self.cf_x + self.cf_amp * np.sin(self.cf_frq * c + self.cf_ph)
            flick = 0.45 + 0.55 * np.abs(np.sin(self.cf_frq * 2 * c + self.cf_ph))
            ok = (cy >= 0) & (cy < H) & (cx >= 0) & (cx < W)
            frame[cy[ok].astype(int), cx[ok].astype(int)] = self.cf_col[ok] * flick[ok, None]

            off = 32 * (1 - gfx.ease_out_back((s - 5.8) / 0.55)) if s < 6.35 else 0
            jx, jy = self.jack_pos
            gfx.clear_behind(frame, self.jack, jx, jy + off)
            gfx.blit(frame, self.jack, jx, jy + off, self.jack_col)
            for start in (7.0, 8.7):
                u = (s - start) / 0.8
                if 0 <= u <= 1:
                    lit = np.zeros((H, W), bool)
                    lit[jy:jy + self.jack.shape[0], jx:jx + self.jack.shape[1]] = self.jack
                    _shine(frame, lit, gfx.lerp(-12, W + 22, u))

            cx0, cy0 = self.cheers_pos
            vis, col = _typed(*self.cheers, CYAN, 6.5, 0.06, s)
            gfx.clear_behind(frame, vis, cx0, cy0)
            gfx.blit(frame, vis, cx0, cy0, col)

        if s > BDAY_LEN - 0.6:
            frame *= 1 - gfx.smoothstep((s - (BDAY_LEN - 0.6)) / 0.6)


# Startup screens by name; the unit's "splash" setting picks one.
def _show(**kw):
    from .splash2 import Show
    return Show(**kw)


SPLASHES = {
    # Cubs, Blackhawks, Bears, logo; 40th birthday finale through 2026-11-25 (splash2.py)
    "jackson40": lambda: _show(),
    "jackson": lambda: _show(birthday=False),
    # The first version (sports parade, basketball burst, rainbow birthday)
    "classic40": lambda: Splash(birthday=True),
    "classic": lambda: Splash(birthday=False),
}
DEFAULT_SPLASH = "jackson40"


def create(name=DEFAULT_SPLASH):
    return SPLASHES.get(name, SPLASHES[DEFAULT_SPLASH])()


def create_show(what, splash_name=DEFAULT_SPLASH):
    """'startup' -> the unit's startup show; 'cubs' / 'hawks' / 'bears' -> that team's intro;
    'gocubsgo' / 'beardown' -> the song played after a Cubs / Bears win."""
    if what in ("gocubsgo", "beardown", "hawkswin"):
        from .splash2 import WIN_SONGS
        return WIN_SONGS[what]()
    if what == "hawkschant":
        from .splash2 import HawksChant
        return HawksChant()
    if what in ("homerun", "homerun2", "homerun3"):
        from .splash2 import HomeRun
        return HomeRun({"homerun": "HOME RUN!", "homerun2": "2-RUN HOMER!", "homerun3": "3-RUN HOMER!"}[what])
    if what in ("bearstd", "bearsfg"):
        from . import splash2
        return splash2.BearsTouchdown() if what == "bearstd" else splash2.BearsFieldGoal()
    if what == "grandslam":
        from .splash2 import GrandSlam
        return GrandSlam()
    if what == "hawksgoal":
        from .splash2 import HawksGoal
        return HawksGoal()
    if what in ("cubs", "hawks", "bears"):
        from .splash2 import TeamIntro
        return TeamIntro(what)
    return create(splash_name)
