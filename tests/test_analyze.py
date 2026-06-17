"""
Analysis-engine tests. Synthetic offers reproduce the brief's real anomalies:
  * "24 GB for +5.09 €"           -> RAM anomaly
  * "512 GB for +30 €"            -> storage anomaly
  * "Nova battery for +20 €"      -> battery anomaly
  * cheapest-path-to-spec across colours/conditions
  * Intel machine must be excluded no matter how cheap
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from refurbed import analyze, config  # noqa: E402
from refurbed.analyze import Offer  # noqa: E402


def mk(product="m4air", model="MacBook Air M4", chip="M4", ram=16, storage=256,
       color="starlight", cond="Premium", rank=3, battery="optimal", kb="US",
       price=999.0, available=True, vid="x"):
    return Offer(product=product, model=model, chip=chip, chip_tier="", ram=ram,
                 storage=storage, color=color, condition=cond, cond_rank=rank,
                 battery=battery, keyboard=kb, price=price, list_price=None,
                 available=available, url=f"http://x/{vid}", variant_id=vid)


# --------------------------------------------------------------------------- #
def test_ram_anomaly_5eur():
    offers = [
        mk(ram=16, price=999.00, vid="a"),
        mk(ram=24, price=1004.09, vid="b"),   # +5.09 -> gold
    ]
    anoms = analyze.marginal_anomalies(offers)
    rams = [a for a in anoms if a.kind == "RAM"]
    assert len(rams) == 1
    assert abs(rams[0].delta - 5.09) < 0.001


def test_storage_anomaly_within_cap_but_not_over():
    base = dict(ram=16, color="silver")
    cheap = [mk(storage=256, price=900, vid="s1", **base),
             mk(storage=512, price=930, vid="s2", **base)]      # +30 -> flagged
    assert any(a.kind == "POHRANA" for a in analyze.marginal_anomalies(cheap))

    pricey = [mk(storage=256, price=900, vid="p1", **base),
              mk(storage=512, price=1100, vid="p2", **base)]    # +200 -> not flagged
    assert not any(a.kind == "POHRANA" for a in analyze.marginal_anomalies(pricey))


def test_battery_anomaly():
    base = dict(ram=16, storage=512, color="midnight")
    offers = [mk(battery="optimal", price=1000, vid="o", **base),
              mk(battery="new", price=1020, vid="n", **base)]   # +20 -> flagged
    anoms = [a for a in analyze.marginal_anomalies(offers) if a.kind == "BATERIJA"]
    assert len(anoms) == 1 and abs(anoms[0].delta - 20) < 0.001


def test_anomaly_requires_availability():
    offers = [mk(ram=16, price=999, vid="a"),
              mk(ram=24, price=1004, vid="b", available=False)]
    assert not analyze.marginal_anomalies(offers)  # upgrade sold out -> no flag


def test_cheapest_per_level_picks_cheapest_seller():
    # two 24GB offers; the cheaper one defines the step delta
    offers = [mk(ram=16, price=999, vid="a"),
              mk(ram=24, price=1200, vid="b1"),
              mk(ram=24, price=1004, vid="b2")]
    rams = [a for a in analyze.marginal_anomalies(offers) if a.kind == "RAM"]
    assert len(rams) == 1 and abs(rams[0].delta - 5) < 0.001


def test_negative_delta_is_an_anomaly():
    # 24GB cheaper than 16GB (different sellers) -> definitely flag
    offers = [mk(ram=16, price=1070, vid="a"),
              mk(ram=24, price=1004.09, vid="b")]
    rams = [a for a in analyze.marginal_anomalies(offers) if a.kind == "RAM"]
    assert len(rams) == 1 and rams[0].delta < 0


# --------------------------------------------------------------------------- #
def test_absolute_deals_filters_and_sorts():
    offers = [
        mk(ram=8, storage=256, price=500, vid="lowram"),      # too little RAM
        mk(ram=16, storage=128, price=500, vid="lowsto"),     # too little storage
        mk(ram=16, storage=256, price=1200, vid="overcap"),   # over ceiling
        mk(ram=16, storage=256, price=754.74, vid="ok1"),
        mk(ram=16, storage=512, price=838.0, vid="ok2"),
        mk(ram=24, storage=512, price=999.0, available=False, vid="sold"),
    ]
    deals = analyze.absolute_deals(offers)
    ids = [o.variant_id for o in deals]
    assert ids == ["ok1", "ok2"]                 # sorted by price, filtered


def test_cheapest_path_across_colours():
    offers = [
        mk(ram=16, storage=512, color="midnight", price=1207, vid="m"),
        mk(ram=16, storage=512, color="silver", price=1114, vid="s"),  # cheapest
        mk(ram=24, storage=512, color="silver", price=1300, vid="big"),
    ]
    o = analyze.cheapest_path(offers, 16, 512)
    assert o.variant_id == "s" and o.price == 1114
    # 24/512 path must accept the 24GB one only
    o2 = analyze.cheapest_path(offers, 24, 512)
    assert o2.variant_id == "big"


# --------------------------------------------------------------------------- #
def test_keyboard_filter_keeps_us_and_hr_only():
    offers = [
        mk(kb="US", price=700, vid="us"),
        mk(kb="HR", price=690, vid="hr"),
        mk(kb="DE", price=650, vid="de"),   # cheaper but wrong layout -> dropped
        mk(kb="UK", price=600, vid="uk"),   # dropped
    ]
    rep = analyze.build_report(offers)
    kbs = {o.keyboard for o in rep.offers}
    assert kbs == {"US", "HR"}
    # cheapest of the ALLOWED layouts wins the path (HR 690, not DE 650)
    assert rep.paths[(16, 256)].variant_id == "hr"


def test_discount_pct_and_steal_tier():
    o = mk(price=635.0, vid="steal")
    o.list_price = 1729.0
    assert round(o.discount_pct) == 63
    # a 63%-off in-budget machine should be the #1 pick
    rep = analyze.build_report([o, mk(price=900, vid="meh")])
    assert rep.picks[0].variant_id == "steal"


def test_best_per_machine_groups_and_keeps_best_variant():
    offers = [
        mk(ram=16, storage=512, cond="Vrlo dobar", rank=1, price=1035, vid="vd"),
        mk(ram=16, storage=512, cond="Premium", rank=3, price=1070, vid="prem"),  # better
        mk(ram=16, storage=256, cond="Premium", rank=3, price=1031, vid="small"),
    ]
    best = analyze.best_per_machine(offers)
    vids = sorted(o.variant_id for o in best)
    # 16/512 collapses to ONE (the Premium, higher value); 16/256 stays separate
    assert vids == ["prem", "small"]


def test_top_picks_one_per_machine_prefers_better_storage():
    offers = [
        mk(ram=16, storage=256, cond="Vrlo dobar", rank=1, price=1031, vid="s256"),
        mk(ram=16, storage=512, cond="Premium", rank=3, price=1070, vid="s512"),
    ]
    picks = analyze.top_picks(offers)
    assert picks[0].variant_id == "s512"   # better variant ranks first


def test_intel_excluded_even_if_cheap():
    offers = [
        mk(ram=32, storage=512, chip=None, price=253.0, model="MBP 13 Intel", vid="intel"),
        mk(ram=16, storage=256, chip="M1", price=754.74, vid="m1"),
    ]
    rep = analyze.build_report(offers)
    assert all(o.chip is not None for o in rep.offers)
    assert all("intel" != o.variant_id for o in rep.deals)
    assert len(rep.offers) == 1


def test_full_report_smoke():
    offers = [
        mk(ram=16, storage=256, price=999, vid="a"),
        mk(ram=24, storage=256, price=1004.09, vid="b"),
        mk(ram=16, storage=512, price=1029, color="silver", vid="c"),
        mk(ram=32, storage=512, chip=None, price=253, vid="intel"),
    ]
    rep = analyze.build_report(offers)
    assert len(rep.offers) == 3                       # Intel dropped
    assert rep.paths[(16, 256)] is not None
    assert any(a.kind == "RAM" for a in rep.anomalies)
    assert len(rep.summaries) >= 1


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
