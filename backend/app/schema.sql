-- FlipForge schema. Applied idempotently on every boot.

CREATE EXTENSION IF NOT EXISTS pg_trgm;
CREATE EXTENSION IF NOT EXISTS timescaledb;

-- ------------------------------------------------------------- migrations --
-- Brings a database created by an earlier version up to date. Guarded on
-- information_schema so it is a no-op on a fresh install and on a current one.
DO $migrate$
BEGIN
    -- `metrics` is entirely derived and rebuilt every minute, so the cheapest
    -- correct upgrade is to drop it and let the next rollup repopulate.
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'metrics' AND column_name = 'spread') THEN
        DROP TABLE metrics;
        RAISE NOTICE 'flipforge: rebuilt metrics for the post-tax schema';
    END IF;

    -- Tax exemption moved from a boolean on items to its own editable table.
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'items' AND column_name = 'tax_exempt') THEN
        ALTER TABLE items DROP COLUMN tax_exempt;
    END IF;

    -- Interval averages carry decimals; money must not round through BIGINT.
    IF EXISTS (SELECT 1 FROM information_schema.columns
                WHERE table_name = 'candles' AND column_name = 'avg_high'
                  AND data_type <> 'numeric') THEN
        ALTER TABLE candles ALTER COLUMN avg_high TYPE NUMERIC(16, 2);
        ALTER TABLE candles ALTER COLUMN avg_low  TYPE NUMERIC(16, 2);
        RAISE NOTICE 'flipforge: widened candle averages to NUMERIC';
    END IF;

    -- Hysteresis and arming arrived with the alert de-spam work.
    IF to_regclass('public.score_snapshots') IS NOT NULL THEN
        ALTER TABLE score_snapshots ADD COLUMN IF NOT EXISTS
            source TEXT NOT NULL DEFAULT 'live';
        ALTER TABLE score_snapshots ADD COLUMN IF NOT EXISTS quantity BIGINT;
    END IF;

    IF to_regclass('public.score_outcomes') IS NOT NULL THEN
        ALTER TABLE score_outcomes ADD COLUMN IF NOT EXISTS
            realised_cycle_profit BIGINT;
    END IF;

    IF to_regclass('public.alerts') IS NOT NULL THEN
        ALTER TABLE alerts ADD COLUMN IF NOT EXISTS
            hysteresis DOUBLE PRECISION NOT NULL DEFAULT 0;
        ALTER TABLE alerts ADD COLUMN IF NOT EXISTS
            armed BOOLEAN NOT NULL DEFAULT TRUE;
    END IF;
END
$migrate$;

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
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS items_name_trgm ON items USING gin (lower(name) gin_trgm_ops);

-- Tax exemptions live as data, not as a constant in business logic. Seeded on
-- first boot and editable through the API so a game update is a row change.
CREATE TABLE IF NOT EXISTS tax_exemptions (
    item_id    INTEGER PRIMARY KEY,
    name       TEXT NOT NULL,
    source     TEXT NOT NULL DEFAULT 'seed',
    note       TEXT,
    added_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- ------------------------------------------------------------------- prices --
CREATE TABLE IF NOT EXISTS latest (
    item_id    INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    high       BIGINT,
    high_time  TIMESTAMPTZ,
    low        BIGINT,
    low_time   TIMESTAMPTZ,
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Interval averages. `timestep` is 5m / 1h / 6h / 24h.
-- Money columns are BIGINT (prices exceed 32-bit) and averages NUMERIC.
CREATE TABLE IF NOT EXISTS candles (
    item_id   INTEGER NOT NULL,
    timestep  TEXT NOT NULL,
    ts        TIMESTAMPTZ NOT NULL,
    avg_high  NUMERIC(16, 2),
    avg_low   NUMERIC(16, 2),
    high_vol  BIGINT NOT NULL DEFAULT 0,
    low_vol   BIGINT NOT NULL DEFAULT 0,
    PRIMARY KEY (item_id, timestep, ts)
);
SELECT create_hypertable(
    'candles', 'ts',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE,
    migrate_data => TRUE
);
CREATE INDEX IF NOT EXISTS candles_recent ON candles (timestep, ts DESC);
CREATE INDEX IF NOT EXISTS candles_item_recent ON candles (item_id, timestep, ts DESC);

-- Per-item rollup, recomputed on a timer so the scanner is one fast scan.
CREATE TABLE IF NOT EXISTS metrics (
    item_id            INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    high               BIGINT,
    low                BIGINT,
    tax                BIGINT,
    margin             BIGINT,          -- post-tax, always
    roi                NUMERIC(12, 6),
    breakeven_sell     BIGINT,
    crossed            BOOLEAN NOT NULL DEFAULT FALSE,
    vol_1h             BIGINT,
    vol_24h            BIGINT,
    buy_vol_24h        BIGINT,
    sell_vol_24h       BIGINT,
    flow_ratio         NUMERIC(6, 4),
    avg_margin_24h     NUMERIC(16, 2),
    margin_cv          NUMERIC(12, 6),  -- stdev of the spread over its own mean
    margin_positive_24h NUMERIC(6, 4),  -- share of the day the flip was live
    price_change_1h    NUMERIC(14, 6),
    price_change_24h   NUMERIC(14, 6),
    price_change_7d    NUMERIC(14, 6),
    volatility_24h     DOUBLE PRECISION,  -- statistics, not money
    zscore_24h         DOUBLE PRECISION,
    vol_zscore         DOUBLE PRECISION,
    rsi_14             DOUBLE PRECISION,
    est_fill_hours     DOUBLE PRECISION,
    potential_profit   BIGINT,
    flip_score         NUMERIC(6, 2),
    score_components   JSONB,           -- value, weight and contribution per term
    data_age_seconds   INTEGER,
    updated_at         TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS metrics_score ON metrics (flip_score DESC NULLS LAST);
CREATE INDEX IF NOT EXISTS metrics_profit ON metrics (potential_profit DESC NULLS LAST);

-- ------------------------------------------------------ score validation ----
-- Hourly snapshot of what the model claimed, so it can be graded later against
-- what the market actually did. A score nobody checks is decoration.
CREATE TABLE IF NOT EXISTS score_snapshots (
    item_id    INTEGER NOT NULL,
    ts         TIMESTAMPTZ NOT NULL,
    score      NUMERIC(6, 2),
    buy        BIGINT,
    sell       BIGINT,
    margin     BIGINT,
    roi        NUMERIC(12, 6),
    vol_24h    BIGINT,
    -- Units realistically fillable in one 4h cycle at snapshot time. Grading
    -- per unit is meaningless across items: a 1gp feather margin against a
    -- 30,000 buy limit and a 70k Scythe margin against a limit of 8 are not
    -- comparable numbers. Cycle profit is the unit the score optimises.
    quantity   BIGINT,
    -- 'live' snapshots freeze the real scoreboard at the time it was shown.
    -- 'reconstructed' ones are rebuilt from stored candles for hours that
    -- predate the install, so the harness has something to grade on day one.
    -- They are an approximation and are labelled as such everywhere.
    source     TEXT NOT NULL DEFAULT 'live',
    PRIMARY KEY (item_id, ts)
);
SELECT create_hypertable(
    'score_snapshots', 'ts',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE,
    migrate_data => TRUE
);
CREATE INDEX IF NOT EXISTS score_snapshots_ts ON score_snapshots (ts DESC);

-- What a flip entered at that snapshot would actually have returned.
CREATE TABLE IF NOT EXISTS score_outcomes (
    item_id          INTEGER NOT NULL,
    ts               TIMESTAMPTZ NOT NULL,
    horizon          TEXT NOT NULL,        -- 1h | 4h | 24h
    exit_price       BIGINT,
    realised_margin  BIGINT,               -- post-tax, per unit
    realised_cycle_profit BIGINT,          -- per unit x fillable quantity
    realised_roi     NUMERIC(14, 6),
    computed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (item_id, ts, horizon)
);
-- A hypertable so retention is a chunk drop rather than a DELETE of millions
-- of rows: this is the second largest table and the one that grows fastest
-- after 5-minute candles.
SELECT create_hypertable(
    'score_outcomes', 'ts',
    chunk_time_interval => INTERVAL '7 days',
    if_not_exists => TRUE,
    migrate_data => TRUE
);
CREATE INDEX IF NOT EXISTS score_outcomes_ts ON score_outcomes (ts DESC);

-- ------------------------------------------------------------------- user ----
CREATE TABLE IF NOT EXISTS watchlist (
    item_id    INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    note       TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS alerts (
    id          SERIAL PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    metric      TEXT NOT NULL,
    op          TEXT NOT NULL,            -- above | below
    threshold   DOUBLE PRECISION NOT NULL,
    -- Hysteresis: once fired, the value must retreat past threshold -/+ this
    -- band before the alert is allowed to arm again. Without it a value sitting
    -- on the threshold fires every single evaluation.
    hysteresis  DOUBLE PRECISION NOT NULL DEFAULT 0,
    armed       BOOLEAN NOT NULL DEFAULT TRUE,
    note        TEXT,
    active      BOOLEAN NOT NULL DEFAULT TRUE,
    cooldown_s  INTEGER NOT NULL DEFAULT 900,
    last_fired  TIMESTAMPTZ,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
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

CREATE TABLE IF NOT EXISTS trades (
    id          SERIAL PRIMARY KEY,
    item_id     INTEGER NOT NULL REFERENCES items(id) ON DELETE CASCADE,
    side        TEXT NOT NULL CHECK (side IN ('buy', 'sell')),
    quantity    BIGINT NOT NULL CHECK (quantity > 0),
    price       BIGINT NOT NULL CHECK (price >= 0),
    tax_paid    BIGINT NOT NULL DEFAULT 0,
    note        TEXT,
    executed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS trades_item ON trades (item_id, executed_at);

-- Pins and exclusions for the slot allocator.
CREATE TABLE IF NOT EXISTS allocator_prefs (
    item_id    INTEGER PRIMARY KEY REFERENCES items(id) ON DELETE CASCADE,
    mode       TEXT NOT NULL CHECK (mode IN ('pin', 'exclude')),
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Buckets the gap filler has already asked upstream about. Some windows are
-- genuinely thin -- a game update, or the servers being down -- and without a
-- record of having checked, those would be re-requested on every pass forever.
CREATE TABLE IF NOT EXISTS candle_gap_checks (
    timestep      TEXT NOT NULL,
    ts            TIMESTAMPTZ NOT NULL,
    rows_returned INTEGER NOT NULL,
    checked_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (timestep, ts)
);

CREATE TABLE IF NOT EXISTS meta (
    key        TEXT PRIMARY KEY,
    value      TEXT,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
