"""Score the recipe pool, offer each person their options, price every pick at their stores.

Usage: .venv/bin/python shopping/meals/plan_week.py [--week YYYY-MM-DD] [--person KEY] [--seed N] [--dry-run] [--allow-stale]

Per person in people.yaml with a plan (or just --person KEY): reads data/recipes/_pool.json, the
week's price snapshot, products.yaml, overrides.yaml and data/people/<key>/{history,tuning,freezer,
settings}.json; offers max(4, 2 x recipes) options, prices every combination of `recipes` of them
at the person's stores, writes data/people/<key>/weeks/<week>.json and records the offered slugs in
their history.json. TJ's files from before people.yaml are moved into his folder first (once).
Without a people.yaml it plans once from data/ as before. Rules are in ../PLAN.md, shapes in
SCHEMA.md ("Per person").

Without --allow-stale a missing snapshot for the week exits non-zero so the scheduler
retries; with it, each store falls back to its latest earlier good snapshot.
"""
import argparse
import datetime
import itertools
import math
import random
import re
import sys

import yaml

import common
from fetch_prices import STORES  # every per-store structure here follows this one tuple

SERVES = 6

# Scoring. Every term is in points; cost is converted at DOLLARS_PER_POINT per PROTEIN_TARGET_G
# of protein (a serve at the target), so one "again" (+1) outweighs a recipe $2 a serve cheaper
# (about $24 over the week's 12 meals), and the smaller nudges below are worth tens of cents to a
# dollar a serve. Comparing on cost per 50 g of protein rather than per serve means meat cooked in
# from whole packs (which lifts protein above the target) counts as value, not as a dearer serve.
DOLLARS_PER_POINT = 2.0     # per the person's protein target (PROTEIN_TARGET_G by default)
RATING_WEIGHT = 1.0        # times tuning score (again 1, fine 0), faded over fade_days
STYLE_WEIGHT = 1.0         # times tuning.styles weight times similar_strength
CUT_PENALTY = 0.5          # per each of the last three weeks the cut was picked
FREEZER_BONUS = 0.5        # the cut is in the freezer
SITE_RATING_WEIGHT = 0.2   # per star above SITE_RATING_FLOOR (most of the site is 4.8+)
SITE_RATING_FLOOR = 4.0
DEFAULT_SIMILAR = 0.3
DEFAULT_FADE_DAYS = 365

RECENT_DAYS = 28           # picked this recently: not offered
CUT_DAYS = 21              # window for CUT_PENALTY
MAX_PER_CUT = 2
MIN_STYLES = 3
SPLIT_SAVING = 10.0        # split across two stores only when it saves this much over the best single store
BULK_DROP = 0.20           # bulk meat alert: this far below the best recent price per kg
BULK_WEEKS = 8
SMALL_AMOUNT = 40         # g or ml: an unmapped ingredient this small is assumed on hand
TASTE_PACK = 0.5           # share of a pack assumed for a non-pantry item with no amount
FREEZE_STEP_G = 500        # a freezable leftover is frozen in whole multiples of this; the rest is cooked this week

# Relaxed in this order, cumulatively, when four can't be found.
RELAXATIONS = [
    ("styles: 2 distinct", {"min_styles": 2}),
    ("styles: any", {"min_styles": 1}),
    ("cut: 3 may share", {"max_per_cut": 3}),
    ("cut: any", {"max_per_cut": math.inf}),
    ("new slot dropped", {"new_slot": False}),
    ("recent: picked in the last four weeks allowed", {"allow_recent": True}),
]

# Grams per item for things recipes count and shops weigh (or the reverse).
# A products.yaml entry overrides with each_g; ml_per_g converts volume to weight.
EACH_G = {
    "onion": 150, "brown onion": 150, "red onion": 150, "egg": 50, "eggs": 50,
    "carrot": 120, "potato": 200, "tomato": 120, "zucchini": 200, "capsicum": 160,
    "red capsicum": 160, "lemon": 120, "lime": 70,
}

WORDS = {4: "four", 6: "six"}
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def days_between(a, b):
    return (datetime.date.fromisoformat(b) - datetime.date.fromisoformat(a)).days


def load_yaml(path):
    return (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}


# Protein per serve that recipe_table sizes the main protein to; costs are compared per this much protein.
PROTEIN_TARGET_G = load_yaml(common.ROOT / "protein.yaml").get("target_protein_g", 50)


def cost_per_protein(effective, serves, protein_g, target=None):
    """Effective cost per `target` g of protein (the person's protein target, PROTEIN_TARGET_G by
    default): effective / serves * target / protein_g a serve. None when the cost or the protein is unknown."""
    if effective is None or not protein_g or protein_g <= 0:
        return None
    return round(effective / (serves or SERVES) * (target or PROTEIN_TARGET_G) / protein_g, 2)


def load_products(path):
    """products.yaml by normalised name; `same_as: other name` entries take that entry's fields."""
    table = {common.norm_name(str(k)): v or {} for k, v in load_yaml(path).items()}
    for key, entry in table.items():
        seen = {key}
        while "same_as" in entry:
            target = common.norm_name(entry["same_as"])
            if target in seen or target not in table:
                raise ValueError(f"products.yaml: {key!r} same_as {entry['same_as']!r} doesn't resolve")
            seen.add(target)
            entry = {**table[target], **{k: v for k, v in entry.items() if k != "same_as"}}
        table[key] = entry
    return table


def pool_entries(raw):
    """_pool.json ({"pool": {slug: {"style": …}}, …}, or a plain list or dict) as (slug, style) pairs."""
    if isinstance(raw, dict):
        raw = raw.get("pool", raw)
    if isinstance(raw, dict):
        return [(s, v.get("style") if isinstance(v, dict) else v) for s, v in raw.items()]
    return [(r, None) if isinstance(r, str) else (r["slug"], r.get("style")) for r in raw or []]


# --- prices ---------------------------------------------------------------

def index(store):
    return {str(p["id"]): p for p in store.get("products", [])}


def snapshots(prices_dir, before):
    """Earlier snapshot paths, newest first."""
    paths = [p for p in prices_dir.glob("*.json") if DATE.match(p.stem) and p.stem < before]
    return sorted(paths, key=lambda p: p.stem, reverse=True)


def load_prices(week, prices_dir, allow_stale, stores=STORES):
    """Catalog per store (None if no data at all), the week each came from, and fresh flags.

    A store missing from the week's snapshot, or there with ok: false, falls back to its latest
    earlier good snapshot; a store with no good snapshot at all gets None and is left out of the costing.
    A store not in `stores` (the person doesn't shop there) gets None too; its fresh flag is still reported."""
    current = common.load(prices_dir / f"{week}.json")
    if current is None and not allow_stale:
        raise SystemExit(f"no price snapshot for {week}; rerun later or with --allow-stale")
    earlier = [(p.stem, common.load(p)) for p in snapshots(prices_dir, week)]
    catalogs, used, fresh = {}, {}, {}
    for s in STORES:
        now = ((current or {}).get("stores") or {}).get(s) or {}
        fresh[s] = bool(now.get("ok"))
        if s not in stores:
            catalogs[s] = used[s] = None
            continue
        src = (week, now) if fresh[s] else next(
            ((w, snap["stores"][s]) for w, snap in earlier if (snap.get("stores") or {}).get(s, {}).get("ok")), None)
        catalogs[s] = index(src[1]) if src else None
        used[s] = src[0] if src else None
    if not any(catalogs.values()):
        raise SystemExit("no usable prices for any store")
    return catalogs, used, fresh


def per_kg_of(p):
    if p.get("per_kg"):
        return p["price"]
    if p.get("size_unit") == "g" and p.get("size_qty"):
        return p["price"] / p["size_qty"] * 1000
    return None


def convert(qty, unit, want, entry, key):
    """Recipe quantity in `unit` -> `want` (g, ml or ea), or None without a sound conversion."""
    if unit == want:
        return qty
    each = entry.get("each_g", EACH_G.get(key))
    ml_per_g = entry.get("ml_per_g")
    grams = {"g": qty, "ea": qty * each if each else None,
             "ml": qty / ml_per_g if ml_per_g else None}.get(unit)
    if grams is None:
        return None
    if want == "g":
        return grams
    if want == "ea":
        return grams / each if each else None
    return grams * ml_per_g if want == "ml" and ml_per_g else None


def small(qty, unit):
    """A garnish-sized amount: no amount given, or at most SMALL_AMOUNT g or ml."""
    return qty is None or (unit in ("g", "ml") and qty <= SMALL_AMOUNT)


def lines_of(slug, prepared, products):
    """Non-removed ingredients as shopping lines in base units, keyed by their products.yaml entry.

    A name with no entry is `assumed` on hand when the amount is small, so a tablespoon of
    something unusual doesn't keep a recipe off the list; a larger one stays unmatched.
    """
    lines = []
    for ing in prepared.get("ingredients", []):
        if ing.get("removed"):
            continue
        name_key = ing.get("key") or common.norm_name(ing["name"])
        key = common.resolve(name_key, products) or name_key
        qty, unit = ing.get("base_qty"), ing.get("base_unit")
        cut = (products.get(key) or {}).get("cut") or (prepared.get("cut") if ing.get("is_protein") else None)
        lines.append({"key": key, "name": ing["name"], "qty": qty, "unit": unit, "cut": cut, "for": slug,
                      "assumed": key not in products and not ing.get("is_protein") and small(qty, unit)})
    return lines


def apply_freezer(lines, freezer, products):
    """Take what's in the freezer off the lines for that cut (or ingredient), once across all the lines given."""
    left = {}
    for f in freezer:
        left[f["cut"]] = left.get(f["cut"], 0) + (f.get("grams") or 0)
    out = []
    for line in lines:
        cut = line["cut"] or line["key"]
        if left.get(cut, 0) > 0 and line["qty"]:
            grams = convert(line["qty"], line["unit"], "g", products.get(line["key"]) or {}, line["key"])
            if grams:
                take = min(grams, left[cut])
                left[cut] -= take
                line = {**line, "qty": line["qty"] * (grams - take) / grams, "frozen_g": take}
        out.append(line)
    return out


def candidates(entry, store):
    """A products.yaml entry's product ids at a store: one id or a list of them."""
    ids = entry.get(store)
    return [] if ids is None else [str(i) for i in (ids if isinstance(ids, list) else [ids])]


def freezable(entry):
    return bool(entry.get("cut") or entry.get("freezes"))


def need_id(key, products):
    """Lines with the same id are one need: same candidates at every store (same_as included)."""
    entry = products.get(key)
    return key if entry is None else repr([candidates(entry, s) for s in STORES])


def qty_in(line, entry, p):
    """A line's quantity in product p's unit, or None."""
    if line["qty"] is None:
        return convert(1, "ea", "g", entry, line["key"]) if p.get("per_kg") else TASTE_PACK * p["size_qty"]
    if line["unit"] == p["size_unit"] == "ea" and entry.get("count_per_pack"):
        return line["qty"] / entry["count_per_pack"]
    return convert(line["qty"], line["unit"], p["size_unit"], entry, line["key"])


def best_product(cands, lines, freeze):
    """(product, qty) for the cheapest candidate: by used cost if the leftover freezes, else by checkout."""
    best = None
    for p in cands:
        qtys = [qty_in(line, entry, p) for line, entry in lines]
        if None in qtys:
            continue
        i = item(p, sum(qtys), ())
        rank = (i["used"], i["cost"]) if freeze else (i["cost"], i["used"])
        if best is None or (rank, i["id"]) < best[0]:
            best = ((rank, i["id"]), p, sum(qtys))
    return best and best[1:]


def grams_per_unit(unit, entry, key):
    """Grams in one of a product's units (one store item for `ea`), or None without a sound conversion."""
    if unit == "ea":
        return convert(entry.get("count_per_pack") or 1, "ea", "g", entry, key)
    return convert(1, unit, "g", entry, key)


def price(lines, store, products, catalog):
    """Whole-pack checkout at one store: each need (lines merged per entry) bought as its cheapest candidate.

    Returns {total, used, freeze_value, effective, items, freeze, extra, unmatched, missing}; the costs
    are None if any line can't be priced. `effective` is checkout less the value of what goes in the
    freezer. A freezable leftover is split: whole FREEZE_STEP_G multiples are frozen, the rest is cooked
    this week and reported in `extra` ({slug: [{name, cut, id, grams_total, …}]}), shared between the
    recipes that need the item in proportion to how much each needs.
    """
    if catalog is None:
        return {"total": None, "used": None, "freeze_value": None, "effective": None,
                "items": [], "freeze": [], "extra": {}, "unmatched": [], "missing": []}
    needs, unmatched, missing = {}, set(), set()
    for line in lines:
        key, entry = line["key"], products.get(line["key"])
        if line.get("assumed"):
            continue
        if entry is None:
            unmatched.add(key)
            continue
        if entry.get("pantry") or line["qty"] == 0:
            continue
        cands = [catalog[i] for i in candidates(entry, store) if i in catalog]
        if not cands:
            missing.add(key)
            continue
        needs.setdefault(need_id(key, products), {"cands": cands, "lines": []})["lines"].append((line, entry))

    chosen = {}
    for n in needs.values():
        freeze = any(freezable(e) for _, e in n["lines"])
        got = best_product(n["cands"], n["lines"], freeze)
        if got is None:
            unmatched |= {line["key"] for line, _ in n["lines"]}
            continue
        p, qty = got
        c = chosen.setdefault(str(p["id"]), {"product": p, "qty": 0, "for": set(), "freeze": False,
                                             "line": n["lines"][0], "lines": []})
        c["qty"] += qty
        c["for"] |= {line["for"] for line, _ in n["lines"]}
        c["freeze"] |= freeze
        c["lines"] += [(line, entry, q) for (line, entry), q in
                       zip(n["lines"], (qty_in(line, entry, p) for line, entry in n["lines"]))]

    made = []
    for c in chosen.values():
        line, entry = c["line"]
        g = grams_per_unit(c["product"]["size_unit"], entry, line["key"]) if c["freeze"] else None
        made.append((item(c["product"], c["qty"], c["for"], c["freeze"], g), c))
    made.sort(key=lambda m: m[0]["name"])
    items = [i for i, _ in made]
    ok = not unmatched and not missing
    total = round(sum(i["cost"] for i in items), 2) if ok else None
    value = round(sum(i["freeze_value"] for i in items), 2) if ok else None
    return {"total": total, "used": round(sum(i["used"] for i in items), 2) if ok else None,
            "freeze_value": value, "effective": round(total - value, 2) if ok else None,
            "items": items, "freeze": freeze_list(items, chosen), "extra": extra_by_recipe(made),
            "unmatched": sorted(unmatched), "missing": sorted(missing)}


def extra_by_recipe(made):
    """{slug: [{name, cut, id, grams_total, bought_g, frozen_g, shared}]}: each item's cooked extra shared by how much each recipe needs."""
    out = {}
    for i, c in made:
        if not i["extra_g"]:
            continue
        need = {}
        for line, entry, q in c["lines"]:
            need.setdefault(line["for"], [0, line, entry])[0] += q or 0
        whole = sum(n for n, _, _ in need.values())
        line, entry = c["line"]
        g = grams_per_unit(i["unit"], entry, line["key"])
        bought = i["packs"] * c["product"]["size_qty"] * g if g and i["packs"] else None
        for slug, (n, first, e) in sorted(need.items()):
            share = n / whole if whole else 1 / len(need)
            if share > 0:
                out.setdefault(slug, []).append({
                    "name": first["name"], "cut": e.get("cut") or first.get("cut"), "id": i["id"],
                    "grams_total": round(i["extra_g"] * share, 1), "bought_g": bought and round(bought),
                    "frozen_g": i["freeze_g"] or 0, "shared": len(need) > 1})
    return out


def freeze_list(items, chosen):
    """What goes in the freezer, in grams (whole FREEZE_STEP_G multiples), for the card's "Add to freezer";
    leftovers that won't convert to grams are left out."""
    out = []
    for i in items:
        line, entry = chosen[i["id"]]["line"]
        g = grams_per_unit(i["unit"], entry, line["key"]) if i["freeze_qty"] else None
        if g and i["freeze_g"]:
            out.append({"cut": entry.get("cut") or line["key"], "grams": int(i["freeze_g"]), "name": i["name"]})
    return out


def split_spare(spare, g_per_unit):
    """A freezable leftover (product units) -> (units frozen, grams frozen, grams cooked this week).

    Frozen is the largest multiple of FREEZE_STEP_G that fits; the rest is cooked. A leftover that
    can't be put in grams is all frozen, as before (grams None)."""
    if not spare:
        return 0, 0, 0
    if not g_per_unit:
        return spare, None, 0
    grams = spare * g_per_unit
    frozen = math.floor(grams / FREEZE_STEP_G + 1e-9) * FREEZE_STEP_G
    return frozen / g_per_unit, frozen, max(0.0, grams - frozen)


def qty_text(qty, unit):
    """A list quantity as it reads: "1.6 kg", "450 g", "2 L", "250 ml", "3"."""
    if qty is None:
        return ""
    if unit in ("g", "ml"):
        big = "kg" if unit == "g" else "L"
        if qty >= 1000:
            return f"{math.floor(qty / 100 + 0.5) / 10:g} {big}"
        step = 5 if qty >= 100 else 1
        return f"{max(1, math.floor(qty / step + 0.5) * step):g} {unit}"
    return f"{math.floor(qty * 10 + 0.5) / 10:g}"


def item(p, qty, for_, freeze=False, g_per_unit=None):
    """One list line. With `freeze`, the spare from whole packs is split by split_spare: the frozen part
    is `freeze_qty`/`freeze_g`/`freeze_value`, the cooked part `extra_g`, which counts as used."""
    if p.get("per_kg"):
        packs, cost = None, qty * p["price"] / 1000
        used, spare = cost, 0
    else:
        packs = math.ceil(qty / p["size_qty"] - 1e-9)
        cost = packs * p["price"]
        spare = max(0, packs * p["size_qty"] - qty) if freeze else 0
    frozen, frozen_g, extra_g = split_spare(spare, g_per_unit)
    value = frozen / p["size_qty"] * p["price"] if frozen else 0
    if not p.get("per_kg"):  # pro rata, counting the cooked extra as used
        used = (qty + spare - frozen) / p["size_qty"] * p["price"]
    return {"name": p["name"], "id": str(p["id"]), "packs": packs, "price": p["price"],
            "cost": round(cost, 2), "used": round(used, 2), "qty": round(qty, 1),
            "qty_text": qty_text(qty, p["size_unit"]),
            "unit": p["size_unit"], "per_kg": bool(p.get("per_kg")), "special": bool(p.get("special")),
            "usual_price": p.get("usual_price"), "for": sorted(for_),
            "freeze_qty": round(frozen, 1), "freeze_g": frozen_g, "freeze_value": round(value, 2),
            "extra_g": round(extra_g, 1)}


def per_serve_with(base, extras, protein, serves=SERVES):
    """A recipe's per-serve figures with the cooked extra added: {meat_g, protein_g, kcal, extra}.

    Extra of a protein.yaml cut adds its raw weight to meat_g and its protein and kcal; extra with
    no known cut is only listed. The recipe's own per_serve is left alone."""
    base = base or {}
    out = {"meat_g": base.get("meat_g"), "protein_g": base.get("protein_g"), "kcal": base.get("kcal"), "extra": []}
    meat = prot = kcal = 0.0
    for e in extras or []:
        per = e["grams_total"] / serves
        out["extra"].append({**e, "grams_per_serve": round(per, 1)})
        row = protein.get(e.get("cut")) if e.get("cut") else None
        if isinstance(row, dict):
            meat += per
            prot += per * row.get("protein_per_100g", 0) / 100
            kcal += per * row.get("kcal_per_100g", 0) / 100
    if meat:
        out["meat_g"] = round((out["meat_g"] or 0) + meat)
        out["protein_g"] = round((out["protein_g"] or 0) + prot)
        out["kcal"] = round(out["kcal"] + kcal) if out["kcal"] is not None else None
    return out


# --- options --------------------------------------------------------------

def cost_option(slug, prepared, ctx):
    lines = lines_of(slug, prepared, ctx["products"])
    priced = {s: price(apply_freezer(lines, ctx["freezer"], ctx["products"]), s, ctx["products"], ctx["catalogs"].get(s))
              for s in STORES}
    serves = prepared.get("serves") or SERVES
    by_store = {s: per_serve_with(prepared.get("per_serve"), priced[s]["extra"].get(slug), protein_of(ctx), serves)
                if priced[s]["total"] is not None else None for s in STORES}
    base_protein = (prepared.get("per_serve") or {}).get("protein_g")
    unmatched = sorted({k for p in priced.values() for k in p["unmatched"]}
                       | {l["key"] for l in lines if l["key"] not in ctx["products"] and not l.get("assumed")})
    return {
        "lines": lines,
        "cost": {s: priced[s]["total"] for s in STORES},
        "cost_used": {s: priced[s]["used"] for s in STORES},
        "cost_effective": {s: priced[s]["effective"] for s in STORES},
        "specials": [{"store": s, "name": i["name"], "price": i["price"], "usual_price": i["usual_price"]}
                     for s in STORES for i in priced[s]["items"] if i["special"]],
        "unmatched": unmatched,
        "assumed": sorted({l["name"] for l in lines if l.get("assumed")}),
        "missing": {s: priced[s]["missing"] for s in STORES if priced[s]["missing"]},
        "per_serve_by_store": by_store,
        "cost_per_50g_protein": {
            s: cost_per_protein(priced[s]["effective"], serves, ((by_store[s] or {}).get("protein_g") or base_protein),
                                ctx.get("protein_target"))
            for s in STORES},
    }


def protein_of(ctx):
    """protein.yaml cuts, from ctx or the default config folder."""
    if "protein" not in ctx:
        ctx["protein"] = load_yaml(common.ROOT / "protein.yaml")
    return ctx["protein"]


def history_view(history, week):
    """Per slug: weeks picked before `week`. Per cut weeks are worked out later."""
    picked = {}
    for w in history.get("weeks", []):
        if w.get("week", "") < week:
            for slug in w.get("picked") or []:
                picked.setdefault(slug, set()).add(w["week"])
    return picked


def fade(entry, week, fade_days):
    if not fade_days or not entry.get("date"):
        return 1.0
    return max(0.0, 1 - max(0, days_between(entry["date"], week)) / fade_days)


def score_terms(c, ctx):
    """Named score terms for a costed candidate; higher is better."""
    tuning, week = ctx["tuning"], ctx["week"]
    serves = c["serves"] or SERVES
    per_protein = c.get("cost_per_50g_protein") or {}
    # Per store: cost per PROTEIN_TARGET_G of protein, or plain cost per serve when the protein is unknown.
    costs = [per_protein.get(s) if per_protein.get(s) is not None else v / serves
             for s, v in (c.get("cost_effective") or c["cost"]).items() if v is not None]
    rated = tuning.get("recipes", {}).get(c["slug"], {})
    styles = {str(k).lower(): v for k, v in (tuning.get("styles") or {}).items()}
    similar = tuning.get("similar_strength", DEFAULT_SIMILAR)
    tags = [c["style"]] + c["cuisines"]
    weight = sum(styles.get(str(t).lower(), 0) for t in tags if t)
    return {
        "cost": -min(costs) / DOLLARS_PER_POINT,
        "rating": RATING_WEIGHT * rated.get("score", 0) * fade(rated, week, tuning.get("fade_days", DEFAULT_FADE_DAYS)),
        "style": STYLE_WEIGHT * similar * weight,
        "cut": 0.0 - CUT_PENALTY * ctx["cut_weeks"].get(c["cut"], 0),
        "freezer": FREEZER_BONUS if c["cut"] and ctx["frozen"].get(c["cut"], 0) > 0 else 0.0,
        "site": SITE_RATING_WEIGHT * max(0.0, (c.get("rating") or 0) - SITE_RATING_FLOOR),
    }


def cut_weeks(picked, cut_of, week):
    """cut -> number of the last three weeks in which it was picked."""
    weeks = {}
    for slug, ws in picked.items():
        cut = cut_of(slug)
        for w in ws:
            if cut and 0 < days_between(w, week) <= CUT_DAYS:
                weeks.setdefault(cut, set()).add(w)
    return {cut: len(ws) for cut, ws in weeks.items()}


def ranked(cands, rng):
    """Best first; equal scores in an order fixed by the seed."""
    tie = {c["slug"]: rng.random() for c in sorted(cands, key=lambda c: c["slug"])}
    return sorted(cands, key=lambda c: (-round(c["score"], 6), tie[c["slug"]]))


def greedy(order, min_styles, max_per_cut, new_slot, n=4):
    chosen = []
    if new_slot:
        first = next((c for c in order if c["new"]), None)
        chosen += [first] if first else []
    for c in order:
        if len(chosen) == n:
            break
        if c in chosen:
            continue
        if c["cut"] and sum(o["cut"] == c["cut"] for o in chosen) >= max_per_cut:
            continue
        styles = {o["style"] for o in chosen} | {c["style"]}
        if len(styles) + n - len(chosen) - 1 < min_styles:
            continue
        chosen.append(c)
    return sorted(chosen, key=order.index)


def choose(cands, rng, n=4):
    """`n` options and the list of constraints that had to be relaxed to find them."""
    order = ranked(cands, rng)
    rules = {"min_styles": MIN_STYLES, "max_per_cut": MAX_PER_CUT, "new_slot": True, "allow_recent": False}
    relaxed = []
    for label, change in [(None, {})] + RELAXATIONS:
        rules.update(change)
        relaxed += [label] if label else []
        pool = [c for c in order if rules["allow_recent"] or not c["recent"]]
        got = greedy(pool, rules["min_styles"], rules["max_per_cut"], rules["new_slot"], n)
        if len(got) == n:
            return got, relaxed
    return got, relaxed + [f"fewer than {WORDS.get(n, n)} options"]


# --- pairs and alerts -----------------------------------------------------

def store_entry(p, per_serve):
    keep = ("total", "freeze_value", "effective", "items", "freeze")
    return {**{k: p[k] for k in keep}, "per_serve": per_serve(p["extra"])} if p["total"] is not None else None


def split_with(lines, stores, ctx, per_serve):
    """Each need from whichever of `stores` is cheaper for it after freezing; None unless they cover everything."""
    pick = {s: [] for s in stores}
    nid = lambda l: need_id(l["key"], ctx["products"])  # noqa: E731
    for _, group in itertools.groupby(sorted(lines, key=nid), key=nid):
        group = list(group)
        costs = {s: price(group, s, ctx["products"], ctx["catalogs"][s])["effective"] for s in stores}
        costs = {s: v for s, v in costs.items() if v is not None}
        if not costs:
            return None
        pick[min(costs, key=costs.get)] += group  # ties go to the first store in STORES order
    parts = {s: price(pick[s], s, ctx["products"], ctx["catalogs"][s]) for s in stores}
    total, value = (round(sum(p[k] for p in parts.values()), 2) for k in ("total", "freeze_value"))
    extra = {}
    for s in stores:
        for slug, es in parts[s]["extra"].items():
            extra.setdefault(slug, []).extend(es)
    return {"stores": list(stores), "total": total, "freeze_value": value, "effective": round(total - value, 2),
            "items": {s: parts[s]["items"] for s in stores},
            "freeze": [f for s in stores for f in parts[s]["freeze"]], "per_serve": per_serve(extra)}


def split(lines, ctx, per_serve=lambda extra: {}):
    """The cheapest two-store split (by effective cost) over every pair of stores with prices, never three;
    None if no pair of stores covers everything. `stores` names the two, in STORES order."""
    have = [s for s in STORES if ctx["catalogs"].get(s) is not None]
    best = None
    for stores in itertools.combinations(have, 2):
        got = split_with(lines, stores, ctx, per_serve)
        if got and (best is None or got["effective"] < best["effective"] - 1e-9):
            best = got
    return best


def options_wanted(recipes):
    """Options offered for a person cooking `recipes` recipes a week."""
    return max(4, 2 * recipes)


def pick_key(opts):
    return "+".join(sorted(o["slug"] for o in opts))


def price_pair(a, b, ctx):
    return price_pick((a, b), ctx)


def price_pick(opts, ctx):
    """One pick (1 to 3 recipes) bought together: an entry per store, the split and the choice."""
    lines = apply_freezer([l for o in opts for l in o["lines"]], ctx["freezer"], ctx["products"])
    base, serves = {}, {}
    for o in opts:
        for slug in {l["for"] for l in o["lines"]} | ({o["slug"]} if o.get("slug") else set()):
            base[slug], serves[slug] = o.get("per_serve") or {}, o.get("serves") or SERVES

    def per_serve(extra):
        """{slug: adjusted per-serve figures} for each recipe, given a priced store's `extra`."""
        return {slug: per_serve_with(base[slug], extra.get(slug), protein_of(ctx), serves[slug]) for slug in sorted(base)}

    out = {}
    for s in STORES:
        out[s] = store_entry(price(lines, s, ctx["products"], ctx["catalogs"].get(s)), per_serve)
    out["split"] = split(lines, ctx, per_serve)
    singles = {s: out[s]["effective"] for s in STORES if out[s]}
    best = min(singles, key=singles.get) if singles else None  # ties to the first in STORES order
    if out["split"] and (best is None or singles[best] - out["split"]["effective"] >= SPLIT_SAVING - 1e-9):
        best = "split"
    out["choice"] = best
    return out


def bulk_alerts(week, products, catalogs, fresh, prices_dir):
    """Products for a cut priced at least BULK_DROP below that cut's best per kg in the last BULK_WEEKS snapshots."""
    ids = {}
    for e in products.values():
        for s in STORES:
            if e.get("cut") and candidates(e, s):
                ids.setdefault(e["cut"], {}).setdefault(s, set()).update(candidates(e, s))
    best = {}
    for path in snapshots(prices_dir, week)[:BULK_WEEKS]:
        stores = common.load(path).get("stores") or {}
        for s in STORES:
            if not (stores.get(s) or {}).get("ok"):
                continue
            cat = index(stores[s])
            for cut, by_store in ids.items():
                for pid in by_store.get(s, ()):
                    pk = per_kg_of(cat[pid]) if pid in cat else None
                    if pk is not None:
                        best[cut] = min(best.get(cut, pk), pk)
    alerts = []
    for cut, by_store in sorted(ids.items()):
        for s in STORES:
            if cut not in best or not fresh.get(s) or not catalogs.get(s):
                continue
            for pid in sorted(by_store.get(s, ())):
                p = catalogs[s].get(pid)
                pk = per_kg_of(p) if p else None
                if pk is not None and pk <= (1 - BULK_DROP) * best[cut] + 1e-9:
                    alerts.append({"kind": "bulk_meat", "store": s, "cut": cut, "id": pid, "name": p["name"],
                                   "per_kg": round(pk, 2), "best_per_kg": round(best[cut], 2)})
    return alerts


# --- the run --------------------------------------------------------------

def default_prepare():
    import recipe_table
    return recipe_table.prepare


def swap_ids(settings, rows):
    """The swap settings ids a person has on: every id in swaps.yaml (as recipe_table.swap_setting
    groups them) not turned off in their settings."""
    import recipe_table
    off = {k for k, v in (settings.get("swaps") or {}).items() if not v}
    return sorted({recipe_table.swap_setting(r) for r in rows or [] if r.get("id")} - off)


def own_dir(root, person=None, data_dir=None):
    """Where a person's history, tuning, freezer, settings and weeks live: data/people/<key>/,
    or data/ itself when planning without people."""
    return data_dir or (common.person_dir(root, person) if person else root / "data")


def plan(week, seed=None, allow_stale=False, prepare=None, root=common.ROOT, person=None, data_dir=None):
    """The week's options as the dict written to <own dir>/weeks/<week>.json (see own_dir).

    `data_dir` overrides where the person's own files are read from (a dry run before migration)."""
    prepare = prepare or default_prepare()
    data, own = root / "data", own_dir(root, person, data_dir)
    seed = week if seed is None else seed
    settings = common.settings_of(common.load(own / "settings.json"), STORES)
    catalogs, used, fresh = load_prices(week, data / "prices", allow_stale, settings["stores"])
    products = load_products(root / "products.yaml")
    overrides = load_yaml(root / "overrides.yaml")
    tuning = common.load(own / "tuning.json", {})
    freezer = common.load(own / "freezer.json", [])
    picked = history_view(common.load(own / "history.json", {"weeks": []}), week)
    frozen = {}
    for f in freezer:
        frozen[f["cut"]] = frozen.get(f["cut"], 0) + (f.get("grams") or 0)
    ctx = {"week": week, "catalogs": catalogs, "products": products, "freezer": freezer,
           "frozen": frozen, "tuning": tuning, "protein_target": settings["protein_g"],
           "protein": load_yaml(root / "protein.yaml") or load_yaml(common.ROOT / "protein.yaml")}
    serves = settings["serves"]
    swaps_on = swap_ids(settings, common.load_yaml(common.ROOT / "swaps.yaml", []))

    cands, cuts, no_entry, tags = [], {}, set(), set()
    for slug, style in pool_entries(common.load(data / "recipes" / "_pool.json", [])):
        over = overrides.get(slug) or {}
        recipe = common.load(data / "recipes" / f"{slug}.json")
        if over.get("exclude") or recipe is None:
            continue
        tags |= {str(t).lower() for t in [over.get("style") or style, *(recipe.get("cuisine") or [])] if t}
        try:
            prepared = prepare(recipe, serves=serves, products=products, overrides=overrides,
                               protein_g=settings["protein_g"], swap_ids=swaps_on)
        except Exception as e:  # one bad recipe shouldn't stop the week
            print(f"skipped {slug}: {e}", file=sys.stderr)
            continue
        cuts[slug] = prepared.get("cut")
        costed = cost_option(slug, prepared, ctx)
        no_entry |= {l["key"] for l in costed["lines"] if l["key"] not in products and not l.get("assumed")}
        if (tuning.get("recipes", {}).get(slug, {}).get("score", 0) <= -1
                or all(v is None for v in costed["cost"].values())):
            continue
        cuisines = list(recipe.get("cuisine") or [])
        cands.append({
            "slug": slug, "title": recipe.get("title"), "url": recipe.get("url"),
            "cut": prepared.get("cut"), "style": over.get("style") or style,
            "cuisine": cuisines[0] if cuisines else None, "cuisines": cuisines,
            "new": slug not in picked, "serves": prepared.get("serves", serves),
            "rating": recipe.get("rating"), "per_serve": prepared.get("per_serve", {}),
            "recent": any(0 < days_between(w, week) <= RECENT_DAYS for w in picked.get(slug, ())),
            "table": prepared, **costed,
        })

    ctx["cut_weeks"] = cut_weeks(picked, lambda s: cuts.get(s) or past_cut(s, own, week), week)
    for c in cands:
        c["score_terms"] = {k: round(v, 4) for k, v in score_terms(c, ctx).items()}
        c["score"] = sum(c["score_terms"].values())
    chosen, relaxed = choose(cands, random.Random(str(seed)), options_wanted(settings["recipes"]))

    keep = ("slug", "title", "url", "cut", "style", "cuisine", "new", "serves", "cost", "cost_used", "cost_effective",
            "cost_per_50g_protein", "per_serve", "per_serve_by_store", "specials", "unmatched", "assumed", "missing", "score", "score_terms", "table")
    options = [{k: c[k] for k in keep} for c in chosen]
    # "pairs" keeps its name: with 1 or 3 recipes a week its keys are single slugs or three slugs.
    pairs = {pick_key(opts): price_pick(opts, ctx)
             for opts in itertools.combinations(chosen, min(settings["recipes"], len(chosen)))}
    weeks_used = [w for w in used.values() if w]
    return {
        "week": week, "generated": datetime.datetime.now().isoformat(timespec="seconds"),
        "person": person, "settings": settings, "stores": settings["stores"],
        "prices_week": min(weeks_used), "prices_weeks": used,
        "stale": any(w != week for w in weeks_used), "stores_ok": fresh, "seed": str(seed),
        "options": options, "pairs": pairs, "relaxed": relaxed,
        "alerts": bulk_alerts(week, products, catalogs, fresh, data / "prices"),
        "unmatched": sorted(no_entry), "styles": sorted(tags),
        "titles": past_titles(picked, data),
    }


def past_titles(picked, data):
    """Title, style and cuisine of every recipe picked before, for the dashboard's Rate tab."""
    styles = dict(pool_entries(common.load(data / "recipes" / "_pool.json", [])))
    out = {}
    for slug in sorted(picked):
        recipe = common.load(data / "recipes" / f"{slug}.json") or {}
        out[slug] = {"title": recipe.get("title") or slug, "style": styles.get(slug),
                     "cuisine": next(iter(recipe.get("cuisine") or []), None)}
    return out


def past_cut(slug, own, week):
    """A picked recipe's cut from the person's earlier week files, for recipes no longer in the pool."""
    for path in sorted((own / "weeks").glob("*.json"), reverse=True):
        if path.stem < week:
            for o in common.load(path).get("options", []):
                if o.get("slug") == slug:
                    return o.get("cut")
    return None


def record_offer(path, week, slugs):
    """Set the week's offered slugs in history.json; picked and ratings are left alone."""
    hist = common.load(path, {"weeks": []})
    entry = next((w for w in hist["weeks"] if w.get("week") == week), None)
    if entry is not None and entry.get("offered") == slugs:
        return False
    if entry is None:
        hist["weeks"].append({"week": week, "offered": slugs})
        hist["weeks"].sort(key=lambda w: w.get("week", ""))
    else:
        entry["offered"] = slugs
    common.save(path, hist)
    return True


def summary(out):
    def money(v, serves):
        return f"${v / serves:5.2f}" if v is not None else "    -"
    stores = out.get("stores") or STORES
    settings = out.get("settings") or {}
    lines = [f"Week {out['week']}: prices {out['prices_week']}"
             + (" (STALE)" if out["stale"] else "")
             + "".join(f", {s} unavailable" for s, ok in out["stores_ok"].items() if not ok and s in stores)]
    if settings:
        lines.append(f"  {settings['recipes']} recipe(s) x {settings['serves']} serves, {settings['protein_g']} g protein,"
                     f" stores {', '.join(stores)}, swaps off: "
                     + (", ".join(k for k, v in settings["swaps"].items() if not v) or "none"))
    for o in out["options"]:
        per = "  ".join(f"{s} {money(o['cost'][s], o['serves'])}" for s in stores)
        lines.append(f"  {o['title'] or o['slug']:<40} {per}/serve" + ("  [new]" if o["new"] else ""))
        p50 = "  ".join(f"{s} {money(o['cost_per_50g_protein'][s], 1)}" for s in stores)
        lines.append(f"    per {settings.get('protein_g', PROTEIN_TARGET_G)} g protein: {p50}  score {o['score']:.3f}")
        if o["unmatched"]:
            lines.append(f"    unmatched: {', '.join(o['unmatched'])}")
    for key, p in out["pairs"].items():
        sp = p.get("split")
        choice = f"split {'+'.join(sp['stores'])}" if p["choice"] == "split" else p["choice"]
        eff = "  ".join(f"{s} {p[s]['effective']:.2f}" for s in STORES if p.get(s))
        lines.append(f"  pick {key}: {choice or 'none'}  ({eff}"
                     + (f"  split {'+'.join(sp['stores'])} {sp['effective']:.2f}" if sp else "") + ")")
    if out["relaxed"]:
        lines.append(f"Relaxed: {'; '.join(out['relaxed'])}")
    if out["alerts"]:
        lines.append("Bulk meat: " +"; ".join(f"{a['store']} {a['name']} ${a['per_kg']}/kg" for a in out["alerts"]))
    if out["unmatched"]:
        lines.append(f"Pool ingredients with no products.yaml entry: {len(out['unmatched'])}")
    return "\n".join(lines)


def plan_and_save(week, args, prepare, root, person=None, data_dir=None):
    out = plan(week, args.seed, args.allow_stale, prepare, root, person, data_dir)
    if not out["options"]:
        raise SystemExit(f"no recipe could be priced for {week}")
    print(summary(out))
    if not args.dry_run:
        own = own_dir(root, person)
        common.save(own / "weeks" / f"{week}.json", out)
        record_offer(own / "history.json", week, [o["slug"] for o in out["options"]])
    return out


def migrate(root, people, dry_run):
    """TJ's single-person files into his folder (once); says what moved. True if any are still to move."""
    pending = False
    rel = lambda p: p.relative_to(root)  # noqa: E731
    for src, dst, done in common.migrate_people(root, people, dry_run):
        if not done:
            print(f"migrate: {rel(dst)} already exists; left {rel(src)} where it is", file=sys.stderr)
        else:
            print(f"migrate: {'would move' if dry_run else 'moved'} {rel(src)} -> {rel(dst)}")
            pending |= dry_run
    return pending


def main(argv=None, prepare=None, root=common.ROOT):
    """Plans and writes each person's week. Returns {key: week file}, or the one week file without people.yaml."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--week", help="any date in the week (YYYY-MM-DD); defaults to this week")
    ap.add_argument("--person", help="a key in people.yaml; default: everyone with a plan")
    ap.add_argument("--seed")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--allow-stale", action="store_true")
    args = ap.parse_args(argv)
    week = common.week_of(datetime.date.fromisoformat(args.week) if args.week else None)
    people = common.load_people(root)
    if not people:
        if args.person:
            raise SystemExit("--person needs a people.yaml")
        return plan_and_save(week, args, prepare, root)
    if args.person and args.person not in common.planned(people):
        raise SystemExit(f"{args.person} is not a person with a plan in people.yaml")
    keys = [args.person] if args.person else common.planned(people)
    unmigrated = migrate(root, people, args.dry_run)
    outs, failed = {}, []
    for key in keys:
        print(f"== {people[key].get('name') or key} ({key})")
        # A dry run before the migration reads TJ's files where they still are.
        data_dir = root / "data" if unmigrated and key == common.owner(people) else None
        try:
            outs[key] = plan_and_save(week, args, prepare, root, key, data_dir)
        except (SystemExit, Exception) as e:  # one person's failure doesn't stop the others
            print(f"FAILED for {key}: {e}", file=sys.stderr)
            failed.append(key)
    if failed:
        raise SystemExit(f"plan failed for {', '.join(failed)}")
    return outs


if __name__ == "__main__":
    main()
