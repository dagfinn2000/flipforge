# FlipForge

Self-hosted, real-time market intelligence for Old School RuneScape. Every
tradeable item, refreshed continuously, with the post-tax maths that actually
decides whether a flip is worth doing.

```bash
git clone https://github.com/dagfinn2000/flipforge.git && cd flipforge
cp .env.example .env          # set FF_CONTACT — the app will not start without it
docker compose up -d
```

Open <http://localhost:8090>. No cloud services, no API keys, no accounts.

---

## The one thing this app will not do

**It will never show you a pre-tax margin.** Not as a secondary column, not in a
tooltip, not anywhere.

An abyssal whip with a 21,000gp raw spread is a *losing* flip once the 2% sale
tax lands. A price site that shows you the spread is telling you something true
and useless. Every margin, ROI, score, allocation and portfolio figure here is
net of tax, and the tax rules are configuration rather than constants because
Jagex has already changed them once.

Related, and the single most common flipping mistake: **selling at your buy price
loses money.** Buy at 1,000,000 and sell at 1,000,000 and you are down 20,000.
Every item page shows the breakeven sell price — the lowest price that actually
covers your cost.

---

## What is here

| Screen | What it answers |
| --- | --- |
| **Dashboard** | What is the market doing right now: best flips, movers over 1h/24h/7d, unusual activity, total volume |
| **Flip scanner** | Every item, filterable on margin, ROI, volume, buy limit, price band, score, quote age, fill time and margin steadiness. Enter your capital and it shows only what you can afford, quantity capped by the buy limit |
| **Slot allocator** | Given *this* bankroll and *these* slots, what should I actually buy — the question a ranked list cannot answer |
| **Item page** | Two-sided price chart, auditable score breakdown, market depth, rolling buy-limit state, profit calculator, one-click alerts |
| **Portfolio** | FIFO cost basis, realised and unrealised P&L, breakeven per position, total tax paid |
| **Alerts** | Threshold rules with hysteresis and cooldown, delivered live over a websocket |
| **Score check** | Whether the flip score actually predicted anything, measured against what the market did |

<kbd>Cmd/Ctrl</kbd>+<kbd>K</kbd> or <kbd>/</kbd> opens item search from anywhere.

### The slot allocator

You have eight Grand Exchange slots (three on free-to-play) and a fixed bankroll.
The best single flip is rarely the best use of all eight, and buy limits mean the
top-ranked item usually cannot absorb your capital.

Formulated as a bounded knapsack with a slot constraint. One structural property
makes it tractable: profit and capital are both linear in quantity for a given
item, so the return per coin is exactly its ROI, and there is never a reason to
part-fund an item while a higher-ROI one is still short. An optimal plan
therefore funds every chosen item to its cap except at most one. That reduces
the problem to choosing which items get slots, solved by a greedy seed on the
real objective followed by local swap improvement.

It is a heuristic, not a proof of optimality, and it is labelled as one. It
respects buy limits, per-item capital, and a diversification cap so it will not
put everything into one thin item. Pin an item to force it in, exclude one to
rule it out, and re-solve.

### The flip score, and checking whether it works

Six bounded components, weighted:

| Component | Weight | What it measures |
| --- | --- | --- |
| Profit | 0.26 | Absolute gp per 4h cycle |
| ROI | 0.22 | Post-tax return on capital, saturating |
| Liquidity | 0.18 | Units traded, both sides, over 24h |
| Stability | 0.16 | Spread's standard deviation over its own mean |
| Fill | 0.10 | Time to fill a full buy limit at current flow |
| Freshness | 0.08 | Age of the last real trade |

Every component is bounded so no single term can run away. The profit term is
what stops a 1gp spread on a 2gp feather — a genuine 50% ROI — from outranking a
real trade. All weights and saturation bands are named constants at the top of
[`backend/app/scoring.py`](backend/app/scoring.py) so they can be argued with and
changed in one place. Each item's per-component value, weight and contribution
are stored and rendered on its page, so a ranking can always be audited.

And then the part most tools skip: **the score is graded.** Every hour the
scoreboard is frozen. Once a holding period elapses, each frozen row is checked
against what the market actually did — buy at the instant-sell price recorded
then, exit at the item's average instant-buy price one period later, minus tax.
The Score check page shows realised return by score decile.

Judge it on **gp per cycle, not per unit**: a 1gp margin against a 30,000 buy
limit beats a 70k margin against a limit of 8, and per-unit figures hide that
completely. This is not a hypothetical — grading per unit made the model look
flat, and the error was in the measurement.

---

## Data source and request budget

The [OSRS Wiki real-time prices API](https://prices.runescape.wiki/), v2. Free
and unauthenticated. The one rule is a descriptive User-Agent with a contact,
which is what `FF_CONTACT` is for — the wiki rejects default agents outright
(`python-requests`, `curl/*`, `Java/*` and friends get a 400), so the app refuses
to boot without it rather than emitting a wall of failures that looks like a
network fault.

| Job | Cadence | Requests |
| --- | --- | --- |
| `/latest` — every item's current quote | 45s | 1 |
| `/5m` — five-minute averages, every item | 5 min | 1 |
| `/1h` — hourly averages, every item | 15 min | 1 |
| `/mapping` — item reference data | daily | 1 |
| Rollup, scoring, alert evaluation | 60s | 0 (local SQL) |
| Score snapshot and grading | hourly | 0 (local SQL) |

**About 2 requests per minute at steady state, for the entire game.**

The id-less bulk endpoints return every item in one response. Nothing here ever
loops `/latest?id=` over the item list. History is backfilled by walking
`/1h?timestamp=` backwards in 3600s steps, so two weeks of hourly candles for all
~4,000 items costs 336 requests, once. Per-item `/timeseries` is used only for
lazy deep history when you open an item page, and the result is cached.

---

## Configuration

Everything lives in `.env`. [`.env.example`](.env.example) is the annotated
reference — copy it and edit. The settings most people touch:

| Variable | Default | Meaning |
| --- | --- | --- |
| `FF_CONTACT` | *(none)* | **Required.** Email or Discord handle, sent in the User-Agent. The API exits at startup without it |
| `FF_PORT` | `8090` | Host port for the web UI |
| `POSTGRES_USER` / `_PASSWORD` / `_DB` | `flipforge` | Database credentials |
| `FF_POLL_LATEST_SECONDS` | `45` | Full price refresh interval |
| `FF_METRICS_INTERVAL_SECONDS` | `60` | Rollup and alert evaluation interval |
| `FF_SNAPSHOT_INTERVAL_SECONDS` | `3600` | How often the scoreboard is frozen for grading |
| `FF_BACKFILL_ON_START` | `true` | Build price history on first boot |
| `FF_BACKFILL_1H_STEPS` | `336` | Hours of hourly history to backfill (14 days) |
| `FF_BACKFILL_5M_STEPS` | `288` | Five-minute windows to backfill (24 hours) |
| `FF_RECONSTRUCT_SNAPSHOTS_HOURS` | `96` | Hours of score snapshots to rebuild from stored candles on first boot, so the Score check page has data on day one |
| `FF_GE_TAX_RATE` | `0.02` | Sale tax rate |
| `FF_GE_TAX_CAP` | `5000000` | Maximum tax per item |
| `FF_GE_SLOTS_MEMBERS` / `_F2P` | `8` / `3` | Grand Exchange slot counts |
| `FF_ALLOCATOR_MAX_SHARE` | `0.35` | Default diversification cap per item |
| `FF_SEED_TAX_EXEMPTIONS` | `true` | Seed the exemption table on first boot |
| `FF_WIKI_BASE` | v2 prices API | Only change to point at a mirror |

Poll intervals (`FF_POLL_5M_SECONDS`, `FF_POLL_1H_SECONDS`,
`FF_POLL_MAPPING_SECONDS`, `FF_OUTCOME_INTERVAL_SECONDS`,
`FF_BACKFILL_RATE_PER_SEC`, `FF_SCANNER_MAX_DATA_AGE_SECONDS`) are all
configurable too and documented in `.env.example`.

There is deliberately **no minimum-taxable-price setting**. That threshold is
*derived* from the rate: tax floors per item, so it is wherever
`floor(price × rate)` first reaches 1 — 50gp at 2%. The widely repeated "under
100gp is untaxed" is a leftover from the 1% era and under-reports tax on
everything between 50 and 99gp.

The exemption list (bonds, spade, hammer, gloves of silence, …) is a seeded
database table, not a constant. Read it at `/api/config/exemptions` and edit it
through the same endpoint; the policy reloads immediately.

---

## Stack

FastAPI + Python 3.12, PostgreSQL with TimescaleDB (candles and score snapshots
are hypertables), React + TypeScript + Vite, lightweight-charts, nginx. Three
containers.

Money is `BIGINT` and `NUMERIC` end to end — prices exceed 32-bit and averages
carry decimals. The tax module works in `Decimal`, never `float`: binary floats
cannot represent 0.02 exactly, and a rounding error in a tax calculation is a lie
about someone's profit. Statistical values that are not money (z-scores, RSI,
volatility) are double precision, which is the right type for them.

### Development

```bash
docker compose up -d db
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt
FF_CONTACT=you@example.com FF_DATABASE_URL=postgresql://flipforge:flipforge@localhost:5432/flipforge \
  .venv/bin/uvicorn app.main:app --reload
```

(Uncomment the `ports` block on the `db` service first.)

```bash
cd frontend && pnpm install && pnpm dev
```

```bash
cd backend && .venv/bin/python -m pytest tests -q
```

The money module has full unit coverage plus property-based tests (hypothesis):
tax is never negative and never exceeds the cap, breakeven always satisfies
`net_received >= buy` and is minimal, FIFO matching conserves quantity, the score
stays inside 0–100 for any input, and the allocator never exceeds the bankroll or
the slot count.

### Layout

```
backend/app/
  money.py       tax, margins, breakeven, buy-limit windows, FIFO   <- the maths
  scoring.py     the six components, their weights and bands
  allocator.py   bounded knapsack over GE slots
  indicators.py  SMA, EMA, RSI, Bollinger, VWAP, volatility
  policy.py      loads tax rules from config + the exemptions table
  ingest.py      pollers, backfill, rollup, snapshot and grading jobs
  wiki.py        upstream v2 client
  schema.sql     tables, hypertables and migrations
  routers/       items, scanner, allocator, validation, portfolio, alerts, config, ws
frontend/src/
  components/    Layout, PriceChart, ItemTable, SearchPalette
  pages/         Dashboard, Scanner, Allocator, Item, Watchlist, Portfolio, Alerts, Validation
```

Full JSON API at **`/api/docs`**, scriptable, CORS-open. The UI is one client,
not the only one.

---

## Data quality, stated plainly

**Prices are the last real transaction, not a live order book.** A thin item's
"current price" can be hours old. Quote age is shown on every row and the scanner
filters on it.

**The instant-sell price sometimes sits above the instant-buy price.** That is
the genuine feed, not a bug — the last two trades landed in an odd order. The
margin is correctly negative, the row is flagged as a crossed quote, and nothing
is clamped to zero to make it look tidy.

**Candles are derived.** The API publishes an average high and low per interval,
not a true open and close. Candle mode opens each bar at the previous midpoint
and says so; the two-sided spread view is the honest one and is the default.

**Midpoints require agreement.** A window where only one side traded, or where
someone paid 11,000gp for a 400gp cape, produces no midpoint rather than a fake
one. Without this, "biggest mover" lists fill up with mithril daggers at +2,881%.

**Reconstructed score snapshots are approximations.** Rows rebuilt for hours
predating your install use hourly averages instead of live quotes and cannot
recover quote freshness at all. They are labelled `reconstructed` everywhere and
can be filtered out.

**Expected profit assumes both sides fill.** That is the optimistic case. Fill
time estimates assume you capture about a quarter of one side's flow.

---

## Limits

This reports on a market. It does not decide for you.

A high score is not a promise. It is a statement that an item currently has a
post-tax edge, trades enough to matter, has held that edge recently, and can
plausibly be filled inside a four-hour window. Every one of those can stop being
true between the page loading and your offer filling.

An item can be liquid, stable and profitable right up until a game update changes
what it is worth. No amount of history predicts a patch note. The Score check
page tells you how the model has done lately, which is the most honest thing any
tool of this kind can offer, and it is deliberately capable of telling you the
model is not working.
