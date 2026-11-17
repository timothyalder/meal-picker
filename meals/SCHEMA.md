# Meal picker data contracts

Shared by every script in `shopping/meals/`. The design is in [../PLAN.md](../PLAN.md). Change a shape here first, then the code. All paths are under `shopping/meals/`; everything in `data/` is generated and stays out of any public repository.

Run from the `home-assistant/` folder: `.venv/bin/python shopping/meals/<script>.py`. Tests: `.venv/bin/python -m pytest shopping/meals/tests`.

## Who owns which file

| File | Role |
|---|---|
| `common.py` | Paths, JSON load/save, `norm_name()`, `to_base()` unit conversion |
| `crawl_recipes.py` | Site → `data/recipes/*.json`; `--ingredients` lists distinct ingredient names |
| `fetch_prices.py` | Aldi pages + Woolworths and Coles files → `data/prices/<week>.json`; its `STORES` tuple is the one list of stores every script uses |
| `recipe_table.py` | A recipe scaled to N serves with protein target and swaps; ingredient/step table |
| `plan_week.py` | Scores the pool, picks four, prices all six pairs, writes `data/weeks/<week>.json` |
| `swaps.yaml`, `protein.yaml`, `products.yaml`, `overrides.yaml` | Hand-edited config |

`<week>` is always the ISO date of the **Wednesday** the specials week starts.

## `data/recipes/<slug>.json`

```json
{
  "slug": "butter-chicken", "url": "https://…", "title": "Butter Chicken",
  "serves": 4,
  "categories": ["Chicken", "Curry"], "cuisine": ["Indian"], "keywords": ["…"],
  "rating": 4.96, "rating_count": 1108,
  "nutrition": {"calories": 402, "protein_g": 30, "fat_g": 25, "carbs_g": 9},
  "ingredients": [
    {"amount": 750, "unit": "g", "name": "chicken thigh fillets", "notes": "cut into bite size pieces",
     "group": "Marinade", "raw": "750 g chicken thigh fillets, cut into bite size pieces"}
  ],
  "steps": ["Combine the Marinade ingredients…", "…"],
  "fetched": "2026-11-15"
}
```

`amount` and `unit` are `null` when the recipe gives none ("salt, to taste"). `amount` is a float; ranges take the lower bound. `unit` is as written, lower-cased; `common.to_base()` turns it into grams, millilitres or a count. `nutrition` is per serve as published and may be missing keys.

## `data/prices/<week>.json`

```json
{
  "week": "2026-11-18",
  "stores": {
    "aldi":       {"ok": true, "fetched": "2026-11-18T07:31:02", "products": [ … ]},
    "woolworths": {"ok": true, "fetched": "2026-11-18T07:33:40", "products": [ … ]},
    "coles":      {"ok": true, "fetched": "2026-11-18T07:34:10", "products": [ … ]}
  }
}
```

Product:

```json
{"store": "aldi", "id": "000000000000123456", "name": "Chicken Breast Fillets 1kg",
 "price": 11.99, "size_qty": 1000, "size_unit": "g",
 "per_kg": false, "usual_price": 11.99, "special": false, "category": "meat"}
```

- `size_unit` is `g`, `ml` or `ea`; `size_qty` is in that unit.
- `per_kg: true` means a loose-weight item: `price` is per kilogram, `size_qty` is 1000 and any weight can be bought.
- `usual_price` is the median over the previous eight weekly snapshots (or the source's own history); `null` when there is none. `special` is true when `price <= 0.85 * usual_price`, or when the store itself lists it as a weekly special (Aldi Super Savers).
- A store that could not be read has `"ok": false` and `"products": []`.

## Config

`products.yaml`: normalised ingredient name (`common.norm_name`) → where to buy it.

```yaml
chicken breast: {aldi: "000000000000123456", woolworths: "6096826", coles: "1234567", cut: chicken breast}
greek yoghurt:  {aldi: "…", woolworths: "…"}
garam masala:   {pantry: true}      # costed at zero, left off the list
```

`cut` marks the main protein and is a key of `protein.yaml`. A store key may be missing if that store doesn't sell it; a recipe needing it can't be priced there. A store value may be one id or a list of candidate ids (see "Costing" below).

`protein.yaml`: cut → `{protein_per_100g, kcal_per_100g}` for the raw weight.

`swaps.yaml`: a list of `{match: [names], to: name | null, note: text, when: optional}`; `to: null` removes the ingredient.

`overrides.yaml`: per slug, hand fixes: `{exclude: true}`, `{style: curry}`, `{steps_for: {ingredient name: step number}}`.

## State (written by Home Assistant, read by `plan_week.py`)

`data/history.json`

```json
{"weeks": [{"week": "2026-11-18", "offered": ["a", "b", "c", "d"], "picked": ["a", "c"],
            "ratings": {"a": "again", "c": "fine"}}]}
```

`data/tuning.json`

```json
{"recipes": {"butter-chicken": {"score": 1.0, "date": "2026-11-25"}},
 "styles": {"curry": 0.3, "indian": 0.1},
 "similar_strength": 0.3, "fade_days": 365}
```

Scores: again = 1, fine = 0, never = -1 (a `never` recipe is excluded outright until reset). `fade_days: 0` turns fading off.

`data/freezer.json`: `[{"cut": "chicken breast", "grams": 2000, "date": "2026-11-15", "seasoning": "paprika and garlic"}]`

## `data/weeks/<week>.json` (what the dashboard card reads)

```json
{
  "week": "2026-11-18", "generated": "2026-11-18T07:35:00",
  "prices_week": "2026-11-18", "stale": false, "stores_ok": {"aldi": true, "woolworths": true, "coles": true},
  "options": [{
    "slug": "butter-chicken", "title": "Butter Chicken", "url": "https://…",
    "cut": "chicken thigh", "style": "curry", "cuisine": "Indian", "new": false, "serves": 6,
    "cost": {"aldi": 31.40, "woolworths": 36.10, "coles": 35.20}, "cost_used": {"aldi": 24.90, "woolworths": 28.00, "coles": 27.50},
    "cost_effective": {"aldi": 27.10, "woolworths": 36.10, "coles": 35.20},
    "cost_per_50g_protein": {"aldi": 3.89, "woolworths": 6.02, "coles": 5.87},
    "per_serve": {"protein_g": 58, "kcal": 610, "meat_g": 280},
    "specials": [{"store": "woolworths", "name": "…", "price": 9.0, "usual_price": 12.0}],
    "unmatched": [],
    "table": { … from recipe_table.py … }
  }],
  "pairs": {"butter-chicken+beef-stroganoff": {
    "aldi": {"total": 58.2, "freeze_value": 8.39, "effective": 49.81,
             "items": [{"name": "…", "packs": 2, "price": 11.99, "for": ["butter-chicken"],
                        "freeze_qty": 700, "freeze_value": 8.39}],
             "freeze": [{"cut": "chicken thigh", "grams": 700, "name": "…"}]},
    "woolworths": {"total": 61.0, "freeze_value": 0, "effective": 61.0, "items": [ … ], "freeze": []},
    "coles": {"total": 59.5, "freeze_value": 0, "effective": 59.5, "items": [ … ], "freeze": []},
    "split": {"stores": ["aldi", "coles"], "total": 52.9, "freeze_value": 0, "effective": 52.9,
              "items": {"aldi": [ … ], "coles": [ … ]}, "freeze": []},
    "choice": "aldi"
  }},
  "alerts": [{"kind": "bulk_meat", "store": "woolworths", "name": "…", "per_kg": 8.0, "best_per_kg": 11.0}],
  "unmatched": ["ingredient names with no products.yaml entry"]
}
```

Pair keys are the two slugs sorted and joined with `+`. `choice` is the single store with the lowest `effective` cost (ties to the first in `STORES` order), or `split` when that saves $10 or more of `effective` cost over it. A store's entry is `null` when the pair can't be priced there.

### Three stores (added 2026-11-16)

- Stores are `fetch_prices.STORES = ("aldi", "woolworths", "coles")`; `plan_week.py` and `run_week.py` import that tuple, and every per-store map in the week file (`stores_ok`, `prices_weeks`, `cost`, `cost_used`, `cost_effective`, `cost_per_50g_protein`, `per_serve_by_store`, `missing`, a pair's store entries) has a key per store.
- A store missing from the week's snapshot, or there with `ok: false`, is priced from its latest earlier good snapshot (`stores_ok` false, `prices_weeks` that week, `stale` true). A store with no good snapshot at all is left out: `prices_weeks` null, every cost and store entry null, and it is never in a split.
- `split` is the cheapest **two-store** combination by `effective` cost over every pair of stores with prices (never all three): each need is bought at whichever of the two is cheaper after freezing. `split.stores` names the two in `STORES` order, and `split.items` is keyed by those two. `null` when no pair of stores covers every need.
- `run_week.py`: Woolworths and Coles both come from hotprices-au. With Aldi read and either or both of them missing, it publishes at once (`published: "aldi-only"` when both are missing, `"partial"` when one is), keeps retrying and republishes without a notification whenever more stores come in. Aldi missing still waits until Friday 18:00.

### Cost per 50 g of protein (added 2026-11-16)

- **Options** gain `cost_per_50g_protein: {store: dollars}`: `effective / serves × PROTEIN_TARGET_G / protein_g`, where `protein_g` is that store's `per_serve_by_store[store].protein_g` (which includes meat cooked in from whole packs), else the recipe's `per_serve.protein_g`. `PROTEIN_TARGET_G` is `protein.yaml`'s `target_protein_g` (50). `null` where the store can't price the option or the protein is unknown.
- The score's `cost` term is `-min over stores(cost_per_50g_protein) / DOLLARS_PER_POINT`, using plain effective cost per serve for a store whose protein is unknown. Meat cooked in from a whole pack therefore counts as value, not as a dearer serve.
- The store choice and the split are unchanged: they compare `effective` checkout cost.

### Costing (added 2026-11-16)

- **Candidates.** A `products.yaml` store value may be a list of ids. Ids missing from the snapshot are ignored; none present makes the ingredient `missing` at that store. Lines are merged into one need per entry (keys with the same candidates at every store, which includes `same_as`) before a candidate and pack count are chosen for the merged quantity. Whole packs (ceil) and per-kg pricing are as before.
- **Freezable** entries have a `cut` or `freezes: true`: the candidate with the lowest used (pro-rata) cost is bought, ties to lower checkout, and the spare (packs bought minus quantity needed) is the leftover. Other entries: lowest checkout, ties to lower used cost, no leftover. Per-kg items never have a leftover.
- `count_per_pack: N` on an entry: N of the recipe's counted items come in one store item, used only when the recipe quantity and the product are both `ea` (2 spring onions against a bunch of 8 is 0.25 of a bunch).
- **List items** gain `freeze_qty` (leftover in the product's `unit`, 0 if none) and `freeze_value` (its pro-rata cost).
- **Store entries** in a pair (`aldi`, `woolworths`, `coles`, `split`) gain `freeze_value` (sum over items), `effective` (`total - freeze_value`) and `freeze`: `[{"cut", "grams", "name"}]`, the freezable leftovers in grams (product name in `name`; `cut` is the entry's cut, or the ingredient key for `freezes: true` entries; leftovers that can't be converted to grams are left out). The card offers each as an "Add to freezer" (`meals_freezer {add: {cut, grams, seasoning: ""}}`).
- **Options** gain `cost_effective` per store. `cost` stays the checkout cost. The score's cost term, the single-store choice, the split's per-need store choice and the $10 split threshold all use effective cost.
- `apply_freezer` matches a freezer entry against a line's cut, or its ingredient key when it has none, so a `freezes: true` leftover added to the freezer comes off the next list.

### Leftovers: freeze in 500 g steps, cook the rest (added 2026-11-16)

`FREEZE_STEP_G = 500` in `plan_week.py`. A freezable item's spare from whole packs, in grams (`ea` items via `count_per_pack` × `each_g`), is split: **frozen** = the largest multiple of 500 g that fits, **cooked** = the rest, eaten this week. 378 g → 0 frozen, 378 cooked; 667 → 500 + 167; 1,250 → 1,000 + 250. Per-kg items have no spare. A spare that can't be put in grams is all frozen, as before, and left off `freeze`.

- The cooked part counts as used: `used` includes it; `freeze_qty`, `freeze_value` and the store entry's `freeze` cover only the frozen part (so `freeze` grams are always multiples of 500); `effective` = `total − freeze_value`. Because the score uses `effective`, a recipe whose leftover is cooked rather than frozen now scores on its full checkout cost.
- The cooked extra is shared between the recipes that need that item in proportion to their need, divided by the serves. Each extra of a `protein.yaml` cut (the entry's `cut`, or the recipe's cut for its protein line) raises `meat_g`, and `protein_g` and `kcal` by that cut's `protein_per_100g`/`kcal_per_100g`; an extra with no cut is only listed. The recipe's own `per_serve` and `table` are left unchanged.
- **List items** gain `extra_g` (grams cooked this week, 0 if none), `freeze_g` (grams frozen; null if the spare can't be put in grams) and `qty_text` (the needed `qty` for display: "1.6 kg", "450 g", "2 L", "250 ml", "3"; `qty` stays exact for costing).
- **Options** gain `per_serve_by_store: {aldi, woolworths, coles}`: the recipe priced on its own at that store, `null` where it can't be priced.
- **Pair store entries** (`aldi`, `woolworths`, `coles`, `split`) gain `per_serve: {slug: …}` for both recipes.
- Both use one shape: `{"meat_g", "protein_g", "kcal", "extra": [{"name", "cut", "id", "grams_total", "grams_per_serve", "bought_g", "frozen_g", "shared"}]}`. `name` is the recipe table's ingredient name (the card matches the Cook table row by it), `cut` the protein.yaml cut or null, `id` the list item's product id, `grams_total` this recipe's share of the cooked extra, `bought_g` the grams of the whole packs bought, `frozen_g` the part of those packs frozen, `shared` true when the other recipe in the pair uses the same item.


## Additions made while building

- `data/recipes/_pool.json`: `{generated, pool: {slug: {style, protein}}, rejected: {slug: why}}`. `_skipped.json` is `{url: reason}`; `_sitemap.json` is `{url: first-seen date}`.
- `per_kg` on a product means per kilogram (or per litre if `size_unit` is `ml`).
- `products.yaml` entries may carry `each_g` (grams per item, for loose produce sold "each") and `ml_per_g`.
- `swaps.yaml` rows may also carry `not`, `ratio`, `when`, `notice`, `notice_over`, `no_amount` and `kcal_saved_per_100`; the file's header explains each.
- `protein.yaml` holds `target_protein_g` beside the cuts; cuts take `match`, `not` and `each_g`. Freezer and `products.yaml` `cut` names must be `protein.yaml` keys.
- `recipe_table.prepare()` returns `{slug, title, url, serves, recipe_serves, cut, cuts, per_serve, ingredients, steps, notes, unplaced}`; each ingredient also has `text` (display amount) and `prep`.
- The week file also has `prices_weeks`, `seed`, `relaxed`, and `styles` (sorted, lower-case list of every style and cuisine in the pool, for the tuning tab), `titles` (`{slug: {title, style, cuisine}}` for every recipe picked before, for the Rate tab); options have `missing`, `score`, `score_terms`; list items have `id`, `cost`, `used`, `qty`, `unit`, `per_kg`, `special`, `usual_price` (`packs` is null for per-kg items); alerts have `cut` and `id`.

## Home Assistant side

HA is the only interface. Two files live in `/config/www/meals/` (served at `/local/meals/`, readable by anyone on the LAN without a login):

| File | Written by | Read by |
|---|---|---|
| `week.json` | pyscript service `meals_publish`, called by `ha_sync.py push` on nebula with the HA token | the Meals card |
| `state.json` | pyscript services called by the card. **pyscript is the only writer** | the Meals card; `ha_sync.py pull` on nebula, over HTTP |

`week.json` is `data/weeks/<week>.json` unchanged.

`state.json`:

```json
{
  "updated": "2026-11-18T19:02:11",
  "picks": {"2026-11-18": ["butter-chicken", "beef-stroganoff"]},
  "ratings": {"butter-chicken": {"rating": "again", "date": "2026-11-25", "title": "Butter Chicken",
                                 "style": "curry", "cuisine": "Indian"}},
  "tuning": {"styles": {"curry": 0.3}, "similar_strength": 0.3, "fade_days": 365},
  "freezer": [{"cut": "chicken breast", "grams": 2000, "date": "2026-11-15", "seasoning": "paprika and garlic"}]
}
```

pyscript services (`shopping/pyscript/meals.py`, deployed to `/config/pyscript/meals.py`):

| Service | Data | Effect |
|---|---|---|
| `pyscript.meals_publish` | `doc` (the week file), `notify` (bool) | writes `week.json`; optionally sends the phone notification |
| `pyscript.meals_pick` | `week`, `slugs` (0 to 2) | sets `picks[week]` |
| `pyscript.meals_rate` | `slug`, `rating` (`again`/`fine`/`never`/`clear`), `title`, `style`, `cuisine` | sets or clears `ratings[slug]`, dated today |
| `pyscript.meals_tune` | any of `style` + `weight` (null removes it), `similar_strength`, `fade_days`, `reset` (bool: clears all ratings and style weights) | updates `tuning` |
| `pyscript.meals_freezer` | `add` ({cut, grams, seasoning}) or `remove` (index) or `set` (index + fields) | updates `freezer` |

Every state-changing service rewrites `state.json` atomically, bumps `updated`, and fires the HA event `meals_state_changed` so open cards refresh.

`ha_sync.py pull` turns `state.json` into the planner's inputs: `picks` and `ratings` are merged into `data/history.json` (never touching `offered`); `ratings` + `tuning` become `data/tuning.json` (again = 1, fine = 0, never = -1); `freezer` becomes `data/freezer.json`. Those three files are derived, so the planner never writes anything HA owns except `offered`.

`data/run_state.json` (written by `run_week.py`): per week, what was published (`fresh`, `partial`, `aldi-only` or `stale`) and `stores_ok`. `meals_freezer` `set` is `{index, ...fields}`. `meals_publish` and every state change fire `meals_state_changed` with `file: week` or `file: state`.

## Per person (added 2026-11-16)

Each Home Assistant user gets their own plan. Agreed with TJ: you, ted, adam and nat each get one; the
`ipad` login is a shared kitchen screen with a "who's this?" picker; plans are fully separate (options,
picks, list, ratings, tuning, freezer); settings start from TJ's; notifications go only to people with a
phone; the card shows only the viewer's own plan (not real privacy: `/local/` is readable on the LAN).

**Person key** = the HA user id (stable). Names come from HA.

`people.yaml` (in `shopping/meals/`, hand-edited, kept out of the repo; start from `people.example.yaml`):

```yaml
0123456789abcdef0123456789abcdef: {name: Alex, notify: notify.mobile_app_alexs_phone}
fedcba9876543210fedcba9876543210: {name: Sam}
00000000000000000000000000000ad0: {name: Kitchen, shared: true}  # no plan; the card shows a picker
```

**Settings** (per person, edited on the card's Tune tab, defaults below = TJ's):

```json
{"recipes": 2, "serves": 6, "protein_g": 50,
 "swaps": {"cream": true, "oil": true, "butter": true, "sugar": true},
 "stores": ["aldi", "woolworths", "coles"]}
```

- `recipes` 1–3, `serves` 2–8, `protein_g` 20–80, `swaps` keyed by `swaps.yaml` row `id` (`butter`
  covers both butter rows), `stores` a non-empty subset of `fetch_prices.STORES`.
- Options offered = `max(4, 2 × recipes)`. "Pairs" generalise to **picks**: every combination of
  `recipes` options, keyed by the sorted slugs joined with `+` (with 1 recipe, one key per option). The
  key `pairs` and its entry shape stay as they are.
- `prepare(recipe, serves=…, protein_g=…, swaps=…)` gets the person's serves, protein target and the
  enabled swap rows; the planner only prices and chooses among the person's `stores`.

**Files on nebula** (`shopping/meals/data/`): prices and recipes stay shared. Per person, under
`people/<key>/`: `history.json`, `tuning.json`, `freezer.json`, `settings.json` (same shapes as before,
plus settings), and `weeks/<week>.json`. TJ's existing `data/history.json`, `tuning.json`,
`freezer.json` and `data/weeks/*` move to his folder on the first run (a one-off migration, kept idempotent).

**Files in HA** (`/config/www/meals/`, served at `/local/meals/`):

| File | Written by | Shape |
|---|---|---|
| `people.json` | `meals_publish_people` from nebula | `{key: {name, shared, has_plan}}`, no notify targets |
| `week-<key>.json` | `meals_publish` with `person` | the person's week file, unchanged shape |
| `state.json` | pyscript only | `{"updated": …, "people": {key: {picks, ratings, tuning, freezer, settings, updated}}}` |

The existing single-person `state.json` is migrated on first load into `people[TJ's key]`.

**Services** (`pyscript.*`): every existing service gains a required `person` field (the person key);
the card sends the logged-in user's id, or the person picked on a shared screen. A service validates
that `person` is a key in `people.json` and isn't `shared`. New:

| Service | Data | Effect |
|---|---|---|
| `meals_settings` | `person` + any of `recipes, serves, protein_g, swaps` (partial map), `stores` | updates and validates that person's settings |
| `meals_publish_people` | `people` (the `people.json` map) | writes `people.json` |

`meals_publish` takes `person`, `doc`, `notify`, and sends the notification to that person's `notify`
target only if nebula passes one (`notify_target`), since targets aren't in `people.json`.
`meals_state_changed` events carry `person`.

`ha_sync.py pull` writes each person's derived files and `settings.json`; `push --person KEY` publishes one
person (default: everyone with a plan). `run_week.py` runs fetch once, then plan and push per person, and
tracks published/stale per person in `run_state.json`.

### As built (2026-11-16)

- **Service data**, exactly (all return `{ok, error?}`; changes also return `person`, `updated`):
  `meals_publish {person, doc, notify, notify_target?}` (notifies only when `notify` and `notify_target`
  are both given; returns `notified`), `meals_publish_people {people}`, `meals_pick {person, week, slugs}`,
  `meals_rate {person, slug, rating, title?, style?, cuisine?}`, `meals_tune {person, style?, weight?,
  similar_strength?, fade_days?, reset?}`, `meals_freezer {person, add | remove | set}`,
  `meals_settings {person, recipes?, serves?, protein_g?, swaps?, stores?}`.
- `meals_settings` rejects anything outside the ranges (whole numbers; `2.0` from a number selector is
  fine), a `swaps` key other than `cream`, `oil`, `butter`, `sugar` or a non-boolean value, and an empty,
  repeated or unknown `stores` list; one bad field changes nothing. `stores` is stored in `STORES` order.
- `meals_pick` allows 0 to 3 slugs (the most `recipes` can be), not the person's own `recipes`, so a
  settings change mid-week can't lock out the week already published. The card limits it to `recipes`.
- With no `people.json` yet, every per-person service fails ("run ha_sync.py push-people").
  `meals_publish_people` rejects any field other than `name`, `shared`, `has_plan` (so no notify target).
- State: each person's entry is `{updated, picks, ratings, tuning, freezer, settings}`, with `settings`
  always complete (defaults filled in). The migration of a flat `state.json` keys TJ by the constant
  `OWNER` in `meals.py`; it happens in memory on first load and is written on the first change.
- Events: `meals_state_changed` with `{file: "state", person, updated}`, `{file: "week", person, week}` or
  `{file: "people"}`.
- **Week file** gains `person` (key), `settings` (as used) and `stores` (the person's stores). A store not
  in `stores` has `prices_weeks` null and every cost and store entry null and is never in a split, but its
  `stores_ok` still says whether its prices were read; `stale` and `prices_week` cover only `stores`. The
  notification mentions only the person's stores.
- `cost_per_50g_protein` keeps its name but is per the person's `protein_g` target (`effective / serves
  × protein_g target / protein per serve`), so the score compares like with like for each person. With
  the default 50 it is unchanged. The week file's `settings.protein_g` says what it is per.
- Options: when fewer than `max(4, 2 × recipes)` can be found, `relaxed` ends with "fewer than four
  options" (or "six"); the relaxation "cut: any" lifts the cut limit entirely.
- `swaps` settings ids group `swaps.yaml` rows by the row `id` up to the first `-` (`butter` covers
  `butter-frying`); a row without an `id` can't be turned off. A row whose group isn't in the person's
  settings is on.
- Nebula: `people.yaml` keys with `shared: true` get no folder, plan or push. `settings.json` is written by
  `pull`; a missing or bad value falls back to the default. The migration (`common.migrate_people`) moves
  `data/{history,tuning,freezer}.json` and `data/weeks/*.json` into TJ's folder, never overwriting a file
  already there; it runs at the start of `plan_week.py`, `ha_sync.py pull`/`push` and `run_week.py`. A
  `--dry-run` moves nothing and plans TJ from the old files. Without a `people.yaml`, `plan_week.py`
  plans once from `data/` as before.
- `run_state.json`: `{week: {person key: {published, stale, stores_ok, at, first, kept?, kept_mode?}}}`;
  an old `{week: {published, …}}` entry becomes TJ's. `run_week.py` calls `ha_sync.py push-people` once
  per run that publishes anything, then `plan_week.py --person KEY` and `ha_sync.py push --person KEY
  [--notify]` per person not yet done. Its exit code is 2 if any person failed, else 1 if any is still
  waiting, else 0.
