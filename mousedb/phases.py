"""
Phase derivation for cohort testing schedules.

Derives canonical (test_phase, phase_group) labels for each session date in a
cohort by detecting the structural pattern of testing days. The protocol creates
a recognizable shape:

    TRAINING_FLAT       tray=F, 2-3 consecutive days
      weekend gap
    TRAINING_PILLAR     tray=P, 4-5 consecutive days
      weekend gap
    PRE-INJURY_TEST     tray=P, 2-5 consecutive days
      ===== INJURY GAP (>=10 days, no testing) =====
    POST-INJURY TESTS   tray=P, isolated single days, ~7 days apart
      weekend gap
    REHAB_EASY          tray=E, ~5 consecutive days
    REHAB_FLAT          tray=F, ~4-5 consecutive days
    REHAB_PILLAR        tray=P, ~4 consecutive days

The injury gap and tray_type sequence are unambiguous structural anchors that
work regardless of how individual test days shifted by weekend/holiday. This
replaces the brittle TIMELINE-day-offset matching in
:func:`mousedb.schema.Cohort.get_phase_for_date`, which broke when actual test
dates didn't match the protocol's nominal day numbers.

Vocabulary follows the lab's Excel summary sheets (`archived_summaries.phase`)
with spaces replaced by underscores. The `phase_group` column carries the
stats-framework bucket (Baseline / Post_Injury_1 / Post_Injury_2-4 /
Post_Rehab_Test / etc.) so downstream analysis can `GROUP BY phase_group`
without recomputing buckets.
"""

from collections import Counter
from dataclasses import dataclass
from datetime import date
from typing import List, Optional, Sequence, Tuple

INJURY_GAP_THRESHOLD_DAYS = 10
PRE_INJURY_TEST_COUNT = 3
POST_REHAB_TEST_COUNT = 3


@dataclass(frozen=True)
class PhaseAssignment:
    """One phase label for one session date."""

    session_date: date
    test_phase: str
    phase_group: str


def _dedupe(test_dates: Sequence[Tuple[date, str]]) -> List[Tuple[date, str]]:
    """Collapse multiple (date, tray) entries per date to one dominant tray."""
    by_date: dict = {}
    for d, tray in test_dates:
        by_date.setdefault(d, []).append((tray or "").upper())
    out: List[Tuple[date, str]] = []
    for d in sorted(by_date):
        counts = Counter(by_date[d]).most_common()
        counts.sort(key=lambda x: (-x[1], x[0]))
        out.append((d, counts[0][0]))
    return out


def _find_injury_gap_index(dates: List[date]) -> Optional[int]:
    """Return index immediately after the injury gap; None if no such gap."""
    for i in range(1, len(dates)):
        if (dates[i] - dates[i - 1]).days >= INJURY_GAP_THRESHOLD_DAYS:
            return i
    return None


def _split_blocks(dates: List[date]) -> List[Tuple[int, int]]:
    """Group consecutive dates (gap <= 1 day) into (start, end_exclusive) ranges."""
    if not dates:
        return []
    blocks: List[Tuple[int, int]] = []
    start = 0
    for i in range(1, len(dates)):
        if (dates[i] - dates[i - 1]).days > 1:
            blocks.append((start, i))
            start = i
    blocks.append((start, len(dates)))
    return blocks


def _assign_pre_injury(
    indexed: List[Tuple[date, str]],
) -> List[PhaseAssignment]:
    """
    Assign labels for dates before the injury gap (or all dates if no gap yet).

    F-tray            -> Flat_Training      (group: Training)
    P-tray, last 3    -> Pillar             (group: Baseline)
    P-tray, earlier   -> Pillar_Training    (group: Training)
    other             -> Unscheduled        (group: Unscheduled)
    """
    p_indices = [i for i, (_, t) in enumerate(indexed) if t == "P"]
    pillar_test_idx = (
        set(p_indices[-PRE_INJURY_TEST_COUNT:])
        if len(p_indices) >= PRE_INJURY_TEST_COUNT
        else set(p_indices)
    )

    out: List[PhaseAssignment] = []
    for i, (d, tray) in enumerate(indexed):
        if tray == "F":
            out.append(PhaseAssignment(d, "Flat_Training", "Training"))
        elif tray == "P":
            if i in pillar_test_idx:
                out.append(PhaseAssignment(d, "Pillar", "Baseline"))
            else:
                out.append(PhaseAssignment(d, "Pillar_Training", "Training"))
        else:
            out.append(PhaseAssignment(d, "Unscheduled", "Unscheduled"))
    return out


def _assign_post_injury(
    indexed: List[Tuple[date, str]],
) -> List[PhaseAssignment]:
    """
    Assign labels for dates after the injury gap.

    Walks blocks in order. While we see leading single-day P-tray blocks they
    become Post_Injury_1, _2, ... Once a multi-day block (or non-P date)
    appears, switch to rehab labeling for the rest:
      E -> Rehab_Easy / Rehab_Easy
      F -> Rehab_Flat / Rehab_Flat
      P -> Rehab_Pillar; last POST_REHAB_TEST_COUNT P-dates get
           group=Post_Rehab_Test, earlier ones group=Rehab_Pillar_Early.
    """
    if not indexed:
        return []

    blocks = _split_blocks([d for d, _ in indexed])
    out: List[Optional[PhaseAssignment]] = [None] * len(indexed)
    post_injury_n = 0
    rehab_started = False

    for b_start, b_end in blocks:
        block = indexed[b_start:b_end]
        block_size = b_end - b_start
        all_p = all(t == "P" for _, t in block)

        if not rehab_started and block_size == 1 and all_p:
            post_injury_n += 1
            phase = f"Post_Injury_{post_injury_n}"
            group = "Post_Injury_1" if post_injury_n == 1 else "Post_Injury_2-4"
            out[b_start] = PhaseAssignment(indexed[b_start][0], phase, group)
        else:
            rehab_started = True
            for i in range(b_start, b_end):
                d, tray = indexed[i]
                if tray == "E":
                    out[i] = PhaseAssignment(d, "Rehab_Easy", "Rehab_Easy")
                elif tray == "F":
                    out[i] = PhaseAssignment(d, "Rehab_Flat", "Rehab_Flat")
                elif tray == "P":
                    out[i] = PhaseAssignment(d, "Rehab_Pillar", "Rehab_Pillar_Early")
                else:
                    out[i] = PhaseAssignment(d, "Unscheduled", "Unscheduled")

    rp_indices = [
        i for i, a in enumerate(out)
        if a is not None and a.test_phase == "Rehab_Pillar"
    ]
    last_n = (
        set(rp_indices[-POST_REHAB_TEST_COUNT:])
        if len(rp_indices) >= POST_REHAB_TEST_COUNT
        else set(rp_indices)
    )
    for i in last_n:
        d, _ = indexed[i]
        out[i] = PhaseAssignment(d, "Rehab_Pillar", "Post_Rehab_Test")

    return [a for a in out if a is not None]


def assign_phases_for_cohort(
    test_dates: Sequence[Tuple[date, str]],
) -> List[PhaseAssignment]:
    """
    Derive (test_phase, phase_group) for each session date in a cohort.

    Args:
        test_dates: iterable of (session_date, tray_type) for ALL test dates
            in one cohort. May contain duplicates (multiple subjects per date)
            or mixed tray types per date -- both are deduped.

    Returns:
        List of PhaseAssignment, one per unique date, sorted ascending.
    """
    indexed = _dedupe(test_dates)
    if not indexed:
        return []

    dates = [d for d, _ in indexed]
    gap_idx = _find_injury_gap_index(dates)

    if gap_idx is None:
        return _assign_pre_injury(indexed)

    return _assign_pre_injury(indexed[:gap_idx]) + _assign_post_injury(indexed[gap_idx:])
