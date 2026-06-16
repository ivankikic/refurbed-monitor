"""
Tunables + watchlist for the refurbed.hr MacBook monitor.

Everything a human would want to change lives here. Env vars (SMTP_*, ALERT_TO,
TELEGRAM_*) are read in notify.py, not here.
"""
from __future__ import annotations

# --------------------------------------------------------------------------- #
# What counts as a "good deal"  (brief §1, §8)
# --------------------------------------------------------------------------- #
CEILING = 1150.0          # absolute-deal price ceiling, EUR
GOOD_RAM = 16             # minimum "good enough" RAM (GB)
GOOD_STORAGE = 256        # minimum "good enough" storage (GB)

# Marginal-anomaly caps: a one-axis upgrade costing <= cap is "near free" gold.
MARGINAL_RAM_MAX = 40.0       # e.g. 16->24 GB for <= 40 EUR
MARGINAL_STORAGE_MAX = 60.0   # e.g. 256->512 GB for <= 60 EUR
MARGINAL_BATTERY_MAX = 35.0   # Optimalna -> Nova for <= 35 EUR

# Cheapest-path-to-spec targets: (ram, storage)
TARGET_SPECS = [(16, 256), (16, 512), (24, 512)]

# Hard rule: Apple Silicon only. Intel Macs are excluded no matter how cheap.
REQUIRE_SILICON = True

# --------------------------------------------------------------------------- #
# Watchlist  (brief §2) — extend freely.
# slug = the part after /p/ in the product URL.
# --------------------------------------------------------------------------- #
BASE = "https://www.refurbed.hr"

WATCHLIST = [
    "apple-macbook-air-m4-2025",
    "apple-macbook-air-m2-2022",
    "apple-macbook-air-m1-2020",
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
    # "Raspored tipki",   # keyboard — intentionally skipped (see note above)
]

MAX_FETCHES_PER_PRODUCT = 70   # hard cap on variant-page fetches per product/run
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
