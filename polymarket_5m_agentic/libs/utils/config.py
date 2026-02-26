from __future__ import annotations

import os
from pydantic import BaseModel, Field


class RiskConfig(BaseModel):
    max_position_usd: float = 50_000
    max_concurrent: int = 5
    daily_loss_limit_usd: float = -2_000
    correlation_threshold: float = 0.7
    liquidity_min_usd: float = 10_000
    min_edge_bps: float = 20.0  # 0.20%
    min_confidence: float = 0.60


class AppConfig(BaseModel):
    kafka_bootstrap: str = Field(default_factory=lambda: os.getenv("KAFKA_BOOTSTRAP", "localhost:9092"))
    redis_url: str = Field(default_factory=lambda: os.getenv("REDIS_URL", "redis://localhost:6379/0"))
    pg_dsn: str = Field(default_factory=lambda: os.getenv("PG_DSN", "postgresql://postgres:postgres@localhost:5432/pm5m"))
    risk: RiskConfig = Field(default_factory=RiskConfig)

    # Feeds
    binance_ws: str = "wss://stream.binance.com:9443/ws/btcusdt@bookTicker"

    # Polymarket / execution placeholders
    polymarket_api_base: str = Field(default_factory=lambda: os.getenv("PM_API_BASE", "https://clob.polymarket.com"))
    polymarket_key: str | None = Field(default_factory=lambda: os.getenv("PM_API_KEY"))


def load_config() -> AppConfig:
    return AppConfig()
