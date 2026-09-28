"""Persistent state: the bot's 'life'.

Uses SQLite so balance history, open positions and every trade survive restarts.
Health = current_equity / start_capital.  When equity hits the death floor the
bot hibernates instead of dying.
"""
from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

DB_PATH = Path(__file__).with_name("bot_state.db")


class State:
    def __init__(
        self,
        db_path: Path | str = DB_PATH,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self.conn = sqlite3.connect(str(db_path))
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def now(self) -> datetime:
        return self._clock()

    def _init_schema(self) -> None:
        self.conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS equity_log (
                id        INTEGER PRIMARY KEY AUTOINCREMENT,
                ts        TEXT NOT NULL,
                equity    REAL NOT NULL,
                health    REAL NOT NULL
            );

            CREATE TABLE IF NOT EXISTS trades (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                ts         TEXT NOT NULL,
                instrument TEXT NOT NULL,
                side       TEXT NOT NULL,        -- BUY / SELL
                units      REAL NOT NULL,
                price      REAL NOT NULL,
                stop_loss  REAL,
                reason     TEXT,
                order_id   TEXT
            );

            CREATE TABLE IF NOT EXISTS daily_stats (
                day     TEXT PRIMARY KEY,        -- YYYY-MM-DD (UTC)
                losses  INTEGER NOT NULL DEFAULT 0,
                wins    INTEGER NOT NULL DEFAULT 0
            );

            -- Broker trade IDs already counted as a win/loss (prevents double counting).
            CREATE TABLE IF NOT EXISTS seen_closed_trades (
                trade_id TEXT PRIMARY KEY
            );

            CREATE TABLE IF NOT EXISTS meta (
                key   TEXT PRIMARY KEY,
                value TEXT
            );
            """
        )
        self.conn.commit()

    # --- equity / health ---
    def record_equity(self, equity: float, start_capital: float) -> float:
        health = equity / start_capital if start_capital else 0.0
        self.conn.execute(
            "INSERT INTO equity_log (ts, equity, health) VALUES (?, ?, ?)",
            (self.now().isoformat(), equity, health),
        )
        self.conn.commit()
        return health

    # --- trades ---
    def record_trade(
        self,
        instrument: str,
        side: str,
        units: float,
        price: float,
        stop_loss: float | None,
        reason: str,
        order_id: str | None,
    ) -> None:
        self.conn.execute(
            """INSERT INTO trades
               (ts, instrument, side, units, price, stop_loss, reason, order_id)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                self.now().isoformat(),
                instrument,
                side,
                units,
                price,
                stop_loss,
                reason,
                order_id,
            ),
        )
        self.conn.commit()

    # --- daily loss tracking ---
    def _today(self) -> str:
        return self.now().date().isoformat()

    def losses_today(self) -> int:
        row = self.conn.execute(
            "SELECT losses FROM daily_stats WHERE day = ?", (self._today(),)
        ).fetchone()
        return int(row["losses"]) if row else 0

    def record_result(self, won: bool) -> None:
        day = self._today()
        self.conn.execute(
            "INSERT OR IGNORE INTO daily_stats (day, losses, wins) VALUES (?, 0, 0)",
            (day,),
        )
        col = "wins" if won else "losses"
        self.conn.execute(
            f"UPDATE daily_stats SET {col} = {col} + 1 WHERE day = ?", (day,)
        )
        self.conn.commit()

    def sync_closed_trade(self, trade_id: str, realized_pl: float, close_day: str) -> bool:
        """Count a broker-closed trade once. Only trades closed today (UTC) affect
        today's loss limit. Returns True if newly counted as a result today."""
        cur = self.conn.execute(
            "INSERT OR IGNORE INTO seen_closed_trades (trade_id) VALUES (?)",
            (trade_id,),
        )
        self.conn.commit()
        if cur.rowcount == 0 or close_day != self._today():
            return False
        self.record_result(won=realized_pl >= 0)
        return True

    # --- key/value meta ---
    def get_meta(self, key: str) -> str | None:
        row = self.conn.execute("SELECT value FROM meta WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_meta(self, key: str, value: str) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)", (key, value)
        )
        self.conn.commit()

    def close(self) -> None:
        self.conn.close()
