"""Shared state between background workers and the render loop.

Workers replace whole values (plain attribute assignment), so the render loop
can read them at any time without locks and never waits on a worker.
"""
import collections
import time


class State:
    def __init__(self, settings):
        self.settings = settings
        self.online = None          # None = not checked yet
        self.online_since = None
        self.started = time.monotonic()
        self.leagues = {}           # league key -> sports.LeagueData
        self.data_version = 0       # bumped whenever league data changes
        self.slides = []            # built by SlidesWorker, shown by the render loop
        self.workers = []           # so a settings change can wake them
        self.live_tracked = {}      # game id -> live.Tracked (snapshots of favorite teams' games)
        self.live_slides = []       # game views and recent finals
        self.live_alert = None      # event announcement on screen now
        self.rivals = {}            # league key -> [sports.LeagueData] for division rivals (role "rival")
        self.fresh = {}             # league key -> time.monotonic() of the last good refresh
        self.fantasy = []           # his fantasy matchups this week (fantasy.FantasyWorker)
        self.fantasy_alerts = collections.deque(maxlen=50)   # his starters' big plays, newest last
        self.light_events = collections.deque(maxlen=10)    # smart-light moments waiting to play
        self.photo_only = []        # "show only this" photos on the board now (photos.py)
        self.light_glow = None      # [r, g, b] while a favorite plays live (lights "glow" mode)
        self.fantasy_results = []   # last week's finished matchups (shown a day)
        self.fantasy_champs = {}    # league id -> {"league", "at"}: League Champion, shown a week
        self.sags = collections.deque(maxlen=50)   # times the supply dipped (PowerGuard)
        # Screen on/off: the schedule decides, unless the button overrode it (cleared at the
        # next scheduled change). screen_visible is what the render loop is actually doing.
        self.screen_override = None
        self.screen_visible = True
        self.hold_started = None    # button held: show the shutdown prompt
        self.shutting_down = 0      # time shutdown started (0 = not shutting down)
        self.safe_to_unplug = 0      # time power-off began (0 = not yet)
        self.sleep_msg_until = 0    # show "SLEEPING / PRESS TO WAKE" until this time
        self.flip_field = False     # NFL mini field orientation (button double-press)
        self.reset_started = None   # factory reset countdown running (button held at startup)
        self.reset_done_until = 0
        self.netmode = {"mode": "normal"}   # WiFi setup hotspot status (from netsetup)
        self.lan_ip = ""            # the board's address on the home network (for the settings card)
        self.card_since = None      # when the "how to reach settings" card started showing
        self.power_cap = 1.0        # brightness multiplier set by PowerGuard (under-voltage)
        self.show_request = None    # "startup" / "cubs" / "hawks" / "bears": play it now
        self.show_ready = None      # the built show, picked up by the render loop
