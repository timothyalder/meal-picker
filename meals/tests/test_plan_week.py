"""Tests for plan_week.py. No network; recipe_table.prepare is replaced by fake_prepare."""
import itertools
import json
import math
import pathlib
import random
import shutil
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402
import plan_week as pw  # noqa: E402

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "plan"
WEEK = "2026-11-18"


def fake_prepare(recipe, serves=6, **_):
    """Stand-in for recipe_table.prepare: scales by serves, no swaps; cut comes from the fixture."""
    f = serves / recipe["serves"]
    ings = []
    for i in recipe["ingredients"]:
        qty, unit = common.to_base(None if i["amount"] is None else i["amount"] * f, i["unit"])
        ings.append({"name": i["name"], "key": common.norm_name(i["name"]), "amount": i["amount"],
                     "unit": i["unit"], "base_qty": qty, "base_unit": unit,
                     "is_protein": bool(i.get("protein")), "removed": bool(i.get("removed"))})
    return {"serves": serves, "cut": recipe.get("cut"), "per_serve": {"protein_g": 50, "meat_g": 200, "kcal": 600},
            "ingredients": ings, "steps": recipe["steps"], "notes": []}


@pytest.fixture
def root(tmp_path):
    shutil.copytree(FIXTURES, tmp_path / "meals")
    return tmp_path / "meals"


def run(root, **kw):
    return pw.plan(WEEK, prepare=fake_prepare, root=root, **kw)


def write(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj))


def line(key, qty, unit="g", cut=None, for_="r"):
    return {"key": key, "name": key, "qty": qty, "unit": unit, "cut": cut, "for": for_}


def product(id, price, qty=1000, unit="g", per_kg=False, name=None):
    return {"id": id, "name": name or id, "price": price, "size_qty": qty, "size_unit": unit,
            "per_kg": per_kg, "usual_price": price, "special": False}


# --- costing --------------------------------------------------------------

def test_pack_rounding_and_per_kg():
    products = {"breast": {"aldi": "B"}, "thigh": {"aldi": "T"}}
    cat = {"B": product("B", 11.0), "T": product("T", 12.0, per_kg=True)}
    p = pw.price([line("breast", 1200), line("thigh", 750)], "aldi", products, cat)
    items = {i["id"]: i for i in p["items"]}
    assert items["B"]["packs"] == 2 and items["B"]["cost"] == 22.0 and items["B"]["used"] == 13.2
    assert items["T"]["packs"] is None and items["T"]["cost"] == 9.0
    assert p["total"] == 31.0 and p["used"] == 22.2
    exact = pw.price([line("breast", 1000)], "aldi", products, cat)
    assert exact["items"][0]["packs"] == 1


def test_pantry_and_unmatched():
    products = {"salt": {"pantry": True}, "honey": {"aldi": "H"}, "honey2": {"aldi": "H", "ml_per_g": 0.5},
                "onion": {"aldi": "O"}, "widget": {"aldi": "W"}}
    cat = {"H": product("H", 4.0, 500), "O": product("O", 3.0, per_kg=True), "W": product("W", 2.0)}
    assert pw.price([line("salt", 5)], "aldi", products, cat) == \
        {"total": 0, "used": 0, "freeze_value": 0, "effective": 0, "items": [], "freeze": [], "extra": {},
         "unmatched": [], "missing": []}
    # ml against a product sold by weight with no hint: reported, not guessed
    p = pw.price([line("honey", 100, "ml")], "aldi", products, cat)
    assert p["total"] is None and p["unmatched"] == ["honey"]
    # with ml_per_g: 100 ml = 200 g of a 500 g jar
    p = pw.price([line("honey2", 100, "ml")], "aldi", products, cat)
    assert p["total"] == 4.0 and p["used"] == 1.6
    # a count against a per-kg product via the built-in table (onion 150 g)
    assert pw.price([line("onion", 2, "ea")], "aldi", products, cat)["total"] == 0.9
    # a count with no each_g anywhere
    assert pw.price([line("widget", 2, "ea")], "aldi", products, cat)["unmatched"] == ["widget"]
    # no products.yaml entry at all
    p = pw.price([line("dragonfruit", 2, "ea")], "aldi", products, cat)
    assert p["total"] is None and p["unmatched"] == ["dragonfruit"]


def test_missing_product_nulls_only_that_store(root):
    out = run(root, seed=1)
    ctx = {"catalogs": pw.load_prices(WEEK, root / "data" / "prices", False)[0],
           "products": pw.load_products(root / "products.yaml"), "freezer": []}
    recipe = common.load(root / "data" / "recipes" / "pork-larb-bowls.json")
    c = pw.cost_option("pork-larb-bowls", fake_prepare(recipe), ctx)
    assert c["cost"]["aldi"] is None and c["cost"]["woolworths"] == 17.7
    assert c["missing"] == {"aldi": ["fish sauce"]} and c["unmatched"] == []
    assert "dragonfruit" in out["unmatched"]
    assert "dragonfruit-surprise" not in [o["slug"] for o in out["options"]]


def test_option_costs_from_fixtures(root):
    ctx = {"catalogs": pw.load_prices(WEEK, root / "data" / "prices", False)[0],
           "products": pw.load_products(root / "products.yaml"), "freezer": []}
    recipe = common.load(root / "data" / "recipes" / "honey-chicken-stirfry.json")
    c = pw.cost_option("honey-chicken-stirfry", fake_prepare(recipe), ctx)
    # breast 1.2 kg = 2 x $11, rice $2, two onions 300 g at $3/kg, salt pantry. The 800 g spare breast is
    # 500 g frozen and 300 g cooked this week, so 1.5 kg of breast counts as used.
    assert c["cost"]["aldi"] == 24.9 and c["cost_used"]["aldi"] == 18.4


def test_freezer_subtraction():
    products = {"breast": {"aldi": "B", "cut": "chicken breast"}}
    cat = {"B": product("B", 11.0)}
    lines = [line("breast", 1500, cut="chicken breast")]
    some = pw.apply_freezer(lines, [{"cut": "chicken breast", "grams": 1000}], products)
    assert pw.price(some, "aldi", products, cat)["total"] == 11.0
    all_ = pw.apply_freezer(lines, [{"cut": "chicken breast", "grams": 2000}], products)
    assert pw.price(all_, "aldi", products, cat) ["items"] == []
    other = pw.apply_freezer(lines, [{"cut": "beef mince", "grams": 2000}], products)
    assert pw.price(other, "aldi", products, cat)["total"] == 22.0


def test_freezable_picks_cheapest_per_kg_and_reports_leftover():
    products = {"breast": {"aldi": ["T1", "T2"], "cut": "chicken breast"}}
    cat = {"T1": product("T1", 11.0), "T2": product("T2", 20.0, 2000)}
    p = pw.price([line("breast", 1000, cut="chicken breast")], "aldi", products, cat)
    [i] = p["items"]
    assert i["id"] == "T2" and i["packs"] == 1 and i["freeze_qty"] == 1000 and i["freeze_value"] == 10.0
    assert p["total"] == 20.0 and p["freeze_value"] == 10.0 and p["effective"] == 10.0
    assert p["freeze"] == [{"cut": "chicken breast", "grams": 1000, "name": "T2"}]


def test_not_freezable_picks_lowest_checkout():
    products = {"yoghurt": {"aldi": ["SMALL", "BULK"]}}
    cat = {"SMALL": product("SMALL", 4.0, 500), "BULK": product("BULK", 6.0, 1000)}
    p = pw.price([line("yoghurt", 400)], "aldi", products, cat)
    assert [i["id"] for i in p["items"]] == ["SMALL"] and p["total"] == 4.0
    assert p["items"][0]["freeze_qty"] == 0 and p["freeze_value"] == 0 and p["effective"] == 4.0 and p["freeze"] == []


def test_freezes_flag_and_per_kg_has_no_leftover():
    products = {"bread": {"aldi": "BR", "freezes": True}, "mince": {"aldi": "MI", "cut": "beef mince"}}
    cat = {"BR": product("BR", 4.0, 700), "MI": product("MI", 12.0, per_kg=True)}
    p = pw.price([line("bread", 350), line("mince", 750, cut="beef mince")], "aldi", products, cat)
    items = {i["id"]: i for i in p["items"]}
    # 350 g spare bread is under FREEZE_STEP_G: none frozen, all of it cooked this week
    assert items["BR"]["freeze_qty"] == 0 and items["BR"]["freeze_value"] == 0 and items["BR"]["extra_g"] == 350
    assert items["MI"]["freeze_qty"] == 0 and items["MI"]["freeze_value"] == 0 and items["MI"]["extra_g"] == 0
    assert p["freeze"] == [] and p["effective"] == 13.0


def test_single_id_and_missing_candidates():
    products = {"rice": {"aldi": "R1"}, "pasta": {"aldi": ["GONE", "P1"]}, "oats": {"aldi": ["GONE", "GONE2"]}}
    cat = {"R1": product("R1", 2.0), "P1": product("P1", 1.5, 500)}
    p = pw.price([line("rice", 500), line("pasta", 500)], "aldi", products, cat)
    assert sorted(i["id"] for i in p["items"]) == ["P1", "R1"] and p["total"] == 3.5
    p = pw.price([line("oats", 500)], "aldi", products, cat)
    assert p["total"] is None and p["missing"] == ["oats"]


def test_needs_merged_across_recipes_and_same_as_before_choosing():
    products = {"breast": {"aldi": ["K1", "K2"], "cut": "chicken breast"},
                "chicken fillet": {"aldi": ["K1", "K2"], "cut": "chicken breast"}}
    # 1 kg at $11, or 2 kg at $23 ($11.50/kg): separately each 500 g would fit in one 1 kg pack
    cat = {"K1": product("K1", 11.0), "K2": product("K2", 23.0, 2000)}
    p = pw.price([line("breast", 500, for_="a"), line("chicken fillet", 500, for_="b")], "aldi", products, cat)
    assert len(p["items"]) == 1 and p["items"][0]["id"] == "K1" and p["items"][0]["packs"] == 1
    assert p["items"][0]["for"] == ["a", "b"] and p["total"] == 11.0 and p["freeze"] == []


def test_count_per_pack():
    products = {"spring onion": {"aldi": "SO", "count_per_pack": 8, "each_g": 15}}
    cat = {"SO": product("SO", 2.0, 1, "ea")}
    p = pw.price([line("spring onion", 2, "ea")], "aldi", products, cat)
    assert p["items"][0]["packs"] == 1 and p["total"] == 2.0 and p["used"] == 0.5
    # not a count on both sides: each_g applies as before (per-kg product)
    cat = {"SO": product("SO", 10.0, per_kg=True)}
    assert pw.price([line("spring onion", 2, "ea")], "aldi", products, cat)["total"] == 0.3


def test_bulk_alert_reads_candidate_lists():
    products = {"breast": {"aldi": ["X", "Y"], "cut": "chicken breast"}}
    ids = set()
    for e in products.values():
        ids |= set(pw.candidates(e, "aldi"))
    assert ids == {"X", "Y"} and pw.candidates({"aldi": 123}, "aldi") == ["123"]
    assert pw.candidates({}, "woolworths") == []


# --- scoring --------------------------------------------------------------

def cand_for_score(**kw):
    c = {"slug": "x", "cost": {"aldi": 12.0, "woolworths": None}, "serves": 6, "style": "curry",
         "cuisines": ["Indian"], "cut": "chicken breast", "rating": None}
    return {**c, **kw}


def score_ctx(tuning=None, cut_weeks=None, frozen=None):
    return {"week": WEEK, "tuning": tuning or {}, "cut_weeks": cut_weeks or {}, "frozen": frozen or {}}


def test_score_cost_term_uses_cheaper_store():
    t = pw.score_terms(cand_for_score(cost={"aldi": 18.0, "woolworths": 12.0}), score_ctx())
    assert t["cost"] == pytest.approx(-12 / 6 / pw.DOLLARS_PER_POINT)
    assert t["rating"] == t["style"] == t["cut"] == t["freezer"] == t["site"] == 0


def test_score_cost_term_uses_effective_cost():
    c = cand_for_score(cost={"aldi": 18.0, "woolworths": 12.0}, cost_effective={"aldi": 9.0, "woolworths": 12.0})
    assert pw.score_terms(c, score_ctx())["cost"] == pytest.approx(-9 / 6 / pw.DOLLARS_PER_POINT)


@pytest.mark.parametrize("date,fade_days,want", [
    (WEEK, 100, 1.0), ("2026-09-29", 100, 0.5), ("2026-04-01", 100, 0.0), ("2020-01-01", 0, 1.0)])
def test_score_rating_fades(date, fade_days, want):
    tuning = {"recipes": {"x": {"score": 1.0, "date": date}}, "fade_days": fade_days}
    assert pw.score_terms(cand_for_score(), score_ctx(tuning))["rating"] == pytest.approx(want * pw.RATING_WEIGHT)


def test_score_style_and_cuisine_weights():
    tuning = {"styles": {"curry": 0.5, "Indian": 0.2, "pasta": 9}, "similar_strength": 0.5}
    assert pw.score_terms(cand_for_score(), score_ctx(tuning))["style"] == pytest.approx(0.35 * pw.STYLE_WEIGHT)


def test_score_cut_penalty_and_window():
    picked = {"a": {"2026-11-11"}, "b": {"2026-11-04"}, "c": {"2026-10-21"}, "d": {"2026-10-28"}}
    weeks = pw.cut_weeks(picked, lambda s: "chicken breast" if s != "d" else "beef", WEEK)
    assert weeks == {"chicken breast": 2, "beef": 1}
    t = pw.score_terms(cand_for_score(), score_ctx(cut_weeks=weeks))
    assert t["cut"] == pytest.approx(-2 * pw.CUT_PENALTY)


def test_score_freezer_and_site_rating():
    t = pw.score_terms(cand_for_score(rating=4.9), score_ctx(frozen={"chicken breast": 500}))
    assert t["freezer"] == pw.FREEZER_BONUS
    assert t["site"] == pytest.approx(0.9 * pw.SITE_RATING_WEIGHT)
    assert pw.score_terms(cand_for_score(rating=3.5), score_ctx())["site"] == 0


def test_never_is_excluded(root):
    first = run(root, seed=1)["options"][0]["slug"]
    write(root / "data" / "tuning.json", {"recipes": {first: {"score": -1, "date": WEEK}}})
    assert first not in [o["slug"] for o in run(root, seed=1)["options"]]


# --- choosing four --------------------------------------------------------

def cand(slug, score, cut=None, style=None, new=False, recent=False):
    return {"slug": slug, "score": score, "cut": cut or slug, "style": style or slug, "new": new, "recent": recent}


def slugs(cs):
    return [c["slug"] for c in cs]


def test_at_most_two_share_a_cut():
    cs = [cand(f"c{i}", 10 - i, cut="chicken") for i in range(4)] + [cand("b", 1), cand("p", 0)]
    got, relaxed = pw.choose(cs, random.Random(0))
    assert slugs(got) == ["c0", "c1", "b", "p"] and relaxed == []


def test_three_styles():
    cs = [cand(f"k{i}", 10 - i, style="curry") for i in range(4)] + [cand("s", 1, style="soup"), cand("t", 0)]
    got, relaxed = pw.choose(cs, random.Random(0))
    assert slugs(got) == ["k0", "k1", "s", "t"] and relaxed == []


def test_new_slot_takes_best_new():
    cs = [cand(f"o{i}", 10 - i) for i in range(5)] + [cand("n1", 2, new=True), cand("n2", 1, new=True)]
    got, _ = pw.choose(cs, random.Random(0))
    assert slugs(got) == ["o0", "o1", "o2", "n1"]


def test_recent_excluded():
    cs = [cand("r", 99, recent=True)] + [cand(f"o{i}", 10 - i) for i in range(4)]
    assert "r" not in slugs(pw.choose(cs, random.Random(0))[0])


def test_relaxation_order_recorded():
    cs = [cand(f"k{i}", 10 - i, style="curry") for i in range(6)]
    got, relaxed = pw.choose(cs, random.Random(0))
    assert slugs(got) == ["k0", "k1", "k2", "k3"]
    assert relaxed == ["styles: 2 distinct", "styles: any"]
    few = [cand("r", 5, recent=True), cand("a", 4), cand("b", 3), cand("c", 2)]
    got, relaxed = pw.choose(few, random.Random(0))
    assert len(got) == 4 and relaxed[-1] == "recent: picked in the last four weeks allowed"


def test_seed_breaks_ties_deterministically():
    cs = [cand(f"t{i}", 1.0) for i in range(10)]
    runs = {s: slugs(pw.choose(cs, random.Random(s))[0]) for s in ("a", "b", "c", "d")}
    assert runs["a"] == slugs(pw.choose(cs, random.Random("a"))[0])
    assert len({tuple(v) for v in runs.values()}) > 1


def test_same_week_same_four(root):
    a = [o["slug"] for o in run(root)["options"]]
    b = [o["slug"] for o in run(root)["options"]]
    assert a == b and len(a) == 4


# --- pairs ----------------------------------------------------------------

def pair_ctx(aldi, woolworths, products):
    return {"catalogs": {"aldi": aldi, "woolworths": woolworths}, "products": products, "freezer": []}


def test_pair_shares_a_pack():
    products = {"yoghurt": {"aldi": "Y", "woolworths": "WY"}}
    ctx = pair_ctx({"Y": product("Y", 5.0)}, {"WY": product("WY", 6.0)}, products)
    a = {"slug": "a", "lines": [line("yoghurt", 400, for_="a")]}
    b = {"slug": "b", "lines": [line("yoghurt", 400, for_="b")]}
    single = pw.price(a["lines"], "aldi", products, ctx["catalogs"]["aldi"])["total"]
    p = pw.price_pair(a, b, ctx)
    assert p["aldi"]["total"] == 5.0 < 2 * single
    assert p["aldi"]["items"][0]["packs"] == 1 and p["aldi"]["items"][0]["for"] == ["a", "b"]
    assert p["choice"] == "aldi"


@pytest.mark.parametrize("w_y,choice", [(20.0, "split"), (20.01, "aldi")])
def test_split_threshold(w_y, choice):
    products = {"x": {"aldi": "AX", "woolworths": "WX"}, "y": {"aldi": "AY", "woolworths": "WY"}}
    ctx = pair_ctx({"AX": product("AX", 20.0), "AY": product("AY", 30.0)},
                   {"WX": product("WX", 30.0), "WY": product("WY", w_y)}, products)
    p = pw.price_pair({"lines": [line("x", 1000, for_="a")]}, {"lines": [line("y", 1000, for_="b")]}, ctx)
    assert p["aldi"]["total"] == 50.0
    assert p["split"]["total"] == round(20 + w_y, 2)
    assert [i["id"] for i in p["split"]["items"]["aldi"]] == ["AX"]
    assert p["choice"] == choice


def test_effective_cost_picks_the_store():
    # Aldi $20 checkout with a 1 kg leftover worth $10; Woolworths $15 with none
    products = {"breast": {"aldi": "A2", "woolworths": "W1", "cut": "chicken breast"}}
    ctx = pair_ctx({"A2": product("A2", 20.0, 2000)}, {"W1": product("W1", 15.0)}, products)
    p = pw.price_pair({"lines": [line("breast", 600, cut="chicken breast", for_="a")]},
                      {"lines": [line("breast", 400, cut="chicken breast", for_="b")]}, ctx)
    assert p["aldi"]["total"] == 20.0 and p["aldi"]["effective"] == 10.0 and p["woolworths"]["effective"] == 15.0
    assert p["choice"] == "aldi"
    assert p["aldi"]["freeze"] == [{"cut": "chicken breast", "grams": 1000, "name": "A2"}]
    assert p["woolworths"]["freeze"] == [] and p["split"]["freeze_value"] == 10.0


@pytest.mark.parametrize("cut,choice", [(None, "split"), ("beef mince", "aldi")])
def test_split_threshold_uses_effective(cut, choice):
    # y at Aldi is a 2 kg pack for a 1 kg need; at Woolworths 1 kg for $14. Split saves $16 at the checkout,
    # but when y freezes Aldi's spare kilo is worth $15 and the effective saving is only $1.
    products = {"x": {"aldi": "AX", "woolworths": "WX"}, "y": {"aldi": "AY", "woolworths": "WY", "cut": cut}}
    ctx = pair_ctx({"AX": product("AX", 20.0), "AY": product("AY", 30.0, 2000)},
                   {"WX": product("WX", 30.0), "WY": product("WY", 14.0)}, products)
    p = pw.price_pair({"lines": [line("x", 1000, for_="a")]}, {"lines": [line("y", 1000, cut=cut, for_="b")]}, ctx)
    assert p["aldi"]["total"] == 50.0 and p["split"]["total"] == 34.0
    assert p["aldi"]["effective"] == (35.0 if cut else 50.0) and p["split"]["effective"] == 34.0
    assert p["choice"] == choice


def test_split_needs_everything_covered():
    products = {"x": {"aldi": "AX"}, "z": {}}
    ctx = pair_ctx({"AX": product("AX", 1.0)}, {}, products)
    p = pw.price_pair({"lines": [line("x", 10)]}, {"lines": [line("z", 10)]}, ctx)
    assert p["split"] is None and p["aldi"] is None and p["choice"] is None


# --- alerts and fallbacks -------------------------------------------------

def set_price(root, week, store, pid, price):
    path = root / "data" / "prices" / f"{week}.json"
    snap = common.load(path)
    for p in snap["stores"][store]["products"]:
        if p["id"] == pid:
            p["price"] = price
    write(path, snap)


@pytest.mark.parametrize("price,alert", [(8.8, True), (8.81, False)])
def test_bulk_meat_alert(root, price, alert):
    set_price(root, WEEK, "woolworths", "W-CHB", price)  # best over earlier weeks is Aldi's $11/kg
    alerts = run(root, seed=1)["alerts"]
    assert bool(alerts) == alert
    if alert:
        assert alerts == [{"kind": "bulk_meat", "store": "woolworths", "cut": "chicken breast", "id": "W-CHB",
                           "name": "Woolworths Chicken Breast 1kg", "per_kg": 8.8, "best_per_kg": 11.0}]


def test_missing_snapshot_errors_without_allow_stale(root):
    (root / "data" / "prices" / f"{WEEK}.json").unlink()
    with pytest.raises(SystemExit):
        run(root)
    out = run(root, allow_stale=True)
    assert out["stale"] and out["prices_week"] == "2026-11-11"
    assert out["stores_ok"] == {"aldi": False, "woolworths": False, "coles": False} and len(out["options"]) == 4


def test_store_down_uses_earlier_snapshot(root):
    snap = common.load(root / "data" / "prices" / f"{WEEK}.json")
    snap["stores"]["woolworths"] = {"ok": False, "products": []}
    write(root / "data" / "prices" / f"{WEEK}.json", snap)
    out = run(root)
    # the fixtures have no Coles data at all: left out, flagged not ok
    assert out["stale"] and out["prices_weeks"] == {"aldi": WEEK, "woolworths": "2026-11-11", "coles": None}
    assert out["stores_ok"] == {"aldi": True, "woolworths": False, "coles": False}


def test_aldi_only_when_woolworths_has_no_data(root):
    for path in (root / "data" / "prices").glob("*.json"):
        snap = common.load(path)
        snap["stores"]["woolworths"] = {"ok": False, "products": []}
        write(path, snap)
    out = run(root)
    assert not out["stale"] and out["stores_ok"]["woolworths"] is False
    assert all(o["cost"]["woolworths"] is None and o["cost"]["aldi"] for o in out["options"])
    assert "pork-larb-bowls" not in [o["slug"] for o in out["options"]]  # fish sauce is Woolworths-only
    for p in out["pairs"].values():
        assert p["woolworths"] is None and p["split"] is None and p["choice"] == "aldi"


# --- history and output ---------------------------------------------------

def test_history_idempotent_and_keeps_ha_fields(root, capsys):
    hist = root / "data" / "history.json"
    write(hist, {"weeks": [{"week": "2026-11-11", "offered": ["a"], "picked": ["a"], "ratings": {"a": "fine"}},
                           {"week": WEEK, "offered": [], "picked": ["beef-ragu"], "ratings": {}}]})
    out = pw.main(["--week", "2026-11-20"], prepare=fake_prepare, root=root)
    assert out["week"] == WEEK and "Week 2026-11-18" in capsys.readouterr().out
    first = hist.read_text()
    pw.main(["--week", WEEK], prepare=fake_prepare, root=root)
    assert hist.read_text() == first
    weeks = common.load(hist)["weeks"]
    assert [w["week"] for w in weeks] == ["2026-11-11", WEEK]
    assert weeks[0] == {"week": "2026-11-11", "offered": ["a"], "picked": ["a"], "ratings": {"a": "fine"}}
    assert weeks[1]["picked"] == ["beef-ragu"] and weeks[1]["offered"] == [o["slug"] for o in out["options"]]
    assert (root / "data" / "weeks" / f"{WEEK}.json").exists()


def test_dry_run_writes_nothing(root):
    pw.main(["--week", WEEK, "--dry-run"], prepare=fake_prepare, root=root)
    assert not (root / "data" / "history.json").exists() and not (root / "data" / "weeks").exists()


def per_serve_ok(v):
    assert set(v) == {"meat_g", "protein_g", "kcal", "extra"}
    for e in v["extra"]:
        assert set(e) >= {"name", "grams_total", "grams_per_serve"} and e["grams_total"] > 0
    return True


def check_week(out):
    """The shape of data/weeks/<week>.json in SCHEMA.md."""
    num = (int, float)
    assert set(out) >= {"week", "generated", "prices_week", "stale", "stores_ok", "options", "pairs", "alerts", "unmatched"}
    assert isinstance(out["stale"], bool) and set(out["stores_ok"]) == set(pw.STORES)
    for o in out["options"]:
        assert set(o) >= {"slug", "title", "url", "cut", "style", "cuisine", "new", "serves", "cost", "cost_used",
                          "per_serve", "specials", "unmatched", "table"}
        assert isinstance(o["new"], bool) and isinstance(o["serves"], int)
        for k in ("cost", "cost_used", "cost_effective"):
            assert set(o[k]) == set(pw.STORES) and all(v is None or isinstance(v, num) for v in o[k].values())
        assert set(o["per_serve"]) >= {"protein_g", "kcal", "meat_g"}
        assert set(o["per_serve_by_store"]) == set(pw.STORES)
        for v in o["per_serve_by_store"].values():
            assert v is None or per_serve_ok(v)
        assert all(set(s) >= {"store", "name", "price", "usual_price"} for s in o["specials"])
        assert all(isinstance(u, str) for u in o["unmatched"])

    def entry_ok(e):
        assert all(isinstance(e[k], num) for k in ("total", "freeze_value", "effective"))
        assert e["effective"] == round(e["total"] - e["freeze_value"], 2)
        for f in e["freeze"]:
            assert set(f) == {"cut", "grams", "name"} and isinstance(f["grams"], int) and f["grams"] > 0
            assert f["grams"] % pw.FREEZE_STEP_G == 0
        assert all(per_serve_ok(v) for v in e["per_serve"].values())

    def items_ok(items):
        for i in items:
            assert set(i) >= {"name", "packs", "price", "for", "freeze_qty", "freeze_value", "extra_g", "qty_text"}
            assert isinstance(i["qty_text"], str) and isinstance(i["extra_g"], num)
            assert i["packs"] is None or isinstance(i["packs"], int)
            assert isinstance(i["for"], list)

    slugs_ = [o["slug"] for o in out["options"]]
    n = (out.get("settings") or {}).get("recipes", 2)
    assert len(out["pairs"]) == math.comb(len(slugs_), n)
    for key, p in out["pairs"].items():
        assert len(key.split("+")) == n
        assert key == "+".join(sorted(key.split("+"))) and set(key.split("+")) <= set(slugs_)
        for s in pw.STORES:
            assert p[s] is None or (entry_ok(p[s]) is None and items_ok(p[s]["items"]) is None)
        if p["split"] is not None:
            stores = p["split"]["stores"]
            assert len(stores) == 2 and set(stores) <= set(pw.STORES) and list(stores) == sorted(stores, key=pw.STORES.index)
            assert entry_ok(p["split"]) is None and set(p["split"]["items"]) == set(stores)
            for items in p["split"]["items"].values():
                items_ok(items)
        assert p["choice"] in (*pw.STORES, "split", None)
        singles = {s: p[s]["effective"] for s in pw.STORES if p[s]}
        if p["choice"] in pw.STORES:
            assert singles[p["choice"]] == min(singles.values())
    for a in out["alerts"]:
        assert set(a) >= {"kind", "store", "name", "per_kg", "best_per_kg"}
    assert all(isinstance(u, str) for u in out["unmatched"])
    json.dumps(out)


def test_output_matches_schema(root):
    write(root / "data" / "freezer.json", [{"cut": "beef mince", "grams": 500, "date": WEEK, "seasoning": ""}])
    out = run(root)
    check_week(out)
    freeze = [f for p in out["pairs"].values() for s in pw.STORES if p[s] for f in p[s]["freeze"]]
    # 800 g spare: 500 g frozen, 300 g cooked
    assert {"cut": "chicken breast", "grams": 500, "name": "Chicken Breast Fillets 1kg"} in freeze
    specials = [s for o in out["options"] for s in o["specials"]]
    assert all(s["store"] == "woolworths" for s in specials)


def test_pool_shapes():
    want = [("a", "curry"), ("b", None)]
    assert pw.pool_entries({"generated": "x", "pool": {"a": {"style": "curry"}, "b": {}}, "rejected": {}}) == want
    assert pw.pool_entries([{"slug": "a", "style": "curry"}, "b"]) == want
    assert pw.pool_entries({"a": "curry", "b": None}) == want


def test_styles_lists_every_pool_style_and_cuisine(root):
    write(root / "overrides.yaml", {"beef-chilli": {"style": "Chilli"}, "lamb-tagine": {"exclude": True}})
    out = run(root)
    assert out["styles"] == ["bake", "bowl", "chilli", "chinese", "curry", "fusion", "greek", "indian", "italian",
                             "mexican", "pasta", "salad", "soup", "stir-fry", "thai"]
    assert "moroccan" not in out["styles"]  # excluded recipes don't count


def test_smoke_with_real_recipe_table(root):
    recipe_table = pytest.importorskip("recipe_table")
    check_week(pw.plan(WEEK, prepare=recipe_table.prepare, root=root))


def test_products_same_as_resolves_and_overrides(tmp_path):
    path = tmp_path / "products.yaml"
    path.write_text("brown onion: {aldi: '1', each_g: 150}\nonion: {same_as: brown onion}\n"
                    "large onion: {same_as: onion, each_g: 250}\nloop: {same_as: loop}\n")
    with pytest.raises(ValueError):
        pw.load_products(path)
    path.write_text("brown onion: {aldi: '1', each_g: 150}\nonion: {same_as: brown onion}\n"
                    "large onion: {same_as: onion, each_g: 250}\n")
    t = pw.load_products(path)
    assert t["onion"] == {"aldi": "1", "each_g": 150}
    assert t["large onion"] == {"aldi": "1", "each_g": 250}


def test_lines_resolve_names_and_assume_small_unmapped():
    products = {"garlic": {"pantry": True}, "chicken breast": {"aldi": "1", "cut": "chicken breast"}}
    prepared = {"cut": "chicken breast", "ingredients": [
        {"name": "large garlic finely chopped", "key": "large garlic finely chopped", "base_qty": 3, "base_unit": "ea"},
        {"name": "chicken breasts", "key": "chicken breasts", "base_qty": 1300, "base_unit": "g", "is_protein": True},
        {"name": "galangal", "key": "galangal", "base_qty": 20, "base_unit": "g"},
        {"name": "sambal", "key": "sambal", "base_qty": None, "base_unit": None},
        {"name": "pumpkin", "key": "pumpkin", "base_qty": 800, "base_unit": "g"},
    ]}
    lines = {l["name"]: l for l in pw.lines_of("x", prepared, products)}
    assert lines["large garlic finely chopped"]["key"] == "garlic" and not lines["large garlic finely chopped"]["assumed"]
    assert lines["chicken breasts"]["key"] == "chicken breast"
    assert lines["galangal"]["assumed"] and lines["sambal"]["assumed"]
    assert not lines["pumpkin"]["assumed"]


# --- leftovers: freeze whole 500 g steps, cook the rest --------------------

def fish_ctx(pack=1000, price=10.0):
    products = {"basa": {"aldi": "BA", "woolworths": "WB", "coles": "CB", "cut": "white fish"}}
    ctx = pair_ctx({"BA": product("BA", price, pack)}, {"WB": product("WB", price, pack)}, products)
    ctx["catalogs"]["coles"] = {"CB": product("CB", price, pack)}
    return ctx


@pytest.mark.parametrize("need,frozen,cooked", [
    (1622, 0, 378), (1333, 500, 167), (750, 1000, 250), (1500, 500, 0), (2000, 0, 0)])
def test_spare_split_into_frozen_steps_and_cooked(need, frozen, cooked):
    ctx = fish_ctx(pack=1000 if need != 750 else 2000)
    p = pw.price([line("basa", need, cut="white fish")], "aldi", ctx["products"], ctx["catalogs"]["aldi"])
    [i] = p["items"]
    assert i["freeze_qty"] == frozen and i["extra_g"] == cooked
    assert p["freeze"] == ([{"cut": "white fish", "grams": frozen, "name": "BA"}] if frozen else [])
    assert all(f["grams"] % pw.FREEZE_STEP_G == 0 for f in p["freeze"])
    if cooked:
        assert p["extra"] == {"r": [{"name": "basa", "cut": "white fish", "id": "BA", "grams_total": cooked,
                                     "bought_g": i["packs"] * (2000 if need == 750 else 1000),
                                     "frozen_g": frozen, "shared": False}]}
    else:
        assert p["extra"] == {}


def test_split_spare_helper():
    assert pw.split_spare(378, 1) == (0, 0, 378)
    assert pw.split_spare(667, 1) == (500, 500, 167)
    assert pw.split_spare(1250, 1) == (1000, 1000, 250)
    assert pw.split_spare(500, 1) == (500, 500, 0)
    assert pw.split_spare(0, 1) == (0, 0, 0)
    assert pw.split_spare(2, None) == (2, None, 0)  # can't be put in grams: frozen as before


def test_costs_count_cooked_extra_as_used():
    # 1622 g from 2 x 1 kg at $10: nothing frozen, all $20 used
    ctx = fish_ctx()
    p = pw.price([line("basa", 1622, cut="white fish")], "aldi", ctx["products"], ctx["catalogs"]["aldi"])
    assert p["total"] == 20.0 and p["used"] == 20.0 and p["freeze_value"] == 0 and p["effective"] == 20.0
    # 1333 g from 2 kg: 500 g frozen ($5), 167 g cooked
    p = pw.price([line("basa", 1333, cut="white fish")], "aldi", ctx["products"], ctx["catalogs"]["aldi"])
    [i] = p["items"]
    assert i["freeze_value"] == 5.0 and i["used"] == 15.0
    assert p["total"] == 20.0 and p["used"] == 15.0 and p["freeze_value"] == 5.0 and p["effective"] == 15.0


def test_per_kg_and_unfreezable_have_no_extra():
    products = {"mince": {"aldi": "MI", "cut": "beef mince"}, "yog": {"aldi": "Y"}}
    cat = {"MI": product("MI", 12.0, per_kg=True), "Y": product("Y", 4.0, 1000)}
    p = pw.price([line("mince", 1622, cut="beef mince"), line("yog", 300)], "aldi", products, cat)
    assert all(i["extra_g"] == 0 and i["freeze_qty"] == 0 for i in p["items"])
    assert p["extra"] == {} and p["freeze"] == []


def test_count_items_convert_with_each_g():
    # sold by the pack of 4 thighs at 150 g each = 600 g a pack; need 7 thighs (1050 g) -> 2 packs, 150 g spare
    products = {"thigh": {"aldi": "TH", "freezes": True, "count_per_pack": 4, "each_g": 150}}
    cat = {"TH": product("TH", 8.0, 1, "ea")}
    p = pw.price([line("thigh", 7, "ea")], "aldi", products, cat)
    [i] = p["items"]
    assert i["packs"] == 2 and i["freeze_qty"] == 0 and i["extra_g"] == 150 and p["freeze"] == []
    # no each_g: leftover can't be put in grams, so it's frozen as before (and not on the freeze list)
    products = {"widget": {"aldi": "TH", "freezes": True, "count_per_pack": 4}}
    p = pw.price([line("widget", 7, "ea")], "aldi", products, cat)
    assert p["items"][0]["freeze_qty"] == 0.2 and p["items"][0]["extra_g"] == 0 and p["freeze"] == []


def test_extra_shared_across_a_pair_by_need():
    # 900 g + 450 g = 1350 g from 2 x 1 kg: 500 g frozen, 150 g cooked, shared 2:1
    ctx = fish_ctx()
    a = {"slug": "a", "per_serve": {"meat_g": 150, "protein_g": 28, "kcal": 400},
         "lines": [line("basa", 900, cut="white fish", for_="a")]}
    b = {"slug": "b", "per_serve": {"meat_g": 75, "protein_g": 14, "kcal": 300},
         "lines": [line("basa", 450, cut="white fish", for_="b")]}
    p = pw.price_pair(a, b, ctx)
    ps = p["aldi"]["per_serve"]
    assert [e["grams_total"] for e in ps["a"]["extra"]] == [100] and [e["grams_total"] for e in ps["b"]["extra"]] == [50]
    assert ps["a"]["extra"][0]["grams_per_serve"] == round(100 / 6, 1) and ps["a"]["extra"][0]["bought_g"] == 2000
    assert ps["a"]["extra"][0]["shared"] and ps["a"]["extra"][0]["frozen_g"] == 500
    assert p["aldi"]["freeze"] == [{"cut": "white fish", "grams": 500, "name": "BA"}]
    assert p["split"]["per_serve"]["a"]["extra"][0]["grams_total"] == 100
    assert a["per_serve"] == {"meat_g": 150, "protein_g": 28, "kcal": 400}  # the recipe's own figures untouched


def test_per_serve_rises_by_protein_yaml():
    protein = {"white fish": {"protein_per_100g": 18.5, "kcal_per_100g": 85}}
    base = {"meat_g": 270, "protein_g": 50, "kcal": 211}
    got = pw.per_serve_with(base, [{"name": "basa", "cut": "white fish", "grams_total": 378, "bought_g": 2000}], protein)
    assert got["meat_g"] == 333 and got["protein_g"] == round(50 + 63 * 0.185) and got["kcal"] == round(211 + 63 * 0.85)
    assert got["extra"] == [{"name": "basa", "cut": "white fish", "grams_total": 378, "bought_g": 2000,
                             "grams_per_serve": 63.0}]
    # no cut: only reported
    got = pw.per_serve_with(base, [{"name": "bread", "cut": None, "grams_total": 350, "bought_g": 700}], protein)
    assert {k: got[k] for k in ("meat_g", "protein_g", "kcal")} == base and got["extra"][0]["grams_per_serve"] == 58.3
    # unknown kcal stays unknown
    assert pw.per_serve_with({**base, "kcal": None}, [{"name": "basa", "cut": "white fish", "grams_total": 60}],
                             protein)["kcal"] is None
    assert pw.per_serve_with(base, None, protein) == {**base, "extra": []}


def test_option_per_serve_by_store():
    ctx = {**fish_ctx(), "freezer": [], "protein": {"white fish": {"protein_per_100g": 18.5, "kcal_per_100g": 85}}}
    prepared = {"cut": "white fish", "serves": 6, "per_serve": {"meat_g": 270, "protein_g": 50, "kcal": 211},
                "ingredients": [{"name": "basa", "key": "basa", "base_qty": 1622, "base_unit": "g", "is_protein": True}]}
    c = pw.cost_option("fish", prepared, ctx)
    for s in pw.STORES:
        ps = c["per_serve_by_store"][s]
        assert ps["meat_g"] == 333 and ps["extra"][0]["grams_total"] == 378 and ps["extra"][0]["grams_per_serve"] == 63.0
    assert prepared["per_serve"]["meat_g"] == 270
    ctx["catalogs"]["woolworths"] = None
    assert pw.cost_option("fish", prepared, ctx)["per_serve_by_store"]["woolworths"] is None


@pytest.mark.parametrize("qty,unit,text", [
    (1621.6, "g", "1.6 kg"), (2000, "g", "2 kg"), (450, "g", "450 g"), (378.4, "g", "380 g"), (12, "g", "12 g"),
    (2000, "ml", "2 L"), (250, "ml", "250 ml"), (3, "ea", "3"), (0.25, "ea", "0.3"), (1250, "g", "1.3 kg"), (None, "g", "")])
def test_qty_text(qty, unit, text):
    assert pw.qty_text(qty, unit) == text


# --- three stores ---------------------------------------------------------

def add_coles(root, weeks, factor=0.9):
    """Coles in the fixtures: a copy of each Woolworths product at `factor` times the price, ids C-…,
    in the given weeks' snapshots, and a coles id beside every woolworths id in products.yaml."""
    for week in weeks:
        path = root / "data" / "prices" / f"{week}.json"
        snap = common.load(path)
        snap["stores"]["coles"] = {"ok": True, "products": [
            {**p, "store": "coles", "id": "C" + p["id"][1:], "name": p["name"].replace("Woolworths", "Coles"),
             "price": round(p["price"] * factor, 2), "usual_price": round(p["price"] * factor, 2)}
            for p in snap["stores"]["woolworths"]["products"]]}
        write(path, snap)
    import yaml
    table = yaml.safe_load((root / "products.yaml").read_text())
    for e in table.values():
        if "woolworths" in e:
            ids = e["woolworths"]
            e["coles"] = ["C" + i[1:] for i in ids] if isinstance(ids, list) else "C" + ids[1:]
    (root / "products.yaml").write_text(yaml.safe_dump(table))


def test_three_store_costing_matches_schema(root):
    add_coles(root, ["2026-11-04", "2026-11-11", WEEK])
    out = run(root)
    check_week(out)
    assert out["stores_ok"] == {"aldi": True, "woolworths": True, "coles": True} and not out["stale"]
    for o in out["options"]:
        assert set(o["cost"]) == {"aldi", "woolworths", "coles"}
        if o["cost"]["woolworths"] is not None:
            assert o["cost"]["coles"] is not None and o["cost"]["coles"] <= o["cost"]["woolworths"]
        assert set(o["cost_per_50g_protein"]) == set(pw.STORES)
    for p in out["pairs"].values():
        if p["woolworths"]:
            assert p["coles"]["effective"] <= p["woolworths"]["effective"]
            assert p["choice"] != "woolworths"          # Coles is the same basket, 10% cheaper


def three_ctx(aldi, woolworths, coles, products):
    return {"catalogs": {"aldi": aldi, "woolworths": woolworths, "coles": coles}, "products": products, "freezer": []}


def test_best_single_store_and_best_pair_split_among_three():
    # x is cheapest at Aldi, y at Woolworths, z at Coles. Singles: Aldi 70, Woolworths 70, Coles 53.
    # Pairs: Aldi+Woolworths 55, Aldi+Coles 33, Woolworths+Coles 45. All three would be 30, never offered.
    products = {k: {"aldi": f"A{k}", "woolworths": f"W{k}", "coles": f"C{k}"} for k in "xyz"}
    ctx = three_ctx({"Ax": product("Ax", 10.0), "Ay": product("Ay", 30.0), "Az": product("Az", 30.0)},
                    {"Wx": product("Wx", 25.0), "Wy": product("Wy", 15.0), "Wz": product("Wz", 30.0)},
                    {"Cx": product("Cx", 30.0), "Cy": product("Cy", 18.0), "Cz": product("Cz", 5.0)}, products)
    p = pw.price_pair({"lines": [line("x", 1000, for_="a"), line("y", 1000, for_="a")]},
                      {"lines": [line("z", 1000, for_="b")]}, ctx)
    assert {s: p[s]["total"] for s in pw.STORES} == {"aldi": 70.0, "woolworths": 70.0, "coles": 53.0}
    assert p["split"]["stores"] == ["aldi", "coles"] and p["split"]["effective"] == 33.0
    assert {s: [i["id"] for i in v] for s, v in p["split"]["items"].items()} == {"aldi": ["Ax"], "coles": ["Cy", "Cz"]}
    assert p["choice"] == "split"                       # saves $20 over Coles alone
    # without the Aldi bargain the best single store wins: Coles 53 against a 45 split
    ctx["catalogs"]["aldi"] = None
    p = pw.price_pair({"lines": [line("x", 1000, for_="a"), line("y", 1000, for_="a")]},
                      {"lines": [line("z", 1000, for_="b")]}, ctx)
    assert p["aldi"] is None and p["split"]["stores"] == ["woolworths", "coles"] and p["choice"] == "coles"


@pytest.mark.parametrize("c_y,choice", [(20.0, "split"), (20.01, "aldi")])
def test_split_threshold_three_stores(c_y, choice):
    # Aldi 50 (ties to Aldi as the first store), Coles 30 + c_y, Woolworths 80; Aldi+Coles = 20 + c_y.
    products = {k: {"aldi": f"A{k}", "woolworths": f"W{k}", "coles": f"C{k}"} for k in "xy"}
    ctx = three_ctx({"Ax": product("Ax", 20.0), "Ay": product("Ay", 30.0)},
                    {"Wx": product("Wx", 40.0), "Wy": product("Wy", 40.0)},
                    {"Cx": product("Cx", 30.0), "Cy": product("Cy", c_y)}, products)
    p = pw.price_pair({"lines": [line("x", 1000, for_="a")]}, {"lines": [line("y", 1000, for_="b")]}, ctx)
    assert p["split"]["stores"] == ["aldi", "coles"] and p["split"]["total"] == round(20 + c_y, 2)
    assert p["choice"] == choice


def test_coles_missing_this_week_falls_back(root):
    add_coles(root, ["2026-11-11"])                      # only last week's snapshot has Coles
    out = run(root)
    assert out["stale"] and out["prices_weeks"] == {"aldi": WEEK, "woolworths": WEEK, "coles": "2026-11-11"}
    assert out["stores_ok"] == {"aldi": True, "woolworths": True, "coles": False}
    assert any(o["cost"]["coles"] is not None for o in out["options"])
    assert not any(a["store"] == "coles" for a in out["alerts"])  # no bulk alert from stale prices
    check_week(out)


def test_coles_with_no_data_is_left_out(root):
    out = run(root)                                      # the fixtures have never had Coles
    assert out["prices_weeks"]["coles"] is None and out["stores_ok"]["coles"] is False
    assert all(o["cost"]["coles"] is None and o["per_serve_by_store"]["coles"] is None for o in out["options"])
    for p in out["pairs"].values():
        assert p["coles"] is None and p["choice"] != "coles"
        assert p["split"] is None or p["split"]["stores"] == ["aldi", "woolworths"]


# --- cost per 50 g of protein ---------------------------------------------

def fish_option(need):
    ctx = {**fish_ctx(), "freezer": [], "protein": {"white fish": {"protein_per_100g": 18.5, "kcal_per_100g": 85}}}
    prepared = {"cut": "white fish", "serves": 6, "per_serve": {"meat_g": 270, "protein_g": 50, "kcal": 211},
                "ingredients": [{"name": "basa", "key": "basa", "base_qty": need, "base_unit": "g", "is_protein": True}]}
    return {**cand_for_score(slug="fish", cut="white fish"), **pw.cost_option("fish", prepared, ctx)}


def test_cooked_extra_meat_scores_better_for_the_same_checkout():
    exact, extra = fish_option(2000), fish_option(1622)  # both 2 x 1 kg at $10: $20, nothing frozen
    assert exact["cost_effective"]["aldi"] == extra["cost_effective"]["aldi"] == 20.0
    # 1622 g: 378 g cooked in, 63 g a serve more fish, protein 50 -> 62 g a serve
    assert extra["per_serve_by_store"]["aldi"]["protein_g"] == 62
    assert exact["cost_per_50g_protein"]["aldi"] == round(20 / 6, 2)
    assert extra["cost_per_50g_protein"]["aldi"] == round(20 / 6 * 50 / 62, 2)
    per_serve_term = -20 / 6 / pw.DOLLARS_PER_POINT        # what both scored before
    assert pw.score_terms(exact, score_ctx())["cost"] == pytest.approx(per_serve_term, abs=0.01)
    assert pw.score_terms(extra, score_ctx())["cost"] > per_serve_term + 0.2


def test_unknown_protein_falls_back_to_cost_per_serve():
    assert pw.cost_per_protein(20.0, 6, None) is None and pw.cost_per_protein(None, 6, 50) is None
    ctx = {**fish_ctx(), "freezer": [], "protein": {}}
    prepared = {"cut": None, "serves": 6, "per_serve": {"kcal": 400},
                "ingredients": [{"name": "basa", "key": "basa", "base_qty": 2000, "base_unit": "g"}]}
    c = {**cand_for_score(cut=None), **pw.cost_option("veg", prepared, ctx)}
    assert c["cost_per_50g_protein"] == {"aldi": None, "woolworths": None, "coles": None}
    assert pw.score_terms(c, score_ctx())["cost"] == pytest.approx(-20 / 6 / pw.DOLLARS_PER_POINT)


# --- per person -------------------------------------------------------------

TJ = "148d862cd5a04037b7f641dbc0f55c72"
TED = "b37e212b9eb2438c9fcec4e98fe55e5d"
IPAD = "6f45a26dcda04f2883b018f6106049f6"


def people(root):
    (root / "people.yaml").write_text(f"{TJ}: {{name: TJ, notify: notify.x}}\n{TED}: {{name: Ted}}\n"
                                      f"{IPAD}: {{name: iPad, shared: true}}\n")
    return root


def set_settings(root, person, **kw):
    write(common.person_dir(root, person) / "settings.json", kw)


def test_default_settings_reproduce_the_single_person_plan(root):
    before = run(root)
    people(root)
    after = run(root, person=TED)
    for out in (before, after):
        out.pop("generated")
    assert after.pop("person") == TED and after.pop("settings") == common.DEFAULT_SETTINGS
    assert after.pop("stores") == list(pw.STORES)
    before.pop("person"), before.pop("settings"), before.pop("stores")
    assert after == before


def test_settings_reach_prepare(root):
    people(root)
    set_settings(root, TED, serves=4, protein_g=30, swaps={"butter": False, "cream": False})
    seen = []

    def spy(recipe, **kw):
        seen.append(kw)
        return fake_prepare(recipe, **kw)
    out = pw.plan(WEEK, prepare=spy, root=root, person=TED)
    assert seen and all(k["serves"] == 4 and k["protein_g"] == 30 and k["swap_ids"] == ["oil", "sugar"] for k in seen)
    assert all(o["serves"] == 4 for o in out["options"])
    seen.clear()
    pw.plan(WEEK, prepare=spy, root=root, person=TJ)
    assert all(k["serves"] == 6 and k["protein_g"] == 50 and k["swap_ids"] == ["butter", "cream", "oil", "sugar"]
               for k in seen)


def test_cost_per_protein_uses_the_persons_target(root):
    people(root)
    base = run(root, person=TJ)
    set_settings(root, TED, protein_g=25)
    half = run(root, person=TED)                      # fake_prepare always gives 50 g a serve
    a = {o["slug"]: o["cost_per_50g_protein"] for o in base["options"]}
    for o in half["options"]:
        if o["slug"] in a:
            for s, v in o["cost_per_50g_protein"].items():
                assert v is None or v == pytest.approx(a[o["slug"]][s] / 2, abs=0.01)


@pytest.mark.parametrize("recipes, options, per_key", [(1, 4, 1), (2, 4, 2), (3, 6, 3)])
def test_recipes_set_options_and_pick_keys(root, recipes, options, per_key):
    people(root)
    set_settings(root, TED, recipes=recipes)
    out = run(root, person=TED)
    slugs_ = [o["slug"] for o in out["options"]]
    assert len(slugs_) == options == pw.options_wanted(recipes)
    assert set(out["pairs"]) == {"+".join(sorted(c)) for c in itertools.combinations(slugs_, per_key)}
    assert len(out["pairs"]) == math.comb(options, per_key)
    check_week(out)


def test_only_the_persons_stores_are_priced(root):
    people(root)
    set_settings(root, TED, stores=["aldi"])
    out = run(root, person=TED)
    assert out["stores"] == ["aldi"] and out["prices_weeks"]["woolworths"] is None
    assert out["stores_ok"]["woolworths"] is True       # the data was there; Ted just doesn't shop there
    for o in out["options"]:
        assert o["cost"]["woolworths"] is None and o["cost"]["aldi"] is not None
    for p in out["pairs"].values():
        assert p["woolworths"] is None and p["split"] is None and p["choice"] == "aldi"
    assert all(a["store"] == "aldi" for a in out["alerts"])
    check_week(out)


def test_people_have_their_own_history(root):
    people(root)
    first = run(root, person=TJ)["options"][0]["slug"]
    write(common.person_dir(root, TJ) / "tuning.json", {"recipes": {first: {"score": -1, "date": WEEK}}})
    assert first not in [o["slug"] for o in run(root, person=TJ)["options"]]
    assert first in [o["slug"] for o in run(root, person=TED)["options"]]


def test_main_plans_everyone_with_a_plan_into_their_folders(root, capsys):
    people(root)
    write(root / "data" / "history.json", {"weeks": [{"week": "2026-11-04", "offered": ["x"], "picked": ["beef-ragu"]}]})
    outs = pw.main(["--week", WEEK], prepare=fake_prepare, root=root)
    assert list(outs) == [TJ, TED]
    for key in (TJ, TED):
        own = common.person_dir(root, key)
        assert common.load(own / "weeks" / f"{WEEK}.json")["person"] == key
        weeks = common.load(own / "history.json")["weeks"]
        assert weeks[-1] == {"week": WEEK, "offered": [o["slug"] for o in outs[key]["options"]]}
    assert len(common.load(common.person_dir(root, TJ) / "history.json")["weeks"]) == 2   # migrated, then added to
    assert not (root / "data" / "history.json").exists() and not common.person_dir(root, IPAD).exists()
    out = capsys.readouterr().out
    assert "moved data/history.json" in out and "== Ted" in out
    pw.main(["--week", WEEK, "--person", TED], prepare=fake_prepare, root=root)
    assert "moved" not in capsys.readouterr().out


def test_main_one_person(root):
    people(root)
    outs = pw.main(["--week", WEEK, "--person", TED], prepare=fake_prepare, root=root)
    assert list(outs) == [TED] and not common.person_dir(root, TJ).exists()
    for bad in (IPAD, "nobody"):
        with pytest.raises(SystemExit):
            pw.main(["--week", WEEK, "--person", bad], prepare=fake_prepare, root=root)


def test_main_carries_on_when_one_person_fails(root):
    people(root)
    set_settings(root, TJ, stores=["coles"])            # the fixtures have no Coles prices at all
    with pytest.raises(SystemExit) as e:
        pw.main(["--week", WEEK], prepare=fake_prepare, root=root)
    assert TJ in str(e.value) and TED not in str(e.value)
    assert (common.person_dir(root, TED) / "weeks" / f"{WEEK}.json").exists()
    assert not (common.person_dir(root, TJ) / "weeks").exists()


def test_dry_run_before_migration_reads_tjs_old_files_and_moves_nothing(root, capsys):
    people(root)
    first = run(root)["options"][0]["slug"]
    write(root / "data" / "tuning.json", {"recipes": {first: {"score": -1, "date": WEEK}}})
    outs = pw.main(["--week", WEEK, "--dry-run"], prepare=fake_prepare, root=root)
    assert first not in [o["slug"] for o in outs[TJ]["options"]]
    assert first in [o["slug"] for o in outs[TED]["options"]]
    assert (root / "data" / "tuning.json").exists() and not (root / "data" / "people").exists()
    assert "would move data/tuning.json" in capsys.readouterr().out
