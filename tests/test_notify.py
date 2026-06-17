"""
Dedup / re-alert tests — the "don't spam me with the same offer" guarantee.
A config alerts only when NEW or when its price drops meaningfully; tiny wiggles
and price rises must stay silent.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from refurbed import config, notify  # noqa: E402
from refurbed.analyze import Offer, Report  # noqa: E402


def _offer(price, ram=16, storage=512, vid="x"):
    return Offer(product="m4", model="M4 Air", chip="M4", chip_tier="", ram=ram,
                 storage=storage, color="midnight", condition="Premium",
                 cond_rank=3, battery="optimal", keyboard="US", price=price,
                 list_price=None, available=True, url=f"http://x/{vid}",
                 variant_id=vid)


def _seen(**kv):
    return {k: {"p": v, "t": notify.now_iso()} for k, v in kv.items()}


def _cur(**kv):
    return {k: {"price": v, "label": k} for k, v in kv.items()}


def test_new_key_alerts():
    alerts, _ = notify.compute_alerts(_cur(A=900.0), {})
    assert alerts == {"A"}


def test_same_price_no_alert():
    alerts, new = notify.compute_alerts(_cur(A=900.0), _seen(A=900.0))
    assert alerts == set()
    assert new["A"]["p"] == 900.0          # reference preserved


def test_tiny_wiggle_no_alert():
    # 900 -> 895 is < max(20€, 3%) -> NOT news
    alerts, _ = notify.compute_alerts(_cur(A=895.0), _seen(A=900.0))
    assert alerts == set()


def test_price_rise_no_alert():
    alerts, new = notify.compute_alerts(_cur(A=950.0), _seen(A=900.0))
    assert alerts == set()
    assert new["A"]["p"] == 900.0          # keep the lower reference


def test_real_price_drop_alerts():
    # 900 -> 850 = 50€ drop, well over the 20€/3% threshold -> news
    alerts, new = notify.compute_alerts(_cur(A=850.0), _seen(A=900.0))
    assert alerts == {"A"}
    assert new["A"]["p"] == 850.0          # reference updated to new low


def test_gradual_decline_eventually_triggers():
    # reference stays at last-alert price, so small steps accumulate to a trigger
    seen = _seen(A=1000.0)
    a1, seen = notify.compute_alerts(_cur(A=990.0), seen)   # -1% -> silent
    assert a1 == set() and seen["A"]["p"] == 1000.0
    a2, seen = notify.compute_alerts(_cur(A=969.0), seen)   # -31 from 1000 -> news
    assert a2 == {"A"}


def test_soldout_config_carried_over():
    # key in seen but not in this run -> kept (so its return at same price is quiet)
    alerts, new = notify.compute_alerts(_cur(B=500.0), _seen(A=900.0, B=500.0))
    assert "A" in new and alerts == set()


def test_save_seen_roundtrip(tmp_path=None):
    import tempfile, json
    p = os.path.join(tempfile.mkdtemp(), "seen.json")
    notify.save_seen({"A": {"p": 900.0, "t": notify.now_iso()},
                      "OLD": "legacy-string"}, p)   # legacy string dropped
    data = json.load(open(p))
    assert "A" in data and "OLD" not in data


def test_new_offers_filters_and_dedups():
    o1 = _offer(1070, vid="a")              # new
    o2 = _offer(1031, storage=256, vid="b")  # not new
    rep = Report(offers=[o1, o2], deals=[o1, o2], paths={}, anomalies=[],
                 picks=[o1, o2], underpriced=[])
    keys = {notify.offer_key(o1)}
    fresh = notify.new_offers(rep, keys)
    assert len(fresh) == 1 and fresh[0].variant_id == "a"


def test_append_history_writes_record():
    import json
    import tempfile
    o1 = _offer(1070, vid="a")
    rep = Report(offers=[o1], deals=[o1], paths={}, anomalies=[], picks=[o1],
                 underpriced=[])
    p = os.path.join(tempfile.mkdtemp(), "h.jsonl")
    notify.append_history(rep, {notify.offer_key(o1)}, "full", notify.now_iso(),
                          True, p)
    rec = json.loads(open(p).read().strip())
    assert rec["n_offers"] == 1 and rec["emitted"] is True and len(rec["new"]) == 1


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
