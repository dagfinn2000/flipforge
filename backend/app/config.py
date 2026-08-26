from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every field can be overridden by an env var of the same name."""

    model_config = SettingsConfigDict(env_prefix="FF_", env_file=".env", extra="ignore")

    database_url: str = "postgresql://flipforge:flipforge@db:5432/flipforge"

    # --- Upstream data source -------------------------------------------------
    wiki_base: str = "https://prices.runescape.wiki/api/v1/osrs"
    # The OSRS Wiki asks every client to identify itself and leave a contact.
    # Set FF_CONTACT to your email or Discord handle -- it is the polite thing to
    # do and it is what keeps the API open for everyone.
    contact: str = "unset - please set FF_CONTACT"
    app_version: str = "1.0.0"

    # --- Poll cadence (seconds) ----------------------------------------------
    poll_latest_seconds: int = 45
    poll_5m_seconds: int = 300
    poll_1h_seconds: int = 900
    poll_mapping_seconds: int = 86400
    metrics_interval_seconds: int = 60

    # --- Startup backfill -----------------------------------------------------
    # Bulk endpoints return every item for one timestamp, so history is built by
    # walking timestamps backwards rather than by crawling items one by one.
    backfill_1h_steps: int = 336   # 14 days of hourly candles
    backfill_5m_steps: int = 288   # 24 hours of 5-minute candles
    backfill_rate_per_sec: float = 5.0
    backfill_on_start: bool = True

    # --- Grand Exchange tax ---------------------------------------------------
    # Jagex has tuned these numbers since the tax launched in Dec 2021, so they
    # are configuration rather than constants. Verify against the current
    # in-game rules if a patch lands.
    ge_tax_rate: float = 0.02
    ge_tax_cap: int = 5_000_000
    ge_tax_min_price: int = 100

    # --- Scanner defaults -----------------------------------------------------
    scanner_max_data_age_seconds: int = 3600

    @property
    def user_agent(self) -> str:
        return f"FlipForge/{self.app_version} (self-hosted market tracker; contact: {self.contact})"


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
