"""Meal picker services for the Meals dashboard card.

Services (shapes in shopping/meals/SCHEMA.md, "Home Assistant side" and "Per person"):
  pyscript.meals_publish(person, doc, notify, notify_target)   write week-<person>.json; optionally notify
  pyscript.meals_publish_people(people)                        write people.json
  pyscript.meals_pick(person, week, slugs)                     the week's picks (0 to MAX_PICKS)
  pyscript.meals_rate(person, slug, rating, title, style, cuisine)
  pyscript.meals_tune(person, style, weight, similar_strength, fade_days, reset)
  pyscript.meals_freezer(person, add | remove | set)
  pyscript.meals_settings(person, recipes, serves, protein_g, swaps, stores)

Every service but meals_publish_people needs `person`: a key in people.json that isn't a shared
screen. Files live in /config/www/meals/ (served at /local/meals/) and are written atomically.
pyscript is the only writer of state.json ({"updated", "people": {key: {...}}}); nebula reads it
with ha_sync.py pull. A single-person state.json from before is moved into TJ's entry on first
load. Bad input is logged as an error and nothing is written. Each change fires
meals_state_changed with the person. Deployed to /config/pyscript/meals.py.
"""
import datetime
import json

WWW = "/config/www/meals"
OWNER = "148d862cd5a04037b7f641dbc0f55c72"  # TJ: the single-person state.json from before is his
MEALS_VIEW = "/lovelace/meals"
EVENT = "meals_state_changed"
RATINGS = ("again", "fine", "never")
MAX_PICKS = 3  # the most `recipes` a person can set; the card limits picks to their own setting
DEFAULT_SIMILAR = 0.3
DEFAULT_FADE_DAYS = 365
STORES = ("aldi", "woolworths", "coles")  # fetch_prices.STORES
SWAP_IDS = ("cream", "oil", "butter", "sugar")  # swaps.yaml ids; butter covers butter-frying too
# Settings a person starts from (TJ's) and the whole-number ranges allowed; common.py has the same.
DEFAULT_SETTINGS = {"recipes": 2, "serves": 6, "protein_g": 50,
                    "swaps": {"cream": True, "oil": True, "butter": True, "sugar": True},
                    "stores": ["aldi", "woolworths", "coles"]}
SETTING_RANGES = {"recipes": (1, 3), "serves": (2, 8), "protein_g": (20, 80)}
PERSON_KEYS = ("picks", "ratings", "tuning", "freezer")

# The pure part: plain functions on the state dict, no decorators or HA globals, and only
# constructs pyscript interprets the same as Python (no key= callables, no generators).
# tests/test_ha_meals.py execs this whole file with stub decorators and tests these.
# Each transition validates first and raises ValueError before changing anything.


def copy(x):
    return json.loads(json.dumps(x))


def empty_state():
    """One person's entry in state.json (picks, ratings, tuning, freezer, settings, updated)."""
    return {"updated": None, "picks": {}, "ratings": {},
            "tuning": {"styles": {}, "similar_strength": DEFAULT_SIMILAR, "fade_days": DEFAULT_FADE_DAYS},
            "freezer": [], "settings": copy(DEFAULT_SETTINGS)}


def normalise(state):
    """A person's entry with any missing keys filled in."""
    out = empty_state()
    for k, v in (state or {}).items():
        out[k] = v
    for k, v in empty_state()["tuning"].items():
        if k not in out["tuning"]:
            out["tuning"][k] = v
    merged = copy(DEFAULT_SETTINGS)
    for k, v in (out["settings"] or {}).items():
        merged[k] = v
    for k, v in DEFAULT_SETTINGS["swaps"].items():
        if k not in merged["swaps"]:
            merged["swaps"][k] = v
    out["settings"] = merged
    return out


def normalise_all(state):
    """A loaded state.json as {"updated", "people": {key: entry}}. A single-person state.json from
    before people (picks, ratings, tuning or freezer at the top) becomes OWNER's entry; loading it
    again changes nothing."""
    state = state or {}
    people = {}
    for key, entry in (state.get("people") or {}).items():
        people[key] = normalise(entry)
    old = {}
    for k in PERSON_KEYS:
        if k in state:
            old[k] = state[k]
    if old and OWNER not in people:
        old["updated"] = state.get("updated")
        people[OWNER] = normalise(old)
    return {"updated": state.get("updated"), "people": people}


def check_person(people, person):
    """`person` must be a key in people.json and not a shared screen; raises ValueError."""
    if not isinstance(person, str) or not person:
        raise ValueError(f"person is required, got {person!r}")
    if not people:
        raise ValueError("no people.json yet; run ha_sync.py push-people on nebula")
    if person not in people:
        raise ValueError(f"unknown person {person!r}")
    if (people[person] or {}).get("shared"):
        raise ValueError(f"{person} is a shared screen; pick who is using it")
    return person


def check_people(people):
    """The people.json map: {key: {name, shared, has_plan}}; raises ValueError."""
    if not isinstance(people, dict) or not people:
        raise ValueError(f"people must be a map of person keys, got {people!r}")
    out = {}
    for key, p in people.items():
        if not isinstance(key, str) or not key or not isinstance(p, dict):
            raise ValueError(f"bad person entry {key!r}: {p!r}")
        if not isinstance(p.get("name"), str) or not p["name"].strip():
            raise ValueError(f"person {key} needs a name")
        unknown = [k for k in p if k not in ("name", "shared", "has_plan")]
        if unknown:
            raise ValueError(f"person {key}: unknown fields {unknown} (notify targets stay on nebula)")
        shared = bool(p.get("shared"))
        out[key] = {"name": p["name"].strip(), "shared": shared, "has_plan": bool(p.get("has_plan", not shared))}
    return out


def whole_in(name, value, lo, hi):
    """A whole number from lo to hi (2.0 from a number selector counts); raises ValueError."""
    if not is_number(value) or value != int(value) or not lo <= value <= hi:
        raise ValueError(f"{name} must be a whole number from {lo} to {hi}, got {value!r}")
    return int(value)


def settings(state, recipes=None, serves=None, protein_g=None, swaps=None, stores=None):
    """Update a person's settings; `swaps` is a partial map {id: bool}. Validates everything first."""
    if recipes is None and serves is None and protein_g is None and swaps is None and stores is None:
        raise ValueError("nothing to change")
    new = {}
    limits = {"recipes": recipes, "serves": serves, "protein_g": protein_g}
    for k in ("recipes", "serves", "protein_g"):
        if limits[k] is not None:
            new[k] = whole_in(k, limits[k], SETTING_RANGES[k][0], SETTING_RANGES[k][1])
    if swaps is not None:
        if not isinstance(swaps, dict) or not swaps:
            raise ValueError(f"swaps must be a map of swap id to true/false, got {swaps!r}")
        for k, v in swaps.items():
            if k not in SWAP_IDS or not isinstance(v, bool):
                raise ValueError(f"swaps: {k!r} must be one of {SWAP_IDS} set to true or false, got {v!r}")
    if stores is not None:
        if not isinstance(stores, list) or not stores or len(stores) != len(dict.fromkeys(stores)):
            raise ValueError(f"stores must be a non-empty list of different stores, got {stores!r}")
        for s in stores:
            if s not in STORES:
                raise ValueError(f"unknown store {s!r}; stores are {STORES}")
        new["stores"] = [s for s in STORES if s in stores]
    current = state["settings"]
    for k, v in new.items():
        current[k] = v
    if swaps is not None:
        for k, v in swaps.items():
            current["swaps"][k] = v
    return state


def is_date(text):
    try:
        return datetime.date.fromisoformat(text).isoformat() == text
    except (TypeError, ValueError):
        return False


def is_number(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool)


def text_or_none(x):
    return None if x is None or x == "" else str(x)


def pick(state, week, slugs):
    if not is_date(week):
        raise ValueError(f"week must be YYYY-MM-DD, got {week!r}")
    if slugs is None:
        slugs = []
    if not isinstance(slugs, list) or not all([isinstance(s, str) and s for s in slugs]):
        raise ValueError(f"slugs must be a list of recipe slugs, got {slugs!r}")
    if len(slugs) > MAX_PICKS or len(slugs) != len(dict.fromkeys(slugs)):
        raise ValueError(f"pick at most {MAX_PICKS} different recipes, got {slugs}")
    state["picks"][week] = list(slugs)
    return state


def rate(state, slug, rating, today, title=None, style=None, cuisine=None):
    if not isinstance(slug, str) or not slug:
        raise ValueError(f"slug is required, got {slug!r}")
    if rating == "clear":
        state["ratings"].pop(slug, None)
        return state
    if rating not in RATINGS:
        raise ValueError(f"rating must be one of {RATINGS + ('clear',)}, got {rating!r}")
    state["ratings"][slug] = {"rating": rating, "date": today, "title": text_or_none(title),
                              "style": text_or_none(style), "cuisine": text_or_none(cuisine)}
    return state


def tune(state, style=None, weight=None, similar_strength=None, fade_days=None, reset=False):
    if style is not None and (not isinstance(style, str) or not style.strip()):
        raise ValueError(f"style must be a name, got {style!r}")
    if weight is not None and (style is None or not is_number(weight)):
        raise ValueError(f"weight needs a style and a number, got style={style!r} weight={weight!r}")
    if similar_strength is not None and (not is_number(similar_strength) or similar_strength < 0):
        raise ValueError(f"similar_strength must be a number >= 0, got {similar_strength!r}")
    if fade_days is not None and (not is_number(fade_days) or fade_days < 0 or fade_days != int(fade_days)):
        raise ValueError(f"fade_days must be a whole number >= 0, got {fade_days!r}")
    if style is None and similar_strength is None and fade_days is None and not reset:
        raise ValueError("nothing to change")
    tuning = state["tuning"]
    if reset:
        state["ratings"] = {}
        tuning["styles"] = {}
    if style is not None:
        key = style.strip().lower()
        if weight is None:
            tuning["styles"].pop(key, None)
        else:
            tuning["styles"][key] = weight
    if similar_strength is not None:
        tuning["similar_strength"] = similar_strength
    if fade_days is not None:
        tuning["fade_days"] = int(fade_days)
    return state


FREEZER_FIELDS = ("cut", "grams", "date", "seasoning")


def freezer_item(fields, base):
    """`base` updated with `fields`, checked; raises ValueError."""
    unknown = [k for k in fields if k not in FREEZER_FIELDS]
    if unknown:
        raise ValueError(f"unknown freezer fields {unknown}")
    item = dict(base)
    item.update(fields)
    if not isinstance(item.get("cut"), str) or not item["cut"].strip():
        raise ValueError(f"freezer cut is required, got {item.get('cut')!r}")
    if not is_number(item.get("grams")) or item["grams"] <= 0:
        raise ValueError(f"freezer grams must be a number > 0, got {item.get('grams')!r}")
    if not is_date(item.get("date")):
        raise ValueError(f"freezer date must be YYYY-MM-DD, got {item.get('date')!r}")
    item["cut"] = item["cut"].strip()
    item["seasoning"] = item.get("seasoning") or ""
    return item


def freezer_index(state, index):
    if is_number(index) and index == int(index):
        index = int(index)  # a number selector may send 2.0
    if not isinstance(index, int) or isinstance(index, bool) or not 0 <= index < len(state["freezer"]):
        raise ValueError(f"no freezer item {index!r} (have {len(state['freezer'])})")
    return index


def freezer(state, today, add=None, remove=None, set=None):
    if len([x for x in (add, remove, set) if x is not None]) != 1:
        raise ValueError("give exactly one of add, remove or set")
    if add is not None:
        if not isinstance(add, dict):
            raise ValueError(f"add must be {{cut, grams, seasoning}}, got {add!r}")
        state["freezer"].append(freezer_item(add, {"date": today, "seasoning": ""}))
    elif remove is not None:
        state["freezer"].pop(freezer_index(state, remove))
    else:
        if not isinstance(set, dict):
            raise ValueError(f"set must be {{index, fields...}}, got {set!r}")
        fields = dict(set)
        i = freezer_index(state, fields.pop("index", None))
        state["freezer"][i] = freezer_item(fields, state["freezer"][i])
    return state


def check_week(doc):
    if not isinstance(doc, dict) or not is_date(doc.get("week")) or not isinstance(doc.get("options"), list):
        raise ValueError("doc must be a week file with week and options")
    return doc


def notification(doc):
    """(title, message) for a newly published week. Only the person's own stores are mentioned."""
    titles = [o.get("title") or o.get("slug") for o in doc.get("options", [])]
    lines = [f"- {t}" for t in titles]
    if doc.get("stale"):
        lines.append(f"Warning: prices are from an earlier week ({doc.get('prices_week')}).")
    stores = doc.get("stores")
    missing = [s.title() for s, ok in (doc.get("stores_ok") or {}).items() if not ok and (stores is None or s in stores)]
    if missing:
        lines.append(f"No fresh prices from {', '.join(missing)}.")
    return f"Meals for the week of {doc['week']}", "\n".join(lines)


def stamp(state, now):
    state["updated"] = now
    return state


def check_target(target):
    """A notify service name ("notify.mobile_app_x") or None; raises ValueError."""
    if target is None or target == "":
        return None
    if not isinstance(target, str) or not target.startswith("notify.") or len(target) < 8:
        raise ValueError(f"notify_target must be a notify service like notify.mobile_app_x, got {target!r}")
    return target


def apply_change(full, people, person, apply, args, now):
    """Run apply(entry, *args) on a copy of the person's entry in the whole state; the new whole
    state. Raises ValueError (nothing changed) on a bad person or bad input."""
    check_person(people, person)
    trial = copy(full)
    entry = trial["people"].get(person) or empty_state()
    apply(entry, *args)
    trial["people"][person] = stamp(entry, now)
    return stamp(trial, now)


# --- pyscript only: everything below uses injected globals ----------------

@pyscript_executor
def _read_json(path):
    # Executor functions run as plain Python and don't see this file's imports.
    import json
    import os
    if not os.path.exists(path):
        return None
    with open(path) as f:
        return json.load(f)


@pyscript_executor
def _write_text(path, text):
    import os
    import uuid
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{uuid.uuid4().hex}.tmp"
    with open(tmp, "w") as f:
        f.write(text)
    os.replace(tmp, path)  # atomic, so the card and nebula never read a half-written file


_cache = {}


def _now():
    return datetime.datetime.now().isoformat(timespec="seconds")


def _people():
    """people.json, read once and then kept (meals_publish_people replaces it); {} if none yet."""
    if "people" not in _cache:
        _cache["people"] = _read_json(f"{WWW}/people.json") or {}
    return _cache["people"]


def _change(name, person, apply, *args):
    """Run apply(person's entry, *args) on the cached state, then write it and tell the cards.

    Arguments are passed explicitly: pyscript lambdas can't see the enclosing function's locals.

    The state is read from disk once and kept in memory, so two quick taps can't both
    read the old file and lose one change (pyscript only switches tasks at awaits).
    """
    if "state" not in _cache:
        _cache["state"] = normalise_all(_read_json(f"{WWW}/state.json"))
    try:
        trial = apply_change(_cache["state"], _people(), person, apply, args, _now())
    except ValueError as e:
        log.error(f"{name}: {e}")
        return {"ok": False, "error": str(e)}
    _cache["state"] = trial
    _write_text(f"{WWW}/state.json", json.dumps(trial, indent=1, ensure_ascii=False))
    event.fire(EVENT, file="state", person=person, updated=trial["updated"])
    return {"ok": True, "person": person, "updated": trial["updated"]}


@service(supports_response="optional")
def meals_publish(person=None, doc=None, notify=False, notify_target=None):
    """yaml
name: Publish meal options
description: Write a person's week (data/people/<key>/weeks/<week>.json from nebula) to /local/meals/week-<person>.json.
fields:
  person:
    description: The person's Home Assistant user id (a key in people.json).
    required: true
    selector:
      text:
  doc:
    description: The week file.
    required: true
    selector:
      object:
  notify:
    description: Send the phone notification with the options (only with a notify_target).
    default: false
    selector:
      boolean:
  notify_target:
    description: The person's notify service (people.yaml on nebula), e.g. notify.mobile_app_x.
    selector:
      text:
"""
    try:
        check_person(_people(), person)
        check_week(doc)
        target = check_target(notify_target)
    except ValueError as e:
        log.error(f"meals_publish: {e}")
        return {"ok": False, "error": str(e)}
    _write_text(f"{WWW}/week-{person}.json", json.dumps(doc, ensure_ascii=False))
    event.fire(EVENT, file="week", person=person, week=doc["week"])
    sent = bool(notify and target)
    if sent:
        title, message = notification(doc)
        domain, name = target.split(".", 1)
        service.call(domain, name, title=title, message=message,
                     data={"url": MEALS_VIEW, "clickAction": MEALS_VIEW})
    log.info(f"meals_publish: {person} week {doc['week']}, {len(doc['options'])} options, notified={sent}")
    return {"ok": True, "person": person, "week": doc["week"], "notified": sent}


@service(supports_response="optional")
def meals_publish_people(people=None):
    """yaml
name: Publish the people list
description: "Write /local/meals/people.json ({key: {name, shared, has_plan}}) from nebula's people.yaml."
fields:
  people:
    description: "{user id: {name, shared, has_plan}}; no notify targets."
    required: true
    selector:
      object:
"""
    try:
        checked = check_people(people)
    except ValueError as e:
        log.error(f"meals_publish_people: {e}")
        return {"ok": False, "error": str(e)}
    _write_text(f"{WWW}/people.json", json.dumps(checked, indent=1, ensure_ascii=False))
    _cache["people"] = checked
    event.fire(EVENT, file="people")
    log.info(f"meals_publish_people: {len(checked)} people")
    return {"ok": True, "people": len(checked)}


@service(supports_response="optional")
def meals_pick(person=None, week=None, slugs=None):
    """yaml
name: Pick meals
description: Set a person's picks for the week (0 to their number of recipes).
fields:
  person:
    description: The person's Home Assistant user id (a key in people.json).
    required: true
    selector:
      text:
  week:
    description: Week (the Wednesday, YYYY-MM-DD).
    required: true
    selector:
      text:
  slugs:
    description: Picked recipe slugs.
    selector:
      object:
"""
    return _change("meals_pick", person, pick, week, slugs)


@service(supports_response="optional")
def meals_rate(person=None, slug=None, rating=None, title=None, style=None, cuisine=None):
    """yaml
name: Rate a meal
description: Rate a recipe again, fine or never, or clear its rating, for one person.
fields:
  person:
    description: The person's Home Assistant user id (a key in people.json).
    required: true
    selector:
      text:
  slug:
    required: true
    selector:
      text:
  rating:
    required: true
    selector:
      select:
        options: [again, fine, never, clear]
  title:
    selector:
      text:
  style:
    selector:
      text:
  cuisine:
    selector:
      text:
"""
    today = datetime.date.today().isoformat()
    return _change("meals_rate", person, rate, slug, rating, today, title, style, cuisine)


@service(supports_response="optional")
def meals_tune(person=None, style=None, weight=None, similar_strength=None, fade_days=None, reset=False):
    """yaml
name: Tune meal scoring
description: Set or remove a style weight, the similar-recipe nudge, the fade time, or reset ratings and weights, for one person.
fields:
  person:
    description: The person's Home Assistant user id (a key in people.json).
    required: true
    selector:
      text:
  style:
    selector:
      text:
  weight:
    description: Weight for the style; leave out to remove it.
    selector:
      number:
        min: -3
        max: 3
        step: 0.1
  similar_strength:
    selector:
      number:
        min: 0
        max: 2
        step: 0.05
  fade_days:
    description: Days for a rating to fade; 0 turns fading off.
    selector:
      number:
        min: 0
        max: 3650
  reset:
    description: Clear every rating and style weight.
    selector:
      boolean:
"""
    return _change("meals_tune", person, tune, style, weight, similar_strength, fade_days, bool(reset))


@service(supports_response="optional")
def meals_freezer(person=None, add=None, remove=None, set=None):
    """yaml
name: Edit the freezer list
description: Add, remove or change an item on a person's freezer list (give exactly one).
fields:
  person:
    description: The person's Home Assistant user id (a key in people.json).
    required: true
    selector:
      text:
  add:
    description: "{cut, grams, seasoning}; dated today."
    selector:
      object:
  remove:
    description: Index of the item to remove.
    selector:
      number:
        min: 0
        max: 100
  set:
    description: "{index, and any of cut, grams, date, seasoning}."
    selector:
      object:
"""
    today = datetime.date.today().isoformat()
    return _change("meals_freezer", person, freezer, today, add, remove, set)


@service(supports_response="optional")
def meals_settings(person=None, recipes=None, serves=None, protein_g=None, swaps=None, stores=None):
    """yaml
name: Meal settings
description: Change a person's settings (used from next week's plan). Give any of the fields.
fields:
  person:
    description: The person's Home Assistant user id (a key in people.json).
    required: true
    selector:
      text:
  recipes:
    description: Recipes cooked a week.
    selector:
      number:
        min: 1
        max: 3
  serves:
    description: Serves per recipe.
    selector:
      number:
        min: 2
        max: 8
  protein_g:
    description: Protein per serve, in grams.
    selector:
      number:
        min: 20
        max: 80
  swaps:
    description: "Partial map of swap id to on/off: {cream, oil, butter, sugar}."
    selector:
      object:
  stores:
    description: "Stores to shop at, a non-empty list of aldi, woolworths, coles."
    selector:
      object:
"""
    return _change("meals_settings", person, settings, recipes, serves, protein_g, swaps, stores)
