"""Smart lights around the TV (owner, 2026-10-03): flash team colors with the board's big
moments. Pick the mode on the settings page:

  "off", "wins" (a favorite wins), "moments" (favorites' scores + wins),
  "glow" (moments + the team's color, softly, the whole time a favorite plays live)

Brands that can be driven over the home WiFi without a cloud login, all found by scanning:
  Philips Hue (bridge; press its button to pair), Govee (needs "LAN Control" switched on in
  the Govee Home app), WiZ, LIFX, Nanoleaf (hold its power button 5-7 s to pair), WLED.

Built blind (no lights to test with); everything is defensive: every network call has a short
timeout, failures are logged and skipped, and all of it runs in its own thread (LightsWorker)
so lights can never touch the display. After a flash, lights go back to how they were.
Pairing tokens (Hue, Nanoleaf) live in secrets.json under "lights", never sent to the page.
"""
import colorsys
import json
import logging
import random
import socket
import struct
import threading
import time

import requests

from .workers import Worker

log = logging.getLogger(__name__)

MODES = ("off", "wins", "moments", "glow")
DEFAULTS = {"mode": "moments", "devices": []}
FLASH_SECONDS = 10.0
FLASH_STEP = 0.6            # seconds per color while flashing (Hue bridges take ~10 commands/s)
GLOW_LEVEL = 0.6
TIMEOUT = 2.0
TYPES = ("hue", "govee", "wiz", "lifx", "nanoleaf", "wled")


def options(settings):
    out = dict(DEFAULTS)
    out.update({k: v for k, v in (settings.get("lights") or {}).items() if k in DEFAULTS})
    return out


def clean(current, update):
    cur = options({"lights": current or {}})
    for k, v in (update or {}).items():
        if k == "mode" and v in MODES:
            cur[k] = v
        elif k == "devices" and isinstance(v, list):
            devs = []
            for d in v[:30]:
                if isinstance(d, dict) and d.get("type") in TYPES and d.get("ip") and d.get("id"):
                    devs.append({"id": str(d["id"])[:80], "type": d["type"], "ip": str(d["ip"])[:40],
                                 "name": str(d.get("name") or d["type"])[:60], "light": str(d.get("light") or "")[:20],
                                 "bridge": str(d.get("bridge") or "")[:40]})
            cur[k] = devs
    return cur


def _tokens():
    from . import fantasy
    return dict(fantasy.secrets().get("lights") or {})


def _save_token(key, token):
    from . import fantasy
    t = _tokens()
    t[key] = token
    fantasy.save_secret("lights", t)


def team_rgb(team):
    """The team's two colors at full strength (lights want saturated colors, not LED-tuned)."""
    out = []
    for hexc in (getattr(team, "color", None), getattr(team, "alt_color", None)):
        try:
            rgb = [int(hexc[i:i + 2], 16) for i in (0, 2, 4)]
        except (ValueError, TypeError, IndexError):
            continue
        m = max(rgb)
        if m < 25:
            continue                                   # black: a light can't show it
        rgb = [round(c * 255 / m) for c in rgb]
        if all(sum(abs(a - b) for a, b in zip(rgb, o)) > 120 for o in out):
            out.append(rgb)
    if not out:
        out = [[255, 180, 0]]
    if len(out) == 1:
        out.append([255, 255, 255])
    return out


# ------------------------------------------------------------------ discovery helpers
def _udp(timeout=TIMEOUT, broadcast=False, port=0):
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    if broadcast:
        s.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
    s.settimeout(timeout)
    s.bind(("", port))
    return s


def _collect(s, seconds):
    """Every (data, ip) received on s for `seconds`."""
    out, end = [], time.time() + seconds
    while time.time() < end:
        s.settimeout(max(0.05, end - time.time()))
        try:
            data, addr = s.recvfrom(4096)
            out.append((data, addr[0]))
        except socket.timeout:
            break
        except OSError:
            break
    return out


def _mdns(service, seconds=2.0):
    """Minimal mDNS browse: ask who offers `service` (e.g. "_wled._tcp.local") with a legacy
    unicast query; answers come straight back to us. Returns {ip: instance name}."""
    q = struct.pack(">HHHHHH", 0, 0, 1, 0, 0, 0)
    for part in service.split("."):
        q += bytes([len(part)]) + part.encode()
    q += b"\x00" + struct.pack(">HH", 12, 1)          # PTR, IN
    found = {}
    try:
        s = _udp()
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 1)
        s.sendto(q, ("224.0.0.251", 5353))
        for data, ip in _collect(s, seconds):
            # The instance name is the first readable label after the question
            name = ""
            i = data.find(service.split(".")[0].encode())
            if i > 0:
                chunk = data[max(0, i - 64):i]
                j = max(chunk.rfind(b"\xc0"), chunk.rfind(b"\x00"))
                txt = chunk[j + 2 if j >= 0 else 0:]
                name = "".join(chr(c) for c in txt if 32 <= c < 127).strip()
            found.setdefault(ip, name)
        s.close()
    except OSError as exc:
        log.warning("mDNS %s: %s", service, exc)
    return found


def _rgb_to_hs(rgb):
    h, s, v = colorsys.rgb_to_hsv(*(c / 255 for c in rgb))
    return h, s, v


# ------------------------------------------------------------------ Philips Hue
def _rgb_to_xy(rgb):
    def g(c):
        c /= 255
        return ((c + 0.055) / 1.055) ** 2.4 if c > 0.04045 else c / 12.92
    r, gr, b = (g(c) for c in rgb)
    X = r * 0.664511 + gr * 0.154324 + b * 0.162028
    Y = r * 0.283881 + gr * 0.668433 + b * 0.047685
    Z = r * 0.000088 + gr * 0.072310 + b * 0.986039
    t = X + Y + Z
    return [round(X / t, 4), round(Y / t, 4)] if t else [0.3227, 0.329]


class Hue:
    @staticmethod
    def find():
        bridges = dict(_mdns("_hue._tcp.local"))
        if not bridges:
            try:   # Philips' own finder (cloud; fine to ask now and then)
                for b in requests.get("https://discovery.meethue.com/", timeout=5).json():
                    bridges[b["internalipaddress"]] = "Hue Bridge"
            except Exception:
                pass
        out = []
        for ip in bridges:
            token = _tokens().get("hue:" + ip)
            if not token:
                out.append({"id": "hue:" + ip, "type": "hue", "ip": ip, "name": "Philips Hue Bridge", "pair": True})
                continue
            try:
                lights = requests.get("http://%s/api/%s/lights" % (ip, token), timeout=TIMEOUT).json()
                for lid, l in lights.items():
                    if "xy" in (l.get("state") or {}):
                        out.append({"id": "hue:%s:%s" % (ip, lid), "type": "hue", "ip": ip, "light": lid,
                                    "bridge": ip, "name": "Hue: %s" % l.get("name", lid)})
            except Exception as exc:
                log.warning("Hue bridge %s: %s", ip, exc)
        return out

    @staticmethod
    def pair(ip):
        r = requests.post("http://%s/api" % ip, json={"devicetype": "ballin_board#scoreboard"}, timeout=5).json()
        item = (r or [{}])[0]
        if "success" in item:
            _save_token("hue:" + ip, item["success"]["username"])
            return True, "Paired"
        return False, "Press the round button on top of the Hue bridge, then tap Pair within 30 seconds"

    def __init__(self, d):
        self.url = "http://%s/api/%s/lights/%s" % (d["ip"], _tokens().get("hue:" + d["ip"], "-"), d["light"])

    def get(self):
        st = requests.get(self.url, timeout=TIMEOUT).json()["state"]
        return {k: st[k] for k in ("on", "bri", "xy", "ct") if k in st} | {"mode": st.get("colormode")}

    def set(self, rgb, level=1.0):
        requests.put(self.url + "/state", json={"on": True, "xy": _rgb_to_xy(rgb), "bri": max(1, round(254 * level)),
                                                "transitiontime": 0}, timeout=TIMEOUT)

    def restore(self, st):
        body = {"on": st.get("on", False), "transitiontime": 4}
        if st.get("on"):
            body["bri"] = st.get("bri", 254)
            if st.get("mode") == "ct" and "ct" in st:
                body["ct"] = st["ct"]
            elif "xy" in st:
                body["xy"] = st["xy"]
        requests.put(self.url + "/state", json=body, timeout=TIMEOUT)


# ------------------------------------------------------------------ Govee (LAN API)
GOVEE_GROUP, GOVEE_SCAN, GOVEE_REPLY, GOVEE_CMD = "239.255.255.250", 4001, 4002, 4003
_govee_lock = threading.Lock()      # one listener on the reply port at a time


class Govee:
    @staticmethod
    def find():
        out = []
        with _govee_lock:
            try:
                s = _udp(port=GOVEE_REPLY)
                s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 2)
                s.sendto(json.dumps({"msg": {"cmd": "scan", "data": {"account_topic": "reserve"}}}).encode(),
                         (GOVEE_GROUP, GOVEE_SCAN))
                for data, ip in _collect(s, 2.5):
                    try:
                        d = json.loads(data)["msg"]["data"]
                        out.append({"id": "govee:" + d.get("device", ip), "type": "govee", "ip": d.get("ip", ip),
                                    "name": "Govee %s" % d.get("sku", "")})
                    except (ValueError, KeyError):
                        pass
                s.close()
            except OSError as exc:
                log.warning("Govee scan: %s", exc)
        return out

    def __init__(self, d):
        self.ip = d["ip"]

    def _send(self, cmd, data):
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.sendto(json.dumps({"msg": {"cmd": cmd, "data": data}}).encode(), (self.ip, GOVEE_CMD))
        finally:
            s.close()

    def get(self):
        with _govee_lock:
            s = _udp(port=GOVEE_REPLY)
            try:
                self._send("devStatus", {})
                end = time.time() + 1.5
                while time.time() < end:          # first answer from this light, no waiting around
                    s.settimeout(max(0.05, end - time.time()))
                    data, addr = s.recvfrom(4096)
                    if addr[0] == self.ip:
                        return json.loads(data)["msg"]["data"]
            except socket.timeout:
                pass
            finally:
                s.close()
        return None

    def set(self, rgb, level=1.0):
        self._send("turn", {"value": 1})
        self._send("brightness", {"value": max(1, round(100 * level))})
        self._send("colorwc", {"color": {"r": rgb[0], "g": rgb[1], "b": rgb[2]}, "colorTemInKelvin": 0})

    def restore(self, st):
        if not st:
            return
        if st.get("onOff"):
            self._send("brightness", {"value": st.get("brightness", 100)})
            if st.get("colorTemInKelvin"):
                self._send("colorwc", {"color": {"r": 0, "g": 0, "b": 0}, "colorTemInKelvin": st["colorTemInKelvin"]})
            elif st.get("color"):
                self._send("colorwc", {"color": st["color"], "colorTemInKelvin": 0})
        else:
            self._send("turn", {"value": 0})


# ------------------------------------------------------------------ WiZ
WIZ_PORT = 38899


class Wiz:
    @staticmethod
    def find():
        out = []
        try:
            s = _udp(broadcast=True)
            s.sendto(b'{"method":"getPilot","params":{}}', ("255.255.255.255", WIZ_PORT))
            for data, ip in _collect(s, 2.0):
                try:
                    mac = json.loads(data).get("result", {}).get("mac", ip)
                    out.append({"id": "wiz:" + mac, "type": "wiz", "ip": ip, "name": "WiZ light %s" % mac[-4:]})
                except ValueError:
                    pass
            s.close()
        except OSError as exc:
            log.warning("WiZ scan: %s", exc)
        return out

    def __init__(self, d):
        self.ip = d["ip"]

    def _call(self, method, params, reply=False):
        s = _udp(timeout=TIMEOUT)
        try:
            s.sendto(json.dumps({"method": method, "params": params}).encode(), (self.ip, WIZ_PORT))
            if reply:
                return json.loads(s.recvfrom(2048)[0]).get("result")
        finally:
            s.close()

    def get(self):
        return self._call("getPilot", {}, reply=True)

    def set(self, rgb, level=1.0):
        self._call("setPilot", {"state": True, "r": rgb[0], "g": rgb[1], "b": rgb[2],
                                "dimming": max(10, round(100 * level))})

    def restore(self, st):
        if not st:
            return
        if not st.get("state"):
            self._call("setPilot", {"state": False})
            return
        p = {"state": True, "dimming": st.get("dimming", 100)}
        if st.get("sceneId"):
            p["sceneId"] = st["sceneId"]
        elif st.get("temp"):
            p["temp"] = st["temp"]
        elif "r" in st:
            p.update(r=st["r"], g=st["g"], b=st["b"])
        self._call("setPilot", p)


# ------------------------------------------------------------------ LIFX (LAN protocol)
LIFX_PORT = 56700


def _lifx_packet(ptype, payload=b"", target=b"\x00" * 8, tagged=False, res=False):
    size = 36 + len(payload)
    proto = 1024 | (1 << 12) | ((1 << 13) if tagged else 0)
    frame = struct.pack("<HHI", size, proto, 0x0BA11B0A)
    addr = target + b"\x00" * 6 + struct.pack("<BB", 1 if res else 0, random.randint(0, 255))
    header = b"\x00" * 8 + struct.pack("<HH", ptype, 0)
    return frame + addr + header + payload


class Lifx:
    @staticmethod
    def find():
        out = []
        try:
            s = _udp(broadcast=True)
            s.sendto(_lifx_packet(2, tagged=True), ("255.255.255.255", LIFX_PORT))   # GetService
            seen = set()
            for data, ip in _collect(s, 2.0):
                if len(data) >= 36 and struct.unpack("<H", data[32:34])[0] == 3 and ip not in seen:
                    seen.add(ip)
                    mac = data[8:14].hex()
                    out.append({"id": "lifx:" + mac, "type": "lifx", "ip": ip, "name": "LIFX %s" % mac[-4:]})
            s.close()
        except OSError as exc:
            log.warning("LIFX scan: %s", exc)
        return out

    def __init__(self, d):
        self.ip = d["ip"]

    def _send(self, ptype, payload=b"", want=None):
        s = _udp(timeout=TIMEOUT)
        try:
            s.sendto(_lifx_packet(ptype, payload, res=want is not None), (self.ip, LIFX_PORT))
            if want is not None:
                end = time.time() + TIMEOUT
                while time.time() < end:
                    data = s.recvfrom(1024)[0]
                    if len(data) >= 36 and struct.unpack("<H", data[32:34])[0] == want:
                        return data[36:]
        finally:
            s.close()

    def get(self):
        p = self._send(101, want=107)                      # Get -> State
        if not p:
            return None
        h, s, b, k = struct.unpack("<HHHH", p[:8])
        power = struct.unpack("<H", p[10:12])[0]
        return {"hsbk": [h, s, b, k], "power": power}

    def set(self, rgb, level=1.0):
        h, s, v = _rgb_to_hs(rgb)
        self._send(117, struct.pack("<HI", 65535, 0))      # power on, now
        self._send(102, struct.pack("<BHHHHI", 0, round(h * 65535), round(s * 65535), round(65535 * level), 3500, 0))

    def restore(self, st):
        if not st:
            return
        h, s, b, k = st["hsbk"]
        self._send(102, struct.pack("<BHHHHI", 0, h, s, b, k, 400))
        self._send(117, struct.pack("<HI", st["power"], 400))


# ------------------------------------------------------------------ Nanoleaf
class Nanoleaf:
    @staticmethod
    def find():
        out = []
        for ip, name in _mdns("_nanoleafapi._tcp.local").items():
            out.append({"id": "nanoleaf:" + ip, "type": "nanoleaf", "ip": ip, "name": name or "Nanoleaf",
                        "pair": not _tokens().get("nanoleaf:" + ip)})
        return out

    @staticmethod
    def pair(ip):
        r = requests.post("http://%s:16021/api/v1/new" % ip, timeout=5)
        if r.status_code == 200:
            _save_token("nanoleaf:" + ip, r.json()["auth_token"])
            return True, "Paired"
        return False, "Hold the Nanoleaf power button for 5-7 seconds until its lights flash, then tap Pair within 30 seconds"

    def __init__(self, d):
        self.url = "http://%s:16021/api/v1/%s" % (d["ip"], _tokens().get("nanoleaf:" + d["ip"], "-"))

    def get(self):
        st = requests.get(self.url + "/state", timeout=TIMEOUT).json()
        out = {k: (st.get(k) or {}).get("value") for k in ("on", "brightness", "hue", "sat", "ct")}
        out["mode"] = st.get("colorMode")
        if out["mode"] == "effect":
            out["effect"] = requests.get(self.url + "/effects/select", timeout=TIMEOUT).json()
        return out

    def set(self, rgb, level=1.0):
        h, s, v = _rgb_to_hs(rgb)
        requests.put(self.url + "/state", json={"on": {"value": True}, "hue": {"value": round(h * 360)},
                                                "sat": {"value": round(s * 100)},
                                                "brightness": {"value": max(1, round(100 * level)), "duration": 0}},
                     timeout=TIMEOUT)

    def restore(self, st):
        if not st.get("on"):
            requests.put(self.url + "/state", json={"on": {"value": False}}, timeout=TIMEOUT)
            return
        if st.get("mode") == "effect" and st.get("effect"):
            requests.put(self.url + "/effects", json={"select": st["effect"]}, timeout=TIMEOUT)
        elif st.get("mode") == "ct" and st.get("ct"):
            requests.put(self.url + "/state", json={"ct": {"value": st["ct"]}}, timeout=TIMEOUT)
        else:
            requests.put(self.url + "/state", json={"hue": {"value": st.get("hue") or 0},
                                                    "sat": {"value": st.get("sat") or 0}}, timeout=TIMEOUT)
        requests.put(self.url + "/state", json={"brightness": {"value": st.get("brightness") or 100}}, timeout=TIMEOUT)


# ------------------------------------------------------------------ WLED
class Wled:
    @staticmethod
    def find():
        return [{"id": "wled:" + ip, "type": "wled", "ip": ip, "name": name or "WLED"}
                for ip, name in _mdns("_wled._tcp.local").items()]

    def __init__(self, d):
        self.url = "http://%s/json/state" % d["ip"]

    def get(self):
        st = requests.get(self.url, timeout=TIMEOUT).json()
        return {"on": st.get("on"), "bri": st.get("bri"),
                "seg": [{k: s[k] for k in ("id", "col", "fx", "pal", "sx", "ix", "on", "bri") if k in s}
                        for s in st.get("seg") or []]}

    def set(self, rgb, level=1.0):
        segs = getattr(self, "_segs", None) or [{"id": 0}]
        requests.post(self.url, json={"on": True, "bri": max(1, round(255 * level)), "transition": 0,
                                      "seg": [{"id": s["id"], "col": [list(rgb)], "fx": 0} for s in segs]},
                      timeout=TIMEOUT)

    def restore(self, st):
        requests.post(self.url, json=dict(st, transition=4), timeout=TIMEOUT)


DRIVERS = {"hue": Hue, "govee": Govee, "wiz": Wiz, "lifx": Lifx, "nanoleaf": Nanoleaf, "wled": Wled}


def scan():
    """Everything the board can find on the home network, every brand at once (~3 s)."""
    results = []
    threads = []

    def run(drv):
        try:
            results.extend(drv.find())
        except Exception as exc:
            log.warning("Light scan %s: %s", drv.__name__, exc)
    for drv in DRIVERS.values():
        t = threading.Thread(target=run, args=(drv,), daemon=True)
        t.start()
        threads.append(t)
    for t in threads:
        t.join(8)
    return sorted(results, key=lambda d: (d["type"], d["name"]))


def pair(kind, ip):
    drv = DRIVERS.get(kind)
    if not hasattr(drv, "pair"):
        return False, "This light doesn't need pairing"
    try:
        return drv.pair(ip)
    except Exception as exc:
        return False, "Couldn't reach it (%s)" % exc


# ------------------------------------------------------------------ the show
class Rig:
    """The chosen lights: remembers how they were, flashes, glows, puts them back."""

    def __init__(self, devices):
        self.lights = []
        for d in devices:
            try:
                drv = DRIVERS[d["type"]](d)
                drv.name = d.get("name", d["type"])
                self.lights.append(drv)
            except Exception as exc:
                log.warning("Light %s: %s", d.get("name"), exc)
        self.saved = {}
        self.glowing = None          # rgb while a favorite's game glow is on

    def _each(self, fn):
        threads = []
        for light in self.lights:
            def run(l=light):
                try:
                    fn(l)
                except Exception as exc:
                    log.warning("Light %s: %s", getattr(l, "name", "?"), exc)
            t = threading.Thread(target=run, daemon=True)
            t.start()
            threads.append(t)
        for t in threads:
            t.join(TIMEOUT * 2)

    def remember(self):
        if self.saved:
            return                    # already holding the "before" picture (e.g. during a glow)

        def get(l):
            st = l.get()
            self.saved[id(l)] = st
            if isinstance(l, Wled) and st:
                l._segs = st.get("seg")
        self._each(get)

    def put_back(self):
        if self.glowing:
            self._each(lambda l: l.set(self.glowing, GLOW_LEVEL))
            return
        saved, self.saved = self.saved, {}
        self._each(lambda l: l.restore(saved[id(l)]) if saved.get(id(l)) is not None else None)

    def flash(self, colors, seconds=FLASH_SECONDS, stop=None):
        self.remember()
        end, i = time.time() + seconds, 0
        while time.time() < end and not (stop and stop.is_set()):
            c = colors[i % len(colors)]
            self._each(lambda l: l.set(c))
            i += 1
            time.sleep(FLASH_STEP)
        self.put_back()

    def glow(self, rgb):
        if rgb == self.glowing:
            return
        self.remember()
        self.glowing = rgb
        self._each(lambda l: l.set(rgb, GLOW_LEVEL))

    def end_glow(self):
        if self.glowing:
            self.glowing = None
            self.put_back()


def test_flash(devices, colors=((30, 65, 210), (255, 80, 0))):
    """Settings page "Test" button: a short flash (Bears blue and orange) on the chosen lights."""
    Rig(devices).flash([list(c) for c in colors], seconds=5)


class LightsWorker(Worker):
    """Plays queued light moments (state.light_events) and the live-game glow."""

    interval = 1.0

    def __init__(self, state, stop):
        super().__init__(state, stop)
        self.rig, self.rig_key = None, None

    def step(self):
        opts = options(self.state.settings)
        key = json.dumps(opts["devices"], sort_keys=True)
        if opts["mode"] == "off" or not opts["devices"]:
            if self.rig:
                self.rig.end_glow()
                self.rig = self.rig_key = None
            self.state.light_events.clear()
            return
        if key != self.rig_key:
            if self.rig:
                self.rig.end_glow()
            self.rig, self.rig_key = Rig(opts["devices"]), key
        while self.state.light_events:
            ev = self.state.light_events.popleft()
            if ev["kind"] == "score" and opts["mode"] == "wins":
                continue
            log.info("Lights: %s %s", ev["kind"], ev.get("what", ""))
            self.rig.flash(ev["colors"], stop=self.stop)
        glow = self.state.light_glow if opts["mode"] == "glow" else None
        if glow:
            self.rig.glow(glow)
        else:
            self.rig.end_glow()
