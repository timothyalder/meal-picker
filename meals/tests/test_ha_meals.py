"""Tests for the pure state logic in shopping/pyscript/meals.py.

pyscript files use injected decorators and globals, so the file is exec'd here with stubs
for them; only the plain functions are tested. Nothing here runs inside pyscript.
"""
import json
import pathlib

import pytest

SOURCE = pathlib.Path(__file__).resolve().parents[2] / "pyscript" / "meals.py"
TODAY = "2026-11-25"
TJ = "148d862cd5a04037b7f641dbc0f55c72"
TED = "b37e212b9eb2438c9fcec4e98fe55e5d"
IPAD = "6f45a26dcda04f2883b018f6106049f6"
PEOPLE = {TJ: {"name": "TJ", "shared": False, "has_plan": True},
          TED: {"name": "Ted", "shared": False, "has_plan": True},
          IPAD: {"name": "iPad", "shared": True, "has_plan": False}}
SETTINGS = {"recipes": 2, "serves": 6, "protein_g": 50,
            "swaps": {"cream": True, "oil": True, "butter": True, "sugar": True},
            "stores": ["aldi", "woolworths", "coles"]}


def load():
    def service(*args, **_):
        return args[0] if args and callable(args[0]) else (lambda f: f)
    ns = {"__name__": "meals", "service": service, "pyscript_executor": lambda f: f}
    exec(compile(SOURCE.read_text(), str(SOURCE), "exec"), ns)
    return ns


m = load()


@pytest.fixture
def state():
    return m["empty_state"]()


def test_empty_state_shape(state):
    assert state == {"updated": None, "picks": {}, "ratings": {},
                     "tuning": {"styles": {}, "similar_strength": 0.3, "fade_days": 365}, "freezer": [],
                     "settings": SETTINGS}


def test_settings_defaults_match_common():
    import sys
    sys.path.insert(0, str(SOURCE.parents[1] / "meals"))
    import common
    assert m["DEFAULT_SETTINGS"] == common.DEFAULT_SETTINGS and m["SETTING_RANGES"] == common.SETTING_RANGES
    if (common.ROOT / "people.yaml").exists():  # kept out of the repo
        assert m["OWNER"] == common.owner(common.load_people())
    import fetch_prices
    assert m["STORES"] == fetch_prices.STORES


def test_normalise_fills_missing_keys():
    s = m["normalise"]({"picks": {"2026-11-18": ["a"]}, "tuning": {"styles": {"curry": 1}}})
    assert s["picks"] == {"2026-11-18": ["a"]} and s["ratings"] == {} and s["freezer"] == []
    assert s["tuning"] == {"styles": {"curry": 1}, "similar_strength": 0.3, "fade_days": 365}
    assert m["normalise"](None) == m["empty_state"]()


# --- pick -----------------------------------------------------------------

def test_pick_sets_and_replaces(state):
    m["pick"](state, "2026-11-18", ["a", "b"])
    m["pick"](state, "2026-11-18", ["c"])
    m["pick"](state, "2026-11-25", [])
    assert state["picks"] == {"2026-11-18": ["c"], "2026-11-25": []}
    m["pick"](state, "2026-11-25", None)
    assert state["picks"]["2026-11-25"] == []


@pytest.mark.parametrize("week, slugs", [
    ("2026-11-18", ["a", "b", "c", "d"]),  # more than MAX_PICKS (3)
    ("2026-11-18", ["a", "a"]),           # the same twice
    ("2026-11-18", "a"),                  # not a list
    ("2026-11-18", ["a", 3]),
    ("2026-11-18", [""]),
    ("18/11/2026", ["a"]),                # bad week
    ("20261118", ["a"]),
    (None, ["a"]),
])
def test_pick_rejects(state, week, slugs):
    with pytest.raises(ValueError):
        m["pick"](state, week, slugs)
    assert state["picks"] == {}


# --- rate -----------------------------------------------------------------

def test_rate_sets_dated_entry_and_clear_removes(state):
    m["rate"](state, "butter-chicken", "again", TODAY, "Butter Chicken", "curry", "Indian")
    assert state["ratings"] == {"butter-chicken": {"rating": "again", "date": TODAY, "title": "Butter Chicken",
                                                   "style": "curry", "cuisine": "Indian"}}
    m["rate"](state, "butter-chicken", "never", "2026-12-02")
    assert state["ratings"]["butter-chicken"]["rating"] == "never"
    assert state["ratings"]["butter-chicken"]["date"] == "2026-12-02"
    m["rate"](state, "butter-chicken", "clear", TODAY)
    m["rate"](state, "not-rated", "clear", TODAY)  # clearing nothing is fine
    assert state["ratings"] == {}


@pytest.mark.parametrize("slug, rating", [("a", "great"), ("a", None), ("a", "Again"), ("", "fine"), (None, "fine")])
def test_rate_rejects(state, slug, rating):
    with pytest.raises(ValueError):
        m["rate"](state, slug, rating, TODAY)
    assert state["ratings"] == {}


# --- tune -----------------------------------------------------------------

def test_tune_style_weights(state):
    m["tune"](state, style="Curry", weight=0.5)
    m["tune"](state, style="indian", weight=-1)
    assert state["tuning"]["styles"] == {"curry": 0.5, "indian": -1}
    m["tune"](state, style="curry")  # no weight removes it
    assert state["tuning"]["styles"] == {"indian": -1}


def test_tune_settings(state):
    m["tune"](state, similar_strength=0.5, fade_days=0)
    assert state["tuning"]["similar_strength"] == 0.5 and state["tuning"]["fade_days"] == 0
    m["tune"](state, fade_days=180.0)
    assert state["tuning"]["fade_days"] == 180 and isinstance(state["tuning"]["fade_days"], int)


def test_tune_reset_clears_ratings_and_weights_only(state):
    m["rate"](state, "a", "again", TODAY)
    m["tune"](state, style="curry", weight=1)
    m["tune"](state, similar_strength=0.6)
    m["pick"](state, "2026-11-18", ["a"])
    m["tune"](state, reset=True)
    assert state["ratings"] == {} and state["tuning"]["styles"] == {}
    assert state["tuning"]["similar_strength"] == 0.6 and state["picks"] == {"2026-11-18": ["a"]}


@pytest.mark.parametrize("kw", [
    {},                                         # nothing to change
    {"weight": 1},                              # weight without a style
    {"style": "curry", "weight": "high"},
    {"style": "curry", "weight": True},
    {"style": "  "},
    {"similar_strength": -0.1},
    {"similar_strength": "0.3"},
    {"fade_days": -1},
    {"fade_days": 1.5},
])
def test_tune_rejects(state, kw):
    before = json.dumps(state)
    with pytest.raises(ValueError):
        m["tune"](state, **kw)
    assert json.dumps(state) == before


# --- freezer --------------------------------------------------------------

def test_freezer_add_set_remove(state):
    m["freezer"](state, TODAY, add={"cut": " chicken breast ", "grams": 2000, "seasoning": "paprika"})
    m["freezer"](state, TODAY, add={"cut": "beef mince", "grams": 500})
    assert state["freezer"] == [
        {"cut": "chicken breast", "grams": 2000, "date": TODAY, "seasoning": "paprika"},
        {"cut": "beef mince", "grams": 500, "date": TODAY, "seasoning": ""}]
    m["freezer"](state, TODAY, set={"index": 0, "grams": 1500, "date": "2026-11-20"})
    assert state["freezer"][0] == {"cut": "chicken breast", "grams": 1500, "date": "2026-11-20", "seasoning": "paprika"}
    m["freezer"](state, TODAY, remove=0)
    assert [f["cut"] for f in state["freezer"]] == ["beef mince"]
    m["freezer"](state, TODAY, remove=0.0)  # a number selector may send a float
    assert state["freezer"] == []


@pytest.mark.parametrize("kw", [
    {},
    {"add": {"cut": "a", "grams": 1}, "remove": 0},
    {"add": {"grams": 100}},
    {"add": {"cut": "chicken", "grams": 0}},
    {"add": {"cut": "chicken", "grams": "lots"}},
    {"add": {"cut": "chicken", "grams": 100, "colour": "red"}},
    {"add": "chicken"},
    {"remove": 1},                      # bad index
    {"remove": -1},
    {"remove": True},
    {"remove": "0"},
    {"remove": 0.5},
    {"set": {"index": 5, "grams": 100}},
    {"set": {"grams": 100}},
    {"set": {"index": 0, "grams": -5}},
    {"set": {"index": 0, "date": "soon"}},
])
def test_freezer_rejects(state, kw):
    m["freezer"](state, TODAY, add={"cut": "beef mince", "grams": 500})
    before = json.dumps(state)
    with pytest.raises(ValueError):
        m["freezer"](state, TODAY, **kw)
    assert json.dumps(state) == before


# --- publish --------------------------------------------------------------

def week_doc(**kw):
    doc = {"week": "2026-11-18", "prices_week": "2026-11-18", "stale": False,
           "stores_ok": {"aldi": True, "woolworths": True},
           "options": [{"slug": s, "title": s.replace("-", " ").title()} for s in ("a-b", "c", "d", "e")]}
    return {**doc, **kw}


def test_check_week():
    assert m["check_week"](week_doc())
    for bad in (None, [], {"week": "2026-11-18"}, {"week": "soon", "options": []}):
        with pytest.raises(ValueError):
            m["check_week"](bad)


def test_notification_lists_titles():
    title, message = m["notification"](week_doc())
    assert title == "Meals for the week of 2026-11-18"
    assert message.splitlines() == ["- A B", "- C", "- D", "- E"]


def test_notification_flags_stale_and_missing_store():
    _, message = m["notification"](week_doc(stale=True, prices_week="2026-11-11",
                                            stores_ok={"aldi": False, "woolworths": False}))
    assert "earlier week (2026-11-11)" in message and "Aldi, Woolworths" in message
    _, message = m["notification"](week_doc(stores_ok={"aldi": True, "woolworths": False}))
    assert "No fresh prices from Woolworths" in message and "earlier week" not in message
    _, message = m["notification"](week_doc(stores_ok={"aldi": True, "woolworths": False, "coles": False}))
    assert "No fresh prices from Woolworths, Coles." in message
    _, message = m["notification"](week_doc(stores_ok={"aldi": False, "woolworths": False, "coles": False}))
    assert "No fresh prices from Aldi, Woolworths, Coles." in message


def test_constants():
    assert m["MEALS_VIEW"] == "/lovelace/meals" and m["WWW"] == "/config/www/meals" and "NOTIFY" not in m


def test_notification_ignores_stores_the_person_does_not_use():
    _, message = m["notification"](week_doc(stores=["aldi"], stores_ok={"aldi": True, "woolworths": False}))
    assert "No fresh prices" not in message


# --- state.json round trip ------------------------------------------------

def test_state_round_trip(state, tmp_path):
    m["pick"](state, "2026-11-18", ["butter-chicken", "beef-stroganoff"])
    m["rate"](state, "butter-chicken", "again", TODAY, "Butter Chicken", "curry", "Indian")
    m["tune"](state, style="curry", weight=0.3)
    m["freezer"](state, TODAY, add={"cut": "chicken breast", "grams": 2000, "seasoning": "paprika and garlic"})
    m["stamp"](state, "2026-11-25T19:02:11")
    path = tmp_path / "state.json"
    path.write_text(json.dumps(state, indent=1, ensure_ascii=False))
    back = m["normalise"](json.loads(path.read_text()))
    assert back == state
    assert set(back) == {"updated", "picks", "ratings", "tuning", "freezer", "settings"}  # a person's entry


# --- people, settings and the migration ------------------------------------

OLD = {"updated": "2026-11-18T19:02:11", "picks": {"2026-11-18": ["a", "b"]},
       "ratings": {"a": {"rating": "again", "date": "2026-11-25"}},
       "tuning": {"styles": {"curry": 0.3}, "similar_strength": 0.4, "fade_days": 0},
       "freezer": [{"cut": "beef mince", "grams": 500, "date": "2026-11-15", "seasoning": ""}]}


def test_old_state_migrates_into_tjs_entry_once():
    new = m["normalise_all"](json.loads(json.dumps(OLD)))
    assert set(new) == {"updated", "people"} and list(new["people"]) == [TJ]
    tj = new["people"][TJ]
    assert tj["picks"] == OLD["picks"] and tj["ratings"] == OLD["ratings"] and tj["freezer"] == OLD["freezer"]
    assert tj["tuning"] == OLD["tuning"] and tj["settings"] == SETTINGS and tj["updated"] == OLD["updated"]
    assert m["normalise_all"](json.loads(json.dumps(new))) == new           # idempotent
    assert m["normalise_all"](None) == {"updated": None, "people": {}}


def test_new_state_is_filled_in_per_person():
    new = m["normalise_all"]({"updated": "x", "people": {TED: {"settings": {"serves": 4, "swaps": {"oil": False}}}}})
    ted = new["people"][TED]
    assert ted["settings"]["serves"] == 4 and ted["settings"]["recipes"] == 2
    assert ted["settings"]["swaps"] == {"cream": True, "oil": False, "butter": True, "sugar": True}
    assert ted["picks"] == {} and ted["tuning"]["fade_days"] == 365


def test_settings_change_and_keep_the_rest(state):
    m["settings"](state, recipes=3, serves=4.0, swaps={"butter": False})
    m["settings"](state, protein_g=35, stores=["coles", "aldi"])
    assert state["settings"] == {"recipes": 3, "serves": 4, "protein_g": 35,
                                 "swaps": {"cream": True, "oil": True, "butter": False, "sugar": True},
                                 "stores": ["aldi", "coles"]}   # STORES order


@pytest.mark.parametrize("kw", [
    {},
    {"recipes": 0}, {"recipes": 4}, {"recipes": 1.5}, {"recipes": "2"}, {"recipes": True},
    {"serves": 1}, {"serves": 9},
    {"protein_g": 19}, {"protein_g": 81}, {"protein_g": None, "serves": 10},
    {"swaps": {"rice": False}}, {"swaps": {"oil": "no"}}, {"swaps": ["oil"]}, {"swaps": {}},
    {"swaps": {"butter-frying": False}},               # settings use the group id, "butter"
    {"stores": []}, {"stores": ["iga"]}, {"stores": "aldi"}, {"stores": ["aldi", "aldi"]},
    {"recipes": 2, "stores": []},                      # one bad field: nothing changes
])
def test_settings_rejects(state, kw):
    before = json.dumps(state)
    with pytest.raises(ValueError):
        m["settings"](state, **kw)
    assert json.dumps(state) == before


def test_check_person():
    assert m["check_person"](PEOPLE, TJ) == TJ
    for people, person in ((PEOPLE, "nobody"), (PEOPLE, IPAD), (PEOPLE, None), (PEOPLE, ""), ({}, TJ)):
        with pytest.raises(ValueError):
            m["check_person"](people, person)


def test_check_people():
    out = m["check_people"]({TJ: {"name": " TJ "}, IPAD: {"name": "iPad", "shared": True}})
    assert out == {TJ: {"name": "TJ", "shared": False, "has_plan": True},
                   IPAD: {"name": "iPad", "shared": True, "has_plan": False}}
    for bad in (None, {}, {TJ: {"name": ""}}, {TJ: "TJ"}, {TJ: {"name": "TJ", "notify": "notify.x"}}):
        with pytest.raises(ValueError):
            m["check_people"](bad)


def test_check_target():
    assert m["check_target"]("notify.mobile_app_x") == "notify.mobile_app_x"
    assert m["check_target"](None) is None and m["check_target"]("") is None
    for bad in ("light.kitchen", 3, "notify."):
        with pytest.raises(ValueError):
            m["check_target"](bad)


def test_executor_writes_atomically(tmp_path):
    path = str(tmp_path / "www" / "meals" / "state.json")
    m["_write_text"](path, '{"a": 1}')
    m["_write_text"](path, '{"a": 2}')
    assert m["_read_json"](path) == {"a": 2}
    assert [p.name for p in (tmp_path / "www" / "meals").iterdir()] == ["state.json"]
    assert m["_read_json"](str(tmp_path / "missing.json")) is None


# --- the service wrappers, with stub HA globals ---------------------------

class Stub:
    """Stands in for pyscript's service (decorator and service.call), log and event."""
    def __init__(self):
        self.calls, self.events, self.errors = [], [], []

    def __call__(self, *args, **_):
        return args[0] if args and callable(args[0]) else (lambda f: f)

    def call(self, domain, name, **kw):
        self.calls.append((f"{domain}.{name}", kw))

    def fire(self, name, **kw):
        self.events.append((name, kw))

    def error(self, msg):
        self.errors.append(msg)

    def info(self, msg):
        pass


@pytest.fixture
def ha(tmp_path):
    stub = Stub()
    ns = {"__name__": "meals", "service": stub, "pyscript_executor": lambda f: f, "log": stub, "event": stub}
    exec(compile(SOURCE.read_text(), str(SOURCE), "exec"), ns)
    ns["WWW"] = str(tmp_path)
    (tmp_path / "people.json").write_text(json.dumps(PEOPLE))
    return ns, stub, tmp_path


def test_services_write_state_and_fire_events(ha):
    ns, stub, www = ha
    assert ns["meals_pick"](person=TJ, week="2026-11-18", slugs=["a", "b"])["ok"]
    assert ns["meals_rate"](person=TJ, slug="a", rating="again", title="A", style="curry", cuisine="Indian")["ok"]
    assert ns["meals_tune"](person=TJ, style="curry", weight=0.3)["ok"]
    assert ns["meals_freezer"](person=TJ, add={"cut": "beef mince", "grams": 500})["ok"]
    assert ns["meals_freezer"](person=TJ, set={"index": 0, "grams": 400})["ok"]
    assert ns["meals_settings"](person=TJ, serves=4, swaps={"oil": False})["ok"]
    full = json.loads((www / "state.json").read_text())
    assert set(full) == {"updated", "people"} and list(full["people"]) == [TJ]
    state = full["people"][TJ]
    assert state["picks"] == {"2026-11-18": ["a", "b"]} and state["ratings"]["a"]["rating"] == "again"
    assert state["tuning"]["styles"] == {"curry": 0.3} and state["freezer"][0]["grams"] == 400
    assert state["settings"]["serves"] == 4 and state["settings"]["swaps"]["oil"] is False
    assert state["updated"] == full["updated"]
    assert [e[0] for e in stub.events] == ["meals_state_changed"] * 6
    assert all(e[1]["person"] == TJ and e[1]["file"] == "state" for e in stub.events)
    assert stub.errors == []


def test_people_are_kept_apart(ha):
    ns, stub, www = ha
    ns["meals_pick"](person=TJ, week="2026-11-18", slugs=["a"])
    ns["meals_pick"](person=TED, week="2026-11-18", slugs=["b", "c", "d"])
    ns["meals_settings"](person=TED, recipes=3)
    people = json.loads((www / "state.json").read_text())["people"]
    assert people[TJ]["picks"] == {"2026-11-18": ["a"]} and people[TED]["picks"] == {"2026-11-18": ["b", "c", "d"]}
    assert people[TJ]["settings"]["recipes"] == 2 and people[TED]["settings"]["recipes"] == 3


def test_bad_input_logs_and_writes_nothing(ha):
    ns, stub, www = ha
    ns["meals_pick"](person=TJ, week="2026-11-18", slugs=["a"])
    before = (www / "state.json").read_text()
    for call in (lambda: ns["meals_pick"](person=TJ, week="2026-11-18", slugs=["a", "b", "c", "d"]),
                 lambda: ns["meals_rate"](person=TJ, slug="a", rating="meh"),
                 lambda: ns["meals_freezer"](person=TJ, remove=3),
                 lambda: ns["meals_tune"](person=TJ),
                 lambda: ns["meals_settings"](person=TJ, serves=9),
                 lambda: ns["meals_settings"](person=TJ, stores=[]),
                 lambda: ns["meals_pick"](person="nobody", week="2026-11-18", slugs=["a"]),
                 lambda: ns["meals_pick"](person=IPAD, week="2026-11-18", slugs=["a"]),
                 lambda: ns["meals_pick"](week="2026-11-18", slugs=["a"])):
        assert call()["ok"] is False
    assert len(stub.errors) == 9 and len(stub.events) == 1
    assert "unknown person" in stub.errors[6] and "shared screen" in stub.errors[7]
    assert (www / "state.json").read_text() == before


def test_no_people_json_refuses_changes(ha):
    ns, stub, www = ha
    (www / "people.json").unlink()
    assert ns["meals_pick"](person=TJ, week="2026-11-18", slugs=["a"])["ok"] is False
    assert "push-people" in stub.errors[0] and not (www / "state.json").exists()


def test_old_state_file_is_migrated_on_first_change(ha):
    ns, stub, www = ha
    (www / "state.json").write_text(json.dumps(OLD))
    assert ns["meals_rate"](person=TJ, slug="b", rating="fine")["ok"]
    full = json.loads((www / "state.json").read_text())
    assert set(full) == {"updated", "people"}
    assert full["people"][TJ]["picks"] == OLD["picks"] and set(full["people"][TJ]["ratings"]) == {"a", "b"}


def test_state_survives_a_reload(ha, tmp_path):
    ns, stub, www = ha
    ns["meals_rate"](person=TJ, slug="a", rating="fine")
    ns2 = {"__name__": "meals", "service": stub, "pyscript_executor": lambda f: f, "log": stub, "event": stub}
    exec(compile(SOURCE.read_text(), str(SOURCE), "exec"), ns2)
    ns2["WWW"] = str(www)
    ns2["meals_rate"](person=TJ, slug="b", rating="never")
    ns2["meals_rate"](person=TED, slug="c", rating="never")
    people = json.loads((www / "state.json").read_text())["people"]
    assert set(people[TJ]["ratings"]) == {"a", "b"} and set(people[TED]["ratings"]) == {"c"}


def test_publish_writes_the_persons_week_and_notifies_their_target(ha):
    ns, stub, www = ha
    doc = week_doc()
    assert ns["meals_publish"](person=TED, doc=doc, notify=False)["ok"]
    assert json.loads((www / f"week-{TED}.json").read_text()) == doc and stub.calls == []
    assert stub.events[-1] == ("meals_state_changed", {"file": "week", "person": TED, "week": "2026-11-18"})
    r = ns["meals_publish"](person=TED, doc=doc, notify=True)          # no target: nobody to notify
    assert r["ok"] and r["notified"] is False and stub.calls == []
    r = ns["meals_publish"](person=TJ, doc=doc, notify=True, notify_target="notify.mobile_app_tj")
    assert r["notified"] is True and len(stub.calls) == 1
    name, kw = stub.calls[0]
    assert name == "notify.mobile_app_tj" and kw["data"]["url"] == "/lovelace/meals" and "- C" in kw["message"]
    assert not (www / "state.json").exists() and not (www / "week.json").exists()
    for bad in ({"person": TJ, "doc": {"week": "x"}, "notify": True},
                {"person": IPAD, "doc": doc},
                {"person": "nobody", "doc": doc},
                {"person": TJ, "doc": doc, "notify": True, "notify_target": "light.kitchen"}):
        assert ns["meals_publish"](**bad)["ok"] is False
    assert len(stub.calls) == 1


def test_publish_people_writes_people_json_and_is_used_at_once(ha):
    ns, stub, www = ha
    (www / "people.json").unlink()
    newbie = "0123456789abcdef0123456789abcdef"
    assert ns["meals_publish_people"](people={newbie: {"name": "Newbie", "shared": False, "has_plan": True}})["ok"]
    assert json.loads((www / "people.json").read_text()) == {newbie: {"name": "Newbie", "shared": False, "has_plan": True}}
    assert stub.events[-1] == ("meals_state_changed", {"file": "people"})
    assert ns["meals_pick"](person=newbie, week="2026-11-18", slugs=["a"])["ok"]
    assert ns["meals_pick"](person=TJ, week="2026-11-18", slugs=["a"])["ok"] is False   # no longer listed
    assert ns["meals_publish_people"](people={TJ: {"name": "TJ", "notify": "notify.x"}})["ok"] is False


def test_every_service_description_is_valid_yaml():
    # pyscript silently drops a service whose description block isn't valid YAML
    import re
    import yaml
    text = (pathlib.Path(__file__).resolve().parents[2] / "pyscript" / "meals.py").read_text()
    blocks = re.findall(r'def (meals_\w+)\(.*?\):\n    """yaml\n(.*?)"""', text, re.S)
    assert len(blocks) >= 7
    for name, block in blocks:
        assert isinstance(yaml.safe_load(block), dict), name
