"""Configuration loader.

Reads settings from environment variables (populated from a local .env file).
All risk/strategy knobs live here so the rest of the code never hard-codes them.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from dotenv import load_dotenv

load_dotenv()


def _get(name: str, default: str | None = None) -> str:
    val = os.getenv(name, default)
    if val is None:
        raise RuntimeError(f"Missing required config value: {name}")
    return val


def _get_float(name: str, default: float) -> float:
    return float(os.getenv(name, str(default)))


def _get_int(name: str, default: int) -> int:
    return int(os.getenv(name, str(default)))


def _get_bool(name: str, default: bool) -> bool:
    return os.getenv(name, str(default)).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class Config:
    # --- OANDA ---
    api_token: str
    account_id: str
    env: str  # "practice" or "live"

    # --- Trading ---
    instrument: str
    granularity: str

    # --- Survival / risk ---
    start_capital: float
    death_floor_pct: float
    risk_per_trade_pct: float
    daily_max_losses: int

    # --- Strategy ---
    fast_ma: int
    slow_ma: int
    trend_ma: int
    atr_period: int
    atr_stop_mult: float

    # --- Master safety switch ---
    live: bool

    @property
    def death_floor_equity(self) -> float:
        """Equity level at/below which the bot stops opening trades."""
        return self.start_capital * (1.0 - self.death_floor_pct / 100.0)

    @classmethod
    def load(cls, require_credentials: bool = True) -> "Config":
        if require_credentials:
            token, account = _get("OANDA_API_TOKEN"), _get("OANDA_ACCOUNT_ID")
        else:
            token = os.getenv("OANDA_API_TOKEN", "")
            account = os.getenv("OANDA_ACCOUNT_ID", "")
        return cls(
            api_token=token,
            account_id=account,
            env=_get("OANDA_ENV", "practice").strip().lower(),
            instrument=_get("INSTRUMENT", "EUR_USD"),
            granularity=_get("GRANULARITY", "H1"),
            start_capital=_get_float("START_CAPITAL", 10.0),
            death_floor_pct=_get_float("DEATH_FLOOR_PCT", 20.0),
            risk_per_trade_pct=_get_float("RISK_PER_TRADE_PCT", 1.0),
            daily_max_losses=_get_int("DAILY_MAX_LOSSES", 3),
            fast_ma=_get_int("FAST_MA", 10),
            slow_ma=_get_int("SLOW_MA", 30),
            trend_ma=_get_int("TREND_MA", 100),
            atr_period=_get_int("ATR_PERIOD", 14),
            atr_stop_mult=_get_float("ATR_STOP_MULT", 2.0),
            live=_get_bool("LIVE", False),
        )
