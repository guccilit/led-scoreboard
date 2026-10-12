#!/usr/bin/env python3
"""Synthetic live-game preview: fake in-progress snapshots for each league, run event
detection, and render the game views and announcements to a contact sheet.

  BALLIN_DATA_DIR=/tmp/bbdata python tools/preview_live.py OUT.png
"""
import copy
import os
import sys
import threading
import time

from PIL import Image, ImageDraw

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from ballin import gfx, live, live_slides, settings as settings_mod  # noqa: E402
from ballin.sports import LeagueWorker  # noqa: E402
from ballin.state import State  # noqa: E402
from tools.preview_splash import led_image  # noqa: E402


def snap(g, **changes):
    g = copy.deepcopy(g)
    sit = changes.pop("situation", None)
    for k, v in changes.items():
        if k in ("home_score", "away_score"):
            getattr(g, k.split("_")[0]).score = v
        else:
            setattr(g, k, v)
    if sit is not None:
        g.situation = sit
    return live.Snap(time.time(), g)


def main():
    st = State(settings_mod.load())
    for key in ("nhl", "mlb", "nfl"):
        LeagueWorker(st, threading.Event(), key).step()
    shots = []

    def add(label, slide, t=1.0):
        shots.append((label, gfx.to_uint8(slide.render(t))))

    nfl = st.leagues["nfl"][0]
    g = nfl.next
    bears, jets = g.home.team_id, g.away.team_id
    a = snap(g, state="in", period=2, clock="8:47", detail="8:47 - 2nd", home_score=10, away_score=7,
             situation={"shortDownDistanceText": "3rd & 7", "possession": bears, "isRedZone": False, "down": 3,
                        "distance": 7, "yardLine": 35, "lastPlay": {"id": "1", "type": {"text": "Rush"}}})
    add("NFL live", live_slides.game_slide(nfl, a.game))
    b = snap(a.game, home_score=16, clock="8:12", situation={
        "shortDownDistanceText": "", "possession": bears, "lastPlay": {
            "id": "2", "type": {"text": "Passing Touchdown"}, "text": "Caleb Williams 12 Yd pass to DJ Moore"}})
    for ev in live.detect("nfl", bears, a, b):
        print("NFL event:", ev.headline, ev.favorite, "|", ev.detail)
        add("NFL fav " + ev.headline, live_slides.event_slide(nfl, ev), 2.0)
    c = snap(b.game, away_score=10, situation={"lastPlay": {"id": "3", "type": {"text": "Field Goal Good"}}})
    for ev in live.detect("nfl", bears, b, c):
        print("NFL event:", ev.headline, ev.favorite)
        add("NFL opp " + ev.headline, live_slides.event_slide(nfl, ev), 2.0)
    pick = snap(a.game, situation=dict(a.game.situation, possession=jets, lastPlay={
        "id": "4", "type": {"text": "Pass Interception Return"},
        "text": "Caleb Williams pass intended for DJ Moore INTERCEPTED by Sauce Gardner at NYJ 20"}))
    for ev in live.detect("nfl", bears, a, pick):
        print("NFL event:", ev.headline, ev.team_id == jets, ev.kind)
        add("NFL opp " + ev.headline, live_slides.event_slide(nfl, ev), 2.0)
    six = snap(a.game, home_score=16, situation=dict(a.game.situation, possession=jets, lastPlay={
        "id": "5", "type": {"text": "Interception Return Touchdown"}, "text": "Montez Sweat 25 Yd Interception Return"}))
    six.game.home.score, a_jets = 16, None
    six2 = snap(a.game, situation=dict(a.game.situation, possession=bears, lastPlay={
        "id": "6", "type": {"text": "Interception Return Touchdown"}, "text": "Jaylon Johnson 40 Yd Interception Return"}))
    six2.game.home.score = 16
    a2 = snap(a.game, situation=dict(a.game.situation, possession=jets))
    for ev in live.detect("nfl", bears, a2, six2):
        print("NFL event:", ev.headline, ev.favorite)
        add("NFL fav " + ev.headline, live_slides.event_slide(nfl, ev), 3.0)
    rz = snap(a.game, situation=dict(a.game.situation, isRedZone=True, shortDownDistanceText="1st & Goal"))
    add("NFL red zone", live_slides.game_slide(nfl, rz.game))
    fin = snap(b.game, state="post", detail="Final", completed=True)
    fin.game.home.winner = True
    add("NFL final (win)", live_slides.game_slide(nfl, fin.game, final=True), 2.0)

    nhl = st.leagues["nhl"][0]
    g = nhl.next
    a = snap(g, state="in", period=2, clock="12:34", detail="12:34 - 2nd", home_score=1, away_score=1)
    add("NHL live", live_slides.game_slide(nhl, a.game))
    b = snap(a.game, away_score=2)
    for ev in live.detect("nhl", nhl.team.id, a, b):
        add("NHL " + ev.headline, live_slides.event_slide(nhl, ev), 2.0)
    add("NHL intermission", live_slides.game_slide(nhl, snap(a.game, detail="End of 2nd").game))

    mlb = st.leagues["mlb"][0]
    g = copy.deepcopy(mlb.last)
    cubs = mlb.team.id
    a = snap(g, state="in", completed=False, period=7, detail="Top 7th", home_score=3, away_score=2,
             situation={"balls": 2, "strikes": 1, "outs": 1, "onFirst": True, "onSecond": False,
                        "onThird": True, "lastPlay": {"id": "10", "type": {"text": "Strike Looking"}}})
    add("MLB live", live_slides.game_slide(mlb, a.game))
    b = snap(a.game, situation=dict(a.game.situation, balls=0, strikes=0, onSecond=True,
                                     lastPlay={"id": "11", "type": {"text": "Double"}, "team": {"id": cubs}}))
    b.game.away.score = 2
    for ev in live.detect("mlb", cubs, a, b):
        print("MLB event:", ev.headline, ev.kind, "caught up:", live.caught_up(ev, b.game))
        add("MLB " + ev.headline, live_slides.event_slide(mlb, ev), 2.0)
    stale = snap(a.game, situation=dict(a.game.situation, balls=0, strikes=0,
                                         lastPlay={"id": "12", "type": {"text": "Single"}, "team": {"id": cubs}}))
    for ev in live.detect("mlb", cubs, a, stale):
        print("MLB stale-bases hit caught up?", live.caught_up(ev, stale.game), "(expect False)")
    single = snap(a.game, situation=dict(a.game.situation, balls=0, strikes=0, onSecond=True, batter={
        "athlete": {"displayName": "Ian Happ"}}, lastPlay={"id": "20", "type": {"text": "Single"}, "team": {"id": cubs},
                                                           "text": "Seiya Suzuki singled to left."}))
    for ev in live.detect("mlb", cubs, a, single):
        print("MLB event:", ev.headline, "next:", ev.next_up)
        add("MLB " + ev.headline + " t=1", live_slides.event_slide(mlb, ev), 1.0)
        add("MLB " + ev.headline + " t=4", live_slides.event_slide(mlb, ev), 4.0)
    out = snap(a.game, situation=dict(a.game.situation, outs=2, balls=0, strikes=0, batter={
        "athlete": {"displayName": "Dansby Swanson"}}, lastPlay={
        "id": "21", "type": {"text": "Fly Out"}, "text": "Cody Bellinger flied out to center."}))
    for ev in live.detect("mlb", cubs, a, out):
        print("MLB event:", ev.headline, ev.kind, "caught up:", live.caught_up(ev, out.game))
        add("MLB " + ev.headline, live_slides.event_slide(mlb, ev), 2.0)
    k = snap(a.game, detail="Mid 7th", situation=dict(a.game.situation, outs=0, lastPlay={
        "id": "22", "type": {"text": "Strikeout Swinging"}, "text": "Nico Hoerner struck out swinging."}))
    k.game.situation["outs"] = 0
    a1 = snap(a.game, situation=dict(a.game.situation, outs=2))
    for ev in live.detect("mlb", cubs, a1, k):
        print("MLB event:", ev.headline)
        add("MLB " + ev.headline, live_slides.event_slide(mlb, ev), 2.0)
    dp = snap(a.game, situation=dict(a.game.situation, outs=3 - 0, lastPlay={
        "id": "23", "type": {"text": "Ground Into Double Play"}, "text": "Swanson grounded into double play."}))
    dp.game.situation["outs"] = 3
    for ev in live.detect("mlb", cubs, a, dp):
        print("MLB event:", ev.headline)
    hr = snap(a.game, away_score=5, situation=dict(a.game.situation, lastPlay={
        "id": "13", "type": {"text": "Home Run"}, "text": "Suzuki homered to left (402 feet)"}))
    for ev in live.detect("mlb", cubs, a, hr):
        print("MLB event:", ev.headline)
        add("MLB " + ev.headline, live_slides.event_slide(mlb, ev), 2.0)

    cols, tw, th = 3, 64 * 6, 32 * 6 + 14
    sheet = Image.new("RGB", (cols * (tw + 8), ((len(shots) + cols - 1) // cols) * (th + 8)), (40, 40, 40))
    draw = ImageDraw.Draw(sheet)
    for k, (label, frame) in enumerate(shots):
        x, y = (k % cols) * (tw + 8) + 4, (k // cols) * (th + 8) + 4
        draw.text((x, y), label, fill=(200, 200, 200))
        sheet.paste(led_image(frame), (x, y + 14))
    sheet.save(sys.argv[1])
    print(sys.argv[1])


if __name__ == "__main__":
    main()
