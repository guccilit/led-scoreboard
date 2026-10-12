"""Settings web page (http://scoreboard.local). Runs as its own service, separate from
the scoreboard, so the page can never freeze the display. The board picks up saved
settings within ~2 s (SettingsWatcher).

  python -m ballin.web [port]
"""
import datetime
import json
import logging
import os
import socket
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import logos, netsetup, settings as settings_mod
from .leagues import LEAGUES
from .settings import DATA_DIR

log = logging.getLogger("ballin.web")

PAGE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "assets", "web", "index.html")
_save_lock = threading.Lock()
NET = None   # netsetup.NetSetup, started in main()


def teams():
    out = {}
    for key, league in LEAGUES.items():
        try:
            with open(os.path.join(DATA_DIR, "teams_%s.json" % key)) as f:
                rows = json.load(f)["teams"]
        except Exception:
            rows = []
        out[key] = {"name": league.name, "teams": sorted(
            ({"id": t["id"], "abbr": t["abbr"], "name": "%s %s" % (t["location"], t["name"]),
              "logo": "/logo/%s/%s.png" % (key, t["id"])} for t in rows), key=lambda t: t["name"])}
    return out


def _run(cmd, timeout=20):
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def wifi_current():
    try:
        out = _run(["nmcli", "-t", "-f", "ACTIVE,SSID,SIGNAL", "dev", "wifi"], 10).stdout
        for line in out.splitlines():
            parts = line.split(":")
            if parts[0] == "yes":
                return {"ssid": parts[1], "signal": int(parts[2] or 0)}
    except Exception:
        pass
    return {"ssid": "", "signal": 0}


def wifi_scan():
    # While the setup hotspot runs the radio can't scan: use the list taken just before.
    if netsetup.read_mode().get("mode") == "setup":
        return netsetup.cached_scan()
    return netsetup.scan()


def wifi_connect(ssid, password):
    """Connect in the background: switching networks drops this very page's connection.
    The network is saved permanently; if it fails, the setup hotspot comes back."""
    def go():
        time.sleep(1.5)
        NET.connect(ssid, password)
    threading.Thread(target=go, daemon=True).start()


def status():
    net = netsetup.read_mode()
    info = {"host": socket.gethostname(), "wifi": wifi_current(), "net": net, "ip": netsetup._wlan_ip(),
            "saved": [n["ssid"] for n in netsetup.saved_networks()]}
    try:
        with open("/sys/class/thermal/thermal_zone0/temp") as f:
            info["temp_c"] = round(int(f.read()) / 1000)
    except OSError:
        pass
    try:
        with open("/proc/uptime") as f:
            info["uptime_h"] = round(float(f.read().split()[0]) / 3600, 1)
    except OSError:
        pass
    updated = {}
    for key in LEAGUES:
        p = os.path.join(DATA_DIR, "cache_%s.pkl" % key)
        if os.path.exists(p):
            updated[key] = int(time.time() - os.path.getmtime(p))
    info["data_age_s"] = updated
    try:
        with open(os.path.join(DATA_DIR, "unit_id")) as f:
            info["unit"] = f.read().strip()
    except OSError:
        pass
    return info


PHOTO_PAGE = os.path.join(os.path.dirname(PAGE), "photos.html")
PHOTO_ICON = os.path.join(os.path.dirname(PAGE), "photos-icon.png")




class Handler(BaseHTTPRequestHandler):
    server_version = "BallinBoard"

    def log_message(self, fmt, *args):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _body(self):
        n = min(int(self.headers.get("Content-Length") or 0), 100_000)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except ValueError:
            return {}

    def _photo_host(self):
        """The photo-only mode: the photo page and nothing else (owner, 2026-10-03: guests shouldn't get
        near the settings). Only on the photo door, port 8080 (PhotoHandler)."""
        return False

    def do_GET(self):
        try:
            if self._photo_host():
                if self.path in ("/", "/index.html"):
                    self.path = "/photos"
                if not (self.path.startswith("/photos") or self.path == "/api/photos"):
                    return self._send(404, {"error": "not found"})
            if self.path in ("/", "/index.html"):
                with open(PAGE, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if self.path == "/api/settings":
                return self._send(200, settings_mod.load())
            if self.path in ("/photos", "/photos/"):
                with open(PHOTO_PAGE, "rb") as f:
                    return self._send(200, f.read(), "text/html; charset=utf-8")
            if self.path == "/photos/manifest.json":
                return self._send(200, {"name": "Ballin' Board Photos", "short_name": "Board Photos",
                                        "start_url": "/photos", "display": "standalone",
                                        "background_color": "#0b0d14", "theme_color": "#0b0d14",
                                        "icons": [{"src": "/photos/icon.png", "sizes": "180x180", "type": "image/png"}]})
            if self.path == "/photos/icon.png":
                with open(PHOTO_ICON, "rb") as f:
                    return self._send(200, f.read(), "image/png")
            if self.path == "/api/photos":
                # What's on the board now: no pictures, just enough to take one off early
                from . import photos
                now = time.time()
                return self._send(200, {"active": [{"id": r["id"], "mode": r["mode"], "left_s": int(r["end"] - now)}
                                                   for r in photos.active(now)]})
            if self.path.startswith("/api/fantasy/sleeper"):
                # His Sleeper leagues this season, for the settings page to list
                from urllib.parse import parse_qs, urlparse
                from . import fantasy
                name = (parse_qs(urlparse(self.path).query).get("username") or [""])[0].strip()
                try:
                    u = fantasy.sleeper_user(name) if name else None
                    if not u:
                        return self._send(404, {"error": "No Sleeper user named \"%s\"" % name})
                    season = str(fantasy._get("state/nfl").get("league_season") or datetime.date.today().year)
                    return self._send(200, {"user": u.get("display_name") or name,
                                            "leagues": fantasy.sleeper_leagues(u["user_id"], season)})
                except Exception as exc:
                    return self._send(502, {"error": "Couldn't reach Sleeper (%s)" % exc})
            if self.path == "/api/lights/scan":
                from . import lights
                return self._send(200, {"found": lights.scan()})
            if self.path == "/api/fantasy/yahoo":
                # Where the Yahoo sign-in stands, and his teams once signed in (no codes or tokens)
                from . import fantasy_yahoo as yahoo
                out = {"app": yahoo.has_app(), "connected": yahoo.connected()}
                if out["app"]:
                    out["auth_url"] = yahoo.auth_url()
                if out["connected"]:
                    try:
                        out["teams"] = yahoo.teams()
                    except yahoo.NotConnected as exc:
                        out["connected"], out["error"] = False, "Yahoo: %s" % exc
                    except Exception as exc:
                        out["error"] = "Couldn't reach Yahoo (%s)" % exc
                return self._send(200, out)
            if self.path.startswith("/api/fantasy/espn"):
                # One ESPN league's name and teams, his own marked when his login codes own one.
                # The codes themselves never come back to the page: only whether they're saved.
                from urllib.parse import parse_qs, urlparse
                from . import fantasy
                lid = (parse_qs(urlparse(self.path).query).get("league") or [""])[0].strip()
                has_login = bool(fantasy.espn_cookies())
                if not lid:
                    return self._send(200, {"has_login": has_login})
                if not lid.isdigit():
                    return self._send(400, {"error": "The league ID is the number after leagueId= in the ESPN web address"})
                try:
                    d = fantasy.espn_league(lid, fantasy.espn_season())
                    return self._send(200, {"name": (d.get("settings") or {}).get("name") or "League",
                                            "teams": fantasy.espn_teams(d), "has_login": has_login})
                except fantasy.EspnPrivate:
                    return self._send(403, {"error": "This league is private: add your ESPN login codes below" if not has_login
                                            else "ESPN didn't accept your login codes (they may have expired): paste them again",
                                            "private": True, "has_login": has_login})
                except Exception as exc:
                    if "404" in str(exc):
                        return self._send(404, {"error": "No ESPN league with that ID this season"})
                    return self._send(502, {"error": "Couldn't reach ESPN (%s)" % exc})
            if self.path == "/api/teams":
                return self._send(200, teams())
            if self.path == "/api/status":
                return self._send(200, status())
            if self.path == "/api/wifi/scan":
                return self._send(200, wifi_scan())
            if self.path.startswith("/logo/"):
                # Team logos served by the board itself (the phone may have no internet,
                # e.g. on the setup hotspot): the same small logos the panel shows.
                parts = self.path.split("/")
                if len(parts) == 4 and parts[2] in LEAGUES and parts[3][:-4].isdigit() and parts[3].endswith(".png"):
                    p = logos.path(parts[2], parts[3][:-4])
                    if os.path.exists(p):
                        with open(p, "rb") as f:
                            return self._send(200, f.read(), "image/png")
                return self._send(404, {"error": "no logo"})
            if netsetup.read_mode().get("mode") == "setup":
                # Setup hotspot: phones probe a "connectivity check" page on joining; sending
                # them to the settings page makes it pop up like a hotel WiFi sign-in.
                self.send_response(302)
                self.send_header("Location", "http://%s/" % netsetup.AP_IP)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self._send(404, {"error": "not found"})
        except Exception as exc:
            log.exception("GET %s failed", self.path)
            self._send(500, {"error": str(exc)})

    def do_POST(self):
        try:
            if self._photo_host() and not self.path.startswith("/api/photos"):
                return self._send(404, {"error": "not found"})
            if self.path.startswith("/api/photos/") and "/original" in self.path:
                # The board shows only the 64x32 panel picture the phone made, so the full-size
                # upload is read and discarded.
                n = int(self.headers.get("Content-Length") or 0)
                while n > 0:
                    chunk = self.rfile.read(min(1 << 16, n))
                    if not chunk:
                        break
                    n -= len(chunk)
                return self._send(200, {"ok": True})
            body = self._body()
            if self.path == "/api/settings":
                with _save_lock:
                    new = settings_mod.clean(settings_mod.load(), body)
                    settings_mod.save(new)
                return self._send(200, new)
            if self.path == "/api/show":
                what = str(body.get("what", ""))
                if what not in ("startup", "cubs", "hawks", "bears", "gocubsgo", "beardown", "hawkschant", "hawksgoal", "hawkswin",
                                "homerun", "homerun2", "homerun3", "grandslam", "bearstd", "bearsfg"):
                    return self._send(400, {"error": "unknown show"})
                with open("/run/ballin-show.request", "w") as f:
                    f.write(what)
                return self._send(200, {"ok": True})
            if self.path == "/api/photos":
                import base64
                from . import photos
                png = base64.b64decode(str(body.get("panel", "")).split(",")[-1])
                pid = photos.add(png, body.get("mode"), body.get("minutes", 60), self.client_address[0])
                return self._send(200, {"id": pid})
            if self.path.startswith("/api/photos/") and self.path.endswith("/stop"):
                from . import photos
                photos.stop(self.path.split("/")[3])
                return self._send(200, {"ok": True})
            if self.path == "/api/lights/pair":
                from . import lights
                ok, msg = lights.pair(str(body.get("type", "")), str(body.get("ip", ""))[:40])
                return self._send(200 if ok else 400, {"ok": ok, "error": None if ok else msg})
            if self.path == "/api/lights/test":
                from . import lights
                devs = lights.options(settings_mod.load())["devices"]
                if not devs:
                    return self._send(400, {"error": "Pick at least one light first"})
                threading.Thread(target=lights.test_flash, args=(devs,), daemon=True).start()
                return self._send(200, {"ok": True})
            if self.path.startswith("/api/fantasy/yahoo/"):
                from . import fantasy_yahoo as yahoo
                what = self.path.rsplit("/", 1)[1]
                if what == "app":
                    cid, secret = str(body.get("client_id", "")).strip()[:200], str(body.get("client_secret", "")).strip()[:200]
                    if len(cid) < 20 or len(secret) < 20:
                        return self._send(400, {"error": "Those don't look like Yahoo app codes (Client ID and Client Secret are long)"})
                    yahoo.save_app(cid, secret)
                    return self._send(200, {"ok": True, "auth_url": yahoo.auth_url()})
                if what == "connect":
                    code = str(body.get("code", "")).strip()[:100]
                    if not code:
                        return self._send(400, {"error": "Paste the code Yahoo showed you"})
                    try:
                        yahoo.connect(code)
                    except yahoo.NotConnected:
                        return self._send(400, {"error": "Yahoo didn't accept that code (codes work once and expire in minutes): sign in again for a new one"})
                    return self._send(200, {"ok": True})
                if what == "disconnect":
                    yahoo.disconnect()
                    return self._send(200, {"ok": True})
            if self.path == "/api/fantasy/espn/login":
                from . import fantasy
                s2, swid = str(body.get("s2", "")).strip()[:600], str(body.get("swid", "")).strip()[:60]
                if body.get("clear"):
                    s2 = swid = ""
                elif len(s2) < 50 or len(swid.strip("{}")) < 30:
                    return self._send(400, {"error": "Those don't look like the ESPN codes: espn_s2 is very long, SWID looks like {XXXXXXXX-...}"})
                fantasy.save_espn_cookies(s2, swid)
                return self._send(200, {"ok": True, "has_login": bool(s2)})
            if self.path == "/api/wifi/connect":
                ssid = str(body.get("ssid", ""))[:64]
                if not ssid:
                    return self._send(400, {"error": "no network chosen"})
                wifi_connect(ssid, str(body.get("password", ""))[:128])
                return self._send(200, {"ok": True})
            self._send(404, {"error": "not found"})
        except Exception as exc:
            log.exception("POST %s failed", self.path)
            self._send(500, {"error": str(exc)})


PHOTO_PORT = 8080


class PhotoHandler(Handler):
    """Port 8080: always the photo-only mode, whatever address was typed."""

    def _photo_host(self):
        return True


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 80
    global NET
    NET = netsetup.NetSetup()
    NET.start()
    # The photo page's own door, http://BOARD-IP:8080: Android often skips .local lookups
    # (owner's Pixel, 2026-10-04), and this serves nothing but the photo page either.
    photo = ThreadingHTTPServer(("", PHOTO_PORT), PhotoHandler)
    photo.daemon_threads = True
    threading.Thread(target=photo.serve_forever, daemon=True, name="PhotoDoor").start()
    server = ThreadingHTTPServer(("", port), Handler)
    server.daemon_threads = True
    log.info("Settings page on port %d, photo page also on port %d", port, PHOTO_PORT)
    server.serve_forever()


if __name__ == "__main__":
    main()
