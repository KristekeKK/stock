# Copilot instructions — Survival Trading Bot

A forex trading bot for OANDA + EUR/USD (Python, Windows). Its overriding goal is
**survival**: keep account equity above a "death floor" rather than maximize profit.
The risk engine dominates every trading decision.

## Setup, run, and test

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
Copy-Item .env.example .env   # then edit OANDA credentials
```

- **One live/dry-run cycle:** `py -3 bot.py` (dry-run unless `LIVE=true` in `.env`).
- **Backtest on real OANDA history:** `py -3 backtest.py` (needs valid credentials).
- **Simulate (no account/network needed):** `.\simulate.bat` or `py -3 simulate.py`.
  Useful flags: `--seed N`, `--capital N`, `--csv file.csv`, `--verbose`, `--delay 0.2`.

There is **no test suite, linter, or build step**. The simulator is the primary way
to exercise real bot logic end-to-end: prefer `python simulate.py --verbose --seed <n>`
to validate behavior changes. A single deterministic run = one `--seed`.

## Architecture (the big picture)

The bot runs **one cycle per invocation, then exits** — Windows Task Scheduler calls
`run_once.bat` hourly (matching H1 candles). There is no long-running process or loop
in production; the loop lives only in `simulate.py`.

`bot.run_cycle(cfg, broker, state, log)` is the single entry point and is written for
**dependency injection**: the same function drives both real trading and simulation.
Its cycle order is deliberate and safety-critical:
1. Sync broker-closed trades into today's loss count (`state.sync_closed_trade`).
2. Read equity → record health (`equity / start_capital`).
3. `RiskEngine.can_trade` gate (death floor + daily loss limit).
4. `Strategy.evaluate` on ~250 candles → `Signal`.
5. `RiskEngine.size_position` → signed units + stop price.
6. `broker.place_market_order` with a **mandatory** stop-loss.

**Broker interface is duck-typed, not a base class.** `broker.py` (`Broker`, real OANDA
via `oandapyV20`) and `paper_broker.py` (`PaperBroker`, simulated fills/spread/margin)
must expose the *same* methods: `equity`, `candles`, `open_trades`,
`recent_closed_trades`, `close_trade`, `place_market_order`. If you add/rename a method
on one, update the other or the simulator breaks silently.

Module roles: `config.py` (all knobs, loaded from `.env`), `risk_engine.py` (the
survival rules), `strategy.py` (MA-crossover + trend filter + ATR stops), `state.py`
(SQLite persistence), `backtest.py` (close-price sanity check on OANDA history).

## Key conventions

- **Config is centralized and immutable.** All tunables come from `.env` via the frozen
  `Config` dataclass. Never hard-code risk/strategy numbers elsewhere; add a field to
  `Config.load` (with a default) instead. Simulation overrides config with
  `dataclasses.replace(cfg, ...)` rather than mutation.
- **`LIVE` and `OANDA_ENV` are independent safety switches.** `LIVE=false` makes
  `place_market_order`/`close_trade` no-ops that only log intent, so the identical code
  path runs in dry-run and live. `OANDA_ENV` (`practice`/`live`) selects demo vs real
  money. Change one flag at a time.
- **Units are signed integers** everywhere: `+` = long, `-` = short, and are truncated
  with `int(...)`. Position size that rounds to 0 units is refused (preserve capital).
- **Every order carries a stop-loss** computed from ATR; the bot never averages down or
  removes a stop. Preserve this invariant in any change to sizing or execution.
- **Idempotency guards:** the scheduler may fire twice in an hour. `state.meta`
  (`last_signal_bar`) prevents acting twice on the same candle; `seen_closed_trades`
  prevents double-counting closed trades. Keep these when touching the cycle.
- **One position per instrument:** an aligned signal holds; an opposite signal closes
  the existing trade first, then re-reads equity before sizing.
- **Time is UTC.** Daily loss limits key off UTC date (`state._today`). `State` accepts a
  `clock` callable so the simulator can drive it from simulated bar time; use
  `state.now()`, not `datetime.now()`, inside logic.
- **Candle format:** strategy consumes OANDA-shaped dicts (`{"time", "mid": {o,h,l,c},
  "complete"}`) via `candles_to_df`; only `complete` candles are used. `PaperBroker`
  mimics this shape so `candles_to_df` is unchanged.
- **`run_cycle` never raises** — a top-level `except` returns exit code 1 so a crash can
  never blow up funds. Keep that guard intact.

## Financial safety

Defaults target a **demo/practice account** with tiny capital. Do not flip defaults to
live trading, raise risk limits, or weaken the death floor / stop-loss logic unless the
user explicitly asks. When in doubt, keep risk small and survival-first.
