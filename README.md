# meal-picker

A weekly meal prep planner that runs through Home Assistant. Every Wednesday it reads this week's supermarket prices, scores a pool of recipes, and offers four options on a dashboard card. You pick two, get a shopping list for whichever store is cheapest, cook on Sunday, and rate the meals the week after. The weekly run is plain Python scripts — no model, no cloud service.

The full design (rules for the four options, scoring, store choice, feedback, failures) is in [PLAN.md](PLAN.md). Every file shape is in [meals/SCHEMA.md](meals/SCHEMA.md).

## What it does

- **Recipes:** crawls the mains from [RecipeTin Eats](https://www.recipetineats.com) using the structured recipe data on each page, then filters for recipes that scale to 6 serves and reheat or freeze well. About 1,500 recipes crawled, 468 in the pool.
- **Prices:** Aldi from the site's product search for one store, Woolworths and Coles from the [hotprices-au](https://hotprices.org) community files. A special is a price at least 15% below the product's own eight-week median.
- **Scaling:** the main protein is sized to a protein target per serve (50 g by default) and the rest of the recipe scales by serves. Swaps in `meals/swaps.yaml` (cream → Greek yoghurt, oil → spray oil, and so on).
- **Planning:** four options, at most two sharing a protein, at least three cuisines, one recipe never cooked before, and nothing from the last four weeks. Scored on cost per 50 g of protein at the cheapest store.
- **Home Assistant:** a custom card (`card/meals-card.js`) shows the options, shopping list, recipe tables (one row per ingredient set against its step), ratings, tuning and a freezer list. Taps call pyscript services (`pyscript/meals.py`). Each HA user gets their own plan; a shared screen gets a "Who's cooking?" picker.

## Limitations

- **Australia only.** The price sources are Aldi AU, Woolworths and Coles, and `meals/products.yaml` (ingredient name → store product ids) was drafted against those catalogues. Another country means new price fetchers and a new products table.
- **The recipe content is not in this repo.** RecipeTin Eats doesn't allow republishing, so `meals/data/` is gitignored and you run the crawl yourself. The card links back to each original recipe.
- **hotprices-au is one person's hobby project** with no data licence. If it stops, the planner falls back to Aldi only and says so on the card.
- `products.yaml` (about 540 entries) covers the common ingredient names. Rarer ones are reported by `meals/shortlist.py --unmapped` and need adding by hand; recipes with an unmapped ingredient can't be fully priced.

## Layout

Clone this repo as a folder called `shopping`, inside a parent folder that holds the Python venv. Every command in the docs runs from that parent:

```
home-assistant/          parent: .venv/, backups/
  shopping/              this repo
    meals/               crawl, prices, planner, HA sync, weekly runner, tests
    card/                the Meals dashboard card, with a screenshot harness
    pyscript/            pick / rate / tune / freezer / settings services
    build_meals_view.py  adds the Meals view to the Overview dashboard
    parse_receipts.py, build_catalog.py   older Woolworths receipt parsing (parked)
```

```sh
git clone https://github.com/timothyalder/meal-picker.git shopping
python3 -m venv .venv && .venv/bin/pip install -r shopping/requirements.txt
.venv/bin/python -m pytest shopping/meals/tests
```

## Setting it up

[AGENTS.md](AGENTS.md) is the setup guide, written so a coding agent (Claude Code or similar) can get it running for someone else, step by step. It works just as well read by hand.
