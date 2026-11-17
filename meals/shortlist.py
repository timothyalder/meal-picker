"""Shortlist store products for ingredient names, to help fill in products.yaml.

Usage: .venv/bin/python shopping/meals/shortlist.py [--week YYYY-MM-DD] [--store coles] [-n 6] "search words" [...]
       .venv/bin/python shopping/meals/shortlist.py --unmapped     every pool ingredient not in products.yaml

Prints, per store, the products whose names share the most words with the search, cheapest per
unit first among equal matches. Words starting with "-" exclude ("chicken breast -crumbed").
Reads the latest price snapshot; nothing is written.
"""
import argparse
import re
import sys

import common


def stem(word):
    return re.sub(r"(ies|es|s)$", "", word) if len(word) > 3 else word


def words(text):
    return {stem(w) for w in common.norm_name(text).split()}


def unit_price(p):
    """(price per kg / L / each, label) for ranking and display."""
    if p.get("per_kg"):
        return p["price"], "/kg"
    if p.get("size_qty"):
        per = p["price"] / p["size_qty"]
        return (per * 1000, "/kg" if p["size_unit"] == "g" else "/L") if p["size_unit"] in ("g", "ml") else (per, "/ea")
    return p["price"], ""


def size_text(p):
    if p.get("per_kg"):
        return "per kg"
    q, u = p.get("size_qty"), p.get("size_unit")
    return f"{q:g}{'' if u == 'ea' else ' '}{u}" if q else "?"


def shortlist(query, products, n):
    want = {w for w in query.split() if not w.startswith("-")}
    avoid = {stem(w[1:].lower()) for w in query.split() if w.startswith("-")}
    want = words(" ".join(want))
    scored = []
    for p in products:
        have = words(p["name"])
        hits = len(want & have)
        if not hits or have & avoid:
            continue
        scored.append((-hits / len(want), len(have), unit_price(p)[0], p))
    scored.sort(key=lambda s: s[:3])
    return [s[3] for s in scored[:n]]


def latest_snapshot(week=None):
    paths = sorted(common.PRICES.glob("*.json"))
    path = common.PRICES / f"{week}.json" if week else (paths[-1] if paths else None)
    snap = common.load(path) if path else None
    if not snap:
        sys.exit("no price snapshot; run fetch_prices.py first")
    return snap


def unmapped():
    import yaml
    from crawl_recipes import build_pool, ingredient_counts, load_overrides, load_recipes
    path = common.ROOT / "products.yaml"
    table = {common.norm_name(str(k)) for k in ((yaml.safe_load(path.read_text()) or {}) if path.exists() else {})}
    recipes = load_recipes(common.RECIPES)
    pool = build_pool(recipes, load_overrides())["pool"]
    return [(name, n) for name, n in ingredient_counts(recipes, pool) if name not in table]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("queries", nargs="*")
    ap.add_argument("--week")
    ap.add_argument("--store", help="only this store (aldi, woolworths, coles)")
    ap.add_argument("-n", type=int, default=6)
    ap.add_argument("--unmapped", action="store_true")
    args = ap.parse_args()
    if args.unmapped:
        for name, n in unmapped():
            print(f"{name}\t{n}")
        return
    stores = latest_snapshot(args.week)["stores"]
    if args.store:
        if args.store not in stores:
            sys.exit(f"no store {args.store!r} in the snapshot; have {', '.join(stores)}")
        stores = {args.store: stores[args.store]}
    for q in args.queries:
        print(f"## {q}")
        for store, data in stores.items():
            for p in shortlist(q, data.get("products") or [], args.n):
                per, label = unit_price(p)
                flag = " SPECIAL" if p.get("special") else ""
                print(f"  {store[:4]} {p['id']:>18}  ${p['price']:<6.2f} {size_text(p):>9}  ${per:6.2f}{label:<3} {p['name']}{flag}")


if __name__ == "__main__":
    main()
