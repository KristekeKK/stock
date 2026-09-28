"""Test version of the bot: paper-trade a simulated market. No OANDA account needed.

Runs the REAL bot logic (bot.run_cycle -> strategy + risk engine + state) against
a PaperBroker, one bar per cycle, exactly like the hourly scheduler would.

Examples:
  python simulate.py                         # ~6 months of synthetic EUR/USD H1
  python simulate.py --seed 7 --capital 100  # different market, bigger account
  python simulate.py --csv my_eurusd_h1.csv  # replay your own data
  python simulate.py --verbose               # print every cycle's reasoning
  python simulate.py --delay 0.2             # watch it trade in slow motion
"""
from __future__ import annotations

import argparse
import sys
import time
from dataclasses import replace

import numpy as np
import pandas as pd

from bot import run_cycle
from config import Config
from paper_broker import PaperBroker
from state import State

WARMUP_BARS = 250  # history the bot needs before its first decision


def synthetic_bars(n: int, seed: int, start_price: float = 1.10) -> pd.DataFrame:
    """Random-walk EUR/USD hourly bars with trending/choppy regimes and
    changing volatility, weekends excluded."""
    rng = np.random.default_rng(seed)
    times = pd.date_range("2025-01-06", periods=n * 2, freq="h", tz="UTC")
    times = times[times.dayofweek < 5][:n]

    drift = vol = 0.0
    regime_left = 0
    price = start_price
    rows = []
    for t in times:
        if regime_left <= 0:
            regime_left = int(rng.integers(100, 500))
            drift = rng.normal(0, 0.00008)
            vol = 0.0008 * rng.uniform(0.5, 1.6)
        regime_left -= 1
        o = price
        c = o * float(np.exp(drift + rng.normal(0, vol)))
        wick = abs(rng.normal(0, vol * 0.6)) * o
        rows.append((t, o, max(o, c) + wick, min(o, c) - abs(rng.normal(0, vol * 0.6)) * o, c))
        price = c
    return pd.DataFrame(rows, columns=["time", "open", "high", "low", "close"])


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    for alt in ("datetime", "date", "timestamp"):
        if "time" not in df.columns and alt in df.columns:
            df = df.rename(columns={alt: "time"})
    missing = {"time", "open", "high", "low", "close"} - set(df.columns)
    if missing:
        raise SystemExit(f"CSV is missing columns: {sorted(missing)}")
    df["time"] = pd.to_datetime(df["time"], utc=True)
    return df[["time", "open", "high", "low", "close"]].astype(
        {"open": float, "high": float, "low": float, "close": float}
    ).sort_values("time").reset_index(drop=True)


def ascii_chart(values: list[float], floor: float, start: float,
                width: int = 70, height: int = 12) -> str:
    if not values:
        return ""
    idx = np.linspace(0, len(values) - 1, min(width, len(values))).astype(int)
    pts = [values[i] for i in idx]
    lo = min(min(pts), floor)
    hi = max(max(pts), start)
    span = (hi - lo) or 1.0

    def row_of(v: float) -> int:
        return int(round((v - lo) / span * (height - 1)))

    grid = [[" "] * len(pts) for _ in range(height)]
    for x in range(len(pts)):
        grid[row_of(floor)][x] = "-"
        grid[row_of(start)][x] = "."
    for x, v in enumerate(pts):
        grid[row_of(v)][x] = "*"
    lines = []
    for r in range(height - 1, -1, -1):
        label = lo + span * r / (height - 1)
        lines.append(f"{label:10.4f} |" + "".join(grid[r]))
    lines.append(" " * 11 + "+" + "-" * len(pts))
    lines.append(" " * 12 + "* equity   . start capital   - death floor")
    return "\n".join(lines)


def main() -> int:
    ap = argparse.ArgumentParser(description="Paper-trade the survival bot on a simulated market.")
    ap.add_argument("--bars", type=int, default=3000, help="synthetic hourly bars (default 3000 ~ 6 months)")
    ap.add_argument("--seed", type=int, default=42, help="random seed for the synthetic market")
    ap.add_argument("--csv", help="replay OHLC data from a CSV (columns: time,open,high,low,close)")
    ap.add_argument("--capital", type=float, help="starting capital (default: START_CAPITAL from .env or 10)")
    ap.add_argument("--spread-pips", type=float, default=1.5, help="bid/ask spread in pips (default 1.5)")
    ap.add_argument("--leverage", type=float, default=30.0, help="max leverage (default 30, EU retail)")
    ap.add_argument("--delay", type=float, default=0.0, help="seconds to pause per bar (watch it live)")
    ap.add_argument("--verbose", action="store_true", help="print every cycle's reasoning")
    args = ap.parse_args()

    cfg = Config.load(require_credentials=False)
    cfg = replace(cfg, env="simulation", live=True,
                  start_capital=args.capital if args.capital is not None else cfg.start_capital)

    bars = load_csv(args.csv) if args.csv else synthetic_bars(args.bars + WARMUP_BARS, args.seed)
    if len(bars) <= WARMUP_BARS + 1:
        raise SystemExit(f"Need more than {WARMUP_BARS + 1} bars, got {len(bars)}.")

    broker = PaperBroker(cfg, bars, start_index=WARMUP_BARS - 1,
                         spread=args.spread_pips * 0.0001, leverage=args.leverage,
                         on_event=print)
    state = State(":memory:", clock=lambda: broker.now)
    log = print if args.verbose else (lambda _msg: None)

    source = args.csv or f"synthetic (seed {args.seed})"
    print(f"SURVIVAL BOT - TEST RUN ({source}, {cfg.instrument} {cfg.granularity})")
    print(f"Start capital {cfg.start_capital:.2f} | death floor {cfg.death_floor_equity:.2f} | "
          f"risk/trade {cfg.risk_per_trade_pct}% | max losses/day {cfg.daily_max_losses}")
    print(f"Period: {broker.now:%Y-%m-%d} -> {pd.Timestamp(bars['time'].iloc[-1]):%Y-%m-%d}\n")

    curve: list[float] = []
    status = "ALIVE"
    while True:
        if run_cycle(cfg=cfg, broker=broker, state=state, log=log) != 0:
            print("Bot crashed during a cycle - aborting test run.", file=sys.stderr)
            return 1
        equity = broker.equity()
        curve.append(equity)
        if equity <= 0:
            status = "DEAD (equity reached 0)"
            break
        if equity <= cfg.death_floor_equity and not broker.open_trades():
            status = "HIBERNATING (hit death floor, trading stopped to survive)"
            break
        if not broker.advance():
            break
        if args.delay:
            time.sleep(args.delay)

    closed = broker.closed_trades
    pls = [float(t["realizedPL"]) for t in closed]
    wins = sum(p >= 0 for p in pls)
    peak, max_dd = curve[0], 0.0
    for v in curve:
        peak = max(peak, v)
        max_dd = max(max_dd, (peak - v) / peak if peak else 0.0)
    final = curve[-1]

    print("\n" + ascii_chart(curve, cfg.death_floor_equity, cfg.start_capital))
    print("\n=== Test run result ===")
    print(f"Bars simulated : {len(curve)}  (up to {broker.now:%Y-%m-%d %H:%M})")
    print(f"Trades closed  : {len(pls)}  (wins {wins} / losses {len(pls) - wins}"
          + (f", win rate {wins / len(pls):.0%})" if pls else ")"))
    print(f"Open trades    : {len(broker.open_trades())}")
    print(f"Final equity   : {final:.4f}  (health {final / cfg.start_capital:.2%})")
    print(f"Lowest equity  : {min(curve):.4f}   Max drawdown: {max_dd:.2%}")
    print(f"Result         : {'SURPLUS' if final > cfg.start_capital else 'DEFICIT'}")
    print(f"Status         : {status}")
    state.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
