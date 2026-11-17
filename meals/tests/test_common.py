"""common.py helpers."""
import datetime
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402


def test_week_of_is_the_wednesday():
    assert common.week_of(datetime.date(2026, 11, 18)) == "2026-11-18"  # Wednesday
    assert common.week_of(datetime.date(2026, 11, 15)) == "2026-11-11"  # Sunday
    assert common.week_of(datetime.date(2026, 11, 24)) == "2026-11-18"  # Tuesday


def test_norm_name():
    assert common.norm_name("  Chicken Thigh Fillets (skinless)  ") == "chicken thigh fillets"
    assert common.norm_name("Extra-virgin olive oil") == "extra virgin olive oil"


def test_to_base():
    assert common.to_base(2, "tbsp") == (40, "ml")
    assert common.to_base(1.5, "kg") == (1500, "g")
    assert common.to_base(1, "cups") == (250, "ml")
    assert common.to_base(3, "cloves") == (3, "ea")
    assert common.to_base(2, None) == (2, "ea")
    assert common.to_base(None, "g") == (None, None)


def test_to_base_extra_units():
    assert common.to_base(2, "Kilograms") == (2000, "g")
    assert common.to_base(500, "millilitres") == (500, "ml")
    assert common.to_base(1, "tbs.") == (20, "ml")
    assert common.to_base(1, "pound") == (453.6, "g")


def test_load_save_and_yaml(tmp_path):
    p = tmp_path / "x" / "a.json"
    assert common.load(p, {}) == {}
    common.save(p, {"a": [1, "é"]})
    assert common.load(p) == {"a": [1, "é"]}
    y = tmp_path / "c.yaml"
    assert common.load_yaml(y, []) == []
    y.write_text("{}\n")
    assert common.load_yaml(y, {"d": 1}) == {"d": 1}
    y.write_text("a: {b: 2}\n")
    assert common.load_yaml(y) == {"a": {"b": 2}}


def test_config_files_parse():
    protein = common.load_yaml(common.ROOT / "protein.yaml")
    assert protein["target_protein_g"] == 50
    cuts = {k: v for k, v in protein.items() if isinstance(v, dict)}
    assert len(cuts) >= 20 and all({"protein_per_100g", "kcal_per_100g", "match"} <= set(v) for v in cuts.values())
    swaps = common.load_yaml(common.ROOT / "swaps.yaml")
    assert all("match" in row and "to" in row for row in swaps)
    assert common.load_yaml(common.ROOT / "overrides.yaml", {}) == {}


def test_resolve_ingredient_names():
    table = {k: {} for k in ["garlic", "chicken breast", "chicken stock", "beef mince", "crushed tomato",
                             "egg noodles", "chicken thigh fillet", "coconut milk", "milk"]}
    cases = {
        "garlic": "garlic", "large garlic, finely chopped": "garlic", "chicken breasts large": "chicken breast",
        "chicken or vegetable stock": "chicken stock", "beef or lamb mince": "beef mince",
        "can crushed tomatoes": "crushed tomato", "fresh egg noodles": "egg noodles",
        "skinless chicken thigh fillets": "chicken thigh fillet", "coconut milk": "coconut milk",
        "green onions": None, "red capsicum bell pepper": None, "chicken": None,
    }
    for name, want in cases.items():
        assert common.resolve(common.norm_name(name), table) == want, name


def test_resolve_synonyms_and_phrases():
    table = {k: {} for k in ["greek yoghurt", "plain flour", "spring onion", "coconut milk", "milk",
                             "chicken stock", "chicken stock powder", "beef mince"]}
    cases = {"plain yogurt": None, "greek yogurt": "greek yoghurt", "plain flour all purpose flour": "plain flour",
             "green onions": "spring onion", "ground beef": "beef mince", "chicken stock powder": "chicken stock powder",
             "low salt chicken stock cubes": "chicken stock", "light coconut milk": "coconut milk",
             "almond milk": None}
    for name, want in cases.items():
        assert common.resolve(name, table) == want, name


# --- people --------------------------------------------------------------------

TJ = "148d862cd5a04037b7f641dbc0f55c72"


@pytest.mark.skipif(not (common.ROOT / "people.yaml").exists(), reason="no people.yaml (kept out of the repo)")
def test_people_yaml():
    people = common.load_people()
    assert common.owner(people) == TJ and people[TJ]["notify"].startswith("notify.")
    assert common.planned(people) == [k for k, v in people.items() if not v.get("shared")]
    assert len(common.planned(people)) == len(people) - 1
    assert common.load_people(pathlib.Path("/nonexistent")) == {}


def test_settings_of_fills_defaults_and_drops_bad_values():
    assert common.settings_of(None) == common.DEFAULT_SETTINGS
    got = common.settings_of({"recipes": 3, "serves": 4.0, "protein_g": 90, "swaps": {"oil": False},
                              "stores": ["coles", "iga", "aldi"]})
    assert got == {"recipes": 3, "serves": 4, "protein_g": 50,
                   "swaps": {"cream": True, "oil": False, "butter": True, "sugar": True}, "stores": ["aldi", "coles"]}
    assert common.settings_of({"recipes": True, "stores": ["iga"]})["recipes"] == 2
    assert common.settings_of({"stores": ["iga"]})["stores"] == common.DEFAULT_SETTINGS["stores"]
    assert common.settings_of({"stores": ["x", "y"]}, stores=("x", "y", "z"))["stores"] == ["x", "y"]


def test_migration_moves_tjs_files_once(tmp_path):
    (tmp_path / "people.yaml").write_text(f"{TJ}: {{name: TJ}}\nb: {{name: Ted}}\n")
    data = tmp_path / "data"
    for name, body in (("history.json", {"weeks": []}), ("tuning.json", {}), ("freezer.json", []),
                       ("weeks/2026-11-11.json", {"week": "2026-11-11"}), ("run_state.json", {})):
        common.save(data / name, body)
    dry = common.migrate_people(tmp_path, dry_run=True)
    assert len(dry) == 4 and all(done for _, _, done in dry) and (data / "history.json").exists()
    moved = common.migrate_people(tmp_path)
    own = common.person_dir(tmp_path, TJ)
    assert sorted(str(dst.relative_to(own)) for _, dst, _ in moved) == [
        "freezer.json", "history.json", "tuning.json", "weeks/2026-11-11.json"]
    assert common.load(own / "weeks" / "2026-11-11.json") == {"week": "2026-11-11"}
    assert sorted(p.name for p in data.iterdir()) == ["people", "run_state.json"]   # shared files stay
    before = {p: p.read_bytes() for p in own.rglob("*.json")}
    assert common.migrate_people(tmp_path) == []                                     # idempotent
    assert {p: p.read_bytes() for p in own.rglob("*.json")} == before


def test_migration_never_overwrites(tmp_path):
    (tmp_path / "people.yaml").write_text(f"{TJ}: {{name: TJ}}\n")
    common.save(tmp_path / "data" / "history.json", {"weeks": ["old"]})
    common.save(common.person_dir(tmp_path, TJ) / "history.json", {"weeks": ["new"]})
    assert [done for _, _, done in common.migrate_people(tmp_path)] == [False]
    assert common.load(common.person_dir(tmp_path, TJ) / "history.json") == {"weeks": ["new"]}
    assert common.load(tmp_path / "data" / "history.json") == {"weeks": ["old"]}


def test_migration_without_tj_does_nothing(tmp_path):
    (tmp_path / "people.yaml").write_text("a: {name: Ted}\n")
    common.save(tmp_path / "data" / "history.json", {})
    assert common.migrate_people(tmp_path) == [] and (tmp_path / "data" / "history.json").exists()
