"""An entry with a problem is left out of the analysis exports -- only that entry.

WHY THESE TESTS EXIST
---------------------
The database holds reaches filed under the wrong animal, dated after the
animal's death, and labelled with experiment phases that contradict the
cohort's own injury dates. The measurements are sound; their labels are not.
The importer cannot yet correct them, so the analysis exports leave them out,
at the smallest scope that contains the problem, until the source is fixed --
and the exclusion is recomputed on every export, so a fixed entry comes back
on its own.

Each check is chosen so that it does NOT rest on trusting hand-typed pellet
scores: a disagreement is never evidence, only a near-perfect match with a
DIFFERENT animal's scores is. These tests pin the thresholds, the scope (a
contested video takes its whole recording with it), the proven-death rule, the
difference between "not checked" and "contradicted" for the phase labels, and
the cohort being passed in rather than read off an animal id.
"""
import sqlite3

import pandas as pd
import pytest

from mousedb.exporters import export_exclusions as ee

DAY = "2026-03-10"


def _vector(seed, n=20):
    """A 20-pellet pattern with real variety, deterministic per seed.

    The multiplier on p must not be a multiple of 3, or every entry collapses
    to one value and the low-information guard (rightly) ignores the pattern.
    """
    return {p: (seed * 7 + p * 5) % 3 for p in range(1, n + 1)}


def _snapshot(tmp_path, videos, hand, records=None):
    """Write a minimal parquet snapshot.

    videos: list of (video_name, subject, date, tray_type, run_number, vector)
    hand:   list of (subject, date, tray_type, tray_number, vector)
    records: list of (subject, tab, record_no, {field: value})
    """
    rows = []
    for video, sid, d, tt, rn, vec in videos:
        for seg, code in vec.items():
            rows.append({"video_name": video, "subject_id": sid, "session_date": d,
                         "tray_type": tt, "run_number": rn, "segment_num": seg,
                         "segment_outcome": {0: "untouched", 1: "displaced_sa", 2: "retrieved"}[code]})
    pd.DataFrame(rows).to_parquet(tmp_path / "reach_data.parquet", index=False)
    prow = []
    for sid, d, tt, tn, vec in hand:
        for p, s in vec.items():
            prow.append({"subject_id": sid, "session_date": d, "tray_type": tt,
                         "tray_number": tn, "pellet_number": p, "score": s})
    pd.DataFrame(prow).to_parquet(tmp_path / "pellet_scores.parquet", index=False)
    arow = []
    for sid, tab, n, fields in (records or []):
        for f, v in fields.items():
            arow.append({"subject_id": sid, "source_tab": tab, "record_no": n,
                         "field": f, "value": v})
    pd.DataFrame(arow, columns=["subject_id", "source_tab", "record_no", "field", "value"]
                 ).to_parquet(tmp_path / "animal_records.parquet", index=False)
    return tmp_path


def _mismatch(vec, keep):
    """A vector agreeing with vec on `keep` of its entries."""
    out = dict(vec)
    for p in list(out)[keep:]:
        out[p] = (out[p] + 1) % 3
    return out


# ------------------------------------------------------------------ identity

def test_a_video_that_matches_another_animal_is_left_out(tmp_path):
    a, b = "CNT_01_01", "CNT_01_02"
    true_b = _vector(1)
    snap = _snapshot(tmp_path,
                     videos=[("20260310_CNT0101_P1", a, DAY, "P", 1, true_b)],
                     hand=[(a, DAY, "P", 1, _mismatch(true_b, 8)),
                           (b, DAY, "P", 1, true_b)])
    ex = ee.compute(snap)
    assert "20260310_CNT0101_P1" in ex.drop_videos
    assert ex.drop_videos["20260310_CNT0101_P1"].startswith("identity")
    assert b in ex.drop_videos["20260310_CNT0101_P1"]


def test_a_video_that_matches_its_own_animal_is_kept(tmp_path):
    a, b = "CNT_01_01", "CNT_01_02"
    own = _vector(2)
    snap = _snapshot(tmp_path,
                     videos=[("20260310_CNT0101_P1", a, DAY, "P", 1, own)],
                     hand=[(a, DAY, "P", 1, own), (b, DAY, "P", 1, _vector(5))])
    assert ee.compute(snap).drop_videos == {}


def test_a_disagreement_with_the_hand_scores_alone_is_never_evidence(tmp_path):
    """Hand scores are wrong routinely; only a near-perfect match elsewhere counts."""
    a, b = "CNT_01_01", "CNT_01_02"
    vec = _vector(3)
    snap = _snapshot(tmp_path,
                     videos=[("20260310_CNT0101_P1", a, DAY, "P", 1, vec)],
                     hand=[(a, DAY, "P", 1, _mismatch(vec, 5)),      # own: poor
                           (b, DAY, "P", 1, _mismatch(vec, 12))])    # other: also poor
    assert ee.compute(snap).drop_videos == {}


def test_a_low_information_pattern_is_never_used(tmp_path):
    """Twenty untouched pellets match every other all-zero tray; that proves nothing."""
    a, b = "CNT_01_01", "CNT_01_02"
    zeros = {p: 0 for p in range(1, 21)}
    snap = _snapshot(tmp_path,
                     videos=[("20260310_CNT0101_P1", a, DAY, "P", 1, zeros)],
                     hand=[(a, DAY, "P", 1, _vector(4)), (b, DAY, "P", 1, zeros)])
    assert ee.compute(snap).drop_videos == {}


def test_a_contested_video_takes_its_whole_recording_with_it(tmp_path):
    """The recording's typed name list assigned every identity in it."""
    a, b, c = "CNT_01_01", "CNT_01_02", "CNT_01_03"
    # seeds 6 and 7 give different patterns; 6 and 9 would not (7*6 and 7*9
    # are both multiples of 3), and an identical cagemate pattern would make
    # the match ambiguous -- which the guard is right to refuse
    true_b = _vector(6)
    snap = _snapshot(tmp_path,
                     videos=[("20260310_CNT0101_P1", a, DAY, "P", 1, true_b),
                             ("20260310_CNT0103_P1", c, DAY, "P", 1, _vector(7))],
                     hand=[(a, DAY, "P", 1, _mismatch(true_b, 6)),
                           (b, DAY, "P", 1, true_b),
                           (c, DAY, "P", 1, _vector(7))])
    pipeline = tmp_path / "pipe"
    pipeline.mkdir()
    con = sqlite3.connect(pipeline / "watcher.db")
    con.execute("CREATE TABLE collages (filename TEXT)")
    con.execute("INSERT INTO collages VALUES (?)",
                ("20260310_CNT0101,CNT0103_P1.mkv",))
    con.commit()
    con.close()
    ex = ee.compute(snap, pipeline_root=pipeline)
    assert "20260310_CNT0101_P1" in ex.drop_videos
    assert "20260310_CNT0103_P1" in ex.drop_videos, "the cagemate in the same recording"
    assert "recording" in ex.drop_videos["20260310_CNT0103_P1"]


# ---------------------------------------------------------------------- tray

def test_hand_scores_attached_to_the_wrong_tray_are_blanked_not_dropped(tmp_path):
    a = "CNT_01_01"
    t2 = _vector(7)
    snap = _snapshot(tmp_path,
                     videos=[("20260310_CNT0101_P1", a, DAY, "P", 1, t2)],
                     hand=[(a, DAY, "P", 1, _mismatch(t2, 7)), (a, DAY, "P", 2, t2)])
    ex = ee.compute(snap)
    assert ex.drop_videos == {}
    assert set(ex.blank_fields["20260310_CNT0101_P1"]) == set(ee.MANUAL_FIELDS)


# ----------------------------------------------------------------- collision

def test_one_video_name_from_two_recordings_is_left_out(tmp_path):
    a = "CNT_01_01"
    snap = _snapshot(tmp_path, videos=[("20260310_CNT0101_P1", a, DAY, "P", 1, _vector(1))],
                     hand=[(a, DAY, "P", 1, _vector(1))])
    pipeline = tmp_path / "pipe"
    pipeline.mkdir()
    con = sqlite3.connect(pipeline / "watcher.db")
    con.execute("CREATE TABLE collages (filename TEXT)")
    con.executemany("INSERT INTO collages VALUES (?)",
                    [("20260310_CNT0101,CNT0102_P1.mkv",), ("20260310_CNT0101,CNT0105_P1.mkv",)])
    con.commit()
    con.close()
    ex = ee.compute(snap, pipeline_root=pipeline)
    assert ex.drop_videos["20260310_CNT0101_P1"].startswith("collision")


# ------------------------------------------------------------------- archive

def test_a_recording_the_archive_cuts_name_differently_is_left_out_whole(tmp_path):
    # MouseReach's name audit found the archive's own cut of a seat named for
    # an animal the recording's name does not list: every video of that
    # recording is left out, whatever its pellets say.
    import json
    a = "ASPA_12_09"
    snap = _snapshot(tmp_path, videos=[("20221209_ASPA1209_P1", a, DAY, "P", 1, _vector(1))],
                     hand=[(a, DAY, "P", 1, _vector(1))])
    pipeline = tmp_path / "pipe"
    (pipeline / "pipeline_records").mkdir(parents=True)
    rec = "20221209_ASPA1209,ASPA1214,ASPA0003,ASPA0004,ASPA0005,ASPA0006,ASPA0007,ASPA0008_P1"
    (pipeline / "pipeline_records" / "archive_name_conflicts.json").write_text(json.dumps(
        {"conflicts": [{"recording": "20221209_L09,L14,003,004,005,006,007,008_P1.mkv",
                        "pipeline_recordings": [rec],
                        "cuts_named_differently": [{"animal": "L13"}]}]}), encoding="utf-8")
    ex = ee.compute(snap, pipeline_root=pipeline)
    assert ex.drop_videos["20221209_ASPA1209_P1"].startswith("archive")
    assert "named L13" in ex.drop_videos["20221209_ASPA1214_P1"]


def test_without_a_name_audit_record_the_archive_check_says_it_did_not_run(tmp_path):
    a = "CNT_01_01"
    snap = _snapshot(tmp_path, videos=[("20260310_CNT0101_P1", a, DAY, "P", 1, _vector(1))],
                     hand=[(a, DAY, "P", 1, _vector(1))])
    pipeline = tmp_path / "pipe"
    pipeline.mkdir()
    ex = ee.compute(snap, pipeline_root=pipeline)
    assert ex.drop_videos == {}
    assert any("archive-name check not run" in n for n in ex.notes)


# --------------------------------------------------------------------- death

def _contusion(sid, date, grams, survived):
    fields = {"Surgery_Date": date, "Survived": survived}
    if grams is not None:
        fields["Subject_Weight (g)"] = str(grams)
    return (sid, "4_Contusion_Injury_Details", 1, fields)


def test_reaches_after_a_proven_death_are_left_out(tmp_path):
    a = "CNT_01_01"
    snap = _snapshot(tmp_path,
                     videos=[("20260310_CNT0101_P1", a, DAY, "P", 1, _vector(1))],
                     hand=[(a, DAY, "P", 1, _vector(1))],
                     records=[_contusion(a, "2026-03-01", 22.4, "N")])
    ex = ee.compute(snap)
    assert ex.drop_videos["20260310_CNT0101_P1"].startswith("death")


def test_a_death_on_a_planned_surgery_proves_nothing(tmp_path):
    """Survived is pre-filled on planned rows and carried over by copying."""
    a = "CNT_01_01"
    snap = _snapshot(tmp_path,
                     videos=[("20260310_CNT0101_P1", a, DAY, "P", 1, _vector(1))],
                     hand=[(a, DAY, "P", 1, _vector(1))],
                     records=[_contusion(a, "2026-03-01", None, "N")])
    assert ee.compute(snap).drop_videos == {}


def test_a_proven_injury_date_is_collected_and_the_cohort_becomes_checkable(tmp_path):
    a = "CNT_01_01"
    snap = _snapshot(tmp_path, videos=[("20260310_CNT0101_P1", a, DAY, "P", 1, _vector(1))],
                     hand=[(a, DAY, "P", 1, _vector(1))],
                     records=[_contusion(a, "2026-03-01", 22.4, "Y")])
    ex = ee.compute(snap)
    assert ex.proven_injury[a] == "2026-03-01"
    assert "CNT_01" in ex.surgery_checkable


# --------------------------------------------------------------------- phase

def _analysis(rows):
    frame = pd.DataFrame(rows)
    frame.insert(0, "entry_index", range(1, len(frame) + 1))
    return frame


def test_phase_labels_are_blanked_only_where_they_contradict_proven_injury(tmp_path):
    ex = ee.Exclusions(proven_injury={"CNT_01_01": "2026-03-01"},
                       surgery_checkable={"CNT_01"})
    frame = _analysis([
        {"video_name": "v1", "subject_id": "CNT_01_01", "test_date": "2026-02-20",
         "experiment_stage": "After injury", "test_type_grouped": "Post_Injury_1",
         "test_type": "Post_Injury_1"},
    ])
    out, report = ee.apply(frame, ex, cohort_id="CNT_01")
    assert (out[ee.PHASE_FIELDS] == "NA").all().all()
    assert report[0]["left_out"] == "columns" and report[0]["cohort"] == "CNT_01"


def test_a_cohort_whose_workbook_cannot_be_checked_keeps_its_labels(tmp_path):
    """Not checked is not the same as contradicted -- the error that blanked
    every ASPA cohort's phases on the first real run."""
    ex = ee.Exclusions(surgery_checkable={"CNT_01"})
    frame = _analysis([
        {"video_name": "v1", "subject_id": "F01", "test_date": "2022-03-24",
         "experiment_stage": "After injury", "test_type_grouped": "Post_Injury_1",
         "test_type": "Post_Injury_1"},
    ])
    out, report = ee.apply(frame, ex, cohort_id="ASPA_06")
    assert out["experiment_stage"].iloc[0] == "After injury"
    assert report == []
    assert any("ASPA_06" in n and "not checked" in n for n in ex.notes)


def test_the_cohort_is_the_one_passed_in_never_read_off_an_animal_id(tmp_path):
    """Decoded ids like F01 carry no cohort; splitting them made every animal
    a cohort of its own."""
    ex = ee.Exclusions(proven_injury={}, surgery_checkable={"ASPA_06"})
    frame = _analysis([
        {"video_name": "v1", "subject_id": "F01", "test_date": "2022-03-24",
         "experiment_stage": "Before injury", "test_type_grouped": "Baseline",
         "test_type": "Pillar"},
        {"video_name": "v2", "subject_id": "F02", "test_date": "2022-03-24",
         "experiment_stage": "Before injury", "test_type_grouped": "Training",
         "test_type": "Pillar_Training"},
    ])
    out, report = ee.apply(frame, ex, cohort_id="ASPA_06")
    # a baseline label with no proven injury contradicts: the WHOLE cohort is
    # blanked, both animals, in one report line keyed by the cohort
    assert (out["experiment_stage"] == "NA").all()
    assert len(report) == 1 and report[0]["cohort"] == "ASPA_06"
    assert report[0]["subject_id"] == "ASPA_06"


# --------------------------------------------------------------------- apply

def test_dropped_videos_vanish_and_the_counter_is_renumbered():
    ex = ee.Exclusions(drop_videos={"v2": "identity: test"})
    frame = _analysis([
        {"video_name": "v1", "subject_id": "CNT_01_01", "test_date": DAY, "experiment_stage": "x"},
        {"video_name": "v2", "subject_id": "CNT_01_01", "test_date": DAY, "experiment_stage": "x"},
        {"video_name": "v3", "subject_id": "CNT_01_01", "test_date": DAY, "experiment_stage": "x"},
    ])
    out, report = ee.apply(frame, ex, cohort_id="CNT_01")
    assert list(out["video_name"]) == ["v1", "v3"]
    assert list(out["entry_index"]) == [1, 2]
    assert report[0]["rows"] == 1 and report[0]["video_name"] == "v2"


def test_blanked_columns_read_as_the_absent_word_and_the_row_stays():
    ex = ee.Exclusions(blank_fields={"v1": {"manual_retrieved": "tray: test"}})
    frame = _analysis([{"video_name": "v1", "subject_id": "CNT_01_01", "test_date": DAY,
                        "experiment_stage": "x", "manual_retrieved": 7.0}])
    out, report = ee.apply(frame, ex, cohort_id="CNT_01")
    assert len(out) == 1 and out["manual_retrieved"].iloc[0] == "NA"
    assert report[0]["fields"] == "manual_retrieved"


def test_thresholds_are_what_the_documentation_says():
    assert ee.BEST_AT_LEAST >= 0.95
    assert ee.OWN_AT_MOST <= 0.75
    assert ee.MIN_COMPARABLE >= 15


def test_dropping_then_blanking_raises_no_pandas_copy_warning():
    """Every nightly export log carried SettingWithCopyWarning: rows were
    dropped into a filtered view and columns then assigned on it (2026-10-22)."""
    import warnings
    from pandas.errors import SettingWithCopyWarning
    # surgery_checkable sends apply() through the phase check, which keeps a
    # reference to the frame -- the condition under which pandas warns.
    ex = ee.Exclusions(drop_videos={"v2": "test"},
                       blank_fields={"v1": {"manual_retrieved": "tray: test"}},
                       surgery_checkable={"CNT_01"})
    frame = _analysis([{"video_name": v, "subject_id": "CNT_01_01", "test_date": DAY,
                        "experiment_stage": "x", "manual_retrieved": 7.0}
                       for v in ("v1", "v2", "v1")])
    with warnings.catch_warnings():
        warnings.simplefilter("error", SettingWithCopyWarning)
        out, _ = ee.apply(frame, ex, cohort_id="CNT_01")
    assert list(out["entry_index"]) == [1, 2]
    assert list(out["manual_retrieved"]) == ["NA", "NA"]
