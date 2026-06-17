"""
Price-baseline tests — learning the typical price and flagging below-typical /
all-time-low offers (the "M4 Air 24/512 usually ~1350, now ~1000" signal).
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from refurbed import baselines, config  # noqa: E402
from refurbed.analyze import Offer  # noqa: E402


def mk(price, ram=24, storage=512, product="apple-macbook-air-m4-2025",
       cond="Premium", available=True):
    return Offer(product=product, model="MacBook Air 2025 M4", chip="M4",
                 chip_tier="", ram=ram, storage=storage, color="midnight",
                 condition=cond, cond_rank=3, battery="optimal", keyboard="US",
                 price=price, list_price=None, available=available,
                 url=f"http://x/{price}")


def test_update_records_cheapest_per_config():
    b = {}
    baselines.update(b, [mk(1400), mk(1350), mk(1500)])  # cheapest = 1350
    key = "apple-macbook-air-m4-2025|24/512"
    assert b[key]["samples"] == [1350.0]
    assert b[key]["min"] == 1350.0


def test_stats_needs_min_samples():
    b = {}
    for p in (1350, 1360, 1340):           # only 3 < MIN_SAMPLES(4)
        baselines.update(b, [mk(p)])
    assert baselines.stats(b, "apple-macbook-air-m4-2025|24/512") is None
    baselines.update(b, [mk(1355)])        # now 4
    st = baselines.stats(b, "apple-macbook-air-m4-2025|24/512")
    assert st and 1340 <= st["median"] <= 1360 and st["min"] == 1340


def test_annotate_and_underpriced_flags_the_steal():
    b = {}
    for p in (1350, 1360, 1340, 1355, 1345):   # typical ~1350
        baselines.update(b, [mk(p)])
    steal = mk(1000)                           # the ludost
    normal = mk(1345)
    offers = [steal, normal]
    baselines.annotate(offers, b)
    assert steal.baseline_median and steal.vs_baseline_pct > 25   # ~26% below
    assert steal.all_time_low is True          # 1000 < previous min 1340
    assert normal.vs_baseline_pct < 5
    under = baselines.underpriced(offers)
    assert steal in under and normal not in under


def test_no_baseline_no_flag():
    b = {}
    o = mk(1000)
    baselines.annotate([o], b)                 # no history -> nothing flagged
    assert o.vs_baseline_pct == 0 and not o.all_time_low
    assert baselines.underpriced([o]) == []


def _run_plain():
    fns = [v for k, v in sorted(globals().items())
           if k.startswith("test_") and callable(v)]
    failed = 0
    for fn in fns:
        try:
            fn()
            print(f"  ok  {fn.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"  FAIL {fn.__name__}: {e}")
    print(f"\n{len(fns) - failed}/{len(fns)} passed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(_run_plain())
