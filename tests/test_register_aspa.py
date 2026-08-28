"""Registering ASPA animals must not invent them.

ASPA is a project like CNT, and its videos run the same pipeline -- but no ASPA
cohort or subject exists in the database, so the sync skips every ASPA video: it
will not write reach data for an animal it has never heard of.

Registering them from the frozen ASPA workbooks is not "read the sheet". Those
workbooks are hand-kept and not uniform:

  - `I.xlsx` carries 40 H-cohort animals in its *Weights* and *balance* tabs,
    identical to `H.xlsx`, left over from copying that workbook. The real I
    animals are in *"Ramp" Training Data*. Reading a fixed sheet by name would
    have registered forty H animals as cohort I.
  - `H.xlsx` likewise carries leftover G data in *Sheet1* and *Group
    distribution*.
  - The earlier cohorts write the animal id itself as `OptD12`, not `D12`.

So the rule is: ids matching THIS cohort's own letter, anywhere in its workbook,
cross-checked against the videos that exist. These tests pin that rule against
each of those three shapes.
"""

import pytest

openpyxl = pytest.importorskip("openpyxl")

from mousedb.cohort_tools.register_aspa import animals_in_sheet


def _workbook(tmp_path, name, sheets):
    """sheets: {sheet_name: [list of cell strings]}"""
    wb = openpyxl.Workbook()
    wb.remove(wb.active)
    for sheet_name, values in sheets.items():
        ws = wb.create_sheet(sheet_name[:31])
        for i, v in enumerate(values, start=1):
            ws.cell(row=i, column=1, value=v)
    path = tmp_path / name
    wb.save(path)
    return path


class TestOnlyThisCohortsAnimals:

    def test_leftover_animals_from_another_cohort_are_ignored(self, tmp_path):
        """The I.xlsx case: 40 H animals sitting in the I workbook."""
        p = _workbook(tmp_path, "I.xlsx", {
            "Weights": ["An#"] + ["H%d" % n for n in range(1, 41)],
            "Ramp Training Data": ["An#"] + ["I%d" % n for n in range(1, 19)],
        })
        found = animals_in_sheet(p, "I")
        assert found == set(range(1, 19)), "must not pick up the H animals"

    def test_the_other_cohort_reads_its_own(self, tmp_path):
        p = _workbook(tmp_path, "H.xlsx", {
            "Weights": ["An#"] + ["H%d" % n for n in range(1, 41)],
            "Sheet1": ["G%d" % n for n in range(1, 41)],     # leftover G data
        })
        assert animals_in_sheet(p, "H") == set(range(1, 41))
        assert animals_in_sheet(p, "G") == set(range(1, 41))


class TestIdShapes:

    def test_bare_and_zero_padded(self, tmp_path):
        p = _workbook(tmp_path, "J.xlsx", {"Weights": ["J1", "J02", "J11"]})
        assert animals_in_sheet(p, "J") == {1, 2, 11}

    def test_the_surgery_sheet_hyphen_form(self, tmp_path):
        """Surgery tabs write 'J-6'."""
        p = _workbook(tmp_path, "J.xlsx", {"Surgery information": ["J-6", "J-7"]})
        assert animals_in_sheet(p, "J") == {6, 7}

    def test_the_opt_prefixed_form(self, tmp_path):
        """D, E, F and G write the animal as OptD12. Without this they yield none."""
        p = _workbook(tmp_path, "OptD.xlsx", {"Weights": ["OptD1", "OptD12"]})
        assert animals_in_sheet(p, "D") == {1, 12}

    def test_a_gap_in_the_numbering_survives(self, tmp_path):
        """J really has no animal 12; the list must not be filled in."""
        p = _workbook(tmp_path, "J.xlsx",
                      {"Weights": ["J%d" % n for n in [1, 2, 3, 11, 13, 14]]})
        assert 12 not in animals_in_sheet(p, "J")


class TestItDoesNotInventAnimals:

    def test_unrelated_text_is_not_an_animal(self, tmp_path):
        p = _workbook(tmp_path, "J.xlsx", {
            "Weights": ["An#", "week 3", "Jan 5", "", None, "Journal", "J1"],
        })
        assert animals_in_sheet(p, "J") == {1}

    def test_out_of_range_numbers_are_rejected(self, tmp_path):
        p = _workbook(tmp_path, "J.xlsx", {"Weights": ["J0", "J1", "J99"]})
        found = animals_in_sheet(p, "J")
        assert 0 not in found and 1 in found

    def test_an_empty_workbook_yields_nothing(self, tmp_path):
        p = _workbook(tmp_path, "J.xlsx", {"Weights": ["An#", "", None]})
        assert animals_in_sheet(p, "J") == set()


class TestThePlanIsReadOnly:

    def test_register_writes_nothing_without_apply(self, monkeypatch):
        """Dry run is the default, and it must really be a dry run."""
        import mousedb.cohort_tools.register_aspa as ra

        added = []

        class FakeSession:
            def query(self, *a, **k):
                return self
            def filter_by(self, **k):
                return self
            def first(self):
                return None
            def add(self, obj):
                added.append(obj)
            def flush(self):
                pass
            def commit(self):
                added.append("COMMIT")
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        class FakeDB:
            def session(self):
                return FakeSession()

        import mousedb.database as dbmod
        monkeypatch.setattr(dbmod, "init_database", lambda *a, **k: FakeDB())

        entries = [{"letter": "J", "cohort_id": "ASPA_10", "sheet": "J.xlsx",
                    "in_sheet": [1, 2], "with_video": [1],
                    "video_only": [], "register": [1, 2],
                    "start_date": "2022-08-11"}]
        counts = ra.register(entries, apply=False)

        assert added == [], "a dry run must add nothing and commit nothing"
        assert counts["subjects"] == 2, "but it still reports what it would do"


class TestStartDate:
    """cohorts.start_date is NOT NULL; the first real --apply crashed on it
    (2026-08-28) because the tool never supplied one."""

    def _fake_db(self, monkeypatch, added):
        class FakeSession:
            def query(self, *a, **k):
                return self
            def filter_by(self, **k):
                return self
            def first(self):
                return None
            def add(self, obj):
                added.append(obj)
            def flush(self):
                pass
            def commit(self):
                added.append("COMMIT")
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        class FakeDB:
            def session(self):
                return FakeSession()

        import mousedb.database as dbmod
        monkeypatch.setattr(dbmod, "init_database", lambda *a, **k: FakeDB())

    def test_apply_writes_the_cohort_with_its_start_date(self, monkeypatch):
        import mousedb.cohort_tools.register_aspa as ra
        from mousedb.schema import Cohort
        added = []
        self._fake_db(monkeypatch, added)
        entries = [{"letter": "J", "cohort_id": "ASPA_10", "sheet": "J.xlsx",
                    "in_sheet": [1], "with_video": [1], "video_only": [],
                    "register": [1], "start_date": "2022-08-11"}]
        ra.register(entries, apply=True)
        cohorts = [o for o in added if isinstance(o, Cohort)]
        assert len(cohorts) == 1
        assert str(cohorts[0].start_date) == "2022-08-11"

    def test_a_cohort_with_no_start_date_is_skipped_not_invented(self, monkeypatch):
        import mousedb.cohort_tools.register_aspa as ra
        from mousedb.schema import Cohort, Subject
        added = []
        self._fake_db(monkeypatch, added)
        entries = [{"letter": "A", "cohort_id": "ASPA_01", "sheet": "OptA.xlsx",
                    "in_sheet": [1, 2], "with_video": [], "video_only": [],
                    "register": [1, 2], "start_date": None}]
        counts = ra.register(entries, apply=True)
        assert counts.get("cohorts_no_start_date") == 1
        assert not [o for o in added if isinstance(o, (Cohort, Subject))]

    def test_plan_derives_start_date_from_the_earliest_video(self, monkeypatch, tmp_path):
        import mousedb.cohort_tools.register_aspa as ra
        monkeypatch.setattr(ra, "videos_for",
                            lambda L: {11: ["20220815", "20220811"], 3: ["20220817"]})
        monkeypatch.setattr(ra, "animals_in_sheet", lambda p, L: {3, 11})
        import mousedb.cohort_sheets as cs
        monkeypatch.setattr(cs, "find_aspa_sheet", lambda L: tmp_path / "J.xlsx")
        monkeypatch.setattr(cs, "aspa_cohort_number", lambda L: "10")
        (tmp_path / "J.xlsx").write_bytes(b"")
        e = ra.plan(["J"])[0]
        assert e["start_date"] == "2022-08-11"
        assert e["register"] == [3, 11]
