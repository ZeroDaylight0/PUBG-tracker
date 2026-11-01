import json
import os
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
PORT = int(os.environ.get("PORT", "8000"))

PLATFORMS = {"steam", "kakao", "psn", "xbox", "stadia", "console"}
MAX_MATCHES = 20

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
TELEMETRY_HOSTS = (".pubg.com",)

MATCH_STORAGE = []
STORAGE_LOCK = threading.Lock()
SESSION = requests.Session()


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


def api_get(url, params=None):
    headers = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/vnd.api+json"}
    for attempt in range(3):
        res = SESSION.get(url, headers=headers, params=params, timeout=30)
        if res.status_code == 429 and attempt < 2:
            time.sleep(int(res.headers.get("Retry-After", "0") or 0) or 5)
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


def load_matches(nickname, platform, count):
    base = f"https://api.pubg.com/shards/{platform}"
    r = api_get(f"{base}/players", params={"filter[playerNames]": nickname})
    if not r.get("data"):
        raise PubgApiError("Player not found")
    player = r["data"][0]
    pid = player["id"]
    name = player["attributes"].get("name", nickname)
    mids = [m["id"] for m in player["relationships"]["matches"]["data"][:count]]
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
        print(f"Skipping match {arg}: {e}")
        return None


def is_allowed_telemetry_url(url):
    try:
        p = urlparse(url)
    except ValueError:
        return False
    return p.scheme == "https" and bool(p.hostname) and p.hostname.endswith(TELEMETRY_HOSTS)


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
    "/telemetry.js": ("telemetry.js", "application/javascript; charset=utf-8"),
}


class PubgProxyHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
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
                    res = SESSION.get(url, timeout=120)
                    if not res.ok:
                        self.send_error(502, f"Telemetry download failed ({res.status_code})")
                        return
                    content = res.content
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

    def do_POST(self):
        if self.path != "/start_analysis":
            self.send_error(404)
            return
        if not API_KEY:
            self._send_json({"success": False, "error": "PUBG_API_KEY is not configured"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            body = json.loads(self.rfile.read(length) or b"{}")
        except ValueError:
            self._send_json({"success": False, "error": "Invalid request"})
            return

        nickname = str(body.get("nickname", "")).strip()
        platform = str(body.get("platform", "steam")).strip().lower()
        try:
            count = max(1, min(MAX_MATCHES, int(body.get("count", 5))))
        except (TypeError, ValueError):
            count = 5
        if not nickname:
            self._send_json({"success": False, "error": "Enter a player nickname"})
            return
        if platform not in PLATFORMS:
            self._send_json({"success": False, "error": "Unknown platform"})
            return

        try:
            matches = load_matches(nickname, platform, count)
        except PubgApiError as e:
            self._send_json({"success": False, "error": str(e)})
            return
        except requests.RequestException as e:
            self._send_json({"success": False, "error": f"Network error: {e.__class__.__name__}"})
            return
        except Exception as e:
            self._send_json({"success": False, "error": f"Unexpected error: {e}"})
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

    def _send_json(self, data):
        self._send_raw(json.dumps(data).encode(), "application/json")

    def _send_raw(self, content, ctype, cache=False):
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Cache-Control", "max-age=86400" if cache else "no-store")
        self.end_headers()
        self.wfile.write(content)

    def log_message(self, *args):
        return


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), PubgProxyHandler)
    print(f"PUBG Tracker is running at http://localhost:{PORT}")
    if not API_KEY:
        print("Warning: PUBG_API_KEY is not set (environment or .env file)")
    threading.Thread(target=server.serve_forever, daemon=True).start()
    webbrowser.open(f"http://localhost:{PORT}/start")
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        server.shutdown()
        print("\nServer stopped")


if __name__ == "__main__":
    main()
