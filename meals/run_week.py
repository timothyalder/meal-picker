"""The scheduled weekly run: pull state from HA, fetch prices once, then plan and publish per person.

Usage: .venv/bin/python shopping/meals/run_week.py [--week YYYY-MM-DD] [--allow-stale] [--dry-run]

Cron calls it hourly on Wednesday and twice a day Thursday and Friday (crontab.example);
it decides for itself what is left to do, for each person with a plan in people.yaml, from
data/run_state.json ({week: {person key: {published, stale, stores_ok, at, first, kept?}}}):
  - fresh options already out for the week: nothing to do
  - every store read: plan and publish, with the phone notification
  - Aldi read but a hotprices store (Woolworths, Coles: one community source, they fail
    together or alone) missing: publish with what's there straight away ("aldi-only" when
    both are missing, "partial" when one is); later calls retry the missing ones and
    republish (no second notification) whenever more stores come in
  - no Aldi: wait for the next call, until Friday 18:00 (or --allow-stale),
    then plan on the latest earlier snapshot and publish flagged stale
A republish never changes the options someone may already have picked from: if the
better prices give a different set, that person's published week is kept and their week is done.
A failed pull is logged and the run goes on with the last pulled picks and ratings.
people.json is pushed to HA on every run that publishes (pyscript checks people against it).
Only people with a `notify` in people.yaml get the phone notification. A plan or push that
fails for one person is logged and the others carry on; that person is retried next call.
TJ's single-person files and run_state entries from before are moved to his name first (once).

Exit codes: 0 done for the week for everyone (fresh options out, or the published ones kept),
and --dry-run; 1 waiting on prices for someone (nothing out yet, or out partial, Aldi-only or
stale and still retrying); 2 a step failed for someone (plan, push, or reading a data file).
"""
import argparse
import datetime
import json
import subprocess
import sys

import common
from fetch_prices import STORES

HOTPRICES = tuple(s for s in STORES if s != "aldi")  # Woolworths and Coles: the hotprices-au files
HERE = common.ROOT
STALE_FROM = (2, 18)       # days after Wednesday, hour: Friday 18:00
RANK = {None: 0, "stale": 1, "aldi-only": 2, "partial": 3, "fresh": 4}
DONE, WAITING, FAILED = 0, 1, 2


def log(msg, now=None):
    print(f"{(now or datetime.datetime.now()):%Y-%m-%d %H:%M:%S} {msg}", flush=True)


def stale_allowed(week, now):
    days, hour = STALE_FROM
    start = datetime.datetime.combine(datetime.date.fromisoformat(week), datetime.time(hour))
    return now >= start + datetime.timedelta(days=days)


def mode_of(stores_ok, stale_ok):
    """What can be published from these store results: fresh, partial, aldi-only, stale or None.

    A store not in `stores_ok` counts as not read."""
    if not stores_ok.get("aldi"):
        return "stale" if stale_ok else None
    have = [s for s in HOTPRICES if stores_ok.get(s)]
    if len(have) == len(HOTPRICES):
        return "fresh"
    return "partial" if have else "aldi-only"


# --- steps (subprocesses; tests replace these) ----------------------------

def script(name, *args):
    """Run a sibling script with this interpreter; True if it exited 0. Output only on failure."""
    r = subprocess.run([sys.executable, str(HERE / name), *args], capture_output=True, text=True, cwd=HERE)
    if r.returncode:
        for line in (r.stdout + r.stderr).strip().splitlines()[-5:]:
            print(f"    {line}", flush=True)
    return r.returncode == 0


class Steps:
    def pull(self):
        return script("ha_sync.py", "pull")

    def fetch(self, week):
        """Store -> read ok, from the week's snapshot after fetch_prices has run."""
        script("fetch_prices.py", "--week", week)  # exits 1 when any store failed; the snapshot says which
        stores = (common.load(common.PRICES / f"{week}.json") or {}).get("stores") or {}
        return {s: bool((stores.get(s) or {}).get("ok")) for s in STORES}

    def push_people(self):
        return script("ha_sync.py", "push-people")

    def plan(self, week, allow_stale, person):
        return script("plan_week.py", "--week", week, "--person", person, *(["--allow-stale"] if allow_stale else []))

    def push(self, week, notify, person):
        return script("ha_sync.py", "push", "--person", person, "--week", week, *(["--notify"] if notify else []))


# --- the run --------------------------------------------------------------

def offered(root, week, person):
    path = common.person_dir(root, person) / "weeks" / f"{week}.json"
    return [o["slug"] for o in (common.load(path) or {}).get("options", [])]


def backup(root, week, person):
    """Copies of the files plan_week rewrites, so a republish can be undone."""
    own = common.person_dir(root, person)
    files = [own / "weeks" / f"{week}.json", own / "history.json"]
    return {f: f.read_bytes() for f in files if f.exists()}


def migrate_runs(runs, owner):
    """run_state.json from before people ({week: {published, ...}}) as {week: {owner: {...}}}."""
    out = {}
    for week, entry in runs.items():
        old = isinstance(entry, dict) and ("published" in entry or "kept" in entry)
        out[week] = ({owner: entry} if owner else {}) if old else entry
    return out


def finished(entry):
    return (entry or {}).get("published") == "fresh" or bool((entry or {}).get("kept"))


def restore(saved):
    for f, content in saved.items():
        f.write_bytes(content)


def run(week, now, root=common.ROOT, steps=None, allow_stale=False, dry_run=False):
    steps = steps or Steps()
    say = lambda msg: log(msg, now)  # noqa: E731
    people = common.load_people(root)
    keys = common.planned(people)
    if not keys:
        say(f"no people with a plan in {root / 'people.yaml'}")
        return FAILED
    name = {k: people[k].get("name") or k for k in keys}
    state_path = root / "data" / "run_state.json"
    raw = common.load(state_path, {})
    runs = migrate_runs(raw, common.owner(people))
    week_runs = runs.get(week) or {}
    todo = [k for k in keys if not finished(week_runs.get(k))]
    if not todo:
        say(f"{week}: fresh options already out for everyone; nothing to do")
        return DONE

    stale_ok = allow_stale or stale_allowed(week, now)
    if dry_run:
        for k in keys:
            done = week_runs.get(k) or {}
            say(f"{week} {name[k]}: published so far: {done.get('published') or 'nothing'}"
                + (" (kept)" if done.get("kept") else "") + (", notify " + people[k]["notify"] if people[k].get("notify") else ", no phone"))
        pending = common.migrate_people(root, people, dry_run=True)
        if pending:
            say("would first move TJ's files: " + ", ".join(str(src.relative_to(root)) for src, _, ok in pending if ok))
        say(f"stale allowed: {stale_ok}")
        say("would run: ha_sync.py pull; fetch_prices.py --week " + week)
        say("then: fresh, partial or Aldi-only -> plan_week.py --person KEY; else "
            + ("plan_week.py --person KEY --allow-stale" if stale_ok else "wait"))
        say("then: ha_sync.py push-people; for each of " + ", ".join(name[k] for k in todo)
            + ": ha_sync.py push --person KEY (--notify the first time, for people with a phone) if better than what is out")
        return DONE

    common.migrate_people(root, people)
    if raw != runs:
        common.save(state_path, runs)
    if not steps.pull():
        say("pull: failed; planning on the last pulled picks and ratings")
    else:
        say("pull: ok")
    stores_ok = steps.fetch(week)
    say("fetch: " + ", ".join(f"{s} {'ok' if ok else 'FAILED'}" for s, ok in stores_ok.items()))
    mode = mode_of(stores_ok, stale_ok)

    results = {}
    pushed_people = False
    for k in todo:
        done = week_runs.get(k) or {}
        published = done.get("published")
        who = f"{week} {name[k]}"
        if RANK[mode] <= RANK[published]:
            say(f"{who}: nothing better than {published or 'nothing'} to publish yet; retry later")
            results[k] = WAITING
            continue
        if not pushed_people:
            pushed_people = True
            say("push-people: " + ("ok" if steps.push_people() else "FAILED; pushing anyway"))

        before, saved = offered(root, week, k), backup(root, week, k)
        if not steps.plan(week, mode == "stale", k):
            say(f"{who}: plan ({mode}): FAILED")
            results[k] = FAILED
            continue
        after = offered(root, week, k)
        say(f"{who}: plan ({mode}): {', '.join(after)}")
        if published and sorted(after) != sorted(before):
            restore(saved)
            say(f"{who}: {mode} prices change the options; keeping the {published} ones already out")
            week_runs[k] = {**done, "kept": now.isoformat(timespec="seconds"), "kept_mode": mode}
            runs[week] = week_runs
            common.save(state_path, runs)
            results[k] = DONE
            continue

        notify = not published and bool(people[k].get("notify"))
        if not steps.push(week, notify, k):
            say(f"{who}: push: FAILED")
            if published:
                restore(saved)  # the published week stays the one on disk
            results[k] = FAILED
            continue
        say(f"{who}: push: published {mode}" + (", notified" if notify else ""))
        week_runs[k] = {**done, "published": mode, "stale": mode == "stale", "stores_ok": stores_ok,
                        "at": now.isoformat(timespec="seconds"),
                        "first": done.get("first") or now.isoformat(timespec="seconds")}
        runs[week] = week_runs
        common.save(state_path, runs)
        results[k] = DONE if mode == "fresh" else WAITING

    codes = set(results.values())
    return FAILED if FAILED in codes else WAITING if WAITING in codes else DONE


def main(argv=None, now=None, steps=None, root=common.ROOT):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--week", help="any date in the week (YYYY-MM-DD); defaults to this week")
    ap.add_argument("--allow-stale", action="store_true", help="publish on old prices now rather than Friday 18:00")
    ap.add_argument("--dry-run", action="store_true", help="say what would run; run nothing")
    args = ap.parse_args(argv)
    now = now or datetime.datetime.now()
    week = common.week_of(datetime.date.fromisoformat(args.week) if args.week else now.date())
    try:
        return run(week, now, root, steps, args.allow_stale, args.dry_run)
    except (OSError, json.JSONDecodeError) as e:
        log(f"FAILED: {type(e).__name__}: {e}", now)
        return FAILED


if __name__ == "__main__":
    sys.exit(main())
