"""
Price-baseline tests — typical-price learning, below-typical / all-time-low
flagging, trimmed typical, windowed min, and sample throttling.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from refurbed import baselines, config  # noqa: E402
from refurbed.analyze import Offer  # noqa: E402

config.BASELINE_SAMPLE_MIN_MINUTES = 0   # disable throttle for most tests
config.BASELINE_MIN_SAMPLES = 4


def mk(price, ram=24, storage=512, product="apple-macbook-air-m4-2025",
       cond="Premium", available=True):
    return Offer(product=product, model="MacBook Air 2025 M4", chip="M4",
                 chip_tier="", ram=ram, storage=storage, color="midnight",
                 condition=cond, cond_rank=3, battery="optimal", keyboard="US",
                 price=price, list_price=None, available=available,
                 url=f"http://x/{price}")


KEY = "apple-macbook-air-m4-2025|24/512"


def test_update_records_cheapest_sample():
    b = {}
    baselines.update(b, [mk(1400), mk(1350), mk(1500)])  # cheapest = 1350
    assert b[KEY]["samples"] == [1350.0]


def test_stats_needs_min_samples_then_median_and_windowed_min():
    b = {}
    for p in (1350, 1360, 1340):
        baselines.update(b, [mk(p)])
    assert baselines.stats(b, KEY) is None          # 3 < 4
    baselines.update(b, [mk(1355)])
    st = baselines.stats(b, KEY)
    assert st and 1340 <= st["median"] <= 1360 and st["min"] == 1340


def test_annotate_flags_steal_and_all_time_low():
    b = {}
    for p in (1350, 1360, 1340, 1355, 1345):
        baselines.update(b, [mk(p)])
    steal, normal = mk(1000), mk(1345)
    baselines.annotate([steal, normal], b)
    assert steal.vs_baseline_pct > 25 and steal.all_time_low is True
    assert normal.vs_baseline_pct < 5 and normal.all_time_low is False
    under = baselines.underpriced([steal, normal])
    assert steal in under and normal not in under


def test_trimmed_typical_ignores_a_glitch_low():
    # a single absurd low must not drag the "typical" down much
    samples = [100] + [1300, 1310, 1320, 1330, 1340, 1350, 1360]
    typ = baselines._trimmed_typical(samples)
    assert typ > 1250


def test_windowed_min_rolls_off_old_glitch():
    config.BASELINE_WINDOW = 4
    b = {}
    for p in (500, 1300, 1310, 1320, 1330):   # 500 glitch rolls out of window 4
        baselines.update(b, [mk(p)])
    st = baselines.stats(b, KEY)
    assert st["min"] >= 1300            # 500 no longer in the window
    config.BASELINE_WINDOW = 200


def test_sample_throttle():
    config.BASELINE_SAMPLE_MIN_MINUTES = 50
    b = {}
    baselines.update(b, [mk(1350)])
    baselines.update(b, [mk(1300)])     # too soon -> ignored
    assert b[KEY]["samples"] == [1350.0]
    config.BASELINE_SAMPLE_MIN_MINUTES = 0


def test_no_baseline_no_flag():
    o = mk(1000)
    baselines.annotate([o], {})
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
