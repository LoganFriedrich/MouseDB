"""Every per-reach row carries four levels, under names that say which level.

Owner's rule (2026-11-19): a per-reach row is one reach and repeats its segment
(one pellet), session (one tray = one video) and day (the animal's date). What
THIS reach did is its own column on every reach: naming the reach that moved a
pellet answers every other reach at it (they missed); every reach at an
untouched pellet missed. The contact column cannot contradict the reach
outcome. Tray and day totals of retrieved / displaced / untouched, from the
algorithm and the hand scores, sit on every row. See exporters.levels.
"""
import pandas as pd

from mousedb.exporters import analysis_reaches as ar
from mousedb.exporters import analysis_sessions

from mousedb.exporters import odc_reaches as ox
from mousedb.exporters import reach_summary

SID = "PROJA_01_01"
DAY = "2025-07-10"


def _reaches(video, run, segments, date=DAY, tray="P"):
    """segments: [(segment_num, segment_outcome, [causal flag per reach])]"""
    rows = []
    for seg, outcome, flags in segments:
        for k, causal in enumerate(flags, 1):
            rows.append({"subject_id": SID, "video_name": video, "session_date": date,
                         "tray_type": tray, "run_number": run, "segment_num": seg,
                         "reach_id": 100 * seg + k, "reach_num": k,
                         "outcome": outcome if causal else None,
                         "causal_reach": int(causal), "segment_outcome": outcome,
                         # A person-picked causal reach has no contact frame.
                         "interaction_frame": None,
                         "contact_group": "missed", "segment_contact_group": None,
                         "attention_score": 0.5, "test_phase": "Pillar",
                         "phase_group": "Baseline", "start_frame": 10 * k,
                         "end_frame": 10 * k + 5, "extended_features": "{}"})
    return rows


V1 = "20250710_PROJA0101_P1"
V2 = "20250710_PROJA0101_P2"


def _reach_table():
    rows = _reaches(V1, 1, [
        (1, "retrieved", [0, 1, 0]),          # credited: reach 2
        (2, "untouched", [0, 0]),             # nothing moved it
        (3, "displaced_sa", [0, 0]),          # moved, NO reach credited
    ])
    rows += _reaches(V2, 2, [(1, "displaced_sa", [1, 0])])
    return pd.DataFrame(rows)


def _video_pellets():
    out = []
    for video, run, outs in ((V1, 1, ["retrieved", "untouched", "displaced_sa"] + ["untouched"] * 17),
                             (V2, 2, ["displaced_sa"] + ["retrieved"] * 2 + ["untouched"] * 17)):
        for s, o in enumerate(outs, 1):
            out.append({"video_name": video, "segment_num": s, "subject_id": SID,
                        "session_date": DAY, "tray_type": "P", "run_number": run,
                        "outcome": o})
    return pd.DataFrame(out)


def _scores():
    rows = []
    for tray, scores in ((1, [2, 0, 1] + [0] * 17), (2, [1, 2, 2, 2] + [0] * 16)):
        for p, sc in enumerate(scores, 1):
            rows.append({"subject_id": SID, "session_date": DAY, "tray_type": "P",
                         "tray_number": tray, "pellet_number": p, "score": sc})
    # An easy tray the same day: not counted in the pillar day totals.
    for p in range(1, 21):
        rows.append({"subject_id": SID, "session_date": DAY, "tray_type": "E",
                     "tray_number": 1, "pellet_number": p, "score": 2})
    return pd.DataFrame(rows)


def _build():
    subjects = pd.DataFrame([{"subject_id": SID, "sex": "F", "date_of_birth": None}])
    vp = _video_pellets()
    # The easy tray is filed too (its reach rows), so the day is complete.
    reach = pd.concat([_reach_table(), pd.DataFrame(_reaches(
        "20250710_PROJA0101_E1", 1, [(1, None, [0])], tray="E"))], ignore_index=True)
    return ox.build_cohort("PROJA_01", reach, subjects, None, _scores(),
                           lambda sid: {}, "lab-x", video_pellets=vp)


def _row(df, video, seg, reach):
    m = (df["video_name"] == video) & (df["segment_num"] == seg) & (df["reach_num"] == reach)
    assert m.sum() == 1
    return df[m].iloc[0]


def test_every_reach_says_what_it_did():
    df, _ = _build()
    assert _row(df, V1, 1, 2)["outcome"] == "retrieved"
    assert _row(df, V1, 1, 1)["outcome"] == "missed"
    assert _row(df, V1, 1, 3)["outcome"] == "missed"
    # Untouched pellet: every reach missed.
    assert _row(df, V1, 2, 1)["outcome"] == "missed"
    # Moved and no reach credited: not known, never "missed".
    assert pd.isna(_row(df, V1, 3, 1)["outcome"])


def test_the_contact_column_follows_the_reach_outcome():
    df, _ = _build()
    # A person-picked causal reach has no contact frame and still met the pellet.
    assert _row(df, V1, 1, 2)["contact_group"] == "contacted"
    assert _row(df, V1, 1, 1)["contact_group"] == "missed"
    assert pd.isna(_row(df, V1, 3, 2)["contact_group"])
    pillar = df[df["tray_type"] == "P"]
    moved = pillar["outcome"].isin(("retrieved", "displaced_sa", "displaced_outside"))
    assert (pillar.loc[moved, "contact_group"] == "contacted").all()
    assert (pillar.loc[pillar["outcome"] == "missed", "contact_group"] == "missed").all()


def test_tray_untouched_counts():
    df, _ = _build()
    r = _row(df, V1, 1, 1)
    assert r["Video_Pellets"] == 20 and r["Video_Contacted"] == 2
    assert r["Session_Untouched_AI"] == 18
    assert r["Session_Untouched_Hand"] == 18


def test_day_totals_on_every_row_both_streams_pillar_only():
    df, _ = _build()
    for _, r in df.iterrows():
        assert r["Day_Total_Reaches_Complete"] == "yes"
        assert r["Day_Pellets_AI"] == 40
        assert r["Day_Retrieved_AI"] == 3          # V1 1 + V2 2
        assert r["Day_Displaced_AI"] == 2
        assert r["Day_Contacted_AI"] == 5
        assert r["Day_Untouched_AI"] == 35
        # Hand: tray1 one 2, one 1; tray2 one 1, three 2; easy tray not counted.
        assert r["Day_Pellets_Hand"] == 40
        assert r["Day_Retrieved_Hand"] == 4
        assert r["Day_Displaced_Hand"] == 2
        assert r["Day_Contacted_Hand"] == 6
        assert r["Day_Untouched_Hand"] == 34


def test_an_incomplete_day_gives_no_algorithm_day_totals():
    subjects = pd.DataFrame([{"subject_id": SID, "sex": "F", "date_of_birth": None}])
    vp = _video_pellets()
    vp = vp[vp["video_name"] == V1]
    reach = _reach_table()
    reach = reach[reach["video_name"] == V1]
    df, _ = ox.build_cohort("PROJA_01", reach, subjects, None, _scores(),
                            lambda sid: {}, "lab-x", video_pellets=vp)
    assert (df["Day_Total_Reaches_Complete"] == "no").all()
    assert df["Day_Pellets_AI"].isna().all() and df["Day_Retrieved_AI"].isna().all()
    # The hand scores are complete at the bench whatever is filed.
    assert (df["Day_Retrieved_Hand"] == 4).all()


def test_shareable_and_analysis_reach_outcome_undetermined_when_no_reach_credited():
    df, _ = _build()
    summary, _ = reach_summary.build(df)
    s = summary.set_index(["session_video", "pellet_number", "reach_number_in_pellet"])
    assert s.loc[(V1, 3, 1), "reach_result"] == "Undetermined"
    assert s.loc[(V1, 1, 1), "reach_result"] == "Missed"
    assert s.loc[(V1, 1, 2), "reach_result"] == "Retrieved"
    analysis, _ = ar.build(df)
    a = analysis.set_index(["video_name", "pellet_number", "swipe_id"])
    assert a.loc[(V1, 3, 2), "reach_outcome"] == "Undetermined"
    assert a.loc[(V1, 2, 1), "reach_outcome"] == "Missed"


def _published_names(frame):
    return set(frame.columns)


def test_published_names_are_level_first(tmp_path):
    df, rows = _build()
    entry, label = ox.write_cohort(tmp_path, "PROJA_01", df, rows)
    files = {p.name: p for p in tmp_path.rglob("*.csv")}
    complete = pd.read_csv(files["ODC_reaches_PROJA_01.csv"], dtype=str)
    for name in ("reach_outcome", "reach_causal", "reach_contact_group", "Session_Pellets_AI",
                 "Session_Untouched_AI", "Session_Untouched_Hand", "Session_Retrieved_Hand",
                 "Day_Reaches_AI", "Day_Complete", "Day_Retrieved_AI", "Day_Untouched_Hand"):
        assert name in complete.columns, name
    for old in ("outcome", "causal_reach", "contact_group", "Video_Pellets", "Manual_Pellets",
                "Day_Total_Reaches_AI"):
        assert old not in complete.columns, old
    # No "Not recorded" for a reach that missed.
    r = complete[(complete["video_name"] == V1) & (complete["segment_num"] == "1")]
    assert set(r["reach_outcome"]) == {"retrieved", "missed"}
    u = complete[(complete["video_name"] == V1) & (complete["segment_num"] == "3")]
    assert set(u["reach_outcome"]) == {"Undetermined"}

    analysis = pd.read_csv(files["reaches_PROJA_01_analysis.csv"], dtype=str)
    for name in ("segment_number", "reach_number", "segment_outcome", "reach_outcome",
                 "session_pellets_ai", "session_untouched_ai", "session_retrieved_hand",
                 "session_untouched_hand", "session_reaches_ai", "day_reaches_ai",
                 "day_complete", "day_retrieved_ai", "day_untouched_hand"):
        assert name in analysis.columns, name
    for old in ("pellet_number", "swipe_id", "pellet_outcome", "pellets_in_video",
                "manual_retrieved", "day_total_reaches", "reaches_in_video"):
        assert old not in analysis.columns, old
    # Identity columns that scripts and limited exports find rows by stay.
    for key in ("subject_id", "test_date", "tray_type", "video_name"):
        assert key in analysis.columns
    sessions = pd.read_csv(files["sessions_PROJA_01_analysis.csv"], dtype=str)
    assert {"session_reaches_ai", "session_pellets_presented", "session_percent_untouched_ai",
            "day_retrieved_hand"} <= set(sessions.columns)
    summary = pd.read_csv(files["reaches_PROJA_01_summary.csv"], dtype=str)
    assert {"segment_number", "reach_number", "reach_outcome", "segment_outcome",
            "session_untouched_ai", "day_retrieved_ai"} <= set(summary.columns)

    # Every published column has a dictionary row under its published name.
    for key, frame in (("_complete_dictionary", complete), ("_analysis_dictionary", analysis),
                       ("_sessions_dictionary", sessions), ("_summary_dictionary", summary)):
        named = {r["VariableName"] for r in entry[key]}
        assert set(frame.columns) <= named, (key, set(frame.columns) - named)


def test_dictionary_no_longer_claims_untouched_pellets_have_a_causal_reach():
    from mousedb.exporters import data_dictionary as dd
    text = [r for r in dd.REACH_DATA if r["VariableName"] == "causal_reach"][0]["Description"]
    assert "untouched pellet" not in text or "every reach of an untouched pellet reads 0" in text
    assert "last reach at an untouched pellet" not in text


# --- 2026-11-19 follow-up (owner's decisions) ---------------------------------
# Every per-reach measurement starts with reach_, in every per-reach file and
# dictionary; the animal-day file is ODC_days_<cohort>.csv, and a saved
# limited-export definition naming the old kind ODC_sessions keeps working.

def test_every_reach_measurement_is_prefixed(tmp_path):
    df, rows = _build()
    entry, label = ox.write_cohort(tmp_path, "PROJA_01", df, rows)
    files = {p.name: p for p in tmp_path.rglob("*.csv")}
    complete = pd.read_csv(files["ODC_reaches_PROJA_01.csv"], nrows=1)
    analysis = pd.read_csv(files["reaches_PROJA_01_analysis.csv"], nrows=1)
    summary = pd.read_csv(files["reaches_PROJA_01_summary.csv"], nrows=1)
    for frame, names in (
            (complete, ("reach_start_frame", "reach_end_frame", "reach_interaction_frame")),
            (analysis, ("reach_start_frame", "reach_end_frame", "reach_swipe_length",
                        "reach_interaction_frame")),
            (summary, ("reach_start_frame", "reach_end_frame"))):
        for n in names:
            assert n in frame.columns, n
        assert not {"start_frame", "end_frame", "swipe_length", "interaction_frame"} & set(frame.columns)
    # Identity and higher levels keep theirs; nothing gets reach_ twice.
    assert {"subject_id", "video_name", "test_date", "session_id"} <= set(analysis.columns)
    assert not [c for c in analysis.columns if c.startswith("reach_reach_")]
    assert "reach_session_pellets_ai" not in analysis.columns
    # The dictionaries carry the new names and say what each was called before.
    by = {r["VariableName"]: r for r in entry["_analysis_dictionary"]}
    assert "Named swipe_length in files written before" in by["reach_swipe_length"]["Comments"]
    assert {r["VariableName"] for r in entry["_complete_dictionary"]} >= set(complete.columns)


def test_a_saved_definition_naming_odc_sessions_is_read_as_odc_days():
    from mousedb.exporters import subsets, layout
    assert "ODC_days" in layout.KINDS and "ODC_sessions" not in layout.KINDS
    assert layout.file_name("ODC_days", "PROJA_01") == "ODC_days_PROJA_01.csv"
    clean = subsets.validate("x", {"cohorts": ["PROJA_01"],
                                   "kinds": ["ODC_reaches", "ODC_sessions"],
                                   "columns": {"ODC_sessions": ["Animal", "Date"]}}, rules={})
    assert clean["kinds"] == ["ODC_reaches", "ODC_days"]
    assert clean["columns"] == {"ODC_days": ["Animal", "Date"]}


def test_files_under_the_old_day_name_are_archived_once_replaced(tmp_path):
    from mousedb.exporters import layout
    cohort = tmp_path / "current" / "PROJA" / "PROJA_01"
    cohort.mkdir(parents=True)
    root = tmp_path / "current"
    (cohort / "ODC_sessions_PROJA_01.csv").write_text("a\n1\n")
    (cohort / "ODC_days_PROJA_01.csv").write_text("a\n1\n")
    (root / "ODC_sessions_DATA_DICTIONARY.csv").write_text("x\n")
    (root / "ODC_days_DATA_DICTIONARY.csv").write_text("x\n")
    (cohort / "ODC_sessions_PROJA_02.csv").write_text("a\n")   # no replacement: kept
    moved = layout.archive_renamed_kinds(root)
    assert len(moved) == 2
    assert not (cohort / "ODC_sessions_PROJA_01.csv").exists()
    assert not (root / "ODC_sessions_DATA_DICTIONARY.csv").exists()
    assert (cohort / "ODC_sessions_PROJA_02.csv").exists()
    assert (cohort / "ODC_days_PROJA_01.csv").exists()
