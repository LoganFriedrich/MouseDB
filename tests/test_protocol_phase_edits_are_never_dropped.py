"""Editing a protocol's phases never ends in 'updated' while the phases are
thrown away.

WHY: the Protocol Manager's Save, on an existing protocol, saved only the name
and description and said "Protocol '<name>' updated." -- every phase edit made
in the table was silently discarded. The phases of an existing protocol are
never rewritten in place (cohorts already assigned to it were scheduled from
those phases), so a phase change is now offered as a NEW protocol, and the
message says exactly what was and was not saved.
"""
import os

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


@pytest.fixture(scope="module")
def qapp():
    from PyQt5.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


@pytest.fixture
def db(tmp_path):
    from mousedb.database import Database
    from mousedb import protocols
    d = Database(tmp_path / "t.db")
    d.init_db()
    with d.session() as s:
        p = protocols.create_protocol(s, "Protocol A", [
            {"phase_name": "Phase_1", "duration_days": 5, "sessions_per_day": 2,
             "phase_type": "training", "notes": "kept note", "expects_ladder": True},
            {"phase_name": "Phase_2", "duration_days": 3},
        ], description="first")
        d.protocol_id = p.id
    return d


@pytest.fixture
def ui(qapp, monkeypatch):
    from PyQt5.QtWidgets import QMessageBox

    class UI:
        boxes = []
        answer = QMessageBox.Yes

    def box(kind):
        def show(parent, title, text, *a, **k):
            UI.boxes.append((kind, title, text))
            return QMessageBox.Ok
        return staticmethod(show)

    for kind in ("warning", "information", "critical"):
        monkeypatch.setattr(QMessageBox, kind, box(kind))

    def question(parent, title, text, *a, **k):
        UI.boxes.append(("question", title, text))
        return UI.answer
    monkeypatch.setattr(QMessageBox, "question", staticmethod(question))
    UI.boxes = []
    return UI


def _open(db, monkeypatch, edited=None):
    """Open the manager on the protocol; ``edited`` is what the phase dialog
    returns when a person edits the first phase."""
    from mousedb.gui import protocol_builder as pb
    dlg = pb.ProtocolBuilderDialog(db)
    dlg.protocols_list.setCurrentRow(0)
    if edited is not None:
        class FakePhaseDialog:
            def __init__(self, *a, **k):
                self.phase_data = k.get("phase_data") or {}
            def exec_(self):
                return True
            def get_phase_data(self):
                d = dict(self.phase_data)
                d.update(edited)
                return d
        monkeypatch.setattr(pb, "PhaseEditDialog", FakePhaseDialog)
        dlg.phases_table.selectRow(0)
        dlg._edit_phase()
    return dlg


def _phases(db, pid):
    from mousedb import protocols
    with db.session() as s:
        return protocols.get_effective_phases(s, pid)


def _all(db):
    from mousedb.schema import Protocol
    with db.session() as s:
        return [(p.id, p.name) for p in s.query(Protocol).order_by(Protocol.id)]


def test_a_phase_edit_is_saved_as_a_new_protocol_when_the_person_says_yes(db, ui, monkeypatch):
    dlg = _open(db, monkeypatch, edited={"duration_days": 9})
    dlg.proto_name_edit.setText("Protocol A longer")
    dlg._save_protocol()
    # the original is untouched
    assert [p["duration_days"] for p in _phases(db, db.protocol_id)] == [5, 3]
    assert _all(db)[0][1] == "Protocol A"
    # the edit landed as a new protocol
    (new_id, new_name), = [r for r in _all(db) if r[0] != db.protocol_id]
    assert new_name == "Protocol A longer"
    new = _phases(db, new_id)
    assert [p["duration_days"] for p in new] == [9, 3]
    # fields the table does not show are carried over, not lost
    assert new[0]["phase_type"] == "training"
    assert new[0]["notes"] == "kept note"
    assert new[0]["expects_ladder"] is True
    assert not any("updated" in text for _, _, text in ui.boxes)


def test_a_phase_edit_the_person_declines_saves_nothing_and_says_so(db, ui, monkeypatch):
    from PyQt5.QtWidgets import QMessageBox
    ui.answer = QMessageBox.No
    dlg = _open(db, monkeypatch, edited={"duration_days": 9})
    dlg.proto_name_edit.setText("Protocol A renamed")
    dlg._save_protocol()
    assert _all(db) == [(db.protocol_id, "Protocol A")]
    assert [p["duration_days"] for p in _phases(db, db.protocol_id)] == [5, 3]
    assert not any("updated" in text for _, _, text in ui.boxes)
    assert any("not saved" in text.lower() for _, _, text in ui.boxes)


def test_a_change_to_a_field_the_table_does_not_show_counts_as_a_phase_edit(db, ui, monkeypatch):
    dlg = _open(db, monkeypatch, edited={"expects_surgery": True})
    dlg.proto_name_edit.setText("Protocol A surgery")
    dlg._save_protocol()
    assert len(_all(db)) == 2
    assert not any("updated" in text for _, _, text in ui.boxes)


def test_removing_a_phase_counts_as_a_phase_edit(db, ui, monkeypatch):
    dlg = _open(db, monkeypatch)
    dlg.phases_table.removeRow(1)
    dlg.proto_name_edit.setText("Protocol A short")
    dlg._save_protocol()
    new_id = [r for r in _all(db) if r[0] != db.protocol_id][0][0]
    assert [p["phase_name"] for p in _phases(db, new_id)] == ["Phase_1"]
    assert _phases(db, new_id)[0]["notes"] == "kept note"   # untouched row kept whole
    assert [p["phase_name"] for p in _phases(db, db.protocol_id)] == ["Phase_1", "Phase_2"]


def test_a_name_only_change_still_updates_in_place(db, ui, monkeypatch):
    dlg = _open(db, monkeypatch)
    dlg.proto_name_edit.setText("Protocol A2")
    dlg._save_protocol()
    assert _all(db) == [(db.protocol_id, "Protocol A2")]
    assert any("updated" in text for _, _, text in ui.boxes)


def test_a_new_protocol_cannot_take_the_name_of_an_existing_one(db, ui, monkeypatch):
    """Two protocols with one name would be told apart only by their id."""
    dlg = _open(db, monkeypatch, edited={"duration_days": 9})
    dlg._save_protocol()          # name left as "Protocol A"
    assert _all(db) == [(db.protocol_id, "Protocol A")]
    assert not any("updated" in text for _, _, text in ui.boxes)


def test_the_phases_table_cannot_be_typed_into(db, ui, monkeypatch):
    """Every phase change goes through Edit Phase, where it is kept whole."""
    from PyQt5.QtWidgets import QAbstractItemView
    dlg = _open(db, monkeypatch)
    assert dlg.phases_table.editTriggers() == QAbstractItemView.NoEditTriggers


def test_a_changed_cell_is_never_dropped_while_save_says_updated(db, ui, monkeypatch):
    """A cell whose text changed (however it changed) is what Save compares
    and saves -- never the stored value behind it."""
    dlg = _open(db, monkeypatch)
    dlg.phases_table.item(0, 2).setText("9")      # Days of the first phase
    dlg.proto_name_edit.setText("Protocol A nine days")
    dlg._save_protocol()
    assert not any("updated" in text for _, _, text in ui.boxes)
    assert any(kind == "question" for kind, _, _ in ui.boxes)
    assert [p["duration_days"] for p in _phases(db, db.protocol_id)] == [5, 3]
    new_id = [r for r in _all(db) if r[0] != db.protocol_id][0][0]
    new = _phases(db, new_id)
    assert [p["duration_days"] for p in new] == [9, 3]
    assert new[0]["notes"] == "kept note"
