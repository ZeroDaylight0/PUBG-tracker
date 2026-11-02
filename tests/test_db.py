"""Match database: creation, deduplication, migrations, history queries."""
import gzip
import json
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import app  # noqa: E402
import db as dbmod  # noqa: E402

FIXTURES = Path(__file__).resolve().parent / "fixtures"


def fixture_match(match_id="m1", created="2026-10-30T12:34:56Z", map_name="Baltic_Main", mode="squad-fpp"):
    match = json.loads((FIXTURES / "match.json").read_text(encoding="utf-8"))
    match["data"]["id"] = match_id
    match["data"]["attributes"].update(createdAt=created, mapName=map_name, gameMode=mode)
    return match


class TempDbTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.dir = Path(self.tmp.name) / "nested" / "storage"

    def tearDown(self):
        self.tmp.cleanup()


class CreationTest(TempDbTest):
    def test_created_on_first_use_in_data_dir(self):
        self.assertFalse(self.dir.exists())
        db = dbmod.Database.in_dir(self.dir)
        self.assertTrue((self.dir / dbmod.DB_FILENAME).is_file())
        self.assertEqual(db.version(), dbmod.SCHEMA_VERSION)
        with db.connect() as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'")}
        self.assertTrue({"matches", "participants", "players", "telemetry", "heatmap_points"} <= tables)
        self.assertEqual(db.counts(), {"matches": 0, "telemetry": 0, "players": 0})

    def test_reopen_keeps_data(self):
        db = dbmod.Database.in_dir(self.dir)
        db.save_match(app.normalize_match(fixture_match()), "steam")
        again = dbmod.Database.in_dir(self.dir)
        self.assertEqual(again.counts()["matches"], 1)
        self.assertEqual(again.version(), dbmod.SCHEMA_VERSION)


class DedupTest(TempDbTest):
    def test_same_match_twice_is_one_row(self):
        db = dbmod.Database.in_dir(self.dir)
        norm = app.normalize_match(fixture_match())
        db.save_match(norm, "steam")
        db.save_match(norm, "steam")
        db.save_player("account.a", "Alpha", "steam")
        db.save_player("account.a", "Alpha", "steam")
        with db.connect() as conn:
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0], 1)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM participants").fetchone()[0], 3)
            self.assertEqual(conn.execute("SELECT COUNT(*) FROM players").fetchone()[0], 1)
        with self.assertRaises(sqlite3.IntegrityError):
            with db.connect() as conn:
                conn.execute("INSERT INTO participants (match_id, account_id, name) VALUES ('m1', 'account.a', 'x')")

    def test_telemetry_stored_once_and_filtered(self):
        db = dbmod.Database.in_dir(self.dir)
        db.save_match(app.normalize_match(fixture_match()), "steam")
        events = [{"_T": "LogMatchStart", "_D": "x", "common": {"isGame": 0}}, {"_T": "LogItemPickup", "_D": "x"}]
        db.save_telemetry("m1", events)
        db.save_telemetry("m1", [{"_T": "LogMatchStart", "_D": "second"}])
        self.assertEqual(db.get_telemetry("m1"), [{"_T": "LogMatchStart", "_D": "x"}])
        self.assertEqual(json.loads(gzip.decompress(db.get_telemetry_blob("m1"))), db.get_telemetry("m1"))
        self.assertEqual(db.matches_without_telemetry(["m1", "m2"]), ["m2"])

    def test_round_trip_builds_same_summary(self):
        db = dbmod.Database.in_dir(self.dir)
        match = fixture_match()
        db.save_match(app.normalize_match(match), "steam")
        live = app.summarize_match(match, "account.a", "alpha")
        stored = app.build_summary(db.get_match("m1"), "account.a", "alpha")
        self.assertEqual(stored, live)


class MigrationTest(TempDbTest):
    def test_upgrades_old_schema_and_keeps_rows(self):
        db = dbmod.Database.in_dir(self.dir)
        # Simulate a database created by version 1 of the schema
        path = db.path
        path.unlink()
        for suffix in ("-wal", "-shm"):
            Path(str(path) + suffix).unlink(missing_ok=True)
        old = dbmod.Database.__new__(dbmod.Database)
        old.path, old.write_lock = path, __import__("threading").Lock()
        old.migrate(target=1)
        self.assertEqual(old.version(), 1)
        old.save_match(app.normalize_match(fixture_match()), "steam")

        upgraded = dbmod.Database(path)
        self.assertEqual(upgraded.version(), dbmod.SCHEMA_VERSION)
        self.assertEqual(upgraded.counts()["matches"], 1)
        upgraded.save_heat_points("m1", "account.a", {"kills": [[1, 2]]})
        self.assertEqual(upgraded.get_heat_points("m1", "account.a"), {"kills": [[1, 2]]})

    def test_refuses_newer_schema(self):
        db = dbmod.Database.in_dir(self.dir)
        with db.connect() as conn:
            conn.execute(f"PRAGMA user_version = {dbmod.SCHEMA_VERSION + 1}")
        with self.assertRaises(dbmod.DatabaseError):
            dbmod.Database(db.path)

    def test_failed_migration_rolls_back(self):
        db = dbmod.Database.in_dir(self.dir)
        broken = dbmod.MIGRATIONS + ("CREATE TABLE broken (x); THIS IS NOT SQL;",)
        orig = dbmod.MIGRATIONS
        dbmod.MIGRATIONS = broken
        try:
            with self.assertRaises(sqlite3.Error):
                db.migrate(target=len(broken))
        finally:
            dbmod.MIGRATIONS = orig
        self.assertEqual(db.version(), dbmod.SCHEMA_VERSION)
        with db.connect() as conn:
            self.assertIsNone(conn.execute("SELECT 1 FROM sqlite_master WHERE name = 'broken'").fetchone())


class HistoryTest(TempDbTest):
    def setUp(self):
        super().setUp()
        self.db = dbmod.Database.in_dir(self.dir)
        self.db.save_player("account.a", "Alpha", "steam")
        for i in range(25):
            self.db.save_match(app.normalize_match(fixture_match(
                f"m{i:02d}", created=f"2026-10-{(i % 28) + 1:02d}T10:00:00Z",
                map_name="Baltic_Main" if i % 2 else "Desert_Main", mode="squad-fpp" if i < 20 else "duo")), "steam")

    def test_pagination_newest_first(self):
        page1 = self.db.list_history(page=1, per_page=10)
        self.assertEqual((page1["total"], page1["pages"], len(page1["items"])), (25, 3, 10))
        self.assertEqual(page1["items"][0]["match_id"], "m24")
        page3 = self.db.list_history(page=3, per_page=10)
        self.assertEqual(len(page3["items"]), 5)
        ids = [it["match_id"] for p in (1, 2, 3) for it in self.db.list_history(page=p, per_page=10)["items"]]
        self.assertEqual(len(set(ids)), 25)
        # Only tracked (searched) players are listed: Bravo and Enemy are not
        self.assertTrue(all(it["account_id"] == "account.a" for it in page1["items"]))

    def test_filters(self):
        self.assertEqual(self.db.list_history(map_id="Baltic_Main")["total"], 12)
        self.assertEqual(self.db.list_history(mode="duo")["total"], 5)
        self.assertEqual(self.db.list_history(player="ALPHA")["total"], 25)
        self.assertEqual(self.db.list_history(player="account.a", map_id="Desert_Main", mode="duo")["total"], 3)
        self.assertEqual(self.db.list_history(player="nobody")["total"], 0)
        # Parameterized: SQL in a filter value is just an unknown value
        self.assertEqual(self.db.list_history(player="x' OR '1'='1")["total"], 0)
        f = self.db.history_filters()
        self.assertEqual(f["maps"], ["Baltic_Main", "Desert_Main"])
        self.assertEqual(f["modes"], ["duo", "squad-fpp"])
        self.assertEqual(f["players"][0]["matches"], 25)

    def test_matches_on_map(self):
        self.db.save_telemetry("m01", [])
        rows = self.db.player_matches_on_map("account.a", "Baltic_Main")
        self.assertEqual(len(rows), 12)
        self.assertIn(("m01", True, "Alpha"), rows)


if __name__ == "__main__":
    unittest.main()
