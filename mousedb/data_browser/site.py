"""What the data browser says: table and column descriptions, the front page,
the ready-made questions (canned queries) and Datasette's settings.

COLUMN DESCRIPTIONS COME FROM THE DATA DICTIONARIES. The per-reach and
hand-score columns are described by ``exporters.data_dictionary`` (REACH_DATA,
MANUAL_SCORES) -- the same text that goes beside the exported CSVs -- so a
column means one thing everywhere and a correction made there reaches the
browser too. Columns those dictionaries do not cover (the animal, weight,
surgery and brain tables, and the ready-made tables' totals) are described
here. A test checks that every column the browser can show has a description.
"""
from __future__ import annotations

import html
from datetime import datetime
from typing import Dict, Optional

from .build import ABOUT_TABLE, DB_NAME, BuildResult

TITLE = "MouseDB data browser"


def _dictionary_text(row: dict) -> str:
    text = row.get("Title", "").strip()
    desc = row.get("Description", "").strip()
    if desc and desc != text:
        text = "%s. %s" % (text, desc) if text else desc
    unit = row.get("Unit_of_Measure", "").strip()
    if unit:
        text += " (unit: %s)" % unit
    pvd = row.get("PVDescribed", "").strip()
    if pvd and pvd not in text:
        text += " Values: %s." % pvd
    return text


def _from_dictionary(rows) -> Dict[str, str]:
    return {r["VariableName"]: _dictionary_text(r) for r in rows}


_COHORT = "Cohort id (PROJECT_CC), e.g. the cohort of animal PROJECT_CC_SS."
_SUBJECT = "Animal id in PROJECT_CC_SS form (project, cohort number, subject number)."
_DERIVED_COHORT = ("Cohort id (PROJECT_CC). Taken from the animal's own id when no "
                   "sheet has named the animal yet.")

OWN = {
    "subjects": {
        "subject_id": _SUBJECT,
        "cohort_id": _COHORT,
        "date_of_birth": "Date of birth, from the tracking sheet.",
        "date_of_death": "Date of death, from the tracking sheet (empty while alive or unrecorded).",
        "sex": "Sex: M or F.",
        "ear_tag": "Ear tag, from the tracking sheet.",
        "notes": "Free-text notes about the animal.",
        "is_active": "1 = in the study, 0 = excluded or dead.",
        "created_at": "When the database first heard of this animal (sheet import or first video).",
    },
    "cohorts": {
        "cohort_id": _COHORT,
        "project_code": "Project the cohort belongs to.",
        "start_date": "First day of the cohort's timeline (food restriction start).",
        "num_mice": "Planned number of animals.",
        "notes": "Free-text notes.",
        "created_at": "When the cohort was created in the database.",
        "protocol_id": "Protocol assigned in mousedb's protocol builder (empty if none).",
        "protocol_version": "Version of that protocol when assigned.",
        "is_archived": "1 = archived (hidden from routine lists), 0 = active.",
        "archived_at": "When it was archived.",
        "archived_reason": "Why it was archived.",
    },
    "projects": {
        "project_code": "Project code (the letters that start every animal id).",
        "project_name": "Project name.",
        "description": "What the project is.",
        "created_at": "When the project was created in the database.",
    },
    "weights": {
        "id": "Database row identifier.",
        "subject_id": _SUBJECT,
        "date": "Date weighed.",
        "weight_grams": "Body weight (unit: g), from the tracking sheet.",
        "weight_percent": "Body weight as a percentage of free-feeding weight (unit: percent).",
        "notes": "Free-text notes.",
        "entered_by": "Who or what wrote the row (excel_import for the sheet importer).",
        "entered_at": "When the row was written to the database.",
    },
    "video_pellets": {
        "video_name": "Session video the pellet was presented in.",
        "segment_num": "Which pellet presentation (segment N holds pellet N).",
        "subject_id": _SUBJECT,
        "session_date": "Date of the recording.",
        "tray_type": "P = pillar, E = easy, F = flat.",
        "run_number": "Which run of that tray type in the session (1-4).",
        "outcome": ("What the pipeline (or a reviewer) decided happened to this pellet: "
                    "retrieved, displaced_sa (into the scoring area), displaced_outside, "
                    "untouched, or abnormal_exception (moved by something other than a reach)."),
        "outcome_confidence": "The outcome detector's confidence (0-1).",
        "outcome_flagged": "1 if the detector could not commit and asked for a person.",
        "n_reaches": "How many reaches were aimed at this pellet (0 = none).",
        "source_file": "The results file the row was imported from.",
        "imported_at": "When the row was written to the database.",
    },
    "animal_records": {
        "id": "Database row identifier.",
        "subject_id": _SUBJECT,
        "cohort_id": _COHORT,
        "source_tab": "Tab of the tracking sheet the value was copied from.",
        "record_no": "1 for the animal's first row on that tab, 2 for its second, and so on.",
        "field": "Column header on that tab, as written in the sheet.",
        "value": "The value, as written in the sheet (text).",
        "source_file": "The tracking sheet it was copied from.",
        "imported_at": "When it was imported.",
    },
    "surgeries": {
        "id": "Database row identifier.",
        "subject_id": _SUBJECT,
        "surgery_date": "Date of the surgery.",
        "surgery_type": "contusion, tracing or perfusion.",
        "pre_surgery_weight_grams": "Weight just before surgery (unit: g).",
        "force_kdyn": "Contusion: impact force (unit: kdyn).",
        "displacement_um": "Contusion: tissue displacement (unit: micrometers).",
        "velocity_mm_s": "Contusion: impactor velocity (unit: mm/s).",
        "dwell_time_s": "Contusion: dwell time (unit: s).",
        "virus_name": "Tracing: virus injected.",
        "virus_lot": "Tracing: virus lot.",
        "volume_nl": "Tracing: volume injected (unit: nL).",
        "injection_site": "Tracing: where it was injected.",
        "perfusion_solution": "Perfusion: solution used.",
        "perfusion_volume_ml": "Perfusion: volume (unit: mL).",
        "surgeon": "Who operated.",
        "anesthesia": "Anesthesia used.",
        "survived": "1 = survived the surgery, 0 = did not.",
        "notes": "Free-text notes.",
        "entered_by": "Who or what wrote the row.",
        "entered_at": "When the row was written to the database.",
    },
    "brain_samples": {
        "id": "Database row identifier (region counts point at it).",
        "subject_id": _SUBJECT,
        "brain_id": "Brain name: {BRAIN#}_{PROJECT}_{COHORT}_{SUBJECT}_{MAG}x_z{ZSTEP}.",
        "brain_number": "Sequential brain number.",
        "magnification": "Imaging magnification.",
        "z_step_um": "Z step (unit: micrometers).",
        "voxel_size_x_um": "Voxel size in X (unit: micrometers).",
        "voxel_size_y_um": "Voxel size in Y (unit: micrometers).",
        "voxel_size_z_um": "Voxel size in Z (unit: micrometers).",
        "atlas_name": "Atlas the brain was registered to.",
        "registration_date": "When it was registered.",
        "registration_quality": "Registration quality score (0-1).",
        "registration_approved": "1 = a person approved the registration, 0 = not yet.",
        "raw_path": "Raw image file, relative to the brain's folder.",
        "registered_path": "Registered data, relative to the brain's folder.",
        "detection_xml_path": "Cell detection output, relative to the brain's folder.",
        "status": "pending, registered, detected or analyzed.",
        "notes": "Free-text notes.",
        "created_at": "When the row was written to the database.",
    },
    "region_counts": {
        "id": "Database row identifier.",
        "brain_sample_id": "Which brain (brain_samples.id).",
        "region_id": "Allen Mouse Brain Atlas region id.",
        "region_name": "Region name (Allen Mouse Brain Atlas).",
        "region_acronym": "Region acronym (e.g. MOp).",
        "parent_region_id": "Parent region's id, for the hierarchy.",
        "hemisphere": "left, right or both.",
        "cell_count": "Cells counted in the region (after classification).",
        "cell_density": "Cells per cubic millimeter.",
        "region_volume_mm3": "Region volume (unit: cubic mm).",
        "cell_type": "Cell type counted ('all' unless classified further).",
        "calibration_run_id": "Detection settings used (calibration run id).",
        "is_final": "1 = final production count, 0 = a calibration count.",
        "confidence": "Confidence score (0-1).",
        "source_file": "File the counts were imported from.",
        "imported_at": "When the row was written to the database.",
    },
    "elife_region_counts": {
        "id": "Database row identifier.",
        "brain_sample_id": "Which brain (brain_samples.id).",
        "group_name": "One of the 25 functional groups of Wang et al. 2022 (eLife, doi:10.7554/eLife.76254).",
        "hemisphere": "left, right or both.",
        "cell_count": "Cells counted in the group (after classification).",
        "constituent_regions": "Atlas regions summed into the group (list of acronyms).",
        "calibration_run_id": "Detection settings used (calibration run id).",
        "is_final": "1 = final production count, 0 = a calibration count.",
        "source_file": "File the counts were imported from.",
        "imported_at": "When the row was written to the database.",
    },
    ABOUT_TABLE: {
        "table_name": "A table copied from the snapshot.",
        "rows": "How many rows it holds.",
        "snapshot_written": "When the nightly snapshot job wrote that table.",
        "copy_made": "When this private copy was made from the snapshot.",
    },
    "sessions": {
        "subject_id": _SUBJECT,
        "cohort_id": _DERIVED_COHORT,
        "session_date": "Testing day.",
        "test_phase": "Phase of the experiment that day belongs to (see pellet_scores).",
        "tray_type": "P = pillar, E = easy, F = flat. One row per tray type tested that day.",
        "weight_grams": "Body weight that day (unit: g), from the tracking sheet.",
        "weight_percent": "Body weight as a percentage of free-feeding weight (unit: percent).",
        "trays_hand_scored": "How many trays of this type have hand scores that day.",
        "pellets_hand_scored": "How many pellets have a hand score (normally 20 per tray).",
        "hand_retrieved": "Pellets hand-scored 2 (retrieved).",
        "hand_displaced": "Pellets hand-scored 1 (displaced).",
        "hand_untouched": "Pellets hand-scored 0 (untouched).",
        "videos": "Videos (trays) of this type with reaches in the database that day.",
        "pellets_in_videos": ("Pellets the pipeline cut those videos into (normally 20 per "
                              "video). Only pillar trays are cut into pellets."),
        "algo_retrieved": "Pellets the pipeline (or a reviewer) called retrieved.",
        "algo_displaced": "Pellets called displaced (into the scoring area or out of reach).",
        "algo_untouched": "Pellets called untouched.",
        "reaches": "Reaches detected in that day's videos of this tray type.",
    },
    "animals": {
        "subject_id": _SUBJECT,
        "cohort_id": _DERIVED_COHORT,
        "project_code": "Project the cohort belongs to.",
        "sex": "Sex: M or F (empty until the sheet records it).",
        "date_of_birth": "Date of birth, from the tracking sheet.",
        "date_of_death": "Date of death, from the tracking sheet.",
        "is_active": "1 = in the study, 0 = excluded or dead.",
        "injury_date": "Date of the contusion surgery (empty if none is recorded).",
        "injury_force_kdyn": "Contusion force (unit: kdyn).",
        "first_session": "First testing day with hand scores or videos.",
        "last_session": "Last testing day with hand scores or videos.",
        "testing_days": "Days with hand scores or videos.",
        "pellets_hand_scored": "Pellets with a hand score, all days.",
        "videos": "Videos with reaches in the database, all days.",
        "reaches": "Reaches in the database, all days.",
    },
    "brain_region_counts": {
        "subject_id": _SUBJECT, "cohort_id": _DERIVED_COHORT,
    },
    "brain_group_counts": {
        "subject_id": _SUBJECT, "cohort_id": _DERIVED_COHORT,
    },
    "reaches": {
        "cohort_id": _DERIVED_COHORT,
    },
}


def column_descriptions() -> Dict[str, Dict[str, str]]:
    """table -> column -> description, for every table the browser can show."""
    from ..exporters.data_dictionary import MANUAL_SCORES, REACH_DATA
    reach = _from_dictionary(REACH_DATA)
    scores = _from_dictionary(MANUAL_SCORES)
    out = {k: dict(v) for k, v in OWN.items()}
    out["reach_data"] = {**reach, "id": "Database row identifier.", "cohort_id": _DERIVED_COHORT + (
        " Added by the data browser; the database itself keeps only the animal id "
        "on each reach.")}
    out["pellet_scores"] = dict(scores)
    out["reaches"] = {**reach, **OWN["reaches"]}
    for view, base in (("brain_region_counts", "region_counts"),
                       ("brain_group_counts", "elife_region_counts")):
        out[view] = {**OWN[base], **OWN["brain_samples"], **OWN[view]}
    return out


TABLE_TEXT = {
    "animals": ("animals", "START HERE. One row per animal: cohort, sex, injury, and how "
                "much data the database holds for it."),
    "sessions": ("sessions", "One row per animal per testing day per tray type: weight, "
                 "hand scores and the pipeline's pellet results side by side."),
    "reaches": ("reaches", "One row per reach the pipeline detected, with the most-used "
                "measurements first. The full set is in reach_data."),
    "brain_region_counts": ("brain_region_counts",
                            "One row per brain per atlas region: cell counts."),
    "brain_group_counts": ("brain_group_counts",
                           "One row per brain per functional group (Wang et al. 2022)."),
    "reach_data": ("reach_data", "Every column the database holds for each reach "
                   "(the extended-features block is left out; it is in the exports)."),
    "pellet_scores": ("pellet_scores", "One row per pellet scored by hand from the tray: "
                      "0 untouched, 1 displaced, 2 retrieved."),
    "video_pellets": ("video_pellets", "One row per pellet the pipeline cut a video into, "
                      "with its outcome."),
    "weights": ("weights", "Body weights from the tracking sheets."),
    "subjects": ("subjects", "The animals as the tracking sheets record them."),
    "cohorts": ("cohorts", "The cohorts."),
    "projects": ("projects", "The projects."),
    "surgeries": ("surgeries", "Surgeries: contusion, tracing, perfusion."),
    "animal_records": ("animal_records", "Every animal-level value the tracking sheets "
                       "hold, one value per row, exactly as written."),
    "brain_samples": ("brain_samples", "The brains imaged, one row each."),
    "region_counts": ("region_counts", "Cell counts per atlas region (raw; see brain_region_counts)."),
    "elife_region_counts": ("elife_region_counts", "Cell counts per functional group (raw)."),
    ABOUT_TABLE: (ABOUT_TABLE, "When each table was written by the snapshot job, and when "
                  "this copy was made."),
}

START_HERE = ["animals", "sessions", "reaches", "brain_region_counts", "brain_group_counts"]

FACETS = {
    "animals": ["cohort_id", "sex"],
    "sessions": ["cohort_id", "tray_type", "test_phase"],
    "reaches": ["cohort_id", "tray_type", "segment_outcome"],
    "pellet_scores": ["tray_type", "test_phase", "score"],
    "brain_region_counts": ["brain_id", "is_final"],
    "brain_group_counts": ["brain_id", "is_final"],
}

QUERIES = {
    "one_animal": {
        "title": "One animal, day by day",
        "description": "Type an animal id (for example the one in the animals table) "
                       "and press Run. Every testing day, oldest first.",
        "sql": "SELECT * FROM sessions WHERE subject_id = :animal_id "
               "ORDER BY session_date, tray_type",
    },
    "one_day_reaches": {
        "title": "One animal's reaches on one day",
        "description": "Type an animal id and a date written YYYY-MM-DD, then press Run.",
        "sql": "SELECT * FROM reaches WHERE subject_id = :animal_id AND "
               "session_date = :date ORDER BY video_name, segment_num, reach_num",
    },
    "one_cohort_by_day": {
        "title": "One cohort, totalled per day",
        "description": "Type a cohort id (PROJECT_CC) and press Run: one row per testing "
                       "day and tray type, all animals added together.",
        "sql": "SELECT session_date, test_phase, tray_type, COUNT(*) AS animals, "
               "SUM(pellets_hand_scored) AS pellets_hand_scored, "
               "SUM(hand_retrieved) AS hand_retrieved, SUM(hand_displaced) AS hand_displaced, "
               "SUM(pellets_in_videos) AS pellets_in_videos, "
               "SUM(algo_retrieved) AS algo_retrieved, SUM(algo_displaced) AS algo_displaced, "
               "SUM(reaches) AS reaches FROM sessions WHERE cohort_id = :cohort_id "
               "GROUP BY session_date, test_phase, tray_type ORDER BY session_date, tray_type",
    },
}


def snapshot_line(built: BuildResult, now: Optional[datetime] = None) -> str:
    """The freshness sentence shown at the foot of every page."""
    from ..data_status import describe_age
    if built.snapshot_time is None:
        return "Snapshot time unknown."
    now = now or datetime.now()
    age = describe_age((now - built.snapshot_time).total_seconds() / 3600.0)
    return ("Copy of the snapshot written %s (%s before this window opened). "
            "Newer data appears after the next nightly snapshot: close the data "
            "browser and open it again."
            % (built.snapshot_time.isoformat(sep=" ", timespec="minutes"), age))


def front_page(present) -> str:
    items = []
    for t in START_HERE:
        if t in present:
            name, text = TABLE_TEXT[t]
            items.append('<li><a href="/%s/%s"><b>%s</b></a> -- %s</li>'
                         % (DB_NAME, name, name, html.escape(text)))
    items.append('<li><b>Ready-made questions</b> (a form, no typing of code): '
                 + ", ".join('<a href="/%s/%s">%s</a>' % (DB_NAME, q, html.escape(v["title"]))
                             for q, v in QUERIES.items()) + "</li>")
    return (
        "<p><b>Read-only.</b> This is your own copy of the database's nightly snapshot. "
        "Nothing you do here can change the database.</p>"
        "<p>Start with one of these:</p><ul>%s</ul>"
        "<p>On any table: click a column heading to sort; click a value in the "
        "<i>facets</i> at the top to keep only rows with that value; use the "
        "<i>Filter</i> row to choose a column, a test (equals, contains, &gt; ...) and a "
        "value, then <i>Apply</i>. <i>What each column means</i> opens the description "
        "of every column. To save what you are looking at, use the <b>CSV</b> link "
        "(tick <i>stream all rows</i> to get every matching row, not just this page).</p>"
        "<p>Reviews still waiting in MouseReach are not in the database; the "
        "<i>Where Is My Data</i> tab shows them.</p>" % "".join(items))


def metadata(built: BuildResult, present) -> dict:
    descriptions = column_descriptions()
    tables = {}
    for t in present:
        entry = {}
        if t in TABLE_TEXT:
            entry["description"] = TABLE_TEXT[t][1]
        cols = descriptions.get(t)
        if cols:
            entry["columns"] = cols
        if t in FACETS:
            entry["facets"] = FACETS[t]
        tables[t] = entry
    return {
        "title": TITLE,
        "source": snapshot_line(built),
        "databases": {
            DB_NAME: {
                "description_html": front_page(present),
                "tables": tables,
                "queries": {k: v for k, v in QUERIES.items()},
            },
        },
        "extra_js_urls": ["/mousedb-static/fold_descriptions.js"],
    }


SETTINGS = {
    # A filter or sort on the 800,000-row reach table takes about a second;
    # the default one-second limit would cut people off mid-question.
    "sql_time_limit_ms": 30000,
    "facet_time_limit_ms": 5000,
    # 0 = no size cap on "stream all rows" CSVs: saving what is on screen must
    # not silently stop at an arbitrary size.
    "max_csv_mb": 0,
    "default_page_size": 100,
    # No "download the whole database" link: the copy is a viewing aid, the
    # files to keep or send are the exports (docs/WHERE_IS_MY_DATA.md).
    "allow_download": False,
    "truncate_cells_html": 200,
    # Off: Datasette would otherwise try every column of a table, on every page
    # load, to suggest facets -- 4 to 9 seconds per click on the reach tables.
    # The useful facets are set per table (FACETS) and any column can still be
    # faceted from its heading's menu ("Facet by this").
    "suggest_facets": False,
}
