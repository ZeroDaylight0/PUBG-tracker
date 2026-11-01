"""Run with: python -m unittest discover tests"""
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import app  # noqa: E402


def participant(pid, name, player_id, **stats):
    base = {"name": name, "playerId": player_id, "kills": 0, "DBNOs": 0, "assists": 0, "headshotKills": 0,
            "damageDealt": 0.0, "revives": 0, "longestKill": 0.0, "timeSurvived": 0, "winPlace": 5,
            "walkDistance": 0.0, "rideDistance": 0.0, "swimDistance": 0.0}
    base.update(stats)
    return {"type": "participant", "id": pid, "attributes": {"stats": base}}


MATCH = {
    "data": {"id": "m1", "attributes": {"mapName": "Baltic_Main", "gameMode": "squad-fpp",
                                         "createdAt": "2026-10-30T12:34:56Z", "duration": 1800}},
    "included": [
        participant("p1", "Alpha", "account.a", kills=3, DBNOs=4, damageDealt=412.7, walkDistance=2000.5, rideDistance=1000.2),
        participant("p2", "Bravo", "account.b", kills=5, DBNOs=2),
        participant("p3", "Enemy", "account.e", kills=1, winPlace=1),
        {"type": "roster", "id": "r1", "relationships": {"participants": {"data": [{"id": "p2"}, {"id": "p1"}]}}},
        {"type": "roster", "id": "r2", "relationships": {"participants": {"data": [{"id": "p3"}]}}},
        {"type": "asset", "id": "a1", "attributes": {"URL": "https://telemetry-cdn.pubg.com/bluehole-pubg/steam/x.json"}},
    ],
}


class SummarizeMatchTest(unittest.TestCase):
    def test_squad_stats_and_knocks(self):
        m = app.summarize_match(MATCH, "account.a", "alpha")
        self.assertEqual(m["searched_nick"], "Alpha")  # canonical name, not the typed one
        self.assertEqual([t["name"] for t in m["team_stats"]], ["Alpha", "Bravo"])
        alpha = m["team_stats"][0]
        self.assertEqual((alpha["kills"], alpha["knocks"], alpha["damage"], alpha["dist"]), (3, 4, 413, 3000))
        self.assertEqual((m["kills"], m["knocks"], m["rank"]), (3, 4, 5))
        self.assertEqual(m["map_name"], "Erangel")
        self.assertEqual(m["date"], "30.10 12:34")
        self.assertTrue(m["telemetry"].startswith("https://telemetry-cdn.pubg.com/"))

    def test_missing_player(self):
        with self.assertRaises(app.PubgApiError):
            app.summarize_match(MATCH, "account.zzz", "x")

    def test_date_with_fraction(self):
        self.assertEqual(app.format_date("2026-10-30T12:34:56.123Z"), "30.10 12:34")


class TelemetryUrlTest(unittest.TestCase):
    def test_only_pubg_https(self):
        self.assertTrue(app.is_allowed_telemetry_url("https://telemetry-cdn.pubg.com/a.json"))
        self.assertFalse(app.is_allowed_telemetry_url("http://telemetry-cdn.pubg.com/a.json"))
        self.assertFalse(app.is_allowed_telemetry_url("https://evil.com/a.json"))
        self.assertFalse(app.is_allowed_telemetry_url("https://pubg.com.evil.com/a.json"))
        self.assertFalse(app.is_allowed_telemetry_url("file:///etc/passwd"))


class MapImageTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patch = mock.patch.object(app, "MAPS_DIR", Path(self.tmp.name))
        self.patch.start()

    def tearDown(self):
        self.patch.stop()
        self.tmp.cleanup()

    def test_rejects_unknown_map_ids(self):
        self.assertIsNone(app.get_map_image("../../etc/passwd", "high"))
        self.assertIsNone(app.get_map_image("Baltic_Main", "ultra"))

    def test_broken_cache_is_replaced_and_404_not_cached(self):
        (Path(self.tmp.name) / "Baltic_Main.png").write_bytes(b"404: Not Found")
        bad = mock.Mock(ok=False, content=b"404: Not Found")
        with mock.patch.object(app.SESSION, "get", return_value=bad) as get:
            self.assertIsNone(app.get_map_image("Baltic_Main", "high"))
            self.assertIn("Erangel_Main_High_Res.png", get.call_args[0][0])
        self.assertFalse((Path(self.tmp.name) / "Baltic_Main.png").exists())

        png = app.PNG_MAGIC + b"data"
        with mock.patch.object(app.SESSION, "get", return_value=mock.Mock(ok=True, content=png)) as get:
            self.assertEqual(app.get_map_image("Baltic_Main", "low"), png)
            self.assertIn("Erangel_Main_Low_Res.png", get.call_args[0][0])
        # Second call is served from disk
        with mock.patch.object(app.SESSION, "get") as get:
            self.assertEqual(app.get_map_image("Baltic_Main", "low"), png)
            get.assert_not_called()


class ApiErrorsTest(unittest.TestCase):
    def test_readable_errors(self):
        for code, text in ((401, "401"), (404, "nickname"), (500, "500")):
            with mock.patch.object(app.SESSION, "get", return_value=mock.Mock(status_code=code, ok=False, headers={})):
                with self.assertRaisesRegex(app.PubgApiError, text):
                    app.api_get("https://api.pubg.com/x")

    def test_load_matches_skips_broken_match(self):
        player = {"data": [{"id": "account.a", "attributes": {"name": "Alpha"},
                            "relationships": {"matches": {"data": [{"id": "m1"}, {"id": "bad"}]}}}]}

        def fake(url, params=None):
            if url.endswith("/players"):
                self.assertEqual(params, {"filter[playerNames]": "alpha"})
                return player
            if url.endswith("/m1"):
                return MATCH
            raise app.PubgApiError("boom")

        with mock.patch.object(app, "api_get", side_effect=fake):
            matches = app.load_matches("alpha", "steam", 5)
        self.assertEqual(len(matches), 1)
        self.assertEqual(matches[0]["searched_nick"], "Alpha")


class DictionaryTest(unittest.TestCase):
    def test_bundled_dictionaries(self):
        d = app.load_dictionaries()
        self.assertEqual(d["damageCauserName"]["WeapHK416_C"], "M416")
        self.assertIn("Damage_Gun", d["damageTypeCategory"])


if __name__ == "__main__":
    unittest.main()
