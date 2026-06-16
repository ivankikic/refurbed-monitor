"""
Polite HTTP fetcher + per-product BFS crawler.

Strategy (Approach B, see FINDINGS.md):
  1. Fetch the product page, read the JSON-LD ProductGroup for seed variant URLs.
  2. BFS: fetch each variant page, turn it into one concrete Offer, and enqueue
     its 1-axis neighbours (from the <select><option> links), skipping the
     keyboard axis. De-dup by variant-id+offer-id, cap fetches per product.

No browser. Plain `requests`. Caching + delay + retry keep us polite (brief §11).
"""
from __future__ import annotations

import os
import random
import re
import time
import urllib.parse as urlparse
from collections import deque
from typing import Optional

import requests

from . import config, parse
from .analyze import Offer

CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "cache")


# --------------------------------------------------------------------------- #
# Fetcher
# --------------------------------------------------------------------------- #
class Fetcher:
    def __init__(self, *, use_cache: bool = True, verbose: bool = True):
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": config.USER_AGENT,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "hr-HR,hr;q=0.9,en;q=0.6",
            # Deliberately omit 'br': the urllib3 brotli streaming decoder is
            # flaky on this host; gzip/deflate decode cleanly and the server
            # honours it.
            "Accept-Encoding": "gzip, deflate",
            "Connection": "keep-alive",
        })
        self.use_cache = use_cache
        self.verbose = verbose
        self.request_count = 0
        if use_cache:
            os.makedirs(CACHE_DIR, exist_ok=True)

    # -- caching ----------------------------------------------------------- #
    @staticmethod
    def _cache_path(url: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9]+", "_", url)[-180:]
        return os.path.join(CACHE_DIR, safe + ".html")

    def _cached(self, url: str) -> Optional[str]:
        if not self.use_cache:
            return None
        p = self._cache_path(url)
        if os.path.exists(p) and (time.time() - os.path.getmtime(p)) < config.CACHE_TTL:
            with open(p, encoding="utf-8") as fh:
                return fh.read()
        return None

    def _store(self, url: str, html: str) -> None:
        if not self.use_cache:
            return
        try:
            with open(self._cache_path(url), "w", encoding="utf-8") as fh:
                fh.write(html)
        except OSError:
            pass

    # -- fetch ------------------------------------------------------------- #
    def get(self, url: str) -> Optional[str]:
        cached = self._cached(url)
        if cached is not None:
            return cached

        for attempt in range(1, config.MAX_RETRIES + 1):
            try:
                # polite spacing between *network* hits only
                delay = config.REQUEST_DELAY + random.uniform(
                    -config.REQUEST_JITTER, config.REQUEST_JITTER
                )
                if self.request_count:
                    time.sleep(max(0.2, delay))
                self.request_count += 1
                resp = self.session.get(url, timeout=config.REQUEST_TIMEOUT)
                if resp.status_code == 200:
                    self._store(url, resp.text)
                    return resp.text
                if resp.status_code in (429, 500, 502, 503, 504):
                    wait = float(resp.headers.get("Retry-After", 2 ** attempt))
                    if self.verbose:
                        print(f"    [{resp.status_code}] backoff {wait:.0f}s {url[-60:]}")
                    time.sleep(min(wait, 30))
                    continue
                if self.verbose:
                    print(f"    [{resp.status_code}] giving up {url[-60:]}")
                return None
            except requests.RequestException as exc:
                if self.verbose:
                    print(f"    [err {attempt}/{config.MAX_RETRIES}] {exc} {url[-50:]}")
                time.sleep(2 ** attempt)
        return None


# --------------------------------------------------------------------------- #
# URL helpers
# --------------------------------------------------------------------------- #
def _ids_from_url(url: str) -> tuple[Optional[str], Optional[str]]:
    """Return (variant_id, offer_id) from a /p/<slug>/<variant>/?offer=<id> URL."""
    mvar = re.search(r"/p/[^/]+/([0-9a-z]+)/", url)
    q = urlparse.urlparse(url).query
    offer = urlparse.parse_qs(q).get("offer", [None])[0]
    return (mvar.group(1) if mvar else None), offer


def _dedup_key(vp: parse.VariantPage) -> tuple:
    """Identify an offer by its full config + price.

    Rationale: the bare product-root URL and its canonical variant URL are the
    SAME listing reached two ways (root has no ?offer=), so keying on the URL
    would double-count. Two listings with identical spec AND price are the same
    deal for our purposes; different-price sellers stay distinct (so the cheapest
    survives), and different conditions of the same numeric id (e.g. 308042
    Premium vs 308042 Dobar) stay distinct via cond_rank.
    """
    s = vp.spec
    return (s.ram, s.storage, s.color, s.cond_rank, vp.battery, s.keyboard,
            round(vp.price, 2), vp.available)


def _model_name(item_name: str) -> str:
    # 'Apple MacBook Air 2025 | 13.6" | M4' -> 'MacBook Air 2025 13.6" M4'
    name = item_name.replace("Apple ", "").replace(" | ", " ")
    return name.strip()


# --------------------------------------------------------------------------- #
# Per-product BFS crawl
# --------------------------------------------------------------------------- #
def crawl_product(slug: str, fetcher: Fetcher, *, verbose: bool = True) -> list[Offer]:
    base = config.BASE
    product_url = f"{base}/p/{slug}/"
    html = fetcher.get(product_url)
    if not html:
        if verbose:
            print(f"  ! could not fetch product page for {slug}")
        return []

    # Seed queue: JSON-LD variant URLs + the product page itself (its default
    # config is a real, fully-specced offer too).
    seeds = parse.parse_product_seeds(html)
    queue: deque[str] = deque()
    queued: set[str] = set()

    def enqueue(u: str) -> None:
        if u not in queued:
            queued.add(u)
            queue.append(u)

    enqueue(product_url)
    for s in seeds:
        enqueue(s.url)

    offers: dict[str, Offer] = {}
    fetches = 0

    # Process the already-fetched product page first (avoid re-fetch).
    pending_html = {product_url: html}

    while queue and fetches < config.MAX_FETCHES_PER_PRODUCT:
        url = queue.popleft()
        page_html = pending_html.pop(url, None) or fetcher.get(url)
        if url != product_url:
            fetches += 1
        if not page_html:
            continue

        vp = parse.parse_variant_page(page_html, base, config.CRAWL_AXES)
        if vp.found and vp.price is not None:
            var_id, offer_id = _ids_from_url(url)
            s = vp.spec
            if s.ram is not None and s.storage is not None:
                key = _dedup_key(vp)
                existing = offers.get(key)
                # Prefer the variant URL that pins a specific ?offer= (nicer link)
                if existing is None or (existing.offer_id is None and offer_id):
                    offers[key] = Offer(
                        product=slug,
                        model=_model_name(vp.item_name),
                        chip=vp.chip,
                        chip_tier=vp.chip_tier,
                        ram=s.ram,
                        storage=s.storage,
                        color=s.color,
                        condition=s.condition or "?",
                        cond_rank=s.cond_rank if s.cond_rank is not None else -1,
                        battery=vp.battery or "optimal",
                        keyboard=s.keyboard,
                        price=vp.price,
                        list_price=vp.list_price,
                        available=vp.available,
                        url=url,
                        variant_id=s.variant_id or var_id,
                        offer_id=offer_id,
                    )

        # enqueue 1-axis neighbours
        for n in vp.neighbors:
            enqueue(n)

    if verbose:
        avail = sum(1 for o in offers.values() if o.available)
        print(f"  {slug}: {fetches} fetches -> {len(offers)} configs "
              f"({avail} available)")
    return list(offers.values())


def crawl_all(slugs: list[str], *, use_cache: bool = True, verbose: bool = True) -> list[Offer]:
    fetcher = Fetcher(use_cache=use_cache, verbose=verbose)
    all_offers: list[Offer] = []
    for slug in slugs:
        if verbose:
            print(f"- crawling {slug} ...")
        all_offers.extend(crawl_product(slug, fetcher, verbose=verbose))
    if verbose:
        print(f"= total {len(all_offers)} offers across {len(slugs)} products "
              f"({fetcher.request_count} network requests)")
    return all_offers
