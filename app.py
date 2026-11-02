import ipaddress
import json
import logging
import os
import re
import threading
import time
import http.server
import socketserver
import webbrowser
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse, parse_qs

import requests


BASE_DIR = Path(__file__).resolve().parent
MAPS_DIR = BASE_DIR / "maps"
DATA_DIR = BASE_DIR / "data"

log = logging.getLogger("pubg-tracker")


def load_local_env() -> None:
    env_path = BASE_DIR / ".env"
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() and key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip().strip("\"'")


load_local_env()
API_KEY = os.environ.get("PUBG_API_KEY", "").strip()
PORT = int(os.environ.get("PORT", "8000") or 8000)
HOST = os.environ.get("PUBG_TRACKER_HOST", "127.0.0.1")

PLATFORMS = {"steam", "kakao", "psn", "xbox", "stadia", "console"}
MAX_MATCHES = 20
MAX_BODY_BYTES = 16 * 1024
MAX_TELEMETRY_BYTES = 400 * 1024 * 1024
MAX_RETRY_WAIT = 60

# PUBG nicknames are letters, digits, "-" and "_"; a comma would query several players at once
NICKNAME_RE = re.compile(r"^[\w.-]{2,64}$")
ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}$")  # match ids (UUIDs) and account ids (account.<hex>, ai.<n>)
MODE_RE = re.compile(r"^[a-z0-9-]{1,40}$")


MAP_NAMES = {
    "Baltic_Main": "Erangel", "Erangel_Main": "Erangel", "Desert_Main": "Miramar",
    "Savage_Main": "Sanhok", "Summerland_Main": "Karakin", "Chimera_Main": "Paramo",
    "Tiger_Main": "Taego", "Kiki_Main": "Deston", "Neon_Main": "Rondo",
    "DihorOtok_Main": "Vikendi", "Heaven_Main": "Haven", "Range_Main": "Camp Jackal",
}
# Telemetry map id -> image name in github.com/pubg/api-assets (Assets/Maps/<name>_{Low,High}_Res.png)
MAP_ASSETS = {
    "Baltic_Main": "Erangel_Main", "Erangel_Main": "Erangel_Main", "Desert_Main": "Miramar_Main",
    "Savage_Main": "Sanhok_Main", "Summerland_Main": "Karakin_Main", "Chimera_Main": "Paramo_Main",
    "Tiger_Main": "Taego_Main", "Kiki_Main": "Deston_Main", "Neon_Main": "Rondo_Main",
    "DihorOtok_Main": "Vikendi_Main", "Heaven_Main": "Haven_Main", "Range_Main": "Camp_Jackal_Main",
}
MAP_URLS = {
    # The low-res image lives in the git tree; the high-res one is stored in Git LFS.
    "low": "https://raw.githubusercontent.com/pubg/api-assets/master/Assets/Maps/{name}_Low_Res.png",
    "high": "https://media.githubusercontent.com/media/pubg/api-assets/master/Assets/Maps/{name}_High_Res.png",
}
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"
# Only the official telemetry CDN may be downloaded (no other *.pubg.com host, no redirects)
TELEMETRY_HOSTS = frozenset({"telemetry-cdn.pubg.com"})

MATCH_STORAGE = []
STORAGE_LOCK = threading.Lock()
SESSION = requests.Session()

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1"}
ALLOWED_HOSTS = set(LOCAL_HOSTS)


class PubgApiError(Exception):
    pass


class LRUCache:
    def __init__(self, size):
        self.size, self.items, self.lock = size, OrderedDict(), threading.Lock()

    def get(self, key):
        with self.lock:
            if key in self.items:
                self.items.move_to_end(key)
                return self.items[key]
        return None

    def put(self, key, value):
        with self.lock:
            self.items[key] = value
            self.items.move_to_end(key)
            while len(self.items) > self.size:
                self.items.popitem(last=False)


TELEMETRY_CACHE = LRUCache(4)
MAP_LOCKS = {}
MAP_LOCKS_GUARD = threading.Lock()


def _sleep(seconds):
    time.sleep(seconds)


def _retry_after(res):
    """Seconds to wait after a 429: Retry-After, else X-RateLimit-Reset (epoch seconds)."""
    try:
        wait = float(res.headers.get("Retry-After", "") or 0)
    except ValueError:
        wait = 0
    if not wait:
        try:
            wait = float(res.headers.get("X-RateLimit-Reset", "") or 0) - time.time()
        except ValueError:
            wait = 0
    return min(MAX_RETRY_WAIT, max(1.0, wait or 5.0))


def api_get(url, params=None, attempts=4):
    """GET from the PUBG API with retries for 429 (rate limit), 5xx and network errors.

    The key only goes into the Authorization header; it is never logged or put
    into an error message.
    """
    headers = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/vnd.api+json"}
    res = None
    for attempt in range(attempts):
        last = attempt == attempts - 1
        try:
            res = SESSION.get(url, headers=headers, params=params, timeout=30)
        except requests.RequestException as e:
            if last:
                raise PubgApiError(f"Could not reach the PUBG API ({e.__class__.__name__})") from None
            _sleep(2 ** attempt)
            continue
        if res.status_code == 429 and not last:
            wait = _retry_after(res)
            log.info("PUBG API rate limit, retrying in %.0fs", wait)
            _sleep(wait)
            continue
        if res.status_code >= 500 and not last:
            _sleep(2 ** attempt)
            continue
        break
    if res.status_code == 401:
        raise PubgApiError("PUBG API rejected the key (401). Check PUBG_API_KEY.")
    if res.status_code == 404:
        raise PubgApiError("Not found (404). Check the nickname (it is case-sensitive) and the platform.")
    if res.status_code == 429:
        raise PubgApiError("PUBG API rate limit reached (429). Wait a minute and try again.")
    if not res.ok:
        raise PubgApiError(f"PUBG API error {res.status_code}")
    return res.json()


def format_date(created_at):
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S.%fZ"):
        try:
            return datetime.strptime(created_at, fmt).strftime("%d.%m %H:%M")
        except (TypeError, ValueError):
            continue
    return created_at or ""


def participant_stats(stats):
    return {
        "name": stats.get("name", ""),
        "kills": stats.get("kills", 0),
        "knocks": stats.get("DBNOs", 0),
        "assists": stats.get("assists", 0),
        "headshots": stats.get("headshotKills", 0),
        "damage": int(round(stats.get("damageDealt", 0))),
        "revives": stats.get("revives", 0),
        "longest": int(round(stats.get("longestKill", 0))),
        "survived": int(stats.get("timeSurvived", 0)),
        "rank": stats.get("winPlace", 0),
        "dist": int(stats.get("walkDistance", 0) + stats.get("rideDistance", 0) + stats.get("swimDistance", 0)),
    }


def summarize_match(match, player_id, searched_name):
    """Turn a /matches/{id} response into what the UI needs for one player's squad."""
    included = match.get("included", [])
    participants = {it["id"]: it for it in included if it["type"] == "participant"}
    me = next((p for p in participants.values() if p["attributes"]["stats"].get("playerId") == player_id), None)
    if me is None:
        raise PubgApiError("Player is missing from match roster")

    roster = next(
        (ro for ro in included if ro["type"] == "roster"
         and me["id"] in {p["id"] for p in ro["relationships"]["participants"]["data"]}),
        None,
    )
    refs = roster["relationships"]["participants"]["data"] if roster else [{"id": me["id"]}]
    team = [participant_stats(participants[r["id"]]["attributes"]["stats"]) for r in refs if r["id"] in participants]
    my_name = me["attributes"]["stats"].get("name") or searched_name
    # The searched player first, then teammates by kills
    team.sort(key=lambda t: (t["name"] != my_name, -t["kills"], t["name"].lower()))

    attrs = match["data"]["attributes"]
    telemetry = next((it["attributes"]["URL"] for it in included if it["type"] == "asset"), None)
    my_stats = participant_stats(me["attributes"]["stats"])
    return {
        "match_id": match["data"].get("id"),
        "telemetry": telemetry,
        "searched_nick": my_name,
        "team_stats": team,
        "map": attrs.get("mapName"),
        "map_name": MAP_NAMES.get(attrs.get("mapName"), attrs.get("mapName")),
        "mode": attrs.get("gameMode", ""),
        "duration": attrs.get("duration", 0),
        "date": format_date(attrs.get("createdAt")),
        "rank": my_stats["rank"],
        "kills": my_stats["kills"],
        "knocks": my_stats["knocks"],
        "damage": my_stats["damage"],
        "players": len(participants),
    }


def validate_search(nickname, platform):
    if not nickname:
        return "Enter a player nickname"
    if not NICKNAME_RE.match(nickname):
        return "Invalid nickname (letters, digits, '-', '_' and '.' only)"
    if platform not in PLATFORMS:
        return "Unknown platform"
    return None


def load_matches(nickname, platform, count):
    base = f"https://api.pubg.com/shards/{platform}"
    r = api_get(f"{base}/players", params={"filter[playerNames]": nickname})
    if not r.get("data"):
        raise PubgApiError("Player not found")
    player = r["data"][0]
    pid = player["id"]
    name = player["attributes"].get("name", nickname)
    mids = [m["id"] for m in player["relationships"]["matches"]["data"][:count] if ID_RE.match(str(m.get("id", "")))]
    if not mids:
        raise PubgApiError("No matches in the last 14 days")

    def fetch(mid):
        return summarize_match(api_get(f"{base}/matches/{mid}"), pid, name)

    # Match objects are not rate limited, so they can be fetched in parallel
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda mid: _safe(fetch, mid), mids))
    matches = [m for m in results if m is not None]
    if not matches:
        raise PubgApiError("Could not load any match")
    return matches


def _safe(fn, arg):
    try:
        return fn(arg)
    except Exception as e:  # one broken match must not break the whole list
        log.warning("Skipping match %s: %s", arg, e.__class__.__name__ if not isinstance(e, PubgApiError) else e)
        return None


def is_allowed_telemetry_url(url):
    try:
        p = urlparse(url)
        port = p.port
    except (ValueError, TypeError):
        return False
    return (p.scheme == "https" and p.hostname in TELEMETRY_HOSTS and port in (None, 443)
            and not p.username and not p.password)


def download_telemetry(url):
    if not is_allowed_telemetry_url(url):
        raise PubgApiError("Telemetry URL is not on the official PUBG telemetry CDN")
    res = SESSION.get(url, timeout=(15, 300), stream=True, allow_redirects=False)
    try:
        if not res.ok or res.is_redirect:
            raise PubgApiError(f"Telemetry download failed ({res.status_code})")
        buf = bytearray()
        for chunk in res.iter_content(1 << 20):
            buf += chunk
            if len(buf) > MAX_TELEMETRY_BYTES:
                raise PubgApiError("Telemetry file is too large")
    finally:
        res.close()
    events = json.loads(bytes(buf))
    if not isinstance(events, list):
        raise PubgApiError("Unexpected telemetry format")
    return events


def get_map_image(map_id, quality):
    """Return PNG bytes for a map, downloading and caching it in maps/ on first use."""
    name = MAP_ASSETS.get(map_id)
    if not name or quality not in MAP_URLS:
        return None
    local = MAPS_DIR / (f"{map_id}.png" if quality == "high" else f"{map_id}_low.png")
    with MAP_LOCKS_GUARD:
        lock = MAP_LOCKS.setdefault(str(local), threading.Lock())
    with lock:
        if local.exists():
            content = local.read_bytes()
            if content.startswith(PNG_MAGIC):
                return content
            local.unlink()  # broken cache (e.g. a saved 404 page)
        res = SESSION.get(MAP_URLS[quality].format(name=name), timeout=600)
        if not res.ok or not res.content.startswith(PNG_MAGIC):
            return None
        MAPS_DIR.mkdir(exist_ok=True)
        tmp = local.with_suffix(".tmp")
        tmp.write_bytes(res.content)
        tmp.replace(local)
        return res.content


def load_dictionaries():
    out = {}
    for key in ("damageCauserName", "damageTypeCategory"):
        try:
            out[key] = json.loads((DATA_DIR / f"{key}.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            out[key] = {}
    return out


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


STATIC_FILES = {
    "/start": ("start.html", "text/html; charset=utf-8"),
    "/start.html": ("start.html", "text/html; charset=utf-8"),
    "/index.html": ("index.html", "text/html; charset=utf-8"),
    "/telemetry.js": ("telemetry.js", "application/javascript; charset=utf-8"),
    "/pubg-data.js": ("pubg-data.js", "application/javascript; charset=utf-8"),
}
# Bundled JSON (dictionaries and demo matches) served as-is: /data/<name>.json, /demo/<name>.json
STATIC_DIRS = ("data", "demo")


def static_json_path(url_path):
    parts = url_path.strip("/").split("/")
    if len(parts) != 2 or parts[0] not in STATIC_DIRS:
        return None
    name = parts[1]
    if not name.endswith(".json") or not all(c.isalnum() or c in "-_." for c in name) or name.startswith("."):
        return None
    path = BASE_DIR / parts[0] / name
    return path if path.is_file() else None


def _host_only(value):
    """'example.com:8000' / '[::1]:8000' -> host name in lower case."""
    value = (value or "").strip().lower()
    if value.startswith("["):
        return value[1:value.find("]")] if "]" in value else ""
    return value.rsplit(":", 1)[0] if value.count(":") == 1 else value


def is_allowed_host(host_header):
    if "*" in ALLOWED_HOSTS:
        return True
    return _host_only(host_header) in ALLOWED_HOSTS


class PubgProxyHandler(http.server.BaseHTTPRequestHandler):
    server_version = "PUBGTracker"
    sys_version = ""

    def end_headers(self):
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        super().end_headers()

    def _check_host(self):
        # Blocks DNS rebinding: another website resolving its name to 127.0.0.1
        if not is_allowed_host(self.headers.get("Host")):
            self.send_error(403, "Host not allowed")
            return False
        return True

    def do_GET(self):
        if not self._check_host():
            return
        parsed = urlparse(self.path)
        query = parse_qs(parsed.query)
        try:
            if parsed.path == "/matches_list":
                with STORAGE_LOCK:
                    data = [
                        {"id": i + 1, "map": m["map_name"], "mode": m["mode"], "date": m["date"],
                         "rank": m["rank"], "kills": m["kills"], "knocks": m["knocks"], "damage": m["damage"]}
                        for i, m in enumerate(MATCH_STORAGE)
                    ]
                self._send_json(data)
            elif parsed.path == "/match_meta":
                try:
                    idx = int(query.get("i", ["0"])[0]) - 1
                except ValueError:
                    idx = -1
                with STORAGE_LOCK:
                    meta = MATCH_STORAGE[idx] if 0 <= idx < len(MATCH_STORAGE) else None
                if meta is None:
                    self.send_error(404)
                else:
                    self._send_json(meta)
            elif parsed.path == "/fetch_telemetry":
                url = query.get("url", [""])[0]
                if not is_allowed_telemetry_url(url):
                    self.send_error(400, "Invalid telemetry URL")
                    return
                content = TELEMETRY_CACHE.get(url)
                if content is None:
                    try:
                        content = json.dumps(download_telemetry(url)).encode()
                    except PubgApiError as e:
                        self.send_error(502, str(e))
                        return
                    TELEMETRY_CACHE.put(url, content)
                self._send_raw(content, "application/json")
            elif parsed.path == "/fetch_image":
                content = get_map_image(query.get("map_id", [""])[0], query.get("q", ["high"])[0])
                if content is None:
                    self.send_error(404, "Map image not available")
                else:
                    self._send_raw(content, "image/png", cache=True)
            elif parsed.path == "/dictionaries":
                self._send_json(load_dictionaries())
            elif parsed.path == "/mode":
                self._send_json({"mode": "server", "api_key": bool(API_KEY)})
            elif static_json_path(parsed.path):
                self._send_raw(static_json_path(parsed.path).read_bytes(), "application/json")
            elif parsed.path in STATIC_FILES:
                fname, ctype = STATIC_FILES[parsed.path]
                self._send_file(fname, ctype)
            else:
                if not MATCH_STORAGE:
                    self.send_response(302)
                    self.send_header("Location", "/start")
                    self.end_headers()
                else:
                    self._send_file("index.html", "text/html; charset=utf-8")
        except (BrokenPipeError, ConnectionResetError):
            pass
        except requests.RequestException as e:
            self.send_error(502, f"Upstream error: {e.__class__.__name__}")
        except Exception:
            log.exception("Request failed: %s", parsed.path)
            self.send_error(500, "Internal error")

    def do_POST(self):
        if not self._check_host():
            return
        if self.path != "/start_analysis":
            self.send_error(404)
            return
        # CSRF: a cross-site page cannot send application/json without a CORS preflight,
        # and a browser always sends Origin on cross-site POSTs.
        origin = self.headers.get("Origin")
        if origin and not is_allowed_host(urlparse(origin).netloc):
            self._send_json({"success": False, "error": "Cross-origin request blocked"}, status=403)
            return
        if not self.headers.get("Content-Type", "").lower().startswith("application/json"):
            self._send_json({"success": False, "error": "Expected application/json"}, status=415)
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            length = -1
        if not 0 <= length <= MAX_BODY_BYTES:
            self._send_json({"success": False, "error": "Invalid request"}, status=400)
            return
        try:
            body = json.loads(self.rfile.read(length) or b"{}")
            if not isinstance(body, dict):
                raise ValueError
        except ValueError:
            self._send_json({"success": False, "error": "Invalid request"}, status=400)
            return
        if not API_KEY:
            self._send_json({"success": False, "error": "PUBG_API_KEY is not configured"})
            return


        nickname = str(body.get("nickname", "")).strip()
        platform = str(body.get("platform", "steam")).strip().lower()
        try:
            count = max(1, min(MAX_MATCHES, int(body.get("count", 5))))
        except (TypeError, ValueError):
            count = 5
        error = validate_search(nickname, platform)
        if error:
            self._send_json({"success": False, "error": error})
            return

        try:
            matches = load_matches(nickname, platform, count)
        except PubgApiError as e:
            self._send_json({"success": False, "error": str(e)})
            return
        except requests.RequestException as e:
            self._send_json({"success": False, "error": f"Network error: {e.__class__.__name__}"})
            return
        except Exception:
            log.exception("Search failed")
            self._send_json({"success": False, "error": "Unexpected error, see the server log"})
            return

        with STORAGE_LOCK:
            MATCH_STORAGE[:] = matches
        self._send_json({"success": True, "matches": len(matches)})

    def _send_file(self, fname, ctype):
        try:
            content = (BASE_DIR / fname).read_bytes()
        except OSError:
            self.send_error(404)
            return
        self._send_raw(content, ctype)

    def _send_json(self, data, status=200):
        self._send_raw(json.dumps(data).encode(), "application/json", status=status)

    def _send_raw(self, content, ctype, cache=False, status=200):
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *args):
        return


def _is_loopback(host):
    if host == "localhost":
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def main():
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    extra = {h.strip().lower() for h in os.environ.get("PUBG_TRACKER_ALLOWED_HOSTS", "").split(",") if h.strip()}
    ALLOWED_HOSTS.update(extra)
    if not _is_loopback(HOST):
        ALLOWED_HOSTS.add(HOST.lower())
        log.warning("Listening on %s: the tracker has no login, anyone who can reach this port can use it.", HOST)
    server = ThreadingHTTPServer((HOST, PORT), PubgProxyHandler)
    print(f"PUBG Tracker is running at http://localhost:{PORT}")
    if not API_KEY:
        print("Warning: PUBG_API_KEY is not set (environment or .env file)")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    if _is_loopback(HOST):
        webbrowser.open(f"http://localhost:{PORT}/start")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.shutdown()
        print("\nServer stopped")


if __name__ == "__main__":
    main()
