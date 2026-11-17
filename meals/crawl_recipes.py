"""Crawl RecipeTin Eats into data/recipes/<slug>.json, filter the meal-prep pool, list ingredients.

Usage: .venv/bin/python shopping/meals/crawl_recipes.py [--limit N] [--delay S] [--refresh-new]
       .venv/bin/python shopping/meals/crawl_recipes.py --filter
       .venv/bin/python shopping/meals/crawl_recipes.py --ingredients > ingredients.tsv

Crawl (default): reads the sitemap index and its post sitemaps, then fetches every post not
already saved or skipped, one request every --delay seconds (default 5). Pages without a
Recipe are listed in data/recipes/_skipped.json and not fetched again. Stops after 3 HTTP
errors in a row or at once on 403/429; re-running resumes. --refresh-new (monthly) only
fetches posts first seen in the sitemap after the initial crawl (data/recipes/_sitemap.json).

Each page is read from its schema.org Recipe JSON-LD, with ingredient amount/unit/name/notes
and groups taken from the WP Recipe Maker spans when present. No images or HTML are kept.

--filter writes data/recipes/_pool.json from the rules below. --ingredients prints the
distinct normalised ingredient names in the pool with how many recipes use each, as TSV.
Personal use only: the files under data/ must not be published.
"""
import argparse
import collections
import datetime
import html
import json
import pathlib
import re
import sys
import time
import xml.etree.ElementTree as ET

import requests
import yaml
from bs4 import BeautifulSoup

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import common  # noqa: E402

SITEMAP_INDEX = "https://www.recipetineats.com/sitemap_index.xml"
USER_AGENT = "meal-picker/0.1 (personal use; single household; one request every 5 s)"
DELAY = 5
MAX_ERRORS = 3
MAX_EMPTY = 25  # this many pages in a row without a recipe means the markup has probably changed
OVERRIDES = common.ROOT / "overrides.yaml"

# Meal-prep rules. A recipe joins the pool only if it passes all three.
#
# | Rule         | Looks at                       | Rejects when                                         |
# |--------------|--------------------------------|------------------------------------------------------|
# | Main         | categories; keywords           | a category matches NOT_MAIN or a keyword NOT_MAIN_KW |
# | Protein      | normalised ingredient names    | none matches PROTEINS (stocks and sauces don't count)|
# | Reheats well | title, categories              | any matches NO_REHEAT, unless its exception matches  |
# | Overrides    | overrides.yaml                 | `exclude: true`; `style:` replaces the derived style |
NOT_MAIN = r"dessert|cake|cookie|biscuit|slice|baking|bread|muffin|pastry|pastries|sweets|drink|cocktail|" \
           r"smoothie|side|side dish|sauce|dressing|condiment|breakfast|brunch|salad|dip|snack|appeti[sz]er|" \
           r"starter|finger food|canape"
NOT_MAIN_KW = r"dessert|cookie|cocktail|smoothie|breakfast|brunch|side dish|snack|appeti[sz]er|finger food|canape"
PROTEINS = {
    "chicken": r"chicken",
    "beef": r"beef|brisket|chuck|oxtail|veal|steak|rump|sirloin|scotch fillet|eye fillet|rib ?eye|short ribs?|silverside|topside",
    "pork": r"pork|ham hock|chorizo",
    "lamb": r"lamb|mutton",
    "turkey": r"turkey",
    "seafood": r"fish|salmon|barramundi|snapper|cod|tuna|ling|basa|prawn|shrimp|mussel|squid|calamari|scallop|crab|clam",
    "mince": r"mince|ground (beef|pork|chicken|lamb|turkey)",
    "tofu": r"tofu|tempeh",
    "legumes": r"lentil|chickpea|dal|dhal|(kidney|black|cannellini|borlotti|butter|pinto|white|baked) beans?",
}
NOT_PROTEIN = r"stock|broth|bouillon|sauce|powder|cube|gravy granules|fat|salt|seasoning|paste"
NO_REHEAT = [  # (pattern, exception, reason)
    (r"fried|fry", r"fried rice|stir[ -]?fr(?:y|ied)", "fried"),
    (r"crispy|crunchy|crumbed|battered|tempura|schnitzel|katsu|karaage|nugget|fritter", None, "crispy coating"),
    (r"burger|sandwich|sliders?|wraps?|tacos?|burrito|quesadilla|pizza|toastie|sushi|poke", None, "assembled to serve"),
    (r"steak", r"pie|stew|curry|casserole|braise|slow", "steak cooked to order"),
    (r"salad", None, "fresh salad"),
]
STYLES = [  # first match wins, title before categories and keywords
    ("curry", r"curry|curries|korma|masala|vindaloo|rendang|tikka|dal|dhal|butter chicken"),
    ("soup", r"soup|chowder|laksa|pho|ramen|broth"),
    ("rice", r"fried rice|risotto|biryani|pilaf|pilau|paella|jambalaya|rice"),
    ("stir-fry", r"stir[ -]?fr(?:y|ied|ies)"),
    ("pasta", r"pasta|spaghetti|lasagne|lasagna|penne|fettuccine|linguine|rigatoni|macaroni|gnocchi|bolognese"),
    ("noodles", r"noodle|pad thai|chow mein|lo mein|udon"),
    ("stew", r"stew|casserole|braise|goulash|tagine|ragu|stroganoff|chilli con carne|chili|pulled|slow cook(?:er|ed)?"),
    ("bake", r"bake|baked|pie|gratin|moussaka|enchilada|traybake|tray bake"),
    ("roast", r"roast(?:ed)?"),
    ("salad", r"salad"),
]

UNIT_WORDS = set(common.UNITS) | {
    "can", "cans", "tin", "tins", "clove", "cloves", "bunch", "bunches", "pinch", "pinches", "handful",
    "sprig", "sprigs", "stalk", "stalks", "slice", "slices", "piece", "pieces", "pkt", "packet", "packets",
    "head", "heads", "sheet", "sheets", "cm", "inch", "stick", "sticks",
}
FRACTIONS = {"½": "1/2", "⅓": "1/3", "⅔": "2/3", "¼": "1/4", "¾": "3/4", "⅕": "1/5", "⅛": "1/8", "⅜": "3/8", "⅝": "5/8", "⅞": "7/8"}
NUM = r"(?:\d+\s+\d+/\d+|\d+/\d+|\d+(?:\.\d+)?)"
AMOUNT = re.compile(rf"^\s*(?P<a>{NUM})(?:\s*(?:-|–|—|to)\s*(?P<b>{NUM}))?\s*")
# The site writes weights both ways inside the amount: "1 kg / 2 lb", "14 oz / 400 g", "3 - 3.5 lb / 1.5 - 1.8 kg".
METRIC = re.compile(rf"(?P<a>{NUM})(?:\s*(?:-|–|—|to)\s*{NUM})?\s*(?P<u>kg|g|ml|l)\b", re.I)


class Stop(Exception):
    """The crawl must stop; the message says why and what to check."""


class ParseError(Stop):
    """A page has a recipe card the parser can't read: the site's markup has probably changed."""


def warn(msg):
    print(f"WARN {msg}", file=sys.stderr)


# ---------- amounts and ingredients ----------

def number(text):
    text = text.strip()
    if " " in text:
        whole, frac = text.split(None, 1)
        return float(whole) + number(frac)
    if "/" in text:
        n, d = text.split("/")
        return int(n) / int(d)
    return float(text)


def plain_fractions(text):
    for sym, frac in FRACTIONS.items():
        text = re.sub(rf"(\d)?\s*{sym}", lambda m: f"{m[1]} {frac}" if m[1] else f" {frac}", text)
    return text


def parse_amount(text):
    """'1 1/2 cups stock' -> (1.5, 'cups stock'). Ranges give the lower bound; no amount gives None."""
    text = plain_fractions(text or "")
    m = AMOUNT.match(text)
    if not m:
        return None, text.strip()
    return round(number(m["a"]), 4), text[m.end():].strip()


def metric_of(text):
    """The metric quantity in an amount like '2 lb / 1 kg' -> (1.0, 'kg'); None if there isn't one."""
    m = METRIC.search(plain_fractions(text or ""))
    return (round(number(m["a"]), 4), m["u"].lower()) if m else None


SIZE_WORDS = {"small", "medium", "large", "small/medium", "medium/large", "big", "x"}
PER_ITEM = re.compile(r"^(x|cans?|tins?|jars?|packets?)\b")


def clean_unit(amount, unit):
    """A unit as written ('cups (tightly packed)', 'x 400g/14oz cans', 'cups (150g)', 'small')
    -> (amount, unit) with a plain unit, a metric total where one is given, or no unit for a count."""
    if amount is None or not unit:
        return amount, unit or None
    text = unit.lower().strip()
    if metric := metric_of(text):
        qty, u = metric
        return (round(amount * qty, 4), u) if PER_ITEM.match(text) else metric
    text = re.sub(r"\(.*?\)", " ", text)
    text = re.sub(r"[+]|\bof\b|\bin total\b|\bheaped\b|\bpacked\b|\bvery\b|\bbig\b", " ", text)
    text = text.split("/")[0].strip(" -–")
    if not text or text in SIZE_WORDS or not text[0].isalpha():
        return amount, None
    return amount, text.split()[0].rstrip(".")


def amount_and_unit(amount_text, unit_text=""):
    """An amount (and unit, if given apart) as written -> (amount, unit), preferring metric."""
    amount, rest = parse_amount(amount_text)
    unit = (unit_text or "").lower() or None
    if (not unit or unit.rstrip(".") not in UNIT_WORDS) and (metric := metric_of(amount_text)):
        return metric
    if unit:
        return clean_unit(amount, unit)
    if not unit and rest:
        unit = split_unit(rest)[0]
    return amount, unit


# "1 lb/500g pork tenderloin" can leave the weight at the front of the name.
NAME_WEIGHT = re.compile(rf"^(?:lbs?|oz|pounds?|ounces?)?\s*/?\s*(?P<a>{NUM})\s*(?P<u>kg|g|ml|l)\b\s*/?\s*", re.I)


def weight_in_name(ing):
    """Move a weight written at the front of the name into amount and unit; True if it did."""
    m = NAME_WEIGHT.match(ing.get("name") or "")
    if not m or ing.get("unit") not in (None, "lb", "lbs", "oz", "pound", "pounds"):
        return False
    ing["amount"], ing["unit"], ing["name"] = round(number(m["a"]), 4), m["u"].lower(), ing["name"][m.end():].strip()
    return True


def repair_units(recipe):
    """Fix ingredients saved before dual amounts were understood; returns how many changed."""
    changed = sum(weight_in_name(ing) for ing in recipe["ingredients"])
    for ing in recipe["ingredients"]:
        unit = ing.get("unit")
        if unit and unit.rstrip(".") not in UNIT_WORDS:
            raw, name = ing.get("raw") or "", ing.get("name") or ""
            lead = raw[:raw.find(name)] if name and name in raw else ""
            fixed = metric_of(lead.split("(")[0]) if metric_of(lead.split("(")[0]) and not PER_ITEM.match(unit.lower()) \
                and not metric_of(unit) else clean_unit(ing.get("amount"), unit)
            if tuple(fixed) != (ing.get("amount"), unit):
                ing["amount"], ing["unit"] = fixed
                changed += 1
        raw, name = ing.get("raw") or "", ing.get("name") or ""
        lead = raw[:raw.find(name)] if name and name in raw else ""
        if ing.get("unit") is None and ing.get("amount") is not None and (metric := metric_of(lead)):
            ing["amount"], ing["unit"] = metric
            changed += 1
    return changed


def split_unit(rest):
    word, _, after = rest.partition(" ")
    if word.lower().rstrip(".") in UNIT_WORDS:
        return word.lower().rstrip("."), after.strip()
    return None, rest


def clean(text):
    text = html.unescape(text or "").replace("▢", " ")
    return re.sub(r"\s+", " ", text).strip()


def strip_note(text):
    text = clean(text).lstrip(",; ").strip()
    if text.startswith("(") and text.endswith(")") and text.count("(") == 1:
        text = text[1:-1].strip()
    return text or None


def ld_ingredient(raw):
    """A JSON-LD recipeIngredient string -> schema ingredient (no group)."""
    raw = clean(raw)
    amount, rest = parse_amount(raw)
    unit, rest = split_unit(rest)
    name, _, notes = rest.partition(",")
    return {"amount": amount, "unit": unit, "name": name.strip(), "notes": strip_note(notes), "group": None, "raw": raw}


def wprm_ingredients(card):
    """Ingredients from WP Recipe Maker spans; None when there are none or they can't be read."""
    rows = []
    groups = card.select(".wprm-recipe-ingredient-group") or [card]
    for g in groups:
        heading = g.select_one(".wprm-recipe-group-name") if g is not card else None
        group = clean(heading.get_text(" ")) or None if heading else None
        for li in g.select("li.wprm-recipe-ingredient"):
            span = {k: li.select_one(f".wprm-recipe-ingredient-{k}") for k in ("amount", "unit", "name", "notes")}
            if not span["name"]:
                warn(f"WPRM ingredient without a name span: {clean(li.get_text(' '))!r}; using JSON-LD instead")
                return None
            amount_text = clean(span["amount"].get_text(" ")) if span["amount"] else ""
            amount, unit = amount_and_unit(amount_text, clean(span["unit"].get_text(" ")) if span["unit"] else "")
            if amount_text and amount is None:
                warn(f"unreadable amount {amount_text!r} for {clean(span['name'].get_text(' '))!r}")
            row = {
                "amount": amount,
                "unit": unit,
                "name": clean(span["name"].get_text(" ")),
                "notes": strip_note(span["notes"].get_text(" ")) if span["notes"] else None,
                "group": group,
                "raw": clean(li.get_text(" ")),
            }
            weight_in_name(row)
            rows.append(row)
    return rows or None


# ---------- page ----------

def find_recipe_ld(soup, url):
    def walk(node):
        if isinstance(node, list):
            return next((r for r in map(walk, node) if r), None)
        if isinstance(node, dict):
            kind = node.get("@type")
            if kind == "Recipe" or (isinstance(kind, list) and "Recipe" in kind):
                return node
            return walk(node.get("@graph"))
        return None

    for script in soup.find_all("script", type="application/ld+json"):
        try:
            found = walk(json.loads(script.string or script.get_text()))
        except ValueError:
            warn(f"{url}: unreadable JSON-LD block skipped")
            continue
        if found:
            return found
    return None


def as_list(value):
    if value is None:
        return []
    if isinstance(value, str):
        return [v.strip() for v in value.split(",") if v.strip()]
    if isinstance(value, list):
        return [clean(str(v)) for v in value if str(v).strip()]
    return [clean(str(value))]


def steps_of(node):
    if isinstance(node, str):
        return [node]
    if isinstance(node, list):
        return [s for n in node for s in steps_of(n)]
    if isinstance(node, dict):
        if "itemListElement" in node:
            return steps_of(node["itemListElement"])
        return [node.get("text") or node.get("name") or ""]
    return []


def first_int(value):
    for v in value if isinstance(value, list) else [value]:
        if m := re.search(r"\d+", str(v)):
            return int(m[0])
    return None


def num_of(value):
    m = re.search(r"\d+(?:\.\d+)?", str(value or ""))
    if not m:
        return None
    f = float(m[0])
    return int(f) if f.is_integer() else f


def slug_of(url):
    return url.rstrip("/").rsplit("/", 1)[-1] if url.rstrip("/").count("/") > 2 else ""


def parse_page(page, url):
    """Recipe page HTML -> schema dict, None if the page has no recipe. Raises ParseError if unreadable."""
    soup = BeautifulSoup(page, "html.parser")
    ld = find_recipe_ld(soup, url)
    card = soup.select_one(".wprm-recipe-container[data-recipe-id]") or soup.select_one(".wprm-recipe")
    if not ld:
        if card:
            raise ParseError(f"{url}: has a WP Recipe Maker card but no schema.org Recipe JSON-LD; "
                             "the page markup has probably changed")
        return None
    if not ld.get("name") or not ld.get("recipeIngredient"):
        raise ParseError(f"{url}: Recipe JSON-LD has no {'name' if not ld.get('name') else 'recipeIngredient'}; "
                         f"keys found: {sorted(ld)}")

    ld_rows = [ld_ingredient(s) for s in ld["recipeIngredient"]]
    rows = wprm_ingredients(card) if card else None
    if card and rows is None:
        warn(f"{url}: no readable WPRM ingredient spans; amounts parsed from JSON-LD text")
    if rows and len(rows) != len(ld_rows):
        warn(f"{url}: {len(rows)} WPRM ingredients but {len(ld_rows)} in JSON-LD; using WPRM")
    steps = [s for s in (clean(BeautifulSoup(s, "html.parser").get_text(" ")) for s in steps_of(ld.get("recipeInstructions"))) if s]
    if not steps:
        warn(f"{url}: no recipeInstructions")
    rating = ld.get("aggregateRating") or {}
    nutrition = ld.get("nutrition") or {}
    keys = {"calories": "calories", "proteinContent": "protein_g", "fatContent": "fat_g", "carbohydrateContent": "carbs_g"}
    serves = first_int(ld.get("recipeYield"))
    if serves is None:
        warn(f"{url}: no number in recipeYield {ld.get('recipeYield')!r}")
    return {
        "slug": slug_of(url), "url": url, "title": clean(ld["name"]),
        "serves": serves,
        "categories": as_list(ld.get("recipeCategory")), "cuisine": as_list(ld.get("recipeCuisine")),
        "keywords": as_list(ld.get("keywords")),
        "rating": float(rating["ratingValue"]) if rating.get("ratingValue") else None,
        "rating_count": int(float(rating["ratingCount"])) if rating.get("ratingCount") else None,
        "nutrition": {k: v for src, k in keys.items() if (v := num_of(nutrition.get(src))) is not None},
        "ingredients": rows or ld_rows,
        "steps": steps,
        "fetched": datetime.date.today().isoformat(),
    }


# ---------- crawl ----------

class Fetcher:
    """GET with an honest User-Agent and at least `delay` seconds between requests."""

    def __init__(self, delay=DELAY):
        self.delay, self.last = delay, 0.0
        self.session = requests.Session()
        self.session.headers["User-Agent"] = USER_AGENT

    def __call__(self, url):
        time.sleep(max(0.0, self.last + self.delay - time.monotonic()))
        try:
            r = self.session.get(url, timeout=30)
            return r.status_code, r.text
        except requests.RequestException as e:
            return 0, str(e)
        finally:
            self.last = time.monotonic()


def parse_sitemap(xml):
    """Sitemap index or urlset XML -> list of <loc> URLs (image:loc entries ignored)."""
    root = ET.fromstring(xml)
    local = lambda el: el.tag.rsplit("}", 1)[-1]  # noqa: E731
    return [loc.text.strip() for el in root if local(el) in ("sitemap", "url")
            for loc in el if local(loc) == "loc" and loc.text]


def get(fetch, url):
    status, body = fetch(url)
    if status != 200:
        raise Stop(f"{url}: HTTP {status or 'error'} {body[:200] if not status else ''}".strip())
    return body


def read_sitemaps(fetch):
    children = [u for u in parse_sitemap(get(fetch, SITEMAP_INDEX)) if "post-sitemap" in u]
    if not children:
        raise Stop(f"{SITEMAP_INDEX}: no post-sitemap*.xml listed; the sitemap layout has changed")
    urls = []
    for child in children:
        found = [u for u in parse_sitemap(get(fetch, child)) if slug_of(u)]
        print(f"{child}: {len(found)} posts")
        urls += found
    return list(dict.fromkeys(urls))


def record_sitemap(urls, out, today=None):
    """Remember when each URL was first seen; return the URLs new since the first sitemap read."""
    path = out / "_sitemap.json"
    seen = common.load(path, {})
    baseline = min(seen.values()) if seen else None
    today = today or datetime.date.today().isoformat()
    for u in urls:
        seen.setdefault(u, today)
    common.save(path, seen)
    return [u for u in urls if baseline and seen[u] > baseline]


def crawl(urls, fetch, out=common.RECIPES, limit=None):
    """Fetch and save every URL not already saved or skipped. Returns (saved, skipped)."""
    skipped_path = out / "_skipped.json"
    skipped = common.load(skipped_path, {})
    todo = [u for u in urls if u not in skipped and not (out / f"{slug_of(u)}.json").exists()]
    print(f"{len(urls)} posts, {len(todo)} not yet fetched" + (f", fetching {limit}" if limit else ""))
    todo = todo[:limit] if limit else todo
    errors = empty = saved = skips = 0
    for i, url in enumerate(todo, 1):
        status, page = fetch(url)
        if status in (403, 429):
            raise Stop(f"{url}: HTTP {status}. The site is refusing this crawler; stopping. "
                       f"Wait a day or raise --delay before running again. {saved} saved this run.")
        if status in (404, 410):
            skipped[url] = f"http {status}"
            common.save(skipped_path, skipped)
            continue
        if status != 200:
            errors += 1
            warn(f"{url}: HTTP {status or 'error'} {page[:120] if not status else ''} ({errors} in a row)")
            if errors >= MAX_ERRORS:
                raise Stop(f"{MAX_ERRORS} failed requests in a row; stopping. Re-run later to resume. {saved} saved this run.")
            continue
        errors = 0
        recipe = parse_page(page, url)
        if recipe is None:
            skipped[url], skips, empty = "no recipe", skips + 1, empty + 1
            common.save(skipped_path, skipped)
            if empty >= MAX_EMPTY:
                raise Stop(f"{MAX_EMPTY} pages in a row without a Recipe; the markup has probably changed. "
                           f"Check one of them, then remove the bad entries from {skipped_path}.")
            continue
        empty = 0
        common.save(out / f"{recipe['slug']}.json", recipe)
        saved += 1
        print(f"[{i}/{len(todo)}] {recipe['slug']}: {len(recipe['ingredients'])} ingredients, serves {recipe['serves']}")
    return saved, skips


# ---------- pool ----------

def has(pattern, *texts):
    """Whole-word match (plurals allowed) of `pattern` in any of `texts`."""
    return re.search(rf"\b(?:{pattern})(?:e?s)?\b", " ".join(texts).lower())


def main_protein(recipe):
    for ing in recipe["ingredients"]:
        name = common.norm_name(ing["name"])
        if has(NOT_PROTEIN, name):
            continue
        for protein, pattern in PROTEINS.items():
            if has(pattern, name):
                return protein
    return None


def style_of(recipe):
    for text in (recipe["title"], " ".join(recipe["categories"] + recipe["keywords"])):
        for style, pattern in STYLES:
            if has(pattern, text):
                return style
    return "other"


def why_not(recipe):
    """Reason the recipe fails the meal-prep rules, or None if it passes."""
    ings = recipe["ingredients"]
    if re.match(r"\s*how to\b", recipe.get("title") or "", re.I):
        return "not a meal (a technique post)"
    if any(re.search(r"\bleftover\b|^sauce of |\bfrom (a |the )?slow cooked\b|\bcarnitas\b|\bchili con carne\b|\bhomemade\b|\bquantity\b|\bbatch(es)? of\b",
                     common.norm_name(i.get("name") or "")) for i in ings):
        return "uses another recipe (leftovers or a homemade component)"
    if ings and sum(i.get("amount") is not None for i in ings) < len(ings) / 2:
        return "not a recipe (most ingredients have no amount: a roundup or list)"
    if m := has(NOT_MAIN, " | ".join(recipe["categories"])) or has(NOT_MAIN_KW, " | ".join(recipe["keywords"])):
        return f"not a main ({m[0]})"
    if not main_protein(recipe):
        return "no main protein"
    title = " | ".join([recipe["title"]] + recipe["categories"])
    for pattern, exception, reason in NO_REHEAT:
        if has(pattern, title) and not (exception and has(exception, title)):
            return f"doesn't reheat: {reason}"
    return None


def build_pool(recipes, overrides=None):
    overrides = overrides or {}
    pool, rejected = {}, {}
    for r in recipes:
        o = overrides.get(r["slug"]) or {}
        why = "excluded in overrides.yaml" if o.get("exclude") else why_not(r)
        if why:
            rejected[r["slug"]] = why
        else:
            pool[r["slug"]] = {"style": o.get("style") or style_of(r), "protein": main_protein(r)}
    return {"generated": datetime.date.today().isoformat(), "pool": pool, "rejected": rejected}


def load_recipes(out=common.RECIPES):
    return [common.load(p) for p in sorted(out.glob("*.json")) if not p.name.startswith("_")]


def load_overrides(path=OVERRIDES):
    return (yaml.safe_load(path.read_text()) or {}) if path.exists() else {}


def ingredient_counts(recipes, pool):
    counts = collections.Counter()
    for r in recipes:
        if r["slug"] in pool:
            counts.update({n for ing in r["ingredients"] if (n := common.norm_name(ing["name"]))})
    return sorted(counts.items(), key=lambda kv: (-kv[1], kv[0]))


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--limit", type=int, help="fetch at most N new pages")
    ap.add_argument("--delay", type=float, default=DELAY, help="seconds between requests (default 5)")
    ap.add_argument("--refresh-new", action="store_true", help="only posts that appeared since the first crawl")
    ap.add_argument("--filter", action="store_true", help="write data/recipes/_pool.json")
    ap.add_argument("--ingredients", action="store_true", help="print pool ingredient names and counts as TSV")
    ap.add_argument("--repair-units", action="store_true", help="fix dual amounts ('1 kg / 2 lb') in recipes already saved")
    args = ap.parse_args()

    if args.repair_units:
        fixed = 0
        for path in sorted(common.RECIPES.glob("*.json")):
            if not path.name.startswith("_") and (n := repair_units(recipe := common.load(path))):
                common.save(path, recipe)
                fixed += n
        print(f"{fixed} ingredient amounts repaired")
        return

    if args.filter or args.ingredients:
        recipes = load_recipes(common.RECIPES)
        result = build_pool(recipes, load_overrides())
        if args.filter:
            common.save(common.RECIPES / "_pool.json", result)
            print(f"{len(recipes)} recipes: {len(result['pool'])} in the pool, {len(result['rejected'])} rejected "
                  f"-> {common.RECIPES / '_pool.json'}", file=sys.stderr)
            for why, n in collections.Counter(result["rejected"].values()).most_common():
                print(f"  {n:5} {why}", file=sys.stderr)
        if args.ingredients:
            for name, n in ingredient_counts(recipes, result["pool"]):
                print(f"{name}\t{n}")
        return

    fetch = Fetcher(args.delay)
    try:
        urls = read_sitemaps(fetch)
        new = record_sitemap(urls, common.RECIPES)
        if args.refresh_new:
            print(f"{len(new)} posts first seen since the initial crawl")
            urls = new
        saved, skips = crawl(urls, fetch, common.RECIPES, args.limit)
    except Stop as e:
        sys.exit(f"STOPPED: {e}")
    print(f"done: {saved} recipes saved, {skips} pages without a recipe -> {common.RECIPES}")


if __name__ == "__main__":
    main()
