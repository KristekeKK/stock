"""Trading strategy: moving-average crossover with a trend filter + ATR stops.

Signals (evaluated on completed candles):
  - Long  when fast MA crosses ABOVE slow MA, and price is above the trend MA.
  - Short when fast MA crosses BELOW slow MA, and price is below the trend MA.
  - Otherwise: no trade.

The ATR (Average True Range) sets the stop distance, which the risk engine turns
into a position size.  Slow timeframe + trend filter = few, higher-quality trades.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from config import Config


@dataclass
class Signal:
    direction: int          # +1 long, -1 short, 0 none
    entry_price: float
    stop_distance: float    # ATR * mult
    note: str
    bar_time: str = ""      # time of the candle that produced the signal


def candles_to_df(candles: list[dict]) -> pd.DataFrame:
    rows = []
    for c in candles:
        mid = c["mid"]
        rows.append(
            {
                "time": c["time"],
                "open": float(mid["o"]),
                "high": float(mid["h"]),
                "low": float(mid["l"]),
                "close": float(mid["c"]),
            }
        )
    return pd.DataFrame(rows)


def _atr(df: pd.DataFrame, period: int) -> pd.Series:
    high, low, close = df["high"], df["low"], df["close"]
    prev_close = close.shift(1)
    tr = pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    return tr.rolling(period).mean()


class Strategy:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

    def evaluate(self, df: pd.DataFrame) -> Signal:
        cfg = self.cfg
        need = max(cfg.slow_ma, cfg.trend_ma, cfg.atr_period) + 2
        if len(df) < need:
            return Signal(0, 0.0, 0.0, f"Not enough data ({len(df)}/{need}).")

        df = df.copy()
        df["fast"] = df["close"].rolling(cfg.fast_ma).mean()
        df["slow"] = df["close"].rolling(cfg.slow_ma).mean()
        df["trend"] = df["close"].rolling(cfg.trend_ma).mean()
        df["atr"] = _atr(df, cfg.atr_period)

        last = df.iloc[-1]
        prev = df.iloc[-2]
        price = float(last["close"])
        atr = float(last["atr"])
        stop_distance = atr * cfg.atr_stop_mult

        crossed_up = prev["fast"] <= prev["slow"] and last["fast"] > last["slow"]
        crossed_down = prev["fast"] >= prev["slow"] and last["fast"] < last["slow"]
        bar_time = str(last["time"])

        if crossed_up and price > last["trend"]:
            return Signal(+1, price, stop_distance, "Bullish MA cross above trend.", bar_time)
        if crossed_down and price < last["trend"]:
            return Signal(-1, price, stop_distance, "Bearish MA cross below trend.", bar_time)
        return Signal(0, price, stop_distance, "No crossover / trend filter blocks.", bar_time)
