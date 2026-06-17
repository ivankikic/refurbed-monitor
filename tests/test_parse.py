"""
Parser tests — run fully offline against saved refurbed.hr fixtures.
Anchors come from the brief's §3/§4 known-good data points.

    python3 -m pytest tests/ -q          # if pytest installed
    python3 tests/test_parse.py          # plain-stdlib fallback runner
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from refurbed import config, parse  # noqa: E402

FIX = os.path.join(os.path.dirname(os.path.abspath(__file__)), "fixtures")


def _read(name):
    with open(os.path.join(FIX, name), encoding="utf-8") as fh:
        return fh.read()


def _vp(name):
    return parse.parse_variant_page(_read(name), config.BASE, config.CRAWL_AXES)


# --------------------------------------------------------------------------- #
# Croatian price format
# --------------------------------------------------------------------------- #
def test_price_hr():
    assert parse.parse_price_hr("1.004,09 €") == 1004.09
    assert parse.parse_price_hr("999,00 €") == 999.0
    assert parse.parse_price_hr("507,00 €") == 507.0
    assert parse.parse_price_hr("1.070,00 €") == 1070.0
    assert parse.parse_price_hr("253,00 €") == 253.0


# --------------------------------------------------------------------------- #
# item_variant spec parsing (incl. Croatian-language variants)
# --------------------------------------------------------------------------- #
def test_item_variant_english():
    s = parse.parse_item_variant("16 GB | 512 GB SSD | 10-Core GPU | Midnight | US | Grade AA (308042)")
    assert (s.ram, s.storage, s.color, s.keyboard) == (16, 512, "midnight", "US")
    assert s.condition == "Premium" and s.cond_rank == 3 and s.variant_id == "308042"


def test_item_variant_croatian():
    s = parse.parse_item_variant("8 GB | 128 GB SSD | 7-jezgrenog GPU-a | Space Grey | SAD | Grade C (112314)")
    assert (s.ram, s.storage, s.color, s.keyboard) == (8, 128, "spacegrey", "US")
    assert s.condition == "Dobar" and s.cond_rank == 0


def test_item_variant_m1pro_chip_lead_and_tb():
    # M1 Pro variants lead with the chip token and use "1 TB SSD" — neither must
    # leak into the colour, and TB must become 1000 GB.
    s = parse.parse_item_variant(
        "M1 Pro | 16-Core GPU | 16 GB | 1 TB SSD | silver | DK | Grade A (61524)")
    assert s.ram == 16 and s.storage == 1000
    assert s.color == "silver" and s.keyboard == "DK"
    assert "m1" not in s.color and "pro" not in s.color


def test_item_variant_2tb():
    s = parse.parse_item_variant(
        "32 GB | 2 TB SSD | 32-Core GPU | space gray | US | Grade AA (1)")
    assert s.ram == 32 and s.storage == 2000 and s.color == "spacegrey"


def test_grade_to_condition_order():
    assert parse.parse_item_variant("16 GB | 256 GB SSD | GPU | Silver | DE | Grade C (1)").cond_rank == 0
    assert parse.parse_item_variant("16 GB | 256 GB SSD | GPU | Silver | DE | Grade B (1)").cond_rank == 1
    assert parse.parse_item_variant("16 GB | 256 GB SSD | GPU | Silver | DE | Grade A (1)").cond_rank == 2
    assert parse.parse_item_variant("16 GB | 256 GB SSD | GPU | Silver | DE | Grade AA (1)").cond_rank == 3


# --------------------------------------------------------------------------- #
# Full variant pages -> exact §3/§4 anchors
# --------------------------------------------------------------------------- #
def test_variant_307991_999():
    vp = _vp("m4air_product.html")  # default selected config
    assert vp.price == 999.0 and vp.chip == "M4"
    assert (vp.spec.ram, vp.spec.storage) == (16, 256)
    assert vp.spec.color == "starlight" and vp.available is True


def test_variant_308042_1070_newbatt():
    vp = _vp("m4air_308042_16-512.html")
    assert vp.price == 1070.0
    assert (vp.spec.ram, vp.spec.storage, vp.spec.color) == (16, 512, "midnight")
    assert vp.battery == "new"


def test_variant_308185_24_512_sold():
    vp = _vp("m4air_308185_24-512_sold.html")
    assert vp.price == 1004.09
    assert (vp.spec.ram, vp.spec.storage) == (24, 512)
    assert vp.available is False              # was sold out at capture time


def test_variant_308040_skyblue():
    vp = _vp("m4air_308040_16-256.html")
    assert vp.price == 1029.04
    assert vp.spec.color == "skyblue" and vp.available is True


# --------------------------------------------------------------------------- #
# Availability heuristic robustness
# --------------------------------------------------------------------------- #
def test_availability_not_fooled_by_gotovo_rasprodano():
    # The in-stock fixture literally contains 'Gotovo rasprodano' yet IS in stock.
    html = _read("m4air_308040_16-256.html")
    assert "Gotovo rasprodano" in html
    assert parse.parse_availability(html) is True


# --------------------------------------------------------------------------- #
# Seeds + neighbour crawl links
# --------------------------------------------------------------------------- #
def test_product_seeds():
    seeds = parse.parse_product_seeds(_read("m4air_product.html"))
    assert len(seeds) == 6
    assert all(s.url.startswith("https://www.refurbed.hr/p/") for s in seeds)
    assert any(abs((s.price or 0) - 999.0) < 0.01 for s in seeds)


def test_neighbors_follow_only_allowed_keyboards():
    html = _read("m4air_308042_16-512.html")
    nbrs = parse.crawl_neighbors(html, config.CRAWL_AXES, config.BASE,
                                 keyboard_filter=["US", "HR"])
    assert len(nbrs) >= 4
    # 24 GB RAM neighbour (308185) must be discoverable
    assert any("308185aa" in u for u in nbrs)
    # non-US/HR keyboard variants (SE 424609, DK 424620, NL 424621) must NOT be
    # followed; the page's own keyboard is US (selected) so no kb neighbour added
    assert not any(x in u for u in nbrs
                   for x in ("/424609/", "/424620/", "/424621/", "/308068aa/"))  # DE


def test_neighbors_unfiltered_follows_all_keyboards():
    html = _read("m4air_308042_16-512.html")
    nbrs = parse.crawl_neighbors(html, config.CRAWL_AXES, config.BASE)  # no filter
    assert any("/424620/" in u for u in nbrs)  # DK now followed when unfiltered


def test_opt_gb_parsing():
    assert parse._opt_gb("512 GB") == 512
    assert parse._opt_gb("1 TB") == 1000
    assert parse._opt_gb("2 TB") == 2000
    assert parse._opt_gb("16.0 GB") == 16


def test_neighbors_prune_storage_range():
    html = _read("m4air_308042_16-512.html")   # a 16/512 page; 256 GB is a neighbour
    # storage_min=512 must drop the 256 GB storage neighbour (variant 308040)
    pruned = parse.crawl_neighbors(html, config.CRAWL_AXES, config.BASE,
                                   keyboard_filter=["US", "HR"], storage_min=512)
    assert not any("/308040aa/" in u for u in pruned)
    # …but it's followed when no range filter is applied
    full = parse.crawl_neighbors(html, config.CRAWL_AXES, config.BASE,
                                 keyboard_filter=["US", "HR"])
    assert any("/308040aa/" in u for u in full)


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
