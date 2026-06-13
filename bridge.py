#!/usr/bin/env python3
"""
Skywatch bridge v3 (container-ready)
Serves the Skywatch portal page plus a live aircraft.json built from
the decoder, tried in order:

  1. a decoder's aircraft.json on disk   (only if mounted into /run/...)
  2. a decoder's aircraft.json over HTTP (on ADSB_HOST)
  3. the BaseStation stream on TCP 30003 (on ADSB_HOST; needs bs="yes"
     in /etc/fr24feed.ini if you rely on fr24feed's embedded decoder)

Configured entirely by environment variables (see docker-compose.yml):
  ADSB_HOST, ADSB_SBS_PORT, PORT,
  RECEIVER_LAT, RECEIVER_LON, RECEIVER_NAME, SITE_NAME, SITE_TAGLINE.

Standard library only - no packages to install.
"""
import json
import os
import socket
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.request import urlopen

PORT = int(os.environ.get("PORT", "8088"))  # port this bridge serves on

HERE = os.path.dirname(os.path.abspath(__file__))
PAGE_NAMES = ["skywatch.html", "index.html"]

# Where the decoder lives. In a container on a separate host, set
# ADSB_HOST to your Pi's address (e.g. 192.168.1.54). Defaults to
# localhost so the script still works when run on the Pi itself.
ADSB_HOST = (os.environ.get("ADSB_HOST", "127.0.0.1").strip() or "127.0.0.1")
ADSB_SBS_PORT = int(os.environ.get("ADSB_SBS_PORT", "30003"))

# On-disk aircraft.json (only used if these paths exist, e.g. the
# decoder's /run dir is mounted into the container).
JSON_FILES = [
    "/run/readsb/aircraft.json",
    "/run/dump1090-fa/aircraft.json",
    "/run/dump1090-mutability/aircraft.json",
    "/run/dump1090/aircraft.json",
]
# aircraft.json over HTTP on the decoder host (richest source).
JSON_URLS = [
    "http://%s:8080/data/aircraft.json" % ADSB_HOST,
    "http://%s/tar1090/data/aircraft.json" % ADSB_HOST,
    "http://%s/skyaware/data/aircraft.json" % ADSB_HOST,
    "http://%s/dump1090-fa/data/aircraft.json" % ADSB_HOST,
]
# BaseStation stream fallback.
SBS_HOSTS = [(ADSB_HOST, ADSB_SBS_PORT)]

# Page config injected from the environment (so the container can be
# configured without editing the HTML). Empty values are skipped.
PAGE_ENV = {
    "siteName":     os.environ.get("SITE_NAME"),
    "tagline":      os.environ.get("SITE_TAGLINE"),
    "receiverName": os.environ.get("RECEIVER_NAME"),
    "lat":          os.environ.get("RECEIVER_LAT"),
    "lon":          os.environ.get("RECEIVER_LON"),
}


# ---------------------------------------------------------------------------
# BaseStation (SBS-1) listener: builds aircraft state from the raw stream.
# Field layout: MSG,TT,SID,AID,HEX,FID,DateG,TimeG,DateL,TimeL,
#               CS,Alt,GS,Trk,Lat,Lon,VR,Squawk,Alert,Emerg,SPI,Ground
# ---------------------------------------------------------------------------
class SbsTracker:
    def __init__(self):
        self.lock = threading.Lock()
        self.ac = {}
        self.count = 0
        self.connected = False
        self.endpoint = None
        threading.Thread(target=self._run, daemon=True).start()

    def _run(self):
        while True:
            for host, port in SBS_HOSTS:
                try:
                    s = socket.create_connection((host, port), timeout=4)
                    s.settimeout(60)
                    self.connected, self.endpoint = True, "%s:%d" % (host, port)
                    buf = b""
                    while True:
                        data = s.recv(4096)
                        if not data:
                            break
                        buf += data
                        while b"\n" in buf:
                            line, buf = buf.split(b"\n", 1)
                            self._line(line.decode("ascii", "ignore").strip())
                except Exception:
                    pass
                self.connected = False
            time.sleep(5)

    def _line(self, line):
        p = line.split(",")
        if len(p) < 11 or p[0] != "MSG":
            return
        hexid = p[4].strip().lower()
        if not hexid:
            return
        self.count += 1
        now = time.time()

        def num(i, cast=float):
            try:
                return cast(p[i])
            except (ValueError, IndexError):
                return None

        with self.lock:
            a = self.ac.setdefault(hexid, {"hex": hexid})
            a["seen"] = now
            tt = p[1].strip()
            cs = p[10].strip() if len(p) > 10 else ""
            if cs:
                a["flight"] = cs
            if tt in ("2", "3", "5", "6", "7"):
                alt = num(11, int)
                if alt is not None:
                    a["alt_baro"] = alt
            if tt in ("2", "3"):
                la, lo = num(14), num(15)
                if la is not None and lo is not None:
                    a["lat"], a["lon"] = la, lo
                    a["pos_t"] = now
            if tt in ("2", "4"):
                gs, trk = num(12), num(13)
                if gs is not None:
                    a["gs"] = gs
                if trk is not None:
                    a["track"] = trk
            if tt == "4":
                vr = num(16, int)
                if vr is not None:
                    a["baro_rate"] = vr
            if tt == "6":
                sq = p[17].strip() if len(p) > 17 else ""
                if sq:
                    a["squawk"] = sq
            if len(p) > 21 and p[21].strip() == "-1":
                a["alt_baro"] = "ground"

    def snapshot(self):
        now = time.time()
        with self.lock:
            stale = [h for h, a in self.ac.items() if now - a.get("seen", 0) > 60]
            for h in stale:
                del self.ac[h]
            out = []
            for a in self.ac.values():
                d = {k: a[k] for k in ("hex", "flight", "alt_baro", "gs", "track",
                                       "baro_rate", "squawk", "lat", "lon") if k in a}
                d["seen"] = round(now - a["seen"], 1)
                if "pos_t" in a:
                    d["seen_pos"] = round(now - a["pos_t"], 1)
                out.append(d)
            return out


sbs = SbsTracker()


# ---------------------------------------------------------------------------
# Source selection
# ---------------------------------------------------------------------------
def read_json_file(path):
    with open(path, "rb") as f:
        return json.loads(f.read())


def read_json_url(url):
    with urlopen(url, timeout=3) as r:
        return json.loads(r.read())


def build_payload():
    """Return (dict, source_label) or (None, None)."""
    for path in JSON_FILES:
        if os.path.isfile(path):
            try:
                j = read_json_file(path)
                if isinstance(j.get("aircraft"), list):
                    return j, "file " + path
            except Exception:
                pass
    for url in JSON_URLS:
        try:
            j = read_json_url(url)
            if isinstance(j.get("aircraft"), list):
                return j, "url " + url
        except Exception:
            pass
    if sbs.connected or sbs.ac:
        return {
            "now": time.time(),
            "messages": sbs.count,
            "aircraft": sbs.snapshot(),
        }, "sbs " + (sbs.endpoint or "30003")
    return None, None


def page_path():
    for name in PAGE_NAMES:
        p = os.path.join(HERE, name)
        if os.path.isfile(p):
            return p
    return None


def render_page(path):
    """Read the page and inject environment config before </head>."""
    with open(path, "rb") as f:
        html = f.read()
    env = {k: v for k, v in PAGE_ENV.items() if v not in (None, "")}
    if not env:
        return html
    # numbers should be numbers, not strings, in the injected JS
    for k in ("lat", "lon"):
        if k in env:
            try:
                env[k] = float(env[k])
            except ValueError:
                del env[k]
    inject = ("<script>window.__SKYWATCH_ENV__=" +
              json.dumps(env) + ";</script></head>").encode()
    if b"</head>" in html:
        return html.replace(b"</head>", inject, 1)
    return inject + html


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------
class Handler(BaseHTTPRequestHandler):
    server_version = "SkywatchBridge/2.0"

    def do_HEAD(self):
        self.do_GET()

    def do_GET(self):
        path = self.path.split("?", 1)[0]
        if path in ("/", "/index.html", "/skywatch.html"):
            p = page_path()
            if p:
                self._send(200, "text/html; charset=utf-8", render_page(p))
            else:
                self._send(404, "text/plain; charset=utf-8",
                           b"Put skywatch.html in the same folder as bridge.py")
        elif path == "/data/aircraft.json":
            payload, source = build_payload()
            if payload is None:
                err = json.dumps({"now": time.time(), "aircraft": [],
                                  "error": "no decoder output found; if using "
                                  "fr24feed's own decoder, set bs=\"yes\" in "
                                  "/etc/fr24feed.ini and restart fr24feed"}).encode()
                self._send(503, "application/json", err)
            else:
                payload["bridge_source"] = source
                self._send(200, "application/json",
                           json.dumps(payload, separators=(",", ":")).encode())
        else:
            self._send(404, "text/plain; charset=utf-8", b"not found")

    def _send(self, code, ctype, body):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    print("Skywatch bridge v3 (container-ready)")
    print("  serving on : http://0.0.0.0:%d/" % PORT)
    print("  ADS-B host : %s  (SBS port %d)" % (ADSB_HOST, ADSB_SBS_PORT))
    print("  sources    : aircraft.json (file/http), then BaseStation")
    ThreadingHTTPServer(("0.0.0.0", PORT), Handler).serve_forever()
