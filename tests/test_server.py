"""HTTP endpoints of the local server, with the PUBG API mocked."""
import copy
import gzip
import http.client
import json
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app  # noqa: E402
import heatmap  # noqa: E402
from db import Database  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"
TELEMETRY = json.loads((FIXTURES / "telemetry_v2.json").read_text(encoding="utf-8"))
SECRET = "test-secret-key-0123456789"


def make_match(match_id, map_name="Baltic_Main"):
    match = json.loads((FIXTURES / "match.json").read_text(encoding="utf-8"))
    match["data"]["id"] = match_id
    match["data"]["attributes"]["mapName"] = map_name
    for it in match["included"]:
        if it["type"] == "asset":
            it["attributes"]["URL"] = f"https://telemetry-cdn.pubg.com/bluehole-pubg/steam/{match_id}.json"
        if it["type"] == "participant" and it["attributes"]["stats"]["playerId"] == "account.a":
            it["attributes"]["stats"]["name"] = "Alpha"
    return match


class FakePubg:
    """Stands in for api.pubg.com and the telemetry CDN."""

    def __init__(self, match_ids):
        self.match_ids = list(match_ids)
        self.calls = []
        self.telemetry_calls = []
        self.lock = threading.Lock()

    def api_get(self, url, params=None):
        with self.lock:
            self.calls.append(url)
        if url.endswith("/players"):
            return {"data": [{"id": "account.a", "attributes": {"name": "Alpha"},
                              "relationships": {"matches": {"data": [{"id": m} for m in self.match_ids]}}}]}
        mid = url.rsplit("/", 1)[1]
        return make_match(mid, "Desert_Main" if mid.endswith("d") else "Baltic_Main")

    def session_get(self, url, **kwargs):
        with self.lock:
            self.telemetry_calls.append(url)
        # The telemetry belongs to "Alpha"; give it the account id used by the match
        events = copy.deepcopy(TELEMETRY)
        for ev in events:
            for k in ("character", "attacker", "victim", "killer", "finisher", "dBNOMaker"):
                if isinstance(ev.get(k), dict) and ev[k].get("accountId") == "acc_Alpha":
                    ev[k]["accountId"] = "account.a"
        body = json.dumps(events).encode()
        res = mock.Mock(ok=True, status_code=200, is_redirect=False)
        res.iter_content.return_value = [body[i:i + 4096] for i in range(0, len(body), 4096)]
        return res

    def player_lookups(self):
        return sum(1 for c in self.calls if c.endswith("/players"))

    def match_fetches(self):
        return [c.rsplit("/", 1)[1] for c in self.calls if "/matches/" in c]


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.db = Database.in_dir(self.tmp.name)
        self.fake = FakePubg(["m1", "m2", "m3d"])
        patches = [
            mock.patch.object(app, "DB", self.db),
            mock.patch.object(app, "API_KEY", SECRET),
            mock.patch.object(app, "api_get", side_effect=self.fake.api_get),
            mock.patch.object(app.SESSION, "get", side_effect=self.fake.session_get),
            mock.patch.object(app, "MATCH_STORAGE", []),
            mock.patch.object(app, "PREFETCHER", mock.Mock()),
        ]
        for p in patches:
            p.start()
            self.addCleanup(p.stop)
        self.server = app.ThreadingHTTPServer(("127.0.0.1", 0), app.PubgProxyHandler)
        self.port = self.server.server_address[1]
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.tmp.cleanup()

    def request(self, method, path, body=None, headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        h = {"Host": f"127.0.0.1:{self.port}"}
        if body is not None:
            body = json.dumps(body).encode() if not isinstance(body, bytes) else body
            h["Content-Type"] = "application/json"
        h.update(headers or {})
        conn.request(method, path, body=body, headers=h)
        res = conn.getresponse()
        data = res.read()
        conn.close()
        if res.getheader("Content-Encoding") == "gzip":
            data = gzip.decompress(data)
        self.assertNotIn(SECRET.encode(), data)  # the key never reaches the browser
        return res, data

    def get_json(self, path, status=200):
        res, data = self.request("GET", path)
        self.assertEqual(res.status, status, data)
        return json.loads(data)

    def search(self, **body):
        res, data = self.request("POST", "/start_analysis", dict({"nickname": "alpha", "platform": "steam", "count": 5}, **body))
        return json.loads(data)

    # --- search & dedup ---------------------------------------------------
    def test_search_stores_matches_once(self):
        self.assertEqual(self.search(), {"success": True, "matches": 3})
        self.assertEqual(sorted(self.fake.match_fetches()), ["m1", "m2", "m3d"])
        self.assertEqual(self.db.counts()["matches"], 3)
        app.PREFETCHER.enqueue.assert_called_with(["m1", "m2", "m3d"])

        # Repeated search: the player is looked up again, matches come from the database
        self.fake.match_ids = ["m4", "m1", "m2", "m3d"]
        self.assertEqual(self.search(), {"success": True, "matches": 4})
        self.assertEqual(sorted(self.fake.match_fetches()), ["m1", "m2", "m3d", "m4"])
        self.assertEqual(self.fake.player_lookups(), 2)
        with self.db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0], 4)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM participants WHERE account_id = 'account.a'").fetchone()[0], 4)

        recent = self.get_json("/matches_list")
        self.assertEqual([m["match_id"] for m in recent], ["m4", "m1", "m2", "m3d"])
        meta = self.get_json("/match_meta?i=1")
        self.assertEqual((meta["match_id"], meta["searched_nick"], meta["account_id"]), ("m4", "Alpha", "account.a"))
        self.assertEqual([t["name"] for t in meta["team_stats"]], ["Alpha", "Bravo"])

    def test_search_validation(self):
        self.assertIn("nickname", self.search(nickname="")["error"])
        self.assertIn("Invalid nickname", self.search(nickname="a,b,c")["error"])
        self.assertIn("Invalid nickname", self.search(nickname="<script>")["error"])
        self.assertIn("platform", self.search(platform="../../x")["error"])
        self.assertEqual(self.fake.calls, [])

    def test_post_requires_json_same_origin_and_small_body(self):
        res, _ = self.request("POST", "/start_analysis", b'{"nickname":"alpha"}', {"Content-Type": "text/plain"})
        self.assertEqual(res.status, 415)
        res, _ = self.request("POST", "/start_analysis", {"nickname": "alpha"}, {"Origin": "https://evil.example"})
        self.assertEqual(res.status, 403)
        res, _ = self.request("POST", "/start_analysis", b"x" * (app.MAX_BODY_BYTES + 1))
        self.assertEqual(res.status, 400)
        res, _ = self.request("POST", "/start_analysis", b"[1]")
        self.assertEqual(res.status, 400)
        res, data = self.request("POST", "/start_analysis", {"nickname": "alpha"}, {"Origin": f"http://localhost:{self.port}"})
        self.assertEqual((res.status, json.loads(data)["success"]), (200, True))

    def test_dns_rebinding_host_is_rejected(self):
        res, _ = self.request("GET", "/mode", headers={"Host": "attacker.example"})
        self.assertEqual(res.status, 403)
        res, _ = self.request("GET", "/mode", headers={"Host": f"localhost:{self.port}"})
        self.assertEqual(res.status, 200)

    def test_security_headers_and_mode(self):
        res, data = self.request("GET", "/mode")
        self.assertEqual(res.getheader("X-Content-Type-Options"), "nosniff")
        self.assertEqual(res.getheader("X-Frame-Options"), "DENY")
        self.assertEqual(json.loads(data), {"mode": "server", "api_key": True,
                                            "stored": {"matches": 0, "telemetry": 0, "players": 0}})

    # --- telemetry ----------------------------------------------------------
    def test_telemetry_downloaded_once_then_served_from_db(self):
        self.search()
        for _ in range(2):
            res, data = self.request("GET", "/fetch_telemetry?match_id=m1", headers={"Accept-Encoding": "gzip"})
            self.assertEqual(res.status, 200)
            self.assertEqual(res.getheader("Content-Encoding"), "gzip")
            events = json.loads(data)
            self.assertTrue(all("common" not in e for e in events))
            self.assertEqual(len(events), len(TELEMETRY))
        self.assertEqual(self.fake.telemetry_calls, ["https://telemetry-cdn.pubg.com/bluehole-pubg/steam/m1.json"])
        # Without gzip support the server decompresses
        res, data = self.request("GET", "/fetch_telemetry?match_id=m1")
        self.assertIsNone(res.getheader("Content-Encoding"))
        self.assertEqual(len(json.loads(data)), len(TELEMETRY))

    def test_telemetry_rejects_bad_ids_and_hosts(self):
        self.assertIn("Invalid", self.get_json("/fetch_telemetry?match_id=../../etc/passwd", 400)["error"])
        self.get_json("/fetch_telemetry?match_id=unknown", 404)
        # A stored match whose telemetry URL is not on the official CDN is never downloaded (SSRF)
        evil = app.normalize_match(make_match("evil"))
        evil["telemetry"] = "http://169.254.169.254/latest/meta-data"
        self.db.save_match(evil, "steam")
        self.get_json("/fetch_telemetry?match_id=evil", 502)
        self.assertEqual(self.fake.telemetry_calls, [])

    def test_old_url_parameter_is_gone(self):
        self.get_json("/fetch_telemetry?url=https://telemetry-cdn.pubg.com/x.json", 400)

    # --- history ------------------------------------------------------------
    def test_history_list_filters_and_meta(self):
        self.fake.match_ids = [f"h{i:02d}" for i in range(12)] + ["x1d"]
        self.search(count=20)
        page = self.get_json("/api/history?per_page=5&page=2")
        self.assertEqual((page["total"], page["pages"], page["page"], len(page["items"])), (13, 3, 2, 5))
        item = page["items"][0]
        self.assertEqual(set(item) >= {"match_id", "account_id", "map_name", "date", "kills", "rank", "has_telemetry"}, True)
        self.assertEqual(self.get_json("/api/history?map=Desert_Main")["total"], 1)
        self.assertEqual(self.get_json("/api/history?player=Alpha&mode=squad-fpp")["total"], 13)
        self.assertEqual(self.get_json("/api/history?player=nobody")["total"], 0)
        self.get_json("/api/history?map=Nowhere", 400)
        self.get_json("/api/history?mode=<b>", 400)
        self.get_json("/api/history?page=abc", 400)
        self.get_json("/api/history?player=%27%20OR%201%3D1--", 400)

        filters = self.get_json("/api/history/filters")
        self.assertEqual(filters["players"][0]["name"], "Alpha")
        self.assertIn({"id": "Desert_Main", "name": "Miramar"}, filters["maps"])
        self.assertEqual(filters["counts"]["matches"], 13)

        meta = self.get_json(f"/match_meta?match_id={item['match_id']}&account_id=account.a")
        self.assertEqual((meta["match_id"], meta["searched_nick"]), (item["match_id"], "Alpha"))
        self.get_json(f"/match_meta?match_id={item['match_id']}&account_id=account.zzz", 404)
        self.get_json("/match_meta?match_id=nope&account_id=account.a", 404)
        self.get_json("/match_meta?match_id=a/b&account_id=account.a", 400)

    # --- heatmap ------------------------------------------------------------
    def test_heatmap_aggregates_stored_matches_on_map(self):
        self.search()
        empty = self.get_json("/api/heatmap?account_id=account.a&map=Baltic_Main")
        self.assertEqual((empty["matches"], empty["stored_matches"], empty["missing_telemetry"]), (0, 2, 2))
        self.assertEqual(sorted(app.PREFETCHER.enqueue.call_args.args[0]), ["m1", "m2"])

        for mid in ("m1", "m2", "m3d"):
            app.ensure_telemetry(mid)
        hm = self.get_json("/api/heatmap?account_id=account.a&map=Baltic_Main")
        self.assertEqual((hm["matches"], hm["stored_matches"], hm["missing_telemetry"]), (2, 2, 0))
        single = heatmap.extract_points(self.db.get_telemetry("m1"), {"account_id": "account.a", "name": "Alpha"})
        for layer in heatmap.LAYERS:
            self.assertEqual(len(hm["layers"][layer]), 2 * len(single[layer]), layer)
        self.assertEqual(len(hm["layers"]["landing"]), 2)
        # Cached per (match, player) after the first request
        self.assertIsNotNone(self.db.get_heat_points("m1", "account.a"))
        self.assertEqual(self.get_json("/api/heatmap?account_id=account.a&map=Desert_Main")["matches"], 1)
        self.get_json("/api/heatmap?account_id=account.a&map=Mars", 400)
        self.get_json("/api/heatmap?account_id=&map=Baltic_Main", 400)

    # --- static files ---------------------------------------------------------
    def test_static_files_and_traversal(self):
        res, data = self.request("GET", "/heatmap.js")
        self.assertEqual(res.status, 200)
        self.assertIn(b"PubgHeatmap", data)
        for bad in ("/app.py", "/../app.py", "/data/../app.py", "/storage/pubg-tracker.sqlite3", "/.env"):
            res, _ = self.request("GET", bad)
            self.assertEqual(res.status, 404, bad)
        res, _ = self.request("GET", "/")
        self.assertEqual((res.status, res.getheader("Location")), (302, "/start"))


class PrefetcherTest(unittest.TestCase):
    def test_jobs_added_while_the_worker_exits_still_run(self):
        done, injected = [], []
        pf = app.TelemetryPrefetcher()
        orig_get = pf.queue.get

        def get(timeout=None):
            # Simulate the worker timing out on an empty queue, then a job arriving before it decides to exit
            if not injected and pf.queue.empty():
                injected.append(True)
                pf.queue.put("late")
                with pf.lock:
                    pf.pending.add("late")
                raise app.queue.Empty
            return orig_get(timeout=0.2)

        pf.queue.get = get
        with mock.patch.object(app, "ensure_telemetry", side_effect=lambda mid: done.append(mid)):
            pf.enqueue(["m1"])
            pf.thread.join(5)
        self.assertEqual(sorted(done), ["late", "m1"])
        self.assertIsNone(pf.thread)
        self.assertEqual(pf.pending, set())


if __name__ == "__main__":
    unittest.main()
