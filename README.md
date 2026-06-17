# refurbed.hr MacBook deal monitor

Watches [refurbed.hr](https://www.refurbed.hr) for good **Apple-Silicon** MacBook
deals and emails you when something worth buying appears. Pure-HTTP (no browser),
runs on a cloud cron.

The email leads with **⭐ TOP PONUDE** — a short, ranked shortlist so you don't
read 50 lines. Ranking is done by **Gemini AI** (with a deterministic fallback),
factoring in the **% discount** vs list price (the "−63%" bagatela signal),
absolute price vs budget, and spec. Below that it still reports:

1. **Absolute deals** — cheapest *available* offers ≥16 GB / ≥256 GB under the ceiling.
2. **Marginal anomalies** — near-free one-axis upgrades: *24 GB for +5 €*,
   *512 GB for +30 €*, *new battery for +20 €*.
3. **Cheapest-path-to-spec** — cheapest way to reach 16/256, 16/512, 24/512.

Tuned to the owner: **≤ 1100 €**, **≥16 GB / ≥256 GB**, **US or HR keyboard only**
(whichever is cheaper), Apple Silicon only. **Intel Macs are always excluded.**

See [`FINDINGS.md`](FINDINGS.md) for how the data source was reverse-engineered.

---

## How it works (30-second version)

For each product in the watchlist it seeds from the page's JSON-LD, then **BFS-
crawls the config matrix over plain HTTP** by following the native
`<select><option value="<variant URL>">` links. Every fetched variant page yields
one fully-specced concrete offer (read from an embedded Google-Analytics
dataLayer). The analysis engine then computes deals / anomalies / cheapest paths
from the concrete offers, and emails you only the **new** findings.

```
refurbed/
  config.py    # all tunables + the product watchlist
  parse.py     # HTML/dataLayer → Offer (+ §4-anchor-validated parsers)
  crawl.py     # polite requests session + per-product BFS
  analyze.py   # Offer model + absolute_deals / cheapest_path / marginal_anomalies
  notify.py    # seen.json dedup + email/Telegram + Croatian report rendering
monitor.py     # entrypoint / CLI
tests/         # offline tests against saved fixtures (21 tests)
```

---

## Setup

```bash
cd refurbed-monitor
python3 -m venv .venv && . .venv/bin/activate
pip install -r requirements.txt          # just `requests`

cp .env.example .env                      # then edit .env (see below)
```

### Email credentials (`.env`)

Gmail with an **App Password** (not your normal password):
Google Account → Security → 2-Step Verification → **App passwords**.

```ini
SMTP_USER=youraddress@gmail.com
SMTP_PASS=your_16_char_app_password
ALERT_TO=youraddress@gmail.com      # comma-separate for multiple recipients
# optional: SMTP_HOST (default smtp.gmail.com), SMTP_PORT (default 465)
```

Prefer Telegram? Set `TELEGRAM_BOT_TOKEN` + `TELEGRAM_CHAT_ID` instead (or as
well). If no channel is configured, the run still prints the full report.

---

## Usage

```bash
python3 monitor.py                 # full run: crawl → AI-rank → email if news
python3 monitor.py --mode light    # fast scan of cheapest configs (catch steals)
python3 monitor.py --no-ai         # skip Gemini, use deterministic ranking
python3 monitor.py --dry-run       # print the report, never send
python3 monitor.py --no-state      # treat everything as new, don't touch seen.json
python3 monitor.py --no-cache      # always hit the network (ignore page cache)
python3 monitor.py --products apple-macbook-air-m1-2020   # one product
python3 monitor.py --max-fetches 30                       # smaller/faster crawl
python3 monitor.py --offers-json out.json                 # dump raw offers
```

Set `GEMINI_API_KEY` (env or `.env`) to enable AI ranking; without it the
monitor uses its built-in value-score ranking and still works.

A run emails **only when there are new signals**. A second run with no market
change sends nothing (it just logs) — see *Dedup* below.

### Tuning (top of `refurbed/config.py`)

| Setting | Default | Meaning |
|---|---|---|
| `CEILING` | 1100 | absolute-deal price ceiling (€) |
| `GOOD_RAM` / `GOOD_STORAGE` | 16 / 256 | "good enough" spec |
| `KEYBOARD_FILTER` | `["US","HR"]` | accepted layouts (cheapest of these wins); `None` = any |
| `DREAM_DISCOUNT_PCT` | 40 | flag as STEAL if ≥ this % off list |
| `MARGINAL_RAM/STORAGE/BATTERY_MAX` | 40/60/35 | near-free upgrade caps (€) |
| `TARGET_SPECS` | (16,256),(16,512),(24,512) | cheapest-path targets |
| `REQUIRE_SILICON` | True | exclude Intel |
| `WATCHLIST` | 7 models | M1/M2/M3/M4 Air + 3 Pros |
| `GEMINI_MODEL` | gemini-2.5-flash | AI ranker (best price/quality; swap freely) |
| `TOP_PICKS` | 8 | how many ranked picks to headline |
| `MAX_FETCHES_PER_PRODUCT` / `LIGHT_MAX_FETCHES` | 70 / 8 | full vs light crawl cap |
| `REQUEST_DELAY` | 1.0 s | polite spacing between requests |

---

## Cron / deployment

You don't want this on a `crontab` on your laptop (laptop has to be on). See
**[`DEPLOY.md`](DEPLOY.md)** for free no-laptop options — the repo ships with a
ready **GitHub Actions** cron (`.github/workflows/monitor.yml`) that runs in the
cloud for free and commits `seen.json` back so dedup state survives.

Local cron (if you do want it on an always-on machine you own) — `run.sh` loads
`.env`, prefers `.venv`, logs to `monitor.log`:

```cron
# refurbed MacBook monitor — 08:00, 13:00, 18:00, 22:00 (server local time)
0 8,13,18,22 * * *  /ABSOLUTE/PATH/refurbed-monitor/run.sh
```

---

## Dedup / state

`seen.json` tracks each finding by its **config identity** (not its price):
`{"OFF|<product>|<ram>/<storage>|<colour>|<condition>|<kb>|<battery>": {"p": <price>, "t": <iso>}}`
(plus `PATH|…` and `ANOM|…` keys).

A config triggers an email only when it's **new** or its price **drops
meaningfully** vs the price you were last alerted on — by ≥ `REALERT_DROP_PCT`
(3%) or ≥ `REALERT_DROP_ABS` (20 €). A tiny price wiggle or a price *rise* never
emails, so **you're never spammed with the same offer**; a genuine price drop
does re-alert (that's good news). Sold-out configs are remembered so they don't
re-alert at the same price when they return. Entries unused for 30 days are
pruned. Delete `seen.json` to reset.

---

## Tests

Fully offline — they run against saved page fixtures in `tests/fixtures/` and
assert the brief's §3/§4 anchors + the analysis logic.

```bash
python3 tests/test_parse.py        # 13 parser tests
python3 tests/test_analyze.py      # 10 engine tests
# or, if you have pytest:  python3 -m pytest tests/ -q
```

A sample run's full report is in [`docs/sample_report.txt`](docs/sample_report.txt).
