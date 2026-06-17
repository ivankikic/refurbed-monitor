"""
Price baselines — learn the TYPICAL price of each (model, RAM, storage) config
so we can flag offers that are unusually cheap ("ispod prosjeka") or an all-time
low. This is the signal the owner actually wants: e.g. an M4 Air 24/512 that
normally sits at ~1350 € showing up at ~1000 €.

State lives in baselines.json (committed back by the FULL workflow):
    { "<product>|<ram>/<storage>": {"samples":[..prices..], "min": x, "updated": iso} }

* Only FULL runs WRITE baselines (they crawl the whole matrix → clean samples).
* Both light and full READ them to annotate/rank offers.
"""
from __future__ import annotations

import json
import os
import statistics
from datetime import datetime, timezone
from typing import Optional

from . import config
from .analyze import Offer

BASELINE_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)),
                             "baselines.json")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def config_key(o: Offer) -> str:
    return f"{o.product}|{o.ram}/{o.storage}"


def load(path: str = BASELINE_PATH) -> dict:
    if os.path.exists(path):
        try:
            with open(path, encoding="utf-8") as fh:
                return json.load(fh)
        except (OSError, ValueError):
            return {}
    return {}


def save(baselines: dict, path: str = BASELINE_PATH) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(baselines, fh, ensure_ascii=False, indent=1, sort_keys=True)


def update(baselines: dict, offers: list[Offer]) -> dict:
    """Record the cheapest AVAILABLE price per config this run (one sample each)."""
    cheapest: dict[str, float] = {}
    for o in offers:
        if not o.available:
            continue
        k = config_key(o)
        if k not in cheapest or o.price < cheapest[k]:
            cheapest[k] = o.price
    now = _now()
    win = getattr(config, "BASELINE_WINDOW", 60)
    for k, price in cheapest.items():
        rec = baselines.setdefault(k, {"samples": [], "min": price, "updated": now})
        rec["samples"] = (rec.get("samples", []) + [round(price, 2)])[-win:]
        rec["min"] = min(rec.get("min", price), price)
        rec["updated"] = now
    return baselines


def stats(baselines: dict, key: str) -> Optional[dict]:
    """Trusted baseline for a config, or None if not enough history yet."""
    rec = baselines.get(key)
    if not rec:
        return None
    samples = rec.get("samples", [])
    if len(samples) < getattr(config, "BASELINE_MIN_SAMPLES", 4):
        return None
    return {
        "median": statistics.median(samples),
        "min": rec.get("min"),
        "n": len(samples),
    }


def annotate(offers: list[Offer], baselines: dict) -> None:
    """Attach baseline_median / vs_baseline_pct / all_time_low to each offer."""
    for o in offers:
        st = stats(baselines, config_key(o))
        if not st:
            continue
        o.baseline_median = st["median"]
        if st["median"] > 0:
            o.vs_baseline_pct = round((st["median"] - o.price) / st["median"] * 100, 1)
        # all-time low: at or below the cheapest we've ever recorded for this config
        if st["min"] is not None and o.price <= st["min"] + 0.01:
            o.all_time_low = True


def underpriced(offers: list[Offer]) -> list[Offer]:
    """Available offers priced well below their config's typical price, best %
    first. (annotate() must have run.)"""
    thr = getattr(config, "UNDERPRICED_PCT", 12.0)
    ok = [o for o in offers
          if o.available and o.baseline_median and o.vs_baseline_pct >= thr]
    ok.sort(key=lambda o: o.vs_baseline_pct, reverse=True)
    return ok
