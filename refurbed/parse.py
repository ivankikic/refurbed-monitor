"""
HTML / embedded-JSON parsers for refurbed.hr product + variant pages.

Investigation summary (see FINDINGS.md for the long version):

* There is NO `__NEXT_DATA__` blob and NO JSON/GraphQL API in the static HTML.
* The product page DOES embed two useful things:
    1. A JSON-LD `ProductGroup` (script type="application/ld+json") with a
       `hasVariant` list = the cheapest available offer per (colour, storage).
       Great as a crawl SEED.
    2. A Google-Analytics "view_item" dataLayer object whose `item_variant`
       string is the FULL spec of the currently-selected config, with `price2`
       = the real price. This is on EVERY variant page too.
* Each individual config is its own server-rendered URL
  `/p/<slug>/<variantId>/?offer=<offerId>`. Fetching it returns that config's
  exact price + availability in static HTML (no browser needed).
* The dropdowns are server-rendered as native `<select><option value="<variant
  URL>">`, so every page links to its 1-axis neighbours -> we can BFS the whole
  matrix over plain HTTP.

So: one fetched variant page == one fully-specced concrete Offer, plus the links
to crawl onward.
"""
from __future__ import annotations

import html as ihtml
import json
import re
from dataclasses import dataclass, field
from typing import Iterable, Optional

# --------------------------------------------------------------------------- #
# Croatian number format:  "1.004,09 €"  -> 1004.09   (dot=thousands, comma=dec)
# --------------------------------------------------------------------------- #
def parse_price_hr(text: str) -> Optional[float]:
    """Parse a Croatian-formatted price string into a float."""
    if text is None:
        return None
    s = text.replace(" ", " ").strip()
    # Prefer a value that has a decimal comma (e.g. 1.004,09 or 999,00).
    m = re.search(r"(\d{1,3}(?:\.\d{3})*|\d+),(\d{2})\b", s)
    if m:
        whole = m.group(1).replace(".", "")
        return float(f"{whole}.{m.group(2)}")
    # Fallback: integer with thousands dots (e.g. "1.004 €").
    m = re.search(r"(\d{1,3}(?:\.\d{3})+|\d+)", s)
    if m:
        return float(m.group(1).replace(".", ""))
    return None


def _clean(text: str) -> str:
    """Strip tags + collapse whitespace + unescape entities."""
    return re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", ihtml.unescape(text))).strip()


def _json_unescape(s: str) -> str:
    r"""Decode JSON string escapes captured from a dataLayer blob (e.g. 13.6\" )."""
    if "\\" not in s:
        return s
    try:
        return json.loads('"' + s + '"')
    except ValueError:
        return s.replace('\\"', '"').replace("\\/", "/").replace("\\\\", "\\")


# --------------------------------------------------------------------------- #
# Colour + condition + keyboard normalisation
# --------------------------------------------------------------------------- #
_COLOR_MAP = {
    "midnight": "midnight", "ponoćni": "midnight", "ponocni": "midnight",
    "starlight": "starlight", "polarna zvijezda": "starlight",
    "sky blue": "skyblue", "nebesko plava": "skyblue", "nebeskoplav": "skyblue",
    "nebeskoplava": "skyblue",
    "silver": "silver", "srebrna": "silver",
    "space grey": "spacegrey", "space gray": "spacegrey",
    "svemirsko sivo": "spacegrey", "svemirska siva": "spacegrey",
    "svemirsko sivi": "spacegrey",
    "gold": "gold", "zlatna": "gold",
}


def normalize_color(raw: str) -> str:
    if not raw:
        return "?"
    key = raw.strip().lower()
    return _COLOR_MAP.get(key, key)


# refurbed grade letter (from the item_variant "Grade XX") -> Croatian condition
# label + an ordinal rank (worst -> best) for anomaly comparisons.
_GRADE_TO_CONDITION = {
    "C": ("Dobar", 0),
    "B": ("Vrlo dobar", 1),
    "A": ("Odlično", 2),
    "AA": ("Premium", 3),
}
# Croatian condition label -> rank (used when we only have the dropdown label).
CONDITION_RANK = {"Dobar": 0, "Vrlo dobar": 1, "Odlično": 2, "Premium": 3}

# Keyboard layout codes we recognise inside item_variant (incl. Croatian "SAD").
_KEYBOARD_CODES = {
    "US", "UK", "DE", "IT", "FR", "ES", "SE", "FI", "DK", "NL", "NO", "BE",
    "PL", "SI", "CH", "PT", "HR", "CZ", "HU", "IE", "AT", "SK", "SAD", "GB",
}
_KEYBOARD_ALIAS = {"SAD": "US", "GB": "UK"}


def _looks_like_keyboard(token: str) -> bool:
    t = token.strip()
    return t in _KEYBOARD_CODES or bool(re.fullmatch(r"[A-Z]{2,3}", t))


# --------------------------------------------------------------------------- #
# item_variant string parsing
#   "16 GB | 512 GB SSD | 10-Core GPU | Midnight | US | Grade AA (308185)"
#   "8 GB | 128 GB SSD | 7-jezgrenog GPU-a | Space Grey | SAD | Grade C (112314)"
# --------------------------------------------------------------------------- #
@dataclass
class Spec:
    ram: Optional[int] = None
    storage: Optional[int] = None
    color: str = "?"
    keyboard: Optional[str] = None
    condition: Optional[str] = None
    cond_rank: Optional[int] = None
    grade: Optional[str] = None
    variant_id: Optional[str] = None
    raw: str = ""


def parse_item_variant(s: str) -> Spec:
    spec = Spec(raw=s or "")
    if not s:
        return spec
    tokens = [t.strip() for t in s.split("|") if t.strip()]
    leftover: list[str] = []
    for tok in tokens:
        low = tok.lower()
        mgrade = re.search(r"Grade\s+([A-Z]+)\s*\((\d+)\)", tok)
        if mgrade:
            spec.grade = mgrade.group(1)
            spec.variant_id = mgrade.group(2)
            cond = _GRADE_TO_CONDITION.get(spec.grade)
            if cond:
                spec.condition, spec.cond_rank = cond
            continue
        # Chip token (some variants lead with "M1 Pro" / "M2" etc.) -> skip so it
        # doesn't pollute the colour. Tokens are NOT positional across models.
        if re.fullmatch(r"M[1-4]([ -]?(Pro|Max))?", tok, re.I):
            continue
        if "ssd" in low:                       # storage token, e.g. "512 GB SSD"/"1 TB SSD"
            mnum = re.search(r"([\d.]+)\s*(TB|GB)", tok, re.I)
            if mnum:
                val = float(mnum.group(1).replace(".", ""))  # HR thousands dot
                if mnum.group(2).upper() == "TB":
                    val *= 1000
                spec.storage = int(val)
            continue
        if "gpu" in low or "jezgr" in low or "core" in low:  # GPU token -> ignore
            continue
        if re.fullmatch(r"\d+\s*GB", tok):     # bare "N GB" -> RAM
            spec.ram = int(re.match(r"(\d+)", tok).group(1))
            continue
        leftover.append(tok)
    # leftover is [colour] or [colour, keyboard] (colour may be multi-word).
    if leftover and _looks_like_keyboard(leftover[-1]):
        kb = leftover[-1].upper()
        spec.keyboard = _KEYBOARD_ALIAS.get(kb, kb)
        leftover = leftover[:-1]
    if leftover:
        spec.color = normalize_color(" ".join(leftover))
    return spec


# --------------------------------------------------------------------------- #
# GA "view_item" dataLayer detail object  (the main, currently-selected config)
#   Discriminator: the main object has  "item_id":<int>  (UNQUOTED), whereas the
#   similar-products widgets use  "item_id":"<int>"  (quoted). So a regex that
#   requires an unquoted integer matches ONLY the main config.
# --------------------------------------------------------------------------- #
_GA_DETAIL_RE = re.compile(
    r'"item_name":"(?P<name>(?:[^"\\]|\\.)*)",'
    r'"item_id":(?P<item_id>\d+),'
    r'"price":"(?P<list>[\d.]+)",'
    r'"price2":"(?P<price>[\d.]+)",'
    r'"currency":"EUR",'
    r'"item_brand":"(?P<brand>[^"]*)",'
    r'"item_variant":"(?P<variant>(?:[^"\\]|\\.)*)"'
)


@dataclass
class VariantPage:
    """Everything we extract from a single fetched variant/product page."""
    item_name: str = ""          # e.g. 'Apple MacBook Air 2025 | 13.6" | M4'
    chip: Optional[str] = None    # M1 / M2 / M3 / M4 (None => Intel/unknown)
    chip_tier: str = ""           # '', 'Pro', 'Max'
    price: Optional[float] = None
    list_price: Optional[float] = None
    spec: Spec = field(default_factory=Spec)
    battery: Optional[str] = None  # 'optimal' | 'new'
    available: bool = False
    offer_id: Optional[str] = None
    instance_id: Optional[str] = None
    neighbors: list[str] = field(default_factory=list)  # variant URLs to crawl
    found: bool = False           # did we manage to parse the GA detail object?


def _parse_chip(item_name: str) -> tuple[Optional[str], str]:
    """Return (chip, tier) e.g. ('M4', 'Pro'); ('M1',''); (None,'') for Intel."""
    m = re.search(r"\bM([1-4])\b(?:\s*(Pro|Max))?", item_name)
    if not m:
        return None, ""
    return f"M{m.group(1)}", (m.group(2) or "")


def parse_availability(html: str) -> bool:
    """In stock iff the page renders the in-stock test hook.

    NOTE: the literal text 'Gotovo rasprodano' appears even on in-stock pages
    (it's an 'almost sold out' badge / similar-items label) — it is NOT a
    reliable sold-out signal. The reliable signals are:
      in stock : data-test="in-stock" / "in-stock-message"
      sold out : 'Odabrani proizvodi su rasprodani, odaberite drugu opciju'
    """
    if 'data-test="in-stock' in html:
        return True
    if "Odabrani proizvodi su rasprodani" in html:
        return False
    # Default conservative: treat as sold-out if we can't see the in-stock hook.
    return False


_AXIS_NAME_RE = {  # axis label -> the <select>'s purpose
    "Odaberite izgled": "condition",
    "Kapacitet RAM-a": "ram",
    "Pohrana": "storage",
    "Boja": "color",
    "Raspored tipki": "keyboard",
    "Odaberite bateriju": "battery",
}


def _iter_select_blocks(html: str) -> Iterable[tuple[str, str]]:
    """Yield (axis_label, inner_html) for each known axis <select> on the page."""
    for label in _AXIS_NAME_RE:
        idx = html.find(label)
        if idx < 0:
            continue
        seg = html[idx: idx + 6000]
        msel = re.search(r"<select\b[^>]*>(.*?)</select>", seg, re.S)
        if msel:
            yield label, msel.group(1)


def _options(inner: str) -> list[tuple[str, str, bool]]:
    """Return list of (label, value, selected) for a <select> inner-HTML."""
    out = []
    for attrs, label in re.findall(r"<option\b([^>]*)>(.*?)</option>", inner, re.S):
        mval = re.search(r'value="([^"]*)"', attrs)
        out.append((_clean(label), mval.group(1) if mval else "", "selected" in attrs))
    return out


def selected_battery(html: str) -> Optional[str]:
    """Battery from the selected option of the 'Odaberite bateriju' select."""
    for label, inner in _iter_select_blocks(html):
        if _AXIS_NAME_RE[label] != "battery":
            continue
        for opt_label, _val, sel in _options(inner):
            if sel:
                low = opt_label.lower()
                if "nova" in low:
                    return "new"
                if "optimal" in low:
                    return "optimal"
    # Fallback: a battery_condition hint in the main GA payload.
    m = re.search(r'"battery_condition":"(\w+)"', html)
    return m.group(1) if m else None


def crawl_neighbors(html: str, axes: Iterable[str], base: str) -> list[str]:
    """Absolute variant URLs reachable by changing one of `axes` from this page."""
    wanted = set(axes)
    urls: list[str] = []
    for label, inner in _iter_select_blocks(html):
        if label not in wanted:
            continue
        for _opt_label, val, sel in _options(inner):
            if sel or not val:
                continue
            urls.append(val if val.startswith("http") else base + val)
    # de-dup, preserve order
    seen: set[str] = set()
    out = []
    for u in urls:
        if u not in seen:
            seen.add(u)
            out.append(u)
    return out


def parse_variant_page(html: str, base: str, crawl_axes: Iterable[str]) -> VariantPage:
    """Parse a fetched variant (or product) page into a VariantPage."""
    vp = VariantPage()
    m = _GA_DETAIL_RE.search(html)
    if m:
        vp.found = True
        vp.item_name = _clean(_json_unescape(m.group("name")))
        vp.list_price = float(m.group("list"))
        vp.price = float(m.group("price"))
        vp.spec = parse_item_variant(_json_unescape(m.group("variant")))
        vp.chip, vp.chip_tier = _parse_chip(vp.item_name)
    vp.available = parse_availability(html)
    vp.battery = selected_battery(html)
    minst = re.search(r'data-instance-id="(\d+)"', html)
    if minst:
        vp.instance_id = minst.group(1)
    vp.neighbors = crawl_neighbors(html, crawl_axes, base)
    return vp


# --------------------------------------------------------------------------- #
# JSON-LD ProductGroup -> crawl seed (cheapest offer per colour x storage)
# --------------------------------------------------------------------------- #
@dataclass
class SeedOffer:
    url: str
    price: Optional[float]
    available: bool
    color: str = "?"
    storage: Optional[int] = None


def _iter_jsonld(html: str) -> Iterable[object]:
    for block in re.findall(
        r'<script type="application/ld\+json"[^>]*>(.*?)</script>', html, re.S
    ):
        try:
            yield json.loads(block.strip())
        except Exception:
            continue


def parse_product_seeds(html: str) -> list[SeedOffer]:
    """Variant-URL seeds from the product page's JSON-LD ProductGroup."""
    seeds: list[SeedOffer] = []
    for doc in _iter_jsonld(html):
        items = doc if isinstance(doc, list) else [doc]
        for el in items:
            if not isinstance(el, dict) or el.get("@type") != "ProductGroup":
                continue
            for var in el.get("hasVariant", []):
                off = var.get("offers") or {}
                url = off.get("url")
                if not url:
                    continue
                size = var.get("size") or ""
                mnum = re.search(r"(\d+)", str(size))
                seeds.append(
                    SeedOffer(
                        url=url,
                        price=off.get("price"),
                        available="InStock" in str(off.get("availability", "")),
                        color=normalize_color(var.get("color", "?")),
                        storage=int(mnum.group(1)) if mnum else None,
                    )
                )
    # de-dup by URL
    seen: set[str] = set()
    out = []
    for s in seeds:
        if s.url not in seen:
            seen.add(s.url)
            out.append(s)
    return out


# --------------------------------------------------------------------------- #
# Category listing (server-rendered) -> discover product slugs / from-prices
# --------------------------------------------------------------------------- #
def parse_category_slugs(html: str) -> list[str]:
    """Product slugs (the /p/<slug>/ part) linked from the category listing."""
    slugs = re.findall(r'/p/(apple-macbook[a-z0-9-]+)/', html)
    seen: set[str] = set()
    out = []
    for s in slugs:
        # strip any trailing variant-id segment if present
        s = s.split("/")[0]
        if s not in seen:
            seen.add(s)
            out.append(s)
    return out
