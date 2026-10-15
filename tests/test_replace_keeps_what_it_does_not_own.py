"""A Replace removes only what the sheet import wrote, and only if the re-import lands.

WHY these tests exist. The Cohort Setup tab's "Replace (delete + re-import)" button
used to:

- delete every row of the cohort in seven tables, including rows a person typed in
  through the GUI (their entered_by is a user name, not 'excel_import'). Re-reading
  the sheet cannot bring those back, so a Replace silently destroyed hand-entered
  weights, surgeries and virus preps -- for surgeries and virus preps with no
  entered_by test at all;
- delete and COMMIT on its own raw sqlite3 connection (foreign keys off) before
  re-importing, so an import that failed part way left the cohort with its sheet
  data gone and nothing, or half, put back;
- run without the cross-task database lock the scheduled imports take, so it could
  delete rows while one of them was writing.

The deletion now lives in a Qt-free function (clear_cohort_sheet_rows) and the whole
Replace in replace_cohort_from_sheet. These tests pin that behaviour on a scratch
database built with the package's own schema and Database class.

Keeping only unmarked rows is safe only if every hand edit re-marks its row. The
Ramp tab did not: it changed a sheet-imported body weight but left the import's
mark on it, so a Replace would have deleted that correction without a word. The
last test pins that the tab now re-marks a weight it changes.
"""
from datetime import date

import openpyxl
import pytest

# app.py imports Qt at module level; no window or display is created here.
pytest.importorskip("PyQt5")

from mousedb import task_mutex  # noqa: E402
from mousedb.database import Database  # noqa: E402
from mousedb.gui import app  # noqa: E402
from mousedb.schema import (  # noqa: E402
    ArchivedSummary, Cohort, LadderEntry, PelletScore, RampEntry, Subject,
    Surgery, VirusPrep, Weight,
)

MARK = "excel_import"     # what the sheet importer writes into entered_by
HAND = "labuser"          # what a GUI tab writes there: the person's user name
D1, D2, D3 = date(2026, 7, 13), date(2026, 7, 14), date(2026, 7, 15)


@pytest.fixture
def db(tmp_path):
    """Cohort CNT_05 holding, in every table a Replace touches, rows the sheet
    import wrote AND rows it did not; plus cohort CNT_04, whose rows a Replace
    of CNT_05 must never touch."""
    d = Database(tmp_path / "scratch.db")
    d.init_db()
    with d.session() as s:
        s.add(Cohort(cohort_id="CNT_05", project_code="CNT", start_date=date(2026, 7, 12)))
        s.add(Cohort(cohort_id="CNT_04", project_code="CNT", start_date=date(2025, 10, 10)))
        s.flush()
        for sid, cid in (("CNT_05_01", "CNT_05"), ("CNT_05_02", "CNT_05"),
                         ("CNT_04_01", "CNT_04")):
            s.add(Subject(subject_id=sid, cohort_id=cid))
        s.flush()

        # Weights: two from the sheet, one typed in by hand, one whose origin
        # nobody recorded.
        s.add(Weight(subject_id="CNT_05_01", date=D1, weight_grams=20.0, entered_by=MARK))
        s.add(Weight(subject_id="CNT_05_02", date=D1, weight_grams=19.0, entered_by=MARK))
        s.add(Weight(subject_id="CNT_05_01", date=D2, weight_grams=22.0, entered_by=HAND,
                     notes="Pre-surgery weight"))
        s.add(Weight(subject_id="CNT_05_02", date=D3, weight_grams=18.5, entered_by=None))
        # Pellets: a sheet tray and a hand-entered tray, two pellets each.
        for p in (1, 2):
            s.add(PelletScore(subject_id="CNT_05_01", session_date=D1, tray_type="P",
                              tray_number=1, pellet_number=p, score=2,
                              test_phase="Training", entered_by=MARK))
            s.add(PelletScore(subject_id="CNT_05_02", session_date=D2, tray_type="P",
                              tray_number=1, pellet_number=p, score=0,
                              test_phase="Training", entered_by=HAND))
        s.add(RampEntry(subject_id="CNT_05_01", date=D1, ramp_day=0,
                        body_weight_grams=21.0, entered_by=MARK))
        s.add(LadderEntry(subject_id="CNT_05_01", date=D1, steps_total=50, entered_by=MARK))
        # Surgeries: the old Replace deleted every contusion/tracing row whoever
        # entered it; a hand-entered contusion must now survive, like a perfusion.
        s.add(Surgery(subject_id="CNT_05_01", surgery_date=D2, surgery_type="contusion",
                      entered_by=MARK))
        s.add(Surgery(subject_id="CNT_05_02", surgery_date=D2, surgery_type="contusion",
                      entered_by=HAND))
        s.add(Surgery(subject_id="CNT_05_01", surgery_date=D3, surgery_type="perfusion",
                      entered_by=HAND))
        # Virus preps: the old Replace deleted every one of the cohort's.
        s.add(VirusPrep(cohort_id="CNT_05", prep_date=D1, virus_name="virus A", entered_by=MARK))
        s.add(VirusPrep(cohort_id="CNT_05", prep_date=D2, virus_name="virus B", entered_by=HAND))
        # Archived summaries have no entered_by: the importer is their only
        # writer. The second row was written by CNT_04's workbook although it
        # names a CNT_05 animal, so it belongs to CNT_04's sheet.
        s.add(ArchivedSummary(cohort_id="CNT_05", sheet_name="7_Stats", subject_id="CNT_05_01",
                              metric_name="retrieved_pct", metric_value=50.0))
        s.add(ArchivedSummary(cohort_id="CNT_04", sheet_name="7_Stats", subject_id="CNT_05_01",
                              metric_name="retrieved_pct", metric_value=40.0))
        # Another cohort's sheet rows.
        s.add(Weight(subject_id="CNT_04_01", date=D1, weight_grams=21.0, entered_by=MARK))
        s.add(Surgery(subject_id="CNT_04_01", surgery_date=D2, surgery_type="contusion",
                      entered_by=MARK))
        s.add(VirusPrep(cohort_id="CNT_04", prep_date=D1, virus_name="virus A", entered_by=MARK))
    return d


def _rows(db):
    """Every row a Replace could touch, plus the cohort start dates, as plain
    tuples -- so 'nothing changed' is one equality."""
    with db.session() as s:
        return {
            "weights": sorted((w.subject_id, w.date, w.weight_grams, w.entered_by or "")
                              for w in s.query(Weight)),
            "pellet_scores": sorted((p.subject_id, p.session_date, p.pellet_number,
                                     p.score, p.entered_by or "")
                                    for p in s.query(PelletScore)),
            "ramp_entries": sorted((r.subject_id, r.date, r.entered_by or "")
                                   for r in s.query(RampEntry)),
            "ladder_entries": sorted((r.subject_id, r.date, r.entered_by or "")
                                     for r in s.query(LadderEntry)),
            "surgeries": sorted((r.subject_id, r.surgery_date, r.surgery_type, r.entered_by or "")
                                for r in s.query(Surgery)),
            "virus_preps": sorted((v.cohort_id, v.prep_date, v.entered_by or "")
                                  for v in s.query(VirusPrep)),
            "archived_summaries": sorted((a.cohort_id, a.subject_id or "", a.metric_value)
                                         for a in s.query(ArchivedSummary)),
            "subjects": sorted(x.subject_id for x in s.query(Subject)),
            "starts": sorted((c.cohort_id, c.start_date) for c in s.query(Cohort)),
        }


class _Lock:
    """Stands in for task_mutex.hold and records when it is held."""

    def __init__(self):
        self.events = []

    def __call__(self, **kwargs):
        self.events.append("acquired")
        lock = self

        class _Held:
            def __enter__(self):
                return self

            def __exit__(self, *exc):
                lock.events.append("released")

        return _Held()


def _sheet(tmp_path, rows):
    """A minimal tracking workbook the real importer reads: the row-per-day
    weight tab, under the file name it takes the cohort from."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "1_Weight"
    ws.append(["Animal", "Date", "Weight"])
    for r in rows:
        ws.append(list(r))
    path = tmp_path / "Connectome_05_Animal_Tracking.xlsx"
    wb.save(path)
    return path


# --------------------------------------------------------------------------
# What is deleted and what is kept
# --------------------------------------------------------------------------

def test_only_rows_the_sheet_import_wrote_are_removed(db):
    with db.session() as s:
        out = app.clear_cohort_sheet_rows(s, "CNT_05")

    assert out["removed"] == {
        "weights": 2, "pellet_scores": 2, "ramp_entries": 1, "surgeries": 1,
        "ladder_entries": 1, "virus_preps": 1, "archived_summaries": 1,
    }
    rows = _rows(db)
    # Hand-entered and unknown-origin weights survive; CNT_04's is untouched.
    assert rows["weights"] == [
        ("CNT_04_01", D1, 21.0, MARK),
        ("CNT_05_01", D2, 22.0, HAND),
        ("CNT_05_02", D3, 18.5, ""),
    ]
    assert rows["pellet_scores"] == [("CNT_05_02", D2, 1, 0, HAND), ("CNT_05_02", D2, 2, 0, HAND)]
    assert rows["ramp_entries"] == [] and rows["ladder_entries"] == []
    # The hand-typed contusion survives as well as the perfusion.
    assert rows["surgeries"] == [
        ("CNT_04_01", D2, "contusion", MARK),
        ("CNT_05_01", D3, "perfusion", HAND),
        ("CNT_05_02", D2, "contusion", HAND),
    ]
    assert rows["virus_preps"] == [("CNT_04", D1, MARK), ("CNT_05", D2, HAND)]
    # Matched by the workbook that wrote it, not by the animal it names.
    assert rows["archived_summaries"] == [("CNT_04", "CNT_05_01", 40.0)]
    # Subjects and cohorts are never part of a Replace.
    assert rows["subjects"] == ["CNT_04_01", "CNT_05_01", "CNT_05_02"]


def test_kept_rows_are_listed_for_the_person_in_plain_ascii(db):
    with db.session() as s:
        kept = app.clear_cohort_sheet_rows(s, "CNT_05")["kept"]

    got = {(k["table"], k["owner"], k["date"], k["entered_by"], k["rows"]) for k in kept}
    assert got == {
        ("weights", "CNT_05_01", D2.isoformat(), HAND, 1),
        ("weights", "CNT_05_02", D3.isoformat(), None, 1),
        # One line for a hand-entered tray, not one per pellet.
        ("pellet_scores", "CNT_05_02", D2.isoformat(), HAND, 2),
        ("surgeries", "CNT_05_02", D2.isoformat(), HAND, 1),
        ("surgeries", "CNT_05_01", D3.isoformat(), HAND, 1),
        ("virus_preps", "CNT_05", D2.isoformat(), HAND, 1),
    }
    for k in kept:
        notice = app._kept_row_notice(k)
        assert notice.isascii()
        assert k["owner"] in notice and k["date"] in notice
        assert (k["entered_by"] or "no record of who entered it") in notice


def test_counting_for_the_preview_changes_nothing(db):
    before = _rows(db)
    with db.session() as s:
        would = app.clear_cohort_sheet_rows(s, "CNT_05", delete=False)
    assert _rows(db) == before

    assert sum(would["removed"].values()) == 9
    lines = app.replace_preview_lines(would)
    assert lines[0].strip().startswith("remove 9 rows")
    assert lines[-1].strip().startswith("keep 7 rows typed in by hand")
    assert all(line.isascii() for line in lines)


# --------------------------------------------------------------------------
# The whole Replace: one transaction, inside the lock
# --------------------------------------------------------------------------

def test_replace_reimports_the_sheet_and_keeps_hand_entered_rows(db, tmp_path):
    sheet = _sheet(tmp_path, [
        ("CNT_05_01", D1, 21.0),   # corrected in the sheet since the last import
        ("CNT_05_01", D2, 23.0),   # the same animal and day as the hand-entered row
        ("CNT_05_02", D1, 19.0),
    ])
    result = app.replace_cohort_from_sheet(db, sheet, "CNT_05", hold=_Lock())

    assert result["success"] and not result["errors"]
    assert result["replace"]["committed"] is True
    assert result["replace"]["kept"] == 7
    weights = [w for w in _rows(db)["weights"] if w[0].startswith("CNT_05")]
    assert weights == [
        ("CNT_05_01", D1, 21.0, MARK),     # the correction took effect
        ("CNT_05_01", D2, 22.0, HAND),     # the hand-entered value stands
        ("CNT_05_02", D1, 19.0, MARK),
        ("CNT_05_02", D3, 18.5, ""),
    ]
    # Every kept row, and every table that got fewer rows back than it lost,
    # is in the warnings a person sees, ahead of the importer's own.
    notices = [w for w in result["warnings"] if w.startswith("Replace ")]
    assert result["warnings"][:len(notices)] == notices
    assert any("CNT_05_01 on %s" % D2.isoformat() in w and HAND in w for w in notices)
    assert any(w.startswith("Replace removed 2 pellet score rows") for w in notices)


def test_replace_applies_the_start_date_in_the_same_transaction(db, tmp_path):
    sheet = _sheet(tmp_path, [("CNT_05_01", D1, 21.0)])
    result = app.replace_cohort_from_sheet(db, sheet, "CNT_05",
                                           start_date=date(2026, 7, 10), hold=_Lock())
    assert result["replace"]["start_date"] == {"old": "2026-07-12", "new": "2026-07-10"}
    assert ("CNT_05", date(2026, 7, 10)) in _rows(db)["starts"]


class _WritesThenCrashes:
    """An importer that writes and 'commits' part of the sheet, then fails --
    what an unreadable tab half way through a real workbook does."""

    def __init__(self, scoped_db):
        self.db = scoped_db

    def import_cohort_file(self, path, dry_run=False):
        with self.db.session() as s:
            s.add(Weight(subject_id="CNT_05_01", date=D3, weight_grams=20.5, entered_by=MARK))
            s.commit()  # the real importer commits its own work; here it must not land
        raise RuntimeError("sheet unreadable half way")


def test_a_reimport_that_crashes_changes_nothing(db, tmp_path):
    before = _rows(db)
    with pytest.raises(RuntimeError, match="half way"):
        app.replace_cohort_from_sheet(db, tmp_path / "Connectome_05_Animal_Tracking.xlsx",
                                      "CNT_05", start_date=date(2026, 7, 1),
                                      importer_factory=_WritesThenCrashes, hold=_Lock())
    # The deletion, the start date and the half-written import are all gone.
    assert _rows(db) == before


class _ReportsErrors(_WritesThenCrashes):
    def import_cohort_file(self, path, dry_run=False):
        with self.db.session() as s:
            s.add(Weight(subject_id="CNT_05_01", date=D3, weight_grams=20.5, entered_by=MARK))
            s.commit()
        return {"success": False, "errors": ["a tab could not be read"],
                "warnings": [], "imported": {"weights": 1}}


def test_a_reimport_that_reports_errors_is_undone_and_says_so(db, tmp_path):
    before = _rows(db)
    result = app.replace_cohort_from_sheet(db, tmp_path / "x.xlsx", "CNT_05",
                                           importer_factory=_ReportsErrors, hold=_Lock())
    assert _rows(db) == before
    assert result["success"] is False
    assert result["replace"]["committed"] is False
    assert "nothing in the database was changed" in result["errors"][0]
    assert "a tab could not be read" in result["errors"]
    # The ledger records 'imported' as rows written; none were, so it says 0,
    # and what the importer had counted is kept beside the Replace report.
    assert result["imported"] == {"weights": 0}
    assert result["replace"]["undone_import"] == {"weights": 1}


class _RollsBackThenWrites(_WritesThenCrashes):
    def import_cohort_file(self, path, dry_run=False):
        with self.db.session() as s:
            s.rollback()  # this also undoes the Replace's deletion
            s.add(Weight(subject_id="CNT_05_01", date=D3, weight_grams=20.5, entered_by=MARK))
            s.commit()
        return {"success": True, "errors": [], "warnings": [], "imported": {"weights": 1}}


def test_an_importer_that_rolls_back_its_own_work_fails_the_replace(db, tmp_path):
    before = _rows(db)
    result = app.replace_cohort_from_sheet(db, tmp_path / "x.xlsx", "CNT_05",
                                           importer_factory=_RollsBackThenWrites, hold=_Lock())
    assert _rows(db) == before
    assert result["success"] is False and result["replace"]["committed"] is False


def test_replace_refuses_a_cohort_that_is_not_in_the_database(db, tmp_path):
    before = _rows(db)
    with pytest.raises(ValueError, match="use Import instead"):
        app.replace_cohort_from_sheet(db, tmp_path / "x.xlsx", "CNT_09", hold=_Lock())
    assert _rows(db) == before


def test_replace_runs_inside_the_shared_database_task_lock(db, tmp_path, monkeypatch):
    """Without hold=..., Replace takes task_mutex.hold -- the lock the scheduled
    imports take -- and everything happens while it is held."""
    lock = _Lock()
    monkeypatch.setattr(task_mutex, "hold", lock)
    seen = {}

    class _Records(_WritesThenCrashes):
        def import_cohort_file(self, path, dry_run=False):
            seen["events_during_import"] = list(lock.events)
            return {"success": True, "errors": [], "warnings": [], "imported": {}}

    app.replace_cohort_from_sheet(db, tmp_path / "x.xlsx", "CNT_05",
                                  importer_factory=_Records)
    assert seen["events_during_import"] == ["acquired"]
    assert lock.events == ["acquired", "released"]


def test_replace_changes_nothing_while_another_task_holds_the_lock(db, tmp_path, monkeypatch):
    """A scheduled import holds the real lock file; a Replace that cannot get it
    in time gives up before touching anything."""
    other = task_mutex.TaskMutex(base_dir=tmp_path)
    assert other.try_acquire()
    # Waiting zero seconds still polls once; skip the five-second pause.
    monkeypatch.setattr(task_mutex.time, "sleep", lambda s: None)

    def hold(**kwargs):
        return task_mutex.TaskMutex(base_dir=tmp_path).acquire(
            wait_seconds=0, log=lambda *a: None, **kwargs)

    before = _rows(db)
    try:
        with pytest.raises(RuntimeError, match="could not acquire"):
            app.replace_cohort_from_sheet(db, tmp_path / "x.xlsx", "CNT_05",
                                          importer_factory=_ReportsErrors, hold=hold)
    finally:
        other.release()
    assert _rows(db) == before


# --------------------------------------------------------------------------
# A correction typed into the Ramp tab must count as typed in by hand
# --------------------------------------------------------------------------
# WHY: the Ramp tab also writes the day's body weight into the weights table.
# When the sheet import had already written that day's weight, the tab used to
# change the grams but leave entered_by as the import's mark, so the next
# Replace deleted the correction and silently put the sheet's old value back.

class _Spin:
    """Stands in for a spin box: _save_all only reads .value()."""

    def __init__(self, value):
        self._value = value

    def value(self):
        return self._value


class _NoBox:
    """Swallows the 'Saved' message box so no window is needed."""

    @staticmethod
    def information(*args, **kwargs):
        return None

    @staticmethod
    def warning(*args, **kwargs):
        return None


class _RampForm:
    """The state RampEntryTab._save_all reads, without building the widget."""

    def __init__(self, db, weights):
        self.db = db
        self.current_cohort_id = "CNT_05"
        self.current_date = D1
        self.ramp_day = 0
        self.weight_spins = {sid: _Spin(g) for sid, g in weights.items()}
        self.tray_start_spins = {sid: _Spin(0.0) for sid in weights}
        self.tray_end_spins = {sid: _Spin(0.0) for sid in weights}
        self.baseline_weights = {}

    def _load_baselines(self):
        pass

    def _load_existing_data(self):
        pass


def test_a_weight_corrected_in_the_ramp_tab_survives_a_replace(db, monkeypatch):
    monkeypatch.setattr(app, "QMessageBox", _NoBox)
    db.set_user(HAND)
    # CNT_05_01's sheet weight on D1 (20.0) is corrected; CNT_05_02's (19.0)
    # is saved again unchanged.
    form = _RampForm(db, {"CNT_05_01": 21.5, "CNT_05_02": 19.0})
    app.RampEntryTab._save_all(form)

    on_d1 = [w for w in _rows(db)["weights"] if w[1] == D1 and w[0].startswith("CNT_05")]
    assert on_d1 == [("CNT_05_01", D1, 21.5, HAND),
                     # Unchanged, so still the sheet's: a Replace may refresh it.
                     ("CNT_05_02", D1, 19.0, MARK)]

    with db.session() as s:
        app.clear_cohort_sheet_rows(s, "CNT_05")
    kept = [w for w in _rows(db)["weights"] if w[1] == D1 and w[0].startswith("CNT_05")]
    assert kept == [("CNT_05_01", D1, 21.5, HAND)]
