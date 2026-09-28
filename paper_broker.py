"""Paper broker: a simulated OANDA account for test runs (no account, no network).

Implements the same interface bot.py uses on the real Broker, so the bot's real
logic (strategy, risk engine, state) runs unchanged against simulated fills.

Simulation rules:
  - Prices come from a DataFrame of OHLC bars (synthetic or loaded from CSV).
  - Market orders fill at the current bar's close +/- half the spread.
  - Stop-losses trigger on the next bars' high/low; a price gap past the stop
    fills at the (worse) open, like a real market.
  - Orders are cancelled for insufficient margin (leverage limit), like OANDA.
  - Account currency is assumed to equal the quote currency (USD for EUR/USD).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable

import pandas as pd

from config import Config


class PaperBroker:
    def __init__(
        self,
        cfg: Config,
        bars: pd.DataFrame,
        start_index: int,
        spread: float = 0.00015,
        leverage: float = 30.0,
        on_event: Callable[[str], None] | None = None,
    ) -> None:
        self.cfg = cfg
        self.bars = bars.reset_index(drop=True)
        if not 0 <= start_index < len(self.bars):
            raise ValueError("start_index outside of available bars")
        self.i = start_index
        self.half_spread = spread / 2.0
        self.leverage = leverage
        self.on_event = on_event or (lambda _msg: None)
        self.balance = float(cfg.start_capital)
        self._open: list[dict[str, Any]] = []
        self._closed: list[dict[str, Any]] = []
        self._next_id = 1

    # --- simulated clock ---
    @property
    def now(self) -> datetime:
        return pd.Timestamp(self.bars.at[self.i, "time"]).to_pydatetime()

    def advance(self) -> bool:
        """Move to the next bar and trigger any stop-losses hit in it."""
        if self.i + 1 >= len(self.bars):
            return False
        self.i += 1
        self._check_stops()
        return True

    # --- prices ---
    def _close_price(self) -> float:
        return float(self.bars.at[self.i, "close"])

    def _bid(self) -> float:
        return self._close_price() - self.half_spread

    def _ask(self) -> float:
        return self._close_price() + self.half_spread

    def current_price(self) -> float:
        return self._close_price()

    # --- account ---
    def _unrealized(self) -> float:
        total = 0.0
        for t in self._open:
            mark = self._bid() if t["units"] > 0 else self._ask()
            total += (mark - t["entry"]) * t["units"]
        return total

    def equity(self) -> float:
        return self.balance + self._unrealized()

    def account_summary(self) -> dict[str, Any]:
        return {"balance": f"{self.balance:.6f}", "NAV": f"{self.equity():.6f}"}

    def _margin_used(self) -> float:
        return sum(abs(t["units"]) * t["entry"] / self.leverage for t in self._open)

    # --- market data ---
    def candles(self, count: int = 200) -> list[dict[str, Any]]:
        window = self.bars.iloc[max(0, self.i + 1 - count) : self.i + 1]
        return [
            {
                "time": pd.Timestamp(row.time).isoformat(),
                "complete": True,
                "mid": {"o": row.open, "h": row.high, "l": row.low, "c": row.close},
            }
            for row in window.itertuples(index=False)
        ]

    # --- trades ---
    def open_trades(self) -> list[dict[str, Any]]:
        return [
            {"id": t["id"], "instrument": self.cfg.instrument, "currentUnits": str(t["units"])}
            for t in self._open
        ]

    def recent_closed_trades(self, count: int = 50) -> list[dict[str, Any]]:
        return self._closed[-count:]

    @property
    def closed_trades(self) -> list[dict[str, Any]]:
        return list(self._closed)

    def _stamp(self) -> str:
        return self.now.strftime("%Y-%m-%d %H:%M")

    def _close(self, trade: dict[str, Any], exit_price: float, why: str) -> None:
        pl = (exit_price - trade["entry"]) * trade["units"]
        self.balance += pl
        self._open.remove(trade)
        self._closed.append(
            {
                "id": trade["id"],
                "instrument": self.cfg.instrument,
                "realizedPL": f"{pl:.6f}",
                "closeTime": self.now.isoformat(),
                "reason": why,
            }
        )
        side = "LONG" if trade["units"] > 0 else "SHORT"
        self.on_event(
            f"{self._stamp()}  CLOSE {side:<5} #{trade['id']} @ {exit_price:.5f}  "
            f"{why:<11} P/L {pl:+.4f}  -> equity {self.equity():.4f}"
        )

    def close_trade(self, trade_id: str) -> dict[str, Any] | None:
        for t in list(self._open):
            if t["id"] == trade_id:
                self._close(t, self._bid() if t["units"] > 0 else self._ask(), "SIGNAL_EXIT")
                return {"orderFillTransaction": {"id": trade_id}}
        return None

    def _check_stops(self) -> None:
        bar = self.bars.iloc[self.i]
        for t in list(self._open):
            if t["units"] > 0 and bar.low - self.half_spread <= t["stop"]:
                self._close(t, min(t["stop"], bar.open - self.half_spread), "STOP_LOSS")
            elif t["units"] < 0 and bar.high + self.half_spread >= t["stop"]:
                self._close(t, max(t["stop"], bar.open + self.half_spread), "STOP_LOSS")

    # --- orders ---
    def place_market_order(self, units: float, stop_loss_price: float) -> dict[str, Any]:
        units = int(units)
        entry = self._ask() if units > 0 else self._bid()

        def cancel(reason: str) -> dict[str, Any]:
            self.on_event(f"{self._stamp()}  ORDER CANCELLED ({reason}) {units:+d} units")
            return {"orderCancelTransaction": {"reason": reason}}

        if units == 0:
            return cancel("UNITS_ZERO")
        if (units > 0 and stop_loss_price >= entry) or (units < 0 and stop_loss_price <= entry):
            return cancel("STOP_LOSS_ON_FILL_LOSS")
        required = abs(units) * entry / self.leverage
        if self._margin_used() + required > self.equity():
            return cancel("INSUFFICIENT_MARGIN")

        trade_id = str(self._next_id)
        self._next_id += 1
        self._open.append({"id": trade_id, "units": units, "entry": entry, "stop": stop_loss_price})
        side = "LONG" if units > 0 else "SHORT"
        self.on_event(
            f"{self._stamp()}  OPEN  {side:<5} #{trade_id} {abs(units)} units @ {entry:.5f}  "
            f"stop {stop_loss_price:.5f}  (equity {self.equity():.4f})"
        )
        return {"orderFillTransaction": {"id": trade_id}}
