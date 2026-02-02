"""
Protocol Builder Dialog for creating and managing behavioral testing protocols.

This dialog allows users to:
- View and edit existing protocols
- Create new protocols with phases
- Manage tray types (physical apparatus)
- Create protocol variants
"""

from datetime import date, timedelta
from typing import Optional, List, Dict, Any

from PyQt5.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QGridLayout, QFormLayout,
    QWidget, QLabel, QPushButton, QComboBox, QSpinBox, QDoubleSpinBox,
    QLineEdit, QTextEdit, QPlainTextEdit, QCheckBox, QGroupBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QTabWidget,
    QMessageBox, QSplitter, QFrame, QScrollArea, QSizePolicy,
    QDialogButtonBox, QListWidget, QListWidgetItem
)
from PyQt5.QtCore import Qt, pyqtSignal
from PyQt5.QtGui import QColor, QFont

from ..schema import Protocol, ProtocolPhase, TrayType
from .. import protocols


class ProtocolBuilderDialog(QDialog):
    """
    Dialog for creating and managing behavioral testing protocols.
    """

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.setWindowTitle("Protocol Manager")
        self.setMinimumSize(900, 600)
        self._setup_ui()
        self._refresh_all()

    def _setup_ui(self):
        layout = QVBoxLayout(self)

        # Create tab widget for different sections
        tabs = QTabWidget()

        # Tab 1: Protocols
        protocols_tab = QWidget()
        self._setup_protocols_tab(protocols_tab)
        tabs.addTab(protocols_tab, "Protocols")

        # Tab 2: Tray Types
        tray_types_tab = QWidget()
        self._setup_tray_types_tab(tray_types_tab)
        tabs.addTab(tray_types_tab, "Tray Types")

        layout.addWidget(tabs)

        # Close button
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        layout.addWidget(close_btn)

    def _setup_protocols_tab(self, parent):
        """Set up the Protocols management tab."""
        layout = QHBoxLayout(parent)

        # Left panel: Protocol list
        left_panel = QVBoxLayout()

        left_panel.addWidget(QLabel("Existing Protocols:"))

        self.protocols_list = QListWidget()
        self.protocols_list.currentItemChanged.connect(self._on_protocol_selected)
        left_panel.addWidget(self.protocols_list)

        # Protocol action buttons
        proto_actions = QHBoxLayout()
        self.new_protocol_btn = QPushButton("New")
        self.new_protocol_btn.clicked.connect(self._create_new_protocol)
        proto_actions.addWidget(self.new_protocol_btn)

        self.delete_protocol_btn = QPushButton("Delete")
        self.delete_protocol_btn.clicked.connect(self._delete_protocol)
        self.delete_protocol_btn.setEnabled(False)
        proto_actions.addWidget(self.delete_protocol_btn)

        left_panel.addLayout(proto_actions)

        left_widget = QWidget()
        left_widget.setLayout(left_panel)
        left_widget.setMaximumWidth(250)
        layout.addWidget(left_widget)

        # Right panel: Protocol details and phase editor
        right_panel = QVBoxLayout()

        # Protocol details
        details_group = QGroupBox("Protocol Details")
        details_layout = QFormLayout()

        self.proto_name_edit = QLineEdit()
        self.proto_name_edit.setPlaceholderText("Protocol name")
        details_layout.addRow("Name:", self.proto_name_edit)

        self.proto_desc_edit = QPlainTextEdit()
        self.proto_desc_edit.setMaximumHeight(60)
        self.proto_desc_edit.setPlaceholderText("Description...")
        details_layout.addRow("Description:", self.proto_desc_edit)

        self.proto_version_label = QLabel("-")
        details_layout.addRow("Version:", self.proto_version_label)

        details_group.setLayout(details_layout)
        right_panel.addWidget(details_group)

        # Phases section
        phases_group = QGroupBox("Protocol Phases")
        phases_layout = QVBoxLayout()

        # Phases table
        self.phases_table = QTableWidget()
        self.phases_table.setColumnCount(8)
        self.phases_table.setHorizontalHeaderLabels([
            "Order", "Phase Name", "Days", "Tray", "Sessions/Day",
            "Weekends", "Pellets", "Weights"
        ])
        self.phases_table.horizontalHeader().setStretchLastSection(True)
        self.phases_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.phases_table.setSelectionBehavior(QTableWidget.SelectRows)
        phases_layout.addWidget(self.phases_table)

        # Phase action buttons
        phase_actions = QHBoxLayout()
        self.add_phase_btn = QPushButton("Add Phase")
        self.add_phase_btn.clicked.connect(self._add_phase)
        self.add_phase_btn.setEnabled(False)
        phase_actions.addWidget(self.add_phase_btn)

        self.edit_phase_btn = QPushButton("Edit Phase")
        self.edit_phase_btn.clicked.connect(self._edit_phase)
        self.edit_phase_btn.setEnabled(False)
        phase_actions.addWidget(self.edit_phase_btn)

        self.remove_phase_btn = QPushButton("Remove Phase")
        self.remove_phase_btn.clicked.connect(self._remove_phase)
        self.remove_phase_btn.setEnabled(False)
        phase_actions.addWidget(self.remove_phase_btn)

        phase_actions.addStretch()

        self.save_protocol_btn = QPushButton("Save Protocol")
        self.save_protocol_btn.clicked.connect(self._save_protocol)
        self.save_protocol_btn.setEnabled(False)
        self.save_protocol_btn.setStyleSheet("background-color: #4CAF50; color: white;")
        phase_actions.addWidget(self.save_protocol_btn)

        phases_layout.addLayout(phase_actions)

        phases_group.setLayout(phases_layout)
        right_panel.addWidget(phases_group)

        right_widget = QWidget()
        right_widget.setLayout(right_panel)
        layout.addWidget(right_widget, stretch=1)

        # Connect selection to enable buttons
        self.phases_table.itemSelectionChanged.connect(self._on_phase_selection_changed)

    def _setup_tray_types_tab(self, parent):
        """Set up the Tray Types management tab."""
        layout = QVBoxLayout(parent)

        layout.addWidget(QLabel(
            "Tray types define the physical apparatus used in behavioral testing.\n"
            "Each tray type has a single-letter code (e.g., P for Pillar, F for Flat)."
        ))

        # Tray types table
        self.tray_types_table = QTableWidget()
        self.tray_types_table.setColumnCount(4)
        self.tray_types_table.setHorizontalHeaderLabels([
            "Code", "Name", "Description", "Active"
        ])
        self.tray_types_table.horizontalHeader().setStretchLastSection(True)
        self.tray_types_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        layout.addWidget(self.tray_types_table)

        # Add new tray type section
        add_group = QGroupBox("Add New Tray Type")
        add_layout = QHBoxLayout()

        add_layout.addWidget(QLabel("Code:"))
        self.tray_code_edit = QLineEdit()
        self.tray_code_edit.setMaxLength(5)
        self.tray_code_edit.setMaximumWidth(60)
        self.tray_code_edit.setPlaceholderText("X")
        add_layout.addWidget(self.tray_code_edit)

        add_layout.addWidget(QLabel("Name:"))
        self.tray_name_edit = QLineEdit()
        self.tray_name_edit.setPlaceholderText("Tray Name")
        add_layout.addWidget(self.tray_name_edit)

        add_layout.addWidget(QLabel("Description:"))
        self.tray_desc_edit = QLineEdit()
        self.tray_desc_edit.setPlaceholderText("Optional description")
        add_layout.addWidget(self.tray_desc_edit, stretch=1)

        add_tray_btn = QPushButton("Add")
        add_tray_btn.clicked.connect(self._add_tray_type)
        add_layout.addWidget(add_tray_btn)

        add_group.setLayout(add_layout)
        layout.addWidget(add_group)

    def _refresh_all(self):
        """Refresh all data in the dialog."""
        self._refresh_protocols_list()
        self._refresh_tray_types()

    def _refresh_protocols_list(self):
        """Refresh the protocols list."""
        self.protocols_list.clear()
        with self.db.session() as session:
            protocol_list = protocols.list_protocols(session, active_only=False)
            for proto in protocol_list:
                item = QListWidgetItem(f"{proto.name} (v{proto.version})")
                item.setData(Qt.UserRole, proto.id)
                if not proto.is_active:
                    item.setForeground(QColor("#999999"))
                self.protocols_list.addItem(item)

    def _refresh_tray_types(self):
        """Refresh the tray types table."""
        with self.db.session() as session:
            tray_types = protocols.get_tray_types(session, active_only=False)

            self.tray_types_table.setRowCount(len(tray_types))
            for i, tt in enumerate(tray_types):
                self.tray_types_table.setItem(i, 0, QTableWidgetItem(tt.code))
                self.tray_types_table.setItem(i, 1, QTableWidgetItem(tt.name))
                self.tray_types_table.setItem(i, 2, QTableWidgetItem(tt.description or ""))
                active_text = "Yes" if tt.is_active else "No"
                self.tray_types_table.setItem(i, 3, QTableWidgetItem(active_text))

    def _on_protocol_selected(self, current, previous):
        """Handle protocol selection change."""
        if not current:
            self._clear_protocol_details()
            self.delete_protocol_btn.setEnabled(False)
            self.add_phase_btn.setEnabled(False)
            self.save_protocol_btn.setEnabled(False)
            return

        protocol_id = current.data(Qt.UserRole)
        self.delete_protocol_btn.setEnabled(True)
        self.add_phase_btn.setEnabled(True)
        self.save_protocol_btn.setEnabled(True)

        with self.db.session() as session:
            proto = protocols.get_protocol(session, protocol_id)
            if proto:
                self.proto_name_edit.setText(proto.name)
                self.proto_desc_edit.setPlainText(proto.description or "")
                self.proto_version_label.setText(str(proto.version))
                self._current_protocol_id = proto.id

                # Load phases
                self._refresh_phases_table(session, proto.id)

    def _refresh_phases_table(self, session, protocol_id):
        """Refresh the phases table for a protocol."""
        phases = protocols.get_effective_phases(session, protocol_id)

        self.phases_table.setRowCount(len(phases))
        for i, phase in enumerate(phases):
            self.phases_table.setItem(i, 0, QTableWidgetItem(str(phase['phase_order'])))
            self.phases_table.setItem(i, 1, QTableWidgetItem(phase['phase_name']))
            self.phases_table.setItem(i, 2, QTableWidgetItem(str(phase['duration_days'])))
            self.phases_table.setItem(i, 3, QTableWidgetItem(phase.get('tray_type_code') or "-"))
            self.phases_table.setItem(i, 4, QTableWidgetItem(str(phase.get('sessions_per_day', 1))))

            weekends = "Yes" if phase.get('include_weekends') else "No"
            self.phases_table.setItem(i, 5, QTableWidgetItem(weekends))

            pellets = "Yes" if phase.get('expects_pellets') else "No"
            self.phases_table.setItem(i, 6, QTableWidgetItem(pellets))

            weights = "Yes" if phase.get('expects_weights') else "No"
            self.phases_table.setItem(i, 7, QTableWidgetItem(weights))

            # Store phase_id for later use
            self.phases_table.item(i, 0).setData(Qt.UserRole, phase.get('id'))

    def _clear_protocol_details(self):
        """Clear the protocol details form."""
        self.proto_name_edit.clear()
        self.proto_desc_edit.clear()
        self.proto_version_label.setText("-")
        self.phases_table.setRowCount(0)
        self._current_protocol_id = None

    def _on_phase_selection_changed(self):
        """Handle phase selection change."""
        has_selection = len(self.phases_table.selectedItems()) > 0
        self.edit_phase_btn.setEnabled(has_selection)
        self.remove_phase_btn.setEnabled(has_selection)

    def _create_new_protocol(self):
        """Create a new protocol."""
        # Clear form for new entry
        self._clear_protocol_details()
        self.proto_name_edit.setFocus()
        self.protocols_list.clearSelection()
        self._current_protocol_id = None
        self.add_phase_btn.setEnabled(True)
        self.save_protocol_btn.setEnabled(True)
        self.delete_protocol_btn.setEnabled(False)

    def _delete_protocol(self):
        """Delete (deactivate) the selected protocol."""
        current = self.protocols_list.currentItem()
        if not current:
            return

        protocol_id = current.data(Qt.UserRole)
        reply = QMessageBox.question(
            self, "Confirm Delete",
            f"Deactivate this protocol?\n\n"
            "The protocol will be hidden but not deleted (existing cohorts may reference it).",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            with self.db.session() as session:
                proto = session.query(Protocol).filter_by(id=protocol_id).first()
                if proto:
                    proto.is_active = 0
                    session.commit()

            self._refresh_protocols_list()
            self._clear_protocol_details()

    def _add_phase(self):
        """Add a new phase to the current protocol."""
        dialog = PhaseEditDialog(self.db, parent=self)
        if dialog.exec_():
            phase_data = dialog.get_phase_data()
            # Add to table (will be saved when protocol is saved)
            row = self.phases_table.rowCount()
            self.phases_table.setRowCount(row + 1)

            self.phases_table.setItem(row, 0, QTableWidgetItem(str(row + 1)))
            self.phases_table.setItem(row, 1, QTableWidgetItem(phase_data['phase_name']))
            self.phases_table.setItem(row, 2, QTableWidgetItem(str(phase_data['duration_days'])))
            self.phases_table.setItem(row, 3, QTableWidgetItem(phase_data.get('tray_type_code') or "-"))
            self.phases_table.setItem(row, 4, QTableWidgetItem(str(phase_data.get('sessions_per_day', 1))))
            self.phases_table.setItem(row, 5, QTableWidgetItem("Yes" if phase_data.get('include_weekends') else "No"))
            self.phases_table.setItem(row, 6, QTableWidgetItem("Yes" if phase_data.get('expects_pellets') else "No"))
            self.phases_table.setItem(row, 7, QTableWidgetItem("Yes" if phase_data.get('expects_weights') else "No"))

            # Store phase data for saving
            self.phases_table.item(row, 0).setData(Qt.UserRole, None)  # New phase, no ID yet
            self.phases_table.item(row, 0).setData(Qt.UserRole + 1, phase_data)

    def _edit_phase(self):
        """Edit the selected phase."""
        items = self.phases_table.selectedItems()
        if not items:
            return

        row = items[0].row()
        # Get existing phase data
        phase_data = {
            'phase_name': self.phases_table.item(row, 1).text(),
            'duration_days': int(self.phases_table.item(row, 2).text()),
            'tray_type_code': self.phases_table.item(row, 3).text() if self.phases_table.item(row, 3).text() != "-" else None,
            'sessions_per_day': int(self.phases_table.item(row, 4).text()),
            'include_weekends': self.phases_table.item(row, 5).text() == "Yes",
            'expects_pellets': self.phases_table.item(row, 6).text() == "Yes",
            'expects_weights': self.phases_table.item(row, 7).text() == "Yes",
        }

        dialog = PhaseEditDialog(self.db, phase_data=phase_data, parent=self)
        if dialog.exec_():
            updated_data = dialog.get_phase_data()
            self.phases_table.item(row, 1).setText(updated_data['phase_name'])
            self.phases_table.item(row, 2).setText(str(updated_data['duration_days']))
            self.phases_table.item(row, 3).setText(updated_data.get('tray_type_code') or "-")
            self.phases_table.item(row, 4).setText(str(updated_data.get('sessions_per_day', 1)))
            self.phases_table.item(row, 5).setText("Yes" if updated_data.get('include_weekends') else "No")
            self.phases_table.item(row, 6).setText("Yes" if updated_data.get('expects_pellets') else "No")
            self.phases_table.item(row, 7).setText("Yes" if updated_data.get('expects_weights') else "No")
            self.phases_table.item(row, 0).setData(Qt.UserRole + 1, updated_data)

    def _remove_phase(self):
        """Remove the selected phase."""
        items = self.phases_table.selectedItems()
        if not items:
            return

        row = items[0].row()
        reply = QMessageBox.question(
            self, "Confirm Remove",
            f"Remove phase '{self.phases_table.item(row, 1).text()}'?",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            self.phases_table.removeRow(row)
            # Renumber remaining phases
            for i in range(self.phases_table.rowCount()):
                self.phases_table.item(i, 0).setText(str(i + 1))

    def _save_protocol(self):
        """Save the current protocol."""
        name = self.proto_name_edit.text().strip()
        if not name:
            QMessageBox.warning(self, "Error", "Protocol name is required.")
            return

        description = self.proto_desc_edit.toPlainText().strip() or None

        # Collect phases from table
        phases_data = []
        for row in range(self.phases_table.rowCount()):
            # Try to get stored phase data first
            stored_data = self.phases_table.item(row, 0).data(Qt.UserRole + 1)
            if stored_data:
                phase = stored_data.copy()
            else:
                # Build from table cells
                phase = {
                    'phase_name': self.phases_table.item(row, 1).text(),
                    'duration_days': int(self.phases_table.item(row, 2).text()),
                    'tray_type_code': self.phases_table.item(row, 3).text() if self.phases_table.item(row, 3).text() != "-" else None,
                    'sessions_per_day': int(self.phases_table.item(row, 4).text()),
                    'include_weekends': self.phases_table.item(row, 5).text() == "Yes",
                    'expects_pellets': self.phases_table.item(row, 6).text() == "Yes",
                    'expects_weights': self.phases_table.item(row, 7).text() == "Yes",
                }
            phase['phase_order'] = row + 1
            phases_data.append(phase)

        if not phases_data:
            QMessageBox.warning(self, "Error", "Protocol must have at least one phase.")
            return

        with self.db.session() as session:
            if self._current_protocol_id:
                # Update existing protocol (creates new version)
                # For now, just update the name/description
                proto = session.query(Protocol).filter_by(id=self._current_protocol_id).first()
                if proto:
                    proto.name = name
                    proto.description = description
                    # Note: Phase updates would require more complex logic
                    # For MVP, we'll just save as new if phases changed significantly
                    session.commit()
                    QMessageBox.information(self, "Saved", f"Protocol '{name}' updated.")
            else:
                # Create new protocol
                proto = protocols.create_protocol(
                    session, name, phases_data, description=description
                )
                self._current_protocol_id = proto.id
                QMessageBox.information(self, "Saved", f"Protocol '{name}' created with {len(phases_data)} phases.")

        self._refresh_protocols_list()

    def _add_tray_type(self):
        """Add a new tray type."""
        code = self.tray_code_edit.text().strip().upper()
        name = self.tray_name_edit.text().strip()
        description = self.tray_desc_edit.text().strip() or None

        if not code:
            QMessageBox.warning(self, "Error", "Tray code is required.")
            return
        if not name:
            QMessageBox.warning(self, "Error", "Tray name is required.")
            return

        with self.db.session() as session:
            try:
                protocols.add_tray_type(session, code, name, description)
                QMessageBox.information(self, "Success", f"Tray type '{code}' added.")
                self._refresh_tray_types()

                # Clear inputs
                self.tray_code_edit.clear()
                self.tray_name_edit.clear()
                self.tray_desc_edit.clear()
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Could not add tray type: {e}")


class PhaseEditDialog(QDialog):
    """Dialog for editing a single protocol phase."""

    def __init__(self, db, phase_data: Dict = None, parent=None):
        super().__init__(parent)
        self.db = db
        self.phase_data = phase_data or {}
        self.setWindowTitle("Edit Phase" if phase_data else "Add Phase")
        self.setMinimumWidth(400)
        self._setup_ui()

    def _setup_ui(self):
        layout = QFormLayout(self)

        # Phase name
        self.name_edit = QLineEdit()
        self.name_edit.setText(self.phase_data.get('phase_name', ''))
        self.name_edit.setPlaceholderText("e.g., Training_Flat, Pre_Injury_Test")
        layout.addRow("Phase Name:", self.name_edit)

        # Duration
        self.duration_spin = QSpinBox()
        self.duration_spin.setRange(1, 365)
        self.duration_spin.setValue(self.phase_data.get('duration_days', 5))
        self.duration_spin.setSuffix(" days")
        layout.addRow("Duration:", self.duration_spin)

        # Tray type
        self.tray_combo = QComboBox()
        self.tray_combo.addItem("-- None --", None)
        with self.db.session() as session:
            tray_types = protocols.get_tray_types(session, active_only=True)
            for tt in tray_types:
                self.tray_combo.addItem(f"{tt.code} - {tt.name}", tt.code)

        # Set current tray type
        current_tray = self.phase_data.get('tray_type_code')
        if current_tray:
            idx = self.tray_combo.findData(current_tray)
            if idx >= 0:
                self.tray_combo.setCurrentIndex(idx)
        layout.addRow("Tray Type:", self.tray_combo)

        # Sessions per day
        self.sessions_spin = QSpinBox()
        self.sessions_spin.setRange(1, 10)
        self.sessions_spin.setValue(self.phase_data.get('sessions_per_day', 4))
        layout.addRow("Sessions/Day:", self.sessions_spin)

        # Include weekends
        self.weekends_check = QCheckBox("Include weekends in phase duration")
        self.weekends_check.setChecked(self.phase_data.get('include_weekends', False))
        layout.addRow("", self.weekends_check)

        # Data expectations
        expectations_group = QGroupBox("Expected Data Entry")
        exp_layout = QVBoxLayout()

        self.expects_pellets_check = QCheckBox("Expects pellet scores")
        self.expects_pellets_check.setChecked(self.phase_data.get('expects_pellets', True))
        exp_layout.addWidget(self.expects_pellets_check)

        self.expects_weights_check = QCheckBox("Expects weight measurements")
        self.expects_weights_check.setChecked(self.phase_data.get('expects_weights', True))
        exp_layout.addWidget(self.expects_weights_check)

        self.expects_surgery_check = QCheckBox("Expects surgery records")
        self.expects_surgery_check.setChecked(self.phase_data.get('expects_surgery', False))
        exp_layout.addWidget(self.expects_surgery_check)

        expectations_group.setLayout(exp_layout)
        layout.addRow(expectations_group)

        # Stagger group size (optional)
        self.stagger_spin = QSpinBox()
        self.stagger_spin.setRange(0, 100)
        self.stagger_spin.setValue(self.phase_data.get('stagger_group_size') or 0)
        self.stagger_spin.setSpecialValueText("No staggering")
        self.stagger_spin.setToolTip("For staggered phases (like surgery), how many subjects per day. 0 = no staggering.")
        layout.addRow("Stagger Group Size:", self.stagger_spin)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addRow(buttons)

    def get_phase_data(self) -> Dict[str, Any]:
        """Get the phase data from the form."""
        stagger = self.stagger_spin.value()
        return {
            'phase_name': self.name_edit.text().strip(),
            'duration_days': self.duration_spin.value(),
            'tray_type_code': self.tray_combo.currentData(),
            'sessions_per_day': self.sessions_spin.value(),
            'include_weekends': self.weekends_check.isChecked(),
            'expects_pellets': self.expects_pellets_check.isChecked(),
            'expects_weights': self.expects_weights_check.isChecked(),
            'expects_surgery': self.expects_surgery_check.isChecked(),
            'stagger_group_size': stagger if stagger > 0 else None,
        }
