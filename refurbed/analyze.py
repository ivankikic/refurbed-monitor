"""
Analysis engine (brief §6) operating on a flat list of concrete Offer records.

Three questions, each run:
  1. absolute_deals   — cheapest available >= good-enough spec, under the ceiling.
  2. cheapest_path    — cheapest way to reach a target (ram, storage) across ALL
                        colours / conditions / sellers.
  3. marginal_anomalies — near-free one-axis upgrades (the gold): hold every axis
                        fixed except ONE, look for a step up that costs <= a cap.

Faithful to the brief's reference logic; the grouping/“cheapest at each level”
detail is spelled out below.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from . import config


@dataclass
class Offer:
    product: str                  # watchlist slug, e.g. 'apple-macbook-air-m4-2025'
    model: str                    # display name, e.g. 'MacBook Air 2025 13.6" M4'
    chip: Optional[str]           # 'M1'..'M4'  (None => Intel/unknown => excluded)
    chip_tier: str                # '', 'Pro', 'Max'
    ram: int
    storage: int
    color: str
    condition: str                # Croatian label: Dobar/Vrlo dobar/Odlično/Premium
    cond_rank: int                # 0..3
    battery: str                  # 'optimal' | 'new'
    keyboard: Optional[str]
    price: float
    list_price: Optional[float]
    available: bool
    url: str
    variant_id: Optional[str] = None
    offer_id: Optional[str] = None

    # convenience -----------------------------------------------------------
    @property
    def spec_label(self) -> str:
        batt = "Nova bat." if self.battery == "new" else "opt.bat."
        kb = f"/{self.keyboard}" if self.keyboard else ""
        return (f"{self.ram}/{self.storage} {self.color} {self.condition}"
                f" {batt}{kb}")

    @property
    def is_silicon(self) -> bool:
        return self.chip is not None

    @property
    def discount_pct(self) -> float:
        """Discount vs refurbed's own list price (the strike-through number)."""
        if self.list_price and self.list_price > self.price:
            return round((self.list_price - self.price) / self.list_price * 100, 1)
        return 0.0


def silicon_only(offers: list[Offer]) -> list[Offer]:
    if not config.REQUIRE_SILICON:
        return offers
    return [o for o in offers if o.is_silicon]


def keyboard_filtered(offers: list[Offer]) -> list[Offer]:
    """Keep only the layouts the owner accepts (config.KEYBOARD_FILTER).

    KEYBOARD_FILTER may be a string ("US") or a list (["US","HR"]). Cheapest of
    the allowed layouts wins naturally downstream (cheapest_path / top_picks).
    """
    kb = getattr(config, "KEYBOARD_FILTER", None)
    if not kb:
        return offers
    allowed = {kb.upper()} if isinstance(kb, str) else {k.upper() for k in kb}
    return [o for o in offers if (o.keyboard or "").upper() in allowed]


# Newer Apple-Silicon generation = nicer (small ranking nudge only).
_CHIP_GEN = {"M1": 1, "M2": 2, "M3": 3, "M4": 4}


def value_score(o: Offer) -> float:
    """Deterministic 'how good a buy is this' score (fallback ranking + AI seed).

    Driven mostly by what the owner cares about: big % off and a low absolute
    price, with small bonuses for spec / condition / newer chip.
    """
    score = o.discount_pct                                   # the bagatela signal
    if o.price <= config.CEILING:                            # cheaper vs budget
        score += (config.CEILING - o.price) / config.CEILING * 45
    score += (o.ram - config.GOOD_RAM) * 0.4                 # extra RAM
    # storage beyond ~512 GB barely matters to this buyer -> cap the bonus
    score += max(0, min(o.storage, 512) - config.GOOD_STORAGE) / 256 * 3
    score += max(o.cond_rank, 0) * 1.5                       # better condition
    score += _CHIP_GEN.get(o.chip or "", 0) * 2              # newer chip (mild)
    if o.battery == "new":
        score += 2
    return round(score, 2)


def _config_key(o: Offer) -> tuple:
    return (o.product, o.ram, o.storage, o.color, o.condition, o.battery, o.keyboard)


def collapse_by_config(offers: list[Offer]) -> list[Offer]:
    """Keep only the cheapest available listing per identical config (drop
    duplicate sellers of the exact same machine)."""
    best: dict = {}
    for o in offers:
        k = _config_key(o)
        if k not in best or o.price < best[k].price:
            best[k] = o
    return list(best.values())


def top_picks(offers: list[Offer], n: int | None = None) -> list[Offer]:
    """Best buyable offers, ranked. Candidates = available, US, silicon, within
    budget and >= good-enough spec, PLUS any in-budget 'dream discount' steal."""
    dream = getattr(config, "DREAM_DISCOUNT_PCT", 100)
    cands = [
        o for o in offers
        if o.available and o.price <= config.CEILING
        and o.ram >= config.GOOD_RAM             # hard floor: owner needs ≥16 GB
        # a "dream discount" can excuse low storage, but never low RAM
        and (o.storage >= config.GOOD_STORAGE or o.discount_pct >= dream)
    ]
    cands = collapse_by_config(cands)        # one listing per identical machine
    cands.sort(key=value_score, reverse=True)
    return cands[: (n or config.TOP_PICKS)]


# --------------------------------------------------------------------------- #
# 1. Absolute deals
# --------------------------------------------------------------------------- #
def absolute_deals(offers: list[Offer]) -> list[Offer]:
    ok = [
        o for o in offers
        if o.available
        and o.price <= config.CEILING
        and o.ram >= config.GOOD_RAM
        and o.storage >= config.GOOD_STORAGE
    ]
    return sorted(collapse_by_config(ok), key=lambda o: o.price)


# --------------------------------------------------------------------------- #
# 2. Cheapest path to a target spec
# --------------------------------------------------------------------------- #
def cheapest_path(offers: list[Offer], target_ram: int, target_storage: int) -> Optional[Offer]:
    ok = [
        o for o in offers
        if o.available and o.ram >= target_ram and o.storage >= target_storage
    ]
    return min(ok, key=lambda o: o.price) if ok else None


# --------------------------------------------------------------------------- #
# 3. Marginal anomalies — near-free one-axis upgrades
# --------------------------------------------------------------------------- #
@dataclass
class Anomaly:
    kind: str            # 'RAM' | 'POHRANA' | 'BATERIJA'
    product: str
    model: str
    base: Offer
    upgrade: Offer
    delta: float         # upgrade.price - base.price
    text: str            # human-readable one-liner
    signature: str       # for dedup


def _cheapest_per_level(group: list[Offer], level_key) -> dict:
    """Within a config family, keep the cheapest available offer per axis level."""
    best: dict = {}
    for o in group:
        lv = level_key(o)
        if lv not in best or o.price < best[lv].price:
            best[lv] = o
    return best


def _group(offers: list[Offer], key) -> dict:
    out: dict = {}
    for o in offers:
        out.setdefault(key(o), []).append(o)
    return out


def _avail_ok(base: Offer, up: Offer) -> bool:
    if not config.ANOMALY_REQUIRE_AVAILABLE:
        return up.available
    return base.available and up.available


def _money(x: float) -> str:
    sign = "+" if x >= 0 else "−"
    return f"{sign}{abs(x):.2f} €"


def marginal_anomalies(offers: list[Offer]) -> list[Anomaly]:
    out: list[Anomaly] = []

    # ----- RAM step (hold storage/colour/condition/battery/keyboard) ---------
    key = lambda o: (o.product, o.storage, o.color, o.cond_rank, o.battery, o.keyboard)
    for _, grp in _group(offers, key).items():
        best = _cheapest_per_level(grp, lambda o: o.ram)
        levels = sorted(best)
        for lo, hi in zip(levels, levels[1:]):
            b, u = best[lo], best[hi]
            if hi <= lo or not _avail_ok(b, u):
                continue
            d = u.price - b.price
            if d <= config.MARGINAL_RAM_MAX:
                out.append(Anomaly(
                    "RAM", u.product, u.model, b, u, d,
                    f"{u.model} {u.color}/{u.condition}: {lo}→{hi} GB RAM za "
                    f"{_money(d)}  ({b.price:.2f} → {u.price:.2f} €)",
                    f"ANOM|{u.product}|RAM|{u.color}|{u.cond_rank}|{u.battery}|"
                    f"{u.storage}|{lo}->{hi}|{u.price:.2f}",
                ))

    # ----- Storage step (hold ram/colour/condition/battery/keyboard) ---------
    key = lambda o: (o.product, o.ram, o.color, o.cond_rank, o.battery, o.keyboard)
    for _, grp in _group(offers, key).items():
        best = _cheapest_per_level(grp, lambda o: o.storage)
        levels = sorted(best)
        for lo, hi in zip(levels, levels[1:]):
            b, u = best[lo], best[hi]
            if hi <= lo or not _avail_ok(b, u):
                continue
            d = u.price - b.price
            if d <= config.MARGINAL_STORAGE_MAX:
                out.append(Anomaly(
                    "POHRANA", u.product, u.model, b, u, d,
                    f"{u.model} {u.color}/{u.condition}: {lo}→{hi} GB pohrane za "
                    f"{_money(d)}  ({b.price:.2f} → {u.price:.2f} €)",
                    f"ANOM|{u.product}|POH|{u.color}|{u.cond_rank}|{u.battery}|"
                    f"{u.ram}|{lo}->{hi}|{u.price:.2f}",
                ))

    # ----- Battery upgrade Optimalna -> Nova ---------------------------------
    key = lambda o: (o.product, o.ram, o.storage, o.color, o.cond_rank, o.keyboard)
    for _, grp in _group(offers, key).items():
        best = _cheapest_per_level(grp, lambda o: o.battery)
        if "optimal" in best and "new" in best:
            b, u = best["optimal"], best["new"]
            if _avail_ok(b, u):
                d = u.price - b.price
                if d <= config.MARGINAL_BATTERY_MAX:
                    out.append(Anomaly(
                        "BATERIJA", u.product, u.model, b, u, d,
                        f"{u.model} {u.ram}/{u.storage} {u.color}/{u.condition}: "
                        f"NOVA baterija za {_money(d)}  "
                        f"({b.price:.2f} → {u.price:.2f} €)",
                        f"ANOM|{u.product}|BAT|{u.color}|{u.cond_rank}|"
                        f"{u.ram}|{u.storage}|{u.price:.2f}",
                    ))

    # Best (smallest delta) first — the closest to "free" is the most exciting.
    out.sort(key=lambda a: a.delta)
    return out


# --------------------------------------------------------------------------- #
# Roll-up used by the email + console report
# --------------------------------------------------------------------------- #
@dataclass
class ProductSummary:
    product: str
    model: str
    config_count: int = 0
    worth_count: int = 0      # available, >= good spec, under ceiling
    anomaly_count: int = 0


@dataclass
class Report:
    offers: list[Offer]
    deals: list[Offer]
    paths: dict                       # (ram,storage) -> Offer | None
    anomalies: list[Anomaly]
    picks: list[Offer] = field(default_factory=list)   # ranked top buys
    summaries: list[ProductSummary] = field(default_factory=list)


def build_report(offers: list[Offer]) -> Report:
    offers = keyboard_filtered(silicon_only(offers))
    deals = absolute_deals(offers)
    paths = {spec: cheapest_path(offers, *spec) for spec in config.TARGET_SPECS}
    anomalies = marginal_anomalies(offers)
    picks = top_picks(offers)

    by_product: dict = {}
    for o in offers:
        by_product.setdefault(o.product, []).append(o)
    anom_by_product: dict = {}
    for a in anomalies:
        anom_by_product[a.product] = anom_by_product.get(a.product, 0) + 1

    summaries = []
    for product, grp in sorted(by_product.items()):
        worth = sum(
            1 for o in grp
            if o.available and o.price <= config.CEILING
            and o.ram >= config.GOOD_RAM and o.storage >= config.GOOD_STORAGE
        )
        summaries.append(ProductSummary(
            product=product,
            model=grp[0].model,
            config_count=len(grp),
            worth_count=worth,
            anomaly_count=anom_by_product.get(product, 0),
        ))

    return Report(offers, deals, paths, anomalies, picks, summaries)
