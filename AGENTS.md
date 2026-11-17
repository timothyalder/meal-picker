# Setting up meal-picker for someone new

You are getting this running for a person (the **owner**) on their own Home Assistant. Design: [PLAN.md](PLAN.md). Every file shape: [meals/SCHEMA.md](meals/SCHEMA.md) — read it before changing any data file or script. Card behaviour and its test harness: [card/README.md](card/README.md).

## Ground rules

- **The owner runs the recipe crawl, not you.** RecipeTin Eats' robots.txt blocks AI crawlers by name and the site forbids republishing. Hand the owner the crawl commands to run in their own terminal, and work only from the files it writes to `meals/data/recipes/`. Read nothing from recipetineats.com yourself.
- **Everything under `meals/data/`, `meals/people.yaml`, `receipts/` and `data/` is personal or copyrighted** and is gitignored. Keep it that way.
- Commands run from the **parent folder** (the one holding `.venv/` and this repo cloned as `shopping/`). The scripts' usage lines assume it.
- Each step below ends on a check. Move on only when it passes.

## Steps

### 1. Python

```sh
git clone https://github.com/timothyalder/meal-picker.git shopping
python3 -m venv .venv && .venv/bin/pip install -r shopping/requirements.txt
mkdir -p backups                       # build_meals_view.py writes dashboard backups here
.venv/bin/python -m pytest shopping/meals/tests
```

Check: every test passes. The tests use invented fixtures only, so they need no crawl, prices or HA.

### 2. Ask the owner

Settle these before writing config; each one changes a file:

| Question | Where it goes |
|---|---|
| Are they in Australia? If not, stop and explain: prices come from Aldi AU, Woolworths and Coles only (README "Limitations") | — |
| Nearest Aldi store id (from aldi.com.au's store selector; the default `G508` is Chisholm ACT) | `ALDI_STORE` in `meals/fetch_prices.py`, or `--aldi-store` |
| HA address, if not `http://homeassistant.local:8123` | `HA_URL` environment variable (read by `ha_sync.py` and `build_meals_view.py`) |
| Who gets a plan: HA user id, name, phone notify service, any shared kitchen screen | `meals/people.yaml`, copied from `meals/people.example.yaml` |
| Recipes per week, serves, protein per serve, swaps, stores | `DEFAULT_SETTINGS` in `meals/common.py` and its copy in `pyscript/meals.py` (2 recipes, 6 serves, 50 g); each person can change theirs later on the card's Tune tab |

HA user ids: Settings → People → Users in HA, or the websocket call `{"type":"config/auth/list"}`.

Check: `meals/people.yaml` exists and `.venv/bin/python -c "import sys; sys.path.insert(0,'shopping/meals'); import common; print(common.load_people())"` prints each person.

### 3. Recipes (the owner runs this)

Give the owner these, in order. The first one takes several hours (about 1,750 posts at 5 s apart), so it suits an overnight run:

```sh
.venv/bin/python shopping/meals/crawl_recipes.py                  # saves every recipe page's structured data
.venv/bin/python shopping/meals/crawl_recipes.py --repair-units   # fixes "1 kg / 2 lb" style amounts
.venv/bin/python shopping/meals/crawl_recipes.py --filter         # writes data/recipes/_pool.json
```

Check: `shopping/meals/data/recipes/_pool.json` exists and holds a few hundred recipes.

### 4. Prices

```sh
.venv/bin/python shopping/meals/fetch_prices.py
```

About 80 slow Aldi requests plus two hotprices downloads. Exit 1 means a store failed; re-running refetches only the failed store.

Check: `shopping/meals/data/prices/<week>.json` has `ok: true` for each store.

### 5. Ingredient → product table

`meals/products.yaml` maps normalised ingredient names to Aldi, Woolworths and Coles product ids (format in `meals/products.example.yaml`). It already covers the common names. List what's missing for this pool:

```sh
.venv/bin/python shopping/meals/shortlist.py --unmapped            # pool ingredients with no entry
.venv/bin/python shopping/meals/shortlist.py "chicken thigh" "feta"  # candidate products and prices to pick ids from
```

Add entries for the names used by more than one recipe; mark seasonings and staples `pantry`. Show the owner what you added: the ids are guesses until they agree.

Check: `plan_week.py --dry-run` (next step) prices most of the pool; the unmapped count has stopped falling for names used twice or more.

### 6. First plan, offline

```sh
.venv/bin/python shopping/meals/plan_week.py --dry-run
```

Check: four options per person, each with a cost at every store, and a cost per serve the owner finds believable. Unbelievable costs almost always trace to a wrong `products.yaml` entry or a unit the crawl misread; fix those first.

### 7. Home Assistant

Needs [pyscript](https://github.com/custom-components/pyscript) (via HACS) and SSH access to HA's `/config` (the Advanced SSH & Web Terminal add-on). Then:

1. A long-lived access token (HA profile → Security) saved to `~/.config/ha/token` on the machine that runs the scripts.
2. Copy the files (card/README.md "Deploy" has the exact commands):
   - `card/meals-card.js` → `/config/www/meals/meals-card.js`
   - `pyscript/meals.py` → `/config/pyscript/meals.py`, then reload pyscript.
3. `.venv/bin/python shopping/build_meals_view.py --dry-run`, show the owner the change, then run it without `--dry-run`. It adds a Meals view to the default (Overview) dashboard and registers the card resource.
4. `.venv/bin/python shopping/meals/ha_sync.py push-people`

Check: the Meals view loads on the owner's phone and says no plan yet. pyscript errors show in HA's system log.

### 8. First real week

```sh
.venv/bin/python shopping/meals/run_week.py --dry-run
.venv/bin/python shopping/meals/run_week.py
```

Check: options appear on the card, a pick shows a shopping list, and people with a `notify` got a notification.

### 9. Schedule

`meals/crontab.example` is the weekly schedule (Wednesday hourly, Thursday and Friday twice, monthly crawl of new recipes). Adjust the paths to where the parent folder lives and install it with `crontab -e` on an always-on machine. The monthly crawl line is the owner's own script running unattended, same as step 3.

Check: after the next Wednesday, `shopping/meals/data/run_week.log` shows a published week for each person.

## Done

The owner has had one full week: options published, two picked, a list shopped from, and meals rated on the card.
