"""Writes the mock people.json / week-<key>.json / state.json variants the harness serves.

Recipes, prices and steps are invented. Shapes follow shopping/meals/SCHEMA.md, "Per person": every
variant has people.json (TJ, Ted, Adam without a plan yet, and the shared iPad), one week file per person
with a plan, and state.json as {updated, people: {key: {picks, ratings, tuning, freezer, settings, updated}}}.
Run: .venv/bin/python shopping/card/test/make_mocks.py   (from home-assistant/)

mock/base/       TJ: four options, six pairs (2 recipes, 6 serves, 50 g protein, all three stores), picks
                 for this week and last, ratings, freezer; the picked pair buys whole 1 kg packs of mince
                 (1622 g needed: 378 g spare, all cooked) and chicken (1333 g: 667 g spare, 500 g frozen
                 and 167 g cooked). Coles is Woolworths' range at 5% more, except cheap mince and chicken
                 breast, so the picked pair's best split is Aldi + Coles and is recommended.
                 Ted: 3 recipes, 4 serves, 40 g protein, Aldi and Coles only, oil swap off: six options,
                 twenty three-recipe picks, three picked
mock/stale/      TJ's options priced on last week's snapshot (stale: true)
mock/aldi-only/  the Woolworths and Coles reads failed: their costs and lists are null
mock/no-coles/   only the Coles read failed and Coles has no earlier snapshot: Coles is null
mock/first-run/  people.json only: no week files, no state.json (both 404)
mock/ted-new/    TJ as in base; Ted has no week file and no state entry yet (settings default)
mock/fail/       base data; the harness makes every service call fail
"""
import copy
import zlib
import itertools
import json
import math
import pathlib
import shutil
import sys

import yaml

MEALS = pathlib.Path(__file__).resolve().parents[2] / "meals"
sys.path.insert(0, str(MEALS))

# Copied from plan_week.py (FREEZE_STEP_G, qty_text, per_serve_with) so the mocks don't break while the
# planner is being changed; keep in step with it.
FREEZE_STEP_G = 500


def qty_text(qty, unit):
    if qty is None:
        return ""
    if unit in ("g", "ml"):
        big = "kg" if unit == "g" else "L"
        if qty >= 1000:
            return f"{math.floor(qty / 100 + 0.5) / 10:g} {big}"
        step = 5 if qty >= 100 else 1
        return f"{max(1, math.floor(qty / step + 0.5) * step):g} {unit}"
    return f"{math.floor(qty * 10 + 0.5) / 10:g}"


def per_serve_with(base, extras, protein, serves=6):
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


PROTEIN = yaml.safe_load((MEALS / "protein.yaml").read_text())
OUT = pathlib.Path(__file__).parent / "mock"
WEEK, PREV = "2026-11-18", "2026-11-11"
SERVES = 6


def ing(text, name, amount=None, unit=None, step=1, original=None, note=None, prep=None, protein=False, removed=False):
    return {"name": name, "key": name.lower(), "amount": amount, "unit": unit, "text": text,
            "original": original, "note": note, "prep": prep, "group": None, "step": step,
            "is_protein": protein, "removed": removed}


def table(slug, title, cut, per_serve, ings, steps, notes=()):
    return {"slug": slug, "title": title, "url": f"https://example.invalid/{slug}", "serves": SERVES,
            "recipe_serves": 4, "cut": cut, "cuts": [cut], "per_serve": per_serve, "ingredients": ings,
            "steps": [{"n": n, "text": t, "ingredients": [i for i, x in enumerate(ings) if x["step"] == n]}
                      for n, t in enumerate(steps, 1)],
            "notes": list(notes), "unplaced": []}


# (name, aldi (product, pack price, usual) or None, woolworths (product, pack price, usual) or None, packs)
RECIPES = [
    dict(slug="smoky-chipotle-beef-chilli", title="Smoky Chipotle Beef Chilli", cut="beef mince", style="chilli",
         cuisine="Mexican", new=False, per_serve={"protein_g": 50, "kcal": 585, "meat_g": 245},
         table=table("smoky-chipotle-beef-chilli", "Smoky Chipotle Beef Chilli", "beef mince",
                     {"protein_g": 50, "kcal": 585, "meat_g": 245}, [
             ing("", "spray oil", step=1, original="olive oil"),
             ing("2", "brown onions", 2, None, 1, prep="finely diced"),
             ing("4", "garlic cloves", 4, None, 1, prep="minced"),
             ing("1625 g", "lean beef mince", 1625, "g", 2, protein=True),
             ing("2 tbsp", "smoked paprika", 2, "tbsp", 3),
             ing("1 1/2 tbsp", "ground cumin", 1.5, "tbsp", 3),
             ing("2", "chipotle chillies in adobo", 2, None, 3, prep="chopped"),
             ing("2 tbsp", "brown sugar", 2, "tbsp", 3, removed=True, original="brown sugar"),
             ing("800 g", "crushed tomatoes", 800, "g", 4),
             ing("2 x 400 g", "red kidney beans", 2, None, 4, prep="drained and rinsed"),
             ing("375 ml", "beef stock", 375, "ml", 4),
             ing("2 1/2 cups", "white rice", 2.5, "cup", 5, prep="to serve"),
         ], [
             "Spray a large heavy-based pot with oil and cook the onion and garlic over medium-high heat for 3 minutes until softened.",
             "Add the beef mince and cook, breaking it up as you go, until it changes from pink to brown.",
             "Stir in the paprika, cumin and chipotle and cook for 1 minute until fragrant.",
             "Add the tomatoes, beans and stock, bring to a simmer, then cover and cook on low for 1 hour, stirring occasionally. Uncover for the last 15 minutes if it's too wet.",
             "Cook the rice, then divide the chilli and rice between six containers. Freezes well for 3 months.",
         ], ["Brown sugar removed: the recipe used 2 tbsp; add a splash of vinegar if it tastes flat."]),
         items=[("Lean Beef Mince 1kg", (12.49, 13.99), ("Woolworths Lean Beef Mince 1kg", 9.00, 13.00), 2),
                ("Brown Onions 1kg", (2.99, 2.99), ("Brown Onions 1kg Bag", 3.50, 3.50), 1),
                ("Garlic Bulbs 3 pack", (2.49, 2.49), ("Garlic Prepack 3pk", 3.00, 3.00), 1),
                ("Chipotle in Adobo 200g", (4.29, 4.29), ("La Morena Chipotle 210g", 4.50, 4.50), 1),
                ("Crushed Tomatoes 400g", (0.95, 0.95), ("Woolworths Crushed Tomatoes 400g", 1.10, 1.10), 2),
                ("Red Kidney Beans 400g", (0.89, 0.89), ("Woolworths Red Kidney Beans 400g", 1.00, 1.00), 2),
                ("Beef Stock 1L", (1.99, 1.99), ("Massel Beef Stock 1L", 3.30, 3.30), 1),
                ("Jasmine Rice 2kg", (4.99, 4.99), ("Woolworths White Rice 2kg", 5.50, 5.50), 1)],
         unmatched=["chipotle chillies in adobo"]),
    dict(slug="lemongrass-chicken-rice-bowls", title="Lemongrass Chicken Rice Bowls with Quick Pickled Carrot",
         cut="chicken thigh", style="bowl", cuisine="Vietnamese", new=True,
         per_serve={"protein_g": 50, "kcal": 640, "meat_g": 270},
         table=table("lemongrass-chicken-rice-bowls", "Lemongrass Chicken Rice Bowls", "chicken thigh",
                     {"protein_g": 50, "kcal": 640, "meat_g": 270}, [
             ing("1625 g", "chicken thigh fillets", 1625, "g", 1, protein=True, prep="trimmed, cut into strips"),
             ing("3 tbsp", "lemongrass paste", 3, "tbsp", 1),
             ing("3 tbsp", "fish sauce", 3, "tbsp", 1),
             ing("1 tbsp", "brown sugar", 1, "tbsp", 1, removed=True, original="brown sugar"),
             ing("4", "garlic cloves", 4, None, 1, prep="minced"),
             ing("3", "carrots", 3, None, 2, prep="julienned"),
             ing("125 ml", "rice vinegar", 125, "ml", 2),
             ing("", "spray oil", step=3, original="vegetable oil", note="Spray the pan well; the marinade catches."),
             ing("2 1/2 cups", "white rice", 2.5, "cup", 4),
             ing("1", "continental cucumber", 1, None, 4, prep="sliced"),
             ing("1 bunch", "coriander", 1, "bunch", 4),
         ], [
             "Mix the lemongrass, fish sauce and garlic, toss through the chicken and marinate for at least 20 minutes (or overnight).",
             "Toss the carrot with the vinegar and a pinch of salt; set aside to pickle while the chicken cooks.",
             "Spray a large frying pan and cook the chicken in batches over high heat, 4 to 5 minutes each, until caramelised and cooked through.",
             "Cook the rice. Pack each container with rice, chicken, pickled carrot, cucumber and coriander. Keep the cucumber separate if freezing.",
         ]),
         items=[("Chicken Thigh Fillets kg", (10.99, 12.99), ("Woolworths Chicken Thigh Fillets 1kg", 13.00, 13.00), 2),
                ("Lemongrass Paste 110g", (2.29, 2.29), ("Gourmet Garden Lemongrass 80g", 4.20, 4.20), 1),
                ("Fish Sauce 200ml", (1.89, 1.89), ("Squid Fish Sauce 300ml", 3.20, 3.20), 1),
                ("Garlic Bulbs 3 pack", (2.49, 2.49), ("Garlic Prepack 3pk", 3.00, 3.00), 1),
                ("Carrots 1kg", (1.49, 1.49), ("Carrots 1kg Bag", 2.00, 2.00), 1),
                ("Rice Vinegar 250ml", (2.19, 2.19), ("Marukan Rice Vinegar 355ml", 4.00, 4.00), 1),
                ("Jasmine Rice 2kg", (4.99, 4.99), ("Woolworths White Rice 2kg", 5.50, 5.50), 1),
                ("Continental Cucumber", (1.49, 1.99), ("Continental Cucumber each", 1.90, 1.90), 1),
                ("Coriander bunch", (2.49, 2.49), ("Coriander bunch", 3.00, 3.00), 1)]),
    dict(slug="creamy-tuscan-chicken-pasta", title="Creamy Tuscan Chicken Pasta", cut="chicken breast", style="pasta",
         cuisine="Italian", new=False, per_serve={"protein_g": 50, "kcal": 560, "meat_g": 215},
         table=table("creamy-tuscan-chicken-pasta", "Creamy Tuscan Chicken Pasta", "chicken breast",
                     {"protein_g": 50, "kcal": 560, "meat_g": 215}, [
             ing("1325 g", "chicken breast", 1325, "g", 1, protein=True, prep="cut into bite size pieces"),
             ing("", "spray oil", step=1, original="olive oil"),
             ing("30 g", "butter", 30, "g", 2, removed=True, original="butter"),
             ing("3", "garlic cloves", 3, None, 2, prep="minced"),
             ing("1/2 cup", "sun-dried tomatoes", 0.5, "cup", 2, prep="sliced"),
             ing("2 tsp", "dried Italian herbs", 2, "tsp", 2),
             ing("375 ml", "chicken stock", 375, "ml", 3),
             ing("450 g", "greek yoghurt", 450, "g", 3, original="thickened cream",
                 note="Stir in off the heat; don't let it boil or it splits."),
             ing("75 g", "parmesan", 75, "g", 3, prep="finely grated"),
             ing("120 g", "baby spinach", 120, "g", 4),
             ing("500 g", "penne", 500, "g", 4),
         ], [
             "Spray a large skillet and sear the chicken over high heat until golden but not cooked through. Remove to a plate.",
             "Lower the heat to medium, add the garlic, sun-dried tomatoes and herbs and cook for 1 minute.",
             "Add the stock and simmer for 3 minutes. Take off the heat, wait a minute, then stir in the yoghurt and parmesan.",
             "Return the chicken, add the spinach and stir until wilted. Toss through the cooked penne and portion out.",
         ], ["Greek yoghurt replaces cream: add it off the heat, don't boil."]),
         items=[("Chicken Breast Fillets 1kg", (11.99, 11.99), ("Woolworths Chicken Breast Fillets 1kg", 8.00, 14.00), 2),
                ("Garlic Bulbs 3 pack", (2.49, 2.49), ("Garlic Prepack 3pk", 3.00, 3.00), 1),
                ("Sun Dried Tomatoes 285g", (3.49, 3.49), ("Always Fresh Sun Dried Tomatoes 230g", 5.50, 5.50), 1),
                ("Chicken Stock 1L", (1.99, 1.99), ("Massel Chicken Stock 1L", 3.30, 3.30), 1),
                ("Greek Style Yoghurt 1kg", (3.79, 3.79), ("Chobani Greek Yoghurt 907g", 6.00, 8.00), 1),
                ("Parmesan 250g", (5.49, 5.49), ("Woolworths Parmesan 250g", 6.50, 6.50), 1),
                ("Baby Spinach 120g", (2.49, 2.49), ("Baby Spinach 120g", 3.00, 3.00), 1),
                ("Penne 500g", (1.19, 1.19), ("Barilla Penne 500g", 2.00, 3.50), 1)]),
    dict(slug="slow-cooker-lamb-rogan-josh", title="Slow Cooker Lamb Rogan Josh", cut="lamb shoulder", style="curry",
         cuisine="Indian", new=False, per_serve={"protein_g": 50, "kcal": 720, "meat_g": 300},
         table=table("slow-cooker-lamb-rogan-josh", "Slow Cooker Lamb Rogan Josh", "lamb shoulder",
                     {"protein_g": 50, "kcal": 720, "meat_g": 300}, [
             ing("1800 g", "diced lamb shoulder", 1800, "g", 1, protein=True),
             ing("", "spray oil", step=1, original="ghee"),
             ing("2", "brown onions", 2, None, 2, prep="finely sliced"),
             ing("5", "garlic cloves", 5, None, 2),
             ing("2 tbsp", "ginger", 2, "tbsp", 2, prep="grated"),
             ing("1/3 cup", "rogan josh paste", 0.333, "cup", 2),
             ing("800 g", "crushed tomatoes", 800, "g", 3),
             ing("375 g", "greek yoghurt", 375, "g", 3, original="plain yoghurt"),
             ing("2 1/2 cups", "basmati rice", 2.5, "cup", 4),
         ], [
             "Spray a frying pan and brown the lamb in batches. Transfer to the slow cooker.",
             "In the same pan cook the onion, garlic and ginger until golden, then stir in the paste for 1 minute.",
             "Tip into the slow cooker with the tomatoes and yoghurt, stir, and cook on low for 7 hours.",
             "Shred any large pieces, season, and serve over basmati rice.",
         ]),
         items=[("Lamb Shoulder Diced kg", (17.99, 21.99), None, 2),
                ("Brown Onions 1kg", (2.99, 2.99), ("Brown Onions 1kg Bag", 3.50, 3.50), 1),
                ("Garlic Bulbs 3 pack", (2.49, 2.49), ("Garlic Prepack 3pk", 3.00, 3.00), 1),
                ("Ginger Paste 100g", (1.99, 1.99), ("Gourmet Garden Ginger 80g", 4.20, 4.20), 1),
                ("Rogan Josh Paste 283g", (2.99, 2.99), ("Patak's Rogan Josh Paste 283g", 4.50, 4.50), 1),
                ("Crushed Tomatoes 400g", (0.95, 0.95), ("Woolworths Crushed Tomatoes 400g", 1.10, 1.10), 2),
                ("Greek Style Yoghurt 1kg", (3.79, 3.79), ("Chobani Greek Yoghurt 907g", 6.00, 8.00), 1),
                ("Basmati Rice 1kg", (2.79, 2.79), ("Sunrice Basmati 1kg", 4.00, 4.00), 1)],
         missing={"woolworths": ["diced lamb shoulder"]}),
    # Two more, only in Ted's six options.
    dict(slug="beef-and-broccoli-stir-fry", title="Beef and Broccoli Stir-fry", cut="beef rump", style="stir-fry",
         cuisine="Chinese", new=True, per_serve={"protein_g": 50, "kcal": 540, "meat_g": 230},
         table=table("beef-and-broccoli-stir-fry", "Beef and Broccoli Stir-fry", "beef rump",
                     {"protein_g": 50, "kcal": 540, "meat_g": 230}, [
             ing("1400 g", "beef rump steak", 1400, "g", 1, protein=True, prep="thinly sliced"),
             ing("", "spray oil", step=2, original="vegetable oil"),
             ing("2 heads", "broccoli", 2, None, 2, prep="cut into florets"),
             ing("1/3 cup", "oyster sauce", 0.333, "cup", 3),
             ing("2 1/2 cups", "white rice", 2.5, "cup", 4),
         ], [
             "Toss the beef with a little cornflour and soy sauce.",
             "Spray a wok and stir-fry the beef in batches over high heat; remove. Stir-fry the broccoli for 2 minutes.",
             "Return the beef, add the oyster sauce and a splash of water and toss until glossy.",
             "Serve over rice.",
         ]),
         items=[("Beef Rump Steak kg", (19.99, 22.99), ("Woolworths Beef Rump Steak kg", 24.00, 26.00), 1),
                ("Broccoli each", (1.49, 1.49), ("Broccoli each", 1.90, 1.90), 2),
                ("Oyster Sauce 250ml", (1.99, 1.99), ("Lee Kum Kee Oyster Sauce 255g", 3.50, 3.50), 1),
                ("Jasmine Rice 2kg", (4.99, 4.99), ("Woolworths White Rice 2kg", 5.50, 5.50), 1)]),
    dict(slug="chicken-burrito-bowls", title="Chicken Burrito Bowls", cut="chicken breast", style="bowl",
         cuisine="Mexican", new=False, per_serve={"protein_g": 50, "kcal": 610, "meat_g": 215},
         table=table("chicken-burrito-bowls", "Chicken Burrito Bowls", "chicken breast",
                     {"protein_g": 50, "kcal": 610, "meat_g": 215}, [
             ing("1325 g", "chicken breast", 1325, "g", 1, protein=True),
             ing("2 tbsp", "taco seasoning", 2, "tbsp", 1),
             ing("2 x 400 g", "black beans", 2, None, 2, prep="drained"),
             ing("2 1/2 cups", "white rice", 2.5, "cup", 2),
             ing("1 cup", "salsa", 1, "cup", 3),
         ], [
             "Rub the chicken with the seasoning and grill until cooked through; slice.",
             "Warm the beans and cook the rice.",
             "Pack rice, beans, chicken and salsa into containers.",
         ]),
         items=[("Chicken Breast Fillets 1kg", (11.99, 11.99), ("Woolworths Chicken Breast Fillets 1kg", 8.00, 14.00), 2),
                ("Taco Seasoning 35g", (0.99, 0.99), ("Old El Paso Taco Spice Mix 35g", 1.80, 1.80), 1),
                ("Black Beans 400g", (0.99, 0.99), ("Woolworths Black Beans 400g", 1.20, 1.20), 2),
                ("Jasmine Rice 2kg", (4.99, 4.99), ("Woolworths White Rice 2kg", 5.50, 5.50), 1),
                ("Chunky Salsa 300g", (2.49, 2.49), ("Old El Paso Thick n Chunky Salsa 300g", 4.00, 4.00), 1)]),
]

# Person keys are the HA user ids in shopping/meals/people.yaml.
TJ, TED, ADAM, IPAD = ("148d862cd5a04037b7f641dbc0f55c72", "b37e212b9eb2438c9fcec4e98fe55e5d",
                       "429a17946d74479b8ae9b003a7e9bd05", "6f45a26dcda04f2883b018f6106049f6")
PEOPLE = {TJ: {"name": "TJ", "shared": False, "has_plan": True},
          TED: {"name": "Ted", "shared": False, "has_plan": True},
          ADAM: {"name": "Adam", "shared": False, "has_plan": False},
          IPAD: {"name": "iPad", "shared": True, "has_plan": False}}
TJ_SETTINGS = {"recipes": 2, "serves": 6, "protein_g": 50,
               "swaps": {"cream": True, "oil": True, "butter": True, "sugar": True},
               "stores": ["aldi", "woolworths", "coles"]}
TED_SETTINGS = {"recipes": 3, "serves": 4, "protein_g": 40,
                "swaps": {"cream": True, "oil": False, "butter": True, "sugar": True},
                "stores": ["aldi", "coles"]}

STORES = ("aldi", "woolworths", "coles")
# Coles: the Woolworths line renamed at 5% more, unless listed here (product, pack price, usual).
COLES = {"Lean Beef Mince 1kg": ("Coles 3 Star Beef Mince 1kg", 8.50, 12.00),
         "Chicken Breast Fillets 1kg": ("Coles RSPCA Chicken Breast Fillets 1kg", 7.00, 12.00),
         "Lamb Shoulder Diced kg": ("Coles Lamb Shoulder Diced kg", 19.00, 19.00)}


def coles_of(name, woolies):
    if name in COLES:
        return COLES[name]
    if woolies is None:
        return None
    pname, price, usual = woolies
    return (pname.replace("Woolworths ", "Coles "), round(price * 1.05, 2), round(usual * 1.05, 2))
# Meat bought in whole 1 kg packs: grams needed, cut and the recipe's ingredient name. Whole 500 g
# steps of the spare go in the freezer; the rest is cooked this week (plan_week.split_spare).
MEAT = {"Lean Beef Mince 1kg": (1622, "beef mince", "lean beef mince"),
        "Chicken Breast Fillets 1kg": (1333, "chicken breast", "chicken breast")}


def store_items(r, store):
    out = []
    for name, aldi, woolies, packs in r["items"]:
        src = {"aldi": aldi and (name, *aldi), "woolworths": woolies, "coles": coles_of(name, woolies)}[store]
        if src is None:
            return None
        pname, price, usual = src
        per_kg = pname.endswith(" kg")
        qty = 1.6 if per_kg else packs
        cost = round(price * (qty if per_kg else packs), 2)
        it = {"name": pname, "id": f"{store[0]}{zlib.crc32(pname.encode()) % 10**6}", "packs": None if per_kg else packs,
              "price": price, "cost": cost, "used": round(cost * 0.8, 2), "qty": 1600.0 if per_kg else packs,
              "unit": "g" if per_kg else "ea", "per_kg": per_kg, "special": price <= 0.85 * usual,
              "usual_price": usual, "for": [r["slug"]], "freeze_qty": 0, "freeze_g": 0, "freeze_value": 0,
              "extra_g": 0, "qty_text": qty_text(1600.0 if per_kg else packs, "g" if per_kg else "ea"), "_key": name}
        if name in MEAT:
            need, cut, ing_name = MEAT[name]
            spare = packs * 1000 - need
            frozen = math.floor(spare / FREEZE_STEP_G) * FREEZE_STEP_G
            extra = spare - frozen
            it.update(qty=float(need), unit="g", qty_text=qty_text(need, "g"),
                      used=round((need + extra) / 1000 * price, 2), freeze_qty=float(frozen), freeze_g=frozen,
                      freeze_value=round(frozen / 1000 * price, 2), extra_g=float(extra), _cut=cut)
            if extra:
                it["_extra"] = {r["slug"]: [{"name": ing_name, "cut": cut, "id": it["id"], "grams_total": float(extra),
                                             "bought_g": packs * 1000, "frozen_g": frozen, "shared": False}]}
        out.append(it)
    return out


def merge(lists):
    merged = {}
    for it in itertools.chain(*lists):
        m = merged.get(it["_key"])
        if m is None:
            merged[it["_key"]] = copy.deepcopy(it)
            continue
        m["for"] = sorted(set(m["for"]) | set(it["for"]))
        if m["packs"] is not None and it["_key"] in ("Garlic Bulbs 3 pack",):
            continue  # one bag covers both
        if m["packs"] is not None:
            m["packs"] += it["packs"]
        m["qty"] += it["qty"]
        m["cost"] = round(m["cost"] + it["cost"], 2)
        m["used"] = round(m["used"] + it["used"], 2)
        m["freeze_qty"] += it["freeze_qty"]
        m["freeze_g"] += it["freeze_g"]
        m["extra_g"] += it["extra_g"]
        m["qty_text"] = qty_text(m["qty"], "g" if m["per_kg"] or m["unit"] == "g" else "ea")
        if it.get("_extra"):
            m["_extra"] = {**(m.get("_extra") or {}), **it["_extra"]}
        m["freeze_value"] = round(m["freeze_value"] + it["freeze_value"], 2)
    return sorted(merged.values(), key=lambda i: i["name"])


def clean(items):
    return [{k: v for k, v in i.items() if not k.startswith("_")} for i in items]


def extras(items):
    out = {}
    for i in items:
        for slug, es in (i.get("_extra") or {}).items():
            out.setdefault(slug, []).extend(es)
    return out


def per_serve(recipes, items, serves=SERVES):
    ex = extras(items)
    return {r["slug"]: per_serve_with(r["per_serve"], ex.get(r["slug"]), PROTEIN, serves) for r in recipes}


def for_person(r, serves, target):
    """A recipe as the planner would prepare it for someone: their serves and protein target."""
    r = copy.deepcopy(r)
    f = target / 50
    r["per_serve"] = {"protein_g": target, "kcal": round(r["per_serve"]["kcal"] - (50 - target) * 6),
                      "meat_g": round(r["per_serve"]["meat_g"] * f)}
    r["table"]["per_serve"] = r["per_serve"]
    r["table"]["serves"] = serves
    return r


def entry(recipes, items, split_items=None, serves=SERVES):
    """A pair's store entry: totals, effective cost, the freeze list and the adjusted per-serve figures."""
    flat = items if split_items is None else [i for v in split_items.values() for i in v]
    total = round(sum(i["cost"] for i in flat), 2)
    value = round(sum(i["freeze_value"] for i in flat), 2)
    freeze = [{"cut": i["_cut"], "grams": i["freeze_g"], "name": i["name"]} for i in flat if i.get("_cut") and i["freeze_g"]]
    return {"total": total, "freeze_value": value, "effective": round(total - value, 2),
            "items": clean(items) if split_items is None else {s: clean(v) for s, v in split_items.items()},
            "freeze": freeze, "per_serve": per_serve(recipes, flat, serves)}


def build(stores_ok, stale=False, recipes=None, k=2, serves=SERVES, target=50):
    """A week file. stores_ok has a key per store the person shops at; k recipes make a pick."""
    recipes = recipes or RECIPES[:4]
    stores = [s for s in STORES if s in stores_ok]
    options, priced = [], {}
    for r in recipes:
        per = {s: (store_items(r, s) if stores_ok[s] else None) for s in stores}
        priced[r["slug"]] = per
        cost = {s: round(sum(i["cost"] for i in per[s]), 2) if per[s] else None for s in stores}
        used = {s: round(sum(i["used"] for i in per[s]), 2) if per[s] else None for s in stores}
        effective = {s: round(cost[s] - sum(i["freeze_value"] for i in per[s]), 2) if per[s] else None for s in stores}
        specials = [{"store": s, "name": i["name"], "price": i["price"], "usual_price": i["usual_price"]}
                    for s in stores for i in (per[s] or []) if i["special"]]
        per50 = {}  # the key keeps its name; the figure is per the person's own protein target
        for s in stores:
            ps = per_serve([r], per[s], serves)[r["slug"]] if per[s] else None
            per50[s] = round(effective[s] / serves * target / ps["protein_g"], 2) if ps and effective[s] is not None else None
        options.append({
            "slug": r["slug"], "title": r["title"], "url": f"https://example.invalid/{r['slug']}",
            "cut": r["cut"], "style": r["style"], "cuisine": r["cuisine"], "new": r["new"], "serves": serves,
            "cost": cost, "cost_used": used, "cost_effective": effective, "cost_per_50g_protein": per50,
            "per_serve": r["per_serve"],
            "per_serve_by_store": {s: per_serve([r], per[s], serves)[r["slug"]] if per[s] else None for s in stores},
            "specials": specials,
            "unmatched": r.get("unmatched", []), "missing": r.get("missing", {}),
            "score": 1.2, "score_terms": {"base": 1.0, "style": 0.2}, "table": r["table"]})
    pairs = {}
    for combo in itertools.combinations(recipes, k):
        key = "+".join(sorted(r["slug"] for r in combo))
        out = {}
        for s in stores:
            ps = [priced[r["slug"]][s] for r in combo]
            items = merge(ps) if all(ps) else None
            out[s] = entry(combo, items, serves=serves) if items else None
        # the best two-store split: each line from the cheaper of the two (mock: both stores must sell it)
        out["split"] = None
        for s1, s2 in itertools.combinations([s for s in stores if stores_ok[s]], 2):
            m = {s: {i["_key"]: i for i in merge([priced[r["slug"]][s] or [] for r in combo])} for s in (s1, s2)}
            if set(m[s1]) != set(m[s2]) or not all(priced[r["slug"]][s] for r in combo for s in (s1, s2)):
                continue
            eff = lambda i: i["cost"] - i["freeze_value"]  # noqa: E731
            parts = {s1: [], s2: []}
            for k in m[s1]:
                s = s1 if eff(m[s1][k]) <= eff(m[s2][k]) else s2
                parts[s].append(m[s][k])
            e = {"stores": [s1, s2], **entry(combo, None, parts, serves)}
            if out["split"] is None or e["effective"] < out["split"]["effective"]:
                out["split"] = e
        singles = {s: out[s]["effective"] for s in stores if out[s]}
        best = min(singles, key=singles.get) if singles else None
        if out["split"] and (best is None or singles[best] - out["split"]["effective"] >= 10):
            best = "split"
        out["choice"] = best
        pairs[key] = out
    doc = {
        "week": WEEK, "generated": "2026-11-18T07:35:00",
        "prices_week": PREV if stale else WEEK,
        "prices_weeks": {s: (PREV if stale else WEEK) if stores_ok[s] else None for s in stores},
        "stale": stale, "stores_ok": stores_ok, "seed": WEEK,
        "options": options, "pairs": pairs,
        "relaxed": ["styles: 2 distinct"] if stale else [],
        "alerts": [] if not stores_ok.get("woolworths") else [
            {"kind": "bulk_meat", "store": "coles", "cut": "chicken breast", "id": "c789",
             "name": "Coles RSPCA Chicken Breast Fillets 1kg", "per_kg": 7.0, "best_per_kg": 11.0},
            {"kind": "bulk_meat", "store": "woolworths", "cut": "beef mince", "id": "w123",
             "name": "Woolworths Lean Beef Mince 1kg", "per_kg": 9.0, "best_per_kg": 12.5},
            {"kind": "bulk_meat", "store": "aldi", "cut": "pork shoulder", "id": "a456",
             "name": "Pork Shoulder Roast Boneless per kg", "per_kg": 7.99, "best_per_kg": 10.49}],
        "unmatched": ["chipotle chillies in adobo", "kecap manis", "shaoxing wine"],
        "styles": ["bake", "bowl", "chilli", "curry", "pasta", "pie", "salad", "soup", "stew", "stir-fry",
                   "Chinese", "French", "Indian", "Italian", "Korean", "Mexican", "Middle Eastern", "Thai", "Vietnamese"],
    }
    return doc


STATE = {  # TJ's entry in state.json `people`
    "updated": "2026-11-18T19:02:11", "settings": TJ_SETTINGS,
    "picks": {PREV: ["honey-garlic-pork-stir-fry", "chicken-tikka-traybake"],
              WEEK: ["smoky-chipotle-beef-chilli", "creamy-tuscan-chicken-pasta"]},
    "ratings": {
        "chicken-tikka-traybake": {"rating": "again", "date": "2026-11-16", "title": "Chicken Tikka Traybake",
                                   "style": "bake", "cuisine": "Indian"},
        "beef-massaman-curry": {"rating": "fine", "date": "2026-11-02", "title": "Beef Massaman Curry",
                                "style": "curry", "cuisine": "Thai"},
        "tuna-mornay-bake": {"rating": "never", "date": "2026-10-12", "title": "Tuna Mornay Bake",
                             "style": "bake", "cuisine": "Australian"},
        "korean-beef-bulgogi-bowls": {"rating": "again", "date": "2026-10-05", "title": "Korean Beef Bulgogi Bowls",
                                      "style": "bowl", "cuisine": "Korean"},
    },
    "tuning": {"styles": {"curry": 0.3, "indian": 0.1, "soup": -0.5}, "similar_strength": 0.3, "fade_days": 365},
    "freezer": [{"cut": "chicken breast", "grams": 2000, "date": "2026-11-15", "seasoning": "paprika and garlic"},
                {"cut": "beef mince", "grams": 1000, "date": "2026-11-01", "seasoning": ""}],
}


TED_PICKS = ["lemongrass-chicken-rice-bowls", "beef-and-broccoli-stir-fry", "chicken-burrito-bowls"]
TED_STATE = {
    "updated": "2026-11-19T08:10:00", "settings": TED_SETTINGS,
    "picks": {WEEK: TED_PICKS},
    "ratings": {"chicken-tikka-traybake": {"rating": "fine", "date": "2026-11-16", "title": "Chicken Tikka Traybake",
                                          "style": "bake", "cuisine": "Indian"}},
    "tuning": {"styles": {"bowl": 0.4}, "similar_strength": 0.3, "fade_days": 365},
    "freezer": [],
}


def ted_week():
    ted = [for_person(r, TED_SETTINGS["serves"], TED_SETTINGS["protein_g"]) for r in RECIPES]
    return build({"aldi": True, "coles": True}, recipes=ted, k=TED_SETTINGS["recipes"],
                 serves=TED_SETTINGS["serves"], target=TED_SETTINGS["protein_g"])


def write(name, weeks=None, people=None):
    """weeks: {key: week doc}; people: {key: state entry} (None: no state.json)."""
    d = OUT / name
    shutil.rmtree(d, ignore_errors=True)
    d.mkdir(parents=True)
    (d / "people.json").write_text(json.dumps(PEOPLE, indent=1))
    for key, week in (weeks or {}).items():
        (d / f"week-{key}.json").write_text(json.dumps(week, indent=1))
    if people is not None:
        updated = max(p["updated"] for p in people.values())
        (d / "state.json").write_text(json.dumps({"updated": updated, "people": people}, indent=1))


def main():
    shutil.rmtree(OUT, ignore_errors=True)
    both = {"aldi": True, "woolworths": True, "coles": True}
    base, ted = build(both), ted_week()
    write("base", {TJ: base, TED: ted}, {TJ: STATE, TED: TED_STATE})
    write("fail", {TJ: base, TED: ted}, {TJ: STATE, TED: TED_STATE})
    write("ted-new", {TJ: base}, {TJ: STATE})
    write("stale", {TJ: build(both, stale=True)}, {TJ: STATE})
    aldi_state = copy.deepcopy(STATE)
    aldi_state["picks"][WEEK] = ["lemongrass-chicken-rice-bowls", "slow-cooker-lamb-rogan-josh"]
    write("aldi-only", {TJ: build({"aldi": True, "woolworths": False, "coles": False})}, {TJ: aldi_state})
    write("no-coles", {TJ: build({"aldi": True, "woolworths": True, "coles": False})}, {TJ: STATE})
    write("first-run")
    key = "+".join(sorted(TED_PICKS))
    print("ted", len(ted["options"]), "options,", len(ted["pairs"]), "picks; picked:", key,
          {s: v and v["total"] for s, v in ted["pairs"][key].items() if s != "choice"}, ted["pairs"][key]["choice"])
    for k, v in base["pairs"].items():
        print(k, {s: v[s] and (v[s]["total"], v[s]["effective"]) for s in (*STORES, "split")},
              v["split"] and v["split"]["stores"], v["choice"])


if __name__ == "__main__":
    main()
