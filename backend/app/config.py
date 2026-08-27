from decimal import Decimal
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime configuration. Every field is overridable by an FF_-prefixed env var."""

    model_config = SettingsConfigDict(env_prefix="FF_", env_file=".env", extra="ignore")

    database_url: str = "postgresql://flipforge:flipforge@db:5432/flipforge"

    # --- Upstream data source -------------------------------------------------
    wiki_base: str = "https://prices.runescape.wiki/api/v2/osrs"
    # The wiki blocks default agents outright (python-requests, curl/*, Java/*,
    # and friends get a 400 with a pointer to the docs). A descriptive
    # User-Agent with a contact is the entire price of admission, so a missing
    # contact is a hard boot failure rather than a warning nobody reads.
    contact: str = ""
    app_version: str = "1.0"

    # --- Poll cadence (seconds) ----------------------------------------------
    poll_latest_seconds: int = 45
    poll_5m_seconds: int = 300
    poll_1h_seconds: int = 900
    poll_mapping_seconds: int = 86400
    metrics_interval_seconds: int = 60
    snapshot_interval_seconds: int = 3600     # score validation snapshots
    # Hours of history to rebuild snapshots for on first boot, so the score
    # validation page has data before the app has been running for a day.
    reconstruct_snapshots_hours: int = 96
    outcome_interval_seconds: int = 1800      # grade matured snapshots

    # --- Startup backfill -----------------------------------------------------
    # One /1h?timestamp= request covers every item for one hour, so history is
    # built by walking timestamps backwards rather than crawling items.
    backfill_1h_steps: int = 336   # 14 days
    backfill_5m_steps: int = 288   # 24 hours
    backfill_rate_per_sec: float = 5.0
    backfill_on_start: bool = True

    # --- Grand Exchange tax ---------------------------------------------------
    # 2% since 29 May 2025 (1% before that), floored per item, capped per item.
    # There is deliberately no minimum-price setting: the threshold below which
    # tax rounds to nothing is derived from the rate itself.
    ge_tax_rate: Decimal = Decimal("0.02")
    ge_tax_cap: int = 5_000_000
    seed_tax_exemptions: bool = True

    # --- Slot allocator -------------------------------------------------------
    ge_slots_members: int = 8
    ge_slots_f2p: int = 3
    allocator_max_share: float = 0.35   # no single item over this share of bankroll

    # --- Data retention -------------------------------------------------------
    # Every window below is driven by what actually reads the data. Candles are
    # a cache of an upstream API and can always be refetched, so keeping them is
    # a convenience rather than a duty; the app's own score history cannot be
    # refetched and is kept longer relative to what reads it.
    #
    # Set any of these to 0 to disable that particular cleanup.
    maintenance_interval_seconds: int = 86400   # daily

    # 5-minute candles: the rollup reads the last 70 minutes, the chart shows
    # 24 hours. This is ~85% of all data growth, so it is the one that matters.
    retain_5m_days: int = 7
    # Hourly candles: grading reaches back 30 days, the validation page allows a
    # 90 day lookback.
    retain_1h_days: int = 90
    # Backstop for everything else in the table, chiefly the 6h and 24h candles
    # behind the long chart timeframes.
    retain_candles_days: int = 400

    retain_snapshots_days: int = 60
    retain_outcomes_days: int = 30
    retain_alert_events_days: int = 90

    # --- Scanner defaults -----------------------------------------------------
    scanner_max_data_age_seconds: int = 3600

    @property
    def user_agent(self) -> str:
        return f"flipforge/{self.app_version} - {self.contact}"

    def require_contact(self) -> None:
        """Refuse to boot without a contact string.

        Booting without one produces a wall of 400s from upstream that looks
        like a network fault, so failing loudly here saves the confusion.
        """
        if not self.contact.strip() or self.contact.strip().lower() in {"unset", "changeme"}:
            raise RuntimeError(
                "FF_CONTACT is not set.\n"
                "The OSRS Wiki prices API requires a descriptive User-Agent with a way "
                "to reach you, and rejects requests without one.\n"
                "Set FF_CONTACT in your .env to an email address or Discord handle, "
                "for example:\n"
                "    FF_CONTACT=you@example.com\n"
            )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
