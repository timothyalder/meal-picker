"""Tests for ha_sync.py. HTTP is stubbed; nothing talks to Home Assistant."""
import json
import pathlib
import sys

import pytest
import requests

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402
import ha_sync  # noqa: E402

TJ = "148d862cd5a04037b7f641dbc0f55c72"
TED = "b37e212b9eb2438c9fcec4e98fe55e5d"
IPAD = "6f45a26dcda04f2883b018f6106049f6"
PEOPLE_YAML = f"""
{TJ}: {{name: TJ, notify: notify.mobile_app_tj}}
{TED}: {{name: Ted}}
{IPAD}: {{name: iPad, shared: true}}
"""

OLD_STATE = {
    "updated": "2026-11-25T19:02:11",
    "picks": {"2026-11-18": ["butter-chicken", "beef-stroganoff"], "2026-11-25": ["lamb-tagine"]},
    "ratings": {
        "butter-chicken": {"rating": "again", "date": "2026-11-24", "title": "Butter Chicken", "style": "curry",
                           "cuisine": "Indian"},
        "beef-stroganoff": {"rating": "never", "date": "2026-11-24"},
        "old-favourite": {"rating": "fine", "date": "2026-11-20"},   # never picked: tuning only
    },
    "tuning": {"styles": {"Curry": 0.3, "indian": -0.1}, "similar_strength": 0.4, "fade_days": 0},
    "freezer": [{"cut": "chicken breast", "grams": 2000, "date": "2026-11-15", "seasoning": "paprika"}],
}
TED_ENTRY = {"picks": {"2026-11-18": ["x"]}, "ratings": {}, "tuning": {"styles": {}}, "freezer": [],
             "settings": {"recipes": 1, "serves": 4, "protein_g": 35, "swaps": {"butter": False},
                          "stores": ["coles"]}, "updated": "2026-11-24T10:00:00"}
STATE = {"updated": "2026-11-25T19:02:11", "people": {TJ: {**OLD_STATE, "settings": {}}, TED: TED_ENTRY}}


def tj(root, name):
    return common.person_dir(root, TJ) / name


class Response:
    def __init__(self, status, body=None):
        self.status_code, self.body = status, body

    def json(self):
        return self.body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise requests.HTTPError(f"{self.status_code}")


@pytest.fixture
def root(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "people.yaml").write_text(PEOPLE_YAML)
    return tmp_path


def serve(monkeypatch, status, body=None):
    seen = []

    def get(url, timeout):
        seen.append(url)
        return Response(status, body)
    monkeypatch.setattr(ha_sync.requests, "get", get)
    return seen


def test_pull_writes_each_persons_files(root, monkeypatch):
    common.save(tj(root, "history.json"), {"weeks": [
        {"week": "2026-11-11", "offered": ["x", "y"], "picked": ["x"], "ratings": {}},
        {"week": "2026-11-18", "offered": ["butter-chicken", "beef-stroganoff", "c", "d"]}]})
    seen = serve(monkeypatch, 200, STATE)
    assert ha_sync.pull(root)
    assert seen == [f"{ha_sync.HA_URL}/local/meals/state.json"]

    weeks = common.load(tj(root, "history.json"))["weeks"]
    assert weeks == [
        {"week": "2026-11-11", "offered": ["x", "y"], "picked": ["x"], "ratings": {}},
        {"week": "2026-11-18", "offered": ["butter-chicken", "beef-stroganoff", "c", "d"],
         "picked": ["butter-chicken", "beef-stroganoff"],
         "ratings": {"beef-stroganoff": "never", "butter-chicken": "again"}},
        {"week": "2026-11-25", "picked": ["lamb-tagine"], "ratings": {}}]

    assert common.load(tj(root, "tuning.json")) == {
        "recipes": {"beef-stroganoff": {"score": -1, "date": "2026-11-24"},
                    "butter-chicken": {"score": 1, "date": "2026-11-24"},
                    "old-favourite": {"score": 0, "date": "2026-11-20"}},
        "styles": {"curry": 0.3, "indian": -0.1}, "similar_strength": 0.4, "fade_days": 0}
    assert common.load(tj(root, "freezer.json")) == OLD_STATE["freezer"]
    assert common.load(tj(root, "settings.json")) == common.DEFAULT_SETTINGS

    ted = common.person_dir(root, TED)
    assert common.load(ted / "history.json") == {"weeks": [{"week": "2026-11-18", "picked": ["x"], "ratings": {}}]}
    assert common.load(ted / "settings.json") == {
        "recipes": 1, "serves": 4, "protein_g": 35,
        "swaps": {"cream": True, "oil": True, "butter": False, "sugar": True}, "stores": ["coles"]}
    assert not (root / "data" / "history.json").exists()
    assert not common.person_dir(root, IPAD).exists()


def test_pull_reads_an_old_single_person_state_as_tjs(root, monkeypatch):
    serve(monkeypatch, 200, OLD_STATE)
    assert ha_sync.pull(root)
    assert common.load(tj(root, "freezer.json")) == OLD_STATE["freezer"]
    assert [p.name for p in (root / "data" / "people").iterdir()] == [TJ]


def test_pull_migrates_tjs_old_files_first(root, monkeypatch):
    common.save(root / "data" / "history.json", {"weeks": [{"week": "2026-11-18", "offered": ["a", "b"]}]})
    common.save(root / "data" / "weeks" / "2026-11-18.json", {"week": "2026-11-18", "options": []})
    serve(monkeypatch, 200, STATE)
    ha_sync.pull(root)
    week = next(w for w in common.load(tj(root, "history.json"))["weeks"] if w["week"] == "2026-11-18")
    assert week["offered"] == ["a", "b"] and week["picked"] == ["butter-chicken", "beef-stroganoff"]
    assert tj(root, "weeks/2026-11-18.json").exists() and not (root / "data" / "weeks").exists()


def test_pull_bad_settings_fall_back_to_defaults(root, monkeypatch):
    bad = json.loads(json.dumps(STATE))
    bad["people"][TED]["settings"] = {"recipes": 9, "serves": "4", "stores": ["iga"], "protein_g": 60}
    serve(monkeypatch, 200, bad)
    ha_sync.pull(root)
    got = common.load(common.person_dir(root, TED) / "settings.json")
    assert got == {**common.DEFAULT_SETTINGS, "protein_g": 60}


def test_cleared_rating_disappears(root, monkeypatch):
    serve(monkeypatch, 200, STATE)
    ha_sync.pull(root)
    cleared = json.loads(json.dumps(STATE))
    del cleared["people"][TJ]["ratings"]["butter-chicken"]
    serve(monkeypatch, 200, cleared)
    ha_sync.pull(root)
    assert "butter-chicken" not in common.load(tj(root, "tuning.json"))["recipes"]
    week = common.load(tj(root, "history.json"))["weeks"][0]
    assert week["ratings"] == {"beef-stroganoff": "never"}


def test_rating_goes_to_latest_pick_before_its_date():
    weeks = [{"week": "2026-10-07", "picked": ["a"]}, {"week": "2026-11-18", "picked": ["a"]},
             {"week": "2026-11-25", "picked": ["a"]}]
    assert ha_sync.rated_week("a", "2026-11-24", weeks) == "2026-11-18"
    assert ha_sync.rated_week("a", "2026-01-01", weeks) == "2026-11-25"  # none before: latest
    assert ha_sync.rated_week("b", "2026-11-24", weeks) is None


def test_unknown_rating_is_skipped(capsys):
    out = ha_sync.tuning_of({"ratings": {"a": {"rating": "meh", "date": "2026-11-24"}}})
    assert out == {"recipes": {}, "styles": {}} and "meh" in capsys.readouterr().err


def test_404_means_no_state_yet(root, monkeypatch):
    serve(monkeypatch, 404)
    assert ha_sync.pull(root) is False
    assert list((root / "data").iterdir()) == []


def test_pull_http_error_exits_1(root, monkeypatch):
    serve(monkeypatch, 500)
    with pytest.raises(SystemExit) as e:
        ha_sync.main(["pull"], root=root)
    assert "500" in str(e.value)


def test_pull_without_people_yaml_exits(root, monkeypatch):
    (root / "people.yaml").unlink()
    serve(monkeypatch, 200, STATE)
    with pytest.raises(SystemExit):
        ha_sync.pull(root)


def posts(monkeypatch, fail_for=()):
    sent = []

    def post(url, headers, json, timeout):
        sent.append((url, headers, json))
        return Response(500 if json.get("person") in fail_for else 200, [])
    monkeypatch.setattr(ha_sync.requests, "post", post)
    return sent


def test_push_builds_the_service_call(root, monkeypatch):
    doc = {"week": "2026-11-18", "options": [{"slug": "a"}]}
    common.save(tj(root, "weeks/2026-11-18.json"), doc)
    sent = posts(monkeypatch)
    ha_sync.push("2026-11-18", TJ, notify=True, root=root, token="tok")
    assert sent == [(f"{ha_sync.HA_URL}/api/services/pyscript/meals_publish",
                     {"Authorization": "Bearer tok", "Content-Type": "application/json"},
                     {"person": TJ, "doc": doc, "notify": True, "notify_target": "notify.mobile_app_tj"})]
    ha_sync.push("2026-11-18", TJ, notify=False, root=root, token="tok")
    assert sent[-1][2] == {"person": TJ, "doc": doc, "notify": False}


def test_push_notifies_only_people_with_a_target(root, monkeypatch):
    common.save(common.person_dir(root, TED) / "weeks" / "2026-11-18.json", {"week": "2026-11-18", "options": []})
    sent = posts(monkeypatch)
    ha_sync.push("2026-11-18", TED, notify=True, root=root, token="tok")
    assert sent[0][2]["notify"] is False and "notify_target" not in sent[0][2]


def test_push_rejects_shared_and_unknown_people(root):
    for key in (IPAD, "nobody"):
        with pytest.raises(SystemExit):
            ha_sync.push("2026-11-18", key, root=root, token="tok")


def test_push_without_week_file_exits(root):
    with pytest.raises(SystemExit):
        ha_sync.push("2026-11-18", TJ, root=root, token="tok")


def test_push_everyone_carries_on_past_a_failure(root, monkeypatch, capsys):
    for key in (TJ, TED):
        common.save(common.person_dir(root, key) / "weeks" / "2026-11-18.json", {"week": "2026-11-18", "options": []})
    sent = posts(monkeypatch, fail_for=(TJ,))
    monkeypatch.setattr(ha_sync, "TOKEN", root / "token")
    (root / "token").write_text("tok\n")
    with pytest.raises(SystemExit) as e:
        ha_sync.main(["push", "--week", "2026-11-20", "--notify"], root=root)
    assert [b["person"] for _, _, b in sent] == [TJ, TED] and TJ in str(e.value) and TED not in str(e.value)
    assert sent[0][1]["Authorization"] == "Bearer tok"


def test_push_one_person(root, monkeypatch):
    common.save(common.person_dir(root, TED) / "weeks" / "2026-11-18.json", {"week": "2026-11-18", "options": []})
    sent = posts(monkeypatch)
    monkeypatch.setattr(ha_sync, "TOKEN", root / "token")
    (root / "token").write_text("tok")
    ha_sync.main(["push", "--person", TED, "--week", "2026-11-18"], root=root)
    assert [b["person"] for _, _, b in sent] == [TED]


def test_push_people_leaves_out_notify_targets(root, monkeypatch):
    sent = posts(monkeypatch)
    ha_sync.push_people(root, token="tok")
    url, _, body = sent[0]
    assert url.endswith("/api/services/pyscript/meals_publish_people")
    assert body == {"people": {TJ: {"name": "TJ", "shared": False, "has_plan": True},
                               TED: {"name": "Ted", "shared": False, "has_plan": True},
                               IPAD: {"name": "iPad", "shared": True, "has_plan": False}}}
    assert "notify" not in json.dumps(body)


def test_people_json_is_accepted_by_pyscript(root):
    import test_ha_meals
    people = ha_sync.people_json(common.load_people(root))
    assert test_ha_meals.m["check_people"](people) == people


def test_ha_url_from_environment(monkeypatch):
    import importlib
    monkeypatch.setenv("HA_URL", "http://ha.test:8123/")
    try:
        assert importlib.reload(ha_sync).HA_URL == "http://ha.test:8123"
    finally:
        monkeypatch.delenv("HA_URL")
        importlib.reload(ha_sync)
