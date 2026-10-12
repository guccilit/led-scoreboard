"""WiFi setup hotspot ("Ballin Board Setup").

The NetSetup thread runs inside the settings-page service (root, already handles WiFi):
  * no WiFi/ethernet connection for NO_NET_SECS -> start the hotspot
  * a request file (button triple-press, factory reset) -> start the hotspot
  * in hotspot mode, every RETRY_SECS with nobody connected to it, briefly drop the
    hotspot and try the saved networks again (a router reboot shouldn't strand the board)
  * a hotspot started from the button gives up after BUTTON_TIMEOUT if nothing happens

The current mode is published in MODE_FILE (JSON) for the scoreboard to show instructions.
Phones joining the hotspot get every DNS name answered with the board's address, so the
settings page pops up like a hotel WiFi sign-in page.

The root filesystem is read-only (overlay), so WiFi networks saved through NetworkManager
(stored by netplan in /etc/netplan) are copied to the protected layer by persist_netplan().
"""
import json
import logging
import os
import shutil
import subprocess
import threading
import time

log = logging.getLogger(__name__)

SSID = "Ballin Board Setup"
CON = "ballin-setup"
AP_IP = "10.42.0.1"
MODE_FILE = "/run/ballin-netmode.json"
REQUEST_FILE = "/run/ballin-netmode.request"
SCAN_FILE = "/run/ballin-wifi-scan.json"
NO_NET_SECS = 90
RETRY_SECS = 180
BUTTON_TIMEOUT = 600
LOWER = "/media/root-ro"
CAPTIVE_CONF = "/etc/NetworkManager/dnsmasq-shared.d/ballin-captive.conf"


def _nmcli(*args, timeout=30):
    return subprocess.run(["nmcli"] + list(args), capture_output=True, text=True, timeout=timeout)


def read_mode():
    try:
        with open(MODE_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {"mode": "normal"}


def _write_mode(**info):
    tmp = MODE_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(dict(info, updated=time.time()), f)
    os.replace(tmp, MODE_FILE)


def request_setup(reason="button"):
    """Ask the NetSetup thread (settings-page service) to start the hotspot."""
    with open(REQUEST_FILE, "w") as f:
        f.write(reason)


def saved_wifi():
    out = _nmcli("-t", "-f", "NAME,TYPE", "con", "show").stdout
    return [line.rsplit(":", 1)[0] for line in out.splitlines()
            if line.endswith(":802-11-wireless") and line.rsplit(":", 1)[0] != CON]


def saved_networks():
    """Saved WiFi networks: [{"name": profile name, "ssid": network name}]."""
    out = []
    for name in saved_wifi():
        ssid = _nmcli("-g", "802-11-wireless.ssid", "con", "show", name).stdout.strip()
        if ssid:
            out.append({"name": name, "ssid": ssid})
    return out


def online():
    """Connected to a network (WiFi other than our hotspot, or ethernet)?"""
    out = _nmcli("-t", "-f", "DEVICE,TYPE,STATE,CONNECTION", "dev").stdout
    for line in out.splitlines():
        dev, typ, state, con = (line.split(":") + ["", "", "", ""])[:4]
        if state == "connected" and con != CON and typ in ("wifi", "ethernet"):
            return True
    return False


def scan():
    """Scan for networks and cache the list (scanning isn't possible while the hotspot runs)."""
    _nmcli("dev", "wifi", "rescan", timeout=20)
    out = _nmcli("-t", "-f", "SSID,SIGNAL,SECURITY,IN-USE", "dev", "wifi", "list", timeout=20).stdout
    nets = {}
    for line in out.splitlines():
        parts = line.replace("\\:", "\x00").split(":")
        if len(parts) < 4 or not parts[0]:
            continue
        ssid = parts[0].replace("\x00", ":")
        if ssid == SSID:
            continue
        signal = int(parts[1] or 0)
        if ssid not in nets or signal > nets[ssid]["signal"]:
            nets[ssid] = {"ssid": ssid, "signal": signal, "secure": bool(parts[2] and parts[2] != "--"),
                          "current": parts[3] == "*"}
    result = sorted(nets.values(), key=lambda n: -n["signal"])
    if result:
        with open(SCAN_FILE + ".tmp", "w") as f:
            json.dump(result, f)
        os.replace(SCAN_FILE + ".tmp", SCAN_FILE)
    return result


def cached_scan():
    try:
        with open(SCAN_FILE) as f:
            return json.load(f)
    except (OSError, ValueError):
        return []


def _remount_lower(mode):
    return subprocess.run(["mount", "-o", "remount," + mode, LOWER], capture_output=True).returncode == 0


def _write_lower(paths):
    """Copy files/dirs from the live system into the read-only layer, so they survive restarts.
    paths: {live path: is_dir}. Directories are mirrored (files removed live are removed too)."""
    if not os.path.ismount(LOWER):
        return True          # not read-only (development): nothing to do
    if not _remount_lower("rw"):
        log.error("Could not unlock the read-only layer to save network settings")
        return False
    try:
        for path, is_dir in paths.items():
            dst = LOWER + path
            if is_dir:
                os.makedirs(dst, exist_ok=True)
                live = set(os.listdir(path)) if os.path.isdir(path) else set()
                for name in os.listdir(dst):
                    if name not in live:
                        os.unlink(os.path.join(dst, name))
                for name in live:
                    shutil.copy2(os.path.join(path, name), os.path.join(dst, name))
            else:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                shutil.copy2(path, dst)
        os.sync()
        return True
    finally:
        if not _remount_lower("ro"):
            log.error("Read-only layer could not be locked again; a restart will lock it")


def persist_netplan():
    """Keep saved WiFi networks across restarts. NetworkManager stores networks added from the
    page in /etc/NetworkManager/system-connections; the one set up when the card was flashed
    lives in /etc/netplan. Both are copied to the read-only layer."""
    return _write_lower({"/etc/netplan": True, "/etc/NetworkManager/system-connections": True})


def ensure_captive_dns():
    """Answer every DNS name with the board's address while the hotspot runs."""
    want = "address=/#/%s\n" % AP_IP
    try:
        with open(CAPTIVE_CONF) as f:
            if f.read() == want:
                return
    except OSError:
        pass
    os.makedirs(os.path.dirname(CAPTIVE_CONF), exist_ok=True)
    with open(CAPTIVE_CONF, "w") as f:
        f.write(want)
    _write_lower({CAPTIVE_CONF: False})


def start_hotspot():
    ensure_captive_dns()
    scan()
    if CON not in _nmcli("-t", "-f", "NAME", "con", "show").stdout.split():
        _nmcli("con", "add", "type", "wifi", "ifname", "wlan0", "con-name", CON, "ssid", SSID,
               "autoconnect", "no", "802-11-wireless.mode", "ap", "802-11-wireless.band", "bg",
               "ipv4.method", "shared", "ipv4.addresses", AP_IP + "/24", "ipv6.method", "disabled")
    r = _nmcli("con", "up", CON, timeout=40)
    log.info("Hotspot %r up: %s", SSID, "ok" if r.returncode == 0 else r.stderr.strip())
    return r.returncode == 0


def stop_hotspot():
    _nmcli("con", "down", CON, timeout=20)


def hotspot_clients():
    out = subprocess.run(["iw", "dev", "wlan0", "station", "dump"], capture_output=True, text=True).stdout
    return out.count("Station ")


def try_saved():
    for name in saved_wifi():
        r = _nmcli("con", "up", name, timeout=45)
        if r.returncode == 0:
            log.info("Reconnected to saved network %s", name)
            return True
    return False


def connect(ssid, password):
    """Join a network chosen on the settings page, saving it for good. Returns (ok, message).
    A saved network with no password given reconnects with what was saved."""
    stop_hotspot()
    time.sleep(2)
    saved = next((n["name"] for n in saved_networks() if n["ssid"] == ssid), None)
    if saved and not password:
        r = _nmcli("con", "up", saved, timeout=45)
        if r.returncode == 0:
            log.info("Reconnected to saved network %r", ssid)
            return True, ""
        return False, (r.stderr or r.stdout).strip()
    cmd = ["dev", "wifi", "connect", ssid] + (["password", password] if password else [])
    r = _nmcli(*cmd, timeout=60)
    if r.returncode != 0:
        msg = (r.stderr or r.stdout).strip()
        log.info("WiFi connect to %r failed: %s", ssid, msg)
        # Don't keep a half-made profile with a wrong password around.
        _nmcli("con", "delete", ssid, timeout=20)
        return False, msg
    persist_netplan()
    log.info("Connected to %r and saved it", ssid)
    return True, ""


def forget_all_wifi():
    """Factory reset: forget every saved WiFi network, for good (also across restarts)."""
    for _ in range(30):                       # NetworkManager may still be starting
        if _nmcli("general", "status", timeout=10).returncode == 0:
            break
        time.sleep(2)
    for name in saved_wifi():
        r = _nmcli("con", "delete", name, timeout=20)
        log.info("Forgot %s: %s", name, "ok" if r.returncode == 0 else r.stderr.strip())
    # Networks defined by netplan files: remove the files too, in case NM left them.
    for name in os.listdir("/etc/netplan"):
        p = os.path.join("/etc/netplan", name)
        try:
            with open(p) as f:
                if "wifis:" in f.read():
                    os.unlink(p)
                    log.info("Removed %s", p)
        except OSError:
            pass
    persist_netplan()


class NetSetup(threading.Thread):
    def __init__(self):
        super().__init__(name="NetSetup", daemon=True)
        self.mode = "normal"
        self.reason = ""
        self.since = time.monotonic()
        self.last_ok = time.monotonic()
        self.last_try = 0.0
        self.last_error = ""
        self.lock = threading.Lock()     # connect() from the web page vs. this loop

    def publish(self):
        _write_mode(mode=self.mode, reason=self.reason, ssid=SSID, ip=AP_IP, last_error=self.last_error)

    def run(self):
        self.publish()
        while True:
            try:
                with self.lock:
                    self.step()
            except Exception:
                log.exception("NetSetup step failed")
            time.sleep(5)

    def enter_setup(self, reason):
        if start_hotspot():
            self.mode, self.reason, self.since = "setup", reason, time.monotonic()
            self.last_try = time.monotonic()
            self.publish()

    def leave_setup(self):
        stop_hotspot()
        self.mode, self.reason = "normal", ""
        self.last_ok = time.monotonic()
        self.publish()

    def step(self):
        now = time.monotonic()
        try:
            publish_web_name()
        except Exception:
            log.exception("Publishing the web name failed")
        if os.path.exists(REQUEST_FILE):
            with open(REQUEST_FILE) as f:
                reason = f.read().strip() or "button"
            os.unlink(REQUEST_FILE)
            if reason == "reset":
                log.info("Factory reset: forgetting saved WiFi")
                if self.mode == "setup":
                    stop_hotspot()
                forget_all_wifi()
                self.mode = "normal"
            if reason == "cancel":
                if self.mode == "setup":
                    log.info("Setup cancelled from the button; back to saved networks")
                    self.leave_setup()
                    try_saved()
                return
            if self.mode != "setup":
                log.info("Setup hotspot requested (%s)", reason)
                self.enter_setup(reason)
            return
        if self.mode == "normal":
            if online():
                self.last_ok = now
            elif now - self.last_ok > NO_NET_SECS:
                log.info("No network for %d s: starting setup hotspot", NO_NET_SECS)
                self.enter_setup("no-wifi")
            return
        # Setup mode
        if self.reason == "button" and now - self.since > BUTTON_TIMEOUT and hotspot_clients() == 0:
            log.info("Setup hotspot timed out; back to saved networks")
            self.leave_setup()
            try_saved()
            return
        if now - self.last_try > RETRY_SECS and hotspot_clients() == 0 and saved_wifi():
            self.last_try = now
            stop_hotspot()
            if try_saved():
                self.mode, self.reason, self.last_ok = "normal", "", now
                self.publish()
            else:
                self.enter_setup(self.reason)

    def connect(self, ssid, password):
        with self.lock:
            ok, msg = connect(ssid, password)
            if ok:
                self.last_error = ""
                self.mode, self.reason, self.last_ok = "normal", "", time.monotonic()
                self.publish()
            else:
                self.last_error = "Couldn't join %s. Check the password and try again." % ssid
                self.enter_setup(self.reason or "no-wifi")
            return ok


AVAHI_HOSTS = "/etc/avahi/hosts"
MDNS_GROUP, MDNS_PORT = "224.0.0.251", 5353
_responder = None


def _wlan_ip():
    out = subprocess.run(["ip", "-4", "-o", "addr", "show", "wlan0"], capture_output=True, text=True, timeout=5).stdout
    for part in out.split():
        if "/" in part and part[0].isdigit():
            return part.split("/")[0]
    return ""


class NameResponder(threading.Thread):
    """Answers local-network name lookups (mDNS) for the owner's chosen name: a phone asking
    for NAME.local gets the board's address. avahi already answers for scoreboard.local but
    refuses a second name for the same address ("local name collision"), so this small
    responder shares the mDNS port with it and handles just the one extra name."""

    def __init__(self):
        super().__init__(daemon=True, name="NameResponder")
        self.names, self.ip = [], None           # e.g. ["test"], "192.168.1.20"
        self.sock = None

    def set(self, names, ip):
        names = [n for n in names if n]
        if (names, ip) != (self.names, self.ip):
            self.names, self.ip = names, ip
            if names and ip:
                log.info("Answering for %s -> %s", ", ".join("http://%s.local" % n for n in names), ip)
                self._announce()

    def _open(self):
        import socket
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
        s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        if hasattr(socket, "SO_REUSEPORT"):
            try:
                s.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEPORT, 1)
            except OSError:
                pass
        s.bind(("", MDNS_PORT))
        s.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_TTL, 255)
        self.sock = s
        self._joined = None

    def _join(self):
        import socket
        if self.ip and self._joined != self.ip:
            mreq = socket.inet_aton(MDNS_GROUP) + socket.inet_aton(self.ip)
            try:
                self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, mreq)
                self.sock.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton(self.ip))
            except OSError:
                pass
            self._joined = self.ip

    def _answer(self, name, qid=0, question=b"", legacy=False, empty=False):
        """legacy: a one-shot lookup from a random port (Android asks this way). Per RFC 6762 6.7
        its answer must not carry the cache-flush bit (Android's resolver ignored ours, 2026-10-04)
        and gets a short TTL. empty: "no such record" (we have no IPv6 address), so the asker
        doesn't sit waiting for an AAAA answer."""
        import socket
        import struct
        fqdn = b"".join(bytes([len(p)]) + p for p in (name.encode(), b"local")) + b"\x00"
        cls, ttl = (1, 10) if legacy else (0x8001, 120)          # IN (+ cache flush on multicast)
        rr = b"" if empty else fqdn + struct.pack("!HHIH", 1, cls, ttl, 4) + socket.inet_aton(self.ip)
        qd = 1 if question else 0
        return struct.pack("!HHHHHH", qid, 0x8400, qd, 0 if empty else 1, 0, 0) + question + rr

    def _announce(self):
        try:
            if self.sock is None:
                self._open()
            self._join()
            for name in self.names:
                self.sock.sendto(self._answer(name), (MDNS_GROUP, MDNS_PORT))
        except OSError as exc:
            log.warning("Name announce failed: %s", exc)

    @staticmethod
    def _questions(data):
        """[(name, qtype, unicast?, raw question bytes)] from an mDNS query (no compression in
        questions is assumed; anything odd is ignored)."""
        import struct
        if len(data) < 12:
            return []
        qid, flags, qd = struct.unpack("!HHH", data[:6])
        if flags & 0x8000:                       # a response, not a query
            return []
        out, pos = [], 12
        for _ in range(qd):
            labels, start = [], pos
            while pos < len(data) and data[pos]:
                if data[pos] & 0xC0:             # compressed: give up on this packet
                    return out
                ln = data[pos]
                labels.append(data[pos + 1:pos + 1 + ln].decode("ascii", "ignore").lower())
                pos += 1 + ln
            pos += 1
            if pos + 4 > len(data):
                break
            qtype, qclass = struct.unpack("!HH", data[pos:pos + 4])
            pos += 4
            out.append((".".join(labels), qtype, bool(qclass & 0x8000), data[start:pos], qid))
        return out

    def run(self):
        while True:
            try:
                if self.sock is None:
                    self._open()
                self._join()
                data, addr = self.sock.recvfrom(9000)
                if not (self.names and self.ip):
                    continue
                for qname, qtype, unicast, raw, qid in self._questions(data):
                    name = qname[:-6] if qname.endswith(".local") else None
                    if name not in self.names:
                        continue
                    if addr[1] != MDNS_PORT:     # a plain one-shot lookup: answer it directly
                        if qtype in (1, 255):
                            self.sock.sendto(self._answer(name, qid, raw, legacy=True), addr)
                        elif qtype == 28:        # IPv6: we have none, say so right away
                            self.sock.sendto(self._answer(name, qid, raw, legacy=True, empty=True), addr)
                        continue
                    if qtype not in (1, 255):
                        continue
                    self.sock.sendto(self._answer(name), (MDNS_GROUP, MDNS_PORT))
                    if unicast:
                        self.sock.sendto(self._answer(name), addr)
            except Exception as exc:
                log.warning("Name responder: %s", exc)
                self.sock = None
                time.sleep(5)


def publish_web_name():
    """Make the settings page reachable at http://NAME.local (the owner's chosen name) as
    well as scoreboard.local, at the board's current address (called every few seconds)."""
    global _responder
    from . import settings as settings_mod
    name = settings_mod.load().get("web_name") or "scoreboard"
    if _responder is None:
        _responder = NameResponder()
        _responder.start()
        try:                                     # undo the earlier avahi-hosts approach (it collided)
            with open(AVAHI_HOSTS) as f:
                old = f.read()
            if "web_name setting" in old:
                with open(AVAHI_HOSTS, "w") as f:
                    f.write("")
                subprocess.run(["systemctl", "reload", "avahi-daemon"], capture_output=True, timeout=15)
        except OSError:
            pass
    _responder.set([name if name != "scoreboard" else None], _wlan_ip())
