# FlipForge

Self-hosted, real-time market intelligence for Old School RuneScape. Every item,
every price, every five minutes -- with the maths that actually decides whether a
flip is worth doing.

```bash
git clone <this repo> && cd flipforge
cp .env.example .env      # set FF_CONTACT to your email or Discord handle
docker compose up -d
```

Open <http://localhost:8090>. First start pulls the item mapping, current prices
and two weeks of history; the UI is usable within seconds and fills in as the
backfill runs.

---

## What it does that a price site doesn't

**Every margin is after tax.** The Grand Exchange takes 2% of each sale, rounded
down per item, capped per item, and waived on items under 100gp and on the
exemption list (bonds, spade, hammer, ...). A whip with a 7k spread is a *losing*
flip once 16k of tax comes off. FlipForge never shows you a pre-tax margin.

**A ranking you can audit.** Every item gets a 0-100 flip score built from six
bounded components -- post-tax ROI, absolute gp per cycle, liquidity, margin
stability, fill time and quote freshness. The item page shows each component,
its weight and its contribution, so you can see *why* something ranked where it
did. ROI alone is how a 1gp spread on a 2gp feather becomes a "50% return"; the
profit term is what stops that outranking a real trade.

**The real two-sided market.** The chart plots what impatient buyers pay and what
impatient sellers accept as two separate lines, with the gap between them being
your gross margin, and volume on each side underneath. That is the picture a
flipper needs. Averaged single-line price charts hide exactly the thing you are
trading.

**Fill time, not just margin.** A 500k margin is worthless if the item trades
twice a day. Every row estimates how long a full 4-hour buy limit takes to fill
at current flow, and caps "profit per cycle" at what you could realistically move.

**Anomaly detection.** Items whose price *or* volume has broken out of their own
recent normal, separated into breakouts (price and volume moved together) and
thin moves (price moved on no volume -- usually someone pushing a shallow book).

**Tax-aware portfolio.** Log your trades; sells are matched against your oldest
open buys FIFO, so realised profit reflects what you actually paid, with tax
recorded at the moment of sale.

**No account, no paywall, no rate limits.** It is your server and your database.
The full JSON API is documented at `/api/docs` and is yours to script against.

---

## The screens

| Screen | What it is for |
| --- | --- |
| **Dashboard** | Market pulse: best flips right now, biggest movers over 1h/24h/7d, unusual activity |
| **Flip scanner** | Every tradeable item with a dozen filters and presets (balanced, high volume, big ticket, low capital, oversold). Enter your capital and it shows what you can actually afford, capped by the buy limit |
| **Item page** | Price history with SMA/VWAP/Bollinger overlays, score breakdown, market depth, a profit calculator and one-click alerts |
| **Watchlist** | Your tracked items, ranked live |
| **Portfolio** | FIFO positions, realised and unrealised P&L, tax paid |
| **Alerts** | Threshold rules on margin, ROI, price, volume, score or z-score. Fire as live toasts anywhere in the app |

Press <kbd>Cmd/Ctrl</kbd>+<kbd>K</kbd> or <kbd>/</kbd> anywhere to search items.

---

## How the data gets there

The source is the [OSRS Wiki real-time prices API](https://prices.runescape.wiki/),
the same feed the in-game-adjacent tools use. It is free and unauthenticated; the
one rule is that clients identify themselves, which is what `FF_CONTACT` is for.

| Job | Cadence | Cost |
| --- | --- | --- |
| Latest prices, all items | 45s | 1 request |
| 5-minute averages, all items | 5 min | 1 request |
| Hourly averages, all items | 15 min | 1 request |
| Item mapping | daily | 1 request |
| Rollup + alert evaluation | 60s | 0 (local SQL) |

History is backfilled by walking the *bulk* endpoints backwards -- one request
covers every item for one timestamp, so two weeks of hourly history for the whole
game costs 336 requests, once, ever. Per-item deep history (up to a year) is
fetched lazily the first time you open an item, then cached.

That is roughly **2 requests a minute** at steady state for the entire game.

---

## Configuration

Everything lives in `.env`. The values that matter:

| Variable | Default | Notes |
| --- | --- | --- |
| `FF_CONTACT` | *unset* | **Set this.** Your email or Discord handle, sent as the User-Agent |
| `FF_PORT` | `8090` | Port the UI is served on |
| `FF_POLL_LATEST_SECONDS` | `45` | How often to refresh every price |
| `FF_BACKFILL_1H_STEPS` | `336` | Hours of history to pull on first start |
| `FF_GE_TAX_RATE` | `0.02` | Sale tax rate |
| `FF_GE_TAX_CAP` | `5000000` | Max tax per item |
| `FF_GE_TAX_MIN_PRICE` | `100` | Sales below this are untaxed |

The tax settings are configuration rather than constants on purpose: Jagex has
changed the rules before. If an update lands, change the number here and every
margin, ROI, score and portfolio figure in the app follows immediately -- no code
change, no redeploy of anything but the API container.

---

## Development

```bash
# Backend: postgres in docker, API on the host with reload
docker compose up -d db
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt
FF_DATABASE_URL=postgresql://flipforge:flipforge@localhost:5432/flipforge \
  .venv/bin/uvicorn app.main:app --reload
```

(Uncomment the `ports` block on the `db` service first so the host can reach it.)

```bash
# Frontend: vite dev server, proxies /api to localhost:8000
cd frontend && pnpm install && pnpm dev
```

```bash
# Tests: the money math has full coverage
cd backend && .venv/bin/python -m pytest tests -q
```

### Layout

```
backend/app/
  analytics.py   tax, margins, indicators, scoring   <- the maths, pure and tested
  ingest.py      pollers, backfill, rollup, alerts
  wiki.py        upstream client
  schema.sql     tables and indexes
  routers/       items, scanner, watchlist, alerts, portfolio, ws
frontend/src/
  components/    Layout, PriceChart, ItemTable, SearchPalette
  pages/         Dashboard, Scanner, Item, Watchlist, Portfolio, Alerts
```

The scoring model is deliberately concentrated in one file. If you disagree with
how it weighs things -- and you might -- `WEIGHTS` and the three saturation bands
at the top of `analytics.py` are the only numbers you need to touch.

---

## Notes and limits

- Prices are what the wiki API reports: the most recent instant-buy and
  instant-sell each item traded at. They are real transactions, not offers, so a
  thinly traded item's "current price" can be hours old. Every table shows quote
  age; the scanner filters on it.
- Occasionally the instant-sell price sits *above* the instant-buy price. That is
  the real feed, not a bug -- it means the last two trades happened in an odd
  order. The resulting margin is correctly negative.
- Candle mode is derived. The exchange publishes an average high and low per
  interval, not a true open and close, so the previous midpoint opens each bar.
  The spread view is the honest one.
- This reports on a market. It does not decide for you, and a high score is not a
  promise -- an item can be liquid, stable and profitable right up until an update
  changes what it is worth.
