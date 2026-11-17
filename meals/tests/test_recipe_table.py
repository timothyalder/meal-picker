"""recipe_table.prepare() against invented fixtures (no real recipe text)."""
import json
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import recipe_table  # noqa: E402

FIXTURES = pathlib.Path(__file__).parent / "fixtures" / "table"


def recipe(name):
    return json.loads((FIXTURES / f"{name}.json").read_text())


def table(name, serves=6, **kw):
    kw.setdefault("products", {})
    kw.setdefault("overrides", {})
    return recipe_table.prepare(recipe(name), serves, **kw)


def ing(t, original):
    return next(i for i in t["ingredients"] if (i["original"] or i["name"]) == original)


def mini(ingredients, steps, serves=4, **kw):
    r = {"slug": "mini", "title": "Mini", "serves": serves, "steps": steps,
         "ingredients": [{"amount": a, "unit": u, "name": n, "group": g} for a, u, n, g in ingredients]}
    kw.setdefault("products", {})
    kw.setdefault("overrides", {})
    return recipe_table.prepare(r, 6, **kw)


def test_one_meat_hits_protein_target():
    t = table("test-chicken-curry")
    chicken = ing(t, "chicken thigh fillets")
    assert t["cut"] == "chicken thigh fillet" and chicken["is_protein"]
    assert chicken["base_qty"] * 0.195 == pytest.approx(50 * 6)
    assert t["per_serve"] == {"protein_g": 50, "meat_g": 256, "kcal": t["per_serve"]["kcal"]}
    assert chicken["unit"] == "g" and chicken["amount"] == 1550


def test_chicken_breast_example_from_design():
    t = mini([(500, "g", "chicken breast", None)], ["Cook the chicken."])
    assert t["per_serve"]["meat_g"] == 222


def test_two_meats_scale_together_keeping_ratio():
    t = table("test-surf-and-turf")
    beef, prawns = ing(t, "beef rump steak"), ing(t, "raw prawns")
    assert t["cuts"] == ["beef rump", "prawns"] and t["cut"] == "beef rump"
    assert beef["base_qty"] / prawns["base_qty"] == pytest.approx(500 / 250)
    assert beef["base_qty"] * 0.225 + prawns["base_qty"] * 0.20 == pytest.approx(300)
    assert not ing(t, "eggs")["is_protein"]  # two eggs are under a quarter of the beef's weight


def test_no_cut_scales_by_serves_only():
    t = table("test-veg-soup")
    assert t["cut"] is None and t["per_serve"]["protein_g"] is None
    assert ing(t, "pumpkin")["base_qty"] == 2000
    assert t["per_serve"]["kcal"] == 210


def test_kcal_estimate_swaps_in_resized_meat():
    t = table("test-chicken-curry")
    before = 600 * 1.45 / 4
    after = ing(t, "chicken thigh fillets")["base_qty"] * 1.45 / 6
    # Swaps take off: 60 ml oil x 8.2 + 45 g butter x 7.2 + 300 ml cream x 2.3 + 60 ml sugar x 3.9, over 6 serves.
    saved = (60 * 8.2 + 45 * 7.2 + 300 * 2.3 + 60 * 3.9) / 6
    assert t["per_serve"]["kcal"] == round(500 - before + after - saved)


def test_spray_oil_has_no_amount():
    oil = ing(table("test-chicken-curry"), "vegetable oil")
    assert oil["name"] == "spray oil" and oil["text"] == "" and oil["base_qty"] is None


def test_liquid_gets_a_quarter_more_and_spice_is_untouched():
    t = table("test-chicken-curry")
    assert ing(t, "chicken stock")["base_qty"] == pytest.approx(250 * 1.5 * 1.25)
    assert ing(t, "crushed tomatoes")["base_qty"] == pytest.approx(400 * 1.5 * 1.25)
    chilli = ing(t, "chilli powder")
    assert chilli["original"] is None and chilli["note"] is None and not chilli["removed"]
    assert chilli["base_qty"] == pytest.approx(10 * 1.5) and chilli["text"] == "3 tsp"
    assert ing(t, "garam masala")["original"] is None


def test_cream_becomes_greek_yoghurt_with_note():
    t = table("test-chicken-curry")
    y = ing(t, "thickened cream")
    assert y["name"] == "Greek yoghurt" and y["key"] == "greek yoghurt" and not y["removed"]
    assert y["base_qty"] == pytest.approx(200 * 1.5 * 1.25)  # 1:1
    assert "boil" in y["note"]
    assert any("Greek yoghurt" in n for n in t["notes"])


def test_sour_cream_swapped_but_not_cream_cheese_or_coconut_cream():
    t = mini([(1, "cup", "sour cream", None), (100, "g", "cream cheese", None), (1, "cup", "coconut cream", None)],
             ["Mix everything."])
    names = [i["name"] for i in t["ingredients"]]
    assert names == ["Greek yoghurt", "cream cheese", "coconut cream"]


def test_cooking_oil_becomes_spray_but_sesame_oil_stays():
    t = mini([(2, "tbsp", "olive oil", None), (1, "tbsp", "canola oil", None), (1, "tsp", "sesame oil", None)],
             ["Fry in the oil, finish with sesame oil."])
    assert [i["name"] for i in t["ingredients"]] == ["spray oil", "spray oil", "sesame oil"]
    assert t["ingredients"][0]["original"] == "olive oil" and t["ingredients"][0]["key"] == "spray oil"


def test_butter_for_frying_becomes_spray_oil():
    b = ing(table("test-chicken-curry"), "butter")
    assert b["name"] == "spray oil" and not b["removed"] and b["step"] == 2


def test_butter_elsewhere_is_removed_but_kept_in_list():
    t = table("test-surf-and-turf")
    b = ing(t, "unsalted butter")
    assert b["removed"] and b["step"] == 1 and b["base_qty"] == pytest.approx(60)


def test_sugar_note_only_above_a_tablespoon():
    big = table("test-chicken-curry")
    s = ing(big, "brown sugar")
    assert s["removed"] and s["text"] == "3 tbsp"
    assert "Brown sugar removed: the recipe used 3 tbsp, so the sauce will be less sweet" in big["notes"]
    small = table("test-surf-and-turf")
    assert ing(small, "caster sugar")["removed"]
    assert not any("sugar" in n.lower() for n in small["notes"])


def test_honey_and_rice_stay():
    t = mini([(2, "tbsp", "honey", None), (2, "cups", "white rice", None)], ["Cook the rice, drizzle honey."])
    assert [i["original"] for i in t["ingredients"]] == [None, None]


def test_step_placement_group_claim_and_unplaced():
    t = table("test-chicken-curry")
    assert ing(t, "turmeric")["step"] == 1           # claimed by "Marinade" in step 1
    assert ing(t, "chicken thigh fillets")["step"] == 1
    assert ing(t, "brown onion")["step"] == 2
    assert ing(t, "garlic cloves")["step"] == 2
    assert ing(t, "crushed tomatoes")["step"] == 4  # plural "tomatoes" in the step
    assert ing(t, "basmati rice")["step"] == 5
    assert ing(t, "bay leaves")["step"] == 1 and t["unplaced"] == ["bay leaves"]
    assert t["steps"][0]["ingredients"] == [0, 1, 2, 14]


def test_plural_tolerance():
    t = mini([(2, None, "potato", None), (3, None, "chillies", None)], ["Peel the potatoes.", "Add the chilli."])
    assert [i["step"] for i in t["ingredients"]] == [1, 2] and t["unplaced"] == []


def test_override_wins():
    t = table("test-chicken-curry", overrides={"test-chicken-curry": {"steps_for": {"Bay Leaves": 4, "garam masala": 2}}})
    assert ing(t, "bay leaves")["step"] == 4 and ing(t, "garam masala")["step"] == 2
    assert t["unplaced"] == []


def test_override_step_moves_butter_out_of_frying():
    t = table("test-chicken-curry", overrides={"test-chicken-curry": {"steps_for": {"butter": 5}}})
    assert ing(t, "butter")["removed"]


def test_products_cut_marks_protein():
    t = mini([(500, "g", "mystery meat", None)], ["Cook the meat."], products={"mystery meat": {"cut": "beef chuck"}})
    assert t["cut"] == "beef chuck" and t["per_serve"]["meat_g"] == 250


@pytest.mark.parametrize("amount,unit,want", [
    (123, "g", "125 g"), (612, "g", "600 g"), (638, "g", "650 g"), (3.2, "g", "3 g"),
    (133, "ml", "135 ml"), (0.4, "tsp", "1/2 tsp"), (1.1, "tbsp", "1 tbsp"), (5.4, "tbsp", "5 1/2 tbsp"),
    (0.3, "cup", "1/3 cup"), (1.7, "cups", "1 2/3 cups"), (1.3, None, "1 1/2"), (0.1, None, "1/2"),
    (2.2, "cloves", "2 cloves"), (1.2, "kg", "1200 g"), (0.06, "tsp", "1/4 tsp"),
])
def test_rounding(amount, unit, want):
    t = recipe_table.prepare({"serves": 6, "steps": ["x"], "ingredients": [{"amount": amount, "unit": unit, "name": "stuff"}]},
                             6, products={}, swaps=[], protein={}, overrides={})
    assert t["ingredients"][0]["text"] == want


def test_base_qty_is_unrounded():
    t = mini([(123, "g", "carrot", None)], ["Add the carrot."], serves=6)
    i = t["ingredients"][0]
    assert i["base_qty"] == 123 and i["amount"] == 125


def test_ratio():
    t = mini([(100, "g", "lard", None)], ["Fry in lard."], swaps=[{"match": ["lard"], "to": "spray oil", "ratio": 0.1}])
    assert t["ingredients"][0]["base_qty"] == pytest.approx(15)


def test_json_round_trip_and_renderers():
    for name in ("test-chicken-curry", "test-surf-and-turf", "test-veg-soup"):
        t = table(name)
        assert json.loads(json.dumps(t)) == t
        assert {"serves", "cut", "per_serve", "ingredients", "steps", "notes", "unplaced"} <= set(t)
        text = recipe_table.as_text(t)
        assert t["title"] in text
        page = recipe_table.as_html(t)
        assert page.startswith("<table") and page.endswith("</table>") and "<script" not in page
    page = recipe_table.as_html(table("test-chicken-curry"))
    assert 'rowspan="4"' in page and "<s>brown sugar</s>" in page


# --- per-person settings ----------------------------------------------------

def test_protein_target_per_person():
    t = table("test-chicken-curry", protein_g=30)
    chicken = ing(t, "chicken thigh fillets")
    assert chicken["base_qty"] * 0.195 == pytest.approx(30 * 6) and t["per_serve"]["protein_g"] == 30
    assert table("test-chicken-curry", protein_g=None) == table("test-chicken-curry")  # default: protein.yaml's 50


def test_serves_per_person():
    t = table("test-chicken-curry", serves=2, protein_g=40)
    assert t["serves"] == 2 and ing(t, "chicken thigh fillets")["base_qty"] * 0.195 == pytest.approx(40 * 2)
    assert ing(t, "chicken stock")["base_qty"] == pytest.approx(250 * 0.5 * 1.25)


def test_swap_ids_turn_rows_off():
    every = ["cream", "oil", "butter", "sugar"]
    assert table("test-chicken-curry", swap_ids=every) == table("test-chicken-curry")
    t = table("test-chicken-curry", swap_ids=["oil", "sugar"])
    cream, butter = ing(t, "thickened cream"), ing(t, "butter")
    assert cream["name"] == "thickened cream" and cream["original"] is None
    assert butter["name"] == "butter" and not butter["removed"] and butter["base_qty"] > 0  # butter-frying off too
    assert ing(t, "vegetable oil")["name"] == "spray oil" and ing(t, "brown sugar")["removed"]
    assert not any("Greek yoghurt" in n for n in t["notes"])
    assert t["per_serve"]["kcal"] > table("test-chicken-curry")["per_serve"]["kcal"]   # less saved
    none = table("test-chicken-curry", swap_ids=[])
    assert all(i["original"] is None and not i["removed"] for i in none["ingredients"])
    assert not ing(table("test-surf-and-turf", swap_ids=["cream"]), "unsalted butter")["removed"]  # the other butter row


def test_swap_setting_groups_rows():
    assert recipe_table.swap_setting({"id": "butter-frying"}) == "butter"
    assert recipe_table.swap_setting({"id": "oil"}) == "oil" and recipe_table.swap_setting({"match": ["x"]}) is None
    rows = [{"id": "butter-frying"}, {"id": "butter"}, {"id": "oil"}, {"match": ["lard"]}]
    assert recipe_table.enabled_swaps(rows, ["oil"]) == [{"id": "oil"}, {"match": ["lard"]}]
    assert recipe_table.enabled_swaps(rows, None) == rows


def test_every_swaps_yaml_row_has_a_setting():
    import common
    rows = common.load_yaml(common.ROOT / "swaps.yaml", [])
    assert {recipe_table.swap_setting(r) for r in rows} == set(common.DEFAULT_SETTINGS["swaps"])
