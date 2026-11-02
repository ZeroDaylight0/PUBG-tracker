"""Local match database for PUBG Tracker (SQLite, Python standard library only).

The PUBG API keeps matches for about 14 days and returns at most the latest
matches of a player, so every match and telemetry file the tracker downloads
is stored here. History keeps growing past what the API still knows about.

Tables (see MIGRATIONS for the exact schema):
  matches        one row per match (match id is the primary key)
  participants   per-player stats, unique per (match_id, account_id)
  players        players that were searched ("tracked" players)
  telemetry      filtered telemetry events, gzip-compressed JSON, one row per match
  heatmap_points per (match, player) heatmap points, cached from telemetry

All queries are parameterized. Connections are short-lived (one per call), so
the database can be used from the threads of the HTTP server.
"""
import gzip
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

DB_FILENAME = "pubg-tracker.sqlite3"

STAT_FIELDS = ("kills", "knocks", "assists", "headshots", "damage", "revives", "longest", "survived", "rank", "dist")

# Telemetry event types the UI uses; everything else is dropped before storing
TELEMETRY_EVENTS = frozenset({
    "LogMatchStart", "LogGameStatePeriodic", "LogVehicleLeave", "LogParachuteLanding", "LogPlayerPosition",
    "LogPlayerMakeGroggy", "LogPlayerKill", "LogPlayerKillV2", "LogPlayerRevive",
})


MIGRATION_1 = """
        CREATE TABLE matches (
            match_id      TEXT PRIMARY KEY,
            shard         TEXT NOT NULL,
            map_id        TEXT,
            game_mode     TEXT,
            created_at    TEXT,
            duration      INTEGER NOT NULL DEFAULT 0,
            player_count  INTEGER NOT NULL DEFAULT 0,
            telemetry_url TEXT,
            fetched_at    TEXT NOT NULL
        );
        CREATE TABLE participants (
            match_id   TEXT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
            account_id TEXT NOT NULL,
            name       TEXT NOT NULL,
            roster_id  TEXT,
            kills      INTEGER NOT NULL DEFAULT 0,
            knocks     INTEGER NOT NULL DEFAULT 0,
            assists    INTEGER NOT NULL DEFAULT 0,
            headshots  INTEGER NOT NULL DEFAULT 0,
            damage     INTEGER NOT NULL DEFAULT 0,
            revives    INTEGER NOT NULL DEFAULT 0,
            longest    INTEGER NOT NULL DEFAULT 0,
            survived   INTEGER NOT NULL DEFAULT 0,
            rank       INTEGER NOT NULL DEFAULT 0,
            dist       INTEGER NOT NULL DEFAULT 0,
            UNIQUE (match_id, account_id)
        );
        CREATE INDEX idx_participants_account ON participants(account_id);
        CREATE TABLE players (
            account_id  TEXT PRIMARY KEY,
            name        TEXT NOT NULL,
            shard       TEXT NOT NULL,
            last_search TEXT NOT NULL
        );
        CREATE TABLE telemetry (
            match_id  TEXT PRIMARY KEY REFERENCES matches(match_id) ON DELETE CASCADE,
            raw_size  INTEGER NOT NULL,
            data      BLOB NOT NULL,
            stored_at TEXT NOT NULL
        );
"""

# Heatmap cache and indexes for the history filters
MIGRATION_2 = """
        CREATE TABLE heatmap_points (
            match_id   TEXT NOT NULL REFERENCES matches(match_id) ON DELETE CASCADE,
            account_id TEXT NOT NULL,
            data       TEXT NOT NULL,
            PRIMARY KEY (match_id, account_id)
        );
        CREATE INDEX idx_matches_map ON matches(map_id, created_at);
        CREATE INDEX idx_matches_created ON matches(created_at);
"""

# Schema version N = MIGRATIONS[:N] applied (stored in PRAGMA user_version).
# Append new migrations at the end; never edit one that has shipped.
MIGRATIONS = (MIGRATION_1, MIGRATION_2)
SCHEMA_VERSION = len(MIGRATIONS)


class DatabaseError(Exception):
    pass


def utc_now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def compress_events(events):
    """Keep only the telemetry events the UI needs and gzip them as JSON."""
    kept = [{k: v for k, v in ev.items() if k != "common"} for ev in events
            if isinstance(ev, dict) and ev.get("_T") in TELEMETRY_EVENTS]
    raw = json.dumps(kept, separators=(",", ":")).encode("utf-8")
    return gzip.compress(raw, compresslevel=6), len(raw)


def decompress_events(blob):
    return json.loads(gzip.decompress(blob).decode("utf-8"))


class Database:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.write_lock = threading.Lock()
        self.migrate()

    @classmethod
    def in_dir(cls, data_dir):
        return cls(Path(data_dir) / DB_FILENAME)

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(str(self.path), timeout=30)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        try:
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise
        finally:
            conn.close()

    # --- schema -----------------------------------------------------------
    def version(self):
        with self.connect() as conn:
            return conn.execute("PRAGMA user_version").fetchone()[0]

    def migrate(self, target=SCHEMA_VERSION):
        with self.write_lock:
            conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
            try:
                conn.execute("PRAGMA journal_mode = WAL")
                current = conn.execute("PRAGMA user_version").fetchone()[0]
                if current > len(MIGRATIONS):
                    raise DatabaseError(
                        f"{self.path.name} has schema version {current}, newer than this app "
                        f"({len(MIGRATIONS)}). Update PUBG Tracker.")
                for version in range(current + 1, target + 1):
                    # Each migration and its version bump are applied in one transaction
                    try:
                        conn.executescript(
                            f"BEGIN; {MIGRATIONS[version - 1]} PRAGMA user_version = {int(version)}; COMMIT;")
                    except sqlite3.Error:
                        if conn.in_transaction:
                            conn.execute("ROLLBACK")
                        raise
            finally:
                conn.close()

    # --- writes -----------------------------------------------------------
    def save_match(self, match, shard):
        """Store a normalized match (see app.normalize_match). Re-saving the same match is a no-op."""
        with self.write_lock, self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO matches (match_id, shard, map_id, game_mode, created_at, duration, "
                "player_count, telemetry_url, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (match["match_id"], shard, match.get("map"), match.get("mode"), match.get("created_at"),
                 int(match.get("duration") or 0), int(match.get("player_count") or 0), match.get("telemetry"),
                 utc_now()),
            )
            conn.executemany(
                "INSERT INTO participants (match_id, account_id, name, roster_id, "
                + ", ".join(STAT_FIELDS) + ") VALUES (?, ?, ?, ?, " + ", ".join("?" * len(STAT_FIELDS)) + ") "
                "ON CONFLICT (match_id, account_id) DO UPDATE SET name = excluded.name, roster_id = excluded.roster_id, "
                + ", ".join(f"{f} = excluded.{f}" for f in STAT_FIELDS),
                [(match["match_id"], p["account_id"], p["name"], p.get("roster_id"),
                  *(int(p.get(f) or 0) for f in STAT_FIELDS)) for p in match["participants"]],
            )

    def save_player(self, account_id, name, shard):
        with self.write_lock, self.connect() as conn:
            conn.execute(
                "INSERT INTO players (account_id, name, shard, last_search) VALUES (?, ?, ?, ?) "
                "ON CONFLICT (account_id) DO UPDATE SET name = excluded.name, shard = excluded.shard, "
                "last_search = excluded.last_search",
                (account_id, name, shard, utc_now()),
            )

    def save_telemetry(self, match_id, events):
        blob, raw_size = compress_events(events)
        with self.write_lock, self.connect() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO telemetry (match_id, raw_size, data, stored_at) VALUES (?, ?, ?, ?)",
                (match_id, raw_size, blob, utc_now()),
            )

    def save_heat_points(self, match_id, account_id, points):
        with self.write_lock, self.connect() as conn:
            conn.execute(
                "INSERT OR REPLACE INTO heatmap_points (match_id, account_id, data) VALUES (?, ?, ?)",
                (match_id, account_id, json.dumps(points, separators=(",", ":"))),
            )

    # --- reads ------------------------------------------------------------
    def has_match(self, match_id):
        with self.connect() as conn:
            return conn.execute("SELECT 1 FROM matches WHERE match_id = ?", (match_id,)).fetchone() is not None

    def has_telemetry(self, match_id):
        with self.connect() as conn:
            return conn.execute("SELECT 1 FROM telemetry WHERE match_id = ?", (match_id,)).fetchone() is not None

    def get_match(self, match_id):
        """The normalized match, as passed to save_match(), or None."""
        with self.connect() as conn:
            row = conn.execute("SELECT * FROM matches WHERE match_id = ?", (match_id,)).fetchone()
            if row is None:
                return None
            parts = conn.execute(
                "SELECT * FROM participants WHERE match_id = ? ORDER BY rowid", (match_id,)).fetchall()
        return {
            "match_id": row["match_id"], "shard": row["shard"], "map": row["map_id"], "mode": row["game_mode"],
            "created_at": row["created_at"], "duration": row["duration"], "player_count": row["player_count"],
            "telemetry": row["telemetry_url"],
            "participants": [
                dict({"account_id": p["account_id"], "name": p["name"], "roster_id": p["roster_id"]},
                     **{f: p[f] for f in STAT_FIELDS})
                for p in parts
            ],
        }

    def get_telemetry_blob(self, match_id):
        with self.connect() as conn:
            row = conn.execute("SELECT data FROM telemetry WHERE match_id = ?", (match_id,)).fetchone()
        return row["data"] if row else None

    def get_telemetry(self, match_id):
        blob = self.get_telemetry_blob(match_id)
        return decompress_events(blob) if blob is not None else None

    def get_heat_points(self, match_id, account_id):
        with self.connect() as conn:
            row = conn.execute("SELECT data FROM heatmap_points WHERE match_id = ? AND account_id = ?",
                               (match_id, account_id)).fetchone()
        return json.loads(row["data"]) if row else None

    def matches_without_telemetry(self, match_ids):
        with self.connect() as conn:
            stored = {r[0] for r in conn.execute(
                "SELECT match_id FROM telemetry WHERE match_id IN (%s)" % ",".join("?" * len(match_ids)),
                list(match_ids)).fetchall()} if match_ids else set()
        return [m for m in match_ids if m not in stored]

    def _history_filter(self, player=None, map_id=None, mode=None):
        where, args = [], []
        if player:
            where.append("(p.account_id = ? OR pl.name = ? COLLATE NOCASE)")
            args += [player, player]
        if map_id:
            where.append("m.map_id = ?")
            args.append(map_id)
        if mode:
            where.append("m.game_mode = ?")
            args.append(mode)
        return (" WHERE " + " AND ".join(where)) if where else "", args

    def list_history(self, player=None, map_id=None, mode=None, page=1, per_page=20):
        """Stored matches of tracked (searched) players, newest first, with pagination."""
        page, per_page = max(1, int(page)), max(1, min(100, int(per_page)))
        base = ("FROM participants p JOIN players pl ON pl.account_id = p.account_id "
                "JOIN matches m ON m.match_id = p.match_id")
        where, args = self._history_filter(player, map_id, mode)
        with self.connect() as conn:
            total = conn.execute(f"SELECT COUNT(*) {base}{where}", args).fetchone()[0]
            rows = conn.execute(
                f"SELECT m.match_id, m.map_id, m.game_mode, m.created_at, m.player_count, p.account_id, p.name, "
                f"p.kills, p.knocks, p.damage, p.rank, "
                f"EXISTS (SELECT 1 FROM telemetry t WHERE t.match_id = m.match_id) AS has_telemetry "
                f"{base}{where} ORDER BY m.created_at DESC, m.match_id, p.account_id LIMIT ? OFFSET ?",
                args + [per_page, (page - 1) * per_page]).fetchall()
        return {"total": total, "page": page, "per_page": per_page,
                "pages": max(1, -(-total // per_page)), "items": [dict(r) for r in rows]}

    def history_filters(self):
        with self.connect() as conn:
            players = conn.execute(
                "SELECT pl.account_id, pl.name, pl.shard, COUNT(p.match_id) AS matches FROM players pl "
                "LEFT JOIN participants p ON p.account_id = pl.account_id "
                "GROUP BY pl.account_id ORDER BY pl.last_search DESC").fetchall()
            maps = [r[0] for r in conn.execute(
                "SELECT DISTINCT map_id FROM matches WHERE map_id IS NOT NULL ORDER BY map_id").fetchall()]
            modes = [r[0] for r in conn.execute(
                "SELECT DISTINCT game_mode FROM matches WHERE game_mode IS NOT NULL ORDER BY game_mode").fetchall()]
        return {"players": [dict(r) for r in players], "maps": maps, "modes": modes}

    def player_matches_on_map(self, account_id, map_id):
        """(match_id, has_telemetry, player name) of every stored match of a player on a map, newest first."""
        with self.connect() as conn:
            rows = conn.execute(
                "SELECT m.match_id, EXISTS (SELECT 1 FROM telemetry t WHERE t.match_id = m.match_id) AS has_t, p.name "
                "FROM matches m JOIN participants p ON p.match_id = m.match_id "
                "WHERE p.account_id = ? AND m.map_id = ? ORDER BY m.created_at DESC",
                (account_id, map_id)).fetchall()
        return [(r[0], bool(r[1]), r[2]) for r in rows]

    def counts(self):
        with self.connect() as conn:
            return {
                "matches": conn.execute("SELECT COUNT(*) FROM matches").fetchone()[0],
                "telemetry": conn.execute("SELECT COUNT(*) FROM telemetry").fetchone()[0],
                "players": conn.execute("SELECT COUNT(*) FROM players").fetchone()[0],
            }
