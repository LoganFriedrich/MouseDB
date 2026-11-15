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
        assert {(x.tray_start_grams, x.tray_end_grams) for x in s.query(RampEntry)} == {(3.0, 2.5)}

    # Overwriting most of them, by contrast, is refused.
    r = _import(db, _workbook(_sheet(tmp_path),
                              ramp=[(a, d, 22.0, 9.0, 2.5) for a in ANIMALS for d in days]))
    assert any(x.startswith("NOT SYNCED ramp_entries") for x in r["sheet_sync"]["refused"])
