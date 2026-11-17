"""Tests for crawl_recipes.py against hand-written fixtures. No network."""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402
import crawl_recipes as cr  # noqa: E402

FIX = pathlib.Path(__file__).parent / "fixtures" / "recipes"
CURRY = "https://example.test/test-chicken-curry/"
STEW = "https://example.test/test-lentil-stew/"


def page(name):
    return (FIX / name).read_text()


# ---------- amounts ----------

@pytest.mark.parametrize("text, amount, rest", [
    ("1 1/2 cups stock", 1.5, "cups stock"),
    ("½ tsp salt", 0.5, "tsp salt"),
    ("1½ tsp salt", 1.5, "tsp salt"),
    ("1 ½ tsp salt", 1.5, "tsp salt"),
    ("1/4 cup oil", 0.25, "cup oil"),
    ("2 - 3 cloves garlic", 2.0, "cloves garlic"),
    ("2-3", 2.0, ""),
    ("2 to 3 carrots", 2.0, "carrots"),
    ("1.5 kg beef", 1.5, "kg beef"),
    ("750g mince", 750.0, "g mince"),
    ("salt, to taste", None, "salt, to taste"),
    ("", None, ""),
])
def test_parse_amount(text, amount, rest):
    assert cr.parse_amount(text) == (amount, rest)


def test_ld_ingredient():
    assert cr.ld_ingredient("2 - 3 cloves garlic, crushed (or 2 tsp jarred)") == {
        "amount": 2.0, "unit": "cloves", "name": "garlic", "notes": "crushed (or 2 tsp jarred)",
        "group": None, "raw": "2 - 3 cloves garlic, crushed (or 2 tsp jarred)"}
    assert cr.ld_ingredient("pepper, to taste")["amount"] is None
    assert cr.ld_ingredient("2 onions")["unit"] is None


@pytest.mark.parametrize("value, serves", [
    (["3", "3 - 4"], 3), ("6 serves", 6), ("Serves 4-6", 4), (8, 8), (None, None), ("a crowd", None),
])
def test_serves(value, serves):
    assert cr.first_int(value) == serves


# ---------- pages ----------

def test_wprm_page():
    r = cr.parse_page(page("wprm_page.html"), CURRY)
    assert r["slug"] == "test-chicken-curry" and r["url"] == CURRY
    assert r["title"] == "Test Chicken Curry & Rice"
    assert r["serves"] == 4
    assert r["categories"] == ["Chicken", "Curry"] and r["cuisine"] == ["Testish"]
    assert r["keywords"] == ["test curry", "weeknight dinner"]
    assert (r["rating"], r["rating_count"]) == (4.8, 123)
    assert r["nutrition"] == {"calories": 455, "protein_g": 36, "fat_g": 28.5, "carbs_g": 9}
    ings = r["ingredients"]
    assert [(i["amount"], i["unit"], i["name"], i["group"]) for i in ings] == [
        (600, "g", "chicken thigh fillets", "Marinade"),
        (1.5, "tbsp", "mild curry powder", "Marinade"),
        (0.5, "tsp", "salt", "Marinade"),
        (2, "cloves", "garlic", "Sauce"),
        (400, "ml", "coconut milk", "Sauce"),
        (1.5, "cups", "water", "Sauce"),
        (None, None, "coriander", "Sauce"),
    ]
    assert ings[0]["notes"] == "in chunks" and ings[3]["notes"] == "crushed" and ings[4]["notes"] is None
    assert ings[0]["raw"] == "600 g chicken thigh fillets , in chunks"
    assert r["steps"][2] == "Add coconut milk & water; simmer 20 minutes. Garnish ."
    assert set(r) == {"slug", "url", "title", "serves", "categories", "cuisine", "keywords", "rating",
                      "rating_count", "nutrition", "ingredients", "steps", "fetched"}
    assert "img.jpg" not in str(r) and "chatty" not in str(r)


def test_jsonld_only_page_with_sections():
    r = cr.parse_page(page("jsonld_page.html"), STEW)
    assert r["title"] == "Test Lentil Stew" and r["serves"] == 6
    assert r["categories"] == ["Vegetarian", "Stew"] and r["keywords"] == ["lentil stew", "freezer friendly"]
    assert r["rating"] is None and r["nutrition"] == {"calories": 310, "protein_g": 18}
    assert r["steps"] == ["Soften the carrots in a pot.", "Add lentils, stock and tomatoes.",
                          "Simmer until thick, season with chilli flakes, salt and pepper."]
    assert [(i["amount"], i["unit"], i["name"], i["notes"]) for i in r["ingredients"]] == [
        (1.5, "cups", "dried red lentils", None),
        (1.25, "litres", "vegetable stock", "salt reduced"),
        (2, None, "carrots", "diced"),
        (0.25, "tsp", "chilli flakes", None),
        (750, "g", "canned tomatoes", None),
        (1, "pinch", "salt", None),
        (None, None, "pepper", "to taste"),
    ]
    assert all(i["group"] is None for i in r["ingredients"])


def test_page_without_recipe_is_none():
    assert cr.parse_page("<html><head><script type='application/ld+json'>{\"@type\":\"Article\"}</script>"
                         "</head><body>news</body></html>", "https://example.test/news/") is None


def test_wprm_card_without_jsonld_is_loud():
    html = '<div class="wprm-recipe-container" data-recipe-id="1"><div class="wprm-recipe"></div></div>'
    with pytest.raises(cr.ParseError, match="no schema.org Recipe JSON-LD"):
        cr.parse_page(html, "https://example.test/x/")


def test_recipe_without_ingredients_is_loud():
    html = '<script type="application/ld+json">{"@type":"Recipe","name":"X"}</script>'
    with pytest.raises(cr.ParseError, match="recipeIngredient"):
        cr.parse_page(html, "https://example.test/x/")


# ---------- sitemaps ----------

def stub(responses, log=None):
    def fetch(url):
        if log is not None:
            log.append(url)
        r = responses[url]
        return r if isinstance(r, tuple) else (200, r)
    return fetch


SITEMAPS = {
    cr.SITEMAP_INDEX: page("sitemap_index.xml"),
    "https://example.test/post-sitemap.xml": page("post-sitemap.xml"),
    "https://example.test/post-sitemap2.xml": page("post-sitemap2.xml"),
}


def test_parse_sitemap_ignores_image_locs():
    assert cr.parse_sitemap(page("post-sitemap.xml")) == [
        "https://example.test/", CURRY, STEW]


def test_read_sitemaps():
    log = []
    urls = cr.read_sitemaps(stub(SITEMAPS, log))
    assert urls == [CURRY, STEW, "https://example.test/about-our-test-kitchen/"]
    assert not any("page-sitemap" in u or "category" in u for u in log)


def test_sitemap_index_without_posts_is_loud():
    with pytest.raises(cr.Stop, match="no post-sitemap"):
        cr.read_sitemaps(stub({cr.SITEMAP_INDEX: page("post-sitemap.xml")}))
    with pytest.raises(cr.Stop, match="HTTP 403"):
        cr.read_sitemaps(stub({cr.SITEMAP_INDEX: (403, "Forbidden")}))


def test_record_sitemap_finds_new(tmp_path):
    assert cr.record_sitemap([CURRY], tmp_path, "2026-11-15") == []
    assert cr.record_sitemap([CURRY, STEW], tmp_path, "2026-12-15") == [STEW]
    assert cr.record_sitemap([CURRY, STEW], tmp_path, "2027-01-15") == [STEW]  # still new until fetched
    assert common.load(tmp_path / "_sitemap.json") == {CURRY: "2026-11-15", STEW: "2026-12-15"}


# ---------- crawl, resume, skip ----------

ABOUT = "https://example.test/about-our-test-kitchen/"
PAGES = {CURRY: page("wprm_page.html"), STEW: page("jsonld_page.html"), ABOUT: "<html><body>About us</body></html>"}


def test_crawl_saves_skips_and_resumes(tmp_path):
    log = []
    urls = [CURRY, ABOUT, STEW]
    assert cr.crawl(urls, stub(PAGES, log), tmp_path, limit=2) == (1, 1)
    assert log == [CURRY, ABOUT]
    assert common.load(tmp_path / "_skipped.json") == {ABOUT: "no recipe"}
    assert common.load(tmp_path / "test-chicken-curry.json")["title"] == "Test Chicken Curry & Rice"

    log.clear()
    assert cr.crawl(urls, stub(PAGES, log), tmp_path) == (1, 0)
    assert log == [STEW]
    log.clear()
    assert cr.crawl(urls, stub(PAGES, log), tmp_path) == (0, 0)
    assert log == []


def test_crawl_stops_on_403_and_429(tmp_path):
    for status in (403, 429):
        with pytest.raises(cr.Stop, match=f"HTTP {status}"):
            cr.crawl([CURRY, STEW], stub({CURRY: (status, "no"), STEW: PAGES[STEW]}), tmp_path)
    assert not (tmp_path / "test-lentil-stew.json").exists()


def test_crawl_stops_after_three_errors(tmp_path):
    urls = [f"https://example.test/p{i}/" for i in range(5)]
    log = []
    with pytest.raises(cr.Stop, match="3 failed requests in a row"):
        cr.crawl(urls, stub({u: (500, "oops") for u in urls}, log), tmp_path)
    assert len(log) == 3
    assert common.load(tmp_path / "_skipped.json", {}) == {}  # errors are retried next run


def test_crawl_error_count_resets_and_404_is_skipped(tmp_path):
    a, b, c, d = (f"https://example.test/p{i}/" for i in range(4))
    responses = {a: (500, ""), b: (0, "timeout"), CURRY: PAGES[CURRY], c: (502, ""), d: (404, "")}
    assert cr.crawl([a, b, CURRY, c, d], stub(responses), tmp_path) == (1, 0)
    assert common.load(tmp_path / "_skipped.json") == {d: "http 404"}


def test_crawl_stops_on_unreadable_card(tmp_path):
    bad = '<div class="wprm-recipe-container" data-recipe-id="1"></div>'
    with pytest.raises(cr.ParseError):
        cr.crawl([CURRY], stub({CURRY: bad}), tmp_path)


# ---------- pool ----------

def recipe(slug, title, ingredients, categories=(), keywords=()):
    return {"slug": slug, "title": title, "categories": list(categories), "keywords": list(keywords),
            "ingredients": [{"name": n, "amount": 1.0} for n in ingredients]}


POOL_CASES = [
    (recipe("curry", "Chicken Korma", ["chicken thigh fillets", "onion"], ["Chicken", "Curry"]), None, "curry"),
    (recipe("ragu", "Slow Cooked Beef Ragu", ["beef chuck", "tomatoes"], ["Beef", "Pasta"]), None, "stew"),
    (recipe("dal", "Red Lentil Dal", ["red lentils", "vegetable stock"], ["Vegetarian"]), None, "curry"),
    (recipe("rice", "Chicken Fried Rice", ["chicken breast", "rice"], ["Rice"]), None, "rice"),
    (recipe("stirfry", "Beef and Broccoli", ["beef rump"], ["Stir Fry"]), None, "stir-fry"),
    (recipe("bake", "Lamb Moussaka", ["lamb mince", "eggplant"], ["Lamb"]), None, "bake"),
    (recipe("cake", "Chocolate Cake", ["flour", "eggs"], ["Dessert", "Cake"]), "not a main (dessert)", None),
    (recipe("side", "Garlic Green Beans", ["green beans", "garlic"], ["Sides"]), "not a main (sides)", None),
    (recipe("veg", "Roast Vegetable Bake", ["pumpkin", "chicken stock", "minced garlic"], ["Vegetarian"]),
     "no main protein", None),
    (recipe("schnitzel", "Chicken Schnitzel", ["chicken breast", "panko"], ["Chicken"]),
     "doesn't reheat: crispy coating", None),
    (recipe("tacos", "Beef Tacos", ["beef mince"], ["Mexican"]), "doesn't reheat: assembled to serve", None),
    (recipe("steak", "Garlic Butter Steak", ["scotch fillet steak"], ["Beef"]),
     "doesn't reheat: steak cooked to order", None),
    (recipe("salad", "Thai Chicken Salad", ["chicken breast"], ["Chicken", "Salad"]), "not a main (salad)", None),
    (recipe("kw", "Pork Bites", ["pork belly"], ["Pork"], ["party snacks"]), "not a main (snacks)", None),
]


@pytest.mark.parametrize("r, why, style", POOL_CASES, ids=[c[0]["slug"] for c in POOL_CASES])
def test_filter_rules(r, why, style):
    assert cr.why_not(r) == why
    if not why:
        assert cr.style_of(r) == style


def test_build_pool_with_overrides():
    recipes = [c[0] for c in POOL_CASES]
    out = cr.build_pool(recipes, {"curry": {"style": "korma"}, "ragu": {"exclude": True}})
    assert out["pool"]["curry"] == {"style": "korma", "protein": "chicken"}
    assert out["pool"]["dal"]["protein"] == "legumes"
    assert out["rejected"]["ragu"] == "excluded in overrides.yaml"
    assert set(out["pool"]) == {"curry", "dal", "rice", "stirfry", "bake"}


def test_ingredient_counts_are_names_only():
    recipes = [
        {"slug": "a", "ingredients": [{"name": "Garlic (crushed)", "notes": "secret note"}, {"name": "garlic"},
                                      {"name": "Chicken thigh"}]},
        {"slug": "b", "ingredients": [{"name": "garlic"}, {"name": "beef chuck"}]},
        {"slug": "c", "ingredients": [{"name": "garlic"}]},
    ]
    assert cr.ingredient_counts(recipes, {"a": {}, "b": {}}) == [("garlic", 2), ("beef chuck", 1), ("chicken thigh", 1)]


# ---------- politeness and CLI ----------

def test_fetcher_waits_and_identifies_itself(monkeypatch):
    sleeps, sent = [], []

    class Resp:
        status_code, text = 200, "ok"

    f = cr.Fetcher(delay=5)
    monkeypatch.setattr(f.session, "get", lambda url, timeout: sent.append(dict(f.session.headers)) or Resp())
    monkeypatch.setattr(cr.time, "sleep", sleeps.append)
    assert f("https://example.test/a/") == (200, "ok")
    assert f("https://example.test/b/") == (200, "ok")
    assert sleeps[0] == 0 and sleeps[1] > 4.9
    assert sent[0]["User-Agent"].startswith("meal-picker/")


def test_cli_filter_and_ingredients(tmp_path, monkeypatch, capsys):
    for r in (cr.parse_page(page("wprm_page.html"), CURRY), cr.parse_page(page("jsonld_page.html"), STEW)):
        common.save(tmp_path / f"{r['slug']}.json", r)
    (tmp_path / "overrides.yaml").write_text("test-lentil-stew: {style: soup}\n")
    monkeypatch.setattr(common, "RECIPES", tmp_path)
    monkeypatch.setattr(sys, "argv", ["crawl_recipes.py", "--filter", "--ingredients"])
    monkeypatch.setattr(cr, "load_overrides", lambda: cr.yaml.safe_load((tmp_path / "overrides.yaml").read_text()))
    cr.main()
    pool = common.load(tmp_path / "_pool.json")
    assert pool["pool"] == {"test-chicken-curry": {"style": "curry", "protein": "chicken"},
                            "test-lentil-stew": {"style": "soup", "protein": "legumes"}}
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "salt\t2"
    assert all(len(line.split("\t")) == 2 for line in out.splitlines())
    assert "Test" not in out and "http" not in out and "simmer" not in out.lower()


@pytest.mark.parametrize("amount, unit, expected", [
    ("1 kg / 2 lb", "", (1.0, "kg")),
    ("14 oz / 400 g", "", (400.0, "g")),
    ("3 - 3.5 lb / 1.5 - 1.8 kg", "", (1.5, "kg")),
    ("75g / 3 oz", "", (75.0, "g")),
    ("1 1/2", "cups", (1.5, "cups")),
    ("2", "", (2.0, None)),
    ("2 tbsp", "", (2.0, "tbsp")),
    ("", "", (None, None)),
])
def test_amount_and_unit_prefers_metric(amount, unit, expected):
    assert cr.amount_and_unit(amount, unit) == expected


def test_repair_units_fixes_saved_dual_amounts():
    recipe = {"ingredients": [
        {"amount": 2.0, "unit": None, "name": "test chicken", "raw": "2 lb / 1 kg test chicken, diced"},
        {"amount": 1.0, "unit": None, "name": "test tomatoes", "raw": "1 test tomatoes (400 g can)"},
        {"amount": 2.0, "unit": "tbsp", "name": "test oil", "raw": "2 tbsp test oil"},
    ]}
    assert cr.repair_units(recipe) == 1
    assert (recipe["ingredients"][0]["amount"], recipe["ingredients"][0]["unit"]) == (1.0, "kg")
    assert recipe["ingredients"][1]["unit"] is None


def test_roundup_posts_are_not_recipes():
    r = {"title": "Rice recipes", "categories": ["Main"], "keywords": [],
         "ingredients": [{"amount": None, "name": "test chicken and rice"}] * 5 + [{"amount": 500, "name": "chicken"}]}
    assert cr.why_not(r).startswith("not a recipe")


def test_weight_at_front_of_name_moves_to_amount():
    ing = {"amount": 1.0, "unit": None, "name": "lb/500g test pork"}
    assert cr.weight_in_name(ing) and (ing["amount"], ing["unit"], ing["name"]) == (500.0, "g", "test pork")
    plain = {"amount": 2.0, "unit": "tbsp", "name": "test oil"}
    assert not cr.weight_in_name(plain) and plain["name"] == "test oil"


@pytest.mark.parametrize("amount, unit, expected", [
    (2.0, "cups (tightly packed)", (2.0, "cups")),
    (2.0, "x 400g/14oz cans", (800.0, "g")),
    (1.0, "can (15 oz / 400 g)", (400.0, "g")),
    (1.5, "cups (150g)", (150.0, "g")),
    (2.0, "small", (2.0, None)),
    (1.0, "tsp+", (1.0, "tsp")),
    (1.0, "tbsp (in total)", (1.0, "tbsp")),
    (1.0, "litre/quart", (1.0, "litre")),
    (3.0, "– 8", (3.0, None)),
    (2.0, "pinch of", (2.0, "pinch")),
])
def test_clean_unit(amount, unit, expected):
    assert cr.clean_unit(amount, unit) == expected


def test_metric_amount_beats_a_note_in_the_unit():
    assert cr.amount_and_unit("285g/ 10 oz", "(approx)") == (285.0, "g")


def test_how_to_posts_are_not_meals():
    r = {"title": "How to Test Chicken", "categories": ["Chicken"], "keywords": [],
         "ingredients": [{"name": "chicken breast", "amount": 500.0}]}
    assert cr.why_not(r) == "not a meal (a technique post)"
