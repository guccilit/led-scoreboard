"""Yahoo fantasy football (owner, 2026-10-03), in the same matchup shape as fantasy.py.

Yahoo needs OAuth: the owner registers one free Yahoo developer app (client id + secret,
saved once on the settings page), then you sign in to Yahoo from the settings page and
pastes the code Yahoo shows him. The board keeps the refresh token in secrets.json (root-only,
never sent back to the page) and renews the hour-long access token by itself.

Yahoo's API speaks XML (fantasysports.yahooapis.com/fantasy/v2); namespaces are stripped.
"""
import logging
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlencode

import requests

from . import fantasy

log = logging.getLogger(__name__)

API = "https://fantasysports.yahooapis.com/fantasy/v2/"
AUTH = "https://api.login.yahoo.com/oauth2/request_auth"
TOKEN = "https://api.login.yahoo.com/oauth2/get_token"
REDIRECT = "oob"                         # Yahoo shows the code on screen instead of redirecting
BENCH = ("BN", "IR", "IR+", "NA")

_session = requests.Session()
_session.headers["User-Agent"] = "ballin-board (personal scoreboard)"


class NotConnected(Exception):
    """No Yahoo app codes, or you haven't signed in (or Yahoo took the permission back)."""


def _cfg():
    return dict(fantasy.secrets().get("yahoo") or {})


def has_app():
    c = _cfg()
    return bool(c.get("client_id") and c.get("client_secret"))


def connected():
    return has_app() and bool(_cfg().get("refresh_token"))


def save_app(client_id, client_secret):
    c = _cfg()
    if (c.get("client_id"), c.get("client_secret")) != (client_id, client_secret):
        c = {}                           # a different app: the old sign-in no longer applies
    c.update(client_id=client_id, client_secret=client_secret)
    fantasy.save_secret("yahoo", c)


def disconnect():
    c = _cfg()
    for k in ("refresh_token", "access_token", "expires_at"):
        c.pop(k, None)
    fantasy.save_secret("yahoo", c)


def auth_url():
    return AUTH + "?" + urlencode({"client_id": _cfg().get("client_id", ""), "redirect_uri": REDIRECT,
                                   "response_type": "code", "scope": "fspt-r"})   # fantasy sports, read


def _token_call(data):
    c = _cfg()
    r = _session.post(TOKEN, data=dict(data, redirect_uri=REDIRECT),
                      auth=(c.get("client_id", ""), c.get("client_secret", "")), timeout=20)
    if r.status_code in (400, 401):
        raise NotConnected(r.text[:200])
    r.raise_for_status()
    t = r.json()
    c.update(access_token=t["access_token"], refresh_token=t.get("refresh_token") or c.get("refresh_token"),
             expires_at=time.time() + float(t.get("expires_in") or 3600))
    fantasy.save_secret("yahoo", c)
    return c["access_token"]


def connect(code):
    """Trade the code Yahoo showed you for tokens."""
    if not has_app():
        raise NotConnected("no Yahoo app codes saved")
    return _token_call({"grant_type": "authorization_code", "code": code.strip()})


def _token(force=False):
    c = _cfg()
    if not c.get("refresh_token") or not has_app():
        raise NotConnected("not signed in to Yahoo")
    if not force and c.get("access_token") and c.get("expires_at", 0) - 120 > time.time():
        return c["access_token"]
    return _token_call({"grant_type": "refresh_token", "refresh_token": c["refresh_token"]})


def _xml(text):
    root = ET.fromstring(text)
    for el in root.iter():
        if "}" in el.tag:
            el.tag = el.tag.split("}", 1)[1]
    return root


def _get(path):
    for force in (False, True):
        r = _session.get(API + path, headers={"Authorization": "Bearer " + _token(force)}, timeout=20)
        if r.status_code != 401:
            break
    if r.status_code == 401:
        raise NotConnected("Yahoo refused the sign-in")
    if r.status_code == 403:
        # Seen 2026-10-03: the Yahoo app was created without the Fantasy Sports permission
        raise NotConnected("the Yahoo app is missing the Fantasy Sports (Read) permission: add it at "
                           "developer.yahoo.com/apps, then sign in again")
    r.raise_for_status()
    return _xml(r.content)


def _num(el, path):
    try:
        return round(float(el.findtext(path) or 0.0), 2)
    except ValueError:
        return 0.0


def teams():
    """His NFL fantasy teams this season: [{"team_key", "team", "league_key", "league", "week", "finished"}]."""
    leagues = {}
    for lg in _get("users;use_login=1/games;game_keys=nfl/leagues").iter("league"):
        leagues[lg.findtext("league_key")] = {
            "league": (lg.findtext("name") or "League").upper(),
            "week": int(lg.findtext("current_week") or 1),
            "finished": lg.findtext("is_finished") == "1"}
    out = []
    for t in _get("users;use_login=1/games;game_keys=nfl/teams").iter("team"):
        key = t.findtext("team_key") or ""
        lk = key.split(".t.")[0]
        if lk in leagues:
            out.append(dict(leagues[lk], team_key=key, team=(t.findtext("name") or "TEAM").upper(), league_key=lk))
    return out


def _player_name(p):
    if p.findtext("display_position") == "DEF" or p.findtext("position_type") == "DT":
        return "%s D/ST" % (p.findtext("editorial_team_abbr") or "").upper()
    return fantasy._short(p.findtext("name/full"))


def _starters(team_key, week, names):
    root = _get("team/%s/roster;week=%d/players/stats;type=week;week=%d" % (team_key, week, week))
    out = {}
    for p in root.iter("player"):
        if p.findtext("selected_position/position") in BENCH:
            continue
        pid = "yahoo:%s" % p.findtext("player_key")
        out[pid] = _num(p, "player_points/total")
        names[pid] = _player_name(p)
    return out


def matchup(t, week):
    """His matchup this week in one Yahoo league (fantasy.py's dict, plus "names")."""
    root = _get("team/%s/matchups;weeks=%d" % (t["team_key"], week))
    m = root.find(".//matchup")
    if m is None:
        return None
    names = {}
    sides = {}
    for team in m.findall("teams/team"):
        key = team.findtext("team_key")
        name = (team.findtext("name") or "TEAM").upper()
        sides[key] = {"name": name, "abbr": fantasy.initials(name), "logo_url": team.findtext("team_logos/team_logo/url"),
                      "roster_id": "yahoo:%s" % key,
                      "points": _num(team, "team_points/total"),
                      "proj": _num(team, "team_projected_points/total") or None, "starters": _starters(key, week, names)}
    me = sides.pop(t["team_key"], None)
    if not me:
        return None
    opp = next(iter(sides.values()), None)
    winner = m.findtext("winner_team_key")
    return {"platform": "yahoo", "league_id": "yahoo:%s" % t["league_key"], "league": t["league"], "week": week,
            "me": me, "opp": opp, "names": names,
            "winner": (winner == t["team_key"]) if winner and m.findtext("status") == "postevent" else None}


def champion(t):
    """His team finished first in a completed Yahoo league."""
    if not t["finished"]:
        return False
    for team in _get("league/%s/standings" % t["league_key"]).iter("team"):
        if team.findtext("team_key") == t["team_key"]:
            return team.findtext("team_standings/rank") == "1"
    return False
