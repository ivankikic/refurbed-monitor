#!/usr/bin/env python3
"""
refurbed.hr Apple-Silicon MacBook deal monitor — entrypoint.

  python3 monitor.py                 # full run: crawl, analyse, email if news
  python3 monitor.py --dry-run       # print the report/email, never send
  python3 monitor.py --no-state      # don't read/write seen.json (everything "new")
  python3 monitor.py --no-cache      # ignore the on-disk page cache
  python3 monitor.py --products apple-macbook-air-m4-2025
  python3 monitor.py --max-fetches 30
  python3 monitor.py --offers-json out.json   # also dump raw offers for inspection

Designed to be run from cron a few times a day (see README / crontab).
"""
from __future__ import annotations

import argparse
import json
import sys

from refurbed import ai, analyze, config, crawl, notify


def parse_args(argv=None) -> argparse.Namespace:
    p = argparse.ArgumentParser(description="refurbed.hr MacBook deal monitor")
    p.add_argument("--dry-run", action="store_true",
                   help="print the alert but never send email/telegram")
    p.add_argument("--no-state", action="store_true",
                   help="ignore seen.json (treat everything as new, don't persist)")
    p.add_argument("--no-cache", action="store_true",
                   help="ignore the on-disk HTML cache (always hit the network)")
    p.add_argument("--products", default="",
                   help="comma-separated slugs to crawl (default: WATCHLIST)")
    p.add_argument("--mode", choices=["full", "light"], default="full",
                   help="full = deep crawl (anomalies); light = fast small crawl "
                        "to catch fast-vanishing steals")
    p.add_argument("--max-fetches", type=int, default=None,
                   help="override the per-product fetch cap")
    p.add_argument("--no-ai", action="store_true",
                   help="skip the Gemini ranking (use deterministic ranking)")
    p.add_argument("--offers-json", default="",
                   help="dump raw collected offers to this JSON file")
    p.add_argument("--quiet", action="store_true", help="less crawl chatter")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.mode == "light":
        config.MAX_FETCHES_PER_PRODUCT = config.LIGHT_MAX_FETCHES
    if args.max_fetches is not None:
        config.MAX_FETCHES_PER_PRODUCT = args.max_fetches

    slugs = [s.strip() for s in args.products.split(",") if s.strip()] or config.WATCHLIST
    verbose = not args.quiet

    kb = config.KEYBOARD_FILTER or "any"
    ai_on = (not args.no_ai) and ai.available()
    print(f"refurbed monitor — {notify.now_iso()} [{args.mode}]")
    print(f"products: {len(slugs)} | ceiling {config.CEILING:.0f}€ | "
          f"kb={kb} | silicon-only={config.REQUIRE_SILICON} | "
          f"cap={config.MAX_FETCHES_PER_PRODUCT} | AI={'on' if ai_on else 'off'}")

    # 1. crawl ------------------------------------------------------------- #
    offers = crawl.crawl_all(slugs, use_cache=not args.no_cache, verbose=verbose)
    if not offers:
        print("No offers collected — aborting (site change or network issue?).")
        return 2

    # 2. analyse (silicon filter applied inside build_report) --------------- #
    report = analyze.build_report(offers)
    excluded = len(offers) - len(report.offers)
    if excluded:
        kb = config.KEYBOARD_FILTER or "any"
        print(f"Excluded {excluded} configs (Intel or keyboard not in {kb}).")

    if args.offers_json:
        with open(args.offers_json, "w", encoding="utf-8") as fh:
            json.dump([o.__dict__ for o in report.offers], fh,
                      ensure_ascii=False, indent=1)
        print(f"Wrote {len(report.offers)} offers -> {args.offers_json}")

    # 3. dedup vs seen.json (config-based; re-alert only on a real price drop) -- #
    seen = {} if args.no_state else notify.load_seen()
    current = notify.current_state(report)
    alert_keys, new_seen = notify.compute_alerts(current, seen)

    # 3b. AI ranking + email composition (optional; falls back gracefully) ---- #
    ai_result = None if args.no_ai else ai.rank(report)

    ts = notify.now_iso()
    body = notify.render_text(report, alert_keys, ts, ai_result)
    subject = notify.subject_line(report, alert_keys, ai_result)

    print()
    print(body)
    print()

    # 4. alert if there is news -------------------------------------------- #
    if alert_keys:
        print(f">> {len(alert_keys)} NEW/cheaper: {subject}")
        if args.dry_run:
            print("   (--dry-run: not sending)")
        else:
            sent = notify.send_email(subject, body)
            notify.send_telegram(subject, body)
            if not sent:
                print("   (email not sent; state still updated so you won't be "
                      "re-spammed — use --no-state while testing)")
    else:
        print(">> Nothing new or cheaper — no email (this is the no-change case).")

    # 5. persist state ------------------------------------------------------ #
    if not args.no_state:
        notify.save_seen(new_seen)
        print(f"   state: {len(new_seen)} configs tracked in seen.json")

    return 0


if __name__ == "__main__":
    sys.exit(main())
