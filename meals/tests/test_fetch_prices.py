"""Tests for fetch_prices.py against saved pages and a trimmed hotprices file (no network)."""
import gzip
import json
import pathlib
import re
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402
import fetch_prices as fp  # noqa: E402

FIX = pathlib.Path(__file__).parent / "fixtures" / "prices"
WEEK = "2026-11-18"
KEYS = {"store", "id", "name", "price", "size_qty", "size_unit", "per_kg", "usual_price", "special", "category"}


@pytest.mark.parametrize("text, expected", [
    ("1kg", (1000, "g", False)),
    ("1 kg", (1000, "g", False)),
    ("500g", (500, "g", False)),
    ("300 g", (300, "g", False)),
    ("2L", (2000, "ml", False)),
    ("1 l", (1000, "ml", False)),
    ("500 ml", (500, "ml", False)),
    ("6 pack", (6, "ea", False)),
    ("2 each", (2, "ea", False)),
    ("each", (1, "ea", False)),
    ("per kg", (1000, "g", True)),
    ("approx. 0.8 kg per package", (1000, "g", True)),
    ("4 x 100g", (400, "g", False)),
    (None, None),
    ("", None),
    ("bottle", None),
])
def test_parse_size(text, expected):
    assert fp.parse_size(text) == expected


def aldi(name):
    items, total = fp.aldi_search((FIX / name).read_text())
    return items, total, {p["id"]: p for i in items if (p := fp.aldi_product(i))}


def test_aldi_search_page():
    items, total, products = aldi("aldi_products_p1.json")
    assert len(items) == 30 and total == 3459
    rice = products["000000000000262101"]
    assert rice == {"store": "aldi", "id": "000000000000262101", "name": "Jasmine Rice 1kg", "price": 2.99,
                    "size_qty": 1000, "size_unit": "g", "per_kg": False, "usual_price": None,
                    "special": False, "category": "pantry"}
    assert products["000000000000200347"]["size_qty"] == 1000  # Sunflower Oil 1L
    assert products["000000000000200347"]["size_unit"] == "ml"
    assert "000000000000268037" not in products  # beer bottle: no selling size


def test_aldi_loose_produce_is_per_kg():
    _, _, products = aldi("aldi_fresh_veg.json")
    broccoli = products["000000000000380208"]  # Broccoli per kg
    assert (broccoli["price"], broccoli["per_kg"], broccoli["size_qty"], broccoli["size_unit"]) == (7.99, True, 1000, "g")
    assert products["000000000000380232"]["size_unit"] == "ea"  # Cauliflower Each
    assert products["000000000000380181"]["size_qty"] == 3  # Baby Gem Lettuce 3 Pack


def test_aldi_random_weight_meat_uses_per_kg_price():
    _, total, products = aldi("aldi_meat.json")
    breast = products["000000000000399727"]  # approx. 1.38 kg pack, $10.99/kg, ~$15.17 a pack
    assert (breast["price"], breast["per_kg"], breast["size_qty"]) == (10.99, True, 1000)
    assert products["000000000000399119"]["size_qty"] == 500  # Regular Beef Mince 500g
    assert total == 152


def test_fetch_aldi_reads_one_store_and_marks_super_savers(tmp_path, monkeypatch):
    monkeypatch.setattr(fp, "ALDI_FOOD", {"940000000": "meat"})
    pages = {fp.ALDI_SPECIALS: "aldi_super_savers.json", "940000000": "aldi_meat.json"}
    urls = []

    def get(url):
        urls.append(url)
        return (FIX / pages[re.search(r"categoryTree=(\d+)", url)[1]]).read_text()

    products, summary = fp.fetch_aldi(WEEK, max_pages=1, get=get, prices_dir=tmp_path)
    assert len(urls) == 2 and all("servicePoint=G508" in u for u in urls)
    assert urls[1].endswith("categoryTree=940000000&offset=0")
    by_id = {p["id"]: p for p in products}
    assert by_id["000000000000399677"]["special"]  # meatballs: in both lists
    assert not by_id["000000000000399119"]["special"]
    assert by_id["000000000000399119"]["category"] == "meat"
    assert all(set(p) == KEYS for p in products)
    assert "store G508" in summary


def test_crawl_pages_until_the_total(monkeypatch):
    doc = json.loads((FIX / "aldi_meat.json").read_text())
    offsets = []

    def get(url):
        offsets.append(int(re.search(r"offset=(\d+)", url)[1]))
        return json.dumps({"meta": {"pagination": {"totalCount": 70}}, "data": doc["data"][:30 if offsets[-1] < 60 else 10]})

    assert len(fp.crawl(get, "G508", "940000000")) == 70 and offsets == [0, 30, 60]


def test_aldi_usual_price_from_snapshots(tmp_path):
    def snap(week, price):
        common.save(tmp_path / f"{week}.json", {"week": week, "stores": {"aldi": {"ok": True, "fetched": "x", "products": [
            {"id": "1", "price": price}]}}})
    for week, price in [("2026-10-28", 10.0), ("2026-11-04", 12.0), ("2026-11-11", 11.0), ("2026-11-25", 1.0)]:
        snap(week, price)
    products = [{"id": "1", "price": 9.0, "usual_price": None, "special": False},
                {"id": "2", "price": 5.0, "usual_price": None, "special": False}]
    fp.apply_history(products, WEEK, tmp_path)
    assert products[0]["usual_price"] == 11.0 and products[0]["special"]  # 9 <= 0.85 * 11
    assert products[1]["usual_price"] is None and not products[1]["special"]


def rec(**kw):
    base = {"id": 1, "name": "Thing", "description": " Thing 500g", "price": 10, "unit": "g", "quantity": 500.0,
            "category": "110", "isWeighted": False,
            "priceHistory": [{"date": "2026-11-11", "price": 10}, {"date": "2025-01-01", "price": 12}]}
    return {**base, **kw}


def test_woolies_pack():
    p, why = fp.woolies_product(rec(), WEEK)
    assert why is None and set(p) == KEYS
    assert (p["price"], p["size_qty"], p["size_unit"], p["per_kg"], p["category"]) == (10, 500, "g", False, "meat")


def test_woolies_per_step_price_is_already_per_kg():  # rule 2
    p, _ = fp.woolies_product(rec(description=" Ingham's Chicken Breast<br>Supreme Shaved... per 100g",
                                  price=21, quantity=100.0), WEEK)
    assert (p["price"], p["size_qty"], p["size_unit"], p["per_kg"]) == (21, 1000, "g", True)


def test_woolies_weight_range_pack_is_dropped():  # rule 3
    for desc in [" Macro Free Range Chicken<br>Breast... 1.1kg - 1.7kg", " Macro Thigh... 1.1kg -1.4kg",
                 " Lamb Offal Hearts 400g - 700g", " Pork Belly Roast  900g - 1.5kg"]:
        assert fp.woolies_product(rec(description=desc, unit="ea", quantity=1), WEEK) == (None, "variable-weight pack")


def test_woolies_each_is_kept_as_one_item():  # rule 4
    p, _ = fp.woolies_product(rec(description="  Onion Brown  each", price=0.63, unit="ea", quantity=1,
                                  category="001"), WEEK)
    assert (p["price"], p["size_qty"], p["size_unit"], p["per_kg"], p["category"]) == (0.63, 1, "ea", False, "produce")


def test_woolies_drops_non_food_and_odd_units():
    assert fp.woolies_product(rec(category="1098"), WEEK) == (None, "not food")  # nappies
    assert fp.woolies_product(rec(category="882"), WEEK) == (None, "not food")  # beer
    assert fp.woolies_product(rec(category=None, unit="cm"), WEEK) == (None, "unit 'cm'")
    assert fp.woolies_product(rec(price=None), WEEK) == (None, "no price")
    assert fp.woolies_product(rec(category=None), WEEK)[0]["category"] is None


def test_usual_price_takes_price_in_force_each_wednesday():
    # Wednesdays before 2026-11-18: 11-11, 11-04, 10-28, 10-21, 10-14, 10-07, 09-30, 09-23
    history = [{"date": "2026-11-12", "price": 6}, {"date": "2026-10-20", "price": 8},
               {"date": "2026-10-06", "price": 10}, {"date": "2026-01-01", "price": 9}]
    # in force: 8, 8, 8, 8, 10, 10, 9, 9 -> median 8.5
    assert fp.usual_from_history(history, WEEK) == 8.5
    p, _ = fp.woolies_product(rec(price=6, priceHistory=history), WEEK)
    assert p["usual_price"] == 8.5 and p["special"]  # 6 <= 0.85 * 8.5
    p, _ = fp.woolies_product(rec(price=8, priceHistory=history), WEEK)
    assert not p["special"]


def test_usual_price_single_entry():  # rule 5
    old = [{"date": "2026-09-23", "price": 5}]  # exactly eight weeks back: price never changed
    assert fp.usual_from_history(old, WEEK) == 5
    p, _ = fp.woolies_product(rec(price=5, priceHistory=old), WEEK)
    assert p["usual_price"] == 5 and not p["special"]
    assert fp.usual_from_history([{"date": "2026-09-24", "price": 5}], WEEK) is None  # too new
    assert fp.usual_from_history([], WEEK) is None


def test_fetch_woolworths_sample_file():
    products, summary = fp.fetch_woolworths(WEEK, FIX / "woolies_sample.json.gz")
    by_id = {p["id"]: p for p in products}
    assert by_id["98431"]["per_kg"] and by_id["98431"]["price"] == 21  # deli chicken, per 100g
    assert by_id["126497"]["per_kg"] and by_id["126497"]["price"] == 42  # salmon, per 200g
    assert by_id["144329"]["size_unit"] == "ea"  # loose onion
    assert by_id["577864"]["size_qty"] == 1000 and not by_id["577864"]["per_kg"]  # beef mince 1kg
    assert "700228" not in by_id and "764859" not in by_id  # weight-range packs
    assert "6014520" not in by_id  # nappies
    assert "2 variable-weight pack" in summary


# --- Retry / merge ---

def test_update_keeps_good_store_and_refetches_failed(capsys):
    good = {"ok": True, "fetched": "2026-11-18T07:31:02", "products": [{"id": "a"}]}
    snap = {"week": WEEK, "stores": {"aldi": good, "woolworths": {"ok": False, "products": [], "error": "x"}}}
    calls = []

    def boom():
        calls.append("aldi")
        raise AssertionError("aldi should not be refetched")

    def woolies():
        calls.append("woolworths")
        return [{"id": "w"}], "1 products"

    fp.update(snap, {"aldi": boom, "woolworths": woolies})
    assert calls == ["woolworths"]
    assert snap["stores"]["aldi"] is good
    assert snap["stores"]["woolworths"]["ok"] and snap["stores"]["woolworths"]["products"] == [{"id": "w"}]
    assert "aldi: kept 1 products" in capsys.readouterr().out


def test_update_records_failure():
    def broken():
        raise ConnectionError("403 Forbidden")

    snap = fp.update({"week": WEEK, "stores": {}}, {"aldi": broken})
    aldi = snap["stores"]["aldi"]
    assert aldi["ok"] is False and aldi["products"] == [] and "403 Forbidden" in aldi["error"]


def test_update_force_refetches():
    snap = {"week": WEEK, "stores": {"aldi": {"ok": True, "fetched": "x", "products": []}}}
    fp.update(snap, {"aldi": lambda: ([{"id": "new"}], "1 products")}, force=True)
    assert snap["stores"]["aldi"]["products"] == [{"id": "new"}]


def coles_rec(**kw):
    rec = {"id": 1, "name": "Test", "description": "TEST", "price": 5.0, "priceHistory": [{"date": "2026-11-16", "price": 5.0}],
           "isWeighted": False, "unit": "g", "quantity": 500.0, "category": "1050"}
    rec.update(kw)
    return rec


def test_coles_pack_and_weighted():
    p, _ = fp.coles_product(coles_rec(), WEEK)
    assert (p["store"], p["price"], p["size_qty"], p["per_kg"], p["category"]) == ("coles", 5.0, 500.0, False, "meat")
    onion, _ = fp.coles_product(coles_rec(price=0.84, quantity=200, isWeighted=True, category="0010"), WEEK)
    assert (onion["price"], onion["size_qty"], onion["per_kg"]) == (4.2, 1000, True)


def test_coles_drops_non_food():
    assert fp.coles_product(coles_rec(category="7701"), WEEK) == (None, "not food")
    assert fp.coles_product(coles_rec(category=None), WEEK)[0] is not None
