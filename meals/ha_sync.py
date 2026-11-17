"""Move the meal picker's files between nebula and Home Assistant.

Usage: .venv/bin/python shopping/meals/ha_sync.py pull
       .venv/bin/python shopping/meals/ha_sync.py push [--person KEY] [--week YYYY-MM-DD] [--notify]
       .venv/bin/python shopping/meals/ha_sync.py push-people

pull: reads HA's /local/meals/state.json (no login needed) and rewrites each person's planner
inputs in data/people/<key>/ from their entry: picks and ratings into history.json (offered is
left alone), ratings and tuning into tuning.json, the freezer into freezer.json, settings into
settings.json. A single-person state.json from before counts as TJ's. No state.json yet (404)
writes nothing. push: sends a person's weeks/<week>.json to pyscript.meals_publish (default:
everyone with a plan in people.yaml), with their notify target from people.yaml when --notify
and they have one. push-people: sends people.yaml, without notify targets, to
pyscript.meals_publish_people. Both use the token from ~/.config/ha/token. HA_URL in the
environment overrides the address. TJ's single-person files are moved into his folder first
(once). Exits 1 on any HTTP or file error. Shapes are in SCHEMA.md, "Home Assistant side" and
"Per person".
"""
import argparse
import datetime
import os
import pathlib
import sys

import requests

import common
from fetch_prices import STORES

HA_URL = os.environ.get("HA_URL", "http://homeassistant.local:8123").rstrip("/")
TOKEN = pathlib.Path("~/.config/ha/token").expanduser()
SCORES = {"again": 1, "fine": 0, "never": -1}
TIMEOUT = 30


# --- pull -----------------------------------------------------------------

def fetch_state(url=None):
    """state.json as a dict, or None if HA has none yet."""
    r = requests.get(url or f"{HA_URL}/local/meals/state.json", timeout=TIMEOUT)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def rated_week(slug, date, weeks):
    """The latest week the recipe was picked in, on or before the rating date (else any)."""
    picked = [w["week"] for w in weeks if slug in (w.get("picked") or [])]
    before = [w for w in picked if not date or w <= date]
    return max(before or picked, default=None)


def merge_history(history, state):
    """history.json with picks and ratings from state.json; offered and other weeks' picks kept."""
    weeks = {w["week"]: w for w in history.get("weeks", [])}
    for week, slugs in (state.get("picks") or {}).items():
        weeks.setdefault(week, {"week": week})["picked"] = list(slugs or [])
    for w in weeks.values():
        w["ratings"] = {}
    for slug, r in sorted((state.get("ratings") or {}).items()):
        week = rated_week(slug, r.get("date"), list(weeks.values()))
        if week and r.get("rating") in SCORES:
            weeks[week]["ratings"][slug] = r["rating"]
    return {**history, "weeks": sorted(weeks.values(), key=lambda w: w["week"])}


def tuning_of(state):
    tuning = state.get("tuning") or {}
    out = {"recipes": {}, "styles": {str(k).lower(): v for k, v in (tuning.get("styles") or {}).items()}}
    for slug, r in sorted((state.get("ratings") or {}).items()):
        if r.get("rating") in SCORES:
            out["recipes"][slug] = {"score": SCORES[r["rating"]], "date": r.get("date")}
        else:
            print(f"ignored rating {r.get('rating')!r} for {slug}", file=sys.stderr)
    for k in ("similar_strength", "fade_days"):
        if tuning.get(k) is not None:
            out[k] = tuning[k]
    return out


def freezer_of(state):
    return [{k: f.get(k) for k in ("cut", "grams", "date", "seasoning")} for f in state.get("freezer") or []]


def people_of(state, owner):
    """state.json's {key: entry}; a single-person state.json from before is the owner's entry."""
    if "people" in state:
        return state.get("people") or {}
    old = {k: state[k] for k in ("picks", "ratings", "tuning", "freezer") if k in state}
    return {owner: old} if old and owner else {}


def load_people(root):
    people = common.load_people(root)
    if not people:
        raise SystemExit(f"no people in {root / 'people.yaml'}")
    return people


def pull(root=common.ROOT, url=None):
    people = load_people(root)
    common.migrate_people(root, people)  # before writing, so TJ's offered history is kept
    state = fetch_state(url)
    if state is None:
        print("no state.json in HA yet; nothing written")
        return False
    entries = people_of(state, common.owner(people))
    for key in sorted(set(entries) - set(common.planned(people))):
        print(f"ignored state for {key}: not a person with a plan in people.yaml", file=sys.stderr)
    for key in common.planned(people):
        entry = entries.get(key)
        if entry is None:
            continue
        own = common.person_dir(root, key)
        common.save(own / "history.json", merge_history(common.load(own / "history.json", {"weeks": []}), entry))
        common.save(own / "tuning.json", tuning_of(entry))
        common.save(own / "freezer.json", freezer_of(entry))
        common.save(own / "settings.json", common.settings_of(entry.get("settings"), STORES))
        print(f"pulled {people[key].get('name') or key} (updated {entry.get('updated') or state.get('updated')}): "
              f"{len(entry.get('picks') or {})} weeks picked, {len(entry.get('ratings') or {})} ratings, "
              f"{len(entry.get('freezer') or [])} in the freezer")
    return True


# --- push -----------------------------------------------------------------

def service_request(name, body, token):
    """(url, headers, body) for a pyscript service call."""
    return (f"{HA_URL}/api/services/pyscript/{name}",
            {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, body)


def publish_request(person, doc, notify_target, token):
    """(url, headers, body) for meals_publish; it notifies only when given a target."""
    body = {"person": person, "doc": doc, "notify": bool(notify_target)}
    if notify_target:
        body["notify_target"] = notify_target
    return service_request("meals_publish", body, token)


def call(request):
    url, headers, body = request
    requests.post(url, headers=headers, json=body, timeout=TIMEOUT).raise_for_status()


def token_of(token):
    return token or TOKEN.read_text().strip()


def push(week, person, notify=False, root=common.ROOT, token=None, people=None):
    """Publish one person's week; notified only with `notify` and a notify target in people.yaml."""
    people = load_people(root) if people is None else people
    if person not in common.planned(people):
        raise SystemExit(f"{person} is not a person with a plan in people.yaml")
    doc = common.load(common.person_dir(root, person) / "weeks" / f"{week}.json")
    if doc is None:
        raise SystemExit(f"no week file for {person} {week}; run plan_week.py first")
    target = people[person].get("notify") if notify else None
    call(publish_request(person, doc, target, token_of(token)))
    print(f"published {people[person].get('name') or person} {week}: {len(doc.get('options', []))} options"
          + (", notified" if target else ""))


def people_json(people):
    """people.json for HA: {key: {name, shared, has_plan}}; notify targets stay on nebula."""
    return {k: {"name": v.get("name") or k, "shared": bool(v.get("shared")), "has_plan": not v.get("shared")}
            for k, v in people.items()}


def push_people(root=common.ROOT, token=None):
    people = load_people(root)
    call(service_request("meals_publish_people", {"people": people_json(people)}, token_of(token)))
    print(f"published people.json: {len(people)} people")


def push_all(week, keys, notify, root=common.ROOT, token=None):
    """push for each key; one failure doesn't stop the rest. Raises SystemExit naming the failures."""
    people, failed = load_people(root), []
    for key in keys:
        try:
            push(week, key, notify, root, token, people)
        except (SystemExit, requests.RequestException, OSError, ValueError) as e:
            print(f"push {key} failed: {type(e).__name__}: {e}", file=sys.stderr)
            failed.append(key)
    if failed:
        raise SystemExit(f"push failed for {', '.join(failed)}")


def main(argv=None, root=common.ROOT):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("pull")
    sub.add_parser("push-people")
    p = sub.add_parser("push")
    p.add_argument("--person", help="a key in people.yaml; default: everyone with a plan")
    p.add_argument("--week", help="any date in the week (YYYY-MM-DD); defaults to this week")
    p.add_argument("--notify", action="store_true", help="notify the person if people.yaml gives them a notify target")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "pull":
            pull(root)
        elif args.cmd == "push-people":
            push_people(root)
        else:
            common.migrate_people(root)
            week = common.week_of(datetime.date.fromisoformat(args.week) if args.week else None)
            push_all(week, [args.person] if args.person else common.planned(load_people(root)), args.notify, root)
    except (requests.RequestException, OSError, ValueError) as e:
        raise SystemExit(f"{args.cmd} failed: {type(e).__name__}: {e}")


if __name__ == "__main__":
    main()
