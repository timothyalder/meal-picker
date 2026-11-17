"""Parse Woolworths eReceipt PDFs into a purchase history.

Usage: .venv/bin/python shopping/parse_receipts.py [receipts_dir]

Reads every shopping/receipts/*.pdf (text via Ghostscript's txtwrite device) and writes
shopping/data/purchases.csv, one row per item line. Re-running rebuilds the
file from all receipts, so new PDFs can simply be dropped into the folder.

Line formats handled (between "Description $" and "SUBTOTAL"):
  ^#Solo Zero Sugar Lime 1.25L        2.40   ^ promo price, # GST taxable
  ^#Sprite Zero Sugar 1.25L                   name on its own line, then
  Qty 2 @ $2.25 each                  4.50   a quantity line
  Tomato Truss                                name on its own line, then
  0.807 kg NET @ $3.90/kg             3.15   a weighed line
  ANY 2 for $6.00                    -1.00   multibuy discount, spread over
                                             the previous N items
"""
import csv
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent
RECEIPTS = pathlib.Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "receipts"
OUT = ROOT / "data" / "purchases.csv"
SKIP = {"Woolworths Paper Bag"}

PRICE = re.compile(r"^(?P<name>.*?)\s+(?P<price>-?\d+\.\d{2})$")
QTY = re.compile(r"^Qty (?P<qty>\d+) @ \$(?P<each>\d+\.\d{2}) each$")
WEIGHED = re.compile(r"^(?P<kg>\d+\.\d+) kg NET @ \$(?P<per>\d+\.\d{2})/kg$")
MULTIBUY = re.compile(r"^(ANY|BUY) (?P<n>\d+) for \$\d+\.\d{2}$", re.I)
WHEN = re.compile(r"POS\s+\d+\s+TRANS\s+\d+\s+(?P<time>\d{2}:\d{2})\s+(?P<d>\d{2})/(?P<m>\d{2})/(?P<y>\d{4})")
STORE = re.compile(r"^\s*(?P<num>\d{4}) (?P<name>.+?)\s+PH:")


def text_of(pdf):
    return subprocess.run(
        ["gs", "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=txtwrite", "-o", "-", str(pdf)],
        capture_output=True, text=True, check=True,
    ).stdout


def parse(pdf):
    text = text_of(pdf)
    lines = [re.sub(r"\s+", " ", l).strip() for l in text.splitlines()]
    store = next((m for l in text.splitlines() if (m := STORE.match(l))), None)
    when = WHEN.search(text)
    if not store or not when:
        raise ValueError(f"{pdf.name}: not a Woolworths eReceipt (no store/date)")
    date = f"{when['y']}-{when['m']}-{when['d']}"

    start = next(i for i, l in enumerate(lines) if l.startswith("Description"))
    end = next(i for i, l in enumerate(lines) if "SUBTOTAL" in l)
    items, pending = [], None
    for raw in lines[start + 1:end]:
        if not raw or raw == "^Promotional Price":
            continue
        promo = raw.startswith("^")
        line = raw.lstrip("^#").strip()
        m = PRICE.match(line)
        body, price = (m["name"].strip(), float(m["price"])) if m else (line, None)

        if price is None:  # name on its own line; detail line follows
            pending = {"name": body, "promo": promo}
            continue
        if price < 0:  # multibuy discount: spread over the last N items
            n = int(mb["n"]) if (mb := MULTIBUY.match(body)) else 1
            share = round(price / min(n, len(items)), 2) if items else 0
            for it in items[-n:]:
                it["discount"] = round(it["discount"] + share, 2)
            continue
        if pending and (q := QTY.match(body)):
            items.append(item(pending["name"], int(q["qty"]), "ea", float(q["each"]), price, pending["promo"]))
        elif pending and (w := WEIGHED.match(body)):
            items.append(item(pending["name"], float(w["kg"]), "kg", float(w["per"]), price, pending["promo"]))
        else:
            items.append(item(body, 1, "ea", price, price, promo))
        pending = None

    return [
        {"receipt": pdf.name, "date": date, "time": when["time"], "store": f"{store['num']} {store['name']}", **it}
        for it in items if it["name"] not in SKIP
    ]


def item(name, qty, unit, unit_price, total, promo):
    return {"name": name, "qty": qty, "unit": unit, "unit_price": unit_price, "total": total, "discount": 0.0, "promo": promo}


def main():
    rows = []
    for pdf in sorted(RECEIPTS.glob("*.pdf")):
        rows += parse(pdf)
    rows.sort(key=lambda r: (r["date"], r["time"], r["receipt"]))
    OUT.parent.mkdir(exist_ok=True)
    with OUT.open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"{len({r['receipt'] for r in rows})} receipts, {len(rows)} items -> {OUT.relative_to(ROOT.parent)}")


if __name__ == "__main__":
    main()
