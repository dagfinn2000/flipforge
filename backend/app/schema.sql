CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------- reference --
CREATE TABLE IF NOT EXISTS items (
    id          INTEGER PRIMARY KEY,
    name        TEXT NOT NULL,
    examine     TEXT,
    members     BOOLEAN NOT NULL DEFAULT FALSE,
    value       BIGINT,
    lowalch     BIGINT,
    highalch    BIGINT,
    buy_limit   INTEGER,
    icon        TEXT,
    tax_exempt  BOOLEAN NOT NULL DEFAULT FALSE,
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS items_name_trgm ON items USING gin (lower(name) gin_trgm_ops);

-- ------------------------------------------------------------------- prices --
-- Newest instant-buy / instant-sell quote seen for each item.
CREATE TABLE IF NOT EXISTS latest (
    item_id    INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    high       BIGINT,
    high_time  TIMESTAMPTZ,
    low        BIGINT,
    low_time   TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- OHLC-style candles. timestep is one of 5m / 1h / 6h / 24h.
CREATE TABLE IF NOT EXISTS candles (
    item_id   INTEGER NOT NULL,
    timestep  TEXT NOT NULL,
    ts        TIMESTAMPTZ NOT NULL,
    avg_high  BIGINT,
    avg_low   BIGINT,
    high_vol  BIGINT NOT NULL DEFAULT 0,
    low_vol   BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (item_id, timestep, ts)
);
CREATE INDEX IF NOT EXISTS candles_recent ON candles (timestep, ts DESC);
CREATE INDEX IF NOT EXISTS candles_item_recent ON candles (item_id, timestep, ts DESC);

-- Per-item rollup recomputed on a timer so the scanner is a single fast scan.
CREATE TABLE IF NOT EXISTS metrics (
    item_id            INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    high               BIGINT,
    low                BIGINT,
    spread             BIGINT,
    tax                BIGINT,
    margin             BIGINT,
    roi                DOUBLE PRECISION,
    vol_1h             BIGINT,
    vol_24h            BIGINT,
    buy_vol_24h        BIGINT,
    sell_vol_24h       BIGINT,
    flow_ratio         DOUBLE PRECISION,   -- buy volume share, 0.5 == balanced
    avg_margin_24h     DOUBLE PRECISION,
    margin_stability   DOUBLE PRECISION,   -- 0..1, share of 24h candles profitable
    price_change_1h    DOUBLE PRECISION,
    price_change_24h   DOUBLE PRECISION,
    price_change_7d    DOUBLE PRECISION,
    volatility_24h     DOUBLE PRECISION,   -- stdev of hourly log returns
    zscore_24h         DOUBLE PRECISION,   -- current mid vs 24h mean
    vol_zscore         DOUBLE PRECISION,   -- current hour volume vs 7d hourly mean
    rsi_14             DOUBLE PRECISION,
    est_fill_hours     DOUBLE PRECISION,   -- hours to fill a full buy limit
    potential_profit   BIGINT,             -- margin * fillable quantity per 4h
    liquidity_score    DOUBLE PRECISION,
    flip_score         DOUBLE PRECISION,
    data_age_seconds   INTEGER,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS metrics_score ON metrics (flip_score DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS metrics_profit ON metrics (potential_profit DESC NULLS LAST);

-- ------------------------------------------------------------------- user ----
CREATE TABLE IF NOT EXISTS watchlist (
    item_id    INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    note       TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alerts (
    id         SERIAL PRIMARY KEY,
    item_id    INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    metric     TEXT NOT NULL,          -- high | low | margin | roi | vol_1h | zscore_24h
    op         TEXT NOT NULL,          -- above | below
    threshold  DOUBLE PRECISION NOT NULL,
    note       TEXT,
    active     BOOLEAN NOT NULL DEFAULT TRUE,
    cooldown_s INTEGER NOT NULL DEFAULT 900,
    last_fired TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS alerts_active ON alerts (active) WHERE active;

CREATE TABLE IF NOT EXISTS alert_events (
    id         SERIAL PRIMARY KEY,
    alert_id   INTEGER REFERENCES alerts(id) ON DELETE CASCADE,
    item_id    INTEGER NOT NULL,
    message    TEXT NOT NULL,
    value      DOUBLE PRECISION,
    seen       BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS alert_events_recent ON alert_events (created_at DESC);

-- Trade ledger. Sells are matched against open buys FIFO to realise P&L.
CREATE TABLE IF NOT EXISTS trades (
    id         SERIAL PRIMARY KEY,
    item_id    INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    side       TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    quantity   BIGINT NOT NULL CHECK (quantity > 0),
    price      BIGINT NOT NULL CHECK (price >= 0),
    tax_paid   BIGINT NOT NULL DEFAULT 0,
    note       TEXT,
    executed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS trades_item ON trades (item_id, executed_at);

-- Free-form key/value for ingest bookkeeping.
CREATE TABLE IF NOT EXISTS meta (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
