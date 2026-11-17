# Weekly meal picker plan

Replaces the inventory-first "kitchen brain" arc. Agreed with TJ on 2026-11-15. Status: **scoped, nothing built**. ⚠️ marks something not yet verified.

## What it's for

Spend less on food, and stop having to plan what to eat. TJ's food only: **12 lunch and dinner meals a week**, cooked on Sunday as two recipes of 6 serves each. Breakfast (overnight oats) is out of scope.

Every Wednesday the house offers **four options**. TJ picks two in Home Assistant, gets a shopping list for the cheapest store, shops on the weekend, cooks on Sunday, and rates the meals afterwards. Everything is driven from the HA dashboard, and the weekly run uses **no model**: plain scripts only.

## The weekly loop

| When | What happens |
|---|---|
| Wed 7:30am | Job on nebula reads prices, scores the recipe pool, builds four options and the shopping list for every pair, and publishes them to HA. Phone notification |
| By Fri | TJ taps two options on the Meals dashboard view |
| Sat/Sun | Shopping list on the phone, for one store (or two if the saving is worth it) |
| Sun | Cook from the recipe tables on the dashboard |
| Following week | One tap per meal: again / fine / never |

## The four options

- At most **two share a main protein**; at least **three different cuisines or styles**; **one slot is a recipe never cooked before**.
- Nothing cooked in the **last four weeks**.
- A recipe's score drops for each of the last three weeks its protein was picked, so chicken breast can appear every week but can't fill every slot.
- Each option shows checkout cost at Aldi, Woolworths and Coles, cost per serve and per 50 g of protein, protein and calories per serve, and which of its ingredients are on special.
- **Scoring** compares cost per 50 g of protein (effective cost ÷ serves × 50 ÷ protein per serve, at the cheapest store), not cost per serve, so meat cooked in from whole packs counts as value; plain cost per serve when the protein isn't known.

## Recipes

- **Pool:** every RecipeTin Eats main, read by script from the structured data on each page (amount, unit and name per ingredient, serves, category, cuisine, rating, nutrition). About 1,750 posts, fewer recipes.
- **Crawl:** once, a few seconds between pages, with an honest user agent; then monthly for new recipes. Stored locally and **kept out of any public repository** (the site forbids republishing). The dashboard links back to the original.
- **Claude does not fetch or process the site's pages**: its robots.txt blocks AI crawlers by name. The crawl and everything after it is TJ's own script.
- **Filter:** mains that scale to 6 serves and reheat or freeze well. Rules on category and keywords, not hand-picking.
- **Scaling:** the main protein is sized to **50 g of protein per serve**, whatever the cut, from a small table of protein per 100 g. The resulting weight, calories and cost are shown so lean and fatty cuts can be compared. The rest of the recipe scales by serve count, with the sauce up by a quarter.
- **Swaps** (`swaps.yaml`, TJ's to edit; the original is shown in brackets, with a one-line note where the method changes):

  | Recipe says | Becomes |
  |---|---|
  | Cream, sour cream | Greek yoghurt (note: don't boil) |
  | Oil | Spray oil |
  | Butter | Spray oil for frying, dropped elsewhere |
  | Sugar | Removed (note if the recipe used more than a tablespoon) |
  | Rice | Stays white |
  | Chilli and spice | Never changed |

- **Recipe table:** built by script, Cooking for Engineers style: one row per ingredient with the scaled amount, set against the step that first mentions it. Word matching will sometimes pick the wrong step; recipes that get repeated can be fixed by hand in an overrides file.

## Prices and stores

| Store | Source | Notes |
|---|---|---|
| Aldi | The site's product search for the **Chisholm store (G508)**, food categories only (about 80 requests, a few seconds apart), read weekly | Checked 2026-11-15: 65 meat, Super Savers and pantry prices were identical to the site's default NSW store, but Chisholm lists about 240 more products. The Super Savers list gives the week's meat specials |
| Woolworths | The hotprices-au community file (about 24,700 products with dated price history), one download a week | Current to the day on 2026-11-15. No "special" flag, so a special is **a price at least 15% below the product's own median over eight weeks**. ⚠️ Loose-weight items aren't marked properly: check a dozen meat and produce lines before trusting it. One person's hobby project with no data licence; if it stops, fall back to Aldi-only and say so |
| Coles | The hotprices-au Coles file, same source and rules as Woolworths | Added 2026-11-16. Woolworths and Coles can fail together or alone |

- Each week's prices are saved as a snapshot, which builds the price history that receipts were going to provide.
- **Ingredient → product:** a saved lookup table (`products.yaml`) from each distinct ingredient name to an Aldi, a Woolworths and a Coles product. Drafted once in a Claude Code session from the ingredient names alone, then corrected by TJ. Spices, oil spray and similar are marked "pantry" and cost nothing. New ingredients go to an "unmatched" list on the dashboard; nothing is guessed.
- **Cost** means checkout cost: whole packs, minus anything on the freezer list. The cost of the amount actually used is shown beside it.
- **Store choice:** no preferred store; always whatever costs less, at one store if possible. The list is written for the cheapest single store for the two picks. It splits across **two** stores only when that saves **$10 or more** over the best single store; the split is the cheapest pair of stores (each line from whichever of the two is cheaper), never all three. The dashboard always shows all four totals (Aldi, Woolworths, Coles, and the split naming its two stores).
- **Missing prices:** a store whose prices can't be read this week is costed on its last good snapshot and flagged; a store with no prices at all is left out.
- **Bulk meat alert:** a freezable cut at any store at 20% or more below its best price per kilo is shown as an optional extra line.

## Feedback

- **again / fine / never** per meal. "Never" removes the recipe; "again" brings it back sooner after the four weeks.
- A rating also nudges recipes of the same cuisine or style.
- **Tunable from the dashboard:** every recipe rating and every cuisine or style weight can be seen, changed and reset there; the strength of the nudge to similar recipes and the fade time are settings on the same view.
- Ratings fade over about a year (adjustable, or off), so old opinions stop steering unless renewed.

## Freezer list

Meat only: cut, weight, date, seasoning. Added with one tap when a list included a bulk buy, edited on the dashboard. Recipes that use what's in the freezer score higher, and that meat is left off the shopping list.

## How Home Assistant drives it

HA is the only interface; the scripts on nebula hold the logic and the data.

- **Meals view** on the dashboard, built the same way as the House view: a custom card under `/config/www/`, installed by a build script. Tabs: this week's options, shopping list, tonight's recipes, ratings and tuning, freezer, unmatched ingredients.
- **Nebula → HA:** the job publishes one JSON file per week for the card to read, plus a sensor for status (last run, price freshness) and the notification.
- **HA → nebula:** taps call pyscript services (pick, rate, set weight, add to freezer), following the pattern in `tv/pyscript/`. They write a small state file that the job reads on its next run.
- Because the job precomputes the list for all six possible pairs, picking needs no round trip: the card shows the right list immediately.
- ⚠️ How files get between nebula and the HA VM's `/config` hasn't been checked; use whatever `build_dashboard.py` already uses to deploy the card.

## Running and failures

- A scheduled script on nebula, not a container (the box is short on RAM).
- If a price read fails: retry hourly through Wednesday, then twice a day until Friday. Options go out as soon as one run succeeds.
- If Friday evening arrives with nothing: options priced on last week's snapshot, with a warning on the card and in the notification.
- If a hotprices file (Woolworths, Coles, or both) is missing but Aldi was read, the run goes ahead with what's there and says so, keeps retrying, and republishes without a second notification when more stores come in.

## Files

```
shopping/meals/
  crawl_recipes.py     one-off + monthly crawl
  fetch_prices.py      Aldi pages + Woolworths and Coles files -> weekly snapshot
  plan_week.py         score, pick four, price six pairs, publish to HA
  recipe_table.py      scaled, swapped recipe as a table
  swaps.yaml  protein.yaml  products.yaml  overrides.yaml
  data/                recipes, price snapshots, history of picks and ratings (not in a public repo)
shopping/card/         the Meals dashboard card
shopping/pyscript/     pick / rate / tune / freezer services
```

## Build order

Each step is usable before the next starts.

1. **Checks first** (done 2026-11-15): Woolworths file against per-kilo meat and produce lines; Aldi prices for the ACT; how files reach `/config`.
2. **Recipes and prices:** crawl, filter, price snapshots, the ingredient lookup table.
3. **Options on the dashboard:** four options with costs at both stores on the Meals view, read-only. This is the test of whether the matching and the costs are believable.
4. **Picking and the shopping list**, with the store choice.
5. **Recipe tables** with scaling and swaps.
6. **Ratings and the tuning tab.**
7. **Freezer list** and the bulk meat alert.
8. **Macros per serve**, the schedule on nebula, retries and notifications.

## Parked

Woolworths receipts (`parse_receipts.py` and `build_catalog.py` stay as they are), stock estimates, the bin barcode scanner, load cells, fridge and pantry cameras, prefilled online orders, and any unattended login to Everyday Rewards. Spend tracking can come back later from the weekly list totals.
