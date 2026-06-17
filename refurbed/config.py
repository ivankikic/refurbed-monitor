"""
Tunables + watchlist for the refurbed.hr MacBook monitor.

Everything a human would want to change lives here. Env vars (SMTP_*, ALERT_TO,
TELEGRAM_*) are read in notify.py, not here.
"""
from __future__ import annotations

# --------------------------------------------------------------------------- #
# What counts as a "good deal"  (brief §1, §8)
# --------------------------------------------------------------------------- #
CEILING = 1100.0          # absolute-deal price ceiling, EUR (owner: max 1100)
GOOD_RAM = 16             # minimum "good enough" RAM (GB)
GOOD_STORAGE = 256        # minimum "good enough" storage (GB)

# Keyboard layouts the owner will accept — US or HR, whichever ends up cheaper.
# Offers with any other layout are filtered out (set to None/[] to disable).
# The crawler navigates the keyboard dropdown toward THESE layouts so it
# discovers the right variants; cheapest-of-the-allowed then wins downstream.
KEYBOARD_FILTER = ["US", "HR"]

# A discount this big (vs refurbed's own list price) is a "steal" worth flagging
# loudly regardless of absolute price — e.g. the M4 Air at 1000€ vs ~1730 list.
DREAM_DISCOUNT_PCT = 40.0

# Marginal-anomaly caps: a one-axis upgrade costing <= cap is "near free" gold.
MARGINAL_RAM_MAX = 40.0       # e.g. 16->24 GB for <= 40 EUR
MARGINAL_STORAGE_MAX = 60.0   # e.g. 256->512 GB for <= 60 EUR
MARGINAL_BATTERY_MAX = 35.0   # Optimalna -> Nova for <= 35 EUR

# Cheapest-path-to-spec targets: (ram, storage)
TARGET_SPECS = [(16, 256), (16, 512), (24, 512)]

# --------------------------------------------------------------------------- #
# Price baselines — "this config is cheaper than usual" detection
# --------------------------------------------------------------------------- #
# We record the cheapest available price per (model, RAM, storage) on every FULL
# run and keep a rolling window, so we learn the TYPICAL price of each config.
# An offer priced well below that typical is the gold (e.g. M4 Air 24/512 that's
# usually ~1350 € showing up at ~1000 €).
BASELINE_WINDOW = 200       # samples kept per config (throttled to ~1/hour → ~8 days)
BASELINE_MIN_SAMPLES = 4    # need this many before a baseline is trusted
BASELINE_SAMPLE_MIN_MINUTES = 50   # don't add >1 sample/config within this gap
                                   #   (decouples baseline horizon from scan rate)
BASELINE_TRIM_PCT = 15      # trim this % off each tail when computing "typical"
                            #   (a lingering steal / glitch can't drag the median)
UNDERPRICED_PCT = 12.0      # flag an offer >= this % below its config's typical

# Hard rule: Apple Silicon only. Intel Macs are excluded no matter how cheap.
REQUIRE_SILICON = True

# --------------------------------------------------------------------------- #
# Watchlist  (brief §2) — extend freely.
# slug = the part after /p/ in the product URL.
# --------------------------------------------------------------------------- #
BASE = "https://www.refurbed.hr"

WATCHLIST = [
    # Owner's sweet spot: M1/M2/M3 Air, 16GB, 256/512GB, US kb, <= 1100 €.
    "apple-macbook-air-m1-2020",
    "apple-macbook-air-m2-2022",
    "apple-macbook-air-m3-2024",
    "apple-macbook-air-m4-2025",
    # Pros kept for the occasional steal (a 32GB Pro under budget, big % off):
    "apple-macbook-pro-2021-m1-14",
    "apple-macbook-pro-2021-m1-16-2",
    "apple-macbook-pro-2024-m4-14",
]

# Apple-laptop category listing (server-rendered) — used by scan_category() to
# optionally discover newly-listed product slugs not yet in WATCHLIST.
CATEGORY_URL = f"{BASE}/c/prijenosna-racunala/?e96=Apple&page=1&sort_by=price"

# --------------------------------------------------------------------------- #
# Crawl behaviour / politeness  (brief §11)
# --------------------------------------------------------------------------- #
# Which dropdown axes to follow during the BFS crawl. Keyboard ("Raspored
# tipki") is deliberately omitted: it multiplies the matrix ~10x for little
# value (we want US/any layout, not every locale). Add it back if you want a
# full sweep — but mind the request count.
CRAWL_AXES = [
    "Odaberite izgled",   # condition
    "Kapacitet RAM-a",    # RAM
    "Pohrana",            # storage
    "Boja",               # colour
    "Odaberite bateriju",  # battery
    "Raspored tipki",     # keyboard — followed ONLY toward KEYBOARD_FILTER (US),
                          #   so we reach US variants without exploring every locale
]

# NOTE: the crawl is broad WITHIN US/HR (keyboard targeting only) and relevance
# (>=16 GB, >=256 GB) is enforced in the analysis filters — NOT by pruning the
# crawl graph. Pruning RAM/storage during the BFS proved fragile for models whose
# cheapest seeds are 8 GB (M1/M2 Air): it starved the 16 GB region. The broad
# crawl reliably reaches it. (crawl_neighbors still supports range args for
# experiments, but the production crawl doesn't pass them.)

# One scan does everything (see DEPLOY.md). Cap bounds the per-product fetches;
# 150 fully covers the US/HR matrix for the biggest models (M1 Air ≈ 149). If a
# run logs CAP HIT, raise this. ~half the fetches are non-US/HR pages reached en
# route (filtered out in analysis) — the price of robust coverage.
MAX_FETCHES_PER_PRODUCT = 150
LIGHT_MAX_FETCHES = 18         # only used by the optional `--mode light` quick check
REQUEST_DELAY = 1.0            # seconds between requests (jitter added)
REQUEST_JITTER = 0.6          # +/- random jitter on the delay
REQUEST_TIMEOUT = 25          # per-request timeout, seconds
MAX_RETRIES = 3               # on 429/5xx/connection errors
CACHE_TTL = 900               # reuse a cached page younger than this (seconds)

USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# Require both the base and the upgraded offer to be in stock before flagging a
# marginal anomaly (a near-free upgrade you can't buy is just noise).
ANOMALY_REQUIRE_AVAILABLE = True

# How many absolute deals to list in the email's "VRIJEDNO SPOMENA" section.
TOP_ABSOLUTE_DEALS = 8

# --------------------------------------------------------------------------- #
# Re-alert policy (so you're NEVER spammed with the same offer)
# --------------------------------------------------------------------------- #
# Dedup is per CONFIG (not per price). An already-seen config re-alerts ONLY if
# its price drops by at least one of these vs the last price we alerted on. A
# tiny price wiggle or a price increase never emails. A genuinely new config or
# new anomaly always emails.
REALERT_DROP_PCT = 3.0     # % price drop that counts as news
REALERT_DROP_ABS = 20.0    # …or an absolute € drop, whichever triggers first

# --------------------------------------------------------------------------- #
# Ranking + AI  (the "don't make me read 54 lines" part)
# --------------------------------------------------------------------------- #
# How many ranked TOP picks to headline in the email.
TOP_PICKS = 8

# Gemini does the final ranking + writes the Croatian email. It's OPTIONAL: if
# the key is missing or the API errors, we fall back to the deterministic
# value-score ranking and a plain email — the monitor never breaks because of AI.
GEMINI_MODEL = "gemini-2.5-flash"   # best price/quality for this; swap freely
GEMINI_API_KEY_ENV = "GEMINI_API_KEY"
GEMINI_TIMEOUT = 40
GEMINI_MAX_CANDIDATES = 40          # how many offers we hand the model to rank
