"""Where Is My Data says what still exists only on an old storage root.

WHY (2026-10-22): MouseReach's legacy inventory found recordings that exist only
on a retired storage root, where no watcher looks; the root cannot be retired
while any are there, and nothing a person reads said so. The status reads the
inventory's summary files and shows one line per root. Synthetic paths only.
"""
import json

from mousedb.data_status import old_storage_lines, read_old_storage


def _summary(folder, label, **fields):
    folder.mkdir(parents=True, exist_ok=True)
    d = {"old_root": r"\\OLDHOST\share\old pipeline", "written_at": "2026-10-22T02:44:33",
         "only_here": 120, "only_here_bytes": 4972970490,
         "for_a_person_by_folder": {"Unanalyzed": 23, "Quarantine": 8}}
    d.update(fields)
    (folder / f"{label}.summary.json").write_text(json.dumps(d), encoding="utf-8")


def test_one_line_per_old_root_with_what_exists_only_there(tmp_path):
    folder = tmp_path / "legacy_inventory"
    _summary(folder, "OLD")
    rows = read_old_storage(folder)
    assert rows == [{"label": "OLD", "old_root": r"\\OLDHOST\share\old pipeline",
                     "written_at": "2026-10-22T02:44:33", "only_here": 120,
                     "only_here_gb": 5.0, "for_a_person": 31,
                     "report": str(folder / "OLD.csv")}]
    (line,) = old_storage_lines(rows)
    assert "120 recording(s), 5.0 GB, exist ONLY there" in line
    assert "31 more need a person's look" in line
    assert "OLD.csv" in line


def test_an_unreadable_summary_is_said_not_dropped(tmp_path):
    folder = tmp_path / "legacy_inventory"
    folder.mkdir()
    (folder / "BAD.summary.json").write_text("{not json", encoding="utf-8")
    rows = read_old_storage(folder)
    assert rows[0]["label"] == "BAD" and "error" in rows[0]
    assert "summary unreadable" in old_storage_lines(rows)[0]


def test_no_inventory_folder_is_no_lines(tmp_path):
    assert read_old_storage(tmp_path / "absent") == []
    assert old_storage_lines([]) == []
