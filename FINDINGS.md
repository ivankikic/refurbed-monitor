# FINDINGS — how refurbed.hr exposes per-config prices, and which approach won

**TL;DR:** Approach **B (variant-URL enumeration over static HTTP)** is the
winner, but with a twist the brief didn't have yet: the variant URLs don't need
brute-forcing or a sitemap — **every variant page links to its 1-axis
neighbours via native `<select><option value="<variant URL>">`**, so we BFS the
matrix with plain `requests`. No browser, no API reverse-engineering, no
Playwright. Each fetched page yields one fully-specced concrete offer from an
embedded Google-Analytics dataLayer object.

---

## 1. What's in the static HTML (verified)

I fetched the product page, the "all offers" page, the category listing, and
several real variant URLs with `curl` + a desktop User-Agent, and dissected them.

| Thing the brief asked about | Result |
|---|---|
| `__NEXT_DATA__` blob | **Absent.** Not a Next.js-style SSR JSON app. |
| `<script type="application/json">` state | **Absent.** |
| JSON / GraphQL API in page | **None referenced** in static HTML. |
| `bulk`, `collect`, Trustpilot, Stripe XHR | Confirmed noise (web-push, GA, widgets) — ignored, as the brief said. |
| Per-config price on the **base** product page | Only the *default* selected config is priced in static HTML; the dropdown price matrix is client-side (confirmed). |
| Per-config price on a **variant URL** | **Present in static HTML** ✓ (this is the key the brief identified). |

So A0 in the *literal* `__NEXT_DATA__` sense fails — but the page **does** embed
the data we need, in two other places:

### 1a. JSON-LD `ProductGroup` (the crawl seed)
One `<script type="application/ld+json">` block holds a `ProductGroup` with a
`hasVariant` array. Each variant carries `name`, `color`, `size` (storage) and an
`offers` object with `url`, `price`, `priceCurrency`, `itemCondition`,
`availability`. It lists **6 entries = the cheapest available offer per
(colour × storage)**. It `variesBy` only colour + size — so it is a *summary*,
not the full matrix (no RAM / condition / battery / keyboard axes). Excellent as
a **seed set of variant URLs**; insufficient on its own.

### 1b. Google-Analytics `view_item` dataLayer (the per-config goldmine)
Every product/variant page embeds a GA ecommerce object:

```
"item_name":"Apple MacBook Air 2025 | 13.6\" | M4","item_id":26919,
"price":"1230.5","price2":"1070","currency":"EUR","item_brand":"Apple",
"item_variant":"16 GB | 512 GB SSD | 10-Core GPU | Midnight | US | Grade AA (308042)"
```

- `price2` = **the real price** (`price` is the list/strike-through price).
- `item_variant` = **the full spec** of the currently-selected config:
  `RAM | Storage | GPU | Colour | Keyboard | Grade (numericId)`.
- **Discriminator:** the main config's `item_id` is an **unquoted integer**
  (`"item_id":26919`); the "similar products" widgets use a **quoted** string
  (`"item_id":"9290"`). A regex requiring an unquoted int matches only the main
  config — clean and reliable.

Token order is **not stable across models**: M1 Pro variants lead with the chip
(`"M1 Pro | 16-Core GPU | 16 GB | 1 TB SSD | silver | DK | Grade A (...)"`), so
the parser classifies tokens by **semantics** (SSD→storage, GPU→ignore, bare
`N GB`→RAM, `M[1-4] Pro/Max`→chip→ignore, `Grade X (id)`→condition+id, leftover→
colour/keyboard) rather than by position.

### 1c. The dropdowns are server-rendered `<select><option>` → free crawl graph
The "custom dropdowns" the brief saw are progressively-enhanced **native
selects**. In static HTML each axis is a `<select>` whose `<option value>` is the
**variant URL you'd navigate to** if you changed that one axis:

```
[Kapacitet RAM-a]  16.0 GB (selected) -> .../308042aa/?offer=15384491
                   24.0 GB            -> .../308185aa/?offer=20138354
[Pohrana]          512 GB (selected)  -> .../308042aa/?offer=15384491
                   256 GB             -> .../308040aa/?offer=20223807
[Boja] / [Odaberite izgled] / [Odaberite bateriju] / [Raspored tipki] ...
```

**This is what makes pure-HTTP enumeration possible.** Seed from the JSON-LD
URLs, then BFS: fetch a page → emit one offer → enqueue its option-link
neighbours → repeat. The whole reachable matrix falls out of plain GETs.

> ⚠️ **Deltas are contextual (brief's warning, confirmed).** An option link
> points to the *cheapest representative* for that axis change, **not** a strict
> hold-everything-else neighbour. E.g. from `16/512/Midnight/US/Premium`,
> changing battery to "Optimalna" jumps to `307991` (`16/256/Starlight/UK`).
> That's why we **don't trust the on-page deltas**; we collect concrete offers
> and let the analysis engine compute true one-axis deltas by grouping on
> all-axes-except-one. More accurate, and it yields exact prices.

---

## 2. Availability — the trap

The literal text **`Gotovo rasprodano` appears even on in-stock pages** (it's an
"almost sold out" badge / a similar-items label). It is **not** a reliable
sold-out signal. Reliable signals:

- **In stock** → `data-test="in-stock"` / `data-test="in-stock-message"` present.
- **Sold out** → `Odabrani proizvodi su rasprodani, odaberite drugu opciju`,
  and no in-stock hook.

Sold-out configs are still recorded with `available: false` (useful to track when
they return), as the brief requested.

---

## 3. Battery + condition

- **Battery** (`optimal` | `new`) is **not** in `item_variant`. It's read from
  the *selected option* of the `Odaberite bateriju` `<select>` (Nova→`new`,
  Optimalna→`optimal`), with a `"battery_condition":"..."` GA hint as fallback.
- **Condition** comes from the `Grade` letter in `item_variant`, mapped to the
  Croatian labels (verified the ordering against the brief's §4 condition
  deltas): `C→Dobar(0) · B→Vrlo dobar(1) · A→Odlično(2) · AA→Premium(3)`. The
  variant-URL suffix matches the grade too (`…aa`=AA, `…c`=C).

---

## 4. Number format

`1.004,09 €` → `1004.09` (dot = thousands, comma = decimals). Storage uses
**`1 TB SSD` / `2 TB SSD`** for the big drives — parsed as 1000 / 2000 GB to
match refurbed's own `128/256/512/1000/2000` scale.

---

## 5. The winning pipeline

```
product page ──(JSON-LD ProductGroup)──► seed variant URLs (cheapest per colour×size)
     │
     └─ BFS over <select><option> neighbours (axes: condition, RAM, storage,
        colour, battery — keyboard deliberately skipped to bound the crawl)
              │  each fetched page ─► parse GA dataLayer ─► one concrete Offer
              ▼
        list[Offer] ──► analysis engine ──► email (dedup via seen.json)
```

- **One product = a handful → ~40 GETs**, gzip, ~1 req/s. No browser.
- Why not Playwright (C)? Unnecessary: static HTML already carries exact prices +
  the neighbour graph. Playwright would only be needed to read the client-side
  deltas, which we sidestep by computing deltas from concrete offers.
- Why not a brute-forced sitemap / SKU enumeration? Not needed and impolite — the
  `<option>` links give us the reachable set for free.

### Coverage / politeness trade-off (important)
The BFS is seeded from the **cheapest** offers and explores neighbours, so the
**cheap region (where deals live) is covered first**. `MAX_FETCHES_PER_PRODUCT`
(default 70) caps the request count. Large matrices (M1 Air, the Pros) hit the
cap; small ones (M4 Air) exhaust naturally (~17 configs). Consequences:

- A specific deep/expensive config can be missed in a single run — fine, it's
  usually over the ceiling anyway, and it'll surface in a later run.
- Some point-in-time §4 anchors aren't currently purchasable (e.g. the
  "24 GB for +5,09 €" was a **24/256** M4 Air; right now the 24 GB RAM option on
  the 256 page snaps to **24/512**, i.e. no 24/256 in stock). The crawler
  behaves correctly; the matrix just changed.
- The **keyboard axis is skipped** by default (it ~10×'s the matrix for little
  value). Re-enable via `CRAWL_AXES` in `config.py` for a full sweep.

---

## 6. Validation against the brief's §3/§4 anchors (live)

Run `python3 monitor.py --max-fetches 40 --dry-run --no-state` and the crawled
anchors line up:

| Anchor | Expected | Crawled |
|---|---|---|
| M1 Air 16/256 Dobar Optimalna US | ~754,74 € | **754.74 €** ✓ exact |
| M4 Air 16/256 Premium UK Starlight | 999 € | **999.00 €** ✓ exact |
| M1 Pro 14″ 16/512 Dobar | ~838–849 € | **849.00 €** ✓ |
| M1 Pro 16″ 16/512 | ~893 € | **871.60 €** ✓ (cheaper in stock) |
| M4 Pro 14″ 16/512 US | ~1.336 € | **1336.04 €** ✓ exact |
| Variant 308042 16/512 Midnight US (Nova) | 1.070,00 € | **1070.00 €** ✓ |
| Variant 308185 24/512 (sold out) | 1.004,09 €, sold | **1004.09 €, available=false** ✓ |
| Intel 32/512 @ 253 € | must be excluded | not in any Apple-Silicon page; chip filter = M1/M2/M4 only ✓ |

Bonus the engine surfaced: cheapest **24/512-or-better = 1001 €** — a **32 GB**
M1 Pro 14″ (cheapest-path-to-spec correctly returns a better-than-target deal).

---

## 7. Known quirks / future tweaks

- **M4 Pro display glass** ("standard glass" / "nano-texture glass") is folded
  into the `colour` field by refurbed's `item_variant`. Grouping stays correct
  (they're genuinely different-priced configs); the label is just verbose.
- The brotli streaming decoder in urllib3 is flaky on this host, so the fetcher
  advertises `Accept-Encoding: gzip, deflate` (server honours it).
- If refurbed ever drops the GA dataLayer or the native `<select>` options, the
  fallbacks are: JSON-LD per-variant `Offer` (price+availability, no full spec)
  and, last resort, Playwright driving the dropdowns (approach C).
