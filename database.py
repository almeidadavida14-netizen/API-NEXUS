"""Small SQLite persistence layer used by the Discord bot."""

from __future__ import annotations

import json
import logging
import math
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any


logger = logging.getLogger(__name__)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


@dataclass(frozen=True)
class GuildConfig:
    guild_id: int
    panel_channel_id: int | None
    panel_message_id: int | None
    ap_channel_id: int | None
    salinha_channel_id: int | None
    match_category_id: int | None
    next_match_number: int


@dataclass(frozen=True)
class MatchRecord:
    match_id: int
    guild_id: int
    mode: str
    channel_id: int | None
    team_a_voice_channel_id: int | None
    team_b_voice_channel_id: int | None
    status: str
    winner_team: str | None
    players: list[int]
    team_a: list[int]
    team_b: list[int]


@dataclass(frozen=True)
class PlayerStats:
    user_id: int
    wins: int
    losses: int


@dataclass(frozen=True)
class HallOfFameEntry:
    user_id: int
    title_count: int


@dataclass(frozen=True)
class PlayerTitle:
    tournament_id: int
    team_id: int
    awarded_at: str


@dataclass(frozen=True)
class TournamentChampion:
    tournament_id: int
    team_id: int
    player_ids: list[int]
    finished_at: str | None


@dataclass(frozen=True)
class TournamentRecord:
    tournament_id: int
    guild_id: int
    mode: str
    team_size: int
    capacity: int
    status: str
    created_by: int
    champion_pair_id: int | None
    panel_channel_id: int | None
    panel_message_id: int | None
    created_at: str | None = None
    started_at: str | None = None
    channel_cleanup_at: str | None = None
    channel_cleanup_done_at: str | None = None
    prize_amount_cents: int | None = None


@dataclass(frozen=True)
class TournamentPaymentBatch:
    tournament_id: int
    guild_id: int
    champion_team_id: int
    total_amount_cents: int | None
    channel_id: int | None
    summary_message_id: int | None
    status: str
    close_at: str | None
    close_done_at: str | None
    finalized_by: int | None
    finalized_at: str | None


@dataclass(frozen=True)
class TournamentPayout:
    tournament_id: int
    guild_id: int
    user_id: int
    amount_cents: int | None
    pix_key: str | None
    recipient_name: str | None
    recipient_city: str | None
    pix_submitted_at: str | None
    status: str
    message_id: int | None
    paid_by: int | None
    paid_at: str | None


@dataclass(frozen=True)
class TournamentPair:
    pair_id: int
    tournament_id: int
    players: list[int]
    slot: int

    @property
    def player_a_id(self) -> int:
        return self.players[0]

    @property
    def player_b_id(self) -> int:
        return self.players[1] if len(self.players) > 1 else self.players[0]


@dataclass(frozen=True)
class TournamentBracketMatch:
    bracket_match_id: int
    tournament_id: int
    round_number: int
    match_number: int
    pair_a_id: int
    pair_b_id: int
    winner_pair_id: int | None
    status: str
    regular_match_id: int | None


@dataclass(frozen=True)
class TournamentResult:
    tournament_id: int
    next_bracket_match_ids: list[int]
    champion_pair_id: int | None


class Database:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        existing_database = path.is_file() and path.stat().st_size > 0
        self.connection = sqlite3.connect(path, check_same_thread=False)
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA journal_mode=WAL")
        self.connection.execute("PRAGMA foreign_keys=ON")
        self.lock = threading.RLock()
        if existing_database:
            try:
                self.create_backup_snapshot()
            except (OSError, sqlite3.Error):
                logger.exception("Não foi possível criar o backup preventivo do SQLite.")
        self._create_schema()

    def _create_schema(self) -> None:
        with self.lock, self.connection:
            self.connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS guild_config (
                    guild_id INTEGER PRIMARY KEY,
                    panel_channel_id INTEGER,
                    panel_message_id INTEGER,
                    ap_channel_id INTEGER,
                    salinha_channel_id INTEGER,
                    match_category_id INTEGER,
                    next_match_number INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );

                CREATE TABLE IF NOT EXISTS queue_entries (
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    queue_name TEXT NOT NULL,
                    joined_at TEXT NOT NULL,
                    PRIMARY KEY (guild_id, user_id),
                    CHECK (queue_name IN ('1v1', '2v2', '3v3', '4v4', 'ap', 'salinha'))
                );

                CREATE INDEX IF NOT EXISTS queue_entries_lookup
                    ON queue_entries (guild_id, queue_name, joined_at);

                CREATE TABLE IF NOT EXISTS matches (
                    match_id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    mode TEXT NOT NULL,
                    channel_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'active',
                    players_json TEXT NOT NULL,
                    team_a_json TEXT NOT NULL,
                    team_b_json TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    ended_at TEXT
                );

                CREATE INDEX IF NOT EXISTS matches_user_lookup
                    ON matches (guild_id, status);

                CREATE TABLE IF NOT EXISTS player_stats (
                    user_id INTEGER PRIMARY KEY,
                    wins INTEGER NOT NULL DEFAULT 0,
                    losses INTEGER NOT NULL DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS tournaments (
                    tournament_id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    capacity INTEGER NOT NULL CHECK (capacity IN (4, 8, 16)),
                    mode TEXT NOT NULL DEFAULT '2v2',
                    team_size INTEGER NOT NULL DEFAULT 2,
                    status TEXT NOT NULL DEFAULT 'registration'
                        CHECK (status IN ('registration', 'ready', 'active', 'finished', 'cancelled')),
                    created_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    champion_pair_id INTEGER,
                    panel_channel_id INTEGER,
                    panel_message_id INTEGER,
                    channel_cleanup_at TEXT,
                    channel_cleanup_done_at TEXT,
                    prize_amount_cents INTEGER
                );

                CREATE INDEX IF NOT EXISTS tournaments_guild_status
                    ON tournaments (guild_id, status);

                CREATE TABLE IF NOT EXISTS tournament_pairs (
                    pair_id INTEGER PRIMARY KEY,
                    tournament_id INTEGER NOT NULL,
                    player_a_id INTEGER NOT NULL,
                    player_b_id INTEGER NOT NULL,
                    slot INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (tournament_id, slot),
                    UNIQUE (tournament_id, player_a_id),
                    UNIQUE (tournament_id, player_b_id)
                );

                CREATE INDEX IF NOT EXISTS tournament_pairs_tournament
                    ON tournament_pairs (tournament_id, slot);

                CREATE TABLE IF NOT EXISTS tournament_teams (
                    team_id INTEGER PRIMARY KEY,
                    tournament_id INTEGER NOT NULL,
                    members_json TEXT NOT NULL,
                    slot INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    UNIQUE (tournament_id, slot)
                );

                CREATE INDEX IF NOT EXISTS tournament_teams_tournament
                    ON tournament_teams (tournament_id, slot);

                CREATE TABLE IF NOT EXISTS tournament_bracket_matches (
                    bracket_match_id INTEGER PRIMARY KEY,
                    tournament_id INTEGER NOT NULL,
                    round_number INTEGER NOT NULL,
                    match_number INTEGER NOT NULL,
                    pair_a_id INTEGER NOT NULL,
                    pair_b_id INTEGER NOT NULL,
                    winner_pair_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'active', 'finished', 'cancelled')),
                    regular_match_id INTEGER UNIQUE,
                    created_at TEXT NOT NULL,
                    ended_at TEXT,
                    UNIQUE (tournament_id, round_number, match_number)
                );

                CREATE INDEX IF NOT EXISTS tournament_bracket_lookup
                    ON tournament_bracket_matches (tournament_id, round_number, status);

                CREATE TABLE IF NOT EXISTS tournament_matchup_plans (
                    tournament_id INTEGER PRIMARY KEY
                        REFERENCES tournaments(tournament_id) ON DELETE CASCADE,
                    semifinal_1_pair_a_id INTEGER NOT NULL,
                    semifinal_1_pair_b_id INTEGER NOT NULL,
                    semifinal_2_pair_a_id INTEGER NOT NULL,
                    semifinal_2_pair_b_id INTEGER NOT NULL,
                    updated_at TEXT NOT NULL,
                    CHECK (
                        semifinal_1_pair_a_id <> semifinal_1_pair_b_id
                        AND semifinal_1_pair_a_id <> semifinal_2_pair_a_id
                        AND semifinal_1_pair_a_id <> semifinal_2_pair_b_id
                        AND semifinal_1_pair_b_id <> semifinal_2_pair_a_id
                        AND semifinal_1_pair_b_id <> semifinal_2_pair_b_id
                        AND semifinal_2_pair_a_id <> semifinal_2_pair_b_id
                    )
                );

                CREATE TABLE IF NOT EXISTS tournament_titles (
                    guild_id INTEGER NOT NULL,
                    tournament_id INTEGER NOT NULL,
                    team_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    awarded_at TEXT NOT NULL,
                    PRIMARY KEY (tournament_id, user_id)
                );

                CREATE INDEX IF NOT EXISTS tournament_titles_player_lookup
                    ON tournament_titles (guild_id, user_id, awarded_at);

                CREATE TABLE IF NOT EXISTS tournament_payment_batches (
                    tournament_id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    champion_team_id INTEGER NOT NULL,
                    total_amount_cents INTEGER,
                    channel_id INTEGER,
                    summary_message_id INTEGER,
                    status TEXT NOT NULL DEFAULT 'provisioning'
                        CHECK (status IN ('provisioning', 'open', 'closing', 'closed')),
                    close_at TEXT,
                    close_done_at TEXT,
                    finalized_by INTEGER,
                    finalized_at TEXT,
                    created_at TEXT NOT NULL
                );

                CREATE INDEX IF NOT EXISTS tournament_payment_batches_guild_status
                    ON tournament_payment_batches (guild_id, status);

                CREATE TABLE IF NOT EXISTS tournament_payouts (
                    tournament_id INTEGER NOT NULL,
                    guild_id INTEGER NOT NULL,
                    user_id INTEGER NOT NULL,
                    amount_cents INTEGER,
                    pix_key TEXT,
                    recipient_name TEXT,
                    recipient_city TEXT,
                    pix_submitted_at TEXT,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK (status IN ('pending', 'paid')),
                    message_id INTEGER,
                    paid_by INTEGER,
                    paid_at TEXT,
                    PRIMARY KEY (tournament_id, user_id)
                );

                CREATE INDEX IF NOT EXISTS tournament_payouts_batch_status
                    ON tournament_payouts (tournament_id, status);
                """
            )
            self._ensure_match_columns()
            self._ensure_tournament_columns()
            self._backfill_finished_tournament_titles()

    def create_backup_snapshot(self, retention: int = 168) -> Path:
        """Create a consistent, integrity-checked SQLite snapshot and rotate old copies."""
        with self.lock:
            database_path = Path(
                str(self.connection.execute("PRAGMA database_list").fetchone()["file"])
            )
        snapshot_dir = database_path.parent / "backups"
        snapshot_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        destination = snapshot_dir / f"{database_path.stem}-{stamp}.sqlite3"
        temporary = destination.with_suffix(destination.suffix + ".tmp")
        target: sqlite3.Connection | None = None
        try:
            target = sqlite3.connect(temporary)
            with self.lock:
                self.connection.backup(target, pages=256, sleep=0.05)
            check = target.execute("PRAGMA integrity_check").fetchone()
            if not check or str(check[0]).lower() != "ok":
                raise sqlite3.DatabaseError(
                    f"Backup SQLite falhou na verificação de integridade: {check[0] if check else 'sem resultado'}"
                )
            target.close()
            target = None
            temporary.replace(destination)
        except Exception:
            if target is not None:
                target.close()
            temporary.unlink(missing_ok=True)
            raise

        keep = max(1, int(retention))
        snapshots = sorted(
            snapshot_dir.glob(f"{database_path.stem}-*.sqlite3"),
            key=lambda item: item.stat().st_mtime,
            reverse=True,
        )
        for old_snapshot in snapshots[keep:]:
            try:
                old_snapshot.unlink()
            except OSError:
                logger.warning("Não foi possível remover o backup antigo %s.", old_snapshot)
        return destination

    def latest_backup_path(self) -> Path | None:
        with self.lock:
            database_path = Path(
                str(self.connection.execute("PRAGMA database_list").fetchone()["file"])
            )
        backup_dir = database_path.parent / "backups"
        snapshots = list(backup_dir.glob(f"{database_path.stem}-*.sqlite3"))
        return max(snapshots, key=lambda item: item.stat().st_mtime) if snapshots else None

    def _backfill_finished_tournament_titles(self) -> None:
        rows = self.connection.execute(
            """
            SELECT tournament_id, guild_id, champion_pair_id, finished_at
            FROM tournaments
            WHERE status = 'finished' AND champion_pair_id IS NOT NULL
            """
        ).fetchall()
        for row in rows:
            tournament_id = int(row["tournament_id"])
            team_id = int(row["champion_pair_id"])
            team = self.connection.execute(
                """
                SELECT members_json
                FROM tournament_teams
                WHERE tournament_id = ? AND team_id = ?
                """,
                (tournament_id, team_id),
            ).fetchone()
            if team:
                user_ids = [int(user_id) for user_id in json.loads(team["members_json"])]
            else:
                legacy_team = self.connection.execute(
                    """
                    SELECT player_a_id, player_b_id
                    FROM tournament_pairs
                    WHERE tournament_id = ? AND pair_id = ?
                    """,
                    (tournament_id, team_id),
                ).fetchone()
                if not legacy_team:
                    continue
                user_ids = [
                    int(legacy_team["player_a_id"]),
                    int(legacy_team["player_b_id"]),
                ]
            awarded_at = str(row["finished_at"] or utc_now())
            for user_id in set(user_ids):
                self.connection.execute(
                    """
                    INSERT OR IGNORE INTO tournament_titles
                    (guild_id, tournament_id, team_id, user_id, awarded_at)
                    VALUES (?, ?, ?, ?, ?)
                    """,
                    (
                        int(row["guild_id"]),
                        tournament_id,
                        team_id,
                        user_id,
                        awarded_at,
                    ),
                )

    def _ensure_match_columns(self) -> None:
        """Apply additive migrations without replacing existing match data."""
        columns = {
            str(row["name"])
            for row in self.connection.execute("PRAGMA table_info(matches)").fetchall()
        }
        for column in (
            "team_a_voice_channel_id",
            "team_b_voice_channel_id",
            "winner_team",
        ):
            if column not in columns:
                column_type = "TEXT" if column == "winner_team" else "INTEGER"
                self.connection.execute(
                    f"ALTER TABLE matches ADD COLUMN {column} {column_type}"
                )

    def _ensure_tournament_columns(self) -> None:
        """Apply additive migrations for the generic tournament system."""
        table_sql = self.connection.execute(
            "SELECT sql FROM sqlite_master WHERE type = 'table' AND name = 'tournaments'"
        ).fetchone()
        if table_sql and "CHECK (capacity IN (8, 16, 32))" in str(table_sql[0]):
            self.connection.executescript("""
                ALTER TABLE tournaments RENAME TO tournaments_legacy;
                CREATE TABLE tournaments (
                    tournament_id INTEGER PRIMARY KEY,
                    guild_id INTEGER NOT NULL,
                    capacity INTEGER NOT NULL CHECK (capacity IN (4, 8, 16)),
                    mode TEXT NOT NULL DEFAULT '2v2',
                    team_size INTEGER NOT NULL DEFAULT 2,
                    status TEXT NOT NULL DEFAULT 'registration'
                        CHECK (status IN ('registration', 'ready', 'active', 'finished', 'cancelled')),
                    created_by INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    started_at TEXT,
                    finished_at TEXT,
                    champion_pair_id INTEGER,
                    panel_channel_id INTEGER,
                    panel_message_id INTEGER,
                    channel_cleanup_at TEXT,
                    channel_cleanup_done_at TEXT,
                    prize_amount_cents INTEGER
                );
                INSERT INTO tournaments
                (tournament_id, guild_id, capacity, mode, team_size, status, created_by, created_at, started_at, finished_at, champion_pair_id, panel_channel_id, panel_message_id)
                SELECT tournament_id, guild_id, capacity, '2v2', 2, status, created_by, created_at, started_at, finished_at, champion_pair_id, panel_channel_id, panel_message_id
                FROM tournaments_legacy;
                DROP TABLE tournaments_legacy;
            """)
        columns = {
            str(row["name"])
            for row in self.connection.execute("PRAGMA table_info(tournaments)").fetchall()
        }
        additions = {
            "panel_channel_id": "INTEGER",
            "panel_message_id": "INTEGER",
            "mode": "TEXT NOT NULL DEFAULT '2v2'",
            "team_size": "INTEGER NOT NULL DEFAULT 2",
            "channel_cleanup_at": "TEXT",
            "channel_cleanup_done_at": "TEXT",
            "prize_amount_cents": "INTEGER",
        }
        for column, column_type in additions.items():
            if column not in columns:
                self.connection.execute(
                    f"ALTER TABLE tournaments ADD COLUMN {column} {column_type}"
                )
        # Migrate any old 2x2 tournament pairs into the generic team table once.
        legacy_pairs = self.connection.execute("SELECT pair_id, tournament_id, player_a_id, player_b_id, slot, created_at FROM tournament_pairs").fetchall()
        for row in legacy_pairs:
            exists = self.connection.execute("SELECT 1 FROM tournament_teams WHERE team_id = ?", (int(row["pair_id"]),)).fetchone()
            if not exists:
                self.connection.execute(
                    "INSERT INTO tournament_teams (team_id, tournament_id, members_json, slot, created_at) VALUES (?, ?, ?, ?, ?)",
                    (int(row["pair_id"]), int(row["tournament_id"]), json.dumps([int(row["player_a_id"]), int(row["player_b_id"])]), int(row["slot"]), str(row["created_at"])),
                )

    def ensure_guild(self, guild_id: int) -> GuildConfig:
        now = utc_now()
        with self.lock, self.connection:
            self.connection.execute(
                """
                INSERT INTO guild_config (guild_id, created_at, updated_at)
                VALUES (?, ?, ?)
                ON CONFLICT(guild_id) DO NOTHING
                """,
                (guild_id, now, now),
            )
        return self.get_guild_config(guild_id)

    def get_guild_config(self, guild_id: int) -> GuildConfig:
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM guild_config WHERE guild_id = ?", (guild_id,)
            ).fetchone()
        if row is None:
            self.ensure_guild(guild_id)
            return self.get_guild_config(guild_id)
        return GuildConfig(
            guild_id=row["guild_id"],
            panel_channel_id=row["panel_channel_id"],
            panel_message_id=row["panel_message_id"],
            ap_channel_id=row["ap_channel_id"],
            salinha_channel_id=row["salinha_channel_id"],
            match_category_id=row["match_category_id"],
            next_match_number=row["next_match_number"],
        )

    def update_guild_config(self, guild_id: int, **values: int | None) -> None:
        if not values:
            return
        allowed = {
            "panel_channel_id",
            "panel_message_id",
            "ap_channel_id",
            "salinha_channel_id",
            "match_category_id",
        }
        unknown = set(values) - allowed
        if unknown:
            raise ValueError(f"Unsupported guild configuration fields: {unknown}")
        assignments = ", ".join(f"{key} = ?" for key in values)
        params = [*values.values(), utc_now(), guild_id]
        with self.lock, self.connection:
            self.connection.execute(
                f"UPDATE guild_config SET {assignments}, updated_at = ? WHERE guild_id = ?",
                params,
            )

    def list_queue(self, guild_id: int, queue_name: str) -> list[int]:
        with self.lock:
            rows = self.connection.execute(
                """
                SELECT user_id FROM queue_entries
                WHERE guild_id = ? AND queue_name = ?
                ORDER BY joined_at ASC
                """,
                (guild_id, queue_name),
            ).fetchall()
        return [int(row["user_id"]) for row in rows]

    def get_user_queue(self, guild_id: int, user_id: int) -> str | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT queue_name FROM queue_entries WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
        return str(row["queue_name"]) if row else None

    def join_queue(self, guild_id: int, user_id: int, queue_name: str) -> tuple[bool, str | None]:
        with self.lock, self.connection:
            existing = self.connection.execute(
                "SELECT queue_name FROM queue_entries WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            ).fetchone()
            if existing:
                return False, str(existing["queue_name"])
            self.connection.execute(
                """
                INSERT INTO queue_entries (guild_id, user_id, queue_name, joined_at)
                VALUES (?, ?, ?, ?)
                """,
                (guild_id, user_id, queue_name, utc_now()),
            )
        return True, None

    def leave_queue(
        self, guild_id: int, user_id: int, queue_name: str | None = None
    ) -> str | None:
        with self.lock, self.connection:
            query = "SELECT queue_name FROM queue_entries WHERE guild_id = ? AND user_id = ?"
            params: list[Any] = [guild_id, user_id]
            if queue_name:
                query += " AND queue_name = ?"
                params.append(queue_name)
            row = self.connection.execute(query, params).fetchone()
            if not row:
                return None
            actual_queue = str(row["queue_name"])
            self.connection.execute(
                "DELETE FROM queue_entries WHERE guild_id = ? AND user_id = ?",
                (guild_id, user_id),
            )
        return actual_queue

    def clear_queue(self, guild_id: int, queue_name: str) -> int:
        with self.lock, self.connection:
            cursor = self.connection.execute(
                "DELETE FROM queue_entries WHERE guild_id = ? AND queue_name = ?",
                (guild_id, queue_name),
            )
        return cursor.rowcount

    def pop_queue(self, guild_id: int, queue_name: str, count: int) -> list[int]:
        with self.lock, self.connection:
            rows = self.connection.execute(
                """
                SELECT user_id FROM queue_entries
                WHERE guild_id = ? AND queue_name = ?
                ORDER BY joined_at ASC LIMIT ?
                """,
                (guild_id, queue_name, count),
            ).fetchall()
            player_ids = [int(row["user_id"]) for row in rows]
            if player_ids:
                placeholders = ",".join("?" for _ in player_ids)
                self.connection.execute(
                    f"""
                    DELETE FROM queue_entries
                    WHERE guild_id = ? AND queue_name = ?
                    AND user_id IN ({placeholders})
                    """,
                    [guild_id, queue_name, *player_ids],
                )
        return player_ids

    def create_match(
        self,
        guild_id: int,
        mode: str,
        players: list[int],
        team_a: list[int],
        team_b: list[int],
    ) -> int:
        with self.lock, self.connection:
            # IDs are global so two guilds can never create the same match
            # identifier in the shared SQLite database.
            row = self.connection.execute(
                "SELECT COALESCE(MAX(match_id), 0) + 1 AS next_id FROM matches"
            ).fetchone()
            match_id = int(row["next_id"])
            now = utc_now()
            self.connection.execute(
                """
                INSERT INTO matches
                (match_id, guild_id, mode, players_json, team_a_json, team_b_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match_id,
                    guild_id,
                    mode,
                    json.dumps(players),
                    json.dumps(team_a),
                    json.dumps(team_b),
                    now,
                ),
            )
            self.connection.execute(
                """
                UPDATE guild_config
                SET next_match_number = ?, updated_at = ?
                WHERE guild_id = ?
                """,
                (match_id + 1, now, guild_id),
            )
        return match_id

    def create_tournament_match(
        self,
        guild_id: int,
        mode: str,
        players: list[int],
        team_a: list[int],
        team_b: list[int],
        bracket_match_id: int,
    ) -> int | None:
        """Create a match and link it to its bracket in one SQLite transaction."""
        with self.lock, self.connection:
            bracket = self.connection.execute(
                """
                SELECT status, regular_match_id
                FROM tournament_bracket_matches
                WHERE bracket_match_id = ?
                """,
                (bracket_match_id,),
            ).fetchone()
            if (
                not bracket
                or str(bracket["status"]) != "pending"
                or bracket["regular_match_id"] is not None
            ):
                return None
            row = self.connection.execute(
                "SELECT COALESCE(MAX(match_id), 0) + 1 AS next_id FROM matches"
            ).fetchone()
            match_id = int(row["next_id"])
            now = utc_now()
            self.connection.execute(
                """
                INSERT INTO matches
                (match_id, guild_id, mode, players_json, team_a_json, team_b_json, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    match_id,
                    guild_id,
                    mode,
                    json.dumps(players),
                    json.dumps(team_a),
                    json.dumps(team_b),
                    now,
                ),
            )
            linked = self.connection.execute(
                """
                UPDATE tournament_bracket_matches
                SET regular_match_id = ?, status = 'active'
                WHERE bracket_match_id = ?
                  AND status = 'pending'
                  AND regular_match_id IS NULL
                """,
                (match_id, bracket_match_id),
            )
            if linked.rowcount != 1:
                self.connection.execute("DELETE FROM matches WHERE match_id = ?", (match_id,))
                return None
            self.connection.execute(
                """
                UPDATE guild_config
                SET next_match_number = ?, updated_at = ?
                WHERE guild_id = ?
                """,
                (match_id + 1, now, guild_id),
            )
            return match_id

    def adopt_orphaned_tournament_match(
        self,
        guild_id: int,
        mode: str,
        players: list[int],
        team_a: list[int],
        team_b: list[int],
        bracket_match_id: int,
        tournament_started_at: str,
    ) -> tuple[str, int | None]:
        """Attach a pre-transaction tournament match left by an older bot version."""
        with self.lock, self.connection:
            bracket = self.connection.execute(
                """
                SELECT status, regular_match_id
                FROM tournament_bracket_matches
                WHERE bracket_match_id = ?
                """,
                (bracket_match_id,),
            ).fetchone()
            if not bracket:
                return "not_found", None
            if bracket["regular_match_id"] is not None:
                return "already_linked", int(bracket["regular_match_id"])
            if str(bracket["status"]) != "pending":
                return "not_pending", None

            candidates = self.connection.execute(
                """
                SELECT match_id
                FROM matches AS m
                WHERE m.guild_id = ?
                  AND m.mode = ?
                  AND m.status = 'active'
                  AND m.created_at >= ?
                  AND m.players_json = ?
                  AND m.team_a_json = ?
                  AND m.team_b_json = ?
                  AND NOT EXISTS (
                      SELECT 1
                      FROM tournament_bracket_matches AS linked
                      WHERE linked.regular_match_id = m.match_id
                  )
                ORDER BY m.match_id
                """,
                (
                    guild_id,
                    mode,
                    tournament_started_at,
                    json.dumps(players),
                    json.dumps(team_a),
                    json.dumps(team_b),
                ),
            ).fetchall()
            if not candidates:
                return "none", None
            if len(candidates) > 1:
                return "ambiguous", None

            match_id = int(candidates[0]["match_id"])
            linked = self.connection.execute(
                """
                UPDATE tournament_bracket_matches
                SET regular_match_id = ?, status = 'active'
                WHERE bracket_match_id = ?
                  AND status = 'pending'
                  AND regular_match_id IS NULL
                """,
                (match_id, bracket_match_id),
            )
            if linked.rowcount != 1:
                return "already_linked", None
            return "adopted", match_id

    def set_match_channel(self, match_id: int, channel_id: int) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                "UPDATE matches SET channel_id = ? WHERE match_id = ?",
                (channel_id, match_id),
            )

    def set_match_voice_channels(
        self, match_id: int, team_a_channel_id: int, team_b_channel_id: int
    ) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                """
                UPDATE matches
                SET team_a_voice_channel_id = ?, team_b_voice_channel_id = ?
                WHERE match_id = ?
                """,
                (team_a_channel_id, team_b_channel_id, match_id),
            )

    def _match_from_row(self, row: sqlite3.Row) -> MatchRecord:
        return MatchRecord(
            match_id=int(row["match_id"]),
            guild_id=int(row["guild_id"]),
            mode=str(row["mode"]),
            channel_id=row["channel_id"],
            team_a_voice_channel_id=row["team_a_voice_channel_id"],
            team_b_voice_channel_id=row["team_b_voice_channel_id"],
            status=str(row["status"]),
            winner_team=row["winner_team"],
            players=json.loads(row["players_json"]),
            team_a=json.loads(row["team_a_json"]),
            team_b=json.loads(row["team_b_json"]),
        )

    def get_active_match_for_user(self, guild_id: int, user_id: int) -> MatchRecord | None:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM matches WHERE guild_id = ? AND status = 'active'",
                (guild_id,),
            ).fetchall()
        for row in rows:
            record = self._match_from_row(row)
            if user_id in record.players:
                return record
        return None

    def get_match(self, match_id: int) -> MatchRecord | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM matches WHERE match_id = ?", (match_id,)
            ).fetchone()
        return self._match_from_row(row) if row else None

    def list_active_matches(self, guild_id: int) -> list[MatchRecord]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM matches WHERE guild_id = ? AND status = 'active'",
                (guild_id,),
            ).fetchall()
        return [self._match_from_row(row) for row in rows]

    def list_all_active_matches(self) -> list[MatchRecord]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM matches WHERE status = 'active'"
            ).fetchall()
        return [self._match_from_row(row) for row in rows]

    def finish_match(self, match_id: int, winner_team: str | None = None) -> bool:
        if winner_team not in (None, "A", "B"):
            raise ValueError("winner_team must be None, 'A', or 'B'")
        with self.lock, self.connection:
            row = self.connection.execute(
                """
                SELECT status, team_a_json, team_b_json
                FROM matches
                WHERE match_id = ?
                """,
                (match_id,),
            ).fetchone()
            if not row or str(row["status"]) != "active":
                return False

            cursor = self.connection.execute(
                """
                UPDATE matches
                SET status = 'finished', winner_team = ?, ended_at = ?
                WHERE match_id = ? AND status = 'active'
                """,
                (winner_team, utc_now(), match_id),
            )
            if cursor.rowcount != 1:
                return False

            if winner_team:
                team_a = [int(user_id) for user_id in json.loads(row["team_a_json"])]
                team_b = [int(user_id) for user_id in json.loads(row["team_b_json"])]
                winners, losers = (
                    (team_a, team_b) if winner_team == "A" else (team_b, team_a)
                )
                for user_id in winners:
                    self.connection.execute(
                        """
                        INSERT INTO player_stats (user_id, wins, losses)
                        VALUES (?, 1, 0)
                        ON CONFLICT(user_id) DO UPDATE SET wins = wins + 1
                        """,
                        (user_id,),
                    )
                for user_id in losers:
                    self.connection.execute(
                        """
                        INSERT INTO player_stats (user_id, wins, losses)
                        VALUES (?, 0, 1)
                        ON CONFLICT(user_id) DO UPDATE SET losses = losses + 1
                        """,
                        (user_id,),
                    )
        return True

    def get_player_stats(self, user_id: int) -> PlayerStats:
        with self.lock:
            row = self.connection.execute(
                """
                SELECT user_id, wins, losses
                FROM player_stats
                WHERE user_id = ?
                """,
                (user_id,),
            ).fetchone()
        if not row:
            return PlayerStats(user_id=user_id, wins=0, losses=0)
        return PlayerStats(
            user_id=int(row["user_id"]),
            wins=int(row["wins"]),
            losses=int(row["losses"]),
        )

    def list_hall_of_fame(
        self, guild_id: int, limit: int = 10
    ) -> list[HallOfFameEntry]:
        limit = max(1, min(int(limit), 25))
        with self.lock:
            rows = self.connection.execute(
                """
                SELECT user_id, COUNT(*) AS title_count
                FROM tournament_titles
                WHERE guild_id = ?
                GROUP BY user_id
                ORDER BY title_count DESC, user_id ASC
                LIMIT ?
                """,
                (guild_id, limit),
            ).fetchall()
        return [
            HallOfFameEntry(int(row["user_id"]), int(row["title_count"]))
            for row in rows
        ]

    def get_player_title_count(self, guild_id: int, user_id: int) -> int:
        with self.lock:
            row = self.connection.execute(
                """
                SELECT COUNT(*) AS title_count
                FROM tournament_titles
                WHERE guild_id = ? AND user_id = ?
                """,
                (guild_id, user_id),
            ).fetchone()
        return int(row["title_count"]) if row else 0

    def get_player_title_history(
        self, guild_id: int, user_id: int, limit: int = 10
    ) -> list[PlayerTitle]:
        limit = max(1, min(int(limit), 25))
        with self.lock:
            rows = self.connection.execute(
                """
                SELECT tournament_id, team_id, awarded_at
                FROM tournament_titles
                WHERE guild_id = ? AND user_id = ?
                ORDER BY awarded_at DESC, tournament_id DESC
                LIMIT ?
                """,
                (guild_id, user_id, limit),
            ).fetchall()
        return [
            PlayerTitle(
                int(row["tournament_id"]),
                int(row["team_id"]),
                str(row["awarded_at"]),
            )
            for row in rows
        ]

    def list_recent_tournament_champions(
        self, guild_id: int, limit: int = 5
    ) -> list[TournamentChampion]:
        limit = max(1, min(int(limit), 10))
        with self.lock:
            rows = self.connection.execute(
                """
                SELECT t.tournament_id, t.champion_pair_id, t.finished_at,
                       team.members_json,
                       legacy.player_a_id, legacy.player_b_id
                FROM tournaments AS t
                LEFT JOIN tournament_teams AS team
                  ON team.tournament_id = t.tournament_id
                 AND team.team_id = t.champion_pair_id
                LEFT JOIN tournament_pairs AS legacy
                  ON legacy.tournament_id = t.tournament_id
                 AND legacy.pair_id = t.champion_pair_id
                WHERE t.guild_id = ?
                  AND t.status = 'finished'
                  AND t.champion_pair_id IS NOT NULL
                ORDER BY COALESCE(t.finished_at, t.created_at) DESC,
                         t.tournament_id DESC
                LIMIT ?
                """,
                (guild_id, limit),
            ).fetchall()
        champions = []
        for row in rows:
            if row["members_json"] is not None:
                player_ids = [int(value) for value in json.loads(row["members_json"])]
            elif row["player_a_id"] is not None and row["player_b_id"] is not None:
                player_ids = [int(row["player_a_id"]), int(row["player_b_id"])]
            else:
                player_ids = []
            champions.append(
                TournamentChampion(
                    int(row["tournament_id"]),
                    int(row["champion_pair_id"]),
                    player_ids,
                    str(row["finished_at"]) if row["finished_at"] else None,
                )
            )
        return champions

    @staticmethod
    def _tournament_from_row(row: sqlite3.Row) -> TournamentRecord:
        mode = str(row["mode"]) if "mode" in row.keys() else "2v2"
        team_size = int(row["team_size"]) if "team_size" in row.keys() else 2
        return TournamentRecord(
            tournament_id=int(row["tournament_id"]),
            guild_id=int(row["guild_id"]),
            mode=mode,
            team_size=team_size,
            capacity=int(row["capacity"]),
            status=str(row["status"]),
            created_by=int(row["created_by"]),
            champion_pair_id=(int(row["champion_pair_id"]) if row["champion_pair_id"] is not None else None),
            panel_channel_id=(int(row["panel_channel_id"]) if row["panel_channel_id"] is not None else None),
            panel_message_id=(int(row["panel_message_id"]) if row["panel_message_id"] is not None else None),
            created_at=(str(row["created_at"]) if row["created_at"] is not None else None),
            started_at=(
                str(row["started_at"])
                if "started_at" in row.keys() and row["started_at"] is not None
                else None
            ),
            channel_cleanup_at=(
                str(row["channel_cleanup_at"])
                if "channel_cleanup_at" in row.keys()
                and row["channel_cleanup_at"] is not None
                else None
            ),
            channel_cleanup_done_at=(
                str(row["channel_cleanup_done_at"])
                if "channel_cleanup_done_at" in row.keys()
                and row["channel_cleanup_done_at"] is not None
                else None
            ),
            prize_amount_cents=(
                int(row["prize_amount_cents"])
                if "prize_amount_cents" in row.keys()
                and row["prize_amount_cents"] is not None
                else None
            ),
        )


    @staticmethod
    def _tournament_pair_from_row(row: sqlite3.Row) -> TournamentPair:
        if "members_json" in row.keys():
            players = [int(x) for x in json.loads(row["members_json"])]
            return TournamentPair(
                pair_id=int(row["team_id"]),
                tournament_id=int(row["tournament_id"]),
                players=players,
                slot=int(row["slot"]),
            )
        return TournamentPair(
            pair_id=int(row["pair_id"]),
            tournament_id=int(row["tournament_id"]),
            players=[int(row["player_a_id"]), int(row["player_b_id"])],
            slot=int(row["slot"]),
        )


    @staticmethod
    def _bracket_match_from_row(row: sqlite3.Row) -> TournamentBracketMatch:
        return TournamentBracketMatch(
            bracket_match_id=int(row["bracket_match_id"]),
            tournament_id=int(row["tournament_id"]),
            round_number=int(row["round_number"]),
            match_number=int(row["match_number"]),
            pair_a_id=int(row["pair_a_id"]),
            pair_b_id=int(row["pair_b_id"]),
            winner_pair_id=(int(row["winner_pair_id"]) if row["winner_pair_id"] is not None else None),
            status=str(row["status"]),
            regular_match_id=(int(row["regular_match_id"]) if row["regular_match_id"] is not None else None),
        )

    def get_tournament(self, tournament_id: int) -> TournamentRecord | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM tournaments WHERE tournament_id = ?",
                (tournament_id,),
            ).fetchone()
        return self._tournament_from_row(row) if row else None

    def get_open_tournament(self, guild_id: int) -> TournamentRecord | None:
        with self.lock:
            row = self.connection.execute(
                """
                SELECT *
                FROM tournaments
                WHERE guild_id = ? AND status IN ('registration', 'ready', 'active')
                ORDER BY tournament_id DESC
                LIMIT 1
                """,
                (guild_id,),
            ).fetchone()
        return self._tournament_from_row(row) if row else None

    def list_pending_tournament_channel_cleanups(
        self, guild_id: int
    ) -> list[TournamentRecord]:
        with self.lock:
            rows = self.connection.execute(
                """
                SELECT *
                FROM tournaments
                WHERE guild_id = ?
                  AND status = 'finished'
                  AND channel_cleanup_at IS NOT NULL
                  AND channel_cleanup_done_at IS NULL
                ORDER BY channel_cleanup_at ASC
                """,
                (guild_id,),
            ).fetchall()
        return [self._tournament_from_row(row) for row in rows]

    def mark_tournament_channel_cleanup_done(
        self, tournament_id: int, expected_cleanup_at: str
    ) -> bool:
        with self.lock, self.connection:
            cursor = self.connection.execute(
                """
                UPDATE tournaments
                SET channel_cleanup_done_at = ?
                WHERE tournament_id = ?
                  AND status = 'finished'
                  AND channel_cleanup_at = ?
                  AND channel_cleanup_done_at IS NULL
                """,
                (utc_now(), tournament_id, expected_cleanup_at),
            )
        return cursor.rowcount == 1

    def create_tournament(self, guild_id: int, capacity: int, created_by: int, mode: str = "2v2") -> int | None:
        if capacity not in (4, 8, 16):
            raise ValueError("Tournament capacity must be 4, 8, or 16")
        team_size = {"1v1": 1, "2v2": 2, "3v3": 3, "4v4": 4}.get(mode)
        if team_size is None:
            raise ValueError("Invalid tournament mode")
        with self.lock, self.connection:
            existing = self.connection.execute(
                "SELECT 1 FROM tournaments WHERE guild_id = ? AND status IN ('registration', 'ready', 'active') LIMIT 1",
                (guild_id,),
            ).fetchone()
            if existing:
                return None
            row = self.connection.execute("SELECT COALESCE(MAX(tournament_id), 0) + 1 AS next_id FROM tournaments").fetchone()
            tournament_id = int(row["next_id"])
            self.connection.execute(
                """INSERT INTO tournaments
                (tournament_id, guild_id, capacity, mode, team_size, created_by, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (tournament_id, guild_id, capacity, mode, team_size, created_by, utc_now()),
            )
        return tournament_id

    def update_tournament_panel(
        self, tournament_id: int, channel_id: int, message_id: int
    ) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                """
                UPDATE tournaments
                SET panel_channel_id = ?, panel_message_id = ?
                WHERE tournament_id = ?
                """,
                (channel_id, message_id, tournament_id),
            )

    def set_tournament_prize(
        self, tournament_id: int, guild_id: int, amount_cents: int
    ) -> str:
        if amount_cents <= 0:
            return "invalid_amount"
        with self.lock, self.connection:
            row = self.connection.execute(
                "SELECT status FROM tournaments WHERE tournament_id = ? AND guild_id = ?",
                (tournament_id, guild_id),
            ).fetchone()
            if not row:
                return "not_found"
            if str(row["status"]) not in {"registration", "ready"}:
                return "closed"
            self.connection.execute(
                """
                UPDATE tournaments
                SET prize_amount_cents = ?
                WHERE tournament_id = ? AND guild_id = ?
                  AND status IN ('registration', 'ready')
                """,
                (amount_cents, tournament_id, guild_id),
            )
        return "saved"

    @staticmethod
    def _payment_batch_from_row(row: sqlite3.Row) -> TournamentPaymentBatch:
        return TournamentPaymentBatch(
            tournament_id=int(row["tournament_id"]),
            guild_id=int(row["guild_id"]),
            champion_team_id=int(row["champion_team_id"]),
            total_amount_cents=(
                int(row["total_amount_cents"])
                if row["total_amount_cents"] is not None
                else None
            ),
            channel_id=int(row["channel_id"]) if row["channel_id"] is not None else None,
            summary_message_id=(
                int(row["summary_message_id"])
                if row["summary_message_id"] is not None
                else None
            ),
            status=str(row["status"]),
            close_at=str(row["close_at"]) if row["close_at"] is not None else None,
            close_done_at=(
                str(row["close_done_at"])
                if row["close_done_at"] is not None
                else None
            ),
            finalized_by=(
                int(row["finalized_by"])
                if row["finalized_by"] is not None
                else None
            ),
            finalized_at=(
                str(row["finalized_at"])
                if row["finalized_at"] is not None
                else None
            ),
        )

    @staticmethod
    def _tournament_payout_from_row(row: sqlite3.Row) -> TournamentPayout:
        return TournamentPayout(
            tournament_id=int(row["tournament_id"]),
            guild_id=int(row["guild_id"]),
            user_id=int(row["user_id"]),
            amount_cents=(
                int(row["amount_cents"]) if row["amount_cents"] is not None else None
            ),
            pix_key=str(row["pix_key"]) if row["pix_key"] is not None else None,
            recipient_name=(
                str(row["recipient_name"])
                if row["recipient_name"] is not None
                else None
            ),
            recipient_city=(
                str(row["recipient_city"])
                if row["recipient_city"] is not None
                else None
            ),
            pix_submitted_at=(
                str(row["pix_submitted_at"])
                if row["pix_submitted_at"] is not None
                else None
            ),
            status=str(row["status"]),
            message_id=int(row["message_id"]) if row["message_id"] is not None else None,
            paid_by=int(row["paid_by"]) if row["paid_by"] is not None else None,
            paid_at=str(row["paid_at"]) if row["paid_at"] is not None else None,
        )

    def get_tournament_payment_batch(
        self, tournament_id: int
    ) -> TournamentPaymentBatch | None:
        with self.lock:
            row = self.connection.execute(
                "SELECT * FROM tournament_payment_batches WHERE tournament_id = ?",
                (tournament_id,),
            ).fetchone()
        return self._payment_batch_from_row(row) if row else None

    def list_tournament_payment_batches(
        self,
        guild_id: int,
        statuses: tuple[str, ...] | None = None,
    ) -> list[TournamentPaymentBatch]:
        query = "SELECT * FROM tournament_payment_batches WHERE guild_id = ?"
        params: list[Any] = [guild_id]
        if statuses is not None:
            if not statuses:
                return []
            query += f" AND status IN ({','.join('?' for _ in statuses)})"
            params.extend(statuses)
        query += " ORDER BY tournament_id ASC"
        with self.lock:
            rows = self.connection.execute(query, params).fetchall()
        return [self._payment_batch_from_row(row) for row in rows]

    def list_tournament_payouts(
        self, tournament_id: int
    ) -> list[TournamentPayout]:
        with self.lock:
            rows = self.connection.execute(
                """
                SELECT * FROM tournament_payouts
                WHERE tournament_id = ?
                ORDER BY user_id ASC
                """,
                (tournament_id,),
            ).fetchall()
        return [self._tournament_payout_from_row(row) for row in rows]

    def get_tournament_payout(
        self, tournament_id: int, user_id: int
    ) -> TournamentPayout | None:
        with self.lock:
            row = self.connection.execute(
                """
                SELECT * FROM tournament_payouts
                WHERE tournament_id = ? AND user_id = ?
                """,
                (tournament_id, user_id),
            ).fetchone()
        return self._tournament_payout_from_row(row) if row else None

    def set_tournament_payment_channel(
        self, tournament_id: int, channel_id: int
    ) -> bool:
        with self.lock, self.connection:
            batch = self.connection.execute(
                """
                SELECT channel_id, status FROM tournament_payment_batches
                WHERE tournament_id = ?
                """,
                (tournament_id,),
            ).fetchone()
            if not batch or str(batch["status"]) not in {"provisioning", "open"}:
                return False
            old_channel_id = (
                int(batch["channel_id"]) if batch["channel_id"] is not None else None
            )
            if old_channel_id != channel_id:
                self.connection.execute(
                    """
                    UPDATE tournament_payment_batches
                    SET channel_id = ?, summary_message_id = NULL
                    WHERE tournament_id = ?
                    """,
                    (channel_id, tournament_id),
                )
                self.connection.execute(
                    """
                    UPDATE tournament_payouts SET message_id = NULL
                    WHERE tournament_id = ?
                    """,
                    (tournament_id,),
                )
            return True

    def set_tournament_payment_summary_message_id(
        self, tournament_id: int, message_id: int
    ) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                """
                UPDATE tournament_payment_batches
                SET summary_message_id = ?
                WHERE tournament_id = ? AND status IN ('provisioning', 'open')
                """,
                (message_id, tournament_id),
            )

    def set_tournament_payout_message_id(
        self, tournament_id: int, user_id: int, message_id: int
    ) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                """
                UPDATE tournament_payouts
                SET message_id = ?
                WHERE tournament_id = ? AND user_id = ?
                  AND EXISTS (
                    SELECT 1 FROM tournament_payment_batches
                    WHERE tournament_id = ? AND status IN ('provisioning', 'open')
                  )
                """,
                (message_id, tournament_id, user_id, tournament_id),
            )

    def mark_tournament_payment_batch_open(self, tournament_id: int) -> bool:
        with self.lock, self.connection:
            batch = self.connection.execute(
                """
                SELECT summary_message_id, status
                FROM tournament_payment_batches
                WHERE tournament_id = ?
                """,
                (tournament_id,),
            ).fetchone()
            if (
                not batch
                or batch["summary_message_id"] is None
                or str(batch["status"]) not in {"provisioning", "open"}
            ):
                return False
            missing = self.connection.execute(
                """
                SELECT 1 FROM tournament_payouts
                WHERE tournament_id = ? AND message_id IS NULL LIMIT 1
                """,
                (tournament_id,),
            ).fetchone()
            if missing:
                return False
            self.connection.execute(
                """
                UPDATE tournament_payment_batches
                SET status = 'open'
                WHERE tournament_id = ? AND status IN ('provisioning', 'open')
                """,
                (tournament_id,),
            )
        return True

    def submit_tournament_pix(
        self,
        tournament_id: int,
        user_id: int,
        pix_key: str,
        recipient_name: str,
        recipient_city: str,
    ) -> str:
        with self.lock, self.connection:
            batch = self.connection.execute(
                "SELECT status FROM tournament_payment_batches WHERE tournament_id = ?",
                (tournament_id,),
            ).fetchone()
            if not batch:
                return "not_found"
            if str(batch["status"]) not in {"provisioning", "open"}:
                return "closed"
            payout = self.connection.execute(
                """
                SELECT status FROM tournament_payouts
                WHERE tournament_id = ? AND user_id = ?
                """,
                (tournament_id, user_id),
            ).fetchone()
            if not payout:
                return "not_found"
            if str(payout["status"]) == "paid":
                return "paid"
            self.connection.execute(
                """
                UPDATE tournament_payouts
                SET pix_key = ?, recipient_name = ?, recipient_city = ?,
                    pix_submitted_at = ?
                WHERE tournament_id = ? AND user_id = ? AND status = 'pending'
                """,
                (
                    pix_key,
                    recipient_name,
                    recipient_city,
                    utc_now(),
                    tournament_id,
                    user_id,
                ),
            )
        return "saved"

    def mark_tournament_payout_paid(
        self, tournament_id: int, user_id: int, admin_id: int
    ) -> str:
        with self.lock, self.connection:
            batch = self.connection.execute(
                "SELECT status FROM tournament_payment_batches WHERE tournament_id = ?",
                (tournament_id,),
            ).fetchone()
            if not batch:
                return "not_found"
            if str(batch["status"]) != "open":
                return "closed"
            payout = self.connection.execute(
                """
                SELECT amount_cents, pix_key, recipient_name, recipient_city, status
                FROM tournament_payouts
                WHERE tournament_id = ? AND user_id = ?
                """,
                (tournament_id, user_id),
            ).fetchone()
            if not payout:
                return "not_found"
            if str(payout["status"]) == "paid":
                return "already_paid"
            if (
                payout["amount_cents"] is None
                or int(payout["amount_cents"]) <= 0
                or not payout["pix_key"]
                or not payout["recipient_name"]
                or not payout["recipient_city"]
            ):
                return "not_ready"
            self.connection.execute(
                """
                UPDATE tournament_payouts
                SET status = 'paid', paid_by = ?, paid_at = ?
                WHERE tournament_id = ? AND user_id = ? AND status = 'pending'
                """,
                (admin_id, utc_now(), tournament_id, user_id),
            )
        return "paid"

    def begin_tournament_payment_finalization(
        self, tournament_id: int, guild_id: int, admin_id: int
    ) -> tuple[str, int]:
        with self.lock, self.connection:
            batch = self.connection.execute(
                """
                SELECT status, total_amount_cents
                FROM tournament_payment_batches
                WHERE tournament_id = ? AND guild_id = ?
                """,
                (tournament_id, guild_id),
            ).fetchone()
            if not batch:
                return "not_found", 0
            if str(batch["status"]) != "open":
                return "not_open", 0
            if (
                batch["total_amount_cents"] is not None
                and int(batch["total_amount_cents"]) > 0
            ):
                unpaid = int(
                    self.connection.execute(
                        """
                        SELECT COUNT(*) FROM tournament_payouts
                        WHERE tournament_id = ? AND status <> 'paid'
                        """,
                        (tournament_id,),
                    ).fetchone()[0]
                )
                if unpaid:
                    return "unpaid", unpaid
            finalized_at = utc_now()
            close_at = (
                datetime.fromisoformat(finalized_at) + timedelta(seconds=60)
            ).isoformat()
            cursor = self.connection.execute(
                """
                UPDATE tournament_payment_batches
                SET status = 'closing', close_at = ?,
                    finalized_by = ?, finalized_at = ?
                WHERE tournament_id = ? AND guild_id = ? AND status = 'open'
                """,
                (close_at, admin_id, finalized_at, tournament_id, guild_id),
            )
            return ("scheduled", 0) if cursor.rowcount == 1 else ("not_open", 0)

    def list_pending_tournament_payment_closures(
        self, guild_id: int
    ) -> list[TournamentPaymentBatch]:
        with self.lock:
            rows = self.connection.execute(
                """
                SELECT * FROM tournament_payment_batches
                WHERE guild_id = ? AND status = 'closing' AND close_at IS NOT NULL
                  AND close_done_at IS NULL
                ORDER BY close_at ASC
                """,
                (guild_id,),
            ).fetchall()
        return [self._payment_batch_from_row(row) for row in rows]

    def mark_tournament_payment_batch_closed(
        self, tournament_id: int, expected_close_at: str
    ) -> bool:
        with self.lock, self.connection:
            cursor = self.connection.execute(
                """
                UPDATE tournament_payment_batches
                SET status = 'closed', close_done_at = ?
                WHERE tournament_id = ? AND status = 'closing'
                  AND close_at = ? AND close_done_at IS NULL
                """,
                (utc_now(), tournament_id, expected_close_at),
            )
        return cursor.rowcount == 1

    def update_tournament_capacity(
        self, tournament_id: int, capacity: int
    ) -> tuple[str, int]:
        if capacity not in (4, 8, 16):
            return "invalid_capacity", 0
        with self.lock, self.connection:
            row = self.connection.execute(
                "SELECT status FROM tournaments WHERE tournament_id = ?",
                (tournament_id,),
            ).fetchone()
            if not row:
                return "not_found", 0
            registered = len(self.list_tournament_pairs(tournament_id))
            if row["status"] != "registration":
                return "closed", registered
            if registered > capacity:
                return "too_small", registered
            self.connection.execute(
                """
                UPDATE tournaments
                SET capacity = ?
                WHERE tournament_id = ? AND status = 'registration'
                """,
                (capacity, tournament_id),
            )
        return "updated", registered

    def list_tournament_pairs(self, tournament_id: int) -> list[TournamentPair]:
        with self.lock:
            rows = self.connection.execute(
                "SELECT * FROM tournament_teams WHERE tournament_id = ? ORDER BY slot ASC",
                (tournament_id,),
            ).fetchall()
            if rows:
                return [self._tournament_pair_from_row(row) for row in rows]
            # Backwards compatibility for the old 2x2-only tournament records.
            rows = self.connection.execute(
                "SELECT * FROM tournament_pairs WHERE tournament_id = ? ORDER BY slot ASC",
                (tournament_id,),
            ).fetchall()
        return [self._tournament_pair_from_row(row) for row in rows]

    def add_tournament_pair(
        self, tournament_id: int, player_ids: list[int] | tuple[int, ...] | int, player_b_id: int | None = None
    ) -> tuple[str, TournamentPair | None]:
        if isinstance(player_ids, int):
            player_ids = [player_ids] + ([player_b_id] if player_b_id is not None else [])
        players = [int(x) for x in player_ids]
        with self.lock, self.connection:
            tournament_row = self.connection.execute("SELECT * FROM tournaments WHERE tournament_id = ?", (tournament_id,)).fetchone()
            if not tournament_row:
                return "not_found", None
            tournament = self._tournament_from_row(tournament_row)
            if tournament.status != "registration":
                return "closed", None
            if len(players) != tournament.team_size or len(set(players)) != len(players):
                return "invalid_team", None
            if any(p <= 0 for p in players):
                return "invalid_team", None
            count_row = self.connection.execute("SELECT COUNT(*) AS total FROM tournament_teams WHERE tournament_id = ?", (tournament_id,)).fetchone()
            count = int(count_row["total"])
            if count >= tournament.capacity:
                return "full", None
            placeholders = ",".join("?" for _ in players)
            # Check all player IDs against teams already registered in this tournament.
            existing_rows = self.connection.execute(
                "SELECT members_json, team_id, slot FROM tournament_teams WHERE tournament_id = ?",
                (tournament_id,),
            ).fetchall()
            wanted = set(players)
            for row in existing_rows:
                existing_players = set(int(x) for x in json.loads(row["members_json"]))
                if wanted & existing_players:
                    if wanted == existing_players:
                        return "duplicate_pair", self._tournament_pair_from_row(row)
                    return "player_registered", self._tournament_pair_from_row(row)
            slot = count + 1
            cursor = self.connection.execute(
                "INSERT INTO tournament_teams (tournament_id, members_json, slot, created_at) VALUES (?, ?, ?, ?)",
                (tournament_id, json.dumps(players), slot, utc_now()),
            )
            team_id = int(cursor.lastrowid)
            return "added", TournamentPair(team_id, tournament_id, players, slot)

    def remove_tournament_pair(self, tournament_id: int, pair_id: int) -> bool:
        with self.lock, self.connection:
            row = self.connection.execute(
                "SELECT team_id FROM tournament_teams WHERE tournament_id = ? AND team_id = ?",
                (tournament_id, pair_id),
            ).fetchone()
            if not row:
                return False
            self.connection.execute("DELETE FROM tournament_teams WHERE team_id = ?", (pair_id,))
            rows = self.connection.execute("SELECT team_id FROM tournament_teams WHERE tournament_id = ? ORDER BY slot", (tournament_id,)).fetchall()
            for index, item in enumerate(rows, 1):
                self.connection.execute("UPDATE tournament_teams SET slot = ? WHERE team_id = ?", (index, int(item["team_id"])))
        return True

    def close_tournament(self, tournament_id: int) -> tuple[str, list[int]]:
        with self.lock, self.connection:
            tournament_row = self.connection.execute(
                "SELECT * FROM tournaments WHERE tournament_id = ?",
                (tournament_id,),
            ).fetchone()
            if not tournament_row:
                return "not_found", []
            tournament = self._tournament_from_row(tournament_row)
            if tournament.status != "registration":
                return "closed", []
            pairs = self.connection.execute(
                """
                SELECT team_id AS pair_id
                FROM tournament_teams
                WHERE tournament_id = ?
                ORDER BY slot ASC
                """,
                (tournament_id,),
            ).fetchall()
            if len(pairs) != tournament.capacity:
                return "not_full", []

            now = utc_now()
            self.connection.execute(
                """
                UPDATE tournaments
                SET status = 'ready'
                WHERE tournament_id = ? AND status = 'registration'
                """,
                (tournament_id,),
            )
        return "ready", []

    def start_tournament(self, tournament_id: int) -> tuple[str, list[int]]:
        with self.lock, self.connection:
            tournament_row = self.connection.execute("SELECT * FROM tournaments WHERE tournament_id = ?", (tournament_id,)).fetchone()
            if not tournament_row:
                return "not_found", []
            tournament = self._tournament_from_row(tournament_row)
            if tournament.status != "ready":
                return "not_ready", []
            teams = self.connection.execute("SELECT team_id FROM tournament_teams WHERE tournament_id = ? ORDER BY slot ASC", (tournament_id,)).fetchall()
            if len(teams) != tournament.capacity:
                return "not_full", []
            existing_bracket = self.connection.execute(
                "SELECT 1 FROM tournament_bracket_matches WHERE tournament_id = ? LIMIT 1",
                (tournament_id,),
            ).fetchone()
            if existing_bracket:
                return "bracket_exists", []
            team_ids = [int(row["team_id"]) for row in teams]
            plan = self.connection.execute(
                """
                SELECT semifinal_1_pair_a_id, semifinal_1_pair_b_id,
                       semifinal_2_pair_a_id, semifinal_2_pair_b_id
                FROM tournament_matchup_plans
                WHERE tournament_id = ?
                """,
                (tournament_id,),
            ).fetchone()
            if plan:
                planned_team_ids = [int(plan[key]) for key in plan.keys()]
                if (
                    tournament.capacity != 4
                    or len(set(planned_team_ids)) != 4
                    or set(planned_team_ids) != set(team_ids)
                ):
                    return "invalid_bracket", []
                team_ids = planned_team_ids
            now = utc_now()
            self.connection.execute("UPDATE tournaments SET status = 'active', started_at = ? WHERE tournament_id = ? AND status = 'ready'", (now, tournament_id))
            bracket_ids=[]
            for index in range(0,len(team_ids),2):
                cur=self.connection.execute("""INSERT INTO tournament_bracket_matches
                    (tournament_id, round_number, match_number, pair_a_id, pair_b_id, status, created_at)
                    VALUES (?,1,?,?,?,'pending',?)""", (tournament_id,index//2+1,team_ids[index],team_ids[index+1],now))
                bracket_ids.append(int(cur.lastrowid))
        return "started", bracket_ids

    def get_tournament_matchup_plan(
        self, tournament_id: int
    ) -> tuple[int, int, int, int] | None:
        with self.lock:
            row = self.connection.execute(
                """
                SELECT semifinal_1_pair_a_id, semifinal_1_pair_b_id,
                       semifinal_2_pair_a_id, semifinal_2_pair_b_id
                FROM tournament_matchup_plans
                WHERE tournament_id = ?
                """,
                (tournament_id,),
            ).fetchone()
        if not row:
            return None
        return tuple(int(row[key]) for key in row.keys())

    def set_tournament_matchup_plan(
        self, tournament_id: int, pair_ids: tuple[int, int, int, int]
    ) -> str:
        if len(pair_ids) != 4 or len(set(pair_ids)) != 4:
            return "invalid_teams"
        with self.lock, self.connection:
            tournament_row = self.connection.execute(
                "SELECT status, capacity, mode FROM tournaments WHERE tournament_id = ?",
                (tournament_id,),
            ).fetchone()
            if not tournament_row:
                return "not_found"
            if (
                str(tournament_row["status"]) != "ready"
                or int(tournament_row["capacity"]) != 4
                or str(tournament_row["mode"]) != "2v2"
            ):
                return "not_ready"
            if self.connection.execute(
                "SELECT 1 FROM tournament_bracket_matches WHERE tournament_id = ? LIMIT 1",
                (tournament_id,),
            ).fetchone():
                return "already_started"
            teams = self.connection.execute(
                "SELECT team_id FROM tournament_teams WHERE tournament_id = ?",
                (tournament_id,),
            ).fetchall()
            registered_ids = {int(row["team_id"]) for row in teams}
            if len(registered_ids) != 4 or set(pair_ids) != registered_ids:
                return "invalid_teams"
            self.connection.execute(
                """
                INSERT INTO tournament_matchup_plans (
                    tournament_id, semifinal_1_pair_a_id, semifinal_1_pair_b_id,
                    semifinal_2_pair_a_id, semifinal_2_pair_b_id, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT(tournament_id) DO UPDATE SET
                    semifinal_1_pair_a_id = excluded.semifinal_1_pair_a_id,
                    semifinal_1_pair_b_id = excluded.semifinal_1_pair_b_id,
                    semifinal_2_pair_a_id = excluded.semifinal_2_pair_a_id,
                    semifinal_2_pair_b_id = excluded.semifinal_2_pair_b_id,
                    updated_at = excluded.updated_at
                """,
                (tournament_id, *pair_ids, utc_now()),
            )
        return "saved"

    def list_tournament_bracket_matches(
        self, tournament_id: int, statuses: tuple[str, ...] | None = None
    ) -> list[TournamentBracketMatch]:
        query = """
            SELECT *
            FROM tournament_bracket_matches
            WHERE tournament_id = ?
        """
        params: list[Any] = [tournament_id]
        if statuses:
            placeholders = ",".join("?" for _ in statuses)
            query += f" AND status IN ({placeholders})"
            params.extend(statuses)
        query += " ORDER BY round_number ASC, match_number ASC"
        with self.lock:
            rows = self.connection.execute(query, params).fetchall()
        return [self._bracket_match_from_row(row) for row in rows]

    def get_tournament_bracket_match(
        self, bracket_match_id: int
    ) -> TournamentBracketMatch | None:
        with self.lock:
            row = self.connection.execute(
                """
                SELECT *
                FROM tournament_bracket_matches
                WHERE bracket_match_id = ?
                """,
                (bracket_match_id,),
            ).fetchone()
        return self._bracket_match_from_row(row) if row else None

    def get_tournament_bracket_for_match(
        self, regular_match_id: int
    ) -> TournamentBracketMatch | None:
        with self.lock:
            row = self.connection.execute(
                """
                SELECT *
                FROM tournament_bracket_matches
                WHERE regular_match_id = ?
                """,
                (regular_match_id,),
            ).fetchone()
        return self._bracket_match_from_row(row) if row else None

    def get_tournament_pair(self, pair_id: int) -> TournamentPair | None:
        with self.lock:
            row = self.connection.execute("SELECT * FROM tournament_teams WHERE team_id = ?", (pair_id,)).fetchone()
            if row:
                return self._tournament_pair_from_row(row)
            row = self.connection.execute("SELECT * FROM tournament_pairs WHERE pair_id = ?", (pair_id,)).fetchone()
        return self._tournament_pair_from_row(row) if row else None

    def cancel_tournament(self, tournament_id: int) -> bool:
        with self.lock, self.connection:
            row = self.connection.execute("SELECT status FROM tournaments WHERE tournament_id = ?", (tournament_id,)).fetchone()
            if not row or str(row["status"]) == "cancelled":
                return False
            self.connection.execute("UPDATE tournaments SET status = 'cancelled', finished_at = ? WHERE tournament_id = ?", (utc_now(), tournament_id))
            self.connection.execute("UPDATE tournament_bracket_matches SET status = 'cancelled', ended_at = ? WHERE tournament_id = ? AND status IN ('pending','active')", (utc_now(), tournament_id))
        return True

    def bracket_embed_data(self, tournament_id: int) -> list[TournamentBracketMatch]:
        return self.list_tournament_bracket_matches(tournament_id)

    def assign_tournament_match(
        self, bracket_match_id: int, regular_match_id: int
    ) -> bool:
        with self.lock, self.connection:
            cursor = self.connection.execute(
                """
                UPDATE tournament_bracket_matches
                SET regular_match_id = ?, status = 'active'
                WHERE bracket_match_id = ?
                  AND status = 'pending'
                  AND regular_match_id IS NULL
                """,
                (regular_match_id, bracket_match_id),
            )
        return cursor.rowcount == 1

    def clear_tournament_match_assignment(self, bracket_match_id: int) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                """
                UPDATE tournament_bracket_matches
                SET regular_match_id = NULL, status = 'pending'
                WHERE bracket_match_id = ? AND status = 'active'
                """,
                (bracket_match_id,),
            )

    def finish_tournament_match(
        self, regular_match_id: int, winner_team: str | None
    ) -> TournamentResult | None:
        if winner_team not in (None, "A", "B"):
            raise ValueError("winner_team must be None, 'A', or 'B'")
        with self.lock, self.connection:
            regular_match = self.connection.execute(
                """
                SELECT status, team_a_json, team_b_json
                FROM matches
                WHERE match_id = ?
                """,
                (regular_match_id,),
            ).fetchone()
            bracket_row = self.connection.execute(
                """
                SELECT *
                FROM tournament_bracket_matches
                WHERE regular_match_id = ?
                """,
                (regular_match_id,),
            ).fetchone()
            if (
                not regular_match
                or str(regular_match["status"]) != "active"
                or not bracket_row
                or str(bracket_row["status"]) != "active"
            ):
                return None

            bracket = self._bracket_match_from_row(bracket_row)
            cursor = self.connection.execute(
                """
                UPDATE matches
                SET status = 'finished', winner_team = ?, ended_at = ?
                WHERE match_id = ? AND status = 'active'
                """,
                (winner_team, utc_now(), regular_match_id),
            )
            if cursor.rowcount != 1:
                return None

            winner_pair_id: int | None = None
            if winner_team:
                team_a = [int(user_id) for user_id in json.loads(regular_match["team_a_json"])]
                team_b = [int(user_id) for user_id in json.loads(regular_match["team_b_json"])]
                winners, losers = (
                    (team_a, team_b) if winner_team == "A" else (team_b, team_a)
                )
                winner_pair_id = bracket.pair_a_id if winner_team == "A" else bracket.pair_b_id
                for user_id in winners:
                    self._increment_player_stats(user_id, wins=1, losses=0)
                for user_id in losers:
                    self._increment_player_stats(user_id, wins=0, losses=1)

            ended_at = utc_now()
            self.connection.execute(
                """
                UPDATE tournament_bracket_matches
                SET status = 'finished', winner_pair_id = ?, ended_at = ?
                WHERE bracket_match_id = ? AND status = 'active'
                """,
                (winner_pair_id, ended_at, bracket.bracket_match_id),
            )

            next_ids: list[int] = []
            champion_pair_id: int | None = None
            current_round_rows = self.connection.execute(
                """
                SELECT *
                FROM tournament_bracket_matches
                WHERE tournament_id = ? AND round_number = ?
                ORDER BY match_number ASC
                """,
                (bracket.tournament_id, bracket.round_number),
            ).fetchall()
            round_complete = bool(current_round_rows) and all(
                str(row["status"]) == "finished" for row in current_round_rows
            )
            has_all_winners = round_complete and all(
                row["winner_pair_id"] is not None for row in current_round_rows
            )
            tournament_row = self.connection.execute(
                """
                SELECT capacity, guild_id, prize_amount_cents
                FROM tournaments
                WHERE tournament_id = ?
                """,
                (bracket.tournament_id,),
            ).fetchone()
            max_round = int(math.log2(int(tournament_row["capacity"]))) if tournament_row else 0
            if has_all_winners:
                if bracket.round_number >= max_round:
                    champion_pair_id = int(current_round_rows[0]["winner_pair_id"])
                    cleanup_at = (
                        datetime.fromisoformat(ended_at) + timedelta(seconds=60)
                    ).isoformat()
                    finished_tournament = self.connection.execute(
                        """
                        UPDATE tournaments
                        SET status = 'finished',
                            champion_pair_id = ?,
                            finished_at = ?,
                            channel_cleanup_at = ?,
                            channel_cleanup_done_at = NULL
                        WHERE tournament_id = ? AND status = 'active'
                        """,
                        (
                            champion_pair_id,
                            ended_at,
                            cleanup_at,
                            bracket.tournament_id,
                        ),
                    )
                    if finished_tournament.rowcount == 1 and tournament_row:
                        team = self.connection.execute(
                            """
                            SELECT members_json
                            FROM tournament_teams
                            WHERE tournament_id = ? AND team_id = ?
                            """,
                            (bracket.tournament_id, champion_pair_id),
                        ).fetchone()
                        if team:
                            champion_players = [
                                int(user_id)
                                for user_id in json.loads(team["members_json"])
                            ]
                        else:
                            legacy_team = self.connection.execute(
                                """
                                SELECT player_a_id, player_b_id
                                FROM tournament_pairs
                                WHERE tournament_id = ? AND pair_id = ?
                                """,
                                (bracket.tournament_id, champion_pair_id),
                            ).fetchone()
                            champion_players = (
                                [
                                    int(legacy_team["player_a_id"]),
                                    int(legacy_team["player_b_id"]),
                                ]
                                if legacy_team
                                else []
                            )
                        for user_id in set(champion_players):
                            self.connection.execute(
                                """
                                INSERT OR IGNORE INTO tournament_titles
                                (guild_id, tournament_id, team_id, user_id, awarded_at)
                                VALUES (?, ?, ?, ?, ?)
                                """,
                                (
                                    int(tournament_row["guild_id"]),
                                    bracket.tournament_id,
                                    champion_pair_id,
                                    user_id,
                                    ended_at,
                                ),
                            )
                        distinct_champion_players = list(dict.fromkeys(champion_players))
                        total_amount_cents = (
                            int(tournament_row["prize_amount_cents"])
                            if tournament_row["prize_amount_cents"] is not None
                            else None
                        )
                        self.connection.execute(
                            """
                            INSERT OR IGNORE INTO tournament_payment_batches
                            (tournament_id, guild_id, champion_team_id,
                             total_amount_cents, status, created_at)
                            VALUES (?, ?, ?, ?, 'provisioning', ?)
                            """,
                            (
                                bracket.tournament_id,
                                int(tournament_row["guild_id"]),
                                champion_pair_id,
                                total_amount_cents,
                                ended_at,
                            ),
                        )
                        if distinct_champion_players:
                            base_amount = (
                                total_amount_cents // len(distinct_champion_players)
                                if total_amount_cents is not None
                                else None
                            )
                            remainder = (
                                total_amount_cents % len(distinct_champion_players)
                                if total_amount_cents is not None
                                else 0
                            )
                            for index, user_id in enumerate(distinct_champion_players):
                                player_amount = (
                                    base_amount + (1 if index < remainder else 0)
                                    if base_amount is not None
                                    else None
                                )
                                self.connection.execute(
                                    """
                                    INSERT OR IGNORE INTO tournament_payouts
                                    (tournament_id, guild_id, user_id, amount_cents,
                                     status)
                                    VALUES (?, ?, ?, ?, 'pending')
                                    """,
                                    (
                                        bracket.tournament_id,
                                        int(tournament_row["guild_id"]),
                                        user_id,
                                        player_amount,
                                    ),
                                )
                else:
                    winners = [int(row["winner_pair_id"]) for row in current_round_rows]
                    for index in range(0, len(winners), 2):
                        cursor = self.connection.execute(
                            """
                            INSERT INTO tournament_bracket_matches
                            (tournament_id, round_number, match_number, pair_a_id, pair_b_id,
                             status, created_at)
                            VALUES (?, ?, ?, ?, ?, 'pending', ?)
                            """,
                            (
                                bracket.tournament_id,
                                bracket.round_number + 1,
                                index // 2 + 1,
                                winners[index],
                                winners[index + 1],
                                ended_at,
                            ),
                        )
                        next_ids.append(int(cursor.lastrowid))
        return TournamentResult(
            tournament_id=bracket.tournament_id,
            next_bracket_match_ids=next_ids,
            champion_pair_id=champion_pair_id,
        )

    def _increment_player_stats(self, user_id: int, *, wins: int, losses: int) -> None:
        self.connection.execute(
            """
            INSERT INTO player_stats (user_id, wins, losses)
            VALUES (?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET
                wins = wins + excluded.wins,
                losses = losses + excluded.losses
            """,
            (user_id, wins, losses),
        )

    def cancel_match(self, match_id: int) -> None:
        with self.lock, self.connection:
            self.connection.execute(
                """
                UPDATE matches SET status = 'cancelled', ended_at = ?
                WHERE match_id = ?
                """,
                (utc_now(), match_id),
            )