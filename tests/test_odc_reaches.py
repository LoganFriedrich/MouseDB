"""Per-reach ODC export: every reach row carries the animal and the session.

WHY: collaborators receive one flat table per cohort; the hand-built version left
every animal column empty. Every column must have a dictionary row, and nothing
may be guessed when no source records a value.
"""
import pandas as pd

from mousedb.exporters import odc_reaches as ox


def _reach():
    rows = []
    for vid, date, run, outcomes in (("20250710_PROJA0101_P1", "2025-07-10", 1, ["retrieved", "displaced_sa"]),
                                     ("20250701_PROJA0101_P2", "2025-07-01", 2, ["untouched"])):
        for seg, o in enumerate(outcomes, 1):
            for n in (1, 2):
                rows.append({"id": len(rows), "subject_id": "PROJA_01_01", "video_name": vid,
                             "session_date": date, "tray_type": "P", "run_number": run,
                             "segment_num": seg, "reach_id": len(rows), "reach_num": n,
                             "segment_outcome": o, "attention_score": 0.5, "test_phase": "Pillar",
                             "phase_group": "Baseline", "extended_features": "{}"})
    return pd.DataFrame(rows)


def _records():
    r = [("4_Contusion_Injury_Details", 1, "Surgery_Date", "2025-07-02"),
         ("4_Contusion_Injury_Details", 1, "Surgery_Type", "Contusion"),
         ("4_Contusion_Injury_Details", 1, "Intended_kd", "60"),
         ("4_Contusion_Injury_Details", 1, "Actual_kd", "65"),
         ("4_Contusion_Injury_Details", 1, "Contusion_Location", "C5"),
         ("4_Contusion_Injury_Details", 1, "Survived", "Y"),
         ("5_SC_Injection_Details", 2, "Injected_Virus", "virus-b")]
    return pd.DataFrame([{"subject_id": "PROJA_01_01", "cohort_id": "PROJA_01", "source_tab": t,
                          "record_no": n, "field": f, "value": v} for t, n, f, v in r])


def _build(records=None, pellets=None):
    subjects = pd.DataFrame([{"subject_id": "PROJA_01_01", "sex": "F", "date_of_birth": None}])
    facts = lambda sid: {"SpeciesStrainTyp": "strain-x", "StudyLeader": "leader-x", "Injury_device": "device-x"}
    return ox.build_cohort("PROJA_01", _reach(), subjects, records, pellets, facts, "lab-x")


def test_columns_in_block_order_and_all_documented():
    df, rows = _build(_records())
    cols = list(df.columns)
    assert cols[:len(ox.ANIMAL_COLUMNS)] == ox.ANIMAL_COLUMNS
    assert cols[-len(ox.TOTAL_COLUMNS):] == ox.TOTAL_COLUMNS
    assert "Contusion_Actual_kd" in cols and "Injection2_Injected_Virus" in cols
    assert "id" not in cols and "extended_features" not in cols
    assert [c for c in cols if c not in {r["VariableName"] for r in rows}] == []


def test_animal_and_session_values():
    df, _ = _build(_records())
    first = df.iloc[0]
    assert first["SpeciesStrainTyp"] == "strain-x" and first["StudyLeader"] == "leader-x"
    assert first["Laboratory"] == "lab-x" and first["SexTyp"] == "F"
    assert first["Injury_Type"] == "Contusion-60kD" and first["Injury_level"] == "C5"
    assert first["Exclusion_in_origin_study"] == "No exclusion"
    by_date = df.groupby("Test_Date")["Days_Post_Injury"].first()
    assert pd.isna(by_date["2025-07-01"]) and by_date["2025-07-10"] == 8   # empty before injury
    assert df["Session_ID"].iloc[0].startswith("PROJA_01_01-")


def test_totals_count_pellets_per_video_and_hand_scores_per_tray():
    pellets = pd.DataFrame([{"subject_id": "PROJA_01_01", "session_date": "2025-07-10", "tray_type": "P",
                             "tray_number": 1, "pellet_number": i, "score": s}
                            for i, s in enumerate([2, 2, 1, 0], 1)])
    df, _ = _build(_records(), pellets)
    day = df[df["Test_Date"] == "2025-07-10"].iloc[0]
    assert day["Total_Swipes_AI"] == 4
    assert (day["Video_Retrieved"], day["Video_Displaced"], day["Video_Contacted"]) == (1, 1, 2)
    assert (day["Manual_Retrieved"], day["Manual_Displaced"], day["Manual_Contacted"]) == (2, 1, 3)
    assert day["Contacted_Match"] == 1
    assert pd.isna(df[df["Test_Date"] == "2025-07-01"].iloc[0]["Manual_Contacted"])


def test_nothing_guessed_without_sheet_records():
    df, _ = _build(records=None)
    assert (df["Injury_Type"] == "").all() and (df["Injury_device"] == "").all()
    assert (df["Exclusion_in_origin_study"] == "").all()
    assert df["Days_Post_Injury"].isna().all()


def test_sex_from_scoring_tab_normalised_and_leader_from_author():
    rec = pd.DataFrame([{"subject_id": "PROJA_01_01", "cohort_id": "PROJA_01", "source_tab": t,
                         "record_no": 1, "field": f, "value": v}
                        for t, f, v in (("3b_Manual_Tray", "Sex", "Female"),
                                        ("ODC", "Author_First_Name", "First"),
                                        ("ODC", "Author_Last_Name", "Last"))])
    subjects = pd.DataFrame([{"subject_id": "PROJA_01_01", "sex": None, "date_of_birth": None}])
    df, _ = ox.build_cohort("PROJA_01", _reach(), subjects, rec, None, lambda s: {}, "")
    assert (df["SexTyp"] == "F").all() and (df["StudyLeader"] == "First Last").all()


def test_letter_cohort_file_names_carry_the_letter_and_old_names_are_archived(tmp_path):
    out = tmp_path / "exports" / "current"
    out.mkdir(parents=True)
    (out / "ODC_reaches_ASPA_04.csv").write_text("old")
    df, rows = _build(_records())
    entry, label = ox.write_cohort(out, "ASPA_04", df, rows)
    assert label == "ASPA_04_D" and (out / "ODC_reaches_ASPA_04_D.csv").exists()
    assert not (out / "ODC_reaches_ASPA_04.csv").exists()
    archived = list((tmp_path / "_archived" / "exports_current").rglob("ODC_reaches_ASPA_04.csv"))
    assert len(archived) == 1 and archived[0].read_text() == "old"     # moved, never deleted
    assert ox.cohort_label("PROJA_01") == "PROJA_01"


def test_letter_cohort_ids_are_decoded():
    assert ox.lab_subject_id("ASPA_11_03") == "K03"
    assert ox.lab_subject_id("PROJA_01_01") == "PROJA_01_01"
