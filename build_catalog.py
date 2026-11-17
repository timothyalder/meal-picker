"""Build the learned grocery catalogue from the purchase history.

Usage: .venv/bin/python shopping/build_catalog.py

Reads shopping/data/purchases.csv (from parse_receipts.py) and writes
shopping/data/catalog.json: one entry per distinct item with how often it's
bought, when it was last bought, its usual and best unit price, and a category.
Categories drive later work: "household" and "personal" are the consumables
worth stocking up on when discounted; the food categories feed meal prep.
Edit CATEGORY_RULES (first match wins) to correct a category.
"""
import csv
import datetime
import json
import pathlib
import re
import statistics

ROOT = pathlib.Path(__file__).resolve().parent
PURCHASES = ROOT / "data" / "purchases.csv"
OUT = ROOT / "data" / "catalog.json"

CATEGORY_RULES = [
    ("household", r"dishwash|freezer bag|detergent|laundry|toilet paper|paper towel|cling|foil|bin bag|garbage|sponge|cleaner|bleach"),
    ("personal", r"toothpaste|toothbrush|deodorant|shampoo|conditioner|body wash|soap|razor|floss|mouthwash|sunscreen|tissue"),
    # Specific before general: "KitKat Strawberry" is a treat, not produce.
    ("snacks & treats", r"kitkat|aero|\bgum\b|mince pie|crackers"),
    ("frozen", r"ice cream|hash brown|samosa"),
    ("drinks", r"\b1\.25l\b|soft drink|sparkling|soda|pasito|gatorade|coffee|latte|smoothie|\bsmth\b"),
    ("pantry", r"rice|oats|pasta|sauce|paste|mayonnaise|garlic|ginger|masala|powder|chia|salsa|spread|strps"),
    ("bakery", r"bread|loaf|bagel|croissant|scroll|tortilla|toast|\bpane\b|sandwich"),
    ("dairy", r"yoghurt|cheese|mozzarella|paneer|cream|milk|eggs|chobani|flip"),
    ("produce", r"tomato|spinach|lettuce|onion|cucumber|berry|chilli|jalepeno|iceberg"),
    ("meat & fish", r"ham|turkey|fish|tuna|chicken|beef|mince"),
]


def category(name):
    low = name.lower()
    return next((cat for cat, rx in CATEGORY_RULES if re.search(rx, low)), "other")


def main():
    rows = list(csv.DictReader(PURCHASES.open()))
    items = {}
    for r in rows:
        it = items.setdefault(r["name"], {"name": r["name"], "dates": [], "prices": [], "promo": 0, "spent": 0.0})
        it["dates"].append(r["date"])
        # Price actually paid per unit, after any multibuy discount.
        qty = float(r["qty"]) if r["unit"] == "ea" else 1.0
        it["prices"].append((float(r["total"]) + float(r["discount"])) / qty)
        it["promo"] += r["promo"] == "True"
        it["spent"] += float(r["total"]) + float(r["discount"])

    catalog = []
    for it in items.values():
        days = sorted({datetime.date.fromisoformat(d) for d in it["dates"]})
        gaps = [(b - a).days for a, b in zip(days, days[1:])]
        catalog.append({
            "name": it["name"],
            "category": category(it["name"]),
            "times_bought": len(it["dates"]),
            "first_bought": days[0].isoformat(),
            "last_bought": days[-1].isoformat(),
            "every_days": round(statistics.median(gaps)) if gaps else None,
            "usual_price": round(statistics.median(it["prices"]), 2),
            "best_price": round(min(it["prices"]), 2),
            "bought_on_promo": it["promo"],
            "total_spent": round(it["spent"], 2),
        })
    catalog.sort(key=lambda c: (-c["times_bought"], -c["total_spent"]))
    OUT.write_text(json.dumps({"built": datetime.date.today().isoformat(), "receipts": len({r["receipt"] for r in rows}), "items": catalog}, indent=1))
    print(f"{len(catalog)} distinct items from {len(rows)} purchases -> {OUT.relative_to(ROOT.parent)}")


if __name__ == "__main__":
    main()
