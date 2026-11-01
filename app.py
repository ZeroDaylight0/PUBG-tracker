import requests
import sys
import os
import json
import threading
import http.server
import socketserver
import webbrowser
from tabulate import tabulate
from datetime import datetime
from urllib.parse import urlparse, parse_qs
from pathlib import Path

def load_local_env() -> None:
    env_path = Path(__file__).with_name(".env")
    if not env_path.is_file():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        key, separator, value = line.partition("=")
        if separator and key.strip() and key.strip() not in os.environ:
            os.environ[key.strip()] = value.strip().strip("\"'")


load_local_env()
API_KEY = os.environ.get("PUBG_API_KEY", "").strip()
BASE_DIR = Path(__file__).resolve().parent

MATCH_STORAGE = []
MAP_NAMES = {
    "Baltic_Main": "Erangel", "Erangel_Main": "Erangel", "Desert_Main": "Miramar", 
    "Savage_Main": "Sanhok", "Summerland_Main": "Karakin", "Chimera_Main": "Paramo",
    "Tiger_Main": "Taego", "Kiki_Main": "Deston", "Neon_Main": "Rondo", 
    "DihorOtok_Main": "Vikendi", "Heaven_Main": "Haven"
}

class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True

class PubgProxyHandler(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == '/matches_list':
            list_data = []
            for i, m in enumerate(MATCH_STORAGE):
                list_data.append({"id": i+1, "map": MAP_NAMES.get(m['map'], m['map']), "date": m['date'], "rank": m['rank'], "kills": m['kills']})
            self._send_json(list_data)
        elif parsed.path == '/match_meta':
            idx = int(parse_qs(parsed.query).get('i', [0])[0]) - 1
            if 0 <= idx < len(MATCH_STORAGE): self._send_json(MATCH_STORAGE[idx])
            else: self.send_error(404)
        elif parsed.path == '/fetch_telemetry':
            url = parse_qs(parsed.query).get('url', [None])[0]
            if url: self._send_raw(requests.get(url).content, 'application/json')
        elif parsed.path == '/fetch_image':
            q = parse_qs(parsed.query)
            mid, url = q.get('map_id', [''])[0], q.get('url', [''])[0]
            local = BASE_DIR / "maps" / f"{mid}.png"
            if local.exists():
                with open(local, "rb") as f: self._send_raw(f.read(), 'image/png')
            else:
                res = requests.get(url)
                if not (BASE_DIR / "maps").exists(): (BASE_DIR / "maps").mkdir()
                with open(local, "wb") as f: f.write(res.content)
                self._send_raw(res.content, 'image/png')
        elif parsed.path == '/start':
            try:
                with open(BASE_DIR / "start.html", "rb") as f: self._send_raw(f.read(), 'text/html; charset=utf-8')
            except: self.send_error(404)
        else:
            if len(MATCH_STORAGE) == 0:
                self.send_response(302)
                self.send_header('Location', '/start')
                self.end_headers()
            else:
                try:
                    with open(BASE_DIR / "index.html", "rb") as f: self._send_raw(f.read(), 'text/html; charset=utf-8')
                except: self.send_error(404)

    def do_POST(self):
        if self.path == '/start_analysis':
            if not API_KEY:
                self._send_json({"success": False, "error": "PUBG_API_KEY is not configured"})
                return
            content_length = int(self.headers['Content-Length'])
            body = json.loads(self.rfile.read(content_length))

            nickname = body.get('nickname', '').strip()
            platform = body.get('platform', 'steam')
            count = body.get('count', 5)

            try:
                MATCH_STORAGE.clear()
                headers = {"Authorization": f"Bearer {API_KEY}", "Accept": "application/vnd.api+json"}
                base = f"https://api.pubg.com/shards/{platform}"

                r = requests.get(f"{base}/players?filter[playerNames]={nickname}", headers=headers).json()
                pid = r['data'][0]['id']
                mids = [m['id'] for m in r['data'][0]['relationships']['matches']['data'][:count]]

                for mid in mids:
                    m = requests.get(f"{base}/matches/{mid}", headers=headers).json()
                    participants = [it for it in m['included'] if it['type'] == 'participant']
                    me = next(p for p in participants if p['attributes']['stats']['playerId'] == pid)
                    p_stats = me['attributes']['stats']

                    my_roster = next(ro for ro in m['included'] if ro['type'] == 'roster' and me['id'] in [p['id'] for p in ro['relationships']['participants']['data']])
                    team_data = []
                    for p_ref in my_roster['relationships']['participants']['data']:
                        p_full = next(p for p in participants if p['id'] == p_ref['id'])
                        s = p_full['attributes']['stats']
                        team_data.append({"name": s['name'], "kills": s['kills'], "damage": int(s['damageDealt']), "rank": s['winPlace'], "dist": int(s['walkDistance'] + s['rideDistance'])})

                    map_id = m['data']['attributes']['mapName']
                    date_str = datetime.strptime(m['data']['attributes']['createdAt'], "%Y-%m-%dT%H:%M:%SZ").strftime("%d.%m %H:%M")

                    MATCH_STORAGE.append({
                        "telemetry": next(it['attributes']['URL'] for it in m['included'] if it['type'] == 'asset'),
                        "searched_nick": nickname, "team_stats": team_data, "map": map_id, "date": date_str, "rank": p_stats['winPlace'], "kills": p_stats['kills']
                    })

                self._send_json({"success": True})
            except Exception as e:
                self._send_json({"success": False, "error": str(e)})

    def _send_json(self, data):
        self.send_response(200); self.send_header('Content-Type', 'application/json'); self.send_header('Access-Control-Allow-Origin', '*'); self.end_headers()
        self.wfile.write(json.dumps(data).encode())
    def _send_raw(self, content, ctype):
        self.send_response(200); self.send_header('Content-Type', ctype); self.send_header('Access-Control-Allow-Origin', '*'); self.end_headers()
        self.wfile.write(content)
    def log_message(self, *args): return

if __name__ == "__main__":
    print("PUBG Tracker is running at http://localhost:8000")
    threading.Thread(target=lambda: ThreadingHTTPServer(("", 8000), PubgProxyHandler).serve_forever(), daemon=True).start()
    webbrowser.open("http://localhost:8000/start")
    try:
        while True: import time; time.sleep(1)
    except KeyboardInterrupt:
        print("\nServer stopped")
