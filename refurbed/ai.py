"""
Optional AI layer (Google Gemini) — ranks the candidate offers and writes the
Croatian email so the owner reads 5 great picks instead of 54 raw lines.

Design rules:
  * STRICTLY optional. No key / any API error / bad JSON  -> return None and the
    caller falls back to the deterministic ranking. The monitor never breaks
    because of the AI.
  * The model RANKS + EXPLAINS; it never supplies numbers. Prices/specs/links in
    the email always come from our own data (candidate index -> our Offer), so
    the model can't hallucinate a price.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Optional

import requests

from . import config
from .analyze import Offer, Report

API_URL = ("https://generativelanguage.googleapis.com/v1beta/models/"
           "{model}:generateContent?key={key}")

_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "subject": {"type": "string"},
        "summary": {"type": "string"},
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "i": {"type": "integer"},
                    "tier": {"type": "string", "enum": ["STEAL", "GREAT", "GOOD"]},
                    "reason": {"type": "string"},
                },
                "required": ["i", "tier", "reason"],
            },
        },
    },
    "required": ["subject", "summary", "picks"],
}

_SYSTEM = """Ti si pomoćnik koji developeru iz Hrvatske pomaže naći NAJBOLJU ponudu \
za rabljeni MacBook na refurbed.hr.

Profil kupca (STROGO):
- budžet maksimalno {ceiling:.0f} €,
- minimalno {ram} GB RAM-a i {storage} GB pohrane,
- SAMO Apple Silicon (M1–M4), tipkovnica US ili HR,
- boja nebitna; voli velik popust (% off) i nisku apsolutnu cijenu.
- Posebno cijeni "bagatele": npr. stroj koji inače košta ~1700 € a sad je ~1000 €.

Dobit ćeš JSON listu kandidata (svaki ima indeks "i"). Rangiraj NAJBOLJE koje \
vrijedi kupiti ODMAH. Vrati JSON:
- "subject": kratki naslov maila na hrvatskom, s emojijem i brojem top ponuda,
- "summary": 1–2 rečenice na hrvatskom (što je danas najbolje i zašto),
- "picks": poredano od najbolje, max {n} komada, svaki {{"i": indeks, \
"tier": STEAL|GREAT|GOOD, "reason": jedna kratka hrvatska rečenica zašto}}.

Tieri (budi strog, ne napuhuj):
- STEAL = samo iznimno: popust ≳35% OD liste ILI dramatično niska cijena za spec
  (npr. M4 Air ~1000€ umjesto ~1730€). Ako je popust ~10-15%, to NIJE steal.
- GREAT = vrlo dobra ponuda (dobar omjer cijene i specifikacija, solidan popust).
- GOOD = u redu ponuda vrijedna spomena.

Pravila: koristi ISKLJUČIVO podatke iz kandidata (ne izmišljaj cijene). Ako nešto \
nije dobro, ne stavljaj ga u listu."""


@dataclass
class AIResult:
    subject: str
    summary: str
    picks: list          # list of (Offer, tier, reason)


def _api_key() -> Optional[str]:
    return os.environ.get(getattr(config, "GEMINI_API_KEY_ENV", "GEMINI_API_KEY"))


def available() -> bool:
    return bool(_api_key())


def _candidate_dict(o: Offer, i: int, tags: list[str]) -> dict:
    return {
        "i": i,
        "model": o.model,
        "chip": (o.chip or "") + ((" " + o.chip_tier) if o.chip_tier else ""),
        "ram_gb": o.ram,
        "storage_gb": o.storage,
        "color": o.color,
        "condition": o.condition,
        "battery": o.battery,
        "keyboard": o.keyboard,
        "price_eur": round(o.price, 2),
        "list_price_eur": round(o.list_price, 2) if o.list_price else None,
        "discount_pct": o.discount_pct,
        "tags": tags,
        "available": o.available,
    }


def build_candidates(report: Report) -> list[Offer]:
    """Union of buyable picks + in-budget anomaly upgrades + cheapest-path winners,
    deduped, ranked by our value score, capped. Index order == this list order."""
    seen: set = set()
    ordered: list[Offer] = []
    tags: dict[int, list[str]] = {}

    def add(o: Offer, tag: str):
        if o is None:
            return
        key = id(o)
        if key not in seen:
            seen.add(key)
            ordered.append(o)
            tags[id(o)] = [tag]
        elif tag not in tags[id(o)]:
            tags[id(o)].append(tag)

    for o in report.picks:
        add(o, "top-buy")
    for o in report.deals:
        if o.price <= config.CEILING:
            add(o, "deal")
    for spec, o in report.paths.items():
        if o is not None and o.price <= config.CEILING:
            add(o, f"najjeftiniji {spec[0]}/{spec[1]}")
    for a in report.anomalies:
        if a.upgrade.price <= config.CEILING:
            add(a.upgrade, f"anomalija:{a.kind.lower()} {a.delta:+.0f}€")

    ordered.sort(key=lambda o: o.discount_pct, reverse=True)
    ordered = ordered[: getattr(config, "GEMINI_MAX_CANDIDATES", 40)]
    # stash tags on the offers via a side dict the caller can read back by index
    build_candidates.last_tags = [tags.get(id(o), []) for o in ordered]  # type: ignore[attr-defined]
    return ordered


def rank(report: Report, *, verbose: bool = True) -> Optional[AIResult]:
    key = _api_key()
    if not key:
        if verbose:
            print("  [ai] GEMINI_API_KEY not set — using deterministic ranking.")
        return None

    candidates = build_candidates(report)
    if not candidates:
        return None
    tag_list = getattr(build_candidates, "last_tags", [[]] * len(candidates))
    payload_cands = [_candidate_dict(o, i, tag_list[i]) for i, o in enumerate(candidates)]

    sys_prompt = _SYSTEM.format(
        ceiling=config.CEILING, ram=config.GOOD_RAM, storage=config.GOOD_STORAGE,
        n=config.TOP_PICKS,
    )
    body = {
        "system_instruction": {"parts": [{"text": sys_prompt}]},
        "contents": [{"parts": [{"text": "KANDIDATI:\n" + json.dumps(
            payload_cands, ensure_ascii=False)}]}],
        "generationConfig": {
            "responseMimeType": "application/json",
            "responseSchema": _RESPONSE_SCHEMA,
            "temperature": 0.3,
            # Headroom for gemini-2.5 thinking tokens + the JSON. NOTE: do NOT
            # also set thinkingConfig.thinkingBudget=0 — combined with a
            # responseSchema it truncates the JSON mid-output.
            "maxOutputTokens": 8192,
        },
    }
    url = API_URL.format(model=config.GEMINI_MODEL, key=key)

    data = _post_with_retry(url, body, verbose=verbose)
    if data is None:
        return None
    try:
        parts = data["candidates"][0]["content"]["parts"]
        text = "".join(p.get("text", "") for p in parts)  # JSON may span parts
        parsed = _loads_tolerant(text)
        if parsed is None:
            raise ValueError("unparseable JSON")
        picks = []
        for p in parsed.get("picks", []):
            idx = p.get("i")
            if isinstance(idx, int) and 0 <= idx < len(candidates):
                picks.append((candidates[idx], p.get("tier", "GOOD"),
                              p.get("reason", "")))
        if not picks:
            return None
        if verbose:
            print(f"  [ai] Gemini ranked {len(picks)} picks.")
        return AIResult(parsed.get("subject", ""), parsed.get("summary", ""), picks)
    except (KeyError, IndexError, ValueError, TypeError) as exc:
        if verbose:
            print(f"  [ai] could not parse Gemini response ({exc}) — fallback.")
        return None


def _loads_tolerant(text: str):
    """json.loads, but salvage common model glitches (trailing commas, trailing
    junk, an unterminated tail)."""
    import re
    if not text:
        return None
    try:
        return json.loads(text)
    except ValueError:
        pass
    cleaned = re.sub(r",(\s*[}\]])", r"\1", text.strip())   # kill trailing commas
    try:
        return json.loads(cleaned)
    except ValueError:
        pass
    # last resort: take the largest prefix ending at a top-level closing brace
    depth, end = 0, -1
    for i, ch in enumerate(cleaned):
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                end = i + 1
    if end > 0:
        try:
            return json.loads(cleaned[:end])
        except ValueError:
            return None
    return None


def _post_with_retry(url: str, body: dict, *, verbose: bool) -> Optional[dict]:
    timeout = getattr(config, "GEMINI_TIMEOUT", 40)
    for attempt in range(1, 4):
        try:
            r = requests.post(url, json=body, timeout=timeout,
                              headers={"Content-Type": "application/json"})
            if r.status_code == 200 and r.content:
                return r.json()
            # transient: empty body / rate limit / server error
            if r.status_code in (404, 429, 500, 502, 503, 504) or not r.content:
                wait = float(r.headers.get("Retry-After", 2 ** attempt))
                if verbose:
                    print(f"  [ai] HTTP {r.status_code}, retry {attempt}/3 in {wait:.0f}s")
                time.sleep(min(wait, 20))
                continue
            if verbose:
                print(f"  [ai] HTTP {r.status_code}: {r.text[:160]} — fallback.")
            return None
        except requests.RequestException as exc:
            if verbose:
                print(f"  [ai] request error {attempt}/3: {exc}")
            time.sleep(2 ** attempt)
    return None
