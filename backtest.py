"""Backtest the strategy + survival engine on historical candles.

Pulls a chunk of history from OANDA (works on a demo account) and replays it
bar by bar, applying the same strategy signals, ATR stops and 1%-risk sizing.
Reports final equity, health, and whether the bot would have 'died'.

This is a lightweight sanity check, NOT a full market simulator: it assumes one
position at a time, fills at the close, and exits on stop or opposite signal.
Use it to catch obviously broken strategies before risking real money.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from config import Config
from strategy import Strategy, candles_to_df


@dataclass
class Position:
    direction: int
    entry: float
    stop: float
    units: float


def run_backtest(count: int = 2500) -> None:
    cfg = Config.load()

    # Import here so backtest can run even if oandapyV20 layout differs.
    import oandapyV20
    from oandapyV20.endpoints import instruments

    client = oandapyV20.API(access_token=cfg.api_token, environment=cfg.env)
    params = {"count": count, "granularity": cfg.granularity, "price": "M"}
    req = instruments.InstrumentsCandles(instrument=cfg.instrument, params=params)
    client.request(req)
    candles = [c for c in req.response["candles"] if c["complete"]]
    df = candles_to_df(candles)

    strategy = Strategy(cfg)
    equity = cfg.start_capital
    floor = cfg.death_floor_equity
    pos: Position | None = None
    trades = wins = losses = 0
    died = False

    warmup = max(cfg.slow_ma, cfg.trend_ma, cfg.atr_period) + 2

    for i in range(warmup, len(df)):
        window = df.iloc[: i + 1]
        price = float(window.iloc[-1]["close"])

        # --- manage open position ---
        if pos is not None:
            hit_stop = (
                (pos.direction > 0 and price <= pos.stop)
                or (pos.direction < 0 and price >= pos.stop)
            )
            sig_now = strategy.evaluate(window)
            opposite = sig_now.direction == -pos.direction and sig_now.direction != 0
            if hit_stop or opposite:
                exit_price = pos.stop if hit_stop else price
                pnl = (exit_price - pos.entry) * pos.units  # units signed
                equity += pnl
                trades += 1
                if pnl >= 0:
                    wins += 1
                else:
                    losses += 1
                pos = None
                if equity <= floor:
                    died = True
                    break

        # --- consider new entry ---
        if pos is None and equity > floor:
            sig = strategy.evaluate(window)
            if sig.direction != 0 and sig.stop_distance > 0:
                risk_amount = equity * (cfg.risk_per_trade_pct / 100.0)
                units = int(risk_amount / sig.stop_distance) * sig.direction
                if units != 0:
                    stop = (
                        sig.entry_price - sig.stop_distance
                        if sig.direction > 0
                        else sig.entry_price + sig.stop_distance
                    )
                    pos = Position(sig.direction, sig.entry_price, stop, units)

    health = equity / cfg.start_capital if cfg.start_capital else 0.0
    print("=== Backtest result ===")
    print(f"Instrument     : {cfg.instrument} ({cfg.granularity})")
    print(f"Bars tested    : {len(df) - warmup}")
    print(f"Trades         : {trades} (wins {wins} / losses {losses})")
    print(f"Start capital  : {cfg.start_capital:.2f}")
    print(f"Final equity   : {equity:.4f}")
    print(f"Health         : {health:.2%}")
    print(f"Death floor    : {floor:.2f}")
    print(f"DIED?          : {'YES' if died else 'no'}")


if __name__ == "__main__":
    run_backtest()
