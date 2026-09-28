"""Main bot loop — runs ONE safe trading cycle, then exits.

Designed to be called repeatedly by Windows Task Scheduler (e.g. hourly).
A single cycle:
  1. Read equity ("life") from the broker and log health.
  2. Ask the risk engine if trading is allowed (death floor, daily loss limit).
  3. If alive: evaluate the strategy on recent candles.
  4. If there is a signal: size the position via the risk engine.
  5. Place the order (or log intent in dry-run) with a mandatory stop-loss.

The bot never averages down or removes a stop. Survival first.

run_cycle() accepts an injected config/broker/state so the exact same logic can
drive the paper-trading simulator (simulate.py).
"""
from __future__ import annotations

import sys
import traceback
from typing import Any, Callable

from config import Config
from risk_engine import RiskEngine
from state import State
from strategy import Strategy, candles_to_df


def run_cycle(
    cfg: Config | None = None,
    broker: Any = None,
    state: State | None = None,
    log: Callable[[str], None] = print,
) -> int:
    cfg = cfg or Config.load()
    owns_state = state is None
    state = state or State()
    try:
        if broker is None:
            from broker import Broker

            broker = Broker(cfg)
        risk = RiskEngine(cfg, state)
        strategy = Strategy(cfg)

        stamp = state.now().isoformat(timespec="seconds")
        mode = "LIVE" if cfg.live else "DRY-RUN"
        log(f"=== Cycle {stamp} [{cfg.env}/{mode}] {cfg.instrument} ===")

        # 1. Life check (count newly closed trades toward today's loss limit first)
        for t in broker.recent_closed_trades():
            close_day = str(t.get("closeTime", ""))[:10]
            if state.sync_closed_trade(t["id"], float(t.get("realizedPL", 0.0)), close_day):
                log(f"Closed trade {t['id']} realizedPL={t.get('realizedPL')}")

        equity = broker.equity()
        health = state.record_equity(equity, cfg.start_capital)
        log(
            f"Equity: {equity:.4f} | Health: {health:.2%} | "
            f"Death floor: {cfg.death_floor_equity:.2f}"
        )

        # 2. Permission to trade
        can, reason = risk.can_trade(equity)
        if not can:
            log(f"Not trading: {reason}")
            return 0

        # 3. Strategy
        df = candles_to_df(broker.candles(count=250))
        signal = strategy.evaluate(df)
        log(f"Signal: dir={signal.direction:+d} | {signal.note}")
        if signal.direction == 0:
            return 0

        # Never act twice on the same candle (e.g. scheduler ran twice in an hour).
        if state.get_meta("last_signal_bar") == signal.bar_time:
            log(f"Signal for bar {signal.bar_time} already handled. Skipping.")
            return 0
        state.set_meta("last_signal_bar", signal.bar_time)

        # One position at a time: skip if already aligned, exit if opposite.
        open_trades = broker.open_trades()
        if any(float(t["currentUnits"]) * signal.direction > 0 for t in open_trades):
            log("Already positioned in signal direction. Holding.")
            return 0
        for t in open_trades:
            log(f"Opposite signal: closing trade {t['id']} ({t['currentUnits']} units)")
            broker.close_trade(t["id"])
        if open_trades:
            equity = broker.equity()

        # 4. Size the position
        decision = risk.size_position(
            equity=equity,
            direction=signal.direction,
            entry_price=signal.entry_price,
            stop_distance=signal.stop_distance,
        )
        log(f"Risk decision: {decision.reason}")
        if not decision.allowed:
            return 0

        # 5. Execute (dry-run logs only)
        resp = broker.place_market_order(
            units=decision.units, stop_loss_price=decision.stop_loss_price
        )
        order_id = None
        if resp is not None:
            fill = resp.get("orderFillTransaction")
            if not fill:
                failed = resp.get("orderCancelTransaction") or resp.get("orderRejectTransaction") or {}
                log(f"Order NOT filled: {failed.get('reason', 'unknown reason')}")
                return 0
            order_id = fill.get("id")
        side = "BUY" if decision.units > 0 else "SELL"
        state.record_trade(
            instrument=cfg.instrument,
            side=side,
            units=decision.units,
            price=signal.entry_price,
            stop_loss=decision.stop_loss_price,
            reason=signal.note,
            order_id=order_id,
        )
        log(
            f"Recorded {side} {abs(decision.units)} units @ ~{signal.entry_price:.5f} "
            f"stop {decision.stop_loss_price:.5f} (order_id={order_id})"
        )
        return 0
    except Exception:  # noqa: BLE001 - top-level guard so a crash never kills funds
        print("ERROR during cycle:", file=sys.stderr)
        traceback.print_exc()
        return 1
    finally:
        if owns_state:
            state.close()


if __name__ == "__main__":
    raise SystemExit(run_cycle())
