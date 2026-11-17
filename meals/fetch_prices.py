"""Fetch this week's Aldi and Woolworths prices into a snapshot.

Usage: .venv/bin/python shopping/meals/fetch_prices.py [--week YYYY-MM-DD] [--store aldi|woolworths]
           [--max-pages N] [--aldi-store ID] [--woolworths-file PATH] [--coles-file PATH] [--force]

Writes data/prices/<week>.json (shape in SCHEMA.md). Aldi comes from the site's product
search for one store (ALDI_STORE, food categories only, read slowly with a normal browser
User-Agent) plus its Super Savers list; Woolworths from the hotprices-au community file. A store that fails is
saved as ok: false and the script exits 1; re-running keeps any store already read for the
week and only refetches the failed one (--force refetches regardless).

Weighted Woolworths records (rules checked against live product pages):
  "... per 100g"       price is already $/kg; quantity is only the ordering step -> per_kg
  "... 1.1kg - 1.7kg"  one variable-weight pack, no per-kg price in the file -> dropped
  "... each"           price per item, no weight -> kept as 1 ea
"""
import argparse
import datetime
import gzip
import json
import re
import statistics
import sys
import time

import requests

import common

STORES = ("aldi", "woolworths", "coles")
UA = "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
DELAY = 3.5  # seconds between Aldi requests
ALDI_STORE = "G508"  # Aldi Chisholm, 36 Benham Street, Chisholm ACT 2905
# The product search the site's own store selector loads prices from.
ALDI_SEARCH = "https://asl.api.aldi.com.au/commerce/v3/product-search?currency=AUD&serviceType=walk-in&limit=30"
ALDI_SPECIALS = "1588161426952145"  # Super Savers
# Category keys worth reading; the rest is liquor, household, baby, pets and seasonal lines.
ALDI_FOOD = {"950000000": "produce", "940000000": "meat", "930000000": "deli", "960000000": "dairy",
             "970000000": "pantry", "920000000": "bakery", "980000000": "frozen", "1000000000": "drinks"}
WOOLIES_URL = "https://hotprices.org/data/latest-canonical.woolies.compressed.json.gz"
COLES_URL = "https://hotprices.org/data/latest-canonical.coles.compressed.json.gz"
SPECIAL = 0.85  # price at or below this share of the usual price is a special
HISTORY_WEEKS = 8

# hotprices category codes are <main><sub>; mains 0-6 are food and drink (7+ is beauty,
# liquor, pharmacy, baby, pet, cleaning, home). Null categories are kept.
WOOLIES_FOOD = {"0": "produce", "1": "meat", "2": "dairy", "3": "pantry", "4": "bakery", "5": "frozen", "6": "drinks"}
# Coles category codes start with two digits; 76 and up is health, beauty, household and liquor.
COLES_FOOD = {"00": "produce", "10": "meat", "11": "seafood", "21": "dairy", "22": "dairy", "32": "pantry",
              "33": "pantry", "44": "bakery", "54": "frozen", "65": "drinks"}
PER_STEP = re.compile(r"\bper\s*\d+(?:\.\d+)?\s*(g|ml)\s*$", re.I)       # "per 100g": price is $/kg
WEIGHT_RANGE = re.compile(r"\d(?:\.\d+)?\s*k?g\s*-\s*\d+(?:\.\d+)?\s*k?g\s*$", re.I)  # "1.1kg - 1.7kg"

SIZE = re.compile(r"(?:(?P<n>\d+)\s*x\s*)?(?P<qty>\d+(?:\.\d+)?)\s*(?P<unit>kg|g|ml|l|litres?|each|ea|pack|pk)\b")


def parse_size(text):
    """Pack size as sold -> (qty, "g" | "ml" | "ea", per_kg), or None if unreadable."""
    t = (text or "").lower().strip()
    if re.fullmatch(r"(per\s*)?kg|approx\.?.*\bkg\b.*", t):
        return 1000, "g", True
    if t in ("each", "ea"):
        return 1, "ea", False
    m = SIZE.search(t)
    if not m:
        return None
    qty, unit = common.to_base(float(m["qty"]) * int(m["n"] or 1), m["unit"])
    return round(qty, 3), unit, False


# --- Aldi -------------------------------------------------------------------------------

def aldi_search(text):
    """One product-search response -> (raw product dicts, total count for the query)."""
    doc = json.loads(text)
    return doc["data"], doc["meta"]["pagination"]["totalCount"]


def aldi_product(item):
    """Raw Aldi product -> snapshot product, or None if it has no usable price or size."""
    cents = (item.get("price") or {}).get("amount")
    if not cents:
        return None
    if item.get("weightType") in ("1", "3"):  # "approx. 0.8 kg per package" meat (1), loose produce (3): amount is $/kg
        size = 1000, "g", True
    else:
        size = parse_size(item.get("sellingSize"))
    if not size:
        return None
    cats = item.get("categories") or []
    return {"store": "aldi", "id": item["sku"], "name": item["name"], "price": cents / 100,
            "size_qty": size[0], "size_unit": size[1], "per_kg": size[2],
            "usual_price": None, "special": False,
            "category": cats[0]["name"].lower() if cats else None}


class Polite:
    """GET with a browser User-Agent and at least DELAY seconds between requests."""

    def __init__(self):
        self.last = 0.0

    def __call__(self, url):
        time.sleep(max(0.0, self.last + DELAY - time.monotonic()))
        try:
            r = requests.get(url, headers={"User-Agent": UA}, timeout=60)
        finally:
            self.last = time.monotonic()
        r.raise_for_status()
        return r.text


def crawl(get, store, category, max_pages=None):
    """Every product in one category at one store."""
    items, offset, total, page = [], 0, 1, 0
    while offset < total and (max_pages is None or page < max_pages):
        found, total = aldi_search(get(f"{ALDI_SEARCH}&servicePoint={store}&categoryTree={category}&offset={offset}"))
        if not found:
            break
        items += found
        offset += len(found)
        page += 1
    return items


def fetch_aldi(week, max_pages=None, get=None, prices_dir=None, store=ALDI_STORE):
    get = get or Polite()
    specials = {i["sku"] for i in crawl(get, store, ALDI_SPECIALS)}
    products, skipped = {}, 0
    for key, category in ALDI_FOOD.items():
        for item in crawl(get, store, key, max_pages):
            p = aldi_product(item)
            if not p:
                skipped += 1
                continue
            p.update(special=p["id"] in specials, category=category)
            products[p["id"]] = p
    products = list(products.values())
    if not products:
        raise ValueError("no products parsed")
    apply_history(products, week, prices_dir or common.PRICES)
    n_special = sum(p["special"] for p in products)
    return products, f"{len(products)} products ({n_special} specials), {skipped} without price/size skipped, prices for store {store}"


def apply_history(products, week, prices_dir):
    """usual_price = median over the previous eight snapshots; special by the 0.85 rule."""
    older = sorted(p for p in prices_dir.glob("????-??-??.json") if p.stem < week)[-HISTORY_WEEKS:]
    seen = {}
    for path in older:
        store = (common.load(path, {}).get("stores") or {}).get("aldi") or {}
        for p in store.get("products", []) if store.get("ok") else []:
            seen.setdefault(p["id"], []).append(p["price"])
    for p in products:
        if p["id"] in seen:
            p["usual_price"] = round(statistics.median(seen[p["id"]]), 2)
            p["special"] = p["special"] or p["price"] <= SPECIAL * p["usual_price"]


# --- Woolworths --------------------------------------------------------------------------

def usual_from_history(history, week):
    """Median of the price in force on each of the previous eight Wednesdays.

    priceHistory holds price-change points, so the price on a day is the newest entry on or
    before it. None unless the history reaches back to the earliest of those Wednesdays.
    """
    wk = datetime.date.fromisoformat(week)
    weds = [(wk - datetime.timedelta(weeks=n)).isoformat() for n in range(1, HISTORY_WEEKS + 1)]
    hist = sorted(history or [], key=lambda h: h["date"], reverse=True)
    if not hist or hist[-1]["date"] > weds[-1]:
        return None
    return round(statistics.median(next(h["price"] for h in hist if h["date"] <= w) for w in weds), 2)


def woolies_product(rec, week):
    """hotprices record -> (product, None) or (None, reason it was dropped)."""
    cat = rec.get("category")
    if cat is not None and not (len(cat) == 3 and cat[0] in WOOLIES_FOOD):
        return None, "not food"
    price, unit, qty = rec.get("price"), rec.get("unit"), rec.get("quantity")
    if not price or price <= 0:
        return None, "no price"
    tail = re.sub(r"<br>", " ", rec.get("description") or "").strip()
    per_kg = False
    if unit in ("g", "ml") and PER_STEP.search(tail):
        qty, per_kg = 1000, True
    elif unit == "ea" and WEIGHT_RANGE.search(tail):
        return None, "variable-weight pack"
    elif unit not in ("g", "ml", "ea") or not qty or qty <= 0:
        return None, f"unit {unit!r}"
    usual = usual_from_history(rec.get("priceHistory"), week)
    return {"store": "woolworths", "id": str(rec["id"]), "name": rec["name"], "price": round(price, 2),
            "size_qty": round(qty, 3), "size_unit": unit, "per_kg": per_kg,
            "usual_price": usual, "special": usual is not None and price <= SPECIAL * usual,
            "category": WOOLIES_FOOD.get(cat[0]) if cat else None}, None


def coles_product(rec, week):
    """hotprices Coles record -> (product, None) or (None, reason it was dropped).

    Coles marks loose and variable-weight items (isWeighted): the price is for a typical piece of
    `quantity` grams ("Loose Brown Onions" $0.84 for 200 g), so it converts exactly to $/kg.
    """
    cat = rec.get("category")
    if cat is not None and str(cat)[:2] not in COLES_FOOD:
        return None, "not food"
    price, unit, qty = rec.get("price"), rec.get("unit"), rec.get("quantity")
    if not price or price <= 0:
        return None, "no price"
    if unit not in ("g", "ml", "ea") or not qty or qty <= 0:
        return None, f"unit {unit!r}"
    usual = usual_from_history(rec.get("priceHistory"), week)
    special = usual is not None and price <= SPECIAL * usual
    if rec.get("isWeighted") and unit == "g":
        scale = 1000 / qty
        price, qty, usual = price * scale, 1000, usual and usual * scale
    return {"store": "coles", "id": str(rec["id"]), "name": rec["name"], "price": round(price, 2),
            "size_qty": round(qty, 3), "size_unit": unit, "per_kg": bool(rec.get("isWeighted") and unit == "g"),
            "usual_price": usual and round(usual, 2), "special": special,
            "category": COLES_FOOD.get(str(cat)[:2]) if cat else None}, None


def read_hotprices(url, path=None):
    if path:
        raw = open(path, "rb").read()
    else:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=120)
        r.raise_for_status()
        raw = r.content
    return json.loads(gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw)


def fetch_coles(week, path=None):
    return from_hotprices(read_hotprices(COLES_URL, path), coles_product, week)


def fetch_woolworths(week, path=None):
    return from_hotprices(read_hotprices(WOOLIES_URL, path), woolies_product, week)


def from_hotprices(records, convert, week):
    products, dropped = [], {}
    for rec in records:
        p, why = convert(rec, week)
        if p:
            products.append(p)
        else:
            dropped[why] = dropped.get(why, 0) + 1
    if not products:
        raise ValueError("no products in file")
    latest = max(h["date"] for r in records for h in r.get("priceHistory") or [])
    n_special = sum(p["special"] for p in products)
    drops = ", ".join(f"{n} {why}" for why, n in sorted(dropped.items(), key=lambda kv: -kv[1]))
    return products, f"{len(products)} products ({n_special} specials), data to {latest}; dropped {drops or 'none'}"


# --- Run ---------------------------------------------------------------------------------

def update(snap, fetchers, force=False):
    """Fill snap["stores"] from each fetcher, keeping stores already read unless forced."""
    for store, fetch in fetchers.items():
        old = snap["stores"].get(store)
        if old and old.get("ok") and not force:
            print(f"{store}: kept {len(old['products'])} products from {old['fetched']}")
            continue
        now = datetime.datetime.now().isoformat(timespec="seconds")
        try:
            products, summary = fetch()
            snap["stores"][store] = {"ok": True, "fetched": now, "products": products}
            print(f"{store}: {summary}")
        except Exception as e:
            snap["stores"][store] = {"ok": False, "fetched": now, "products": [], "error": f"{type(e).__name__}: {e}"}
            print(f"{store}: FAILED {type(e).__name__}: {e}")
    return snap


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--week", default=common.week_of(), help="Wednesday the specials week starts (default: current)")
    ap.add_argument("--store", choices=STORES)
    ap.add_argument("--max-pages", type=int, help="limit Aldi pages per category (testing)")
    ap.add_argument("--aldi-store", default=ALDI_STORE, help=f"Aldi store code (default {ALDI_STORE}, Chisholm)")
    ap.add_argument("--woolworths-file", help="local hotprices .json.gz instead of downloading")
    ap.add_argument("--coles-file", help="local hotprices Coles .json.gz instead of downloading")
    ap.add_argument("--force", action="store_true", help="refetch stores already read this week")
    args = ap.parse_args()
    week = common.week_of(datetime.date.fromisoformat(args.week))

    fetchers = {
        "aldi": lambda: fetch_aldi(week, args.max_pages, store=args.aldi_store),
        "woolworths": lambda: fetch_woolworths(week, args.woolworths_file),
        "coles": lambda: fetch_coles(week, args.coles_file),
    }
    if args.store:
        fetchers = {args.store: fetchers[args.store]}
    out = common.PRICES / f"{week}.json"
    snap = update(common.load(out) or {"week": week, "stores": {}}, fetchers, args.force)
    common.save(out, snap)
    print(f"-> {out.relative_to(common.ROOT.parent.parent)}")
    sys.exit(0 if all(s["ok"] for s in snap["stores"].values()) else 1)


if __name__ == "__main__":
    main()
