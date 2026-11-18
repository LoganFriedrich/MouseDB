"""The tracking sheet is the source of record for the rows the sheet import wrote.

WHY: the import used to only add. A value corrected in the sheet kept its old
value in the database, and a session typed into the wrong cohort's workbook,
imported, then moved to the right workbook stayed behind as a phantom session
no sheet holds. A cohort's phases are derived from its set of session dates,
so one phantom date relabelled the cohort's real sessions in the database and
in every export. Likewise a workbook made by copying the previous cohort's
left that cohort's weights and surgeries filed under the new cohort's animals
for good once it had been imported.

Now each import makes the rows IT wrote match the sheet (changed values
changed, rows gone from the sheet removed), never touches a row typed in by
hand, re-derives the phases when the cohort's dates change, and refuses to
remove anything when the sheet reads as broken.
"""
import json
from datetime import date, timedelta

import openpyxl
import pytest
from sqlalchemy import text

from mousedb import sheet_sync
from mousedb.database import init_database
from mousedb.importers import ExcelImporter
from mousedb.schema import (AuditLog, Cohort, PelletScore, Project, RampEntry, Subject,
                            Surgery, Weight)

COHORT = "PROJA_01"
ANIMALS = ("PROJA_01_01", "PROJA_01_02")
# Ten pillar days, then two later tests. With one more date inside the gap
# (PHANTOM) the last three pillar days shift, so BASELINE_DAY reads as
# training instead of baseline.
DAYS = [d for d in (date(2026, 3, 2) + timedelta(days=i) for i in range(12)) if d.weekday() < 5]
DAYS += [date(2026, 3, 30), date(2026, 4, 6)]
PHANTOM = date(2026, 3, 19)
BASELINE_DAY = date(2026, 3, 11)


@pytest.fixture(autouse=True)
def _project_a(tmp_path_factory, monkeypatch):
    """This file's own lab settings: one project, PROJA, whose sheets are
    named ProjectA_NN_Tracking.xlsx."""
    f = tmp_path_factory.mktemp("lab") / "lab.json"
    f.write_text(json.dumps({"sheet_projects": {
        "PROJA": {"sheet_name": "ProjectA_{NN}_Tracking.xlsx"}}}), encoding="utf-8")
    monkeypatch.setenv("MOUSEDB_LAB_CONFIG", str(f))
    # The audit log's change files go beside the test database, never into
    # the configured (real) log folder.
    from mousedb import database as dbmod
    monkeypatch.setattr(dbmod, "DEFAULT_LOG_PATH", None)


def _scores(day, animal, tray=1):
    """A tray's 20 scores, different per day/animal/tray so a change shows."""
    return [(day.day + int(animal[-1]) + tray + p) % 3 for p in range(1, 21)]


def _workbook(path, days=DAYS, trays=(1,), score_edit=None, weights=True, surgeries=None,
              tray_tab="3b_Manual_Tray", ramp=None, unreadable=None):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = tray_tab
    ws.append(["Date", "Animal", "Tray Type/Number"] + list(range(1, 21)))
    for d in days:
        for a in ANIMALS:
            for t in trays:
                sc = _scores(d, a, t)
                if score_edit and (d, a, t) in score_edit:
                    pellet, value = score_edit[(d, a, t)]
                    sc[pellet - 1] = value
                if unreadable and (d, a, t) == unreadable[0]:
                    sc[unreadable[1] - 1] = "x"
                ws.append([d, a, "P%d" % t] + sc)
    w = wb.create_sheet("3d_Weights")
    w.append(["Date", "Animal", "Weight_g"])
    if weights:
        for d in days[:3]:
            for i, a in enumerate(ANIMALS):
                w.append([d, a, 20.0 + i])
    s = wb.create_sheet("4_Contusion_Injury_Details")
    s.append(["Subject_ID", "Surgery_Date", "Surgery_Type", "Subject_Weight (g)",
              "Actual_kd", "Survived"])
    for row in (surgeries if surgeries is not None else
                [(a, date(2026, 3, 20), 21.0, 60 + i) for i, a in enumerate(ANIMALS)]):
        a, d, grams, kd = row
        s.append([a, d, "Contusion", grams, kd, "Y"])
    if ramp:
        r = wb.create_sheet("3a_Manual_Ramp")
        food = len(ramp[0]) > 3
        r.append(["Animal", "Date", "Weight"] + (["Food_Start_(g)", "Food_End_(g)"] if food else []))
        for row in ramp:
            r.append(list(row))
    wb.save(path)
    return path


def _db(tmp_path):
    db = init_database(tmp_path / "scratch.db")
    assert db.log_path == tmp_path / "logs"
    with db.session() as s:
        if not s.query(Project).filter_by(project_code="PROJA").first():
            s.add(Project(project_code="PROJA", project_name="PROJA"))
        s.add(Cohort(cohort_id=COHORT, project_code="PROJA", start_date=DAYS[0]))
        for a in ANIMALS:
            s.add(Subject(subject_id=a, cohort_id=COHORT))
    return db


def _import(db, path, dry_run=False, allow=False):
    imp = ExcelImporter(db)
    imp.allow_large_removal = allow
    return imp.import_cohort_file(path, dry_run=dry_run)


def _sheet(tmp_path):
    return tmp_path / "ProjectA_01_Tracking.xlsx"


def _pellets(db, **kw):
    with db.session() as s:
        return {(p.subject_id, p.session_date, p.tray_number, p.pellet_number): p.score
                for p in s.query(PelletScore).filter_by(**kw)}


def _add_reach(db, day):
    with db.engine.begin() as c:
        c.execute(text(
            "INSERT INTO reach_data (subject_id, video_name, session_date, segment_num, "
            "reach_id, reach_num, causal_reach, is_first_reach, is_last_reach, "
            "n_reaches_in_segment, start_frame, end_frame, duration_frames, source_file, "
            "flagged_for_review) VALUES (:s, :v, :d, 1, 1, 1, 0, 1, 1, 1, 10, 20, 10, 'f', 0)"),
            {"s": ANIMALS[0], "v": "%s_PROJA0101_P1" % day.strftime("%Y%m%d"), "d": day.isoformat()})


def _reach_phase(db, day):
    with db.engine.begin() as c:
        return c.execute(text("SELECT test_phase FROM reach_data WHERE session_date = :d"),
                         {"d": day.isoformat()}).scalar()


# --------------------------------------------------------------------------
# a value changed in the sheet
# --------------------------------------------------------------------------

def test_a_score_changed_in_the_sheet_is_changed_in_the_database(tmp_path):
    db = _db(tmp_path)
    _import(db, _workbook(_sheet(tmp_path)))
    d, a = DAYS[2], ANIMALS[1]
    old = _scores(d, a)[4]
    new = (old + 1) % 3

    r = _import(db, _workbook(_sheet(tmp_path), score_edit={(d, a, 1): (5, new)}))

    assert _pellets(db)[(a, d, 1, 5)] == new
    change = r["sheet_sync"]["pellet_scores"]["changed"]
    assert change == [{"subject_id": a, "date": d.isoformat(), "tray": "P1", "pellet": 5,
                       "old": old, "new": new}]
    assert r["imported"]["pellet_scores_changed"] == 1
    with db.session() as s:
        audit = s.query(AuditLog).filter_by(table_name="pellet_scores", action="UPDATE").all()
        assert len(audit) == 1 and json.loads(audit[0].new_values) == {"score": new}


def test_a_weight_and_a_surgery_changed_in_the_sheet_are_changed(tmp_path):
    db = _db(tmp_path)
    _import(db, _workbook(_sheet(tmp_path)))
    p = _workbook(_sheet(tmp_path), surgeries=[(ANIMALS[0], date(2026, 3, 20), 21.0, 75),
                                               (ANIMALS[1], date(2026, 3, 20), 21.0, 61)])
    wb = openpyxl.load_workbook(p)
    wb["3d_Weights"]["C2"] = 19.5          # first animal, first day
    wb.save(p)

    r = _import(db, p)

    with db.session() as s:
        assert s.query(Weight).filter_by(subject_id=ANIMALS[0], date=DAYS[0]).one().weight_grams == 19.5
        assert s.query(Surgery).filter_by(subject_id=ANIMALS[0]).one().force_kdyn == 75
    assert r["sheet_sync"]["weights"]["changed"][0]["values"] == {
        "weight_grams": {"old": 20.0, "new": 19.5}}
    assert r["sheet_sync"]["surgeries"]["changed"][0]["values"] == {
        "force_kdyn": {"old": 60.0, "new": 75.0}}


# --------------------------------------------------------------------------
# a session no longer in the sheet
# --------------------------------------------------------------------------

def test_a_session_moved_out_of_the_sheet_is_removed_and_the_phases_rederived(tmp_path):
    db = _db(tmp_path)
    # The phantom session was imported once (typed into this cohort's sheet).
    _import(db, _workbook(_sheet(tmp_path), days=sorted(DAYS + [PHANTOM])))
    _add_reach(db, BASELINE_DAY)
    from mousedb.backfill import backfill_phases
    backfill_phases(db, cohort_id=COHORT)
    assert _reach_phase(db, BASELINE_DAY) == "Pillar_Training"   # the damage

    # ... then moved out of it.
    r = _import(db, _workbook(_sheet(tmp_path)))

    assert not _pellets(db, session_date=PHANTOM)
    gone = r["sheet_sync"]["pellet_scores"]["removed_trays"]
    assert [(g["subject_id"], g["date"], g["tray"], g["pellets"]) for g in gone] == [
        (a, PHANTOM.isoformat(), "P1", 20) for a in ANIMALS]
    assert r["sheet_sync"]["pellet_scores"]["dates_removed"] == [PHANTOM.isoformat()]
    assert r["imported"]["pellet_scores_removed"] == 40
    # The removal changed the cohort's dates, so its phases were re-derived in
    # the same run -- reaches included -- and the result says so.
    assert _reach_phase(db, BASELINE_DAY) == "Pillar"
    with db.session() as s:
        assert {p.test_phase for p in s.query(PelletScore).filter_by(session_date=BASELINE_DAY)} == {"Pillar"}
    ph = r["phases_rederived"]
    assert ph["cohort_id"] == COHORT and ph["reach_rows_relabelled"] == 1 and "error" not in ph
    # BASELINE_DAY's 40 hand scores moved from training to baseline.
    assert ph["pellet_rows_relabelled"] == 40
    # Every removed row is in the audit log with its old values.
    with db.session() as s:
        dels = s.query(AuditLog).filter_by(table_name="pellet_scores", action="DELETE").all()
        assert len(dels) == 40
        assert json.loads(dels[0].old_values)["session_date"] == PHANTOM.isoformat()


def test_weights_and_surgeries_copied_from_another_workbook_are_removed(tmp_path):
    """A workbook made by copying an earlier cohort's still held that cohort's
    weights and surgery when it was first imported; once the sheet holds its
    own, the copied rows go."""
    db = _db(tmp_path)
    copied_days = [date(2026, 1, 5), date(2026, 1, 6)]
    first = _workbook(_sheet(tmp_path), surgeries=[(a, date(2026, 1, 20), 20.0, 70) for a in ANIMALS])
    wb = openpyxl.load_workbook(first)
    for d in copied_days:
        for a in ANIMALS:
            wb["3d_Weights"].append([d, a, 18.0])
    wb.save(first)
    _import(db, first)

    r = _import(db, _workbook(_sheet(tmp_path)), allow=True)

    with db.session() as s:
        assert not s.query(Weight).filter(Weight.date.in_(copied_days)).count()
        assert {x.surgery_date for x in s.query(Surgery)} == {date(2026, 3, 20)}
    assert len(r["sheet_sync"]["weights"]["removed"]) == 4
    assert len(r["sheet_sync"]["surgeries"]["removed"]) == 2


def test_an_emptied_cell_is_removed_but_an_unreadable_one_is_kept(tmp_path):
    db = _db(tmp_path)
    _import(db, _workbook(_sheet(tmp_path)))
    d, a = DAYS[1], ANIMALS[0]
    p = _workbook(_sheet(tmp_path), unreadable=((d, a, 1), 3))
    wb = openpyxl.load_workbook(p)
    ws = wb["3b_Manual_Tray"]
    for row in ws.iter_rows(min_row=2):
        if row[0].value.date() == DAYS[4] and row[1].value == a:
            row[3 + 6].value = None         # pellet 7 emptied
    wb.save(p)

    r = _import(db, p)

    stored = _pellets(db)
    assert (a, d, 1, 3) in stored                     # unreadable: kept
    assert (a, DAYS[4], 1, 7) not in stored           # empty: removed
    assert [c["pellet"] for c in r["sheet_sync"]["pellet_scores"]["removed_cells"]] == [7]


# --------------------------------------------------------------------------
# rows typed in by hand
# --------------------------------------------------------------------------

def test_rows_typed_in_by_hand_are_never_touched(tmp_path):
    db = _db(tmp_path)
    _import(db, _workbook(_sheet(tmp_path)))
    hand_day = date(2026, 3, 25)
    with db.session() as s:
        for p in range(1, 21):
            s.add(PelletScore(subject_id=ANIMALS[0], session_date=hand_day, tray_type="P",
                              tray_number=1, pellet_number=p, score=2, test_phase="x",
                              entered_by="a_reviewer"))
        s.add(Weight(subject_id=ANIMALS[0], date=hand_day, weight_grams=22.0, entered_by="a_reviewer"))
        s.add(Surgery(subject_id=ANIMALS[0], surgery_date=hand_day, surgery_type="perfusion",
                      entered_by="a_reviewer"))
        # A hand-typed value on a day the sheet also has: the hand value stands.
        hand_value = (_scores(DAYS[0], ANIMALS[1])[0] + 1) % 3
        s.query(PelletScore).filter_by(subject_id=ANIMALS[1], session_date=DAYS[0],
                                       pellet_number=1).update({"entered_by": "a_reviewer",
                                                                "score": hand_value})

    r = _import(db, _workbook(_sheet(tmp_path)))

    with db.session() as s:
        assert s.query(PelletScore).filter_by(session_date=hand_day).count() == 20
        assert s.query(Weight).filter_by(date=hand_day).count() == 1
        assert s.query(Surgery).filter_by(surgery_type="perfusion").count() == 1
    assert _pellets(db)[(ANIMALS[1], DAYS[0], 1, 1)] == hand_value
    assert "pellet_scores" not in (r.get("sheet_sync") or {})
    assert any("typed in by hand (by a_reviewer)" in w for w in r["warnings"])


# --------------------------------------------------------------------------
# a broken read removes nothing
# --------------------------------------------------------------------------

def test_a_missing_tray_tab_removes_nothing_and_says_so(tmp_path):
    db = _db(tmp_path)
    _import(db, _workbook(_sheet(tmp_path)))
    before = _pellets(db)

    r = _import(db, _workbook(_sheet(tmp_path), tray_tab="3b_Manual_Tray_old"))

    assert _pellets(db) == before
    refused = r["sheet_sync"]["refused"]
    assert len(refused) == 1 and refused[0].startswith("NOT SYNCED pellet_scores")
    assert r["warnings"][0] == refused[0]


def test_a_sheet_that_lost_most_of_its_rows_removes_nothing(tmp_path):
    db = _db(tmp_path)
    _import(db, _workbook(_sheet(tmp_path)))
    before = _pellets(db)

    # Only the first three days left: a file caught mid-save looks like this.
    r = _import(db, _workbook(_sheet(tmp_path), days=DAYS[:3]))

    assert _pellets(db) == before
    assert any("would remove 18 of the 24 trays" in x for x in r["sheet_sync"]["refused"])
    assert "removed_trays" not in (r["sheet_sync"].get("pellet_scores") or {})

    # Checked by a person and really meant: allowed for this one import.
    r = _import(db, _workbook(_sheet(tmp_path), days=DAYS[:3]), allow=True)
    assert {k[1] for k in _pellets(db)} == set(DAYS[:3])
    assert "refused" not in r["sheet_sync"]


def test_an_empty_weights_tab_removes_no_weight(tmp_path):
    db = _db(tmp_path)
    _import(db, _workbook(_sheet(tmp_path)))
    r = _import(db, _workbook(_sheet(tmp_path), weights=False), allow=True)
    with db.session() as s:
        assert s.query(Weight).count() == 6
    assert any(x.startswith("NOT SYNCED weights") for x in r["sheet_sync"]["refused"])


def test_a_refusal_fails_the_scheduled_import_and_is_listed_for_a_person(tmp_path, monkeypatch):
    db = _db(tmp_path)
    folder = tmp_path / "sheets"
    folder.mkdir()
    _import(db, _workbook(folder / "ProjectA_01_Tracking.xlsx"))
    _workbook(folder / "ProjectA_01_Tracking.xlsx", days=DAYS[:3])
    monkeypatch.setattr(sheet_sync, "LEDGER", tmp_path / "ledger.jsonl")
    import mousedb.importers as importers
    monkeypatch.setattr(importers, "get_db", lambda: db)
    monkeypatch.setattr("mousedb.task_mutex.hold", _no_lock)
    monkeypatch.setattr(sheet_sync, "_animal_records", lambda *a, **k: None)

    r = sheet_sync.import_cohorts(sheets_dir=folder)

    assert not sheet_sync.import_succeeded(r)
    line = json.loads((tmp_path / "ledger.jsonl").read_text().splitlines()[-1])
    assert line["sheet_sync"]["refused"]
    lines = []
    sheet_sync.print_import_result(r, log=lines.append)
    assert "NOT SYNCED with the sheet" in lines[-1]
    from mousedb import attention
    items = attention.sheet_items([COHORT])
    assert any(i["says"].startswith("NOT SYNCED pellet_scores") for i in items)


def _no_lock(*a, **k):
    from contextlib import nullcontext
    return nullcontext()


# --------------------------------------------------------------------------
# dry run, notes, ledger
# --------------------------------------------------------------------------

def test_a_dry_run_reports_what_would_change_and_writes_nothing(tmp_path):
    db = _db(tmp_path)
    _import(db, _workbook(_sheet(tmp_path), days=sorted(DAYS + [PHANTOM])))
    before = _pellets(db)

    r = _import(db, _workbook(_sheet(tmp_path)), dry_run=True)

    assert _pellets(db) == before
    assert len(r["sheet_sync"]["pellet_scores"]["removed_trays"]) == 2
    with db.session() as s:
        assert not s.query(AuditLog).filter_by(action="DELETE").count()


def test_an_animal_first_seen_in_a_video_loses_its_placeholder_note(tmp_path):
    db = _db(tmp_path)
    with db.session() as s:
        s.query(Subject).filter_by(subject_id=ANIMALS[0]).update(
            {"notes": "auto-created from video 20260302_PROJA0101_P1 by the reach import"})

    r = _import(db, _workbook(_sheet(tmp_path)))

    with db.session() as s:
        note = s.query(Subject).filter_by(subject_id=ANIMALS[0]).one().notes
    assert not note.lower().startswith("auto-created") and "20260302_PROJA0101_P1" in note
    assert r["sheet_sync"]["subjects"]["notes_cleared"][0]["subject_id"] == ANIMALS[0]
    assert r["imported"]["subject_notes_cleared"] == 1


def test_ramp_rows_no_longer_in_the_sheet_are_removed(tmp_path):
    db = _db(tmp_path)
    ramp = [(a, date(2026, 2, 23) + timedelta(days=i), 22.0) for a in ANIMALS for i in range(3)]
    _import(db, _workbook(_sheet(tmp_path), ramp=ramp))
    r = _import(db, _workbook(_sheet(tmp_path), ramp=ramp[:2] + ramp[3:5]))
    with db.session() as s:
        assert s.query(RampEntry).count() == 4
    assert len(r["sheet_sync"]["ramp_entries"]["removed"]) == 2


def test_the_summary_line_names_what_the_sheet_removed():
    entry = {"sheet_sync": {"pellet_scores": {
        "removed_trays": [{"pellets": 20}, {"pellets": 20}], "dates_removed": ["2026-03-19"]}}}
    said = sheet_sync.sync_summary(entry)
    assert said.startswith("changed from the sheet")
    assert "40 rows removed in 2 trays no longer in the sheet" in said
    assert "2026-03-19" in said


def test_the_export_signature_follows_the_phase_labels(tmp_path):
    """A relabel changes no row count; the export's skip signature must still
    change, or the files keep the old labels. It hashes the reach and
    hand-score tables' whole content, test_phase included."""
    import pandas as pd
    from mousedb import snapshot_generation
    from mousedb.exporters.current import ODC_INPUT_TABLES
    assert {"reach_data", "pellet_scores"} <= set(ODC_INPUT_TABLES)
    df = pd.DataFrame({"subject_id": ["A_01_01"] * 2, "session_date": ["2026-03-11"] * 2,
                       "test_phase": ["Pillar_Training"] * 2, "phase_group": ["x"] * 2})
    df.to_parquet(tmp_path / "reach_data.parquet")
    one = snapshot_generation.table_hash(tmp_path, "reach_data")
    df.assign(test_phase="Pillar").to_parquet(tmp_path / "reach_data.parquet")
    assert one and snapshot_generation.table_hash(tmp_path, "reach_data") != one


def test_filling_in_values_typed_later_is_not_a_broken_read(tmp_path):
    """Columns are often typed in days after a row (the food weighed in and
    out on a ramp day). Filling a value that was stored empty is a correction
    like any other, and never counts toward the shifted-column guard."""
    db = _db(tmp_path)
    days = [date(2026, 2, 23) + timedelta(days=i) for i in range(4)]
    _import(db, _workbook(_sheet(tmp_path), ramp=[(a, d, 22.0) for a in ANIMALS for d in days]))

    r = _import(db, _workbook(_sheet(tmp_path),
                              ramp=[(a, d, 22.0, 3.0, 2.5) for a in ANIMALS for d in days]))

    assert "refused" not in r.get("sheet_sync", {})
    assert len(r["sheet_sync"]["ramp_entries"]["changed"]) == 8
    with db.session() as s:
        assert {(x.tray_start_grams, x.tray_end_grams, x.food_consumed_grams)
                for x in s.query(RampEntry)} == {(3.0, 2.5, 0.5)}

    # Overwriting most of them, by contrast, is refused.
    r = _import(db, _workbook(_sheet(tmp_path),
                              ramp=[(a, d, 22.0, 9.0, 2.5) for a in ANIMALS for d in days]))
    assert any(x.startswith("NOT SYNCED ramp_entries") for x in r["sheet_sync"]["refused"])


def test_a_death_copied_in_with_a_removed_contusion_is_undone(tmp_path):
    """A copied workbook carried the previous cohort's contusion rows, some
    saying the animal did not survive -- so living animals were marked dead
    on that day. Removing the copied row undoes the death it recorded, and
    only that one."""
    db = _db(tmp_path)
    p = _workbook(_sheet(tmp_path), surgeries=[(a, date(2026, 1, 20), 20.0, 70) for a in ANIMALS])
    wb = openpyxl.load_workbook(p)
    wb["4_Contusion_Injury_Details"]["F2"] = "N"      # first animal "did not survive"
    wb.save(p)
    _import(db, p)
    with db.session() as s:
        assert s.query(Subject).filter_by(subject_id=ANIMALS[0]).one().is_active == 0

    r = _import(db, _workbook(_sheet(tmp_path)), allow=True)

    with db.session() as s:
        a = s.query(Subject).filter_by(subject_id=ANIMALS[0]).one()
        assert a.is_active == 1 and a.date_of_death is None
    assert r["sheet_sync"]["subjects"]["death_undone"][0]["subject_id"] == ANIMALS[0]
    assert "1 death undone" in sheet_sync.sync_summary(r)


# --------------------------------------------------------------------------
# the weight percentage
# --------------------------------------------------------------------------

def _with_percent(path, percents):
    """The 3d_Weights tab with a Weight_Pct_Baseline column: {row number: value}."""
    wb = openpyxl.load_workbook(path)
    w = wb["3d_Weights"]
    w["D1"] = "Weight_Pct_Baseline"
    for row, value in percents.items():
        w["D%d" % row] = value
    wb.save(path)
    return path


def _percent(db, animal, day):
    with db.session() as s:
        return s.query(Weight).filter_by(subject_id=animal, date=day).one().weight_percent


def test_a_weight_percentage_changed_in_the_sheet_is_changed(tmp_path):
    """WHY: the sync compared the grams only, so a percentage corrected in the
    sheet kept its old value in the database and every export."""
    db = _db(tmp_path)
    _import(db, _with_percent(_workbook(_sheet(tmp_path)), {2: 0.91, 3: 0.92}))
    assert _percent(db, ANIMALS[0], DAYS[0]) == 0.91

    r = _import(db, _with_percent(_workbook(_sheet(tmp_path)), {2: 0.85, 3: 0.92}))

    assert _percent(db, ANIMALS[0], DAYS[0]) == 0.85
    assert r["sheet_sync"]["weights"]["changed"] == [{
        "subject_id": ANIMALS[0], "date": DAYS[0].isoformat(),
        "values": {"weight_percent": {"old": 0.91, "new": 0.85}}}]


def test_a_percentage_typed_as_a_percent_is_the_same_as_its_fraction(tmp_path):
    """87 and 0.87 are one value in two units: not a change to write."""
    db = _db(tmp_path)
    _import(db, _with_percent(_workbook(_sheet(tmp_path)), {2: 87}))
    r = _import(db, _with_percent(_workbook(_sheet(tmp_path)), {2: 0.87}))
    assert "weights" not in (r.get("sheet_sync") or {})
    assert _percent(db, ANIMALS[0], DAYS[0]) == 87


def test_the_tray_tabs_percentage_is_kept_in_step(tmp_path):
    """The tray tab's 'Weight %' fills a weight its own tab gave no percentage
    for; a correction there now reaches the stored row too."""
    def tray_percent(path, value):
        wb = openpyxl.load_workbook(path)
        ws = wb["3b_Manual_Tray"]
        col = ws.max_column + 1
        ws.cell(row=1, column=col, value="Weight %")
        for r in range(2, ws.max_row + 1):
            if ws.cell(row=r, column=1).value is not None:
                ws.cell(row=r, column=col, value=value)
        wb.save(path)
        return path

    db = _db(tmp_path)
    _import(db, tray_percent(_workbook(_sheet(tmp_path)), 0.9))
    assert _percent(db, ANIMALS[0], DAYS[0]) == 0.9
    _import(db, tray_percent(_workbook(_sheet(tmp_path)), 0.8), allow=True)
    assert _percent(db, ANIMALS[0], DAYS[0]) == 0.8


# --------------------------------------------------------------------------
# a workbook nobody chose: add only
# --------------------------------------------------------------------------

def test_a_workbook_read_on_a_guess_changes_and_removes_nothing(tmp_path):
    """WHY: with several files matching one cohort and none pinned, the newest
    is read -- a guess. Bringing the database in step with it would change and
    remove rows to match a draft or an old copy; the import adds only."""
    db = _db(tmp_path)
    _import(db, _workbook(_sheet(tmp_path)))
    d, a = DAYS[2], ANIMALS[1]
    old = _scores(d, a)[4]
    imp = ExcelImporter(db)
    imp.sync_blocked = "2 files match (test)"
    imp.allow_large_removal = True            # never overrides it
    r = imp.import_cohort_file(_workbook(_sheet(tmp_path), days=DAYS[:5],
                                         score_edit={(d, a, 1): (5, (old + 1) % 3)}))
    assert _pellets(db)[(a, d, 1, 5)] == old                       # not changed
    assert any(k[1] == DAYS[-1] for k in _pellets(db))              # not removed
    refused = r["sheet_sync"]["refused"]
    assert len(refused) == 1 and "pin" in refused[0] and "2 files match" in refused[0]


def test_a_first_import_on_a_guess_is_still_reported_not_synced(tmp_path):
    db = _db(tmp_path)
    imp = ExcelImporter(db)
    imp.sync_blocked = "2 files match (test)"
    r = imp.import_cohort_file(_workbook(_sheet(tmp_path)))
    assert len(r["sheet_sync"]["refused"]) == 1
    assert r["imported"]["pellet_scores"] > 0                        # rows still added


def test_a_percentage_typed_in_the_rows_own_column_replaces_the_stored_fraction(tmp_path):
    """The two units are compared as one, and a real change still lands: a
    stored 0.87 whose own column now reads 85 becomes 85."""
    db = _db(tmp_path)
    _import(db, _with_percent(_workbook(_sheet(tmp_path)), {2: 0.87}))
    _import(db, _with_percent(_workbook(_sheet(tmp_path)), {2: 85}))
    assert _percent(db, ANIMALS[0], DAYS[0]) == 85


def _guess(db, path):
    imp = ExcelImporter(db)
    imp.sync_blocked = "2 files match (test)"
    return imp.import_cohort_file(path)


def _dates_stored(db):
    with db.session() as s:
        out = {}
        for p in s.query(PelletScore):
            out[p.session_date] = out.get(p.session_date, 0) + 1
        return out


def test_a_workbook_read_on_a_guess_re_dates_no_stored_score(tmp_path):
    """WHY: the sheet-wins sync was refused on a guess, but the year-typo
    repair still moved stored scores to the date it read -- following a file
    nobody chose. They stay where they are, and are not stored twice."""
    db = _db(tmp_path)
    _import(db, _workbook(_sheet(tmp_path)))
    typo = DAYS[3].replace(year=DAYS[3].year - 1)
    with db.session() as s:      # an earlier import filed that day under the typo
        s.query(PelletScore).filter_by(session_date=DAYS[3]).update({"session_date": typo})
    days = [typo if d == DAYS[3] else d for d in DAYS]
    per_day = _dates_stored(db)[typo]

    r = _guess(db, _workbook(_sheet(tmp_path), days=days))
    stored = _dates_stored(db)
    assert stored.get(typo) == per_day and DAYS[3] not in stored
    assert any("HELD BACK" in w and "re-dating" in w for w in r["warnings"])

    _import(db, _workbook(_sheet(tmp_path), days=days))        # once pinned: moved
    stored = _dates_stored(db)
    assert stored.get(DAYS[3]) == per_day and typo not in stored


def test_a_guess_after_the_repair_reads_the_typo_rows_under_the_date_meant(tmp_path):
    """WHY: the usual case. An earlier import already filed a year-typo day
    under the date meant; a second matching file then makes the import a
    guess. Reading the typo rows under the typed date would find nothing
    stored there and store the same trays again, a year away -- one tray
    counted twice and the cohort's phases derived around a false gap."""
    db = _db(tmp_path)
    typo = DAYS[3].replace(year=DAYS[3].year - 1)
    days = [typo if d == DAYS[3] else d for d in DAYS]
    _import(db, _workbook(_sheet(tmp_path), days=days))
    before = _dates_stored(db)
    assert typo not in before and before.get(DAYS[3])

    _guess(db, _workbook(_sheet(tmp_path), days=days))
    assert _dates_stored(db) == before


def _with_weekend_island(path, weekend):
    """The second row of the third day (first animal, tray 2) dated a weekend
    day: a run inside that weekday's block, as a formula date counting from
    the wrong row leaves it."""
    wb = openpyxl.load_workbook(path)
    ws = wb["3b_Manual_Tray"]
    for r in range(2, ws.max_row + 1):
        if (ws.cell(r, 1).value.date() == DAYS[2] and ws.cell(r, 2).value == ANIMALS[0]
                and ws.cell(r, 3).value == "P2"):
            ws.cell(r, 1, weekend)
    wb.save(path)
    return path


def test_a_guess_reads_a_weekend_island_under_its_weekday_and_moves_no_stored_tray(tmp_path):
    db = _db(tmp_path)
    weekend = date(2026, 3, 7)
    assert weekend.weekday() == 5 and DAYS[2] < weekend
    sheet = lambda: _with_weekend_island(_workbook(_sheet(tmp_path), trays=(1, 2)), weekend)
    _import(db, sheet())
    before = _dates_stored(db)
    assert weekend not in before

    # Read on a guess after the repair: the island is read under its weekday
    # and matches what is stored -- not stored again under the weekend.
    _guess(db, sheet())
    assert _dates_stored(db) == before

    # Stored rows still under the weekend (left from before the repair): on a
    # guess they are not moved, and the rows read match them where they are.
    with db.session() as s:
        s.query(PelletScore).filter_by(subject_id=ANIMALS[0], session_date=DAYS[2],
                                       tray_number=2).update({"session_date": weekend})
    stale = _dates_stored(db)
    r = _guess(db, sheet())
    assert _dates_stored(db) == stale
    assert any("HELD BACK" in w and "weekend" in w for w in r["warnings"])


def _hand_score(db, animal, day, tray, pellet=1):
    """One score a person typed in (not the sheet import's)."""
    with db.session() as s:
        s.add(PelletScore(subject_id=animal, session_date=day, tray_type="P",
                          tray_number=tray, pellet_number=pellet, score=1,
                          test_phase="Pillar", entered_by="manual"))


def test_a_score_typed_in_by_hand_under_a_typo_date_pulls_nothing_back_on_a_guess(tmp_path):
    """WHY: on a guess the import used to read a whole year-typo DAY as typed
    when anything at all was stored under it. One score a person had typed in
    under that date (which the repair never moves) sent every tray of the day
    back to the typed date and stored them all a second time, a year away.
    The choice is per tray, and only the import's own rows count."""
    db = _db(tmp_path)
    typo = DAYS[3].replace(year=DAYS[3].year - 1)
    days = [typo if d == DAYS[3] else d for d in DAYS]
    _import(db, _workbook(_sheet(tmp_path), days=days))       # moved to the date meant
    _hand_score(db, ANIMALS[0], typo, tray=1)
    before = _dates_stored(db)
    assert before[typo] == 1

    _guess(db, _workbook(_sheet(tmp_path), days=days))
    assert _dates_stored(db) == before


def test_a_pellet_the_move_left_behind_does_not_pull_its_tray_back_on_a_guess(tmp_path):
    """A stored score the move could not take (the date meant already had that
    pellet) stays under the typed date until a sync clears it; the rest of its
    tray is filed under the date meant, and a guess reads the tray there --
    not all of it again under the typed date."""
    db = _db(tmp_path)
    typo = DAYS[3].replace(year=DAYS[3].year - 1)
    days = [typo if d == DAYS[3] else d for d in DAYS]
    _import(db, _workbook(_sheet(tmp_path), days=days))       # moved to the date meant
    with db.session() as s:      # the import's own pellet the move left behind
        s.add(PelletScore(subject_id=ANIMALS[0], session_date=typo, tray_type="P",
                          tray_number=1, pellet_number=1, score=1, test_phase="Pillar",
                          entered_by="excel_import"))
    before = _dates_stored(db)
    assert before[typo] == 1

    _guess(db, _workbook(_sheet(tmp_path), days=days))
    assert _dates_stored(db) == before


def test_a_score_typed_in_by_hand_under_a_weekend_date_pulls_no_tray_back_on_a_guess(tmp_path):
    db = _db(tmp_path)
    weekend = date(2026, 3, 7)
    sheet = lambda: _with_weekend_island(_workbook(_sheet(tmp_path), trays=(1, 2)), weekend)
    _import(db, sheet())
    _hand_score(db, ANIMALS[0], weekend, tray=2)
    before = _dates_stored(db)

    _guess(db, sheet())
    assert _dates_stored(db) == before


def test_a_guess_fills_the_tray_tabs_percentage_on_the_date_meant(tmp_path):
    """The weight % on the tray tab follows the same per-tray choice as the
    scores: a score typed in by hand under the typo date does not send the
    percentage to a date no weight is stored under."""
    typo = DAYS[1].replace(year=DAYS[1].year - 1)

    def sheet():
        """The weights typed right; the tray tab's day typed a year off,
        with a 'Weight %' column."""
        wb = openpyxl.load_workbook(_workbook(_sheet(tmp_path)))
        ws = wb["3b_Manual_Tray"]
        col = ws.max_column + 1
        ws.cell(row=1, column=col, value="Weight %")
        for r in range(2, ws.max_row + 1):
            ws.cell(row=r, column=col, value=0.9)
            if ws.cell(row=r, column=1).value.date() == DAYS[1]:
                ws.cell(row=r, column=1, value=typo)
        wb.save(_sheet(tmp_path))
        return _sheet(tmp_path)

    db = _db(tmp_path)
    _import(db, sheet())
    assert _percent(db, ANIMALS[0], DAYS[1]) == 0.9
    with db.session() as s:
        s.query(Weight).filter_by(subject_id=ANIMALS[0], date=DAYS[1]).update(
            {"weight_percent": None})
    _hand_score(db, ANIMALS[0], typo, tray=1)

    _guess(db, sheet())
    assert _percent(db, ANIMALS[0], DAYS[1]) == 0.9


def test_a_workbook_read_on_a_guess_replaces_no_note_and_records_no_death(tmp_path):
    db = _db(tmp_path)
    note = "auto-created from video 20260302_PROJA0101_P1 by the reach import"
    with db.session() as s:
        s.query(Subject).filter_by(subject_id=ANIMALS[0]).update({"notes": note})
    path = _workbook(_sheet(tmp_path))
    wb = openpyxl.load_workbook(path)
    ws = wb["4_Contusion_Injury_Details"]
    ws.cell(row=2, column=6, value="N")          # the first animal did not survive
    wb.save(path)

    r = _guess(db, path)
    with db.session() as s:
        a = s.query(Subject).filter_by(subject_id=ANIMALS[0]).one()
        assert a.notes == note and a.is_active != 0
    held = r["sheet_sync"]["held_back"]
    assert any("placeholder note" in h for h in held)
    assert any("not survived" in h for h in held)


# --------------------------------------------------------------------------
# the contusion tab's Survived column
# --------------------------------------------------------------------------

def _survived_cells(path, values):
    """Type ``values`` (one per animal, in ANIMALS order) into the contusion
    tab's Survived column (F)."""
    wb = openpyxl.load_workbook(path)
    for i, v in enumerate(values):
        # Assigned, not passed to cell(): cell(value=None) leaves the cell as it was.
        wb["4_Contusion_Injury_Details"].cell(row=2 + i, column=6).value = v
    wb.save(path)
    return path


def _survived(db):
    with db.session() as s:
        return {x.subject_id: x.survived
                for x in s.query(Surgery).filter_by(surgery_type="contusion")}


def test_a_contusion_the_animal_did_not_survive_is_stored_as_not_survived(tmp_path):
    """The contusion reader marked the animal dead from Survived = N but never
    stored the flag on the surgery row itself, so every contusion read as
    survived (the column's default) -- in the database and in every count of
    surgical deaths built from it."""
    db = _db(tmp_path)

    _import(db, _survived_cells(_workbook(_sheet(tmp_path)), ["N", "Y"]))

    assert _survived(db) == {ANIMALS[0]: 0, ANIMALS[1]: 1}


def test_a_survived_flag_corrected_in_the_sheet_is_corrected_in_the_database(tmp_path):
    db = _db(tmp_path)
    _import(db, _workbook(_sheet(tmp_path)))
    assert _survived(db) == {ANIMALS[0]: 1, ANIMALS[1]: 1}

    r = _import(db, _survived_cells(_workbook(_sheet(tmp_path)), ["N", "Y"]))

    assert _survived(db) == {ANIMALS[0]: 0, ANIMALS[1]: 1}
    assert r["sheet_sync"]["surgeries"]["changed"][0]["values"] == {
        "survived": {"old": 1, "new": 0}}


def test_a_blank_or_unreadable_survived_cell_leaves_the_stored_flag_alone(tmp_path):
    """A blank cell says nothing, and '?' is not an answer: neither may turn a
    stored flag into 'unknown' or count as a change."""
    db = _db(tmp_path)
    _import(db, _survived_cells(_workbook(_sheet(tmp_path)), ["N", "Y"]))

    r = _import(db, _survived_cells(_workbook(_sheet(tmp_path)), [None, "?"]))

    assert _survived(db) == {ANIMALS[0]: 0, ANIMALS[1]: 1}
    assert "surgeries" not in r.get("sheet_sync", {})


def test_survived_flags_overwritten_on_too_many_rows_are_refused(tmp_path):
    """A survived flag turned from 1 to 0 overwrites a stored value, so it
    counts toward the shifted-column guard like any other. Both sides of the
    line, with the line moved so two animals can show them."""
    def imp(path):
        i = ExcelImporter(db)
        i.SYNC_SMALL = 0
        i.SYNC_MAX_CHANGED_FRACTION = 0.5
        return i.import_cohort_file(path)

    db = _db(tmp_path)
    imp(_workbook(_sheet(tmp_path)))

    r = imp(_survived_cells(_workbook(_sheet(tmp_path)), ["N", "Y"]))   # 1 of 2: allowed
    assert "refused" not in r["sheet_sync"]
    assert _survived(db) == {ANIMALS[0]: 0, ANIMALS[1]: 1}

    imp(_workbook(_sheet(tmp_path)))                                    # back to Y, Y
    r = imp(_survived_cells(_workbook(_sheet(tmp_path)), ["N", "N"]))   # 2 of 2: refused
    assert any(x.startswith("NOT SYNCED surgeries") for x in r["sheet_sync"]["refused"])
    assert _survived(db) == {ANIMALS[0]: 1, ANIMALS[1]: 1}


def _subject_state(db, animal):
    with db.session() as s:
        a = s.query(Subject).filter_by(subject_id=animal).one()
        return a.is_active, a.date_of_death


def test_a_survived_cell_corrected_to_yes_brings_the_animal_back(tmp_path):
    """A mistyped N on a contusion row marks the animal dead on the surgery's
    day AND stores the surgery as not survived. Correcting the cell to Y used
    to correct only the surgery: the animal stayed dead, dated that day, so
    the database said both "survived the surgery" and "died that day", and
    the death is what attrition counts and the exports read."""
    db = _db(tmp_path)
    _import(db, _survived_cells(_workbook(_sheet(tmp_path)), ["N", "Y"]))
    assert _subject_state(db, ANIMALS[0]) == (0, date(2026, 3, 20))

    dry = _import(db, _workbook(_sheet(tmp_path)), dry_run=True)
    said = ((dry.get("sheet_sync") or {}).get("subjects") or {}).get("death_undone") or []
    assert [u["subject_id"] for u in said] == [ANIMALS[0]]
    assert _subject_state(db, ANIMALS[0]) == (0, date(2026, 3, 20))   # a dry run writes nothing

    r = _import(db, _workbook(_sheet(tmp_path)))                      # Y, Y

    assert _survived(db) == {ANIMALS[0]: 1, ANIMALS[1]: 1}
    assert _subject_state(db, ANIMALS[0]) == (1, None)
    undone = ((r.get("sheet_sync") or {}).get("subjects") or {}).get("death_undone") or []
    assert [u["subject_id"] for u in undone] == [ANIMALS[0]]
    assert "survived" in undone[0]["because"]
    with db.session() as s:
        logged = [json.loads(e.new_values or "{}") for e in s.query(AuditLog).filter_by(
            table_name="subjects", record_id=ANIMALS[0])]
    assert any(v.get("is_active") == 1 for v in logged)


def test_a_death_the_metadata_tab_records_is_not_undone_by_a_survived_cell(tmp_path):
    """When 0a_Metadata gives the animal a Date_of_Death, the death is the
    sheet's own statement: a contusion row corrected to Y does not bring the
    animal back, and a warning says which two cells disagree."""
    db = _db(tmp_path)

    def with_death(path):
        wb = openpyxl.load_workbook(path)
        m = wb.create_sheet("0a_Metadata")
        m.append(["SubjectID", "Date_of_Death"])
        m.append([ANIMALS[0], date(2026, 3, 20)])
        m.append([ANIMALS[1], None])
        wb.save(path)
        return path

    _import(db, with_death(_survived_cells(_workbook(_sheet(tmp_path)), ["N", "Y"])))
    assert _subject_state(db, ANIMALS[0]) == (0, date(2026, 3, 20))

    r = _import(db, with_death(_workbook(_sheet(tmp_path))))

    assert _survived(db) == {ANIMALS[0]: 1, ANIMALS[1]: 1}
    assert _subject_state(db, ANIMALS[0]) == (0, date(2026, 3, 20))
    assert "death_undone" not in r["sheet_sync"].get("subjects", {})
    assert any(ANIMALS[0] in w and "0a_Metadata" in w and "Date_of_Death" in w
               for w in r["warnings"])


def test_another_contusion_row_that_still_says_not_survived_keeps_the_death(tmp_path):
    """Only the row that recorded the death can take it back: while another
    contusion row of the animal still says N, the animal stays dead."""
    db = _db(tmp_path)
    day = date(2026, 3, 20)
    rows = [(ANIMALS[0], day, 21.0, 60), (ANIMALS[1], day, 21.0, 61)]
    _import(db, _survived_cells(_workbook(_sheet(tmp_path), surgeries=rows), ["N", "Y"]))
    assert _subject_state(db, ANIMALS[0]) == (0, day)

    p = _workbook(_sheet(tmp_path), surgeries=rows + [(ANIMALS[0], date(2026, 3, 27), 21.0, 62)])
    wb = openpyxl.load_workbook(p)
    wb["4_Contusion_Injury_Details"]["F4"] = "N"     # the extra row still says N
    wb.save(p)
    _import(db, p)

    assert _subject_state(db, ANIMALS[0]) == (0, day)


# --------------------------------------------------------------------------
# the weight on the day of a contusion
# --------------------------------------------------------------------------

def _contusion_grams(db):
    with db.session() as s:
        return {x.subject_id: x.pre_surgery_weight_grams
                for x in s.query(Surgery).filter_by(surgery_type="contusion")}


def test_a_contusions_weight_on_the_day_is_stored(tmp_path):
    """The weight typed on the contusion tab is the number that proves the
    surgery happened, yet it was read and thrown away: no contusion carried
    its surgery-day weight, while every injection did."""
    db = _db(tmp_path)

    _import(db, _workbook(_sheet(tmp_path), surgeries=[
        (ANIMALS[0], date(2026, 3, 20), 21.5, 60), (ANIMALS[1], date(2026, 3, 20), 23.0, 61)]))

    assert _contusion_grams(db) == {ANIMALS[0]: 21.5, ANIMALS[1]: 23.0}


def test_contusions_stored_without_their_weight_get_it_and_that_is_no_broken_read(tmp_path):
    """Rows imported before the weight was kept hold none. The next import
    fills it in -- every row at once -- and, being a fill rather than an
    overwrite, that never trips the shifted-column guard."""
    db = _db(tmp_path)
    _import(db, _workbook(_sheet(tmp_path)))
    with db.session() as s:
        for x in s.query(Surgery):
            x.pre_surgery_weight_grams = None

    r = _import(db, _workbook(_sheet(tmp_path)))

    assert _contusion_grams(db) == {ANIMALS[0]: 21.0, ANIMALS[1]: 21.0}
    assert "refused" not in r["sheet_sync"]
    assert len(r["sheet_sync"]["surgeries"]["changed"]) == 2
    # A weight corrected in the sheet is corrected too.
    _import(db, _workbook(_sheet(tmp_path), surgeries=[
        (ANIMALS[0], date(2026, 3, 20), 22.0, 60), (ANIMALS[1], date(2026, 3, 20), 21.0, 61)]))
    assert _contusion_grams(db) == {ANIMALS[0]: 22.0, ANIMALS[1]: 21.0}
