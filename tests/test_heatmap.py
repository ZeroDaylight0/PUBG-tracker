"""Heatmap point extraction (Python side; heatmap.test.js checks the browser side)."""
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import heatmap  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def load(*parts):
    return json.loads(Path(*parts).read_text(encoding="utf-8"))


class ExtractTest(unittest.TestCase):
    def test_matches_shared_expectation(self):
        events = load(FIXTURES, "telemetry_v2.json")
        expected = load(FIXTURES, "heatmap_expected.json")
        self.assertEqual(heatmap.extract_points(events, {"account_id": "acc_Alpha", "name": "Renamed"}), expected["by_account"])
        self.assertEqual(heatmap.extract_points(events, {"name": "Alpha"}), expected["by_name"])
        self.assertEqual(heatmap.extract_points(events, {"account_id": "acc_Bravo", "name": "Bravo"}), expected["bravo"])

    def test_demo_match_layers_agree_with_stats(self):
        matches = load(ROOT, "demo", "matches.json")
        for m in matches:
            events = load(ROOT, m["telemetry"])
            me = m["team_stats"][0]
            pts = heatmap.extract_points(events, {"name": me["name"]})
            self.assertEqual(len(pts["landing"]), 1)
            self.assertEqual(len(pts["knocks"]), me["knocks"])
            self.assertEqual(len(pts["kills"]), me["kills"])
            self.assertEqual(len(pts["deaths"]), 0 if me["rank"] == 1 else 1)
            self.assertGreater(len(pts["movement"]), 10)

    def test_merge_and_garbage(self):
        total = heatmap.merge({}, {"kills": [[1, 2]]})
        heatmap.merge(total, {"kills": [[3, 4]], "landing": [[5, 6]]})
        self.assertEqual(total["kills"], [[1, 2], [3, 4]])
        self.assertEqual(total["landing"], [[5, 6]])
        junk = [None, 1, {"_T": "LogPlayerKillV2"}, {"_T": "LogParachuteLanding", "character": {"name": "A", "location": {"x": "1", "y": 2}}}]
        self.assertEqual(heatmap.extract_points(junk, {"name": "A"}), {k: [] for k in heatmap.LAYERS})


if __name__ == "__main__":
    unittest.main()
