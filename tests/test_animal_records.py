"""animal_records copies a tracking sheet's animal-level tabs column for column.

WHY: the per-reach ODC export repeats each animal's surgery details on every row;
the typed surgery table kept only a few of those columns, so everything else the
lab typed into the sheet never reached an export.
"""
from datetime import datetime

import openpyxl
import pytest

from mousedb import animal_records as ar


def _cnt_workbook(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "4_Contusion_Injury_Details"
    # A surgery row counts only when the weight on the day is typed in (see
    # animal_records.surgery_verdict), so the performed rows carry one and the
    # planned row does not.
    ws.append(["Subject_ID", "Surgery_Date", "Surgery_Type", "Anesthetic", "Intended_kd",
               "Subject_Weight (g)", "Actual_kd", "Survived"])
    ws.append(["PROJA_01_01", datetime(2025, 7, 2), "Contusion", "drug-x", 60, 25.0, 65.0, "Y"])
    ws.append(["PROJA_01_02", datetime(2025, 7, 2), "Contusion", "drug-x", 60, None, None, None])  # planned, not done
    ws.append(["PROJB_01_01", datetime(2025, 7, 2), "Contusion", "drug-x", 60, 26.0, 61, "Y"])     # other cohort
    sc = wb.create_sheet("5_SC_Injection_Details")
    sc.append(["Subject_ID", "Surgery_Date", "Subject_Weight (g)", "Injected_Virus", "Survived"])
    sc.append(["PROJA_01_01", datetime(2025, 8, 1), 24.0, "virus-a", "Y"])
    sc.append(["PROJA_01_01", datetime(2025, 9, 1), 24.5, "virus-b", "N"])
    meta = wb.create_sheet("0a_Metadata")
    meta.append(["SubjectID", "Sex", "Notes"])
    meta.append(["PROJA_01_01", "F", "?"])
    tray = wb.create_sheet("3b_Manual_Tray")
    tray.append(["Date", "Animal", "Sex", "1"])
    tray.append([datetime(2025, 6, 19), "PROJA_01_03", None, 2])
    tray.append([datetime(2025, 6, 20), "PROJA_01_03", "Female", 1])
    tray.append([datetime(2025, 6, 21), "PROJA_01_03", "Male", 1])   # first value wins
    wb.save(path)


def _odc_workbook(path):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "ODC"
    ws.append([1, 1, 1, 1])
    ws.append([None, 123, "a note"])
    ws.append(["Group ID", "Animal ID", "Surgery_Date", "Organism_Strain"])
    ws.append(["K", 1, datetime(2022, 9, 22), "strain-k"])
    ws.append(["K", "?", None, "strain-k"])
    wb.save(path)


def test_cnt_tabs_copied_as_written(tmp_path):
    p = tmp_path / "PROJA_01.xlsx"
    _cnt_workbook(p)
    w = []
    recs = ar.read_cnt_workbook(p, "PROJA_01", w)
    got = {(r["subject_id"], r["source_tab"], r["record_no"], r["field"]): r["value"] for r in recs}
    assert got[("PROJA_01_01", "4_Contusion_Injury_Details", 1, "Surgery_Date")] == "2025-07-02"
    assert got[("PROJA_01_01", "4_Contusion_Injury_Details", 1, "Actual_kd")] == "65"
    assert got[("PROJA_01_01", "5_SC_Injection_Details", 2, "Injected_Virus")] == "virus-b"
    assert got[("PROJA_01_01", "0a_Metadata", 1, "Sex")] == "F"
    assert ("PROJA_01_01", "0a_Metadata", 1, "Notes") not in got           # '?' is not a value
    assert got[("PROJA_01_03", "3b_Manual_Tray", 1, "Sex")] == "Female"    # sex from the scoring tab
    assert not any(k[0] == "PROJA_01_02" for k in got)                     # planned row skipped
    assert not any(k[0].startswith("PROJB") for k in got)                   # other cohort skipped
    # ...and not said here: the sheet importer reads the same tab, refuses the same
    # row and names it, and one import must not say it twice.
    assert not any("PROJB_01_01" in m for m in w)


def test_odc_tab_header_found_below_notes(tmp_path):
    p = tmp_path / "K.xlsx"
    _odc_workbook(p)
    recs = ar.read_odc_tab(p, "ASPA_11", [])
    fields = {(r["subject_id"], r["field"]): r["value"] for r in recs}
    assert fields[("ASPA_11_01", "Organism_Strain")] == "strain-k"
    assert fields[("ASPA_11_01", "Surgery_Date")] == "2022-09-22"
    assert {r["subject_id"] for r in recs} == {"ASPA_11_01"}


def test_import_replaces_instead_of_piling_up(tmp_path):
    from mousedb.database import Database
    from mousedb.schema import AnimalRecord
    db = Database(tmp_path / "t.db")
    p = tmp_path / "PROJA_01.xlsx"
    _cnt_workbook(p)
    first = ar.sync_cohort(db, "PROJA_01", p)
    second = ar.sync_cohort(db, "PROJA_01", p)
    assert first["error"] is None and second["records"] == first["records"] > 0
    with db.session() as s:
        assert s.query(AnimalRecord).count() == first["records"]


def test_sync_never_raises(tmp_path):
    r = ar.sync_cohort(None, "PROJA_01", tmp_path / "missing.xlsx")
    assert r["error"] and r["records"] == 0


# ---------------------------------------------------------------------------
# a tab whose read looks broken leaves its stored details as they were
# ---------------------------------------------------------------------------

def _stored_by_tab(db):
    from mousedb.schema import AnimalRecord
    with db.session() as s:
        out = {}
        for r in s.query(AnimalRecord):
            out[r.source_tab] = out.get(r.source_tab, 0) + 1
        return out


def _scratch_db(tmp_path):
    from mousedb.database import Database
    return Database(tmp_path / "t.db")


def test_a_tab_missing_from_the_workbook_keeps_its_details(tmp_path):
    """WHY: every tab's details would otherwise be deleted whether or not the tab was
    read, so a tab renamed or cut from the workbook emptied its records."""
    db = _scratch_db(tmp_path)
    p = tmp_path / "PROJA_01.xlsx"
    _cnt_workbook(p)
    ar.sync_cohort(db, "PROJA_01", p)
    before = _stored_by_tab(db)
    assert before.get("0a_Metadata")
    wb = openpyxl.load_workbook(p)
    del wb["0a_Metadata"]
    wb.save(p)
    ar.sync_cohort(db, "PROJA_01", p)
    assert _stored_by_tab(db).get("0a_Metadata") == before["0a_Metadata"]


def test_a_tab_that_reads_as_empty_keeps_its_details_unless_allowed(tmp_path):
    db = _scratch_db(tmp_path)
    p = tmp_path / "PROJA_01.xlsx"
    _cnt_workbook(p)
    ar.sync_cohort(db, "PROJA_01", p)
    before = _stored_by_tab(db)["0a_Metadata"]
    wb = openpyxl.load_workbook(p)
    ws = wb["0a_Metadata"]
    ws.delete_rows(2, ws.max_row)                # header only: no animal at all
    wb.save(p)

    r = ar.sync_cohort(db, "PROJA_01", p)
    assert _stored_by_tab(db).get("0a_Metadata") == before
    assert [k["tab"] for k in r["kept"]] == ["0a_Metadata"]
    assert "broken read" in r["kept"][0]["why"]

    r = ar.sync_cohort(db, "PROJA_01", p, allow_large_removal=True)
    assert r["kept"] == [] and "0a_Metadata" not in _stored_by_tab(db)


def test_a_tab_the_caller_keeps_is_left_as_it_was(tmp_path):
    db = _scratch_db(tmp_path)
    p = tmp_path / "PROJA_01.xlsx"
    _cnt_workbook(p)
    ar.sync_cohort(db, "PROJA_01", p)
    before = _stored_by_tab(db)
    ar.replace_cohort(db, "PROJA_01", ("4_Contusion_Injury_Details",), [])
    r = ar.sync_cohort(db, "PROJA_01", p,
                       keep_tabs={"4_Contusion_Injury_Details": "surgeries refused"})
    assert r["kept"] == [{"tab": "4_Contusion_Injury_Details", "why": "surgeries refused"}]
    assert "4_Contusion_Injury_Details" not in _stored_by_tab(db)
    assert _stored_by_tab(db)["0a_Metadata"] == before["0a_Metadata"]


def test_a_dry_run_does_not_create_the_table(tmp_path):
    from sqlalchemy import inspect
    db = _scratch_db(tmp_path)
    p = tmp_path / "PROJA_01.xlsx"
    _cnt_workbook(p)
    r = ar.sync_cohort(db, "PROJA_01", p, dry_run=True)
    assert r["error"] is None and r["records"] > 0
    assert not inspect(db.engine).has_table("animal_records")


def test_a_tab_that_was_not_read_is_reported_and_removed_only_when_allowed(tmp_path):
    """WHY: a tab missing from the workbook (renamed, cut) kept its stored
    details in silence, so its old values stayed in the exports for good while
    every import said OK. It is now listed under "kept" -- which the sheet
    import reports as a refusal -- and a person who checked the sheet can have
    its details removed."""
    db = _scratch_db(tmp_path)
    p = tmp_path / "PROJA_01.xlsx"
    _cnt_workbook(p)
    ar.sync_cohort(db, "PROJA_01", p)
    before = _stored_by_tab(db)
    wb = openpyxl.load_workbook(p)
    wb["0a_Metadata"].title = "0a_Metadata (renamed)"
    tray = wb["3b_Manual_Tray"]
    tray.delete_cols(3)                       # the Sex column is gone
    wb.save(p)

    r = ar.sync_cohort(db, "PROJA_01", p)
    after = _stored_by_tab(db)
    assert after["0a_Metadata"] == before["0a_Metadata"]
    assert after["3b_Manual_Tray"] == before["3b_Manual_Tray"]
    kept = {k["tab"]: k for k in r["kept"]}
    assert set(kept) == {"0a_Metadata", "3b_Manual_Tray"}
    assert all(k.get("unread") and "not read" in k["why"] for k in kept.values())

    r = ar.sync_cohort(db, "PROJA_01", p, allow_large_removal=True)
    after = _stored_by_tab(db)
    assert r["kept"] == [] and "0a_Metadata" not in after and "3b_Manual_Tray" not in after


def test_a_rebuild_takes_an_empty_tab_as_empty_but_keeps_an_unread_one(tmp_path):
    """WHY: the cohort-setup Replace used the removal permission, which also
    deleted the details of a tab that was not read (renamed, missing) and said
    nothing. allow_empty_tabs takes a tab READ as holding no animal as empty,
    and still keeps and reports a tab that was not read."""
    db = _scratch_db(tmp_path)
    p = tmp_path / "PROJA_01.xlsx"
    _cnt_workbook(p)
    ar.sync_cohort(db, "PROJA_01", p)
    before = _stored_by_tab(db)
    wb = openpyxl.load_workbook(p)
    wb["0a_Metadata"].title = "0a_Metadata (renamed)"
    ws = wb["5_SC_Injection_Details"]
    ws.delete_rows(2, ws.max_row)                # read, and no animal in it
    wb.save(p)

    r = ar.sync_cohort(db, "PROJA_01", p, allow_empty_tabs=True)
    after = _stored_by_tab(db)
    assert after["0a_Metadata"] == before["0a_Metadata"]
    assert "5_SC_Injection_Details" not in after
    assert [(k["tab"], k.get("unread")) for k in r["kept"]] == [("0a_Metadata", True)]


def test_an_odc_tab_without_its_header_is_reported(tmp_path):
    db = _scratch_db(tmp_path)
    p = tmp_path / "Z.xlsx"                    # an invented letter cohort
    _odc_workbook(p)
    wb = openpyxl.load_workbook(p)
    for row in (4, 5):
        wb["ODC"].cell(row, 1, "Z")
    wb.save(p)
    ar.sync_cohort(db, "ASPA_26", p, "odc")
    assert _stored_by_tab(db).get("ODC")
    wb = openpyxl.load_workbook(p)
    wb["ODC"]["B3"] = "Animal"                 # the header cell no longer found
    wb.save(p)
    r = ar.sync_cohort(db, "ASPA_26", p, "odc")
    assert _stored_by_tab(db).get("ODC") and [k["tab"] for k in r["kept"]] == ["ODC"]


def _metadata_workbook(path, animals):
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "0a_Metadata"
    ws.append(["SubjectID", "Sex", "Date_of_Birth"])
    for n in animals:
        ws.append(["PROJA_01_%02d" % n, "F" if n % 2 else "M", datetime(2025, 1, n)])
    wb.save(path)
    return path


def _metadata_animals(db):
    from mousedb.schema import AnimalRecord
    with db.session() as s:
        return {sid for (sid,) in s.query(AnimalRecord.subject_id)
                .filter(AnimalRecord.source_tab == "0a_Metadata").distinct()}


def test_a_tab_that_reads_only_part_of_its_animals_keeps_its_details(tmp_path):
    """WHY: the empty-read guard was zero-or-nothing, so a tab that read only
    some of its rows (a shifted header, a file caught mid-save) still replaced
    the cohort's details, and the left-out animals' sex and date of birth
    vanished without a word. More than the importer's SYNC_SMALL animals and
    more than its SYNC_MAX_REMOVED_FRACTION of them left out is now a broken
    read, as it is for pellet scores, weights and surgeries."""
    db = _scratch_db(tmp_path)
    p = tmp_path / "PROJA_01.xlsx"
    every = list(range(1, 17))
    ar.sync_cohort(db, "PROJA_01", _metadata_workbook(p, every))
    assert len(_metadata_animals(db)) == 16

    r = ar.sync_cohort(db, "PROJA_01", _metadata_workbook(p, every[:8]))   # 8 of 16 left out

    assert len(_metadata_animals(db)) == 16
    assert [k["tab"] for k in r["kept"]] == ["0a_Metadata"]
    assert "8 of the 16 animals" in r["kept"][0]["why"]
    assert "PROJA_01_09" in r["kept"][0]["why"]


def test_a_few_animals_taken_out_of_a_tab_is_an_edit(tmp_path):
    db = _scratch_db(tmp_path)
    p = tmp_path / "PROJA_01.xlsx"
    every = list(range(1, 17))
    ar.sync_cohort(db, "PROJA_01", _metadata_workbook(p, every))

    r = ar.sync_cohort(db, "PROJA_01", _metadata_workbook(p, every[:-2]))  # 2 of 16 left out

    assert r["kept"] == []
    assert _metadata_animals(db) == {"PROJA_01_%02d" % n for n in every[:-2]}


@pytest.mark.parametrize("permission", ["allow_large_removal", "allow_empty_tabs"])
def test_a_partly_read_tab_is_replaced_when_a_person_allows_it(tmp_path, permission):
    db = _scratch_db(tmp_path)
    p = tmp_path / "PROJA_01.xlsx"
    every = list(range(1, 17))
    ar.sync_cohort(db, "PROJA_01", _metadata_workbook(p, every))

    r = ar.sync_cohort(db, "PROJA_01", _metadata_workbook(p, every[:8]), **{permission: True})

    assert r["kept"] == []
    assert _metadata_animals(db) == {"PROJA_01_%02d" % n for n in every[:8]}


def test_a_partly_read_tab_fails_the_sheet_import(tmp_path):
    """The kept tab is reported with the importer's refusals, so the import is
    not called a success (sheet_sync.import_succeeded)."""
    from mousedb import sheet_sync
    db = _scratch_db(tmp_path)
    p = tmp_path / "PROJA_01.xlsx"
    every = list(range(1, 17))
    ar.sync_cohort(db, "PROJA_01", _metadata_workbook(p, every))
    rec = ar.sync_cohort(db, "PROJA_01", _metadata_workbook(p, every[:8]))
    entry = {"cohort_id": "PROJA_01", "success": True, "warnings": []}

    sheet_sync._report_kept_tabs(entry, rec)

    assert any("0a_Metadata" in x for x in sheet_sync.sync_refused(entry))
    assert not sheet_sync.import_succeeded({"cohorts": [entry]})
