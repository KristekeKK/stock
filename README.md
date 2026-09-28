# Survival Trading Bot 🫀

A forex trading bot whose single overriding goal is **to not die**. Its "life" is
its account equity; if equity falls to the **death floor** it stops trading and
hibernates instead of blowing up the account. Built for OANDA + EUR/USD, Python,
Windows Task Scheduler.

> ⚠️ **Financial risk warning.** Automated forex trading with real money can lose
> money extremely fast. This project defaults to a **demo (practice) account** and
> a **dry-run** switch. Prove it works for weeks before you ever flip it live. You
> are solely responsible for any trades placed with real funds.

## How it maps to the idea
| Your idea | Implementation |
|---|---|
| Bot that buys & sells | `strategy.py` (MA crossover + trend filter) + `broker.py` (OANDA orders) |
| Goal: stay in surplus | Health metric = `equity / start_capital`, logged every cycle |
| Dies at $0 | `risk_engine.py` death floor halts trading before ruin |
| Fear of death | Risk engine dominates every decision: 1%/trade, mandatory stops, daily loss limit |

## Files
- `config.py` — all settings (from `.env`)
- `broker.py` — OANDA API adapter (account, candles, orders)
- `state.py` — SQLite persistence (equity log, trades, daily stats)
- `risk_engine.py` — **the survival instinct** (floor, sizing, stops, loss limit)
- `strategy.py` — MA crossover + trend filter, ATR stops
- `bot.py` — runs one safe cycle (what the scheduler calls)
- `backtest.py` — replay history to sanity-check before going live
- `paper_broker.py` — simulated OANDA account (fills, spread, stop-losses, margin)
- `simulate.py` / `simulate.bat` — **test version**: runs the real bot on a simulated market
- `run_once.bat` — Task Scheduler entry point

## Test version (no account needed) 🧪
Runs the **real** bot logic (same `bot.py`, strategy and risk engine) against a
paper broker, one simulated hour per cycle, and reports whether it survived.
```powershell
.\simulate.bat                       # ~6 months of synthetic EUR/USD, capital 10
.\simulate.bat --seed 7              # a different random market
.\simulate.bat --capital 100         # bigger starting account
.\simulate.bat --verbose             # show every cycle's reasoning
.\simulate.bat --delay 0.2           # watch it trade in slow motion
.\simulate.bat --csv eurusd_h1.csv   # replay real data (columns: time,open,high,low,close)
```
It prints every open/close, an ASCII equity chart, and a final status:
**ALIVE**, **HIBERNATING** (hit the death floor and stopped trading to survive) or
**DEAD** (equity reached 0). Risk settings come from `.env` if present, so you can
try e.g. `RISK_PER_TRADE_PCT=10` and see how quickly greed kills it.

> Synthetic markets are random, so a good result there does not mean the strategy
> makes money in real markets. Use it to test *behaviour*, then use the OANDA demo.

## Setup
1. **Create a virtual env + install deps**
   ```powershell
   py -3 -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```
2. **Get OANDA demo credentials** — sign up for a free *fxTrade Practice* account,
   then create a personal API token. Note your account ID (looks like `101-...`).
3. **Configure**
   ```powershell
   Copy-Item .env.example .env
   ```
   Edit `.env`: paste `OANDA_API_TOKEN` and `OANDA_ACCOUNT_ID`. Keep
   `OANDA_ENV=practice` and `LIVE=false` for now.

## Run
- **Dry run (no orders, just logs intent):**
  ```powershell
  py -3 bot.py
  ```
- **Backtest on history:**
  ```powershell
  py -3 backtest.py
  ```

## Going further (only when demo proves out)
1. Let it run on the demo account via Task Scheduler for a few weeks.
2. Review `bot_state.db` (equity log / trades) and `bot.log`.
3. If health stays healthy and it never "dies", THEN consider:
   - flip `LIVE=true` (still on demo → real order flow on demo), then
   - switch `OANDA_ENV=live` with live credentials.
   Change **one flag at a time** and re-verify.

## Schedule on Windows (hourly, matches H1 candles)
```powershell
schtasks /Create /SC HOURLY /TN "SurvivalTradingBot" /TR "C:\Git\stockmarket\run_once.bat" /RL LIMITED
```
Remove with:
```powershell
schtasks /Delete /TN "SurvivalTradingBot" /F
```

## Safety switches (in `.env`)
- `LIVE=false` — dry run; places no orders. Set `true` to actually trade.
- `OANDA_ENV=practice` — demo money. Set `live` for real money.
- `DEATH_FLOOR_PCT=20` — halt trading if equity drops 20% below start.
- `RISK_PER_TRADE_PCT=1` — max 1% of equity risked per trade.
- `DAILY_MAX_LOSSES=3` — stop for the day after 3 losing trades (counted from
  trades the broker reports as closed today, UTC).

## Known limitations
- Position sizing treats risk per unit as the stop distance in the quote currency
  (USD for EUR/USD). If your account currency is EUR, actual risk per trade
  differs slightly from 1% (by the EUR/USD rate). Keep risk small.
- One position at a time per instrument; an opposite signal closes it first.
- The death floor stops *new* trades. A trade already open when equity crosses
  the floor can still lose up to its stop, so equity may end slightly below it.
- The backtest is a rough sanity check (close-price fills, no spread/slippage).
