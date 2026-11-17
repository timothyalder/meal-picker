"""Shared paths and helpers for the meal picker. Shapes are in SCHEMA.md."""
import datetime
import json
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parent
DATA = ROOT / "data"
RECIPES = DATA / "recipes"
PRICES = DATA / "prices"
WEEKS = DATA / "weeks"


def load(path, default=None):
    path = pathlib.Path(path)
    return json.loads(path.read_text()) if path.exists() else default


def save(path, obj):
    path = pathlib.Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, ensure_ascii=False))
    tmp.replace(path)  # atomic, so the dashboard never reads a half-written file


def week_of(day=None):
    """ISO date of the Wednesday that starts the specials week containing `day`."""
    day = day or datetime.date.today()
    return (day - datetime.timedelta(days=(day.weekday() - 2) % 7)).isoformat()


def norm_name(name):
    """Key for products.yaml: lower case, no brackets, punctuation or extra spaces."""
    name = re.sub(r"\(.*?\)", " ", name.lower())
    name = re.sub(r"[^a-z0-9 ]+", " ", name)
    name = re.sub(r"\s+", " ", name).strip()
    return re.sub(r"^of ", "", name)  # "2 cloves of garlic" leaves "of garlic"


# Unit as written in a recipe -> (base unit, factor). Volumes are metric (AU cups and spoons).
UNITS = {
    "g": ("g", 1), "gram": ("g", 1), "grams": ("g", 1), "kg": ("g", 1000),
    "oz": ("g", 28.35), "lb": ("g", 453.6),
    "ml": ("ml", 1), "l": ("ml", 1000), "litre": ("ml", 1000), "litres": ("ml", 1000),
    "cup": ("ml", 250), "cups": ("ml", 250),
    "tbsp": ("ml", 20), "tablespoon": ("ml", 20), "tablespoons": ("ml", 20),
    "tsp": ("ml", 5), "teaspoon": ("ml", 5), "teaspoons": ("ml", 5),
    "kilogram": ("g", 1000), "kilograms": ("g", 1000), "ounce": ("g", 28.35), "ounces": ("g", 28.35),
    "lbs": ("g", 453.6), "pound": ("g", 453.6), "pounds": ("g", 453.6),
    "millilitre": ("ml", 1), "millilitres": ("ml", 1), "milliliter": ("ml", 1), "milliliters": ("ml", 1),
    "liter": ("ml", 1000), "liters": ("ml", 1000),
    "tbs": ("ml", 20), "tbsps": ("ml", 20), "tsps": ("ml", 5),
}


def to_base(amount, unit):
    """(amount, unit) as written -> (quantity, "g" | "ml" | "ea"); (None, None) if no amount."""
    if amount is None:
        return None, None
    base, factor = UNITS.get((unit or "").lower().rstrip("."), ("ea", 1))
    return amount * factor, base


def load_yaml(path, default=None):
    """A hand-edited config file; `default` if it is missing or empty."""
    import yaml
    path = pathlib.Path(path)
    return (yaml.safe_load(path.read_text()) if path.exists() else None) or default


# --- people (SCHEMA.md "Per person") ----------------------------------------

OWNER_NAME = "TJ"  # the single-person files from before 2026-11-16 are his
PERSON_FILES = ("history.json", "tuning.json", "freezer.json")
# Settings a person starts from (TJ's), and the ranges the Tune tab may set.
DEFAULT_SETTINGS = {"recipes": 2, "serves": 6, "protein_g": 50,
                    "swaps": {"cream": True, "oil": True, "butter": True, "sugar": True},
                    "stores": ["aldi", "woolworths", "coles"]}
SETTING_RANGES = {"recipes": (1, 3), "serves": (2, 8), "protein_g": (20, 80)}


def load_people(root=ROOT):
    """people.yaml as {key: {name, notify?, shared?}}; {} when there is none."""
    return {str(k): v or {} for k, v in (load_yaml(pathlib.Path(root) / "people.yaml", {}) or {}).items()}


def planned(people):
    """Keys of the people with a plan of their own (everyone but shared screens), in file order."""
    return [k for k, v in people.items() if not v.get("shared")]


def owner(people):
    """TJ's key, or None."""
    return next((k for k, v in people.items() if v.get("name") == OWNER_NAME), None)


def person_dir(root, key):
    return pathlib.Path(root) / "data" / "people" / key


def settings_of(raw, stores=None):
    """A settings.json (or state.json settings) with defaults filled in. A value out of range or of the
    wrong type falls back to the default, so a bad file never stops the planner."""
    raw = raw if isinstance(raw, dict) else {}
    out = json.loads(json.dumps(DEFAULT_SETTINGS))
    for k, (lo, hi) in SETTING_RANGES.items():
        v = raw.get(k)
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v == int(v) and lo <= v <= hi:
            out[k] = int(v)
    for k, v in (raw.get("swaps") or {}).items() if isinstance(raw.get("swaps"), dict) else ():
        out["swaps"][str(k)] = bool(v)
    known = list(stores or DEFAULT_SETTINGS["stores"])
    if isinstance(raw.get("stores"), list):
        chosen = [s for s in known if s in raw["stores"]]
        out["stores"] = chosen or known
    return out


def migrate_people(root=ROOT, people=None, dry_run=False):
    """Move TJ's single-person files (data/history.json, tuning.json, freezer.json, data/weeks/*)
    into data/people/<his key>/. Idempotent: a file already there is never overwritten (the old
    one is left and named in the returned list as skipped). Returns [(src, dst, done)]."""
    root = pathlib.Path(root)
    people = load_people(root) if people is None else people
    key = owner(people)
    if key is None:
        return []
    data, dest = root / "data", person_dir(root, key)
    pairs = [(data / f, dest / f) for f in PERSON_FILES]
    pairs += [(p, dest / "weeks" / p.name) for p in sorted((data / "weeks").glob("*.json"))]
    out = []
    for src, dst in pairs:
        if not src.exists():
            continue
        done = not dst.exists()
        if done and not dry_run:
            dst.parent.mkdir(parents=True, exist_ok=True)
            src.replace(dst)
        out.append((src, dst, done))
    weeks = data / "weeks"
    if not dry_run and weeks.is_dir() and not any(weeks.iterdir()):
        weeks.rmdir()
    return out


# Words that describe size, preparation or packaging, not what to buy ("2 large onions, finely chopped").
NOISE = set("""
a an of the each x and about approx approximately extra more good little few some plus big heaped
large small medium big mini baby-sized jumbo thick thin
fresh freshly finely roughly coarsely thinly very lightly loosely packed firmly
chopped sliced diced grated minced shredded julienned cubed quartered halved peeled
trimmed drained rinsed thawed melted softened cut pieces piece bite size
boneless skinless raw uncooked
can cans canned tin tins tinned packet packets pkt jar bottle bunch bunches handful handfuls
sprig sprigs stem stems stalk stalks dash dashes pinch grind grinds splash dollop
low sodium reduced salt
""".split())


def singular(word):
    if len(word) > 4 and word.endswith("ies"):
        return word[:-3] + "y"
    if len(word) > 4 and word.endswith(("oes", "ches", "shes")):
        return word[:-2]
    if len(word) > 3 and word.endswith("s") and not word.endswith("ss"):
        return word[:-1]
    return word


# American, British and Australian names for the same thing, tried after the name as written.
SYNONYMS = [
    (r"\byogurt\b", "yoghurt"), (r"\bcilantro\b", "coriander"), (r"\bscallions?\b", "spring onion"),
    (r"\bgreen onions?\b", "spring onion"), (r"\bshrimps?\b", "prawns"), (r"\bheavy cream\b", "cream"),
    (r"\ball purpose flour\b", "plain flour"), (r"\bbell peppers?\b", "capsicum"), (r"\bground (beef|pork|lamb|chicken|turkey)\b", r"\1 mince"),
    (r"\bchuck beef\b", "beef chuck"), (r"\bzucchinis?\b", "zucchini"), (r"\beggplants?\b", "eggplant"),
    (r"\beschalots?\b", "eschallot"), (r"\bchilis?\b", "chilli"), (r"\bchilies\b", "chilli"),
]


def phrase_in(key, table):
    """The longest table key of two or more words found whole inside `key`, or None."""
    padded = f" {key} "
    hits = [k for k in table if " " in k and f" {k} " in padded]
    return max(hits, key=len) if hits else None


def resolve(key, table):
    """resolve_as_written, then with SYNONYMS swapped in, then the longest known phrase inside it."""
    if re.search(r"\bbone in\b", key) and re.search(r"\bchicken thighs?\b", key) and "chicken thigh cutlets" in table:
        return "chicken thigh cutlets"
    found = resolve_as_written(key, table)
    if found:
        return found
    alt = key
    for pattern, repl in SYNONYMS:
        alt = re.sub(pattern, repl, alt)
    if alt != key and (found := resolve_as_written(alt, table)):
        return found
    return phrase_in(alt, table)


def resolve_as_written(key, table):
    """The table key an ingredient name means, or None: exact, then without size and preparation
    words, then each side of an "or", then singular. ("large garlic cloves, minced" -> "garlic")"""
    def tries(name):
        words = name.split()
        core = [w for w in words if w not in NOISE]
        yield name
        yield " ".join(core)
        yield " ".join(singular(w) for w in core)
    if key in table:
        return key
    # "x 150 180g 5 6 oz fish fillets", "chicken thigh fillets cut into bite size pieces"
    key = re.sub(r"\b(cut|separated|plus|for|to serve|recommended|of choice|about)\b.*$", "", key)
    key = " ".join(w for w in key.split() if not re.fullmatch(r"x|\d+\w*|oz|lb|g|kg|cm|inch|mm", w)).strip()
    if key in table:
        return key
    parts = [p.split() for p in re.split(r"\bor\b", key)]
    tail = parts[-1][1:]  # "chicken or vegetable stock": the alternatives share "stock"
    alternatives = [" ".join(p + tail if len(p) == 1 and tail else p) for p in parts if p]
    for part in [key] + alternatives:
        for cand in tries(part.strip()):
            if cand and cand in table:
                return cand
    return None
