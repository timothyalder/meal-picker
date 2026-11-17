"""Tests for run_week.py's failure rules. Steps are stubbed and the clock is injected."""
import datetime
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))
import common  # noqa: E402
import run_week as rw  # noqa: E402

WEEK = "2026-11-18"                                    # a Wednesday
WED = datetime.datetime(2026, 11, 18, 7, 30)
THU = datetime.datetime(2026, 11, 19, 18, 0)
FRI_AM = datetime.datetime(2026, 11, 20, 8, 0)
FRI_PM = datetime.datetime(2026, 11, 20, 18, 0)
BOTH = {"aldi": True, "woolworths": True, "coles": True}     # every store read
ALDI = {"aldi": True, "woolworths": False, "coles": False}
NONE = {"aldi": False, "woolworths": False, "coles": False}
NO_COLES = {"aldi": True, "woolworths": True, "coles": False}
NO_WOOLIES = {"aldi": True, "woolworths": False, "coles": True}
TJ = "148d862cd5a04037b7f641dbc0f55c72"
TED = "b37e212b9eb2438c9fcec4e98fe55e5d"
NAT = "3fa346873d444ce3ab69087c9431f88f"
IPAD = "6f45a26dcda04f2883b018f6106049f6"


class Steps:
    """Records calls; plan writes a week file offering `four`.

    `calls` leaves out the person (most tests have TJ alone); `full` has it; `people_pushes`
    counts push-people. `plan`/`push` may be a set of people for whom that step fails."""
    def __init__(self, root, stores, four=("a", "b", "c", "d"), pull=True, plan=True, push=True):
        self.root, self.stores, self.four = root, stores, list(four)
        self.ok = {"pull": pull, "plan": plan, "push": push}
        self.calls, self.full, self.people_pushes = [], [], 0

    def works(self, step, person):
        ok = self.ok[step]
        return person not in ok if isinstance(ok, (set, frozenset)) else ok

    def pull(self):
        self.calls.append(("pull",))
        return self.ok["pull"]

    def fetch(self, week):
        self.calls.append(("fetch", week))
        return dict(self.stores)

    def push_people(self):
        self.people_pushes += 1
        return True

    def plan(self, week, allow_stale, person):
        self.calls.append(("plan", week, allow_stale))
        self.full.append(("plan", person))
        if self.works("plan", person):
            common.save(common.person_dir(self.root, person) / "weeks" / f"{week}.json",
                        {"week": week, "options": [{"slug": s} for s in self.four]})
        return self.works("plan", person)

    def push(self, week, notify, person):
        self.calls.append(("push", week, notify))
        self.full.append(("push", person, notify))
        return self.works("push", person)

    def names(self):
        return [c[0] for c in self.calls]


@pytest.fixture
def root(tmp_path):
    (tmp_path / "data").mkdir()
    (tmp_path / "people.yaml").write_text(f"{TJ}: {{name: TJ, notify: notify.mobile_app_tj}}\n")
    return tmp_path


@pytest.fixture
def house(root):
    """TJ with a phone, Ted and Nat without, and a shared iPad."""
    (root / "people.yaml").write_text(f"{TJ}: {{name: TJ, notify: notify.mobile_app_tj}}\n{TED}: {{name: Ted}}\n"
                                      f"{NAT}: {{name: Nat}}\n{IPAD}: {{name: iPad, shared: true}}\n")
    return root


def tj(root, name):
    return common.person_dir(root, TJ) / name


def go(root, steps, now, **kw):
    return rw.run(WEEK, now, root, steps, **kw)


def runs(root, person=TJ):
    """run_state.json for one person: {week: entry}."""
    return {w: e[person] for w, e in common.load(root / "data" / "run_state.json", {}).items() if person in e}


def test_fresh_publishes_with_notification_then_does_nothing(root, capsys):
    s = Steps(root, BOTH)
    assert go(root, s, WED) == rw.DONE
    assert s.calls == [("pull",), ("fetch", WEEK), ("plan", WEEK, False), ("push", WEEK, True)]
    assert runs(root)[WEEK]["published"] == "fresh" and runs(root)[WEEK]["stale"] is False
    s2 = Steps(root, BOTH)
    assert go(root, s2, THU) == rw.DONE and s2.calls == []
    out = capsys.readouterr().out.splitlines()
    assert out[0].startswith("2026-11-18 07:30:00 pull: ok") and "nothing to do" in out[-1]


def test_no_prices_waits_and_publishes_nothing(root):
    s = Steps(root, NONE)
    assert go(root, s, FRI_AM) == rw.WAITING
    assert s.names() == ["pull", "fetch"] and runs(root) == {}


def test_aldi_missing_but_woolworths_ok_still_waits(root):
    s = Steps(root, {"aldi": False, "woolworths": True, "coles": True})
    assert go(root, s, THU) == rw.WAITING and "plan" not in s.names()


def test_friday_evening_publishes_stale(root):
    s = Steps(root, NONE)
    assert go(root, s, FRI_PM) == rw.WAITING
    assert s.calls[2:] == [("plan", WEEK, True), ("push", WEEK, True)]
    assert runs(root)[WEEK]["published"] == "stale" and runs(root)[WEEK]["stale"] is True


def test_allow_stale_flag_publishes_stale_early(root):
    s = Steps(root, NONE)
    go(root, s, WED, allow_stale=True)
    assert ("plan", WEEK, True) in s.calls and runs(root)[WEEK]["published"] == "stale"


def test_later_in_the_week_counts_as_after_friday(root):
    assert rw.stale_allowed(WEEK, datetime.datetime(2026, 11, 21, 9, 0))
    assert not rw.stale_allowed(WEEK, datetime.datetime(2026, 11, 20, 17, 59))


def test_stale_published_then_fresh_republishes_once_without_notification(root):
    go(root, Steps(root, NONE), FRI_PM)
    s = Steps(root, BOTH)
    assert go(root, s, FRI_PM + datetime.timedelta(hours=1)) == rw.DONE
    assert s.calls[-1] == ("push", WEEK, False) and runs(root)[WEEK]["published"] == "fresh"


def test_stale_published_and_still_no_prices_does_not_republish(root):
    go(root, Steps(root, NONE), FRI_PM)
    s = Steps(root, NONE)
    assert go(root, s, FRI_PM) == rw.WAITING and s.names() == ["pull", "fetch"]


def test_aldi_only_publishes_straight_away_and_retries_woolworths(root):
    s = Steps(root, ALDI)
    assert go(root, s, WED) == rw.WAITING
    assert s.calls[2:] == [("plan", WEEK, False), ("push", WEEK, True)]
    assert runs(root)[WEEK]["published"] == "aldi-only"

    s = Steps(root, ALDI)                               # Woolworths still down: nothing new
    assert go(root, s, WED.replace(hour=8, minute=30)) == rw.WAITING and s.names() == ["pull", "fetch"]

    s = Steps(root, BOTH)                               # Woolworths back: republish, no notification
    assert go(root, s, WED.replace(hour=9, minute=30)) == rw.DONE
    assert s.calls[2:] == [("plan", WEEK, False), ("push", WEEK, False)]
    assert runs(root)[WEEK]["published"] == "fresh"

    s = Steps(root, BOTH)
    assert go(root, s, THU) == rw.DONE and s.calls == []


def test_republish_that_would_change_the_four_keeps_the_published_week(root):
    common.save(root / "data" / "history.json", {"weeks": [{"week": WEEK, "offered": ["a", "b", "c", "d"]}]})
    go(root, Steps(root, ALDI), WED)                       # moves TJ's history.json into his folder first
    published = tj(root, f"weeks/{WEEK}.json").read_text()
    history = tj(root, "history.json").read_text()
    assert not (root / "data" / "history.json").exists()

    def plan_and_record(week, allow_stale, person):        # plan_week rewrites history too
        Steps.plan(s, week, allow_stale, person)
        common.save(tj(root, "history.json"), {"weeks": [{"week": WEEK, "offered": s.four}]})
        return True
    s = Steps(root, BOTH, four=("a", "b", "c", "z"))
    s.plan = plan_and_record
    assert go(root, s, THU) == rw.DONE
    assert "push" not in s.names() and runs(root)[WEEK]["kept"]
    assert tj(root, f"weeks/{WEEK}.json").read_text() == published
    assert tj(root, "history.json").read_text() == history
    s = Steps(root, BOTH)
    assert go(root, s, FRI_AM) == rw.DONE and s.calls == []


def test_same_four_in_another_order_still_republishes(root):
    go(root, Steps(root, ALDI), WED)
    s = Steps(root, BOTH, four=("d", "c", "b", "a"))
    assert go(root, s, THU) == rw.DONE and s.calls[-1] == ("push", WEEK, False)


def test_plan_failure_exits_2_and_retries(root):
    s = Steps(root, BOTH, plan=False)
    assert go(root, s, WED) == rw.FAILED and "push" not in s.names() and runs(root) == {}
    s = Steps(root, BOTH)
    assert go(root, s, WED.replace(hour=8)) == rw.DONE and s.calls[-1] == ("push", WEEK, True)


def test_push_failure_exits_2_and_notifies_on_the_retry(root):
    s = Steps(root, BOTH, push=False)
    assert go(root, s, WED) == rw.FAILED and runs(root) == {}
    s = Steps(root, BOTH)
    go(root, s, WED.replace(hour=8))
    assert s.calls[-1] == ("push", WEEK, True)


def test_failed_republish_restores_the_published_week(root):
    go(root, Steps(root, ALDI), WED)
    published = tj(root, f"weeks/{WEEK}.json").read_text()
    s = Steps(root, BOTH, push=False)
    assert go(root, s, THU) == rw.FAILED
    assert tj(root, f"weeks/{WEEK}.json").read_text() == published
    assert runs(root)[WEEK]["published"] == "aldi-only"


def test_failed_pull_carries_on(root, capsys):
    s = Steps(root, BOTH, pull=False)
    assert go(root, s, WED) == rw.DONE and s.names() == ["pull", "fetch", "plan", "push"]
    assert "pull: failed" in capsys.readouterr().out


def test_dry_run_runs_nothing(root, capsys):
    s = Steps(root, BOTH)
    assert go(root, s, FRI_PM, dry_run=True) == rw.DONE and s.calls == []
    out = capsys.readouterr().out
    assert "would run" in out and "--allow-stale" in out and not (root / "data" / "run_state.json").exists()


def test_weeks_are_kept_apart(root):
    go(root, Steps(root, BOTH), WED)
    s = Steps(root, BOTH)
    nxt = datetime.datetime(2026, 11, 25, 7, 30)
    assert rw.main([], now=nxt, steps=s, root=root) == rw.DONE
    assert ("push", "2026-11-25", True) in s.calls and set(runs(root)) == {WEEK, "2026-11-25"}


def test_main_week_flag(root):
    s = Steps(root, NONE)
    assert rw.main(["--week", "2026-11-20"], now=WED, steps=s, root=root) == rw.WAITING
    assert ("fetch", WEEK) in s.calls


def test_real_steps_build_the_right_commands(root, monkeypatch):
    ran = []
    monkeypatch.setattr(rw, "script", lambda name, *args: ran.append((name, *args)) or True)
    monkeypatch.setattr(common, "PRICES", root)
    common.save(root / f"{WEEK}.json", {"week": WEEK, "stores": {"aldi": {"ok": True}, "woolworths": {"ok": False}}})
    steps = rw.Steps()
    assert steps.fetch(WEEK) == ALDI
    steps.pull(), steps.plan(WEEK, True, TJ), steps.push(WEEK, True, TJ), steps.push(WEEK, False, TED)
    steps.push_people()
    assert ran == [("fetch_prices.py", "--week", WEEK), ("ha_sync.py", "pull"),
                   ("plan_week.py", "--week", WEEK, "--person", TJ, "--allow-stale"),
                   ("ha_sync.py", "push", "--person", TJ, "--week", WEEK, "--notify"),
                   ("ha_sync.py", "push", "--person", TED, "--week", WEEK), ("ha_sync.py", "push-people")]
    assert rw.Steps().fetch("2026-11-25") == NONE  # no snapshot at all


# --- three stores: Woolworths and Coles both come from hotprices -----------

@pytest.mark.parametrize("stores,mode", [
    (BOTH, "fresh"), (NO_COLES, "partial"), (NO_WOOLIES, "partial"), (ALDI, "aldi-only"),
    ({"aldi": True, "woolworths": True}, "partial"),         # a store not reported counts as not read
    ({"aldi": False, "woolworths": True, "coles": True}, None)])
def test_mode_of_three_stores(stores, mode):
    assert rw.mode_of(stores, False) == mode


def test_coles_missing_publishes_then_republishes_without_notification(root):
    s = Steps(root, NO_COLES)
    assert go(root, s, WED) == rw.WAITING
    assert s.calls[2:] == [("plan", WEEK, False), ("push", WEEK, True)]
    assert runs(root)[WEEK]["published"] == "partial" and runs(root)[WEEK]["stores_ok"] == NO_COLES

    s = Steps(root, NO_COLES)                           # Coles still down: nothing new
    assert go(root, s, WED.replace(hour=8, minute=30)) == rw.WAITING and s.names() == ["pull", "fetch"]
    s = Steps(root, NO_WOOLIES)                         # the other one down instead: no better
    assert go(root, s, WED.replace(hour=9, minute=30)) == rw.WAITING and "push" not in s.names()

    s = Steps(root, BOTH)                               # Coles back: republish, no notification
    assert go(root, s, THU) == rw.DONE
    assert s.calls[2:] == [("plan", WEEK, False), ("push", WEEK, False)]
    assert runs(root)[WEEK]["published"] == "fresh"
    s = Steps(root, BOTH)
    assert go(root, s, FRI_AM) == rw.DONE and s.calls == []


def test_both_hotprices_stores_missing_then_one_then_both(root):
    assert go(root, Steps(root, ALDI), WED) == rw.WAITING
    s = Steps(root, NO_COLES)                           # Woolworths back first: republish quietly
    assert go(root, s, WED.replace(hour=9)) == rw.WAITING and s.calls[-1] == ("push", WEEK, False)
    assert runs(root)[WEEK]["published"] == "partial"
    s = Steps(root, BOTH)
    assert go(root, s, THU) == rw.DONE and s.calls[-1] == ("push", WEEK, False)


def test_real_fetch_reports_coles(root, monkeypatch):
    monkeypatch.setattr(rw, "script", lambda name, *args: True)
    monkeypatch.setattr(common, "PRICES", root)
    common.save(root / f"{WEEK}.json", {"week": WEEK, "stores": {"aldi": {"ok": True}, "woolworths": {"ok": True},
                                                                 "coles": {"ok": False, "products": []}}})
    assert rw.Steps().fetch(WEEK) == NO_COLES


# --- per person -----------------------------------------------------------

def test_everyone_planned_and_pushed_but_only_phones_notified(house):
    s = Steps(house, BOTH)
    assert go(house, s, WED) == rw.DONE
    assert s.full == [("plan", TJ), ("push", TJ, True), ("plan", TED), ("push", TED, False),
                      ("plan", NAT), ("push", NAT, False)]
    assert s.people_pushes == 1 and s.names()[:2] == ["pull", "fetch"]
    assert {p: runs(house, p)[WEEK]["published"] for p in (TJ, TED, NAT)} == dict.fromkeys((TJ, TED, NAT), "fresh")
    assert IPAD not in common.load(house / "data" / "run_state.json")[WEEK]
    s = Steps(house, BOTH)
    assert go(house, s, THU) == rw.DONE and s.calls == [] and s.people_pushes == 0


def test_one_person_failing_does_not_stop_the_others(house):
    s = Steps(house, BOTH, plan={TED})
    assert go(house, s, WED) == rw.FAILED
    assert ("push", TJ, True) in s.full and ("push", NAT, False) in s.full and ("push", TED, False) not in s.full
    assert WEEK in runs(house, TJ) and WEEK in runs(house, NAT) and runs(house, TED) == {}
    s = Steps(house, BOTH)                                # the retry does Ted alone
    assert go(house, s, WED.replace(hour=8, minute=30)) == rw.DONE
    assert s.full == [("plan", TED), ("push", TED, False)]


def test_push_failing_for_one_person(house):
    s = Steps(house, ALDI, push={TJ})
    assert go(house, s, WED) == rw.FAILED
    assert runs(house, TJ) == {} and runs(house, TED)[WEEK]["published"] == "aldi-only"
    s = Steps(house, ALDI)                                # TJ still gets his first notification
    assert go(house, s, WED.replace(hour=9)) == rw.WAITING
    assert s.full == [("plan", TJ), ("push", TJ, True)]


def test_new_person_is_planned_after_the_others_are_done(root):
    go(root, Steps(root, BOTH), WED)
    (root / "people.yaml").write_text(f"{TJ}: {{name: TJ, notify: notify.mobile_app_tj}}\n{TED}: {{name: Ted}}\n")
    s = Steps(root, BOTH)
    assert go(root, s, THU) == rw.DONE and s.full == [("plan", TED), ("push", TED, False)]


def test_old_run_state_becomes_tjs(root):
    common.save(root / "data" / "run_state.json", {WEEK: {"published": "aldi-only", "stale": False, "stores_ok": ALDI,
                                                          "at": "x", "first": "x"}})
    s = Steps(root, ALDI)
    assert go(root, s, WED) == rw.WAITING and s.calls[2:] == []     # nothing better than what TJ has
    assert common.load(root / "data" / "run_state.json") == {WEEK: {TJ: {"published": "aldi-only", "stale": False,
                                                                         "stores_ok": ALDI, "at": "x", "first": "x"}}}
    assert rw.migrate_runs(common.load(root / "data" / "run_state.json"), TJ) == common.load(root / "data" / "run_state.json")


def test_dry_run_moves_nothing(root, capsys):
    common.save(root / "data" / "history.json", {"weeks": []})
    assert go(root, Steps(root, BOTH), WED, dry_run=True) == rw.DONE
    assert (root / "data" / "history.json").exists() and not (root / "data" / "people").exists()
    assert "would first move TJ's files: data/history.json" in capsys.readouterr().out


def test_no_people_fails(root):
    (root / "people.yaml").unlink()
    assert go(root, Steps(root, BOTH), WED) == rw.FAILED
