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

from refurbed import analyze, config, crawl, notify


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
    p.add_argument("--max-fetches", type=int, default=None,
                   help="override MAX_FETCHES_PER_PRODUCT")
    p.add_argument("--offers-json", default="",
                   help="dump raw collected offers to this JSON file")
    p.add_argument("--quiet", action="store_true", help="less crawl chatter")
    return p.parse_args(argv)


def main(argv=None) -> int:
    args = parse_args(argv)
    if args.max_fetches is not None:
        config.MAX_FETCHES_PER_PRODUCT = args.max_fetches

    slugs = [s.strip() for s in args.products.split(",") if s.strip()] or config.WATCHLIST
    verbose = not args.quiet

    print(f"refurbed monitor — {notify.now_iso()}")
    print(f"products: {len(slugs)} | ceiling {config.CEILING:.0f}€ | "
          f"silicon-only={config.REQUIRE_SILICON} | max-fetches={config.MAX_FETCHES_PER_PRODUCT}")

    # 1. crawl ------------------------------------------------------------- #
    offers = crawl.crawl_all(slugs, use_cache=not args.no_cache, verbose=verbose)
    if not offers:
        print("No offers collected — aborting (site change or network issue?).")
        return 2

    # 2. analyse (silicon filter applied inside build_report) --------------- #
    report = analyze.build_report(offers)
    excluded = len(offers) - len(report.offers)
    if excluded:
        print(f"Excluded {excluded} non-Apple-Silicon (Intel) configs.")

    if args.offers_json:
        with open(args.offers_json, "w", encoding="utf-8") as fh:
            json.dump([o.__dict__ for o in report.offers], fh,
                      ensure_ascii=False, indent=1)
        print(f"Wrote {len(report.offers)} offers -> {args.offers_json}")

    # 3. dedup vs seen.json ------------------------------------------------- #
    seen = {} if args.no_state else notify.load_seen()
    sigs = notify.current_signatures(report)
    new_sigs = {s for s in sigs if s not in seen}

    ts = notify.now_iso()
    body = notify.render_text(report, new_sigs, ts)
    subject = notify.subject_line(report, new_sigs)

    print()
    print(body)
    print()

    # 4. alert if there is news -------------------------------------------- #
    if new_sigs:
        print(f">> {len(new_sigs)} NEW signal(s): {subject}")
        if args.dry_run:
            print("   (--dry-run: not sending)")
        else:
            sent = notify.send_email(subject, body)
            notify.send_telegram(subject, body)
            if not sent:
                print("   (email not sent; state still updated so you won't be "
                      "re-spammed — use --no-state while testing)")
    else:
        print(">> No new signals — no email sent (this is the no-change case).")

    # 5. persist state ------------------------------------------------------ #
    if not args.no_state:
        now = notify.now_iso()
        for s in sigs:
            seen[s] = now
        notify.save_seen(seen)
        print(f"   state: {len(seen)} signatures tracked in seen.json")

    return 0


if __name__ == "__main__":
    sys.exit(main())
