"""The survival instinct.

This is the heart of the idea: a risk engine whose overriding goal is to NOT
reach $0.  It decides whether the bot is allowed to trade at all, and if so how
large a position it may open.

Rules:
  1. Hard death floor  -> if equity <= floor, refuse all new trades (hibernate).
  2. Daily loss limit  -> after N losses today, stop for the day.
  3. Risk per trade    -> size the position so a stop-out costs <= risk_pct of equity.
  4. Every trade has a stop-loss (enforced here + at the broker).
"""
from __future__ import annotations

from dataclasses import dataclass

from config import Config
from state import State


@dataclass
class TradeDecision:
    allowed: bool
    reason: str
    units: float = 0.0          # signed: + long, - short
    stop_loss_price: float = 0.0


class RiskEngine:
    def __init__(self, cfg: Config, state: State) -> None:
        self.cfg = cfg
        self.state = state

    def is_alive(self, equity: float) -> bool:
        return equity > self.cfg.death_floor_equity

    def can_trade(self, equity: float) -> tuple[bool, str]:
        if not self.is_alive(equity):
            return False, (
                f"HIBERNATING: equity {equity:.2f} <= death floor "
                f"{self.cfg.death_floor_equity:.2f}. No new trades."
            )
        losses = self.state.losses_today()
        if losses >= self.cfg.daily_max_losses:
            return False, (
                f"Daily loss limit hit ({losses}/{self.cfg.daily_max_losses}). "
                "Standing down until tomorrow."
            )
        return True, "OK"

    def size_position(
        self,
        equity: float,
        direction: int,        # +1 long, -1 short
        entry_price: float,
        stop_distance: float,  # price distance to the stop (positive)
    ) -> TradeDecision:
        """Compute a signed unit size that risks <= risk_per_trade_pct of equity.

        For FX priced in the quote currency, risk per unit ~= stop_distance.
        units = risk_amount / stop_distance.
        """
        can, reason = self.can_trade(equity)
        if not can:
            return TradeDecision(allowed=False, reason=reason)

        if stop_distance <= 0:
            return TradeDecision(allowed=False, reason="Invalid stop distance (<=0).")

        risk_amount = equity * (self.cfg.risk_per_trade_pct / 100.0)
        raw_units = risk_amount / stop_distance
        units = int(raw_units) * direction

        if units == 0:
            # Account too small for even 1 unit at this risk — protect capital.
            return TradeDecision(
                allowed=False,
                reason=(
                    f"Position size rounds to 0 units (risk {risk_amount:.4f} / "
                    f"stop {stop_distance:.5f}). Skipping to preserve capital."
                ),
            )

        stop_loss_price = (
            entry_price - stop_distance
            if direction > 0
            else entry_price + stop_distance
        )
        return TradeDecision(
            allowed=True,
            reason=f"Sized {units} units risking {risk_amount:.4f}.",
            units=units,
            stop_loss_price=stop_loss_price,
        )
