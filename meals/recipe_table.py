"""A recipe scaled to N serves, with the protein target and swaps, as an ingredient/step table.

Usage: .venv/bin/python shopping/meals/recipe_table.py <slug | recipe.json> [--serves 6] [--format text|json|html]

The main protein (a cut in protein.yaml) is sized so each serve gets target_protein_g from it;
a second cut weighing at least a quarter of the main one is scaled with it, keeping their ratio.
Everything else scales by serves, and sauce and liquid by a further quarter. Swaps come from
swaps.yaml; overrides.yaml `steps_for` fixes step placement by hand.

prepare(recipe, serves=6, products=None, swaps=None, protein=None, overrides=None, protein_g=None,
swap_ids=None) loads the yaml files when an argument is None and returns a plain JSON-able dict.
protein_g is the protein target per serve (default: protein.yaml's target_protein_g); swap_ids,
when given, are the swap ids a person has on (see enabled_swaps), and the other rows are skipped:

  {"slug": str, "title": str, "url": str, "serves": 6, "recipe_serves": 4,
   "cut": "chicken thigh fillet" | None,     # main protein, a protein.yaml key
   "cuts": [str],                            # every cut scaled to the target, main first
   "per_serve": {"protein_g": 50 | None,     # from the scaled cuts only
                 "meat_g": 256 | None,       # their raw weight per serve
                 "kcal": 610 | None},        # published kcal with the protein resized and swaps taken off; rough
   "ingredients": [{
       "name": str,                          # after swap
       "key": str,                           # common.norm_name(name): the products.yaml key
       "amount": float | None, "unit": str | None, "text": "1 1/2 tbsp",   # rounded, for display
       "base_qty": float | None, "base_unit": "g" | "ml" | "ea" | None,   # unrounded, for costing
       "original": str | None,               # name before a swap
       "note": str | None,                   # swap note
       "prep": str | None,                   # the recipe's own notes ("finely chopped")
       "group": str | None, "step": 1,       # 1-based step it is placed against
       "is_protein": bool,
       "removed": bool}],                    # swapped to nothing: show struck through, don't cost
   "steps": [{"n": 1, "text": str, "ingredients": [0, 3]}],   # 0-based indexes into ingredients
   "notes": [str],                           # shown above the table
   "unplaced": [str]}                        # names no step mentions; put against step 1
"""
import argparse
import html
import itertools
import json
import math
import pathlib
import re
import sys
import textwrap

import common

SAUCE_EXTRA = 1.25
# Ingredients that make the sauce or liquid: these get SAUCE_EXTRA on top of the serves scaling.
SAUCY = ["stock", "broth", "passata", "tomato", "coconut milk", "coconut cream", "cream",
         "yoghurt", "yogurt", "sauce", "water", "milk", "wine"]
SECOND_CUT = 0.25  # another cut at least this share of the main cut's weight is scaled with it
FRYING = re.compile(r"\b(fry|fries|fried|frying|stir-?fry\w*|saut[eé]\w*|sear\w*|brown(?!\s+sugar)\w*|skillet|wok)\b", re.I)
# Words that don't identify an ingredient when looking for it in the steps.
FILLER = {"fresh", "freshly", "chopped", "finely", "roughly", "large", "small", "medium", "extra", "virgin",
          "ground", "dried", "boneless", "skinless", "of", "and", "or", "to", "taste", "for", "the", "a", "an",
          "plain", "whole", "raw", "canned", "tinned", "can", "x", "g", "peeled", "sliced", "diced",
          "unsalted", "salted", "low", "fat", "reduced", "sodium", "optional", "pure", "thickened", "store", "bought"}
SPOONS = {"tsp": "tsp", "teaspoon": "tsp", "teaspoons": "tsp", "tsps": "tsp",
          "tbsp": "tbsp", "tablespoon": "tbsp", "tablespoons": "tbsp", "tbs": "tbsp", "tbsps": "tbsp",
          "cup": "cup", "cups": "cup"}
FRACTIONS = {0.25: "1/4", 1 / 3: "1/3", 0.5: "1/2", 2 / 3: "2/3", 0.75: "3/4"}


def stem(word):
    if word.endswith("eaves"):  # leaves -> leaf, not the verb "leave"
        return word[:-3] + "f"
    if word.endswith("ies") and len(word) > 4:
        return word[:-2]
    if word.endswith("y") and len(word) > 3:
        return word[:-1] + "i"
    if word.endswith("oes") and len(word) > 4:
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def words(text):
    return [stem(w) for w in common.norm_name(text or "").split()]


def contains(hay, needle):
    n = len(needle)
    return n > 0 and any(hay[i:i + n] == needle for i in range(len(hay) - n + 1))


def mentions(name_words, phrases):
    return any(contains(name_words, words(p)) for p in phrases or [])


def config(name, default):
    return common.load_yaml(common.ROOT / name, default)


# --- protein -----------------------------------------------------------------

def cut_of(item, products, cuts):
    marked = (products.get(item["key"]) or {}).get("cut")
    if marked in cuts:
        return marked
    best = None
    for cut, row in cuts.items():
        if mentions(item["words"], row.get("not")):
            continue
        for m in row.get("match") or [cut]:
            if contains(item["words"], words(m)) and (best is None or len(m) > len(best[0])):
                best = (m, cut)
    return best and best[1]


def raw_grams(item, row):
    qty, unit = item["base_qty"], item["base_unit"]
    if qty is None:
        return None
    if unit == "ea":
        return qty * row["each_g"] if row.get("each_g") else None
    return qty  # g, or ml taken as g


def protein_group(items, products, protein):
    """[(index, cut, raw grams as written)] for the cuts to scale, main first."""
    cuts = {k: v for k, v in protein.items() if isinstance(v, dict)}
    found = []
    for i, it in enumerate(items):
        cut = cut_of(it, products, cuts)
        grams = cut and raw_grams(it, cuts[cut])
        if grams:
            found.append((i, cut, grams))
    found.sort(key=lambda f: -f[2])
    return [f for f in found if f[2] >= SECOND_CUT * found[0][2]] if found else []


# --- step placement ------------------------------------------------------------

def place(items, steps, steps_for):
    """Set each item's 1-based step; return the names no step mentions."""
    step_words = [set(words(s)) for s in steps]

    def first(test):
        return next((n for n, ws in enumerate(step_words, 1) if test(ws)), None)

    fixed = {common.norm_name(k): v for k, v in (steps_for or {}).items()}
    unplaced = []
    for it in items:
        if it["key"] in fixed:
            it["step"] = int(fixed[it["key"]])
            continue
        sig = [w for w in it["words"] if w not in FILLER and not w.isdigit()] or it["words"]
        own = (first(lambda ws: set(sig) <= ws) or first(lambda ws: sig[-1] in ws)
               or first(lambda ws: ws & set(sig))) if sig else None
        group = [w for w in words(it["group"]) if w not in FILLER]
        claim = first(lambda ws: set(group) <= ws) if group else None
        found = [n for n in (own, claim) if n]
        it["step"] = min(found) if found else 1
        if not found:
            unplaced.append(it["name"])
    return unplaced


# --- swaps ---------------------------------------------------------------------

def swap_setting(row):
    """The settings id that turns a swaps.yaml row on or off: its id up to the first "-", so
    `butter` covers both `butter-frying` and `butter`. None for a row without an id."""
    return str(row["id"]).split("-")[0] if row.get("id") else None


def enabled_swaps(swaps, swap_ids=None):
    """The rows to apply: all of them when swap_ids is None, else those whose setting is in swap_ids
    (rows without an id always apply)."""
    if swap_ids is None:
        return list(swaps)
    on = set(swap_ids)
    return [r for r in swaps if swap_setting(r) is None or swap_setting(r) in on]


def swap_for(item, swaps, step_text):
    for row in swaps:
        if not mentions(item["words"], row.get("match")) or mentions(item["words"], row.get("not")):
            continue
        if row.get("when") == "frying" and not FRYING.search(step_text or ""):
            continue
        if row.get("when") not in (None, "frying"):
            continue
        return row
    return None


def notice(row, item):
    text = row.get("notice")
    over = row.get("notice_over") or {}
    if not text or (item["base_unit"] in over and (item["base_qty"] or 0) <= over[item["base_unit"]]):
        return None
    return text.format(name=item["original"], Name=item["original"][:1].upper() + item["original"][1:],
                       amount=item["text"])


# --- display rounding -------------------------------------------------------------

def nearest(x, step):
    return round(x / step) * step


def to_fraction(x, steps):
    whole = math.floor(x)
    frac = min(steps, key=lambda f: abs(x - whole - f))
    return whole + frac or min(s for s in steps if s)


def fraction_text(x):
    whole = math.floor(x + 1e-9)
    frac = next((t for f, t in FRACTIONS.items() if abs(x - whole - f) < 0.01), "")
    if not frac and x - whole > 0.01:
        return f"{x:g}"
    return " ".join(p for p in (str(whole) if whole else "", frac) if p) or "0"


def show(amount, unit, qty, base):
    """Scaled amount -> (rounded amount, display unit, text)."""
    if qty is None:
        return None, unit, ""
    spoon = SPOONS.get((unit or "").lower().rstrip("."))
    if spoon:
        steps = [0, 0.25, 1 / 3, 0.5, 2 / 3, 0.75, 1] if spoon == "cup" else [0, 0.25, 0.5, 0.75, 1]
        n = to_fraction(amount, steps if amount < 3 else [0, 0.5, 1])
        return n, spoon, f"{fraction_text(n)} {spoon}{'s' if spoon == 'cup' and n > 1 else ''}"
    if base in ("g", "ml"):
        step = 25 if base == "g" and qty > 500 else 5 if qty >= 10 else 1
        n = max(nearest(qty, step), 1)
        return n, base, f"{n:g} {base}"
    n = max(nearest(amount, 0.5), 0.5)
    return n, unit, f"{fraction_text(n)}{' ' + unit if unit else ''}"


# --- prepare --------------------------------------------------------------------

def kcal_estimate(published, before, after, saved):
    """Published kcal adjusted for the resized meat and the swaps; None when the result is implausible
    (a misread amount upstream), rather than a nonsense number."""
    if not published:
        return None
    kcal = round(published - before + after - saved)
    return kcal if 0.25 * published <= kcal <= 4 * published + 600 else None


def is_saucy(item):
    return mentions(item["words"], SAUCY)


def prepare(recipe, serves=6, products=None, swaps=None, protein=None, overrides=None, protein_g=None, swap_ids=None):
    products = config("products.yaml", {}) if products is None else products
    swaps = enabled_swaps(config("swaps.yaml", []) if swaps is None else swaps, swap_ids)
    protein = config("protein.yaml", {}) if protein is None else protein
    overrides = config("overrides.yaml", {}) if overrides is None else overrides
    fix = overrides.get(recipe.get("slug")) or {}
    steps = recipe.get("steps") or []
    factor = serves / (recipe.get("serves") or serves)

    items = []
    for ing in recipe["ingredients"]:
        qty, base = common.to_base(ing.get("amount"), ing.get("unit"))
        items.append({"name": ing["name"], "key": common.norm_name(ing["name"]), "words": words(ing["name"]),
                      "amount": ing.get("amount"), "unit": ing.get("unit"), "base_qty": qty, "base_unit": base,
                      "original": None, "note": None, "prep": ing.get("notes") or None,
                      "group": ing.get("group") or None, "is_protein": False, "removed": False})
    unplaced = place(items, steps, fix.get("steps_for"))

    group = protein_group(items, products, protein)
    target = (protein_g if protein_g is not None else protein.get("target_protein_g", 50)) * serves
    have = sum(g * protein[cut]["protein_per_100g"] / 100 for _, cut, g in group)
    meat_factor = target / have if have else None
    kcal_before = kcal_after = 0
    for i, cut, grams in group:
        it = items[i]
        it.update(is_protein=True, base_qty=grams * meat_factor, base_unit="g")
        kcal_before += grams * protein[cut]["kcal_per_100g"] / 100 / (recipe.get("serves") or serves)
        kcal_after += it["base_qty"] * protein[cut]["kcal_per_100g"] / 100 / serves
    for it in items:
        if not it["is_protein"] and it["base_qty"] is not None:
            k = factor * (SAUCE_EXTRA if is_saucy(it) else 1)
            it["plain_qty"] = it["base_qty"] * factor  # without the sauce extra, for the kcal estimate
            it["base_qty"] *= k
            it["amount"] *= k

    notes, kcal_saved = [], 0
    for it in items:
        row = None if it["is_protein"] else swap_for(it, swaps, steps[it["step"] - 1] if it["step"] <= len(steps) else "")
        if it["is_protein"]:
            it["amount"], it["unit"], it["text"] = show(it["base_qty"], "g", it["base_qty"], "g")
        else:
            it["amount"], it["unit"], it["text"] = show(it["amount"], it["unit"], it["base_qty"], it["base_unit"])
        if not row:
            continue
        it.update(original=it["name"], note=row.get("note"), removed=row.get("to") is None)
        if row.get("to"):
            ratio = row.get("ratio", 1)
            it["name"], it["key"] = row["to"], common.norm_name(row["to"])
            if it["base_qty"] is not None and ratio != 1:
                it["base_qty"] *= ratio
                it["amount"], it["unit"], it["text"] = show(it["amount"] * ratio, it["unit"], it["base_qty"], it["base_unit"])
        if (line := notice(row, it)) and line not in notes:
            notes.append(line)
        if it["base_unit"] in ("g", "ml"):
            kcal_saved += it.get("plain_qty", 0) * row.get("kcal_saved_per_100", 0) / 100 / serves
        if row.get("no_amount"):  # "spray oil", not "45 ml spray oil"
            it.update(amount=None, unit=None, text="", base_qty=None, base_unit=None)

    published = (recipe.get("nutrition") or {}).get("calories")
    meat_g = sum(items[i]["base_qty"] for i, _, _ in group) / serves if group else None
    for it in items:
        del it["words"]
        it.pop("plain_qty", None)
        if it["amount"] is not None:
            it["amount"] = round(it["amount"], 3)
    return {
        "slug": recipe.get("slug"), "title": recipe.get("title"), "url": recipe.get("url"),
        "serves": serves, "recipe_serves": recipe.get("serves"),
        "cut": group[0][1] if group else None, "cuts": list(dict.fromkeys(c for _, c, _ in group)),
        "per_serve": {"protein_g": round(target / serves) if group else None,
                      "meat_g": round(meat_g) if meat_g else None,
                      "kcal": kcal_estimate(published, kcal_before, kcal_after, kcal_saved)},
        "ingredients": items,
        "steps": [{"n": n, "text": s, "ingredients": [i for i, it in enumerate(items) if it["step"] == n]}
                  for n, s in enumerate(steps, 1)],
        "notes": notes,
        "unplaced": unplaced,
    }


# --- output --------------------------------------------------------------------

def label(it):
    if it["removed"]:
        return f"{it['name']} (removed)"
    return f"{it['name']} ({it['original']})" if it["original"] else it["name"]


def as_text(t):
    ps = t["per_serve"]
    out = [f"{t['title']}: {t['serves']} serves",
           f"Per serve: {ps['protein_g'] or '?'} g protein ({t['cut'] or 'no main cut'}), "
           f"{ps['meat_g'] or '?'} g raw meat, {ps['kcal'] or '?'} kcal"]
    out += [f"* {n}" for n in t["notes"]] + [""]
    ings = t["ingredients"]
    aw = max([len(i["text"]) for i in ings] + [6])
    nw = min(max([len(label(i)) for i in ings] + [10]), 40)
    sw = 64
    rule = f"+-{'-' * aw}-+-{'-' * nw}-+-{'-' * sw}-+"
    out.append(rule)
    for s in t["steps"] or [{"n": 1, "text": "", "ingredients": list(range(len(ings)))}]:
        left = [(ings[i]["text"], label(ings[i])) for i in s["ingredients"]]
        right = textwrap.wrap(f"{s['n']}. {s['text']}", sw)
        for l, r in itertools.zip_longest(left, right, fillvalue=None):
            a, name = l or ("", "")
            for k, chunk in enumerate(textwrap.wrap(name, nw) or [""]):
                out.append(f"| {(a if k == 0 else ''):>{aw}} | {chunk:<{nw}} | {(r or '') if k == 0 else '':<{sw}} |")
        out.append(rule)
    return "\n".join(out)


CELL = 'style="border:1px solid #ccc;padding:4px 8px;vertical-align:top"'


def as_html(t):
    esc = html.escape
    ings = t["ingredients"]
    rows = []
    for s in t["steps"]:
        idx = s["ingredients"] or [None]
        for k, i in enumerate(idx):
            if i is None:
                cells = f"<td {CELL}></td><td {CELL}></td>"
            else:
                it = ings[i]
                name = esc(it["name"]) + (f" <small>({esc(it['original'])})</small>" if it["original"] and not it["removed"] else "")
                name = f"<s>{name}</s>" if it["removed"] else name
                note = f"<br><small><i>{esc(it['note'])}</i></small>" if it["note"] and not it["removed"] else ""
                cells = f'<td {CELL} align="right">{esc(it["text"])}</td><td {CELL}>{name}{note}</td>'
            step = f'<td {CELL} rowspan="{len(idx)}">{s["n"]}. {esc(s["text"])}</td>' if k == 0 else ""
            rows.append(f"<tr>{cells}{step}</tr>")
    caption = "".join(f"<div>{esc(n)}</div>" for n in t["notes"])
    return (f'<table style="border-collapse:collapse">'
            + (f'<caption style="text-align:left">{caption}</caption>' if caption else "")
            + "<tbody>" + "".join(rows) + "</tbody></table>")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("recipe", help="slug in data/recipes, or a recipe .json path")
    ap.add_argument("--serves", type=int, default=6)
    ap.add_argument("--format", choices=["text", "json", "html"], default="text")
    args = ap.parse_args()
    path = pathlib.Path(args.recipe)
    recipe = common.load(path if path.suffix == ".json" else common.RECIPES / f"{args.recipe}.json")
    if recipe is None:
        sys.exit(f"no recipe {args.recipe}")
    table = prepare(recipe, args.serves)
    print({"text": as_text, "html": as_html, "json": lambda t: json.dumps(t, indent=1)}[args.format](table))


if __name__ == "__main__":
    main()
