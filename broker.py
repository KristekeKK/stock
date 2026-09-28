"""OANDA v20 broker adapter.

Wraps the pieces of the OANDA REST API the bot needs:
  - fetch account summary (balance / equity)
  - fetch recent candles
  - fetch current price
  - place a market order with a stop-loss

If LIVE is false, place_market_order() is a no-op that only logs intent, so the
exact same code path runs in dry-run and live modes.
"""
from __future__ import annotations

from typing import Any

import oandapyV20
from oandapyV20.endpoints import accounts, instruments, orders, pricing, trades

from config import Config


class Broker:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg
        self.client = oandapyV20.API(access_token=cfg.api_token, environment=cfg.env)

    # --- account ---
    def account_summary(self) -> dict[str, Any]:
        r = accounts.AccountSummary(accountID=self.cfg.account_id)
        self.client.request(r)
        return r.response["account"]

    def equity(self) -> float:
        acct = self.account_summary()
        # NAV = balance + unrealized P&L = true equity ("life").
        return float(acct.get("NAV", acct["balance"]))

    # --- market data ---
    def candles(self, count: int = 200) -> list[dict[str, Any]]:
        params = {
            "count": count,
            "granularity": self.cfg.granularity,
            "price": "M",  # midpoint
        }
        r = instruments.InstrumentsCandles(
            instrument=self.cfg.instrument, params=params
        )
        self.client.request(r)
        return [c for c in r.response["candles"] if c["complete"]]

    def current_price(self) -> float:
        params = {"instruments": self.cfg.instrument}
        r = pricing.PricingInfo(accountID=self.cfg.account_id, params=params)
        self.client.request(r)
        p = r.response["prices"][0]
        bid = float(p["bids"][0]["price"])
        ask = float(p["asks"][0]["price"])
        return (bid + ask) / 2.0

    # --- trades ---
    def open_trades(self) -> list[dict[str, Any]]:
        """Open trades for the configured instrument."""
        r = trades.OpenTrades(accountID=self.cfg.account_id)
        self.client.request(r)
        return [t for t in r.response.get("trades", []) if t["instrument"] == self.cfg.instrument]

    def recent_closed_trades(self, count: int = 50) -> list[dict[str, Any]]:
        params = {"state": "CLOSED", "instrument": self.cfg.instrument, "count": count}
        r = trades.TradesList(accountID=self.cfg.account_id, params=params)
        self.client.request(r)
        return r.response.get("trades", [])

    def close_trade(self, trade_id: str) -> dict[str, Any] | None:
        if not self.cfg.live:
            print(f"[DRY-RUN] Would close trade {trade_id}")
            return None
        r = trades.TradeClose(accountID=self.cfg.account_id, tradeID=trade_id)
        self.client.request(r)
        return r.response

    # --- orders ---
    def place_market_order(
        self, units: float, stop_loss_price: float
    ) -> dict[str, Any] | None:
        """Place a market order with an attached stop-loss.

        units > 0 => BUY (long), units < 0 => SELL (short).
        Returns the OANDA response, or None in dry-run mode.
        """
        side = "BUY" if units > 0 else "SELL"
        if not self.cfg.live:
            print(
                f"[DRY-RUN] Would place {side} {abs(units)} units of "
                f"{self.cfg.instrument} with stop-loss @ {stop_loss_price:.5f}"
            )
            return None

        data = {
            "order": {
                "type": "MARKET",
                "instrument": self.cfg.instrument,
                "units": str(int(units)),
                "timeInForce": "FOK",
                "positionFill": "DEFAULT",
                "stopLossOnFill": {"price": f"{stop_loss_price:.5f}"},
            }
        }
        r = orders.OrderCreate(accountID=self.cfg.account_id, data=data)
        self.client.request(r)
        return r.response
