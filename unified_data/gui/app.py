"""
Main PyQt application for Connectome Data Entry.

A bulletproof data entry system for undergrads:
- Constrained inputs only (dropdowns, number spinners)
- Real-time validation feedback
- Auto-save with audit trail
"""

import sys
from datetime import datetime, date
from typing import Optional, List, Dict, Tuple
from pathlib import Path

try:
    from PyQt5.QtWidgets import (
        QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
        QLabel, QComboBox, QPushButton, QGroupBox, QGridLayout,
        QDoubleSpinBox, QSpinBox, QMessageBox, QStatusBar, QFrame,
        QScrollArea, QSizePolicy, QSpacerItem, QTabWidget, QTextEdit,
        QLineEdit, QDateEdit, QTableWidget, QTableWidgetItem, QHeaderView,
        QFormLayout, QPlainTextEdit, QFileDialog, QSplitter, QCheckBox
    )
    from PyQt5.QtCore import Qt, pyqtSignal, QDate, QByteArray
    from PyQt5.QtGui import QFont, QColor, QPixmap, QImage
except ImportError:
    print("PyQt5 not found. Install with: pip install PyQt5")
    sys.exit(1)

# Matplotlib for embedded charts
try:
    import matplotlib
    matplotlib.use('Qt5Agg')
    from matplotlib.backends.backend_qt5agg import FigureCanvasQTAgg as FigureCanvas
    from matplotlib.backends.backend_qt5agg import NavigationToolbar2QT as NavigationToolbar
    from matplotlib.figure import Figure
    HAS_MATPLOTLIB = True
except ImportError:
    HAS_MATPLOTLIB = False
    print("Warning: matplotlib not found. Visualization features will be limited.")

from ..database import get_db, init_database
from ..schema import (
    Project, Cohort, Subject, Weight, PelletScore, Surgery, TIMELINE,
    RampEntry, SessionException, VirusPrep,
    BrainSample, RegionCount, DetectedCell, CalibrationRun,
    Protocol, ProtocolPhase, TrayType
)
from sqlalchemy import String
from .. import protocols
from ..stats import (
    calculate_daily_stats, calculate_subject_summary, calculate_cohort_summary,
    get_cohort_overview, DailyStats, SubjectSummary, TrayStats
)
from .styles import STYLESHEET, COLORS, SCORE_COLORS, SCORE_LABELS
from .timeline_gantt import TimelineGanttWidget, MiniTimelineWidget


class PelletButton(QPushButton):
    """Single pellet score button that cycles through 0/1/2."""

    scoreChanged = pyqtSignal(int, int)  # pellet_number, score

    def __init__(self, pellet_number: int, parent=None):
        super().__init__(parent)
        self.pellet_number = pellet_number
        self._score: Optional[int] = None
        self.setObjectName("pellet_button")
        self.setProperty("score", "none")
        self.setText("-")
        self.setFocusPolicy(Qt.StrongFocus)
        self.clicked.connect(self._on_click)

    @property
    def score(self) -> Optional[int]:
        return self._score

    @score.setter
    def score(self, value: Optional[int]):
        self._score = value
        if value is None:
            self.setProperty("score", "none")
            self.setText("-")
        else:
            self.setProperty("score", str(value))
            self.setText(str(value))
        self.style().unpolish(self)
        self.style().polish(self)

    def _on_click(self):
        """Cycle through None -> 0 -> 1 -> 2 -> 0..."""
        if self._score is None:
            self.score = 0
        elif self._score == 0:
            self.score = 1
        elif self._score == 1:
            self.score = 2
        else:
            self.score = 0
        self.scoreChanged.emit(self.pellet_number, self._score)

    def keyPressEvent(self, event):
        """Handle keyboard input for fast entry."""
        key = event.text()
        if key in ('0', '1', '2'):
            self.score = int(key)
            self.scoreChanged.emit(self.pellet_number, self._score)
            # Move to next button
            self.focusNextChild()
        elif key == '-' or key == ' ':
            self.score = None
            self.scoreChanged.emit(self.pellet_number, self._score)
            self.focusNextChild()
        else:
            super().keyPressEvent(event)


class TrayWidget(QGroupBox):
    """Widget for entering scores for a single tray (20 pellets)."""

    scoresChanged = pyqtSignal()

    def __init__(self, tray_number: int, tray_type: str, parent=None):
        super().__init__(f"Tray {tray_number} ({tray_type})", parent)
        self.tray_number = tray_number
        self.tray_type = tray_type
        self.pellet_buttons: List[PelletButton] = []

        self._setup_ui()

    def _setup_ui(self):
        layout = QGridLayout()
        layout.setSpacing(4)

        # Create 20 pellet buttons in 2 rows of 10
        for i in range(20):
            btn = PelletButton(i + 1, self)
            btn.scoreChanged.connect(self._on_score_changed)
            self.pellet_buttons.append(btn)

            row = i // 10
            col = i % 10
            layout.addWidget(btn, row, col)

        self.setLayout(layout)

    def _on_score_changed(self, pellet_num: int, score: int):
        self.scoresChanged.emit()

    def get_scores(self) -> Dict[int, Optional[int]]:
        """Get all scores as dict {pellet_number: score}."""
        return {btn.pellet_number: btn.score for btn in self.pellet_buttons}

    def set_scores(self, scores: Dict[int, Optional[int]]):
        """Set scores from dict."""
        for btn in self.pellet_buttons:
            btn.score = scores.get(btn.pellet_number)

    def clear(self):
        """Clear all scores."""
        for btn in self.pellet_buttons:
            btn.score = None

    def get_summary(self) -> Dict[str, int]:
        """Get score summary."""
        scores = [btn.score for btn in self.pellet_buttons if btn.score is not None]
        return {
            'total': len(scores),
            'miss': scores.count(0),
            'displaced': scores.count(1),
            'retrieved': scores.count(2),
        }


class PelletEntryTab(QWidget):
    """Tab for pellet score data entry."""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        # Store primitive values to avoid detached session issues
        self.current_cohort_id: Optional[str] = None
        self.current_cohort_start_date: Optional[date] = None
        self.current_subject_id: Optional[str] = None
        self.current_date: Optional[date] = None
        self.current_phase: Optional[str] = None
        self.tray_widgets: List[TrayWidget] = []

        self._setup_ui()
        self._load_cohorts()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(15)

        # Step 1: Select Animal
        step1_group = self._create_step_group("Step 1: Select Animal")
        step1_layout = QHBoxLayout()

        step1_layout.addWidget(QLabel("Cohort:"))
        self.cohort_combo = QComboBox()
        self.cohort_combo.currentIndexChanged.connect(self._on_cohort_changed)
        step1_layout.addWidget(self.cohort_combo)

        step1_layout.addWidget(QLabel("Animal:"))
        self.animal_combo = QComboBox()
        self.animal_combo.currentIndexChanged.connect(self._on_animal_changed)
        step1_layout.addWidget(self.animal_combo)

        step1_layout.addStretch()
        step1_group.layout().addLayout(step1_layout)
        main_layout.addWidget(step1_group)

        # Step 2: Select Session
        step2_group = self._create_step_group("Step 2: Select Session")
        step2_layout = QHBoxLayout()

        step2_layout.addWidget(QLabel("Date:"))
        self.date_combo = QComboBox()
        self.date_combo.currentIndexChanged.connect(self._on_date_changed)
        step2_layout.addWidget(self.date_combo)

        step2_layout.addWidget(QLabel("Phase:"))
        self.phase_label = QLabel("-")
        self.phase_label.setStyleSheet("font-weight: bold; color: #2196F3;")
        step2_layout.addWidget(self.phase_label)

        step2_layout.addStretch()
        step2_group.layout().addLayout(step2_layout)
        main_layout.addWidget(step2_group)

        # Step 3: Enter Weight
        step3_group = self._create_step_group("Step 3: Enter Weight")
        step3_layout = QHBoxLayout()

        step3_layout.addWidget(QLabel("Weight:"))
        self.weight_spin = QDoubleSpinBox()
        self.weight_spin.setRange(10.0, 50.0)
        self.weight_spin.setDecimals(1)
        self.weight_spin.setSuffix(" g")
        self.weight_spin.setSpecialValueText("-")
        self.weight_spin.setValue(self.weight_spin.minimum())
        step3_layout.addWidget(self.weight_spin)

        self.weight_status = QLabel("")
        step3_layout.addWidget(self.weight_status)
        step3_layout.addStretch()

        step3_group.layout().addLayout(step3_layout)
        main_layout.addWidget(step3_group)

        # Step 4: Enter Pellet Scores
        step4_group = self._create_step_group("Step 4: Enter Pellet Scores (click or type 0/1/2)")

        # Tray widgets container
        trays_layout = QVBoxLayout()
        self.trays_container = QWidget()
        self.trays_container.setLayout(trays_layout)

        step4_group.layout().addWidget(self.trays_container)

        # Summary
        summary_layout = QHBoxLayout()
        self.summary_label = QLabel("Summary: 0/80 entered")
        self.summary_label.setStyleSheet("font-weight: bold;")
        summary_layout.addWidget(self.summary_label)
        summary_layout.addStretch()
        step4_group.layout().addLayout(summary_layout)

        main_layout.addWidget(step4_group)

        # Navigation buttons
        nav_layout = QHBoxLayout()

        self.prev_btn = QPushButton("← Previous Animal")
        self.prev_btn.setObjectName("secondary_button")
        self.prev_btn.clicked.connect(self._previous_animal)
        nav_layout.addWidget(self.prev_btn)

        nav_layout.addStretch()

        self.save_btn = QPushButton("Save & Next Animal →")
        self.save_btn.setObjectName("success_button")
        self.save_btn.clicked.connect(self._save_and_next)
        nav_layout.addWidget(self.save_btn)

        main_layout.addLayout(nav_layout)

    def _create_step_group(self, title: str) -> QGroupBox:
        group = QGroupBox()
        layout = QVBoxLayout()
        layout.setSpacing(10)

        label = QLabel(title)
        label.setObjectName("step_label")
        layout.addWidget(label)

        group.setLayout(layout)
        return group

    def _load_cohorts(self):
        """Load available cohorts into dropdown."""
        with self.db.session() as session:
            cohorts = session.query(Cohort).filter(
                (Cohort.is_archived == 0) | (Cohort.is_archived == None)
            ).order_by(Cohort.cohort_id).all()
            self.cohort_combo.clear()
            self.cohort_combo.addItem("-- Select Cohort --", None)
            for c in cohorts:
                self.cohort_combo.addItem(f"{c.cohort_id} (start: {c.start_date})", c.cohort_id)

    def _on_cohort_changed(self, index: int):
        """Handle cohort selection change."""
        cohort_id = self.cohort_combo.currentData()
        if not cohort_id:
            self.current_cohort_id = None
            self.current_cohort_start_date = None
            self.animal_combo.clear()
            return

        with self.db.session() as session:
            cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
            if cohort:
                # Store primitive values to avoid detached session issues
                self.current_cohort_id = cohort.cohort_id
                self.current_cohort_start_date = cohort.start_date

                subjects = session.query(Subject).filter_by(
                    cohort_id=cohort_id, is_active=1
                ).order_by(Subject.subject_id).all()

                self.animal_combo.clear()
                self.animal_combo.addItem("-- Select Animal --", None)
                for s in subjects:
                    self.animal_combo.addItem(s.subject_id, s.subject_id)

    def _on_animal_changed(self, index: int):
        """Handle animal selection change."""
        subject_id = self.animal_combo.currentData()
        if not subject_id:
            self.current_subject_id = None
            self.date_combo.clear()
            return

        self.current_subject_id = subject_id

        with self.db.session() as session:
            if self.current_subject_id and self.current_cohort_id:
                # Query cohort fresh within this session to call get_valid_dates()
                cohort = session.query(Cohort).filter_by(cohort_id=self.current_cohort_id).first()
                if cohort:
                    valid_dates = cohort.get_valid_dates()

                    self.date_combo.clear()
                    self.date_combo.addItem("-- Select Date --", None)
                    for d, phase, tray_type, trays in valid_dates:
                        existing = session.query(PelletScore).filter_by(
                            subject_id=subject_id, session_date=d
                        ).first()
                        status = " [has data]" if existing else ""
                        self.date_combo.addItem(
                            f"{d.strftime('%Y-%m-%d')} - {phase}{status}",
                            (d, phase, tray_type, trays)
                        )

    def _on_date_changed(self, index: int):
        """Handle date selection change."""
        data = self.date_combo.currentData()
        if not data:
            self.current_date = None
            self.current_phase = None
            self.phase_label.setText("-")
            self._clear_trays()
            return

        self.current_date, self.current_phase, tray_type, num_trays = data
        self.phase_label.setText(self.current_phase)
        self._setup_trays(num_trays, tray_type)
        self._load_existing_data()

    def _setup_trays(self, num_trays: int, tray_type: str):
        """Create tray widgets for data entry."""
        self._clear_trays()

        layout = self.trays_container.layout()
        for i in range(num_trays):
            tray = TrayWidget(i + 1, tray_type)
            tray.scoresChanged.connect(self._update_summary)
            self.tray_widgets.append(tray)
            layout.addWidget(tray)

        self._update_summary()

    def _clear_trays(self):
        """Remove all tray widgets."""
        layout = self.trays_container.layout()
        for tray in self.tray_widgets:
            layout.removeWidget(tray)
            tray.deleteLater()
        self.tray_widgets = []
        self._update_summary()

    def _load_existing_data(self):
        """Load existing data for current subject/date."""
        if not self.current_subject_id or not self.current_date:
            return

        with self.db.session() as session:
            weight = session.query(Weight).filter_by(
                subject_id=self.current_subject_id,
                date=self.current_date
            ).first()
            if weight:
                self.weight_spin.setValue(weight.weight_grams)
            else:
                self.weight_spin.setValue(self.weight_spin.minimum())

            scores = session.query(PelletScore).filter_by(
                subject_id=self.current_subject_id,
                session_date=self.current_date
            ).all()

            tray_scores = {}
            for s in scores:
                if s.tray_number not in tray_scores:
                    tray_scores[s.tray_number] = {}
                tray_scores[s.tray_number][s.pellet_number] = s.score

            for tray in self.tray_widgets:
                if tray.tray_number in tray_scores:
                    tray.set_scores(tray_scores[tray.tray_number])

        self._update_summary()

    def _update_summary(self):
        """Update the pellet score summary."""
        total = miss = displaced = retrieved = 0

        for tray in self.tray_widgets:
            summary = tray.get_summary()
            total += summary['total']
            miss += summary['miss']
            displaced += summary['displaced']
            retrieved += summary['retrieved']

        expected = len(self.tray_widgets) * 20
        self.summary_label.setText(
            f"Summary: {total}/{expected} entered  |  "
            f"Retrieved: {retrieved}  |  Displaced: {displaced}  |  Miss: {miss}"
        )

    def _save_and_next(self):
        """Save current data and move to next animal."""
        if not self._validate_and_save():
            return

        current_index = self.animal_combo.currentIndex()
        if current_index < self.animal_combo.count() - 1:
            self.animal_combo.setCurrentIndex(current_index + 1)
        else:
            QMessageBox.information(self, "Complete",
                                    "All animals in this cohort have been entered!")

    def _previous_animal(self):
        """Move to previous animal."""
        current_index = self.animal_combo.currentIndex()
        if current_index > 1:
            self.animal_combo.setCurrentIndex(current_index - 1)

    def _validate_and_save(self) -> bool:
        """Validate and save current data."""
        if not self.current_subject_id or not self.current_date:
            QMessageBox.warning(self, "Error", "Please select an animal and date first.")
            return False

        weight_value = self.weight_spin.value()
        if weight_value == self.weight_spin.minimum():
            weight_value = None

        with self.db.session() as session:
            if weight_value:
                existing_weight = session.query(Weight).filter_by(
                    subject_id=self.current_subject_id,
                    date=self.current_date
                ).first()

                if existing_weight:
                    existing_weight.weight_grams = weight_value
                    existing_weight.entered_by = self.db.current_user
                    existing_weight.entered_at = datetime.now()
                else:
                    weight = Weight(
                        subject_id=self.current_subject_id,
                        date=self.current_date,
                        weight_grams=weight_value,
                        entered_by=self.db.current_user,
                    )
                    session.add(weight)

            for tray in self.tray_widgets:
                scores = tray.get_scores()
                for pellet_num, score in scores.items():
                    if score is None:
                        continue

                    existing = session.query(PelletScore).filter_by(
                        subject_id=self.current_subject_id,
                        session_date=self.current_date,
                        tray_type=tray.tray_type,
                        tray_number=tray.tray_number,
                        pellet_number=pellet_num
                    ).first()

                    if existing:
                        existing.score = score
                        existing.entered_by = self.db.current_user
                        existing.entered_at = datetime.now()
                    else:
                        pellet_score = PelletScore(
                            subject_id=self.current_subject_id,
                            session_date=self.current_date,
                            test_phase=self.current_phase or "",
                            tray_type=tray.tray_type,
                            tray_number=tray.tray_number,
                            pellet_number=pellet_num,
                            score=score,
                            entered_by=self.db.current_user,
                        )
                        session.add(pellet_score)

            session.commit()

        return True


class SurgeryEntryTab(QWidget):
    """Tab for surgery data entry (contusion, tracing, perfusion)."""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._setup_ui()
        self._load_cohorts()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(15)

        # Animal Selection
        select_group = QGroupBox("Select Animal")
        select_layout = QHBoxLayout()

        select_layout.addWidget(QLabel("Cohort:"))
        self.cohort_combo = QComboBox()
        self.cohort_combo.currentIndexChanged.connect(self._on_cohort_changed)
        select_layout.addWidget(self.cohort_combo)

        select_layout.addWidget(QLabel("Animal:"))
        self.animal_combo = QComboBox()
        self.animal_combo.currentIndexChanged.connect(self._on_animal_changed)
        select_layout.addWidget(self.animal_combo)

        select_layout.addStretch()
        select_group.setLayout(select_layout)
        main_layout.addWidget(select_group)

        # Surgery Type Tabs
        self.surgery_tabs = QTabWidget()

        # Contusion Tab
        contusion_tab = QWidget()
        contusion_layout = QFormLayout(contusion_tab)

        self.contusion_date = QDateEdit()
        self.contusion_date.setCalendarPopup(True)
        self.contusion_date.setDate(QDate.currentDate())
        contusion_layout.addRow("Date:", self.contusion_date)

        # Pre-surgery weight (workflow addition)
        self.contusion_pre_weight = QDoubleSpinBox()
        self.contusion_pre_weight.setRange(0, 50.0)
        self.contusion_pre_weight.setDecimals(1)
        self.contusion_pre_weight.setSuffix(" g")
        self.contusion_pre_weight.setSpecialValueText("-")
        self.contusion_pre_weight.setValue(0)
        self.contusion_pre_weight.setToolTip("Body weight taken just before surgery")
        contusion_layout.addRow("Pre-Surgery Weight:", self.contusion_pre_weight)

        self.force_spin = QDoubleSpinBox()
        self.force_spin.setRange(0, 500)
        self.force_spin.setDecimals(1)
        self.force_spin.setSuffix(" kDyn")
        contusion_layout.addRow("Force:", self.force_spin)

        self.displacement_spin = QDoubleSpinBox()
        self.displacement_spin.setRange(0, 5000)
        self.displacement_spin.setDecimals(1)
        self.displacement_spin.setSuffix(" µm")
        contusion_layout.addRow("Displacement:", self.displacement_spin)

        self.velocity_spin = QDoubleSpinBox()
        self.velocity_spin.setRange(0, 500)
        self.velocity_spin.setDecimals(1)
        self.velocity_spin.setSuffix(" mm/s")
        contusion_layout.addRow("Velocity:", self.velocity_spin)

        self.contusion_surgeon = QLineEdit()
        contusion_layout.addRow("Surgeon:", self.contusion_surgeon)

        # Survived checkbox (workflow addition)
        self.contusion_survived = QCheckBox("Animal survived surgery")
        self.contusion_survived.setChecked(True)
        self.contusion_survived.setToolTip("Uncheck if animal did not survive surgery")
        contusion_layout.addRow("Outcome:", self.contusion_survived)

        self.contusion_notes = QPlainTextEdit()
        self.contusion_notes.setMaximumHeight(80)
        contusion_layout.addRow("Notes:", self.contusion_notes)

        self.surgery_tabs.addTab(contusion_tab, "Contusion")

        # Tracing Tab
        tracing_tab = QWidget()
        tracing_layout = QFormLayout(tracing_tab)

        self.tracing_date = QDateEdit()
        self.tracing_date.setCalendarPopup(True)
        self.tracing_date.setDate(QDate.currentDate())
        tracing_layout.addRow("Date:", self.tracing_date)

        self.virus_name = QComboBox()
        self.virus_name.setEditable(True)
        self.virus_name.setPlaceholderText("Select virus prep or type name")
        tracing_layout.addRow("Virus Name:", self.virus_name)

        self.volume_spin = QDoubleSpinBox()
        self.volume_spin.setRange(0, 10000)
        self.volume_spin.setDecimals(1)
        self.volume_spin.setSuffix(" nL")
        tracing_layout.addRow("Volume:", self.volume_spin)

        self.tracing_surgeon = QLineEdit()
        tracing_layout.addRow("Surgeon:", self.tracing_surgeon)

        self.tracing_notes = QPlainTextEdit()
        self.tracing_notes.setMaximumHeight(80)
        tracing_layout.addRow("Notes:", self.tracing_notes)

        self.surgery_tabs.addTab(tracing_tab, "Tracing Injection")

        # Perfusion Tab
        perfusion_tab = QWidget()
        perfusion_layout = QFormLayout(perfusion_tab)

        self.perfusion_date = QDateEdit()
        self.perfusion_date.setCalendarPopup(True)
        self.perfusion_date.setDate(QDate.currentDate())
        perfusion_layout.addRow("Date:", self.perfusion_date)

        self.perfusion_surgeon = QLineEdit()
        perfusion_layout.addRow("Performed By:", self.perfusion_surgeon)

        self.perfusion_notes = QPlainTextEdit()
        self.perfusion_notes.setMaximumHeight(80)
        perfusion_layout.addRow("Notes:", self.perfusion_notes)

        self.surgery_tabs.addTab(perfusion_tab, "Perfusion")

        main_layout.addWidget(self.surgery_tabs)

        # Save Button
        save_layout = QHBoxLayout()
        save_layout.addStretch()

        self.save_btn = QPushButton("Save Surgery Record")
        self.save_btn.setObjectName("success_button")
        self.save_btn.clicked.connect(self._save_surgery)
        save_layout.addWidget(self.save_btn)

        main_layout.addLayout(save_layout)

        # Existing Surgeries Table
        existing_group = QGroupBox("Existing Surgery Records for This Animal")
        existing_layout = QVBoxLayout(existing_group)

        self.surgeries_table = QTableWidget()
        self.surgeries_table.setColumnCount(5)
        self.surgeries_table.setHorizontalHeaderLabels([
            "Type", "Date", "Details", "Surgeon", "Notes"
        ])
        self.surgeries_table.horizontalHeader().setStretchLastSection(True)
        existing_layout.addWidget(self.surgeries_table)

        main_layout.addWidget(existing_group)
        main_layout.addStretch()

    def _load_cohorts(self):
        """Load available cohorts into dropdown."""
        with self.db.session() as session:
            cohorts = session.query(Cohort).filter(
                (Cohort.is_archived == 0) | (Cohort.is_archived == None)
            ).order_by(Cohort.cohort_id).all()
            self.cohort_combo.clear()
            self.cohort_combo.addItem("-- Select Cohort --", None)
            for c in cohorts:
                self.cohort_combo.addItem(c.cohort_id, c.cohort_id)

    def _on_cohort_changed(self, index: int):
        """Handle cohort selection change."""
        cohort_id = self.cohort_combo.currentData()
        if not cohort_id:
            self.animal_combo.clear()
            self.virus_name.clear()
            return

        with self.db.session() as session:
            subjects = session.query(Subject).filter_by(
                cohort_id=cohort_id
            ).order_by(Subject.subject_id).all()

            self.animal_combo.clear()
            self.animal_combo.addItem("-- Select Animal --", None)
            for s in subjects:
                self.animal_combo.addItem(s.subject_id, s.subject_id)

            # Load virus preps for this cohort into dropdown
            self.virus_name.clear()
            preps = session.query(VirusPrep).filter_by(
                cohort_id=cohort_id
            ).order_by(VirusPrep.prep_date).all()
            for p in preps:
                label = p.virus_name
                if p.virus_lot:
                    label += f" (Lot: {p.virus_lot})"
                self.virus_name.addItem(label, p.id)

    def _on_animal_changed(self, index: int):
        """Handle animal selection change."""
        subject_id = self.animal_combo.currentData()
        if not subject_id:
            self.surgeries_table.setRowCount(0)
            return

        self._load_existing_surgeries(subject_id)

    def _load_existing_surgeries(self, subject_id: str):
        """Load existing surgery records for the selected animal."""
        with self.db.session() as session:
            surgeries = session.query(Surgery).filter_by(
                subject_id=subject_id
            ).order_by(Surgery.surgery_date).all()

            self.surgeries_table.setRowCount(len(surgeries))

            for i, s in enumerate(surgeries):
                self.surgeries_table.setItem(i, 0, QTableWidgetItem(s.surgery_type))
                self.surgeries_table.setItem(i, 1, QTableWidgetItem(str(s.surgery_date)))

                details = ""
                if s.surgery_type == 'contusion':
                    details = f"Force: {s.force_kdyn} kDyn, Disp: {s.displacement_um} µm"
                elif s.surgery_type == 'tracing':
                    details = f"{s.virus_name}, {s.volume_nl} nL"

                self.surgeries_table.setItem(i, 2, QTableWidgetItem(details))
                self.surgeries_table.setItem(i, 3, QTableWidgetItem(s.surgeon or ""))
                self.surgeries_table.setItem(i, 4, QTableWidgetItem(s.notes or ""))

    def _save_surgery(self):
        """Save the surgery record."""
        subject_id = self.animal_combo.currentData()
        if not subject_id:
            QMessageBox.warning(self, "Error", "Please select an animal first.")
            return

        tab_index = self.surgery_tabs.currentIndex()
        surgery_types = ['contusion', 'tracing', 'perfusion']
        surgery_type = surgery_types[tab_index]

        with self.db.session() as session:
            surgery = Surgery(
                subject_id=subject_id,
                surgery_type=surgery_type,
                entered_by=self.db.current_user,
            )

            survived = True  # Default for non-contusion

            if surgery_type == 'contusion':
                surgery.surgery_date = self.contusion_date.date().toPyDate()
                surgery.pre_surgery_weight_grams = self.contusion_pre_weight.value() or None
                surgery.force_kdyn = self.force_spin.value() or None
                surgery.displacement_um = self.displacement_spin.value() or None
                surgery.velocity_mm_s = self.velocity_spin.value() or None
                surgery.surgeon = self.contusion_surgeon.text() or None
                surgery.survived = 1 if self.contusion_survived.isChecked() else 0
                surgery.notes = self.contusion_notes.toPlainText() or None
                survived = self.contusion_survived.isChecked()

                # Also save pre-surgery weight to weights table
                if surgery.pre_surgery_weight_grams:
                    existing_weight = session.query(Weight).filter_by(
                        subject_id=subject_id,
                        date=surgery.surgery_date
                    ).first()

                    if not existing_weight:
                        weight_record = Weight(
                            subject_id=subject_id,
                            date=surgery.surgery_date,
                            weight_grams=surgery.pre_surgery_weight_grams,
                            notes="Pre-surgery weight",
                            entered_by=self.db.current_user,
                        )
                        session.add(weight_record)

            elif surgery_type == 'tracing':
                surgery.surgery_date = self.tracing_date.date().toPyDate()
                surgery.virus_name = self.virus_name.currentText() or None
                surgery.volume_nl = self.volume_spin.value() or None
                surgery.surgeon = self.tracing_surgeon.text() or None
                surgery.notes = self.tracing_notes.toPlainText() or None

            elif surgery_type == 'perfusion':
                surgery.surgery_date = self.perfusion_date.date().toPyDate()
                surgery.surgeon = self.perfusion_surgeon.text() or None
                surgery.notes = self.perfusion_notes.toPlainText() or None

            session.add(surgery)

            # If animal didn't survive, mark as deceased
            if not survived:
                subject = session.query(Subject).filter_by(subject_id=subject_id).first()
                if subject:
                    subject.is_active = 0
                    subject.date_of_death = surgery.surgery_date
                    existing_notes = subject.notes or ""
                    subject.notes = f"{existing_notes}\nDied during {surgery_type} surgery".strip()

            session.commit()

        msg = f"Surgery record saved for {subject_id}"
        if not survived:
            msg += "\n\nNote: Animal marked as deceased due to surgery outcome."
        QMessageBox.information(self, "Saved", msg)

        self._load_existing_surgeries(subject_id)


class VirusPrepTab(QWidget):
    """
    Tab for virus preparation and injection calculations.

    The surgeon uses this to:
    - Track which virus is being used (name, lot, titer)
    - Calculate dilutions for preparing the injection solution
    - Calculate total volumes needed for the cohort
    """

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._setup_ui()
        self._load_cohorts()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(15)

        # Cohort Selection
        select_group = QGroupBox("Select Cohort")
        select_layout = QHBoxLayout()

        select_layout.addWidget(QLabel("Cohort:"))
        self.cohort_combo = QComboBox()
        self.cohort_combo.currentIndexChanged.connect(self._on_cohort_changed)
        select_layout.addWidget(self.cohort_combo)

        select_layout.addWidget(QLabel("Prep Date:"))
        self.prep_date = QDateEdit()
        self.prep_date.setCalendarPopup(True)
        self.prep_date.setDate(QDate.currentDate())
        select_layout.addWidget(self.prep_date)

        select_layout.addStretch()
        select_group.setLayout(select_layout)
        main_layout.addWidget(select_group)

        # Create a splitter for form and calculations
        splitter = QSplitter(Qt.Horizontal)

        # Left side: Virus Details Form
        left_widget = QWidget()
        left_layout = QVBoxLayout(left_widget)

        # Virus Information
        virus_group = QGroupBox("Virus Information")
        virus_layout = QFormLayout()

        self.virus_name = QLineEdit()
        self.virus_name.setPlaceholderText("e.g., AAV2-hSyn-GFP")
        virus_layout.addRow("Virus Name:", self.virus_name)

        self.virus_lot = QLineEdit()
        self.virus_lot.setPlaceholderText("Lot number from supplier")
        virus_layout.addRow("Lot Number:", self.virus_lot)

        self.virus_source = QLineEdit()
        self.virus_source.setPlaceholderText("e.g., Addgene, UNC Vector Core")
        virus_layout.addRow("Source:", self.virus_source)

        self.construct_id = QLineEdit()
        self.construct_id.setPlaceholderText("Plasmid/construct ID")
        virus_layout.addRow("Construct ID:", self.construct_id)

        virus_group.setLayout(virus_layout)
        left_layout.addWidget(virus_group)

        # Stock Properties
        stock_group = QGroupBox("Stock Virus Properties")
        stock_layout = QFormLayout()

        self.stock_titer = QDoubleSpinBox()
        self.stock_titer.setRange(1e6, 1e15)
        self.stock_titer.setDecimals(2)
        self.stock_titer.setSuffix(" gc/mL")
        self.stock_titer.setValue(1e13)
        self.stock_titer.setSpecialValueText("-")
        self.stock_titer.valueChanged.connect(self._update_calculations)
        stock_layout.addRow("Stock Titer:", self.stock_titer)

        self.stock_volume = QDoubleSpinBox()
        self.stock_volume.setRange(0, 1000)
        self.stock_volume.setDecimals(1)
        self.stock_volume.setSuffix(" µL")
        self.stock_volume.setValue(5.0)
        self.stock_volume.valueChanged.connect(self._update_calculations)
        stock_layout.addRow("Aliquot Volume:", self.stock_volume)

        self.aliquot_id = QLineEdit()
        self.aliquot_id.setPlaceholderText("Aliquot identifier")
        stock_layout.addRow("Aliquot ID:", self.aliquot_id)

        self.storage_temp = QComboBox()
        self.storage_temp.addItems(["-80°C", "-20°C", "4°C"])
        stock_layout.addRow("Storage Temp:", self.storage_temp)

        self.thaw_date = QDateEdit()
        self.thaw_date.setCalendarPopup(True)
        self.thaw_date.setDate(QDate.currentDate())
        stock_layout.addRow("Thaw Date:", self.thaw_date)

        stock_group.setLayout(stock_layout)
        left_layout.addWidget(stock_group)

        # Dilution Parameters
        dilution_group = QGroupBox("Dilution Parameters")
        dilution_layout = QFormLayout()

        self.target_titer = QDoubleSpinBox()
        self.target_titer.setRange(1e6, 1e15)
        self.target_titer.setDecimals(2)
        self.target_titer.setSuffix(" gc/mL")
        self.target_titer.setValue(1e12)
        self.target_titer.setSpecialValueText("-")
        self.target_titer.valueChanged.connect(self._update_calculations)
        dilution_layout.addRow("Target Titer:", self.target_titer)

        self.diluent = QComboBox()
        self.diluent.addItems(["PBS", "Saline", "Fast Green + PBS", "Other"])
        self.diluent.setEditable(True)
        dilution_layout.addRow("Diluent:", self.diluent)

        dilution_group.setLayout(dilution_layout)
        left_layout.addWidget(dilution_group)

        # Injection Parameters
        injection_group = QGroupBox("Injection Parameters")
        injection_layout = QFormLayout()

        self.injection_volume = QDoubleSpinBox()
        self.injection_volume.setRange(0, 10000)
        self.injection_volume.setDecimals(1)
        self.injection_volume.setSuffix(" nL")
        self.injection_volume.setValue(100.0)
        self.injection_volume.valueChanged.connect(self._update_calculations)
        injection_layout.addRow("Volume per Site:", self.injection_volume)

        self.num_sites = QSpinBox()
        self.num_sites.setRange(1, 20)
        self.num_sites.setValue(1)
        self.num_sites.valueChanged.connect(self._update_calculations)
        injection_layout.addRow("Sites per Animal:", self.num_sites)

        self.num_animals = QSpinBox()
        self.num_animals.setRange(1, 32)
        self.num_animals.setValue(16)
        self.num_animals.valueChanged.connect(self._update_calculations)
        injection_layout.addRow("Number of Animals:", self.num_animals)

        self.overage = QSpinBox()
        self.overage.setRange(0, 100)
        self.overage.setValue(20)
        self.overage.setSuffix("%")
        self.overage.valueChanged.connect(self._update_calculations)
        injection_layout.addRow("Overage Factor:", self.overage)

        injection_group.setLayout(injection_layout)
        left_layout.addWidget(injection_group)

        left_layout.addStretch()
        splitter.addWidget(left_widget)

        # Right side: Calculations Display
        right_widget = QWidget()
        right_layout = QVBoxLayout(right_widget)

        # Calculations Output
        calc_group = QGroupBox("Calculated Values")
        calc_layout = QVBoxLayout()

        # Large display for key calculations
        self.calc_display = QTextEdit()
        self.calc_display.setReadOnly(True)
        self.calc_display.setStyleSheet("""
            QTextEdit {
                background-color: #f0f8ff;
                font-family: 'Consolas', 'Courier New', monospace;
                font-size: 10pt;
                padding: 10px;
            }
        """)
        self.calc_display.setMinimumWidth(350)
        calc_layout.addWidget(self.calc_display)

        calc_group.setLayout(calc_layout)
        right_layout.addWidget(calc_group)

        # Notes
        notes_group = QGroupBox("Preparation Notes")
        notes_layout = QVBoxLayout()

        self.prep_notes = QPlainTextEdit()
        self.prep_notes.setPlaceholderText(
            "Enter any special handling instructions, mixing notes, etc."
        )
        self.prep_notes.setMaximumHeight(100)
        notes_layout.addWidget(self.prep_notes)

        notes_group.setLayout(notes_layout)
        right_layout.addWidget(notes_group)

        # Save Button
        save_layout = QHBoxLayout()
        save_layout.addStretch()

        self.save_btn = QPushButton("Save Virus Prep Record")
        self.save_btn.setObjectName("success_button")
        self.save_btn.clicked.connect(self._save_prep)
        save_layout.addWidget(self.save_btn)

        right_layout.addLayout(save_layout)

        # Existing Preps Table
        existing_group = QGroupBox("Previous Virus Preps for This Cohort")
        existing_layout = QVBoxLayout()

        self.preps_table = QTableWidget()
        self.preps_table.setColumnCount(6)
        self.preps_table.setHorizontalHeaderLabels([
            "Date", "Virus", "Stock Titer", "Target Titer", "Final Volume", "Surgeon"
        ])
        self.preps_table.horizontalHeader().setStretchLastSection(True)
        self.preps_table.setMaximumHeight(150)
        self.preps_table.itemClicked.connect(self._on_prep_selected)
        existing_layout.addWidget(self.preps_table)

        existing_group.setLayout(existing_layout)
        right_layout.addWidget(existing_group)

        right_layout.addStretch()
        splitter.addWidget(right_widget)

        splitter.setSizes([500, 400])
        main_layout.addWidget(splitter)

        # Initial calculation update
        self._update_calculations()

    def _load_cohorts(self):
        """Load available cohorts into dropdown."""
        with self.db.session() as session:
            cohorts = session.query(Cohort).filter(
                (Cohort.is_archived == 0) | (Cohort.is_archived == None)
            ).order_by(Cohort.cohort_id).all()
            self.cohort_combo.clear()
            self.cohort_combo.addItem("-- Select Cohort --", None)
            for c in cohorts:
                # Show number of animals in cohort
                num_subjects = session.query(Subject).filter_by(cohort_id=c.cohort_id).count()
                self.cohort_combo.addItem(f"{c.cohort_id} ({num_subjects} animals)", c.cohort_id)

    def _on_cohort_changed(self, index: int):
        """Handle cohort selection change."""
        cohort_id = self.cohort_combo.currentData()
        if not cohort_id:
            self.preps_table.setRowCount(0)
            return

        # Update number of animals spinner to match cohort size
        with self.db.session() as session:
            num_subjects = session.query(Subject).filter_by(cohort_id=cohort_id).count()
            if num_subjects > 0:
                self.num_animals.setValue(num_subjects)

        self._load_existing_preps(cohort_id)
        self._update_calculations()

    def _load_existing_preps(self, cohort_id: str):
        """Load existing virus prep records for the selected cohort."""
        with self.db.session() as session:
            preps = session.query(VirusPrep).filter_by(
                cohort_id=cohort_id
            ).order_by(VirusPrep.prep_date.desc()).all()

            self.preps_table.setRowCount(len(preps))

            for i, p in enumerate(preps):
                self.preps_table.setItem(i, 0, QTableWidgetItem(str(p.prep_date)))
                self.preps_table.setItem(i, 1, QTableWidgetItem(p.virus_name or ""))
                self.preps_table.setItem(i, 2, QTableWidgetItem(f"{p.stock_titer:.2e}" if p.stock_titer else ""))
                self.preps_table.setItem(i, 3, QTableWidgetItem(f"{p.target_titer:.2e}" if p.target_titer else ""))
                self.preps_table.setItem(i, 4, QTableWidgetItem(f"{p.final_volume_ul:.1f} µL" if p.final_volume_ul else ""))
                self.preps_table.setItem(i, 5, QTableWidgetItem(p.surgeon or ""))

                # Store the prep ID for loading later
                self.preps_table.item(i, 0).setData(Qt.UserRole, p.id)

    def _on_prep_selected(self, item):
        """Load selected prep record into form."""
        row = item.row()
        prep_id = self.preps_table.item(row, 0).data(Qt.UserRole)

        with self.db.session() as session:
            prep = session.query(VirusPrep).filter_by(id=prep_id).first()
            if prep:
                self._load_prep_into_form(prep)

    def _load_prep_into_form(self, prep: VirusPrep):
        """Load a virus prep record into the form fields."""
        self.prep_date.setDate(QDate(prep.prep_date.year, prep.prep_date.month, prep.prep_date.day))
        self.virus_name.setText(prep.virus_name or "")
        self.virus_lot.setText(prep.virus_lot or "")
        self.virus_source.setText(prep.virus_source or "")
        self.construct_id.setText(prep.construct_id or "")

        if prep.stock_titer:
            self.stock_titer.setValue(prep.stock_titer)
        if prep.stock_volume_ul:
            self.stock_volume.setValue(prep.stock_volume_ul)
        self.aliquot_id.setText(prep.aliquot_id or "")

        if prep.storage_temp:
            idx = self.storage_temp.findText(prep.storage_temp)
            if idx >= 0:
                self.storage_temp.setCurrentIndex(idx)

        if prep.thaw_date:
            self.thaw_date.setDate(QDate(prep.thaw_date.year, prep.thaw_date.month, prep.thaw_date.day))

        if prep.target_titer:
            self.target_titer.setValue(prep.target_titer)
        if prep.diluent:
            idx = self.diluent.findText(prep.diluent)
            if idx >= 0:
                self.diluent.setCurrentIndex(idx)
            else:
                self.diluent.setEditText(prep.diluent)

        if prep.injection_volume_nl:
            self.injection_volume.setValue(prep.injection_volume_nl)
        if prep.num_injection_sites:
            self.num_sites.setValue(prep.num_injection_sites)
        if prep.num_animals:
            self.num_animals.setValue(prep.num_animals)

        self.prep_notes.setPlainText(prep.preparation_notes or "")
        self._update_calculations()

    def _update_calculations(self):
        """Update the calculations display based on current values."""
        stock_titer = self.stock_titer.value()
        stock_vol = self.stock_volume.value()
        target_titer = self.target_titer.value()
        inj_vol = self.injection_volume.value()
        num_sites = self.num_sites.value()
        num_animals = self.num_animals.value()
        overage_pct = self.overage.value() / 100.0

        lines = []
        lines.append("=" * 40)
        lines.append("   DILUTION CALCULATIONS")
        lines.append("=" * 40)
        lines.append("")

        # Dilution factor
        if stock_titer > 0 and target_titer > 0:
            dilution_factor = stock_titer / target_titer
            lines.append(f"Dilution Factor: {dilution_factor:.1f}x")
            lines.append(f"  ({stock_titer:.2e} ÷ {target_titer:.2e})")
            lines.append("")

            # Diluent volume needed (C1V1 = C2V2)
            if stock_vol > 0:
                final_vol = (stock_titer * stock_vol) / target_titer
                diluent_vol = final_vol - stock_vol
                lines.append("Dilution Recipe:")
                lines.append(f"  Stock virus:  {stock_vol:.1f} µL")
                lines.append(f"  + Diluent:    {max(0, diluent_vol):.1f} µL")
                lines.append(f"  = Final:      {final_vol:.1f} µL")
                lines.append(f"  @ {target_titer:.2e} gc/mL")
                lines.append("")

        lines.append("=" * 40)
        lines.append("   INJECTION VOLUMES")
        lines.append("=" * 40)
        lines.append("")

        # Per animal and total volumes
        vol_per_animal = inj_vol * num_sites  # nL
        total_vol = vol_per_animal * num_animals  # nL
        total_vol_ul = total_vol / 1000  # µL
        with_overage = total_vol_ul * (1 + overage_pct)

        lines.append(f"Per Animal:")
        lines.append(f"  {inj_vol:.1f} nL × {num_sites} site(s) = {vol_per_animal:.1f} nL")
        lines.append("")
        lines.append(f"Total for {num_animals} Animals:")
        lines.append(f"  {vol_per_animal:.1f} nL × {num_animals} = {total_vol:.1f} nL")
        lines.append(f"                       = {total_vol_ul:.2f} µL")
        lines.append("")
        lines.append(f"With {int(overage_pct * 100)}% Overage:")
        lines.append(f"  PREPARE: {with_overage:.2f} µL")
        lines.append("")

        # Check if we have enough
        if stock_vol > 0 and target_titer > 0 and stock_titer > 0:
            final_vol = (stock_titer * stock_vol) / target_titer
            if final_vol >= with_overage:
                lines.append("✓ Sufficient volume available")
            else:
                lines.append("⚠ WARNING: Insufficient volume!")
                lines.append(f"  Have: {final_vol:.2f} µL")
                lines.append(f"  Need: {with_overage:.2f} µL")

        self.calc_display.setPlainText("\n".join(lines))

    def _save_prep(self):
        """Save the virus prep record."""
        cohort_id = self.cohort_combo.currentData()
        if not cohort_id:
            QMessageBox.warning(self, "Error", "Please select a cohort first.")
            return

        if not self.virus_name.text().strip():
            QMessageBox.warning(self, "Error", "Please enter a virus name.")
            return

        # Calculate final values
        stock_titer = self.stock_titer.value()
        stock_vol = self.stock_volume.value()
        target_titer = self.target_titer.value()
        inj_vol = self.injection_volume.value()
        num_sites = self.num_sites.value()
        num_animals = self.num_animals.value()
        overage_pct = self.overage.value() / 100.0

        diluent_vol = 0
        final_vol = 0
        if stock_titer > 0 and target_titer > 0 and stock_vol > 0:
            final_vol = (stock_titer * stock_vol) / target_titer
            diluent_vol = max(0, final_vol - stock_vol)

        total_per_animal = inj_vol * num_sites
        total_needed = (total_per_animal * num_animals / 1000) * (1 + overage_pct)

        with self.db.session() as session:
            prep = VirusPrep(
                cohort_id=cohort_id,
                prep_date=self.prep_date.date().toPyDate(),
                virus_name=self.virus_name.text().strip(),
                virus_lot=self.virus_lot.text().strip() or None,
                virus_source=self.virus_source.text().strip() or None,
                construct_id=self.construct_id.text().strip() or None,
                stock_titer=stock_titer if stock_titer > 0 else None,
                stock_volume_ul=stock_vol if stock_vol > 0 else None,
                aliquot_id=self.aliquot_id.text().strip() or None,
                storage_temp=self.storage_temp.currentText(),
                thaw_date=self.thaw_date.date().toPyDate(),
                target_titer=target_titer if target_titer > 0 else None,
                diluent=self.diluent.currentText(),
                diluent_volume_ul=diluent_vol if diluent_vol > 0 else None,
                final_volume_ul=final_vol if final_vol > 0 else None,
                final_titer=target_titer if target_titer > 0 else None,
                injection_volume_nl=inj_vol if inj_vol > 0 else None,
                num_injection_sites=num_sites,
                total_volume_per_animal_nl=total_per_animal if total_per_animal > 0 else None,
                num_animals=num_animals,
                total_volume_needed_ul=total_needed if total_needed > 0 else None,
                preparation_notes=self.prep_notes.toPlainText().strip() or None,
                surgeon=self.db.current_user,
                entered_by=self.db.current_user,
            )
            session.add(prep)
            session.commit()

        QMessageBox.information(
            self, "Saved",
            f"Virus prep record saved for {cohort_id}.\n\n"
            f"Final volume: {final_vol:.1f} µL\n"
            f"Target titer: {target_titer:.2e} gc/mL"
        )

        self._load_existing_preps(cohort_id)


class DashboardTab(QWidget):
    """
    Comprehensive dashboard for viewing auto-calculated statistics.

    Displays ALL statistics that matter:
    - Cohort overview with key metrics
    - Pre vs Post injury comparison with effect size
    - Per-phase breakdown
    - Injury statistics
    - Weight tracking
    - Per-subject details
    - Data completeness
    """

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._setup_ui()
        self._load_cohorts()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)

        # Cohort Selection Header
        header_layout = QHBoxLayout()
        header_layout.addWidget(QLabel("Cohort:"))
        self.cohort_combo = QComboBox()
        self.cohort_combo.setMinimumWidth(150)
        self.cohort_combo.currentIndexChanged.connect(self._on_cohort_changed)
        header_layout.addWidget(self.cohort_combo)

        self.refresh_btn = QPushButton("🔬 Generate Analysis")
        self.refresh_btn.setStyleSheet("""
            QPushButton {
                background-color: #1976D2;
                color: white;
                font-weight: bold;
                font-size: 9pt;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #1565C0;
            }
            QPushButton:pressed {
                background-color: #0D47A1;
            }
        """)
        self.refresh_btn.clicked.connect(self._refresh_stats)
        header_layout.addWidget(self.refresh_btn)

        header_layout.addStretch()

        # Data completeness indicator
        self.completeness_label = QLabel("Data: -")
        self.completeness_label.setStyleSheet("font-weight: bold; padding: 5px;")
        header_layout.addWidget(self.completeness_label)

        main_layout.addLayout(header_layout)

        # === EXPECTED TODAY PANEL ===
        self.expected_today_group = QGroupBox("📅 Expected Today")
        self.expected_today_group.setStyleSheet("""
            QGroupBox {
                font-size: 9pt;
                font-weight: bold;
                background-color: #FFF8E1;
                border: 2px solid #FFB300;
                border-radius: 6px;
                margin-top: 8px;
            }
            QGroupBox::title {
                color: #E65100;
            }
        """)
        expected_layout = QVBoxLayout(self.expected_today_group)
        self.expected_today_label = QLabel("Loading...")
        self.expected_today_label.setWordWrap(True)
        self.expected_today_label.setStyleSheet("padding: 8px; font-size: 10pt;")
        expected_layout.addWidget(self.expected_today_label)
        main_layout.addWidget(self.expected_today_group)

        # Load expected today data
        self._refresh_expected_today()

        # === TIMELINE GANTT VIEW ===
        timeline_group = QGroupBox("📊 Cohort Timeline")
        timeline_group.setStyleSheet("""
            QGroupBox {
                font-size: 9pt;
                font-weight: bold;
                background-color: #E8F5E9;
                border: 2px solid #4CAF50;
                border-radius: 6px;
                margin-top: 8px;
            }
            QGroupBox::title {
                color: #2E7D32;
            }
        """)
        timeline_layout = QVBoxLayout(timeline_group)
        self.timeline_widget = TimelineGanttWidget(self.db)
        self.timeline_widget.setMinimumHeight(200)
        self.timeline_widget.setMaximumHeight(300)
        self.timeline_widget.cohort_date_clicked.connect(self._on_timeline_date_clicked)
        timeline_layout.addWidget(self.timeline_widget)
        main_layout.addWidget(timeline_group)

        # Create scroll area for all stats
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll_widget = QWidget()
        scroll_layout = QVBoxLayout(scroll_widget)
        scroll_layout.setSpacing(15)

        # === KEY METRICS (Big Numbers) ===
        key_metrics_group = QGroupBox("Key Metrics")
        key_metrics_group.setStyleSheet("""
            QGroupBox {
                font-size: 10pt;
                font-weight: bold;
                background-color: #E3F2FD;
            }
        """)
        key_layout = QGridLayout(key_metrics_group)

        # Create large stat cards
        self.key_stats = {}
        key_stats_config = [
            ('n_subjects', 'Sample Size', 0, 0),
            ('total_sessions', 'Total Sessions', 0, 1),
            ('total_pellets', 'Total Pellets', 0, 2),
            ('overall_retrieved', 'Overall Retrieved %', 0, 3),
            ('pre_injury_mean', 'Baseline Mean %', 1, 0),
            ('post_injury_mean', 'Post-Injury Mean %', 1, 1),
            ('effect_size', "Cohen's d", 1, 2),
            ('effect_interpret', 'Effect Size', 1, 3),
        ]

        for key, label, row, col in key_stats_config:
            card = self._create_stat_card(label)
            self.key_stats[key] = card
            key_layout.addWidget(card, row, col)

        scroll_layout.addWidget(key_metrics_group)

        # === BASELINE VS POST INJURY COMPARISON ===
        comparison_group = QGroupBox("Baseline vs Post-Injury Comparison")
        comparison_layout = QGridLayout(comparison_group)

        # Headers - store baseline header for dynamic update
        self.comparison_stats = {}
        headers = ['Metric', 'Baseline', 'Post-Injury', 'Change', 'p-value']
        for col, h in enumerate(headers):
            lbl = QLabel(h)
            lbl.setStyleSheet("font-weight: bold; background-color: #E0E0E0; padding: 5px;")
            comparison_layout.addWidget(lbl, 0, col)
            if col == 1:  # Store reference to baseline header
                self.comparison_stats['baseline_header'] = lbl
        metrics = [
            ('retrieved', 'Retrieved %'),
            ('contacted', 'Contacted %'),
            ('displaced', 'Displaced %'),
            ('miss', 'Miss %'),
        ]

        for row, (key, label) in enumerate(metrics, start=1):
            comparison_layout.addWidget(QLabel(label), row, 0)
            self.comparison_stats[f'{key}_pre'] = QLabel('-')
            self.comparison_stats[f'{key}_post'] = QLabel('-')
            self.comparison_stats[f'{key}_change'] = QLabel('-')
            self.comparison_stats[f'{key}_pval'] = QLabel('-')
            comparison_layout.addWidget(self.comparison_stats[f'{key}_pre'], row, 1)
            comparison_layout.addWidget(self.comparison_stats[f'{key}_post'], row, 2)
            comparison_layout.addWidget(self.comparison_stats[f'{key}_change'], row, 3)
            comparison_layout.addWidget(self.comparison_stats[f'{key}_pval'], row, 4)

        scroll_layout.addWidget(comparison_group)

        # === PER-PHASE BREAKDOWN ===
        phase_group = QGroupBox("Performance by Phase")
        phase_layout = QVBoxLayout(phase_group)

        self.phase_table = QTableWidget()
        self.phase_table.setColumnCount(7)
        self.phase_table.setHorizontalHeaderLabels([
            'Phase', 'Sessions', 'Pellets', 'Retrieved %', 'Contacted %', 'Mean ± SD', 'N Animals'
        ])

        # Better column sizing - stretch Phase column, fixed widths for data
        header = self.phase_table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.Stretch)  # Phase stretches
        header.setSectionResizeMode(1, QHeaderView.ResizeToContents)  # Sessions
        header.setSectionResizeMode(2, QHeaderView.ResizeToContents)  # Pellets
        header.setSectionResizeMode(3, QHeaderView.ResizeToContents)  # Retrieved %
        header.setSectionResizeMode(4, QHeaderView.ResizeToContents)  # Contacted %
        header.setSectionResizeMode(5, QHeaderView.ResizeToContents)  # Mean ± SD
        header.setSectionResizeMode(6, QHeaderView.ResizeToContents)  # N Animals

        # Set minimum column widths for readability
        self.phase_table.setColumnWidth(0, 200)  # Phase name
        self.phase_table.setColumnWidth(1, 70)   # Sessions
        self.phase_table.setColumnWidth(2, 70)   # Pellets
        self.phase_table.setColumnWidth(3, 90)   # Retrieved %
        self.phase_table.setColumnWidth(4, 90)   # Contacted %
        self.phase_table.setColumnWidth(5, 100)  # Mean ± SD
        self.phase_table.setColumnWidth(6, 80)   # N Animals

        # Better row height and styling
        self.phase_table.verticalHeader().setDefaultSectionSize(28)
        self.phase_table.verticalHeader().setVisible(False)
        self.phase_table.setAlternatingRowColors(True)
        self.phase_table.setStyleSheet("""
            QTableWidget {
                font-size: 10pt;
                gridline-color: #ddd;
            }
            QTableWidget::item {
                padding: 4px 8px;
            }
            QHeaderView::section {
                background-color: #37474F;
                color: white;
                font-weight: bold;
                padding: 6px;
                border: none;
            }
        """)

        # Allow table to grow but set reasonable min/max
        self.phase_table.setMinimumHeight(150)
        self.phase_table.setMaximumHeight(350)
        phase_layout.addWidget(self.phase_table)

        scroll_layout.addWidget(phase_group)

        # === INJURY STATISTICS ===
        injury_group = QGroupBox("Injury Statistics")
        injury_layout = QGridLayout(injury_group)

        self.injury_stats = {}
        injury_stats_config = [
            ('force_mean', 'Force Mean (kDyn)', 0, 0),
            ('force_sd', 'Force SD', 0, 1),
            ('force_range', 'Force Range', 0, 2),
            ('disp_mean', 'Displacement Mean (µm)', 1, 0),
            ('disp_sd', 'Displacement SD', 1, 1),
            ('disp_range', 'Displacement Range', 1, 2),
            ('survived_n', 'Survived', 2, 0),
            ('mortality_pct', 'Mortality %', 2, 1),
            ('injury_date', 'Injury Date', 2, 2),
        ]

        for key, label, row, col in injury_stats_config:
            frame = QFrame()
            frame.setStyleSheet("background-color: #FFF3E0; border-radius: 5px; padding: 5px;")
            frame_layout = QVBoxLayout(frame)
            frame_layout.setContentsMargins(8, 4, 8, 4)
            name_lbl = QLabel(label)
            name_lbl.setStyleSheet("color: #666; font-size: 9pt;")
            value_lbl = QLabel('-')
            value_lbl.setStyleSheet("font-weight: bold; font-size: 9pt;")
            frame_layout.addWidget(name_lbl)
            frame_layout.addWidget(value_lbl)
            self.injury_stats[key] = value_lbl
            injury_layout.addWidget(frame, row, col)

        scroll_layout.addWidget(injury_group)

        # === WEIGHT TRACKING ===
        weight_group = QGroupBox("Weight Tracking Summary")
        weight_layout = QGridLayout(weight_group)

        self.weight_stats = {}
        weight_stats_config = [
            ('baseline_mean', 'Baseline Mean (g)', 0, 0),
            ('baseline_sd', 'Baseline SD', 0, 1),
            ('current_mean', 'Current Mean (g)', 0, 2),
            ('min_pct', 'Min Weight %', 1, 0),
            ('max_pct', 'Max Weight %', 1, 1),
            ('weights_recorded', 'Weights Recorded', 1, 2),
        ]

        for key, label, row, col in weight_stats_config:
            frame = QFrame()
            frame.setStyleSheet("background-color: #E8F5E9; border-radius: 5px; padding: 5px;")
            frame_layout = QVBoxLayout(frame)
            frame_layout.setContentsMargins(8, 4, 8, 4)
            name_lbl = QLabel(label)
            name_lbl.setStyleSheet("color: #666; font-size: 9pt;")
            value_lbl = QLabel('-')
            value_lbl.setStyleSheet("font-weight: bold; font-size: 9pt;")
            frame_layout.addWidget(name_lbl)
            frame_layout.addWidget(value_lbl)
            self.weight_stats[key] = value_lbl
            weight_layout.addWidget(frame, row, col)

        scroll_layout.addWidget(weight_group)

        # === PER-SUBJECT TABLE ===
        subjects_group = QGroupBox("Individual Subject Summary")
        subjects_layout = QVBoxLayout(subjects_group)

        self.subjects_table = QTableWidget()
        self.subjects_table.setColumnCount(11)
        self.subjects_table.setHorizontalHeaderLabels([
            'Subject', 'Sex', 'Sessions', 'Pellets',
            'Baseline %', 'Post-Injury %', 'Change',
            'Injury (kDyn)', 'Disp (µm)', 'Weight %', 'Status'
        ])
        self.subjects_table.horizontalHeader().setStretchLastSection(True)
        self.subjects_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.subjects_table.setMaximumHeight(250)
        subjects_layout.addWidget(self.subjects_table)

        scroll_layout.addWidget(subjects_group)

        # === SESSION DETAILS (for selected subject) ===
        sessions_group = QGroupBox("Session Details (click subject above)")
        sessions_layout = QVBoxLayout(sessions_group)

        self.sessions_table = QTableWidget()
        self.sessions_table.setColumnCount(10)
        self.sessions_table.setHorizontalHeaderLabels([
            'Date', 'Phase', 'DPI', 'Weight (g)', 'Weight %',
            'Miss', 'Displaced', 'Retrieved', 'Retrieved %', 'Contacted %'
        ])
        self.sessions_table.horizontalHeader().setStretchLastSection(True)
        self.sessions_table.setMaximumHeight(200)
        sessions_layout.addWidget(self.sessions_table)

        scroll_layout.addWidget(sessions_group)

        # === DATA COMPLETENESS ===
        completeness_group = QGroupBox("Data Completeness")
        completeness_layout = QGridLayout(completeness_group)

        self.completeness_stats = {}
        completeness_config = [
            ('expected_sessions', 'Expected Sessions', 0, 0),
            ('actual_sessions', 'Actual Sessions', 0, 1),
            ('session_pct', 'Session Completeness', 0, 2),
            ('expected_pellets', 'Expected Pellets', 1, 0),
            ('actual_pellets', 'Actual Pellets', 1, 1),
            ('pellet_pct', 'Pellet Completeness', 1, 2),
            ('weights_expected', 'Expected Weights', 2, 0),
            ('weights_actual', 'Actual Weights', 2, 1),
            ('weight_pct', 'Weight Completeness', 2, 2),
        ]

        for key, label, row, col in completeness_config:
            frame = QFrame()
            frame.setStyleSheet("background-color: #F3E5F5; border-radius: 5px; padding: 5px;")
            frame_layout = QVBoxLayout(frame)
            frame_layout.setContentsMargins(8, 4, 8, 4)
            name_lbl = QLabel(label)
            name_lbl.setStyleSheet("color: #666; font-size: 9pt;")
            value_lbl = QLabel('-')
            value_lbl.setStyleSheet("font-weight: bold; font-size: 9pt;")
            frame_layout.addWidget(name_lbl)
            frame_layout.addWidget(value_lbl)
            self.completeness_stats[key] = value_lbl
            completeness_layout.addWidget(frame, row, col)

        scroll_layout.addWidget(completeness_group)

        # === BRAINGLOBE RESULTS ===
        brainglobe_group = QGroupBox("BrainGlobe Analysis Results")
        brainglobe_group.setStyleSheet("""
            QGroupBox {
                font-size: 10pt;
                font-weight: bold;
                background-color: #E8EAF6;
            }
        """)
        brainglobe_layout = QVBoxLayout(brainglobe_group)

        # BrainGlobe summary stats
        bg_stats_layout = QGridLayout()
        self.brainglobe_stats = {}
        bg_stats_config = [
            ('brains_total', 'Brains Registered', 0, 0),
            ('brains_detected', 'Brains with Cells', 0, 1),
            ('total_cells', 'Total Cells Detected', 0, 2),
            ('total_regions', 'Regions with Cells', 0, 3),
            ('avg_cells_brain', 'Avg Cells/Brain', 1, 0),
            ('detection_runs', 'Calibration Runs', 1, 1),
            ('best_runs', 'Best Runs Marked', 1, 2),
            ('pending_brains', 'Pending Analysis', 1, 3),
        ]

        for key, label, row, col in bg_stats_config:
            frame = QFrame()
            frame.setStyleSheet("background-color: #C5CAE9; border-radius: 5px; padding: 5px;")
            frame_layout = QVBoxLayout(frame)
            frame_layout.setContentsMargins(8, 4, 8, 4)
            name_lbl = QLabel(label)
            name_lbl.setStyleSheet("color: #303F9F; font-size: 9pt;")
            value_lbl = QLabel('-')
            value_lbl.setStyleSheet("font-weight: bold; font-size: 9pt; color: #1A237E;")
            frame_layout.addWidget(name_lbl)
            frame_layout.addWidget(value_lbl)
            self.brainglobe_stats[key] = value_lbl
            bg_stats_layout.addWidget(frame, row, col)

        brainglobe_layout.addLayout(bg_stats_layout)

        # Brain samples table
        brains_label = QLabel("Brain Samples Status")
        brains_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        brainglobe_layout.addWidget(brains_label)

        self.brains_table = QTableWidget()
        self.brains_table.setColumnCount(7)
        self.brains_table.setHorizontalHeaderLabels([
            'Subject', 'Brain ID', 'Status', 'Cells Detected',
            'Regions', 'Best Run', 'Mag/Z-step'
        ])
        self.brains_table.horizontalHeader().setStretchLastSection(True)
        self.brains_table.setMaximumHeight(150)
        brainglobe_layout.addWidget(self.brains_table)

        # Top regions table (collapsed by default)
        regions_label = QLabel("Top Regions by Cell Count (click brain above)")
        regions_label.setStyleSheet("font-weight: bold; margin-top: 10px;")
        brainglobe_layout.addWidget(regions_label)

        self.regions_table = QTableWidget()
        self.regions_table.setColumnCount(5)
        self.regions_table.setHorizontalHeaderLabels([
            'Region', 'Acronym', 'Hemisphere', 'Cell Count', 'Density'
        ])
        self.regions_table.horizontalHeader().setStretchLastSection(True)
        self.regions_table.setMaximumHeight(150)
        brainglobe_layout.addWidget(self.regions_table)

        scroll_layout.addWidget(brainglobe_group)

        scroll_layout.addStretch()
        scroll.setWidget(scroll_widget)
        main_layout.addWidget(scroll)

        # Connect subject selection
        self.subjects_table.itemSelectionChanged.connect(self._on_subject_selected)
        self.brains_table.itemSelectionChanged.connect(self._on_brain_selected)

    def _create_stat_card(self, label: str) -> QFrame:
        """Create a large stat display card."""
        card = QFrame()
        card.setStyleSheet("""
            QFrame {
                background-color: white;
                border: 2px solid #BBDEFB;
                border-radius: 8px;
                padding: 10px;
            }
        """)
        layout = QVBoxLayout(card)
        layout.setContentsMargins(10, 5, 10, 5)

        name_label = QLabel(label)
        name_label.setStyleSheet("color: #666; font-size: 10pt;")
        name_label.setAlignment(Qt.AlignCenter)

        value_label = QLabel('-')
        value_label.setStyleSheet("font-weight: bold; font-size: 11pt; color: #1976D2;")
        value_label.setAlignment(Qt.AlignCenter)
        value_label.setObjectName("value")

        layout.addWidget(name_label)
        layout.addWidget(value_label)

        return card

    def _get_card_value_label(self, card: QFrame) -> QLabel:
        """Get the value label from a stat card."""
        return card.findChild(QLabel, "value")

    def _load_cohorts(self):
        """Load available cohorts into dropdown."""
        with self.db.session() as session:
            cohorts = session.query(Cohort).filter(
                (Cohort.is_archived == 0) | (Cohort.is_archived == None)
            ).order_by(Cohort.cohort_id).all()
            self.cohort_combo.clear()
            self.cohort_combo.addItem("-- Select Cohort --", None)
            for c in cohorts:
                self.cohort_combo.addItem(c.cohort_id, c.cohort_id)

        # Refresh timeline widget
        if hasattr(self, 'timeline_widget'):
            self.timeline_widget.refresh()

    def _refresh_expected_today(self):
        """Refresh the 'Expected Today' panel showing what data entry is expected."""
        today = date.today()
        expected_items = []

        with self.db.session() as session:
            # Get all active (non-archived) cohorts
            cohorts = session.query(Cohort).filter(
                (Cohort.is_archived == 0) | (Cohort.is_archived == None)
            ).all()

            for cohort in cohorts:
                if cohort.protocol_id:
                    # Use protocol schedule
                    try:
                        expected = protocols.get_expected_data_for_date(
                            session, cohort.cohort_id, today
                        )
                        if expected.get('has_activity'):
                            phase = expected.get('phase_name', 'Unknown')
                            activities = []
                            if expected.get('expects_pellets'):
                                activities.append("pellet scores")
                            if expected.get('expects_weights'):
                                activities.append("weights")
                            if expected.get('expects_surgery'):
                                activities.append("surgery records")

                            if activities:
                                expected_items.append(
                                    f"<b>{cohort.cohort_id}</b>: {phase} - Enter {', '.join(activities)}"
                                )
                    except Exception as e:
                        pass  # Skip cohorts with protocol errors
                else:
                    # Use hardcoded TIMELINE
                    if cohort.start_date:
                        day_offset = (today - cohort.start_date).days
                        for offset, phase, tray_type, trays in TIMELINE:
                            if offset == day_offset:
                                expected_items.append(
                                    f"<b>{cohort.cohort_id}</b>: {phase} - Enter pellet scores ({tray_type} tray, {trays} runs)"
                                )
                                break

        if expected_items:
            self.expected_today_label.setText("<br>".join(expected_items))
            self.expected_today_group.setVisible(True)
        else:
            self.expected_today_label.setText("No testing expected today for any cohort.")
            self.expected_today_group.setVisible(True)

    def _on_timeline_date_clicked(self, cohort_id: str, clicked_date):
        """Handle click on timeline - select the cohort and show info."""
        # Find and select the cohort in the combo
        for i in range(self.cohort_combo.count()):
            if self.cohort_combo.itemData(i) == cohort_id:
                self.cohort_combo.setCurrentIndex(i)
                break

        # Show a message about the clicked date
        if clicked_date:
            QMessageBox.information(
                self, "Timeline Click",
                f"Selected {cohort_id} at date {clicked_date}\n\n"
                f"Use the data entry tabs to enter data for this cohort."
            )

    def _on_cohort_changed(self, index: int):
        """Handle cohort selection change - clear stats but don't auto-analyze."""
        cohort_id = self.cohort_combo.currentData()
        self._clear_stats()

        if cohort_id:
            # Show message prompting user to generate analysis
            self._get_card_value_label(self.key_stats['n_subjects']).setText("Click →")
            self.completeness_label.setText("Click 'Generate Analysis' to load stats")
            self.completeness_label.setStyleSheet(
                "font-weight: bold; padding: 5px; background-color: #FFF3E0; color: #E65100;"
            )

    def _refresh_stats(self):
        """Refresh ALL statistics for the selected cohort."""
        cohort_id = self.cohort_combo.currentData()
        if not cohort_id:
            self._clear_stats()
            return

        with self.db.session() as session:
            cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
            if not cohort:
                return

            # Get all subjects
            subjects = session.query(Subject).filter_by(cohort_id=cohort_id).all()
            n_subjects = len(subjects)

            # Get all pellet scores
            pellet_scores = session.query(PelletScore).filter(
                PelletScore.subject_id.like(f"{cohort_id}%")
            ).all()

            # Get all surgeries
            surgeries = session.query(Surgery).filter(
                Surgery.subject_id.like(f"{cohort_id}%"),
                Surgery.surgery_type == 'contusion'
            ).all()

            # Get all weights
            weights = session.query(Weight).filter(
                Weight.subject_id.like(f"{cohort_id}%")
            ).all()

            # === CALCULATE KEY METRICS ===
            total_pellets = len(pellet_scores)
            total_sessions = len(set((p.subject_id, p.session_date) for p in pellet_scores))
            total_retrieved = sum(1 for p in pellet_scores if p.score == 2)
            overall_retrieved_pct = (total_retrieved / total_pellets * 100) if total_pellets > 0 else 0

            # Pre vs Post injury stats
            # Baseline = Pre-Injury_Test phases, or late Training phases if no Pre-Injury_Test data
            pre_injury_scores = [p for p in pellet_scores if 'Pre-Injury' in p.test_phase]

            # If no Pre-Injury_Test data, use late training phases (Pillar 5-7) as baseline
            baseline_label = "Pre-Injury Test"
            if not pre_injury_scores:
                # Fall back to late training phases (last 3 pillar training days)
                late_training_phases = ['Training_Pillar_5', 'Training_Pillar_6', 'Training_Pillar_7']
                pre_injury_scores = [p for p in pellet_scores
                                    if any(phase in p.test_phase for phase in late_training_phases)]
                if pre_injury_scores:
                    baseline_label = "Late Training"

            post_injury_scores = [p for p in pellet_scores if 'Post-Injury' in p.test_phase]

            pre_retrieved = sum(1 for p in pre_injury_scores if p.score == 2)
            pre_total = len(pre_injury_scores)
            pre_mean = (pre_retrieved / pre_total * 100) if pre_total > 0 else 0

            post_retrieved = sum(1 for p in post_injury_scores if p.score == 2)
            post_total = len(post_injury_scores)
            post_mean = (post_retrieved / post_total * 100) if post_total > 0 else 0

            # Update comparison header to show what baseline is being used
            self.comparison_stats['baseline_header'].setText(baseline_label)

            # Calculate per-subject pre/post for Cohen's d
            pre_by_subject = {}
            post_by_subject = {}

            # Helper function to check if phase is baseline
            def is_baseline_phase(phase):
                if 'Pre-Injury' in phase:
                    return True
                if baseline_label == "Late Training":
                    return any(tp in phase for tp in ['Training_Pillar_5', 'Training_Pillar_6', 'Training_Pillar_7'])
                return False

            for p in pellet_scores:
                if is_baseline_phase(p.test_phase):
                    if p.subject_id not in pre_by_subject:
                        pre_by_subject[p.subject_id] = {'retr': 0, 'total': 0}
                    pre_by_subject[p.subject_id]['total'] += 1
                    if p.score == 2:
                        pre_by_subject[p.subject_id]['retr'] += 1
                elif 'Post-Injury' in p.test_phase:
                    if p.subject_id not in post_by_subject:
                        post_by_subject[p.subject_id] = {'retr': 0, 'total': 0}
                    post_by_subject[p.subject_id]['total'] += 1
                    if p.score == 2:
                        post_by_subject[p.subject_id]['retr'] += 1

            # Calculate Cohen's d
            pre_pcts = []
            post_pcts = []
            for sid in set(pre_by_subject.keys()) & set(post_by_subject.keys()):
                if pre_by_subject[sid]['total'] > 0:
                    pre_pcts.append(pre_by_subject[sid]['retr'] / pre_by_subject[sid]['total'] * 100)
                if post_by_subject[sid]['total'] > 0:
                    post_pcts.append(post_by_subject[sid]['retr'] / post_by_subject[sid]['total'] * 100)

            effect_size = 0
            effect_interpret = '-'
            if pre_pcts and post_pcts:
                import statistics
                pre_mean_subj = statistics.mean(pre_pcts)
                post_mean_subj = statistics.mean(post_pcts)
                try:
                    pre_sd = statistics.stdev(pre_pcts) if len(pre_pcts) > 1 else 0
                    post_sd = statistics.stdev(post_pcts) if len(post_pcts) > 1 else 0
                    pooled_sd = ((pre_sd**2 + post_sd**2) / 2) ** 0.5
                    if pooled_sd > 0:
                        effect_size = (post_mean_subj - pre_mean_subj) / pooled_sd
                        # Interpret effect size
                        abs_d = abs(effect_size)
                        if abs_d < 0.2:
                            effect_interpret = 'Negligible'
                        elif abs_d < 0.5:
                            effect_interpret = 'Small'
                        elif abs_d < 0.8:
                            effect_interpret = 'Medium'
                        else:
                            effect_interpret = 'Large'
                except:
                    pass

            # Update key stats cards
            self._get_card_value_label(self.key_stats['n_subjects']).setText(str(n_subjects))
            self._get_card_value_label(self.key_stats['total_sessions']).setText(str(total_sessions))
            self._get_card_value_label(self.key_stats['total_pellets']).setText(f"{total_pellets:,}")
            self._get_card_value_label(self.key_stats['overall_retrieved']).setText(f"{overall_retrieved_pct:.1f}%")
            self._get_card_value_label(self.key_stats['pre_injury_mean']).setText(f"{pre_mean:.1f}%")
            self._get_card_value_label(self.key_stats['post_injury_mean']).setText(f"{post_mean:.1f}%")
            self._get_card_value_label(self.key_stats['effect_size']).setText(f"{effect_size:.2f}")
            self._get_card_value_label(self.key_stats['effect_interpret']).setText(effect_interpret)

            # Color code the effect size
            if effect_size < 0:
                self._get_card_value_label(self.key_stats['effect_size']).setStyleSheet(
                    "font-weight: bold; font-size: 11pt; color: #D32F2F;")  # Red for decline
            else:
                self._get_card_value_label(self.key_stats['effect_size']).setStyleSheet(
                    "font-weight: bold; font-size: 11pt; color: #388E3C;")  # Green for improvement

            # === PRE VS POST COMPARISON ===
            for metric, calc_fn in [
                ('retrieved', lambda p: p.score == 2),
                ('contacted', lambda p: p.score in (1, 2)),
                ('displaced', lambda p: p.score == 1),
                ('miss', lambda p: p.score == 0),
            ]:
                pre_count = sum(1 for p in pre_injury_scores if calc_fn(p))
                post_count = sum(1 for p in post_injury_scores if calc_fn(p))
                pre_pct = (pre_count / pre_total * 100) if pre_total > 0 else 0
                post_pct = (post_count / post_total * 100) if post_total > 0 else 0
                change = post_pct - pre_pct

                self.comparison_stats[f'{metric}_pre'].setText(f"{pre_pct:.1f}%")
                self.comparison_stats[f'{metric}_post'].setText(f"{post_pct:.1f}%")

                change_text = f"{change:+.1f}%"
                self.comparison_stats[f'{metric}_change'].setText(change_text)
                if change > 0:
                    self.comparison_stats[f'{metric}_change'].setStyleSheet("color: #388E3C; font-weight: bold;")
                elif change < 0:
                    self.comparison_stats[f'{metric}_change'].setStyleSheet("color: #D32F2F; font-weight: bold;")
                else:
                    self.comparison_stats[f'{metric}_change'].setStyleSheet("")

                # Note: proper p-value would need scipy, showing placeholder
                self.comparison_stats[f'{metric}_pval'].setText("-")

            # === PER-PHASE BREAKDOWN ===
            # Track per-session retrieved percentages for Mean ± SD
            phases = {}
            session_retrieved = {}  # {(phase, subject, date): (retrieved, total)}

            for p in pellet_scores:
                phase = p.test_phase
                if phase not in phases:
                    phases[phase] = {'total': 0, 'retr': 0, 'cont': 0, 'subjects': set(), 'sessions': set()}
                phases[phase]['total'] += 1
                if p.score == 2:
                    phases[phase]['retr'] += 1
                if p.score in (1, 2):
                    phases[phase]['cont'] += 1
                phases[phase]['subjects'].add(p.subject_id)
                phases[phase]['sessions'].add((p.subject_id, p.session_date))

                # Track per-session for Mean ± SD calculation
                session_key = (phase, p.subject_id, p.session_date)
                if session_key not in session_retrieved:
                    session_retrieved[session_key] = {'retr': 0, 'total': 0}
                session_retrieved[session_key]['total'] += 1
                if p.score == 2:
                    session_retrieved[session_key]['retr'] += 1

            self.phase_table.setRowCount(len(phases))
            for i, (phase, data) in enumerate(sorted(phases.items())):
                retr_pct = (data['retr'] / data['total'] * 100) if data['total'] > 0 else 0
                cont_pct = (data['cont'] / data['total'] * 100) if data['total'] > 0 else 0

                # Calculate per-session Mean ± SD for this phase
                phase_session_pcts = []
                for key, counts in session_retrieved.items():
                    if key[0] == phase and counts['total'] > 0:
                        phase_session_pcts.append(counts['retr'] / counts['total'] * 100)

                if len(phase_session_pcts) > 1:
                    import statistics
                    mean_pct = statistics.mean(phase_session_pcts)
                    sd_pct = statistics.stdev(phase_session_pcts)
                    mean_sd_text = f"{mean_pct:.1f} ± {sd_pct:.1f}"
                elif phase_session_pcts:
                    mean_sd_text = f"{phase_session_pcts[0]:.1f} ± 0"
                else:
                    mean_sd_text = "-"

                # Create items with proper alignment
                phase_item = QTableWidgetItem(phase)
                sessions_item = QTableWidgetItem(str(len(data['sessions'])))
                pellets_item = QTableWidgetItem(str(data['total']))
                retr_item = QTableWidgetItem(f"{retr_pct:.1f}%")
                cont_item = QTableWidgetItem(f"{cont_pct:.1f}%")
                mean_sd_item = QTableWidgetItem(mean_sd_text)
                animals_item = QTableWidgetItem(str(len(data['subjects'])))

                # Center-align numeric columns
                center_align = Qt.AlignCenter
                sessions_item.setTextAlignment(center_align)
                pellets_item.setTextAlignment(center_align)
                retr_item.setTextAlignment(center_align)
                cont_item.setTextAlignment(center_align)
                mean_sd_item.setTextAlignment(center_align)
                animals_item.setTextAlignment(center_align)

                self.phase_table.setItem(i, 0, phase_item)
                self.phase_table.setItem(i, 1, sessions_item)
                self.phase_table.setItem(i, 2, pellets_item)
                self.phase_table.setItem(i, 3, retr_item)
                self.phase_table.setItem(i, 4, cont_item)
                self.phase_table.setItem(i, 5, mean_sd_item)
                self.phase_table.setItem(i, 6, animals_item)

            # === INJURY STATISTICS ===
            if surgeries:
                forces = [s.force_kdyn for s in surgeries if s.force_kdyn]
                disps = [s.displacement_um for s in surgeries if s.displacement_um]
                survived = sum(1 for s in surgeries if s.survived == 1)

                if forces:
                    import statistics
                    self.injury_stats['force_mean'].setText(f"{statistics.mean(forces):.1f}")
                    self.injury_stats['force_sd'].setText(f"{statistics.stdev(forces):.1f}" if len(forces) > 1 else "-")
                    self.injury_stats['force_range'].setText(f"{min(forces):.1f} - {max(forces):.1f}")

                if disps:
                    self.injury_stats['disp_mean'].setText(f"{statistics.mean(disps):.1f}")
                    self.injury_stats['disp_sd'].setText(f"{statistics.stdev(disps):.1f}" if len(disps) > 1 else "-")
                    self.injury_stats['disp_range'].setText(f"{min(disps):.1f} - {max(disps):.1f}")

                self.injury_stats['survived_n'].setText(f"{survived}/{len(surgeries)}")
                mortality = ((len(surgeries) - survived) / len(surgeries) * 100) if surgeries else 0
                self.injury_stats['mortality_pct'].setText(f"{mortality:.1f}%")

                dates = [s.surgery_date for s in surgeries if s.surgery_date]
                if dates:
                    self.injury_stats['injury_date'].setText(str(min(dates)))

            # === WEIGHT STATISTICS ===
            if weights:
                import statistics
                all_weights = [w.weight_grams for w in weights if w.weight_grams]
                if all_weights:
                    self.weight_stats['baseline_mean'].setText(f"{statistics.mean(all_weights):.1f}")
                    self.weight_stats['baseline_sd'].setText(
                        f"{statistics.stdev(all_weights):.1f}" if len(all_weights) > 1 else "-")
                    self.weight_stats['current_mean'].setText(f"{all_weights[-1]:.1f}" if all_weights else "-")

                weight_pcts = [w.weight_percent for w in weights if w.weight_percent]
                if weight_pcts:
                    self.weight_stats['min_pct'].setText(f"{min(weight_pcts):.1f}%")
                    self.weight_stats['max_pct'].setText(f"{max(weight_pcts):.1f}%")

                self.weight_stats['weights_recorded'].setText(str(len(weights)))

            # === PER-SUBJECT TABLE ===
            self._update_subjects_table(session, cohort_id, pellet_scores, surgeries, weights)

            # === DATA COMPLETENESS ===
            self._update_completeness(session, cohort, n_subjects, pellet_scores, weights)

            # === BRAINGLOBE RESULTS ===
            self._update_brainglobe_stats(session, cohort_id)

    def _update_subjects_table(self, session, cohort_id, pellet_scores, surgeries, weights):
        """Update the per-subject table."""
        subjects = session.query(Subject).filter_by(cohort_id=cohort_id).order_by(Subject.subject_id).all()

        self.subjects_table.setRowCount(len(subjects))

        # Index data by subject
        scores_by_subj = {}
        for p in pellet_scores:
            if p.subject_id not in scores_by_subj:
                scores_by_subj[p.subject_id] = []
            scores_by_subj[p.subject_id].append(p)

        surgeries_by_subj = {s.subject_id: s for s in surgeries}
        weights_by_subj = {}
        for w in weights:
            if w.subject_id not in weights_by_subj:
                weights_by_subj[w.subject_id] = []
            weights_by_subj[w.subject_id].append(w)

        for i, subj in enumerate(subjects):
            scores = scores_by_subj.get(subj.subject_id, [])
            surgery = surgeries_by_subj.get(subj.subject_id)
            subj_weights = weights_by_subj.get(subj.subject_id, [])

            # Baseline calculations - Pre-Injury_Test or late Training phases
            pre_scores = [p for p in scores if 'Pre-Injury' in p.test_phase]
            if not pre_scores:
                # Fall back to late training phases
                late_training = ['Training_Pillar_5', 'Training_Pillar_6', 'Training_Pillar_7']
                pre_scores = [p for p in scores
                             if any(phase in p.test_phase for phase in late_training)]

            post_scores = [p for p in scores if 'Post-Injury' in p.test_phase]

            pre_retr = sum(1 for p in pre_scores if p.score == 2)
            pre_total = len(pre_scores)
            pre_pct = (pre_retr / pre_total * 100) if pre_total > 0 else 0

            post_retr = sum(1 for p in post_scores if p.score == 2)
            post_total = len(post_scores)
            post_pct = (post_retr / post_total * 100) if post_total > 0 else 0

            change = post_pct - pre_pct

            sessions = len(set((p.session_date,) for p in scores))

            self.subjects_table.setItem(i, 0, QTableWidgetItem(subj.subject_id))
            self.subjects_table.setItem(i, 1, QTableWidgetItem(subj.sex or "-"))
            self.subjects_table.setItem(i, 2, QTableWidgetItem(str(sessions)))
            self.subjects_table.setItem(i, 3, QTableWidgetItem(str(len(scores))))
            self.subjects_table.setItem(i, 4, QTableWidgetItem(f"{pre_pct:.1f}%"))
            self.subjects_table.setItem(i, 5, QTableWidgetItem(f"{post_pct:.1f}%"))

            change_item = QTableWidgetItem(f"{change:+.1f}%")
            if change > 0:
                change_item.setBackground(QColor("#C8E6C9"))
            elif change < 0:
                change_item.setBackground(QColor("#FFCDD2"))
            self.subjects_table.setItem(i, 6, change_item)

            self.subjects_table.setItem(i, 7,
                QTableWidgetItem(f"{surgery.force_kdyn:.1f}" if surgery and surgery.force_kdyn else "-"))
            self.subjects_table.setItem(i, 8,
                QTableWidgetItem(f"{surgery.displacement_um:.1f}" if surgery and surgery.displacement_um else "-"))

            # Latest weight %
            if subj_weights:
                latest = max(subj_weights, key=lambda w: w.date)
                wt_pct = latest.weight_percent
                self.subjects_table.setItem(i, 9, QTableWidgetItem(f"{wt_pct:.1f}%" if wt_pct else "-"))
            else:
                self.subjects_table.setItem(i, 9, QTableWidgetItem("-"))

            status = "Active" if subj.is_active else "Excluded"
            self.subjects_table.setItem(i, 10, QTableWidgetItem(status))

            # Store data for session details
            self.subjects_table.item(i, 0).setData(Qt.UserRole, {
                'subject_id': subj.subject_id,
                'scores': scores,
                'weights': subj_weights,
            })

    def _update_completeness(self, session, cohort, n_subjects, pellet_scores, weights):
        """Calculate and display data completeness metrics."""
        # Expected sessions based on TIMELINE
        expected_sessions_per_subject = len(TIMELINE)
        expected_sessions = expected_sessions_per_subject * n_subjects
        actual_sessions = len(set((p.subject_id, p.session_date) for p in pellet_scores))
        session_pct = (actual_sessions / expected_sessions * 100) if expected_sessions > 0 else 0

        # Expected pellets (varies by phase, simplified to 80 per session)
        expected_pellets = expected_sessions * 80  # Approximate
        actual_pellets = len(pellet_scores)
        pellet_pct = (actual_pellets / expected_pellets * 100) if expected_pellets > 0 else 0

        # Expected weights (at least one per testing day)
        expected_weights = expected_sessions  # At least one per session
        actual_weights = len(weights)
        weight_pct = (actual_weights / expected_weights * 100) if expected_weights > 0 else 0

        self.completeness_stats['expected_sessions'].setText(str(expected_sessions))
        self.completeness_stats['actual_sessions'].setText(str(actual_sessions))
        self.completeness_stats['session_pct'].setText(f"{session_pct:.1f}%")

        self.completeness_stats['expected_pellets'].setText(f"{expected_pellets:,}")
        self.completeness_stats['actual_pellets'].setText(f"{actual_pellets:,}")
        self.completeness_stats['pellet_pct'].setText(f"{pellet_pct:.1f}%")

        self.completeness_stats['weights_expected'].setText(str(expected_weights))
        self.completeness_stats['weights_actual'].setText(str(actual_weights))
        self.completeness_stats['weight_pct'].setText(f"{weight_pct:.1f}%")

        # Update header completeness indicator
        overall_pct = (session_pct + pellet_pct + weight_pct) / 3
        if overall_pct >= 90:
            color = "#388E3C"  # Green
            status = "Complete"
        elif overall_pct >= 70:
            color = "#F57C00"  # Orange
            status = "Partial"
        else:
            color = "#D32F2F"  # Red
            status = "Incomplete"

        self.completeness_label.setText(f"Data: {overall_pct:.0f}% {status}")
        self.completeness_label.setStyleSheet(f"font-weight: bold; padding: 5px; color: {color};")

    def _on_subject_selected(self):
        """Show session details for selected subject."""
        items = self.subjects_table.selectedItems()
        if not items:
            self.sessions_table.setRowCount(0)
            return

        row = items[0].row()
        data = self.subjects_table.item(row, 0).data(Qt.UserRole)
        if not data:
            return

        scores = data['scores']
        weights_list = data['weights']

        # Group scores by session
        sessions = {}
        for p in scores:
            key = (p.session_date, p.test_phase)
            if key not in sessions:
                sessions[key] = {'miss': 0, 'disp': 0, 'retr': 0, 'total': 0}
            sessions[key]['total'] += 1
            if p.score == 0:
                sessions[key]['miss'] += 1
            elif p.score == 1:
                sessions[key]['disp'] += 1
            elif p.score == 2:
                sessions[key]['retr'] += 1

        # Index weights by date
        weights_by_date = {w.date: w for w in weights_list}

        self.sessions_table.setRowCount(len(sessions))

        for i, ((sess_date, phase), data) in enumerate(sorted(sessions.items())):
            weight = weights_by_date.get(sess_date)

            # Calculate DPI (Days Post Injury) - would need injury date
            dpi = "-"

            retr_pct = (data['retr'] / data['total'] * 100) if data['total'] > 0 else 0
            cont_pct = ((data['disp'] + data['retr']) / data['total'] * 100) if data['total'] > 0 else 0

            self.sessions_table.setItem(i, 0, QTableWidgetItem(str(sess_date)))
            self.sessions_table.setItem(i, 1, QTableWidgetItem(phase))
            self.sessions_table.setItem(i, 2, QTableWidgetItem(dpi))
            self.sessions_table.setItem(i, 3, QTableWidgetItem(
                f"{weight.weight_grams:.1f}" if weight else "-"))
            self.sessions_table.setItem(i, 4, QTableWidgetItem(
                f"{weight.weight_percent:.1f}%" if weight and weight.weight_percent else "-"))
            self.sessions_table.setItem(i, 5, QTableWidgetItem(str(data['miss'])))
            self.sessions_table.setItem(i, 6, QTableWidgetItem(str(data['disp'])))
            self.sessions_table.setItem(i, 7, QTableWidgetItem(str(data['retr'])))
            self.sessions_table.setItem(i, 8, QTableWidgetItem(f"{retr_pct:.1f}%"))
            self.sessions_table.setItem(i, 9, QTableWidgetItem(f"{cont_pct:.1f}%"))

    def _clear_stats(self):
        """Clear all statistics."""
        # Clear key stats
        for card in self.key_stats.values():
            self._get_card_value_label(card).setText("-")

        # Clear comparison stats (skip baseline_header which is a column header label)
        for key, label in self.comparison_stats.items():
            if key == 'baseline_header':
                label.setText("Baseline")  # Reset to default
            else:
                label.setText("-")
                label.setStyleSheet("")

        # Clear tables
        self.phase_table.setRowCount(0)
        self.subjects_table.setRowCount(0)
        self.sessions_table.setRowCount(0)

        # Clear injury stats
        for label in self.injury_stats.values():
            label.setText("-")

        # Clear weight stats
        for label in self.weight_stats.values():
            label.setText("-")

        # Clear completeness stats
        for label in self.completeness_stats.values():
            label.setText("-")

        self.completeness_label.setText("Data: -")
        self.completeness_label.setStyleSheet("font-weight: bold; padding: 5px;")

        # Clear BrainGlobe stats
        for label in self.brainglobe_stats.values():
            label.setText("-")
        self.brains_table.setRowCount(0)
        self.regions_table.setRowCount(0)

    def _on_brain_selected(self):
        """Show region details for selected brain sample."""
        items = self.brains_table.selectedItems()
        if not items:
            self.regions_table.setRowCount(0)
            return

        row = items[0].row()
        brain_sample_id = self.brains_table.item(row, 0).data(Qt.UserRole)
        if not brain_sample_id:
            return

        with self.db.session() as session:
            # Get top regions for this brain
            regions = session.query(RegionCount).filter_by(
                brain_sample_id=brain_sample_id
            ).order_by(RegionCount.cell_count.desc()).limit(25).all()

            self.regions_table.setRowCount(len(regions))
            for i, reg in enumerate(regions):
                self.regions_table.setItem(i, 0, QTableWidgetItem(reg.region_name or "-"))
                self.regions_table.setItem(i, 1, QTableWidgetItem(reg.region_acronym or "-"))
                self.regions_table.setItem(i, 2, QTableWidgetItem(reg.hemisphere or "both"))
                self.regions_table.setItem(i, 3, QTableWidgetItem(f"{reg.cell_count:,}"))
                # Show density if available
                density_str = f"{reg.cell_density:.1f}" if reg.cell_density else "-"
                self.regions_table.setItem(i, 4, QTableWidgetItem(density_str))

    def _update_brainglobe_stats(self, session, cohort_id):
        """Update BrainGlobe statistics for the cohort."""
        # Get subjects in this cohort
        subjects = session.query(Subject).filter_by(cohort_id=cohort_id).all()
        subject_ids = [s.subject_id for s in subjects]

        if not subject_ids:
            return

        # Get all brain samples for these subjects
        brain_samples = session.query(BrainSample).filter(
            BrainSample.subject_id.in_(subject_ids)
        ).all()

        if not brain_samples:
            # No BrainGlobe data yet
            self.brainglobe_stats['brains_total'].setText("0")
            self.brainglobe_stats['brains_detected'].setText("0")
            self.brainglobe_stats['total_cells'].setText("0")
            self.brainglobe_stats['total_regions'].setText("0")
            self.brainglobe_stats['avg_cells_brain'].setText("-")
            self.brainglobe_stats['detection_runs'].setText("0")
            self.brainglobe_stats['best_runs'].setText("0")
            self.brainglobe_stats['pending_brains'].setText("0")
            return

        brain_sample_ids = [bs.id for bs in brain_samples]

        # Count stats
        brains_total = len(brain_samples)
        brains_detected = sum(1 for bs in brain_samples if bs.status == 'cells_detected')
        pending_brains = sum(1 for bs in brain_samples if bs.status in ('pending', 'registered'))

        # Get total cells
        total_cells = session.query(DetectedCell).filter(
            DetectedCell.brain_sample_id.in_(brain_sample_ids)
        ).count()

        # Get unique regions with cells
        region_counts = session.query(RegionCount).filter(
            RegionCount.brain_sample_id.in_(brain_sample_ids),
            RegionCount.cell_count > 0
        ).all()
        unique_regions = len(set(rc.region_id for rc in region_counts))

        # Get calibration runs
        calibration_runs = session.query(CalibrationRun).filter(
            CalibrationRun.brain_sample_id.in_(brain_sample_ids)
        ).all()
        total_runs = len(calibration_runs)
        best_runs = sum(1 for cr in calibration_runs if cr.is_best == 1)

        # Average cells per brain
        avg_cells = total_cells / brains_detected if brains_detected > 0 else 0

        # Update stats
        self.brainglobe_stats['brains_total'].setText(str(brains_total))
        self.brainglobe_stats['brains_detected'].setText(str(brains_detected))
        self.brainglobe_stats['total_cells'].setText(f"{total_cells:,}")
        self.brainglobe_stats['total_regions'].setText(str(unique_regions))
        self.brainglobe_stats['avg_cells_brain'].setText(f"{avg_cells:,.0f}" if avg_cells > 0 else "-")
        self.brainglobe_stats['detection_runs'].setText(str(total_runs))
        self.brainglobe_stats['best_runs'].setText(str(best_runs))
        self.brainglobe_stats['pending_brains'].setText(str(pending_brains))

        # Update brains table
        self.brains_table.setRowCount(len(brain_samples))
        for i, bs in enumerate(sorted(brain_samples, key=lambda x: x.brain_id)):
            # Count cells for this brain
            cells = session.query(DetectedCell).filter_by(brain_sample_id=bs.id).count()
            regions = session.query(RegionCount).filter(
                RegionCount.brain_sample_id == bs.id,
                RegionCount.cell_count > 0
            ).count()

            # Get best run status
            best_run = session.query(CalibrationRun).filter_by(
                brain_sample_id=bs.id, is_best=1
            ).first()

            self.brains_table.setItem(i, 0, QTableWidgetItem(bs.subject_id))
            self.brains_table.setItem(i, 1, QTableWidgetItem(bs.brain_id))
            self.brains_table.setItem(i, 2, QTableWidgetItem(bs.status or "pending"))
            self.brains_table.setItem(i, 3, QTableWidgetItem(f"{cells:,}" if cells > 0 else "-"))
            self.brains_table.setItem(i, 4, QTableWidgetItem(str(regions) if regions > 0 else "-"))
            self.brains_table.setItem(i, 5, QTableWidgetItem("Yes" if best_run else "No"))
            mag_z = f"{bs.magnification}x / z{bs.z_step_um}" if bs.magnification else "-"
            self.brains_table.setItem(i, 6, QTableWidgetItem(mag_z))

            # Store brain_sample_id for selection handling
            self.brains_table.item(i, 0).setData(Qt.UserRole, bs.id)


class BulkTrayEntryTab(QWidget):
    """
    High-throughput tray entry showing 8 mice × 2 runs on one screen.

    Physical layout matches how pellets come off the belt:
    - 20 pellets arranged as 2 rows × 10 columns
    - Tab/Arrow navigation through all fields
    - Keyboard entry (0/1/2) for fast scoring
    """

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        # Store primitive values to avoid detached session issues
        self.current_cohort_id: Optional[str] = None
        self.current_cohort_start_date: Optional[date] = None
        self.current_date: Optional[date] = None
        self.current_phase: Optional[str] = None
        self.tray_type: str = 'P'
        self.num_trays: int = 2

        # All pellet buttons indexed by (subject_idx, run_idx, pellet_idx)
        self.pellet_buttons: Dict[tuple, PelletButton] = {}
        self.subject_ids: List[str] = []  # Ordered list of subject IDs
        self.current_mouse_group: int = 0  # 0 = mice 1-8, 1 = mice 9-16, etc.

        self._setup_ui()
        self._load_cohorts()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(8)

        # Header controls - Row 1
        header_layout = QHBoxLayout()

        # Cohort selection
        header_layout.addWidget(QLabel("Cohort:"))
        self.cohort_combo = QComboBox()
        self.cohort_combo.setMinimumWidth(120)
        self.cohort_combo.currentIndexChanged.connect(self._on_cohort_changed)
        header_layout.addWidget(self.cohort_combo)

        # Date selection
        header_layout.addWidget(QLabel("Date:"))
        self.date_combo = QComboBox()
        self.date_combo.setMinimumWidth(200)
        self.date_combo.currentIndexChanged.connect(self._on_date_changed)
        header_layout.addWidget(self.date_combo)

        # Phase display
        header_layout.addWidget(QLabel("Phase:"))
        self.phase_label = QLabel("-")
        self.phase_label.setStyleSheet("font-weight: bold; color: #2196F3;")
        header_layout.addWidget(self.phase_label)

        # Protocol info (shown when cohort has protocol assigned)
        self.protocol_info_label = QLabel("")
        self.protocol_info_label.setStyleSheet(
            "font-weight: bold; color: #4CAF50; font-size: 10pt; "
            "padding: 2px 8px; background-color: #E8F5E9; border-radius: 4px;"
        )
        self.protocol_info_label.setVisible(False)
        header_layout.addWidget(self.protocol_info_label)

        header_layout.addStretch()

        # Mouse group selector (for cohorts > 8 mice)
        header_layout.addWidget(QLabel("Mice:"))
        self.mouse_group_combo = QComboBox()
        self.mouse_group_combo.setMinimumWidth(100)
        self.mouse_group_combo.currentIndexChanged.connect(self._on_mouse_group_changed)
        header_layout.addWidget(self.mouse_group_combo)

        # Summary
        self.summary_label = QLabel("Entered: 0 / 0")
        self.summary_label.setStyleSheet("font-weight: bold; font-size: 10pt;")
        header_layout.addWidget(self.summary_label)

        main_layout.addLayout(header_layout)

        # Row 2: Per-tray session stats
        stats_layout = QHBoxLayout()
        self.session_stats_label = QLabel("")
        self.session_stats_label.setStyleSheet("color: #666; font-size: 10pt;")
        stats_layout.addWidget(self.session_stats_label)
        stats_layout.addStretch()
        self.data_status_label = QLabel("")
        self.data_status_label.setStyleSheet("font-weight: bold;")
        stats_layout.addWidget(self.data_status_label)
        main_layout.addLayout(stats_layout)

        # Scroll area for the grid
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        # Grid container
        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setSpacing(10)

        scroll.setWidget(self.grid_container)
        main_layout.addWidget(scroll, stretch=1)

        # Bottom controls
        bottom_layout = QHBoxLayout()

        # Legend
        legend_layout = QHBoxLayout()
        legend_layout.addWidget(QLabel("Legend:"))
        for score, label in [(0, "Miss"), (1, "Displaced"), (2, "Retrieved")]:
            btn = QPushButton(f" {score} = {label} ")
            btn.setProperty("score", str(score))
            btn.setObjectName("pellet_button")
            btn.setEnabled(False)
            btn.setFixedHeight(24)
            legend_layout.addWidget(btn)
        legend_layout.addStretch()
        bottom_layout.addLayout(legend_layout)

        bottom_layout.addStretch()

        # Exception button for issues like spilled tray
        self.exception_btn = QPushButton("Add Exception...")
        self.exception_btn.setStyleSheet("background-color: #FF9800; color: white;")
        self.exception_btn.clicked.connect(self._add_exception)
        self.exception_btn.setToolTip("Record issues like spilled tray, incomplete session, etc.")
        bottom_layout.addWidget(self.exception_btn)

        # Save button
        self.save_btn = QPushButton("Save All")
        self.save_btn.setObjectName("success_button")
        self.save_btn.clicked.connect(self._save_all)
        self.save_btn.setMinimumWidth(120)
        bottom_layout.addWidget(self.save_btn)

        main_layout.addLayout(bottom_layout)

        # Help text
        help_label = QLabel(
            "Keyboard: 0/1/2 to score | Tab/Arrow keys to navigate | "
            "Space/- to clear | Enter to save | Use 'Add Exception' for issues"
        )
        help_label.setStyleSheet("color: #666; font-size: 10pt;")
        main_layout.addWidget(help_label)

    def _load_cohorts(self):
        """Load available cohorts."""
        with self.db.session() as session:
            cohorts = session.query(Cohort).filter(
                (Cohort.is_archived == 0) | (Cohort.is_archived == None)
            ).order_by(Cohort.cohort_id).all()
            self.cohort_combo.clear()
            self.cohort_combo.addItem("-- Select Cohort --", None)
            for c in cohorts:
                self.cohort_combo.addItem(c.cohort_id, c.cohort_id)

    def _on_cohort_changed(self, index: int):
        """Handle cohort selection."""
        cohort_id = self.cohort_combo.currentData()
        if not cohort_id:
            self.current_cohort_id = None
            self.current_cohort_start_date = None
            self.date_combo.clear()
            self.mouse_group_combo.clear()
            self._clear_grid()
            return

        with self.db.session() as session:
            cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()

            if cohort:
                # Store primitive values to avoid detached session issues
                self.current_cohort_id = cohort.cohort_id
                self.current_cohort_start_date = cohort.start_date

                # Load ALL subjects (not just active) so we can see historical data
                subjects = session.query(Subject).filter_by(
                    cohort_id=cohort_id
                ).order_by(Subject.subject_id).all()
                self.subject_ids = [s.subject_id for s in subjects]

                # Populate mouse group selector
                self.mouse_group_combo.clear()
                num_groups = (len(self.subject_ids) + 7) // 8  # Ceiling division
                if num_groups <= 1:
                    self.mouse_group_combo.addItem("All", 0)
                else:
                    for g in range(num_groups):
                        start = g * 8 + 1
                        end = min((g + 1) * 8, len(self.subject_ids))
                        self.mouse_group_combo.addItem(f"Mice {start:02d}-{end:02d}", g)
                    self.mouse_group_combo.addItem("All (scroll)", -1)  # -1 = show all
                self.current_mouse_group = 0

                # Show protocol info if assigned
                if cohort.protocol_id and cohort.protocol:
                    self.protocol_info_label.setText(f"Protocol: {cohort.protocol.name}")
                    self.protocol_info_label.setVisible(True)
                else:
                    self.protocol_info_label.setVisible(False)

                # Load valid dates - use protocol schedule if assigned, else TIMELINE
                self.date_combo.clear()
                self.date_combo.addItem("-- Select Date --", None)
                # Add custom date option for new cohorts or any flexible entry
                self.date_combo.addItem("📅 Custom Date...", "CUSTOM")

                if cohort.protocol_id:
                    # Use protocol-based schedule
                    self._load_protocol_dates(session, cohort)
                else:
                    # Fall back to hardcoded TIMELINE
                    valid_dates = cohort.get_valid_dates()
                    for d, phase, tray_type, trays in valid_dates:
                        self.date_combo.addItem(
                            f"{d.strftime('%Y-%m-%d')} - {phase}",
                            (d, phase, tray_type, trays)
                        )

    def _load_protocol_dates(self, session, cohort):
        """Load dates from protocol schedule for testing phases."""
        try:
            schedule = protocols.generate_schedule(session, cohort.cohort_id)
            today = date.today()

            for phase_info in schedule.get('phases', []):
                phase_name = phase_info['phase_name']
                # Only show phases that expect pellet data
                if not phase_info.get('expects_pellets', True):
                    continue

                tray_code = phase_info.get('tray_type_code', 'P')
                sessions_per_day = phase_info.get('sessions_per_day', 2)

                for day_info in phase_info.get('schedule', []):
                    d = day_info['date']
                    # Mark today's date
                    if d == today:
                        prefix = "🔵 "  # Today indicator
                    elif d < today:
                        prefix = ""  # Past
                    else:
                        prefix = "⚪ "  # Future

                    self.date_combo.addItem(
                        f"{prefix}{d.strftime('%Y-%m-%d')} - {phase_name}",
                        (d, phase_name, tray_code, sessions_per_day)
                    )
        except Exception as e:
            # Fallback to TIMELINE if protocol schedule fails
            print(f"Protocol schedule error: {e}")
            valid_dates = cohort.get_valid_dates()
            for d, phase, tray_type, trays in valid_dates:
                self.date_combo.addItem(
                    f"{d.strftime('%Y-%m-%d')} - {phase}",
                    (d, phase, tray_type, trays)
                )

    def _on_mouse_group_changed(self, index: int):
        """Handle mouse group selection change."""
        group = self.mouse_group_combo.currentData()
        if group is None:
            return
        self.current_mouse_group = group
        if self.current_date:
            self._build_grid()
            self._load_existing_data()

    def _on_date_changed(self, index: int):
        """Handle date selection."""
        data = self.date_combo.currentData()
        if not data:
            self.current_date = None
            self.current_phase = None
            self.phase_label.setText("-")
            self._clear_grid()
            return

        # Handle custom date selection
        if data == "CUSTOM":
            self._show_custom_date_dialog()
            return

        self.current_date, self.current_phase, self.tray_type, self.num_trays = data
        self.phase_label.setText(self.current_phase)

        # Build the grid
        self._build_grid()

        # Load existing data
        self._load_existing_data()

    def _show_custom_date_dialog(self):
        """Show dialog for custom date entry."""
        from PyQt5.QtWidgets import QDialog, QDialogButtonBox

        dialog = QDialog(self)
        dialog.setWindowTitle("Custom Date Entry")
        dialog.setMinimumWidth(300)

        layout = QVBoxLayout(dialog)

        # Date picker
        layout.addWidget(QLabel("Select Date:"))
        date_edit = QDateEdit()
        date_edit.setCalendarPopup(True)
        date_edit.setDate(QDate.currentDate())
        date_edit.setDisplayFormat("yyyy-MM-dd")
        layout.addWidget(date_edit)

        # Phase name
        layout.addWidget(QLabel("Phase/Label (optional):"))
        phase_edit = QLineEdit()
        phase_edit.setPlaceholderText("e.g., Training_Day_1, Custom_Session")
        layout.addWidget(phase_edit)

        # Tray type - load from database
        layout.addWidget(QLabel("Tray Type:"))
        tray_type_combo = QComboBox()
        with self.db.session() as session:
            tray_types = protocols.get_tray_types(session, active_only=True)
            for tt in tray_types:
                tray_type_combo.addItem(f"{tt.name} ({tt.code})", tt.code)
        # Default to Pillar if available
        pillar_idx = tray_type_combo.findData("P")
        if pillar_idx >= 0:
            tray_type_combo.setCurrentIndex(pillar_idx)
        layout.addWidget(tray_type_combo)

        # Number of runs
        layout.addWidget(QLabel("Number of Runs/Trays:"))
        runs_spin = QSpinBox()
        runs_spin.setRange(1, 4)
        runs_spin.setValue(2)
        layout.addWidget(runs_spin)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec_() == QDialog.Accepted:
            # Get values from dialog
            qdate = date_edit.date()
            self.current_date = date(qdate.year(), qdate.month(), qdate.day())
            self.current_phase = phase_edit.text() or f"Custom_{self.current_date.strftime('%Y%m%d')}"
            self.tray_type = tray_type_combo.currentData()
            self.num_trays = runs_spin.value()

            self.phase_label.setText(self.current_phase)

            # Build the grid
            self._build_grid()

            # Load existing data for this date
            self._load_existing_data()
        else:
            # User cancelled - reset to "Select Date"
            self.date_combo.setCurrentIndex(0)

    def _clear_grid(self):
        """Clear all widgets from the grid."""
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.pellet_buttons.clear()
        self._update_summary()

    def _build_grid(self):
        """Build the mice × runs grid based on current mouse group selection."""
        self._clear_grid()

        if not self.subject_ids:
            return

        # Determine which subjects to show
        if self.current_mouse_group == -1:
            # Show all mice
            visible_subjects = self.subject_ids
        else:
            # Show 8-mouse group
            start_idx = self.current_mouse_group * 8
            end_idx = min(start_idx + 8, len(self.subject_ids))
            visible_subjects = self.subject_ids[start_idx:end_idx]

        if not visible_subjects:
            return

        # Header row: Run labels
        self.grid_layout.addWidget(QLabel(""), 0, 0)  # Empty corner

        for run_idx in range(self.num_trays):
            run_label = QLabel(f"Run {run_idx + 1}")
            run_label.setStyleSheet(
                "font-weight: bold; font-size: 9pt; padding: 4px; "
                "background-color: #E3F2FD; border-radius: 4px;"
            )
            run_label.setAlignment(Qt.AlignCenter)
            self.grid_layout.addWidget(run_label, 0, 1 + run_idx)

        # Subject rows
        for display_idx, subject_id in enumerate(visible_subjects):
            row = display_idx + 1

            # Get actual index in full subject list for button storage
            actual_idx = self.subject_ids.index(subject_id)

            # Subject label - show full ID for clarity
            subj_label = QLabel(subject_id)
            subj_label.setToolTip(subject_id)
            subj_label.setStyleSheet(
                "font-weight: bold; font-size: 9pt; padding: 4px; "
                "background-color: #FFF3E0; border-radius: 4px; min-width: 80px;"
            )
            subj_label.setAlignment(Qt.AlignCenter)
            self.grid_layout.addWidget(subj_label, row, 0)

            # Pellet grids for each run
            for run_idx in range(self.num_trays):
                tray_widget = self._create_pellet_grid(actual_idx, run_idx)
                self.grid_layout.addWidget(tray_widget, row, 1 + run_idx)

        # Set tab order for keyboard navigation
        self._setup_tab_order()

        self._update_summary()

    def _create_pellet_grid(self, subj_idx: int, run_idx: int) -> QFrame:
        """Create a 2×10 pellet grid for one mouse's one run."""
        frame = QFrame()
        frame.setFrameStyle(QFrame.StyledPanel)
        frame.setStyleSheet(
            "QFrame { background-color: #FAFAFA; border: 1px solid #DDD; "
            "border-radius: 4px; padding: 4px; }"
        )

        layout = QGridLayout(frame)
        layout.setSpacing(2)
        layout.setContentsMargins(4, 4, 4, 4)

        # Create 2 rows × 10 columns = 20 pellets
        for pellet_idx in range(20):
            row = pellet_idx // 10  # 0 or 1
            col = pellet_idx % 10   # 0-9

            btn = PelletButton(pellet_idx + 1)
            btn.setFixedSize(28, 28)
            btn.setToolTip(f"Pellet {pellet_idx + 1}")
            btn.scoreChanged.connect(self._on_score_changed)

            # Store reference for tab order and saving
            key = (subj_idx, run_idx, pellet_idx)
            self.pellet_buttons[key] = btn

            layout.addWidget(btn, row, col)

        return frame

    def _setup_tab_order(self):
        """Set up tab order for keyboard navigation across all pellets."""
        prev_btn = None

        # Get the subjects currently in the grid
        if self.current_mouse_group == -1:
            visible_indices = range(len(self.subject_ids))
        else:
            start_idx = self.current_mouse_group * 8
            end_idx = min(start_idx + 8, len(self.subject_ids))
            visible_indices = range(start_idx, end_idx)

        # Order: for each subject, for each run, for each pellet
        for subj_idx in visible_indices:
            for run_idx in range(self.num_trays):
                for pellet_idx in range(20):
                    key = (subj_idx, run_idx, pellet_idx)
                    btn = self.pellet_buttons.get(key)
                    if btn and prev_btn:
                        self.setTabOrder(prev_btn, btn)
                    prev_btn = btn

    def _on_score_changed(self, pellet_num: int, score: int):
        """Handle score change - update summary."""
        self._update_summary()

    def _update_summary(self):
        """Update the entry summary."""
        total = 0
        entered = 0
        miss = displaced = retrieved = 0

        for btn in self.pellet_buttons.values():
            total += 1
            if btn.score is not None:
                entered += 1
                if btn.score == 0:
                    miss += 1
                elif btn.score == 1:
                    displaced += 1
                elif btn.score == 2:
                    retrieved += 1

        self.summary_label.setText(
            f"Entered: {entered}/{total} | "
            f"Miss: {miss} | Displaced: {displaced} | Retrieved: {retrieved}"
        )

    def _load_existing_data(self):
        """Load existing pellet scores for all subjects on this date."""
        if not self.current_date:
            self.data_status_label.setText("")
            self.session_stats_label.setText("")
            return

        total_loaded = 0
        subjects_with_data = 0
        session_stats = {'retrieved': 0, 'displaced': 0, 'miss': 0, 'total': 0}

        with self.db.session() as session:
            # Load data for ALL subjects (not just visible) to get full session stats
            all_scores = session.query(PelletScore).filter(
                PelletScore.subject_id.in_(self.subject_ids),
                PelletScore.session_date == self.current_date
            ).all()

            # Calculate full session stats
            for s in all_scores:
                session_stats['total'] += 1
                if s.score == 0:
                    session_stats['miss'] += 1
                elif s.score == 1:
                    session_stats['displaced'] += 1
                elif s.score == 2:
                    session_stats['retrieved'] += 1

            # Group scores by subject
            scores_by_subject = {}
            for s in all_scores:
                if s.subject_id not in scores_by_subject:
                    scores_by_subject[s.subject_id] = []
                scores_by_subject[s.subject_id].append(s)

            # Apply to visible buttons
            for subj_idx, subject_id in enumerate(self.subject_ids):
                scores = scores_by_subject.get(subject_id, [])

                if scores:
                    subjects_with_data += 1

                # Group by tray
                tray_scores = {}
                for s in scores:
                    if s.tray_number not in tray_scores:
                        tray_scores[s.tray_number] = {}
                    tray_scores[s.tray_number][s.pellet_number] = s.score

                # Apply to buttons (only if button exists for this subject)
                for run_idx in range(self.num_trays):
                    tray_num = run_idx + 1
                    scores_dict = tray_scores.get(tray_num, {})

                    for pellet_idx in range(20):
                        key = (subj_idx, run_idx, pellet_idx)
                        btn = self.pellet_buttons.get(key)
                        if btn:
                            score_val = scores_dict.get(pellet_idx + 1)
                            if score_val is not None:
                                total_loaded += 1
                            btn.score = score_val

        self._update_summary()

        # Update data status label
        if session_stats['total'] > 0:
            retrieved_pct = (session_stats['retrieved'] / session_stats['total'] * 100)
            contacted = session_stats['retrieved'] + session_stats['displaced']
            contacted_pct = (contacted / session_stats['total'] * 100)
            self.data_status_label.setText(
                f"✓ Loaded {session_stats['total']} scores from {subjects_with_data} mice"
            )
            self.data_status_label.setStyleSheet("font-weight: bold; color: #388E3C;")
            self.session_stats_label.setText(
                f"Session: {session_stats['total']} pellets | "
                f"Retrieved: {session_stats['retrieved']} ({retrieved_pct:.1f}%) | "
                f"Contacted: {contacted} ({contacted_pct:.1f}%) | "
                f"Miss: {session_stats['miss']}"
            )
        else:
            self.data_status_label.setText("No existing data for this date")
            self.data_status_label.setStyleSheet("font-weight: bold; color: #666;")
            self.session_stats_label.setText("")

        # Update summary to show loaded data status
        if total_loaded > 0:
            # Append to existing summary
            current = self.summary_label.text()
            self.summary_label.setText(f"{current} (loaded {total_loaded})")

    def _save_all(self):
        """Save all pellet scores."""
        if not self.current_date or not self.current_cohort_id:
            QMessageBox.warning(self, "Error", "Please select a cohort and date first.")
            return

        saved_count = 0

        with self.db.session() as session:
            for subj_idx, subject_id in enumerate(self.subject_ids[:8]):
                for run_idx in range(self.num_trays):
                    tray_num = run_idx + 1

                    for pellet_idx in range(20):
                        key = (subj_idx, run_idx, pellet_idx)
                        btn = self.pellet_buttons.get(key)

                        if btn and btn.score is not None:
                            pellet_num = pellet_idx + 1

                            # Check for existing
                            existing = session.query(PelletScore).filter_by(
                                subject_id=subject_id,
                                session_date=self.current_date,
                                tray_type=self.tray_type,
                                tray_number=tray_num,
                                pellet_number=pellet_num
                            ).first()

                            if existing:
                                if existing.score != btn.score:
                                    existing.score = btn.score
                                    existing.entered_by = self.db.current_user
                                    existing.entered_at = datetime.now()
                                    saved_count += 1
                            else:
                                new_score = PelletScore(
                                    subject_id=subject_id,
                                    session_date=self.current_date,
                                    test_phase=self.current_phase or "",
                                    tray_type=self.tray_type,
                                    tray_number=tray_num,
                                    pellet_number=pellet_num,
                                    score=btn.score,
                                    entered_by=self.db.current_user,
                                )
                                session.add(new_score)
                                saved_count += 1

            session.commit()

        QMessageBox.information(
            self, "Saved",
            f"Saved {saved_count} pellet scores for {self.current_date}"
        )

    def _add_exception(self):
        """Show dialog to add a session exception (spilled tray, etc.)."""
        if not self.current_date or not self.current_cohort_id:
            QMessageBox.warning(self, "Error", "Please select a cohort and date first.")
            return

        from PyQt5.QtWidgets import QDialog, QDialogButtonBox

        dialog = QDialog(self)
        dialog.setWindowTitle("Add Session Exception")
        dialog.setMinimumWidth(400)
        layout = QVBoxLayout(dialog)

        # Subject selection
        subject_layout = QHBoxLayout()
        subject_layout.addWidget(QLabel("Mouse:"))
        subject_combo = QComboBox()
        subject_combo.addItem("-- All Mice (Session-wide) --", None)
        for subject_id in self.subject_ids:
            short_id = subject_id.split('_')[-1]
            subject_combo.addItem(f"{short_id} ({subject_id})", subject_id)
        subject_layout.addWidget(subject_combo)
        layout.addLayout(subject_layout)

        # Exception type
        type_layout = QHBoxLayout()
        type_layout.addWidget(QLabel("Issue Type:"))
        type_combo = QComboBox()
        type_combo.addItems([
            "Spilled Tray",
            "Incomplete Session",
            "Equipment Issue",
            "Animal Distress",
            "Early Termination",
            "Other"
        ])
        type_layout.addWidget(type_combo)
        layout.addLayout(type_layout)

        # Tray number (for spilled tray)
        tray_layout = QHBoxLayout()
        tray_layout.addWidget(QLabel("Tray Number (if applicable):"))
        tray_spin = QSpinBox()
        tray_spin.setRange(0, 4)
        tray_spin.setSpecialValueText("N/A")
        tray_spin.setValue(0)
        tray_layout.addWidget(tray_spin)
        layout.addLayout(tray_layout)

        # Description
        layout.addWidget(QLabel("Description:"))
        desc_edit = QPlainTextEdit()
        desc_edit.setMaximumHeight(80)
        desc_edit.setPlaceholderText("Describe what happened...")
        layout.addWidget(desc_edit)

        # Buttons
        buttons = QDialogButtonBox(
            QDialogButtonBox.Save | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec_() == QDialog.Accepted:
            subject_id = subject_combo.currentData()
            exception_type_text = type_combo.currentText()
            tray_num = tray_spin.value() if tray_spin.value() > 0 else None
            description = desc_edit.toPlainText().strip()

            # Map display text to database value
            type_map = {
                "Spilled Tray": "spilled_tray",
                "Incomplete Session": "incomplete_session",
                "Equipment Issue": "equipment_issue",
                "Animal Distress": "animal_distress",
                "Early Termination": "early_termination",
                "Other": "other"
            }
            exception_type = type_map.get(exception_type_text, "other")

            with self.db.session() as session:
                # If "All Mice" selected, create exception for each subject
                subjects_to_add = [subject_id] if subject_id else self.subject_ids

                for sid in subjects_to_add:
                    exception = SessionException(
                        subject_id=sid,
                        session_date=self.current_date,
                        exception_type=exception_type,
                        tray_number=tray_num,
                        description=description,
                        entered_by=self.db.current_user,
                    )
                    session.add(exception)

                session.commit()

            count = len(subjects_to_add)
            QMessageBox.information(
                self, "Exception Recorded",
                f"Added '{exception_type_text}' exception for {count} mouse(es) on {self.current_date}"
            )

    def keyPressEvent(self, event):
        """Handle keyboard shortcuts."""
        if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
            # Ctrl+Enter or just Enter when no button focused saves
            if event.modifiers() == Qt.ControlModifier or not self.focusWidget():
                self._save_all()
                return
        super().keyPressEvent(event)


class BulkWeightEntryTab(QWidget):
    """
    Bulk weight entry for all mice in a cohort on a specific date.

    Features:
    - Enter weights for all mice at once
    - Auto-calculate % of baseline weight
    - Works for ramp phase and regular weight tracking
    - Quick keyboard navigation
    """

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        # Store primitive values to avoid detached session issues
        self.current_cohort_id: Optional[str] = None
        self.current_cohort_start_date: Optional[date] = None
        self.current_date: Optional[date] = None
        self.weight_spinboxes: Dict[str, QDoubleSpinBox] = {}  # subject_id -> spinbox
        self.baseline_weights: Dict[str, float] = {}  # subject_id -> baseline weight

        self._setup_ui()
        self._load_cohorts()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)

        # Header controls
        header_layout = QHBoxLayout()

        # Cohort selection
        header_layout.addWidget(QLabel("Cohort:"))
        self.cohort_combo = QComboBox()
        self.cohort_combo.setMinimumWidth(120)
        self.cohort_combo.currentIndexChanged.connect(self._on_cohort_changed)
        header_layout.addWidget(self.cohort_combo)

        # Date selection
        header_layout.addWidget(QLabel("Date:"))
        self.date_edit = QDateEdit()
        self.date_edit.setCalendarPopup(True)
        self.date_edit.setDate(QDate.currentDate())
        self.date_edit.dateChanged.connect(self._on_date_changed)
        header_layout.addWidget(self.date_edit)

        # Phase indicator
        header_layout.addWidget(QLabel("Phase:"))
        self.phase_label = QLabel("-")
        self.phase_label.setStyleSheet("font-weight: bold; color: #2196F3;")
        header_layout.addWidget(self.phase_label)

        header_layout.addStretch()

        # Summary
        self.summary_label = QLabel("Entered: 0 / 0")
        self.summary_label.setStyleSheet("font-weight: bold;")
        header_layout.addWidget(self.summary_label)

        main_layout.addLayout(header_layout)

        # Weight grid
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setSpacing(8)

        scroll.setWidget(self.grid_container)
        main_layout.addWidget(scroll, stretch=1)

        # Bottom controls
        bottom_layout = QHBoxLayout()

        # Set all weights button
        self.set_baseline_btn = QPushButton("Calculate Baselines")
        self.set_baseline_btn.clicked.connect(self._calculate_baselines)
        self.set_baseline_btn.setToolTip(
            "Calculate baseline weights from first 3 days of data"
        )
        bottom_layout.addWidget(self.set_baseline_btn)

        bottom_layout.addStretch()

        # Save button
        self.save_btn = QPushButton("Save All Weights")
        self.save_btn.setObjectName("success_button")
        self.save_btn.clicked.connect(self._save_all)
        self.save_btn.setMinimumWidth(150)
        bottom_layout.addWidget(self.save_btn)

        main_layout.addLayout(bottom_layout)

        # Help text
        help_label = QLabel(
            "Tab to navigate between fields | Enter to save | "
            "Values auto-calculate % of baseline"
        )
        help_label.setStyleSheet("color: #666; font-size: 10pt;")
        main_layout.addWidget(help_label)

    def _load_cohorts(self):
        """Load available cohorts."""
        with self.db.session() as session:
            cohorts = session.query(Cohort).filter(
                (Cohort.is_archived == 0) | (Cohort.is_archived == None)
            ).order_by(Cohort.cohort_id).all()
            self.cohort_combo.clear()
            self.cohort_combo.addItem("-- Select Cohort --", None)
            for c in cohorts:
                self.cohort_combo.addItem(c.cohort_id, c.cohort_id)

    def _on_cohort_changed(self, index: int):
        """Handle cohort selection."""
        cohort_id = self.cohort_combo.currentData()
        if not cohort_id:
            self.current_cohort_id = None
            self.current_cohort_start_date = None
            self._clear_grid()
            return

        with self.db.session() as session:
            cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
            if cohort:
                # Store primitive values to avoid detached session issues
                self.current_cohort_id = cohort.cohort_id
                self.current_cohort_start_date = cohort.start_date

        self._build_grid()
        self._calculate_baselines()
        self._on_date_changed()

    def _on_date_changed(self):
        """Handle date change - update phase and load existing data."""
        if not self.current_cohort_id or not self.current_cohort_start_date:
            return

        self.current_date = self.date_edit.date().toPyDate()

        # Calculate phase based on days since start
        days_offset = (self.current_date - self.current_cohort_start_date).days

        if days_offset < 0:
            self.phase_label.setText("Before experiment")
        elif days_offset < 4:
            self.phase_label.setText(f"Ramp Day {days_offset}")
        elif days_offset == 17:
            self.phase_label.setText("Injury Day")
        else:
            # Find matching phase from timeline
            phase = None
            for day, name, _, _ in TIMELINE:
                if day == days_offset:
                    phase = name
                    break

            if phase:
                self.phase_label.setText(phase)
            elif days_offset < 17:
                self.phase_label.setText(f"Training (Day {days_offset})")
            elif days_offset < 25:
                self.phase_label.setText(f"Recovery (DPI {days_offset - 17})")
            else:
                self.phase_label.setText(f"Day {days_offset}")

        self._load_existing_data()

    def _clear_grid(self):
        """Clear all widgets from the grid."""
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.weight_spinboxes.clear()
        self._update_summary()

    def _build_grid(self):
        """Build the weight entry grid."""
        self._clear_grid()

        if not self.current_cohort_id:
            return

        with self.db.session() as session:
            subjects = session.query(Subject).filter_by(
                cohort_id=self.current_cohort_id,
                is_active=1
            ).order_by(Subject.subject_id).all()

            # Header row
            headers = ["Subject", "Weight (g)", "% Baseline", "Notes"]
            for col, header in enumerate(headers):
                label = QLabel(header)
                label.setStyleSheet("font-weight: bold; padding: 4px;")
                self.grid_layout.addWidget(label, 0, col)

            # Subject rows
            for row, subj in enumerate(subjects, start=1):
                # Subject ID
                subj_label = QLabel(subj.subject_id.split('_')[-1])
                subj_label.setToolTip(subj.subject_id)
                subj_label.setStyleSheet(
                    "font-weight: bold; padding: 4px; "
                    "background-color: #FFF3E0; border-radius: 4px;"
                )
                self.grid_layout.addWidget(subj_label, row, 0)

                # Weight spinbox
                weight_spin = QDoubleSpinBox()
                weight_spin.setRange(0, 50.0)
                weight_spin.setDecimals(1)
                weight_spin.setSuffix(" g")
                weight_spin.setSpecialValueText("-")
                weight_spin.setValue(0)
                weight_spin.setMinimumWidth(100)
                weight_spin.valueChanged.connect(lambda v, s=subj.subject_id: self._on_weight_changed(s, v))
                self.grid_layout.addWidget(weight_spin, row, 1)
                self.weight_spinboxes[subj.subject_id] = weight_spin

                # Baseline percentage label
                pct_label = QLabel("-")
                pct_label.setMinimumWidth(80)
                pct_label.setObjectName(f"pct_{subj.subject_id}")
                self.grid_layout.addWidget(pct_label, row, 2)

                # Quick status indicator
                status_label = QLabel("")
                status_label.setObjectName(f"status_{subj.subject_id}")
                self.grid_layout.addWidget(status_label, row, 3)

            # Add stretch at bottom
            self.grid_layout.setRowStretch(len(subjects) + 1, 1)

        self._update_summary()

    def _on_weight_changed(self, subject_id: str, value: float):
        """Handle weight value change - update percentage display."""
        if value > 0 and subject_id in self.baseline_weights:
            baseline = self.baseline_weights[subject_id]
            pct = (value / baseline) * 100 if baseline > 0 else 0

            pct_label = self.grid_container.findChild(QLabel, f"pct_{subject_id}")
            if pct_label:
                pct_label.setText(f"{pct:.1f}%")

                # Color code based on weight
                if pct < 80:
                    pct_label.setStyleSheet("color: #F44336; font-weight: bold;")  # Red - too low
                elif pct < 85:
                    pct_label.setStyleSheet("color: #FF9800; font-weight: bold;")  # Orange - warning
                elif pct < 90:
                    pct_label.setStyleSheet("color: #FFC107;")  # Yellow
                else:
                    pct_label.setStyleSheet("color: #4CAF50;")  # Green - good
        else:
            pct_label = self.grid_container.findChild(QLabel, f"pct_{subject_id}")
            if pct_label:
                pct_label.setText("-")
                pct_label.setStyleSheet("")

        self._update_summary()

    def _update_summary(self):
        """Update the entry summary."""
        total = len(self.weight_spinboxes)
        entered = sum(1 for spin in self.weight_spinboxes.values() if spin.value() > 0)
        self.summary_label.setText(f"Entered: {entered} / {total}")

    def _calculate_baselines(self):
        """Calculate baseline weights from first 3 days of data."""
        if not self.current_cohort_id:
            return

        self.baseline_weights.clear()

        with self.db.session() as session:
            for subject_id in self.weight_spinboxes.keys():
                # Get first 3 weights (by date) for this subject
                weights = session.query(Weight).filter_by(
                    subject_id=subject_id
                ).order_by(Weight.date).limit(3).all()

                if weights:
                    avg_weight = sum(w.weight_grams for w in weights) / len(weights)
                    self.baseline_weights[subject_id] = avg_weight

        # Trigger recalculation of percentages
        for subject_id, spin in self.weight_spinboxes.items():
            self._on_weight_changed(subject_id, spin.value())

    def _load_existing_data(self):
        """Load existing weight data for the selected date."""
        if not self.current_cohort_id or not self.current_date:
            return

        with self.db.session() as session:
            for subject_id, spin in self.weight_spinboxes.items():
                existing = session.query(Weight).filter_by(
                    subject_id=subject_id,
                    date=self.current_date
                ).first()

                if existing:
                    spin.setValue(existing.weight_grams)

                    # Mark as already saved
                    status_label = self.grid_container.findChild(QLabel, f"status_{subject_id}")
                    if status_label:
                        status_label.setText("(saved)")
                        status_label.setStyleSheet("color: #4CAF50;")
                else:
                    spin.setValue(0)
                    status_label = self.grid_container.findChild(QLabel, f"status_{subject_id}")
                    if status_label:
                        status_label.setText("")

        self._update_summary()

    def _save_all(self):
        """Save all weight entries."""
        if not self.current_cohort_id or not self.current_date:
            QMessageBox.warning(self, "Error", "Please select a cohort and date first.")
            return

        saved_count = 0
        errors = []

        with self.db.session() as session:
            for subject_id, spin in self.weight_spinboxes.items():
                weight_val = spin.value()

                if weight_val <= 0:
                    continue  # Skip empty entries

                # Calculate percentage if we have baseline
                weight_pct = None
                if subject_id in self.baseline_weights:
                    baseline = self.baseline_weights[subject_id]
                    if baseline > 0:
                        weight_pct = (weight_val / baseline) * 100

                try:
                    existing = session.query(Weight).filter_by(
                        subject_id=subject_id,
                        date=self.current_date
                    ).first()

                    if existing:
                        if existing.weight_grams != weight_val:
                            existing.weight_grams = weight_val
                            existing.weight_percent = weight_pct
                            existing.entered_by = self.db.current_user
                            existing.entered_at = datetime.now()
                            saved_count += 1
                    else:
                        weight = Weight(
                            subject_id=subject_id,
                            date=self.current_date,
                            weight_grams=weight_val,
                            weight_percent=weight_pct,
                            entered_by=self.db.current_user,
                        )
                        session.add(weight)
                        saved_count += 1

                except Exception as e:
                    errors.append(f"{subject_id}: {str(e)}")

            session.commit()

        if errors:
            QMessageBox.warning(
                self, "Partial Save",
                f"Saved {saved_count} weights but had errors:\n" + "\n".join(errors[:5])
            )
        else:
            QMessageBox.information(
                self, "Saved",
                f"Saved {saved_count} weights for {self.current_date}"
            )

        # Refresh status labels
        self._load_existing_data()

    def keyPressEvent(self, event):
        """Handle keyboard shortcuts."""
        if event.key() == Qt.Key_Return or event.key() == Qt.Key_Enter:
            if event.modifiers() == Qt.ControlModifier:
                self._save_all()
                return
        super().keyPressEvent(event)


class RampEntryTab(QWidget):
    """
    Ramp Entry tab for food deprivation phase (Days 0-3).

    During this phase, we track:
    - Body weight for each mouse each day
    - Food tray start/end weights to calculate consumption

    This is a workflow-oriented tab: all data needed for a ramp day
    is entered together.
    """

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        # Store primitive values to avoid detached session issues
        self.current_cohort_id: Optional[str] = None
        self.current_cohort_start_date: Optional[date] = None
        self.current_date: Optional[date] = None
        self.ramp_day: int = 0

        # Widgets for each subject
        self.weight_spins: Dict[str, QDoubleSpinBox] = {}
        self.tray_start_spins: Dict[str, QDoubleSpinBox] = {}
        self.tray_end_spins: Dict[str, QDoubleSpinBox] = {}
        self.pct_labels: Dict[str, QLabel] = {}
        self.consumed_labels: Dict[str, QLabel] = {}
        self.baseline_weights: Dict[str, float] = {}

        self._setup_ui()
        self._load_cohorts()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)

        # Header with helpful context
        header_layout = QHBoxLayout()

        info_label = QLabel(
            "Ramp Phase (Days 0-3): Enter body weights and food tray weights for all mice"
        )
        info_label.setStyleSheet("font-weight: bold; color: #2196F3; font-size: 9pt;")
        header_layout.addWidget(info_label)

        # Protocol info (shown when cohort has protocol assigned)
        self.protocol_info_label = QLabel("")
        self.protocol_info_label.setStyleSheet(
            "font-weight: bold; color: #4CAF50; font-size: 10pt; "
            "padding: 2px 8px; background-color: #E8F5E9; border-radius: 4px;"
        )
        self.protocol_info_label.setVisible(False)
        header_layout.addWidget(self.protocol_info_label)

        header_layout.addStretch()

        main_layout.addLayout(header_layout)

        # Controls row
        controls_layout = QHBoxLayout()

        # Cohort selection
        controls_layout.addWidget(QLabel("Cohort:"))
        self.cohort_combo = QComboBox()
        self.cohort_combo.setMinimumWidth(120)
        self.cohort_combo.currentIndexChanged.connect(self._on_cohort_changed)
        controls_layout.addWidget(self.cohort_combo)

        # Ramp day selection
        controls_layout.addWidget(QLabel("Ramp Day:"))
        self.day_combo = QComboBox()
        self.day_combo.addItems(["Day 0 (First FD)", "Day 1", "Day 2", "Day 3"])
        self.day_combo.currentIndexChanged.connect(self._on_day_changed)
        controls_layout.addWidget(self.day_combo)

        # Date display (auto-calculated)
        controls_layout.addWidget(QLabel("Date:"))
        self.date_label = QLabel("-")
        self.date_label.setStyleSheet("font-weight: bold; min-width: 100px;")
        controls_layout.addWidget(self.date_label)

        controls_layout.addStretch()

        # Summary
        self.summary_label = QLabel("Weights: 0/0 | Food Trays: 0/0")
        self.summary_label.setStyleSheet("font-weight: bold;")
        controls_layout.addWidget(self.summary_label)

        main_layout.addLayout(controls_layout)

        # Main entry grid in scroll area
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        self.grid_container = QWidget()
        self.grid_layout = QGridLayout(self.grid_container)
        self.grid_layout.setSpacing(6)

        scroll.setWidget(self.grid_container)
        main_layout.addWidget(scroll, stretch=1)

        # Bottom controls
        bottom_layout = QHBoxLayout()

        # Quick fill buttons
        quick_fill_label = QLabel("Quick Fill:")
        quick_fill_label.setStyleSheet("font-weight: bold;")
        bottom_layout.addWidget(quick_fill_label)

        self.copy_weights_btn = QPushButton("Copy Weights from Previous Day")
        self.copy_weights_btn.clicked.connect(self._copy_previous_weights)
        self.copy_weights_btn.setToolTip("Copy body weights from the previous ramp day")
        bottom_layout.addWidget(self.copy_weights_btn)

        bottom_layout.addStretch()

        # Save button
        self.save_btn = QPushButton("Save All")
        self.save_btn.setObjectName("success_button")
        self.save_btn.clicked.connect(self._save_all)
        self.save_btn.setMinimumWidth(150)
        bottom_layout.addWidget(self.save_btn)

        main_layout.addLayout(bottom_layout)

        # Help text
        help_label = QLabel(
            "Tab to navigate | Enter body weight, then tray start/end weights | "
            "Food consumed = start - end"
        )
        help_label.setStyleSheet("color: #666; font-size: 10pt;")
        main_layout.addWidget(help_label)

    def _load_cohorts(self):
        """Load available cohorts."""
        with self.db.session() as session:
            cohorts = session.query(Cohort).filter(
                (Cohort.is_archived == 0) | (Cohort.is_archived == None)
            ).order_by(Cohort.cohort_id).all()
            self.cohort_combo.clear()
            self.cohort_combo.addItem("-- Select Cohort --", None)
            for c in cohorts:
                self.cohort_combo.addItem(f"{c.cohort_id} (start: {c.start_date})", c.cohort_id)

    def _on_cohort_changed(self, index: int):
        """Handle cohort selection."""
        cohort_id = self.cohort_combo.currentData()
        if not cohort_id:
            self.current_cohort_id = None
            self.current_cohort_start_date = None
            self._clear_grid()
            return

        with self.db.session() as session:
            cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
            if cohort:
                # Store primitive values to avoid detached session issues
                self.current_cohort_id = cohort.cohort_id
                self.current_cohort_start_date = cohort.start_date

                # Update protocol info in the UI if available
                if hasattr(self, 'protocol_info_label'):
                    if cohort.protocol_id and cohort.protocol:
                        self.protocol_info_label.setText(f"Protocol: {cohort.protocol.name}")
                        self.protocol_info_label.setVisible(True)
                    else:
                        self.protocol_info_label.setVisible(False)

        self._build_grid()
        self._load_baselines()
        self._on_day_changed()

    def _on_day_changed(self):
        """Handle ramp day change."""
        if not self.current_cohort_id or not self.current_cohort_start_date:
            return

        self.ramp_day = self.day_combo.currentIndex()

        # Calculate date
        from datetime import timedelta
        self.current_date = self.current_cohort_start_date + timedelta(days=self.ramp_day)
        self.date_label.setText(self.current_date.strftime("%Y-%m-%d"))

        self._load_existing_data()

    def _clear_grid(self):
        """Clear all widgets from the grid."""
        while self.grid_layout.count():
            item = self.grid_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.weight_spins.clear()
        self.tray_start_spins.clear()
        self.tray_end_spins.clear()
        self.pct_labels.clear()
        self.consumed_labels.clear()
        self._update_summary()

    def _build_grid(self):
        """Build the entry grid for all subjects."""
        self._clear_grid()

        if not self.current_cohort_id:
            return

        with self.db.session() as session:
            subjects = session.query(Subject).filter_by(
                cohort_id=self.current_cohort_id,
                is_active=1
            ).order_by(Subject.subject_id).all()

            # Header row
            headers = ["Mouse", "Body Wt (g)", "% Base", "Tray Start (g)", "Tray End (g)", "Consumed", "Status"]
            for col, header in enumerate(headers):
                label = QLabel(header)
                label.setStyleSheet("font-weight: bold; padding: 4px; background-color: #E3F2FD;")
                self.grid_layout.addWidget(label, 0, col)

            # Subject rows
            for row, subj in enumerate(subjects, start=1):
                subject_id = subj.subject_id
                short_id = subject_id.split('_')[-1]  # Just the number part

                # Mouse ID
                mouse_label = QLabel(short_id)
                mouse_label.setToolTip(subject_id)
                mouse_label.setStyleSheet(
                    "font-weight: bold; padding: 4px; "
                    "background-color: #FFF3E0; border-radius: 4px; min-width: 35px;"
                )
                self.grid_layout.addWidget(mouse_label, row, 0)

                # Body weight
                weight_spin = QDoubleSpinBox()
                weight_spin.setRange(0, 50.0)
                weight_spin.setDecimals(1)
                weight_spin.setSuffix(" g")
                weight_spin.setSpecialValueText("-")
                weight_spin.setValue(0)
                weight_spin.setMinimumWidth(90)
                weight_spin.valueChanged.connect(lambda v, s=subject_id: self._on_weight_changed(s))
                self.grid_layout.addWidget(weight_spin, row, 1)
                self.weight_spins[subject_id] = weight_spin

                # Percentage of baseline
                pct_label = QLabel("-")
                pct_label.setMinimumWidth(60)
                pct_label.setAlignment(Qt.AlignCenter)
                self.grid_layout.addWidget(pct_label, row, 2)
                self.pct_labels[subject_id] = pct_label

                # Tray start weight
                tray_start_spin = QDoubleSpinBox()
                tray_start_spin.setRange(0, 100.0)
                tray_start_spin.setDecimals(1)
                tray_start_spin.setSuffix(" g")
                tray_start_spin.setSpecialValueText("-")
                tray_start_spin.setValue(0)
                tray_start_spin.setMinimumWidth(90)
                tray_start_spin.valueChanged.connect(lambda v, s=subject_id: self._on_tray_changed(s))
                self.grid_layout.addWidget(tray_start_spin, row, 3)
                self.tray_start_spins[subject_id] = tray_start_spin

                # Tray end weight
                tray_end_spin = QDoubleSpinBox()
                tray_end_spin.setRange(0, 100.0)
                tray_end_spin.setDecimals(1)
                tray_end_spin.setSuffix(" g")
                tray_end_spin.setSpecialValueText("-")
                tray_end_spin.setValue(0)
                tray_end_spin.setMinimumWidth(90)
                tray_end_spin.valueChanged.connect(lambda v, s=subject_id: self._on_tray_changed(s))
                self.grid_layout.addWidget(tray_end_spin, row, 4)
                self.tray_end_spins[subject_id] = tray_end_spin

                # Food consumed (calculated)
                consumed_label = QLabel("-")
                consumed_label.setMinimumWidth(60)
                consumed_label.setAlignment(Qt.AlignCenter)
                self.grid_layout.addWidget(consumed_label, row, 5)
                self.consumed_labels[subject_id] = consumed_label

                # Status indicator
                status_label = QLabel("")
                status_label.setObjectName(f"status_{subject_id}")
                self.grid_layout.addWidget(status_label, row, 6)

            # Add stretch at bottom
            self.grid_layout.setRowStretch(len(subjects) + 1, 1)

        self._update_summary()

    def _load_baselines(self):
        """Load baseline (day 0) weights for percentage calculation."""
        self.baseline_weights.clear()

        if not self.current_cohort_id:
            return

        with self.db.session() as session:
            # Get day 0 ramp entries
            entries = session.query(RampEntry).filter(
                RampEntry.subject_id.like(f"{self.current_cohort_id}%"),
                RampEntry.ramp_day == 0
            ).all()

            for entry in entries:
                self.baseline_weights[entry.subject_id] = entry.body_weight_grams

    def _on_weight_changed(self, subject_id: str):
        """Handle weight value change - update percentage display."""
        if subject_id not in self.weight_spins:
            return

        weight = self.weight_spins[subject_id].value()
        pct_label = self.pct_labels.get(subject_id)

        if not pct_label:
            return

        if weight > 0 and subject_id in self.baseline_weights:
            baseline = self.baseline_weights[subject_id]
            pct = (weight / baseline) * 100 if baseline > 0 else 0
            pct_label.setText(f"{pct:.1f}%")

            # Color code based on weight percentage
            if pct < 80:
                pct_label.setStyleSheet("color: #F44336; font-weight: bold;")  # Red
            elif pct < 85:
                pct_label.setStyleSheet("color: #FF9800; font-weight: bold;")  # Orange
            elif pct < 90:
                pct_label.setStyleSheet("color: #FFC107;")  # Yellow
            else:
                pct_label.setStyleSheet("color: #4CAF50;")  # Green
        else:
            pct_label.setText("-")
            pct_label.setStyleSheet("")

        self._update_summary()

    def _on_tray_changed(self, subject_id: str):
        """Handle tray weight change - update consumed display."""
        if subject_id not in self.tray_start_spins:
            return

        start = self.tray_start_spins[subject_id].value()
        end = self.tray_end_spins[subject_id].value()
        consumed_label = self.consumed_labels.get(subject_id)

        if not consumed_label:
            return

        if start > 0 and end > 0:
            consumed = start - end
            consumed_label.setText(f"{consumed:.1f} g")
            if consumed < 0:
                consumed_label.setStyleSheet("color: #F44336;")  # Red if negative
            else:
                consumed_label.setStyleSheet("color: #4CAF50;")  # Green if positive
        else:
            consumed_label.setText("-")
            consumed_label.setStyleSheet("")

        self._update_summary()

    def _update_summary(self):
        """Update the entry summary."""
        total = len(self.weight_spins)
        weights_entered = sum(1 for spin in self.weight_spins.values() if spin.value() > 0)

        trays_complete = 0
        for subject_id in self.tray_start_spins:
            if (self.tray_start_spins[subject_id].value() > 0 and
                self.tray_end_spins[subject_id].value() > 0):
                trays_complete += 1

        self.summary_label.setText(
            f"Weights: {weights_entered}/{total} | Food Trays: {trays_complete}/{total}"
        )

    def _load_existing_data(self):
        """Load existing ramp entry data for the selected day."""
        if not self.current_cohort_id or not self.current_date:
            return

        with self.db.session() as session:
            for subject_id in self.weight_spins:
                existing = session.query(RampEntry).filter_by(
                    subject_id=subject_id,
                    date=self.current_date
                ).first()

                status_label = self.grid_container.findChild(QLabel, f"status_{subject_id}")

                if existing:
                    self.weight_spins[subject_id].setValue(existing.body_weight_grams or 0)
                    self.tray_start_spins[subject_id].setValue(existing.tray_start_grams or 0)
                    self.tray_end_spins[subject_id].setValue(existing.tray_end_grams or 0)

                    if status_label:
                        status_label.setText("(saved)")
                        status_label.setStyleSheet("color: #4CAF50;")
                else:
                    self.weight_spins[subject_id].setValue(0)
                    self.tray_start_spins[subject_id].setValue(0)
                    self.tray_end_spins[subject_id].setValue(0)

                    if status_label:
                        status_label.setText("")

        self._update_summary()

    def _copy_previous_weights(self):
        """Copy weights from the previous ramp day."""
        if not self.current_cohort_id or self.ramp_day == 0:
            QMessageBox.warning(self, "Error", "Cannot copy weights - no previous day.")
            return

        from datetime import timedelta
        previous_date = self.current_date - timedelta(days=1)

        with self.db.session() as session:
            copied = 0
            for subject_id in self.weight_spins:
                existing = session.query(RampEntry).filter_by(
                    subject_id=subject_id,
                    date=previous_date
                ).first()

                if existing and existing.body_weight_grams:
                    self.weight_spins[subject_id].setValue(existing.body_weight_grams)
                    copied += 1

        QMessageBox.information(self, "Copied", f"Copied {copied} weights from previous day.")

    def _save_all(self):
        """Save all ramp entries."""
        if not self.current_cohort_id or not self.current_date:
            QMessageBox.warning(self, "Error", "Please select a cohort and day first.")
            return

        saved_count = 0

        with self.db.session() as session:
            for subject_id in self.weight_spins:
                weight_val = self.weight_spins[subject_id].value()
                tray_start = self.tray_start_spins[subject_id].value()
                tray_end = self.tray_end_spins[subject_id].value()

                # Skip if nothing entered
                if weight_val <= 0 and tray_start <= 0 and tray_end <= 0:
                    continue

                # Calculate percentage if we have baseline
                pct = None
                if subject_id in self.baseline_weights and weight_val > 0:
                    pct = (weight_val / self.baseline_weights[subject_id]) * 100

                # Calculate food consumed
                consumed = None
                if tray_start > 0 and tray_end > 0:
                    consumed = tray_start - tray_end

                try:
                    existing = session.query(RampEntry).filter_by(
                        subject_id=subject_id,
                        date=self.current_date
                    ).first()

                    if existing:
                        if weight_val > 0:
                            existing.body_weight_grams = weight_val
                            existing.weight_percent_baseline = pct
                        if tray_start > 0:
                            existing.tray_start_grams = tray_start
                        if tray_end > 0:
                            existing.tray_end_grams = tray_end
                        existing.food_consumed_grams = consumed
                        existing.entered_by = self.db.current_user
                        existing.entered_at = datetime.now()
                    else:
                        entry = RampEntry(
                            subject_id=subject_id,
                            date=self.current_date,
                            ramp_day=self.ramp_day,
                            body_weight_grams=weight_val if weight_val > 0 else None,
                            weight_percent_baseline=pct,
                            tray_start_grams=tray_start if tray_start > 0 else None,
                            tray_end_grams=tray_end if tray_end > 0 else None,
                            food_consumed_grams=consumed,
                            entered_by=self.db.current_user,
                        )
                        session.add(entry)

                    saved_count += 1

                    # Also update Weight table if body weight was entered
                    if weight_val > 0:
                        existing_weight = session.query(Weight).filter_by(
                            subject_id=subject_id,
                            date=self.current_date
                        ).first()

                        if existing_weight:
                            existing_weight.weight_grams = weight_val
                            existing_weight.weight_percent = pct
                        else:
                            weight_record = Weight(
                                subject_id=subject_id,
                                date=self.current_date,
                                weight_grams=weight_val,
                                weight_percent=pct,
                                entered_by=self.db.current_user,
                            )
                            session.add(weight_record)

                except Exception as e:
                    print(f"Error saving {subject_id}: {e}")

            session.commit()

        QMessageBox.information(
            self, "Saved",
            f"Saved ramp data for {saved_count} mice on Day {self.ramp_day}"
        )

        # Update baselines if this was day 0
        if self.ramp_day == 0:
            self._load_baselines()

        # Refresh status labels
        self._load_existing_data()


class CohortSetupTab(QWidget):
    """
    Tab for creating and managing cohorts (experiments).

    Features:
    - Create new cohorts with project code, number, start date
    - Auto-generate subject IDs for specified number of mice
    - View and edit existing cohorts
    - Import from Excel tracking sheets
    """

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._setup_ui()
        self._refresh_cohorts()

    def _setup_ui(self):
        main_layout = QHBoxLayout(self)
        main_layout.setSpacing(20)

        # Left panel - Create new cohort
        left_panel = QWidget()
        left_layout = QVBoxLayout(left_panel)
        left_layout.setSpacing(15)

        # Create Cohort section
        create_group = QGroupBox("Create New Cohort")
        create_layout = QFormLayout()
        create_layout.setSpacing(12)

        # Project code
        self.project_combo = QComboBox()
        self.project_combo.addItems(['CNT', 'ENCR', 'SCI'])
        self.project_combo.setEditable(True)
        create_layout.addRow("Project Code:", self.project_combo)

        # Cohort number
        self.cohort_num_spin = QSpinBox()
        self.cohort_num_spin.setRange(1, 99)
        self.cohort_num_spin.setValue(1)
        create_layout.addRow("Cohort Number:", self.cohort_num_spin)

        # Preview of cohort ID
        self.cohort_id_preview = QLabel("CNT_01")
        self.cohort_id_preview.setStyleSheet(
            "font-weight: bold; font-size: 10pt; color: #2196F3; "
            "padding: 8px; background-color: #E3F2FD; border-radius: 4px;"
        )
        self.project_combo.currentTextChanged.connect(self._update_cohort_preview)
        self.cohort_num_spin.valueChanged.connect(self._update_cohort_preview)
        create_layout.addRow("Cohort ID:", self.cohort_id_preview)

        # Start date
        self.start_date_edit = QDateEdit()
        self.start_date_edit.setCalendarPopup(True)
        self.start_date_edit.setDate(QDate.currentDate())
        create_layout.addRow("Start Date:", self.start_date_edit)

        # Number of mice
        self.num_mice_spin = QSpinBox()
        self.num_mice_spin.setRange(1, 999)  # No upper limit per user request
        self.num_mice_spin.setValue(16)
        create_layout.addRow("Number of Mice:", self.num_mice_spin)

        # Protocol selection
        protocol_row = QHBoxLayout()
        self.protocol_combo = QComboBox()
        self.protocol_combo.setMinimumWidth(200)
        self._refresh_protocol_combo()
        protocol_row.addWidget(self.protocol_combo)

        self.manage_protocols_btn = QPushButton("Manage...")
        self.manage_protocols_btn.setMaximumWidth(80)
        self.manage_protocols_btn.clicked.connect(self._open_protocol_manager)
        self.manage_protocols_btn.setToolTip("Create and edit protocol templates")
        protocol_row.addWidget(self.manage_protocols_btn)

        protocol_widget = QWidget()
        protocol_widget.setLayout(protocol_row)
        create_layout.addRow("Protocol:", protocol_widget)

        # Notes
        self.notes_edit = QPlainTextEdit()
        self.notes_edit.setMaximumHeight(80)
        self.notes_edit.setPlaceholderText("Optional notes about this cohort...")
        create_layout.addRow("Notes:", self.notes_edit)

        create_group.setLayout(create_layout)
        left_layout.addWidget(create_group)

        # Initial weights section (workflow: enter weights when creating cohort)
        weights_group = QGroupBox("Initial Weights (Day 0 - First Food Deprivation Day)")
        weights_outer_layout = QVBoxLayout()

        # Help text
        weights_help = QLabel(
            "Enter initial body weights for each mouse. These are recorded on the first day "
            "when mice are put on food deprivation."
        )
        weights_help.setWordWrap(True)
        weights_help.setStyleSheet("color: #666; font-size: 9pt; padding: 4px;")
        weights_outer_layout.addWidget(weights_help)

        # Scrollable grid for weight entry
        weights_scroll = QScrollArea()
        weights_scroll.setWidgetResizable(True)
        weights_scroll.setMaximumHeight(200)

        self.weights_container = QWidget()
        self.weights_grid = QGridLayout(self.weights_container)
        self.weights_grid.setSpacing(6)
        weights_scroll.setWidget(self.weights_container)

        weights_outer_layout.addWidget(weights_scroll)

        # Dictionary to hold weight spinboxes
        self.initial_weight_spins: Dict[int, QDoubleSpinBox] = {}

        self.num_mice_spin.valueChanged.connect(self._update_weights_grid)
        self._update_weights_grid()

        weights_group.setLayout(weights_outer_layout)
        left_layout.addWidget(weights_group)

        # Create button
        self.create_btn = QPushButton("Create Cohort")
        self.create_btn.setObjectName("success_button")
        self.create_btn.clicked.connect(self._create_cohort)
        self.create_btn.setMinimumHeight(40)
        left_layout.addWidget(self.create_btn)

        left_layout.addStretch()
        main_layout.addWidget(left_panel)

        # Right panel - Existing cohorts
        right_panel = QWidget()
        right_layout = QVBoxLayout(right_panel)

        # Mini timeline view
        timeline_group = QGroupBox("Timeline Overview")
        timeline_layout = QVBoxLayout()
        self.mini_timeline = MiniTimelineWidget(self.db)
        self.mini_timeline.setMinimumHeight(120)
        self.mini_timeline.cohort_clicked.connect(self._on_timeline_cohort_clicked)
        timeline_layout.addWidget(self.mini_timeline)
        timeline_group.setLayout(timeline_layout)
        right_layout.addWidget(timeline_group)

        existing_group = QGroupBox("Existing Cohorts")
        existing_layout = QVBoxLayout()

        # Cohorts table
        self.cohorts_table = QTableWidget()
        self.cohorts_table.setColumnCount(8)
        self.cohorts_table.setHorizontalHeaderLabels([
            "Cohort ID", "Protocol", "Start Date", "Status", "Progress",
            "Subjects", "Sessions", "Notes"
        ])
        self.cohorts_table.horizontalHeader().setStretchLastSection(True)
        self.cohorts_table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        self.cohorts_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.cohorts_table.itemSelectionChanged.connect(self._on_cohort_selected)
        existing_layout.addWidget(self.cohorts_table)

        # Cohort actions row
        cohort_actions = QHBoxLayout()
        self.show_archived_cb = QCheckBox("Show Archived")
        self.show_archived_cb.stateChanged.connect(lambda: self._refresh_cohorts())
        cohort_actions.addWidget(self.show_archived_cb)
        cohort_actions.addStretch()
        self.archive_cohort_btn = QPushButton("Archive Cohort...")
        self.archive_cohort_btn.setEnabled(False)
        self.archive_cohort_btn.setStyleSheet("background-color: #D32F2F; color: white;")
        self.archive_cohort_btn.clicked.connect(self._archive_cohort)
        cohort_actions.addWidget(self.archive_cohort_btn)
        existing_layout.addLayout(cohort_actions)

        existing_group.setLayout(existing_layout)
        right_layout.addWidget(existing_group)

        # Selected cohort details
        details_group = QGroupBox("Selected Cohort Details")
        details_layout = QVBoxLayout()

        # Subjects list for selected cohort
        self.subjects_table = QTableWidget()
        self.subjects_table.setColumnCount(10)
        self.subjects_table.setHorizontalHeaderLabels([
            "Subject ID", "Sex", "Active", "DoD", "Sessions",
            "Force (kDyn)", "Disp (µm)", "Last Wt", "Base Wt", "Wt %"
        ])
        self.subjects_table.horizontalHeader().setStretchLastSection(True)
        details_layout.addWidget(self.subjects_table)

        # Subject action buttons
        subject_actions = QHBoxLayout()

        self.mark_deceased_btn = QPushButton("Mark Deceased...")
        self.mark_deceased_btn.clicked.connect(self._mark_subject_deceased)
        self.mark_deceased_btn.setEnabled(False)
        subject_actions.addWidget(self.mark_deceased_btn)

        self.mark_removed_btn = QPushButton("Mark Removed")
        self.mark_removed_btn.clicked.connect(self._mark_subject_removed)
        self.mark_removed_btn.setEnabled(False)
        subject_actions.addWidget(self.mark_removed_btn)

        self.reactivate_btn = QPushButton("Reactivate")
        self.reactivate_btn.clicked.connect(self._reactivate_subject)
        self.reactivate_btn.setEnabled(False)
        self.reactivate_btn.setStyleSheet("background-color: #4CAF50;")
        subject_actions.addWidget(self.reactivate_btn)

        subject_actions.addStretch()

        details_layout.addLayout(subject_actions)

        # Connect selection to enable/disable buttons
        self.subjects_table.itemSelectionChanged.connect(self._on_subject_table_selection)

        # Action buttons
        actions_layout = QHBoxLayout()

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._refresh_cohorts)
        actions_layout.addWidget(self.refresh_btn)

        actions_layout.addStretch()

        details_layout.addLayout(actions_layout)
        details_group.setLayout(details_layout)
        right_layout.addWidget(details_group)

        main_layout.addWidget(right_panel, stretch=1)

    def _update_cohort_preview(self):
        """Update the cohort ID preview."""
        project = self.project_combo.currentText().upper()
        num = self.cohort_num_spin.value()
        cohort_id = f"{project}_{num:02d}"
        self.cohort_id_preview.setText(cohort_id)
        self._update_subjects_preview()

    def _update_weights_grid(self):
        """Update the weight entry grid based on number of mice."""
        # Clear existing widgets
        while self.weights_grid.count():
            item = self.weights_grid.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        self.initial_weight_spins.clear()

        num_mice = self.num_mice_spin.value()
        project = self.project_combo.currentText().upper()
        cohort_num = self.cohort_num_spin.value()

        # Create grid: 4 columns of (label, spinbox)
        cols = 4
        for i in range(num_mice):
            mouse_num = i + 1
            row = i // cols
            col = (i % cols) * 2  # Each entry takes 2 columns (label + spinbox)

            # Label with subject short ID
            label = QLabel(f"_{mouse_num:02d}:")
            label.setToolTip(f"{project}_{cohort_num:02d}_{mouse_num:02d}")
            label.setStyleSheet("font-weight: bold; min-width: 30px;")
            self.weights_grid.addWidget(label, row, col)

            # Weight spinbox
            spin = QDoubleSpinBox()
            spin.setRange(0, 50.0)
            spin.setDecimals(1)
            spin.setSuffix(" g")
            spin.setSpecialValueText("-")
            spin.setValue(0)
            spin.setMinimumWidth(80)
            spin.setToolTip(f"Initial weight for mouse {mouse_num}")
            self.weights_grid.addWidget(spin, row, col + 1)

            self.initial_weight_spins[mouse_num] = spin

    def _update_subjects_preview(self):
        """Update the subjects preview text - no longer used but kept for compatibility."""
        pass

    def _create_cohort(self):
        """Create the new cohort and subjects."""
        project = self.project_combo.currentText().upper().strip()
        cohort_num = self.cohort_num_spin.value()
        cohort_id = f"{project}_{cohort_num:02d}"
        start_date = self.start_date_edit.date().toPyDate()
        num_mice = self.num_mice_spin.value()
        notes = self.notes_edit.toPlainText().strip() or None

        # Get selected protocol
        protocol_id = self.protocol_combo.currentData()  # None if "-- No Protocol --"

        # Check if cohort already exists
        with self.db.session() as session:
            existing = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
            if existing:
                QMessageBox.warning(
                    self, "Error",
                    f"Cohort {cohort_id} already exists!\n\n"
                    f"Start date: {existing.start_date}\n"
                    f"Please choose a different cohort number."
                )
                return

            # Ensure project exists
            proj = session.query(Project).filter_by(project_code=project).first()
            if not proj:
                proj = Project(
                    project_code=project,
                    project_name=f"{project} Project"
                )
                session.add(proj)

            # Get protocol version if assigned
            protocol_version = None
            if protocol_id:
                proto = session.query(Protocol).filter_by(id=protocol_id).first()
                if proto:
                    protocol_version = proto.version

            # Create cohort
            cohort = Cohort(
                cohort_id=cohort_id,
                project_code=project,
                start_date=start_date,
                notes=notes,
                protocol_id=protocol_id,
                protocol_version=protocol_version
            )
            session.add(cohort)

            # Create subjects and their initial weights
            weights_saved = 0
            for i in range(1, num_mice + 1):
                subject_id = f"{project}_{cohort_num:02d}_{i:02d}"
                subject = Subject(
                    subject_id=subject_id,
                    cohort_id=cohort_id,
                    is_active=1
                )
                session.add(subject)

                # Save initial weight if entered (ramp day 0)
                if i in self.initial_weight_spins:
                    weight_val = self.initial_weight_spins[i].value()
                    if weight_val > 0:
                        # Create RampEntry for day 0
                        ramp_entry = RampEntry(
                            subject_id=subject_id,
                            date=start_date,
                            ramp_day=0,
                            body_weight_grams=weight_val,
                            weight_percent_baseline=100.0,  # Day 0 is baseline
                            entered_by=self.db.current_user,
                        )
                        session.add(ramp_entry)

                        # Also create Weight record for consistency
                        weight_record = Weight(
                            subject_id=subject_id,
                            date=start_date,
                            weight_grams=weight_val,
                            weight_percent=100.0,
                            entered_by=self.db.current_user,
                        )
                        session.add(weight_record)
                        weights_saved += 1

            session.commit()

            # If protocol assigned, generate empty records for tracking
            records_generated = {}
            if protocol_id:
                try:
                    records_generated = protocols.generate_empty_records(
                        session, cohort_id, overwrite=False
                    )
                except Exception as e:
                    print(f"Warning: Could not generate empty records: {e}")

        # Build success message
        msg = f"Created cohort {cohort_id} with {num_mice} subjects!\n\n"
        msg += f"Start date: {start_date}\n"
        if protocol_id and protocol_version:
            proto_name = self.protocol_combo.currentText()
            msg += f"Protocol: {proto_name}\n"
        if weights_saved > 0:
            msg += f"Initial weights recorded: {weights_saved}\n"
        if records_generated:
            total_records = sum(records_generated.values())
            msg += f"Pre-generated records: {total_records}\n"
        msg += "\nYou can continue in the Ramp Entry tab to enter remaining ramp days."

        QMessageBox.information(self, "Success", msg)

        # Refresh the table
        self._refresh_cohorts()

        # Clear form
        self.notes_edit.clear()
        self.cohort_num_spin.setValue(cohort_num + 1)  # Increment for next cohort

        # Clear weight spinboxes
        for spin in self.initial_weight_spins.values():
            spin.setValue(0)

    def _refresh_cohorts(self):
        """Refresh the cohorts table."""
        with self.db.session() as session:
            query = session.query(Cohort)
            if not self.show_archived_cb.isChecked():
                query = query.filter(
                    (Cohort.is_archived == 0) | (Cohort.is_archived == None)
                )
            cohorts = query.order_by(Cohort.cohort_id).all()

            self.cohorts_table.setRowCount(len(cohorts))

            for i, cohort in enumerate(cohorts):
                # Col 0: Cohort ID
                self.cohorts_table.setItem(i, 0, QTableWidgetItem(cohort.cohort_id))

                # Col 1: Protocol name
                proto_name = "-"
                if cohort.protocol_id:
                    proto = session.query(Protocol).filter_by(id=cohort.protocol_id).first()
                    if proto:
                        proto_name = proto.name
                self.cohorts_table.setItem(i, 1, QTableWidgetItem(proto_name))

                # Col 2: Start date
                self.cohorts_table.setItem(i, 2, QTableWidgetItem(str(cohort.start_date)))

                # Col 3: Status
                status_text, status_color = self._get_cohort_status(session, cohort)
                status_item = QTableWidgetItem(status_text)
                status_item.setForeground(QColor(status_color))
                self.cohorts_table.setItem(i, 3, status_item)

                # Col 4: Progress (completion percentage)
                progress = self._get_cohort_progress(session, cohort)
                progress_item = QTableWidgetItem(f"{progress:.0f}%")
                if progress >= 90:
                    progress_item.setForeground(QColor("#4CAF50"))  # Green
                elif progress >= 50:
                    progress_item.setForeground(QColor("#FF9800"))  # Orange
                else:
                    progress_item.setForeground(QColor("#F44336"))  # Red
                self.cohorts_table.setItem(i, 4, progress_item)

                # Col 5: Subject count
                subject_count = session.query(Subject).filter_by(
                    cohort_id=cohort.cohort_id
                ).count()
                self.cohorts_table.setItem(i, 5, QTableWidgetItem(str(subject_count)))

                # Col 6: Session count (unique date/subject combos with pellet data)
                from sqlalchemy import func
                session_count = session.query(
                    func.count(func.distinct(
                        PelletScore.subject_id + '-' + func.cast(PelletScore.session_date, type_=String)
                    ))
                ).filter(
                    PelletScore.subject_id.like(f"{cohort.cohort_id}%")
                ).scalar() or 0
                self.cohorts_table.setItem(i, 6, QTableWidgetItem(str(session_count)))

                # Col 7: Notes (truncated)
                notes_text = cohort.notes or ""
                if len(notes_text) > 50:
                    notes_text = notes_text[:47] + "..."
                self.cohorts_table.setItem(i, 7, QTableWidgetItem(notes_text))

                # Store cohort_id for selection
                self.cohorts_table.item(i, 0).setData(Qt.UserRole, cohort.cohort_id)

                # Gray out archived cohorts
                if cohort.is_archived:
                    for col in range(self.cohorts_table.columnCount()):
                        item = self.cohorts_table.item(i, col)
                        if item:
                            item.setForeground(QColor("#999999"))

        self.subjects_table.setRowCount(0)

        # Refresh mini timeline
        if hasattr(self, 'mini_timeline'):
            self.mini_timeline.refresh()

    def _archive_cohort(self):
        """Archive (soft-delete) the selected cohort with confirmation."""
        selected = self.cohorts_table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        cohort_id = self.cohorts_table.item(row, 0).data(Qt.UserRole)
        if not cohort_id:
            return

        # Count related data
        with self.db.session() as session:
            cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
            if not cohort:
                return
            subject_count = len(cohort.subjects)
            from sqlalchemy import func
            weight_count = session.query(func.count(Weight.id)).filter(
                Weight.subject_id.like(f"{cohort_id}%")
            ).scalar() or 0
            pellet_count = session.query(func.count(PelletScore.id)).filter(
                PelletScore.subject_id.like(f"{cohort_id}%")
            ).scalar() or 0
            surgery_count = session.query(func.count(Surgery.id)).filter(
                Surgery.subject_id.like(f"{cohort_id}%")
            ).scalar() or 0

        # Build warning message
        data_summary = []
        if subject_count:
            data_summary.append(f"  - {subject_count} subjects")
        if weight_count:
            data_summary.append(f"  - {weight_count} weight records")
        if pellet_count:
            data_summary.append(f"  - {pellet_count} pellet scores")
        if surgery_count:
            data_summary.append(f"  - {surgery_count} surgery records")

        msg = f"Archive cohort {cohort_id}?\n\n"
        if data_summary:
            msg += "This cohort contains:\n" + "\n".join(data_summary) + "\n\n"
        msg += "Archived cohorts are hidden from all views but data is preserved.\n"
        msg += "You can restore archived cohorts using 'Show Archived'."

        reply = QMessageBox.warning(
            self, "Archive Cohort",
            msg,
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel
        )

        if reply != QMessageBox.Yes:
            return

        # Perform archive
        with self.db.session() as session:
            cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
            if cohort:
                cohort.is_archived = 1
                cohort.archived_at = datetime.now()
                cohort.archived_reason = "User archived from Cohort Setup"
                session.commit()

        self._refresh_cohorts()
        QMessageBox.information(self, "Archived", f"Cohort {cohort_id} has been archived.")

    def _unarchive_cohort(self):
        """Restore an archived cohort."""
        selected = self.cohorts_table.selectedItems()
        if not selected:
            return
        row = selected[0].row()
        cohort_id = self.cohorts_table.item(row, 0).data(Qt.UserRole)
        if not cohort_id:
            return

        reply = QMessageBox.question(
            self, "Restore Cohort",
            f"Restore cohort {cohort_id} from archive?",
            QMessageBox.Yes | QMessageBox.Cancel,
            QMessageBox.Cancel
        )
        if reply != QMessageBox.Yes:
            return

        with self.db.session() as session:
            cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
            if cohort:
                cohort.is_archived = 0
                cohort.archived_at = None
                cohort.archived_reason = None
                session.commit()

        self._refresh_cohorts()
        QMessageBox.information(self, "Restored", f"Cohort {cohort_id} has been restored.")

    def _on_timeline_cohort_clicked(self, cohort_id: str):
        """Handle click on a cohort in the mini timeline."""
        # Find the cohort in the table and select it
        for row in range(self.cohorts_table.rowCount()):
            item = self.cohorts_table.item(row, 0)
            if item and item.data(Qt.UserRole) == cohort_id:
                self.cohorts_table.selectRow(row)
                break

    def _on_cohort_selected(self):
        """Show subjects for selected cohort."""
        selected_items = self.cohorts_table.selectedItems()
        self.archive_cohort_btn.setEnabled(bool(selected_items))

        if not selected_items:
            self.subjects_table.setRowCount(0)
            return

        row = selected_items[0].row()
        cohort_id = self.cohorts_table.item(row, 0).data(Qt.UserRole)

        # Toggle archive/restore button based on cohort state
        with self.db.session() as session:
            cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
            if cohort and cohort.is_archived:
                self.archive_cohort_btn.setText("Restore Cohort...")
                self.archive_cohort_btn.setStyleSheet("background-color: #4CAF50; color: white;")
                try: self.archive_cohort_btn.clicked.disconnect()
                except: pass
                self.archive_cohort_btn.clicked.connect(self._unarchive_cohort)
            else:
                self.archive_cohort_btn.setText("Archive Cohort...")
                self.archive_cohort_btn.setStyleSheet("background-color: #D32F2F; color: white;")
                try: self.archive_cohort_btn.clicked.disconnect()
                except: pass
                self.archive_cohort_btn.clicked.connect(self._archive_cohort)

        with self.db.session() as session:
            subjects = session.query(Subject).filter_by(
                cohort_id=cohort_id
            ).order_by(Subject.subject_id).all()

            self.subjects_table.setRowCount(len(subjects))

            for i, subj in enumerate(subjects):
                # Col 0: Subject ID
                self.subjects_table.setItem(i, 0, QTableWidgetItem(subj.subject_id))

                # Col 1: Sex
                self.subjects_table.setItem(i, 1, QTableWidgetItem(subj.sex or "-"))

                # Col 2: Active status
                status = "Yes" if subj.is_active else "No"
                self.subjects_table.setItem(i, 2, QTableWidgetItem(status))

                # Col 3: Date of Death
                dod_text = str(subj.date_of_death) if subj.date_of_death else "-"
                self.subjects_table.setItem(i, 3, QTableWidgetItem(dod_text))

                # Col 4: Session count
                session_count = session.query(
                    PelletScore.session_date
                ).filter_by(
                    subject_id=subj.subject_id
                ).distinct().count()
                self.subjects_table.setItem(i, 4, QTableWidgetItem(str(session_count)))

                # Col 5 & 6: Injury force and displacement
                surgery = session.query(Surgery).filter_by(
                    subject_id=subj.subject_id,
                    surgery_type='contusion'
                ).first()
                if surgery:
                    force_text = f"{surgery.force_kdyn:.1f}" if surgery.force_kdyn else "-"
                    disp_text = f"{surgery.displacement_um:.0f}" if surgery.displacement_um else "-"
                else:
                    force_text = "-"
                    disp_text = "-"
                self.subjects_table.setItem(i, 5, QTableWidgetItem(force_text))
                self.subjects_table.setItem(i, 6, QTableWidgetItem(disp_text))

                # Col 7, 8, 9: Last Weight, Baseline Weight, Weight %
                # Get latest weight
                from sqlalchemy import desc
                latest_weight = session.query(Weight).filter_by(
                    subject_id=subj.subject_id
                ).order_by(desc(Weight.date)).first()

                # Get baseline (earliest weight or ramp day 0)
                baseline_entry = session.query(RampEntry).filter_by(
                    subject_id=subj.subject_id,
                    ramp_day=0
                ).first()
                baseline_wt = baseline_entry.body_weight_grams if baseline_entry else None

                if latest_weight:
                    self.subjects_table.setItem(i, 7, QTableWidgetItem(f"{latest_weight.weight_grams:.1f}"))
                else:
                    self.subjects_table.setItem(i, 7, QTableWidgetItem("-"))

                if baseline_wt:
                    self.subjects_table.setItem(i, 8, QTableWidgetItem(f"{baseline_wt:.1f}"))
                else:
                    self.subjects_table.setItem(i, 8, QTableWidgetItem("-"))

                # Weight percentage
                if latest_weight and baseline_wt and baseline_wt > 0:
                    wt_pct = (latest_weight.weight_grams / baseline_wt) * 100
                    wt_item = QTableWidgetItem(f"{wt_pct:.1f}%")
                    # Color code weight percentage
                    if wt_pct < 80:
                        wt_item.setForeground(QColor("#F44336"))  # Red - too low
                    elif wt_pct < 85:
                        wt_item.setForeground(QColor("#FF9800"))  # Orange - warning
                    else:
                        wt_item.setForeground(QColor("#4CAF50"))  # Green - healthy
                    self.subjects_table.setItem(i, 9, wt_item)
                else:
                    self.subjects_table.setItem(i, 9, QTableWidgetItem("-"))

                # Store subject_id and is_active for actions
                self.subjects_table.item(i, 0).setData(Qt.UserRole, subj.subject_id)
                self.subjects_table.item(i, 0).setData(Qt.UserRole + 1, subj.is_active)

                # Visual indication for inactive subjects
                if not subj.is_active:
                    for col in range(10):
                        item = self.subjects_table.item(i, col)
                        if item:
                            item.setForeground(QColor("#999999"))
                            item.setBackground(QColor("#F5F5F5"))

    def _on_subject_table_selection(self):
        """Enable/disable subject action buttons based on selection."""
        items = self.subjects_table.selectedItems()
        if not items:
            self.mark_deceased_btn.setEnabled(False)
            self.mark_removed_btn.setEnabled(False)
            self.reactivate_btn.setEnabled(False)
            return

        row = items[0].row()
        is_active = self.subjects_table.item(row, 0).data(Qt.UserRole + 1)

        self.mark_deceased_btn.setEnabled(is_active)
        self.mark_removed_btn.setEnabled(is_active)
        self.reactivate_btn.setEnabled(not is_active)

    def _get_selected_subject_id(self) -> Optional[str]:
        """Get the currently selected subject ID."""
        items = self.subjects_table.selectedItems()
        if not items:
            return None
        row = items[0].row()
        return self.subjects_table.item(row, 0).data(Qt.UserRole)

    def _mark_subject_deceased(self):
        """Mark selected subject as deceased with date."""
        subject_id = self._get_selected_subject_id()
        if not subject_id:
            return

        # Show date picker dialog
        from PyQt5.QtWidgets import QDialog, QDialogButtonBox

        dialog = QDialog(self)
        dialog.setWindowTitle("Mark Subject Deceased")
        layout = QVBoxLayout(dialog)

        layout.addWidget(QLabel(f"Mark {subject_id} as deceased"))
        layout.addWidget(QLabel("Date of death:"))

        death_date = QDateEdit()
        death_date.setCalendarPopup(True)
        death_date.setDate(QDate.currentDate())
        layout.addWidget(death_date)

        notes_label = QLabel("Notes (optional):")
        layout.addWidget(notes_label)

        notes_edit = QPlainTextEdit()
        notes_edit.setMaximumHeight(60)
        layout.addWidget(notes_edit)

        buttons = QDialogButtonBox(
            QDialogButtonBox.Ok | QDialogButtonBox.Cancel
        )
        buttons.accepted.connect(dialog.accept)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)

        if dialog.exec_() == QDialog.Accepted:
            death_date_val = death_date.date().toPyDate()
            notes_val = notes_edit.toPlainText().strip()

            with self.db.session() as session:
                subject = session.query(Subject).filter_by(subject_id=subject_id).first()
                if subject:
                    subject.date_of_death = death_date_val
                    subject.is_active = 0
                    if notes_val:
                        existing_notes = subject.notes or ""
                        subject.notes = f"{existing_notes}\nDeceased: {notes_val}".strip()
                    session.commit()

            QMessageBox.information(
                self, "Updated",
                f"{subject_id} marked as deceased on {death_date_val}"
            )

            # Refresh the subjects table
            self._on_cohort_selected()

    def _mark_subject_removed(self):
        """Mark selected subject as removed (inactive) without death date."""
        subject_id = self._get_selected_subject_id()
        if not subject_id:
            return

        reply = QMessageBox.question(
            self, "Confirm Removal",
            f"Mark {subject_id} as removed from the study?\n\n"
            "This subject will be marked inactive and excluded from future entries.",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            with self.db.session() as session:
                subject = session.query(Subject).filter_by(subject_id=subject_id).first()
                if subject:
                    subject.is_active = 0
                    existing_notes = subject.notes or ""
                    subject.notes = f"{existing_notes}\nRemoved from study: {date.today()}".strip()
                    session.commit()

            QMessageBox.information(self, "Updated", f"{subject_id} marked as removed")
            self._on_cohort_selected()

    def _reactivate_subject(self):
        """Reactivate a previously removed/deceased subject."""
        subject_id = self._get_selected_subject_id()
        if not subject_id:
            return

        reply = QMessageBox.question(
            self, "Confirm Reactivation",
            f"Reactivate {subject_id}?\n\n"
            "This will make the subject active again for data entry.",
            QMessageBox.Yes | QMessageBox.No
        )

        if reply == QMessageBox.Yes:
            with self.db.session() as session:
                subject = session.query(Subject).filter_by(subject_id=subject_id).first()
                if subject:
                    subject.is_active = 1
                    subject.date_of_death = None
                    existing_notes = subject.notes or ""
                    subject.notes = f"{existing_notes}\nReactivated: {date.today()}".strip()
                    session.commit()

            QMessageBox.information(self, "Updated", f"{subject_id} reactivated")
            self._on_cohort_selected()

    def _refresh_protocol_combo(self):
        """Refresh the protocol dropdown with available protocols."""
        self.protocol_combo.clear()
        self.protocol_combo.addItem("-- No Protocol --", None)

        with self.db.session() as session:
            protocol_list = protocols.list_protocols(session, active_only=True)
            for proto in protocol_list:
                # Show name with phase count
                phase_count = len(proto.phases)
                display_text = f"{proto.name} ({phase_count} phases)"
                self.protocol_combo.addItem(display_text, proto.id)

    def _open_protocol_manager(self):
        """Open the Protocol Manager dialog."""
        from .protocol_builder import ProtocolBuilderDialog

        dialog = ProtocolBuilderDialog(self.db, parent=self)
        if dialog.exec_():
            # Refresh protocol combo after changes
            self._refresh_protocol_combo()

    def _get_cohort_status(self, session, cohort: Cohort) -> Tuple[str, str]:
        """
        Determine cohort status based on protocol phase.

        Returns (status_text, status_color)
        """
        if not cohort.protocol_id:
            return ("No Protocol", "#888888")

        # Get current phase info
        current_phase = protocols.get_cohort_current_phase(session, cohort.cohort_id)
        if current_phase:
            if current_phase.get('completed'):
                return ("Completed", "#4CAF50")  # Green
            else:
                phase_name = current_phase.get('phase_name', 'Unknown')
                return (phase_name, "#2196F3")  # Blue
        return ("Not Started", "#FF9800")  # Orange

    def _get_cohort_progress(self, session, cohort: Cohort) -> float:
        """Calculate data entry completeness percentage for a cohort."""
        if not cohort.protocol_id:
            # Without protocol, just check if has any pellet data
            pellet_count = session.query(PelletScore).filter(
                PelletScore.subject_id.like(f"{cohort.cohort_id}%")
            ).count()
            return 100.0 if pellet_count > 0 else 0.0

        # With protocol, check expected vs actual records
        try:
            summary = protocols.get_protocol_summary(session, cohort.protocol_id)
            # Rough estimate: expected pellets per phase that expects them
            total_expected = 0
            for phase in summary.get('phases', []):
                if phase.get('expects_pellets'):
                    # 80 pellets per session per subject * sessions_per_day * duration
                    subjects = session.query(Subject).filter_by(
                        cohort_id=cohort.cohort_id, is_active=1
                    ).count()
                    sessions = phase.get('sessions_per_day', 1)
                    days = phase.get('duration_days', 1)
                    total_expected += 80 * sessions * days * subjects

            if total_expected == 0:
                return 0.0

            actual = session.query(PelletScore).filter(
                PelletScore.subject_id.like(f"{cohort.cohort_id}%")
            ).count()
            return min(100.0, (actual / total_expected) * 100)
        except Exception:
            return 0.0


class VisualizationTab(QWidget):
    """Tab for interactive data visualization with publication-quality charts."""

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.current_figure = None
        self._setup_ui()
        self._load_cohorts()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)

        # Controls row
        controls_layout = QHBoxLayout()

        # Cohort selection
        controls_layout.addWidget(QLabel("Cohort:"))
        self.cohort_combo = QComboBox()
        self.cohort_combo.currentIndexChanged.connect(self._on_cohort_changed)
        controls_layout.addWidget(self.cohort_combo)

        # Chart type selection
        controls_layout.addWidget(QLabel("Chart:"))
        self.chart_combo = QComboBox()
        self.chart_combo.addItems([
            "Learning Curve",
            "Phase Comparison",
            "Recovery Trajectory",
            "Weight Tracking",
            "Pellet Heatmap",
        ])
        self.chart_combo.currentIndexChanged.connect(self._update_chart)
        controls_layout.addWidget(self.chart_combo)

        # Options
        self.show_individual_cb = QCheckBox("Show Individual Animals")
        self.show_individual_cb.setChecked(True)
        self.show_individual_cb.stateChanged.connect(self._update_chart)
        controls_layout.addWidget(self.show_individual_cb)

        self.show_ci_cb = QCheckBox("Show 95% CI")
        self.show_ci_cb.setChecked(True)
        self.show_ci_cb.stateChanged.connect(self._update_chart)
        controls_layout.addWidget(self.show_ci_cb)

        controls_layout.addStretch()

        # Generate button (don't auto-generate on cohort change)
        self.generate_btn = QPushButton("📊 Generate Graph")
        self.generate_btn.setStyleSheet("""
            QPushButton {
                background-color: #388E3C;
                color: white;
                font-weight: bold;
                font-size: 9pt;
                padding: 8px 16px;
                border-radius: 4px;
            }
            QPushButton:hover {
                background-color: #2E7D32;
            }
            QPushButton:pressed {
                background-color: #1B5E20;
            }
        """)
        self.generate_btn.clicked.connect(self._generate_current_chart)
        controls_layout.addWidget(self.generate_btn)

        # Export button
        self.export_btn = QPushButton("Export Plot")
        self.export_btn.clicked.connect(self._export_plot)
        controls_layout.addWidget(self.export_btn)

        self.export_all_btn = QPushButton("Export All Plots")
        self.export_all_btn.clicked.connect(self._export_all_plots)
        controls_layout.addWidget(self.export_all_btn)

        main_layout.addLayout(controls_layout)

        # Chart area
        if HAS_MATPLOTLIB:
            self.figure = Figure(figsize=(10, 6), dpi=100)
            self.canvas = FigureCanvas(self.figure)
            self.toolbar = NavigationToolbar(self.canvas, self)

            main_layout.addWidget(self.toolbar)
            main_layout.addWidget(self.canvas, stretch=1)
        else:
            no_chart_label = QLabel(
                "Matplotlib not installed.\n\n"
                "Install with: pip install matplotlib\n\n"
                "Then restart the application."
            )
            no_chart_label.setAlignment(Qt.AlignCenter)
            no_chart_label.setStyleSheet("font-size: 10pt; color: #666;")
            main_layout.addWidget(no_chart_label, stretch=1)

        # Statistics panel
        stats_group = QGroupBox("Statistics Summary")
        stats_layout = QGridLayout(stats_group)

        self.stats_labels = {}
        stats_items = [
            ("Sample Size:", "sample_size"),
            ("Total Sessions:", "total_sessions"),
            ("Total Pellets:", "total_pellets"),
            ("Overall Retrieved:", "overall_retrieved_pct"),
            ("Overall Contacted:", "overall_contacted_pct"),
            ("Pre-Injury Mean:", "pre_injury_mean"),
            ("Post-Injury Mean:", "post_injury_mean"),
            ("Difference:", "difference"),
            ("Effect Size (Cohen's d):", "cohens_d"),
            ("P-value:", "p_value"),
        ]

        for i, (label, key) in enumerate(stats_items):
            row, col = divmod(i, 5)
            stats_layout.addWidget(QLabel(label), row * 2, col)
            value_label = QLabel("-")
            value_label.setStyleSheet("font-weight: bold;")
            self.stats_labels[key] = value_label
            stats_layout.addWidget(value_label, row * 2 + 1, col)

        main_layout.addWidget(stats_group)

    def _load_cohorts(self):
        """Load available cohorts."""
        with self.db.session() as session:
            cohorts = session.query(Cohort).filter(
                (Cohort.is_archived == 0) | (Cohort.is_archived == None)
            ).order_by(Cohort.cohort_id).all()
            self.cohort_combo.clear()
            self.cohort_combo.addItem("-- Select Cohort --", None)
            for c in cohorts:
                self.cohort_combo.addItem(c.cohort_id, c.cohort_id)

    def _on_cohort_changed(self, index: int):
        """Handle cohort selection - clear chart but don't auto-generate."""
        if HAS_MATPLOTLIB:
            self.figure.clear()
            ax = self.figure.add_subplot(111)
            cohort_id = self.cohort_combo.currentData()
            if cohort_id:
                ax.text(0.5, 0.5, f"Cohort: {cohort_id}\n\nClick 'Generate Graph' to create visualization",
                        ha='center', va='center', fontsize=12, transform=ax.transAxes)
            else:
                ax.text(0.5, 0.5, "Select a cohort and click 'Generate Graph'",
                        ha='center', va='center', fontsize=12, transform=ax.transAxes)
            ax.set_xlim(0, 1)
            ax.set_ylim(0, 1)
            ax.axis('off')
            self.canvas.draw()

        # Clear statistics
        for label in self.stats_labels.values():
            label.setText("-")

    def _generate_current_chart(self):
        """Generate the current chart when button is clicked."""
        self._update_chart()
        self._update_statistics()

    def _update_chart(self):
        """Update the displayed chart."""
        if not HAS_MATPLOTLIB:
            return

        cohort_id = self.cohort_combo.currentData()
        if not cohort_id:
            self.figure.clear()
            self.canvas.draw()
            return

        chart_type = self.chart_combo.currentText()
        show_individual = self.show_individual_cb.isChecked()
        show_ci = self.show_ci_cb.isChecked()

        try:
            # Import visualization module
            from ..visualizations import (
                plot_learning_curves, plot_phase_comparison,
                plot_recovery_trajectory, plot_weight_curves,
                plot_pellet_heatmap
            )
            import matplotlib.pyplot as plt

            # Generate the plot - these functions return their own Figure
            if chart_type == "Learning Curve":
                fig = plot_learning_curves(
                    cohort_id, db=self.db,
                    show_individual=show_individual,
                    show_ci=show_ci
                )
            elif chart_type == "Phase Comparison":
                fig = plot_phase_comparison(cohort_id, db=self.db)
            elif chart_type == "Recovery Trajectory":
                fig = plot_recovery_trajectory(cohort_id, db=self.db)
            elif chart_type == "Weight Tracking":
                fig = plot_weight_curves(cohort_id, db=self.db)
            elif chart_type == "Pellet Heatmap":
                fig = plot_pellet_heatmap(cohort_id, db=self.db)
            else:
                return

            # Store for export
            self.current_figure = fig

            # Render the figure to an image using matplotlib's native agg backend
            # This works without PIL
            import numpy as np

            # Draw the figure to its canvas to populate the buffer
            fig.canvas.draw()

            # Get the RGB buffer from the figure
            w, h = fig.canvas.get_width_height()
            buf = np.frombuffer(fig.canvas.tostring_rgb(), dtype=np.uint8)
            buf = buf.reshape((h, w, 3))

            # Clear our figure and display the rendered image
            self.figure.clear()
            ax = self.figure.add_subplot(111)
            ax.imshow(buf)
            ax.axis('off')
            self.figure.subplots_adjust(left=0, right=1, top=1, bottom=0)
            self.canvas.draw()

            # Close the temporary figure
            plt.close(fig)
        except Exception as e:
            self.figure.clear()
            ax = self.figure.add_subplot(111)
            ax.text(0.5, 0.5, f"Error generating chart:\n{str(e)}",
                   ha='center', va='center', transform=ax.transAxes,
                   fontsize=12, color='red')
            ax.set_axis_off()
            self.canvas.draw()

    def _update_chart_simple(self):
        """Fallback chart update without PIL - draw directly on canvas."""
        cohort_id = self.cohort_combo.currentData()
        if not cohort_id:
            return

        chart_type = self.chart_combo.currentText()
        show_individual = self.show_individual_cb.isChecked()
        show_ci = self.show_ci_cb.isChecked()

        try:
            from ..visualizations import get_cohort_data, COLORS
            import matplotlib.pyplot as plt
            import matplotlib.dates as mdates

            data = get_cohort_data(cohort_id, self.db)
            sessions = data['sessions']

            self.figure.clear()
            ax = self.figure.add_subplot(111)

            if sessions.empty:
                ax.text(0.5, 0.5, 'No session data available',
                       ha='center', va='center', transform=ax.transAxes)
                self.canvas.draw()
                return

            metric = 'retrieved_pct'
            sessions = sessions.sort_values('date')

            # Plot individual animals
            if show_individual:
                for subject_id in sessions['subject_id'].unique():
                    subj_data = sessions[sessions['subject_id'] == subject_id]
                    ax.plot(subj_data['date'], subj_data[metric],
                           alpha=0.3, linewidth=1, color='#2196F3')

            # Calculate and plot mean with CI
            daily_stats = sessions.groupby('date')[metric].agg(['mean', 'std', 'count'])
            daily_stats['sem'] = daily_stats['std'] / (daily_stats['count'] ** 0.5)
            daily_stats['ci95'] = 1.96 * daily_stats['sem']

            ax.plot(daily_stats.index, daily_stats['mean'],
                   linewidth=2.5, color='#2196F3', label='Group Mean')

            if show_ci:
                ax.fill_between(daily_stats.index,
                               daily_stats['mean'] - daily_stats['ci95'],
                               daily_stats['mean'] + daily_stats['ci95'],
                               alpha=0.3, color='#2196F3', label='95% CI')

            ax.set_xlabel('Date', fontsize=12)
            ax.set_ylabel('Retrieved (%)', fontsize=12)
            ax.set_title(f'{cohort_id} - {chart_type}', fontsize=14, fontweight='bold')
            ax.legend(loc='best')
            ax.set_ylim(0, 100)
            ax.grid(True, alpha=0.3)

            ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
            self.figure.autofmt_xdate()
            self.figure.tight_layout()
            self.canvas.draw()

        except Exception as e:
            self.figure.clear()
            ax = self.figure.add_subplot(111)
            ax.text(0.5, 0.5, f"Error: {str(e)}",
                   ha='center', va='center', transform=ax.transAxes,
                   fontsize=12, color='red')
            ax.set_axis_off()
            self.canvas.draw()

    def _update_statistics(self):
        """Update the statistics panel."""
        cohort_id = self.cohort_combo.currentData()
        if not cohort_id:
            for label in self.stats_labels.values():
                label.setText("-")
            return

        try:
            from ..visualizations import calculate_cohort_statistics
            stats = calculate_cohort_statistics(cohort_id, db=self.db)

            self.stats_labels['sample_size'].setText(str(stats.get('sample_size', '-')))
            self.stats_labels['total_sessions'].setText(str(stats.get('total_sessions', '-')))
            self.stats_labels['total_pellets'].setText(f"{stats.get('total_pellets', 0):,}")
            self.stats_labels['overall_retrieved_pct'].setText(
                f"{stats.get('overall_retrieved_pct', 0):.1f}%")
            self.stats_labels['overall_contacted_pct'].setText(
                f"{stats.get('overall_contacted_pct', 0):.1f}%")

            # Recovery stats
            recovery = stats.get('recovery_stats', {})
            if recovery:
                self.stats_labels['pre_injury_mean'].setText(
                    f"{recovery.get('pre_injury_mean', 0):.1f}%")
                self.stats_labels['post_injury_mean'].setText(
                    f"{recovery.get('post_injury_mean', 0):.1f}%")
                self.stats_labels['difference'].setText(
                    f"{recovery.get('difference', 0):.1f}%")
                self.stats_labels['cohens_d'].setText(
                    f"{recovery.get('cohens_d', 0):.2f}")

                p_val = recovery.get('p_value')
                if p_val is not None:
                    if p_val < 0.001:
                        self.stats_labels['p_value'].setText("< 0.001 ***")
                    elif p_val < 0.01:
                        self.stats_labels['p_value'].setText(f"{p_val:.3f} **")
                    elif p_val < 0.05:
                        self.stats_labels['p_value'].setText(f"{p_val:.3f} *")
                    else:
                        self.stats_labels['p_value'].setText(f"{p_val:.3f}")
                else:
                    self.stats_labels['p_value'].setText("-")
            else:
                for key in ['pre_injury_mean', 'post_injury_mean', 'difference',
                           'cohens_d', 'p_value']:
                    self.stats_labels[key].setText("-")

        except Exception as e:
            print(f"Error calculating statistics: {e}")
            for label in self.stats_labels.values():
                label.setText("-")

    def _export_plot(self):
        """Export current plot to file."""
        if not HAS_MATPLOTLIB or self.current_figure is None:
            QMessageBox.warning(self, "Error", "No plot to export.")
            return

        cohort_id = self.cohort_combo.currentData() or "plot"
        chart_type = self.chart_combo.currentText().lower().replace(" ", "_")

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Save Plot",
            f"{cohort_id}_{chart_type}.png",
            "PNG Files (*.png);;PDF Files (*.pdf);;SVG Files (*.svg)"
        )

        if file_path:
            try:
                self.figure.savefig(file_path, dpi=300, bbox_inches='tight',
                                   facecolor='white')
                QMessageBox.information(self, "Saved", f"Plot saved to:\n{file_path}")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to save plot: {e}")

    def _export_all_plots(self):
        """Export all plots for the selected cohort."""
        cohort_id = self.cohort_combo.currentData()
        if not cohort_id:
            QMessageBox.warning(self, "Error", "Please select a cohort first.")
            return

        dir_path = QFileDialog.getExistingDirectory(
            self, "Select Output Directory"
        )

        if dir_path:
            try:
                from ..visualizations import generate_all_plots
                generated = generate_all_plots(cohort_id, Path(dir_path), db=self.db)
                QMessageBox.information(
                    self, "Complete",
                    f"Generated {len(generated)} plots in:\n{dir_path}"
                )
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to generate plots: {e}")


class DatabaseBrowserTab(QWidget):
    """Tab for browsing raw database tables - great for showing PIs what data looks like."""

    # Map table names to ORM models and display names
    TABLE_CONFIG = {
        'Projects': (Project, ['project_code', 'project_name', 'description']),
        'Cohorts': (Cohort, ['cohort_id', 'project_code', 'start_date', 'num_mice', 'notes']),
        'Subjects': (Subject, ['subject_id', 'cohort_id', 'date_of_birth', 'sex', 'date_of_death', 'notes']),
        'Weights': (Weight, ['id', 'subject_id', 'date', 'weight_grams', 'entered_by', 'entered_at']),
        'Pellet Scores': (PelletScore, ['id', 'subject_id', 'session_date', 'test_phase', 'tray_type', 'tray_number', 'pellet_number', 'score', 'entered_by']),
        'Ramp Entries': (RampEntry, ['id', 'subject_id', 'date', 'day_number', 'body_weight', 'food_offered', 'food_remaining', 'entered_by']),
        'Surgeries': (Surgery, ['id', 'subject_id', 'surgery_date', 'surgery_type', 'force_kdyn', 'displacement_um', 'velocity_mm_s', 'surgeon']),
        'Virus Preps': (VirusPrep, ['id', 'cohort_id', 'prep_date', 'virus_name', 'lot_number', 'original_titer', 'dilution_factor', 'final_titer']),
    }

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self._setup_ui()
        self._load_table_list()

    def _setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Header
        header = QLabel("Database Browser")
        header.setObjectName("section_header")
        header.setStyleSheet("font-size: 11px; font-weight: bold; margin-bottom: 10px;")
        layout.addWidget(header)

        desc = QLabel("View raw data stored in the database. Select a table to see its contents.")
        desc.setWordWrap(True)
        desc.setStyleSheet("color: #888; margin-bottom: 10px;")
        layout.addWidget(desc)

        # Controls row
        controls_layout = QHBoxLayout()

        controls_layout.addWidget(QLabel("Table:"))
        self.table_combo = QComboBox()
        self.table_combo.setMinimumWidth(200)
        self.table_combo.currentTextChanged.connect(self._on_table_changed)
        controls_layout.addWidget(self.table_combo)

        controls_layout.addSpacing(20)

        controls_layout.addWidget(QLabel("Filter:"))
        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Type to filter rows...")
        self.filter_edit.textChanged.connect(self._apply_filter)
        self.filter_edit.setMinimumWidth(200)
        controls_layout.addWidget(self.filter_edit)

        controls_layout.addStretch()

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.clicked.connect(self._refresh_data)
        controls_layout.addWidget(self.refresh_btn)

        self.export_btn = QPushButton("Export to CSV")
        self.export_btn.clicked.connect(self._export_to_csv)
        controls_layout.addWidget(self.export_btn)

        layout.addLayout(controls_layout)

        # Stats row
        self.stats_label = QLabel("Select a table to view data")
        self.stats_label.setStyleSheet("color: #666; font-style: italic;")
        layout.addWidget(self.stats_label)

        # Data table
        self.data_table = QTableWidget()
        self.data_table.setAlternatingRowColors(True)
        self.data_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.data_table.setSortingEnabled(True)
        self.data_table.horizontalHeader().setStretchLastSection(True)
        self.data_table.setStyleSheet("""
            QTableWidget {
                gridline-color: #ddd;
                font-family: monospace;
            }
            QTableWidget::item {
                padding: 4px;
            }
            QHeaderView::section {
                background-color: #f0f0f0;
                padding: 6px;
                border: 1px solid #ddd;
                font-weight: bold;
            }
        """)
        layout.addWidget(self.data_table)

        # Record count summary at bottom
        self.summary_frame = QFrame()
        self.summary_frame.setStyleSheet("background-color: #f8f8f8; border-radius: 4px; padding: 10px;")
        summary_layout = QHBoxLayout(self.summary_frame)

        self.count_labels = {}
        for table_name in self.TABLE_CONFIG.keys():
            label = QLabel(f"{table_name}: -")
            label.setStyleSheet("margin-right: 15px;")
            self.count_labels[table_name] = label
            summary_layout.addWidget(label)

        summary_layout.addStretch()
        layout.addWidget(self.summary_frame)

    def _load_table_list(self):
        """Populate the table dropdown."""
        self.table_combo.clear()
        for table_name in self.TABLE_CONFIG.keys():
            self.table_combo.addItem(table_name)

        # Load initial counts
        self._update_counts()

    def _update_counts(self):
        """Update record count labels."""
        with self.db.session() as session:
            for table_name, (model, _) in self.TABLE_CONFIG.items():
                try:
                    count = session.query(model).count()
                    self.count_labels[table_name].setText(f"{table_name}: {count:,}")
                except Exception:
                    self.count_labels[table_name].setText(f"{table_name}: ?")

    def _on_table_changed(self, table_name: str):
        """Load data for the selected table."""
        if not table_name or table_name not in self.TABLE_CONFIG:
            return

        self.filter_edit.clear()
        self._load_table_data(table_name)

    def _load_table_data(self, table_name: str):
        """Load data from the selected table into the grid."""
        if table_name not in self.TABLE_CONFIG:
            return

        model, columns = self.TABLE_CONFIG[table_name]

        self.data_table.setSortingEnabled(False)  # Disable while loading
        self.data_table.clear()
        self.data_table.setRowCount(0)
        self.data_table.setColumnCount(len(columns))
        self.data_table.setHorizontalHeaderLabels(columns)

        try:
            with self.db.session() as session:
                # Query all records
                records = session.query(model).all()

                self.data_table.setRowCount(len(records))

                for row_idx, record in enumerate(records):
                    for col_idx, col_name in enumerate(columns):
                        value = getattr(record, col_name, None)

                        # Format the value for display
                        if value is None:
                            display_value = ""
                        elif isinstance(value, datetime):
                            display_value = value.strftime("%Y-%m-%d %H:%M:%S")
                        elif isinstance(value, date):
                            display_value = value.strftime("%Y-%m-%d")
                        else:
                            display_value = str(value)

                        item = QTableWidgetItem(display_value)
                        # Store original value for sorting
                        if isinstance(value, (int, float)):
                            item.setData(Qt.UserRole, value)
                        self.data_table.setItem(row_idx, col_idx, item)

                self.stats_label.setText(f"Showing {len(records):,} records from '{table_name}'")

        except Exception as e:
            self.stats_label.setText(f"Error loading data: {e}")
            QMessageBox.warning(self, "Error", f"Failed to load table data:\n{e}")

        self.data_table.setSortingEnabled(True)
        self.data_table.resizeColumnsToContents()

    def _apply_filter(self, filter_text: str):
        """Filter visible rows based on text."""
        filter_text = filter_text.lower().strip()

        for row in range(self.data_table.rowCount()):
            show_row = False

            if not filter_text:
                show_row = True
            else:
                for col in range(self.data_table.columnCount()):
                    item = self.data_table.item(row, col)
                    if item and filter_text in item.text().lower():
                        show_row = True
                        break

            self.data_table.setRowHidden(row, not show_row)

        # Update stats to show filtered count
        if filter_text:
            visible = sum(1 for row in range(self.data_table.rowCount())
                         if not self.data_table.isRowHidden(row))
            self.stats_label.setText(f"Showing {visible:,} of {self.data_table.rowCount():,} records (filtered)")

    def _refresh_data(self):
        """Reload current table data."""
        current_table = self.table_combo.currentText()
        if current_table:
            self._load_table_data(current_table)
            self._update_counts()
            QMessageBox.information(self, "Refreshed", f"Data refreshed for '{current_table}'")

    def _export_to_csv(self):
        """Export current table view to CSV."""
        current_table = self.table_combo.currentText()
        if not current_table:
            QMessageBox.warning(self, "Error", "Please select a table first.")
            return

        default_name = f"{current_table.lower().replace(' ', '_')}_{datetime.now().strftime('%Y%m%d')}.csv"

        file_path, _ = QFileDialog.getSaveFileName(
            self, "Export to CSV", default_name, "CSV files (*.csv)"
        )

        if file_path:
            try:
                import csv
                with open(file_path, 'w', newline='', encoding='utf-8') as f:
                    writer = csv.writer(f)

                    # Write header
                    headers = [self.data_table.horizontalHeaderItem(col).text()
                              for col in range(self.data_table.columnCount())]
                    writer.writerow(headers)

                    # Write visible rows
                    for row in range(self.data_table.rowCount()):
                        if not self.data_table.isRowHidden(row):
                            row_data = []
                            for col in range(self.data_table.columnCount()):
                                item = self.data_table.item(row, col)
                                row_data.append(item.text() if item else "")
                            writer.writerow(row_data)

                QMessageBox.information(
                    self, "Export Complete",
                    f"Exported to:\n{file_path}"
                )
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to export:\n{e}")


class DataEntryWindow(QMainWindow):
    """Main data entry window with tabs."""

    def __init__(self):
        super().__init__()
        self.db = init_database()

        self._setup_ui()

    def _setup_ui(self):
        self.setWindowTitle("Connectome Data Entry")
        self.setMinimumSize(900, 750)
        self.setStyleSheet(STYLESHEET)

        # Central widget with tabs
        central = QWidget()
        self.setCentralWidget(central)
        main_layout = QVBoxLayout(central)
        main_layout.setContentsMargins(10, 10, 10, 10)

        # Header
        header = QLabel("Connectome Data Entry")
        header.setObjectName("header")
        main_layout.addWidget(header)

        # Tab widget - organized by workflow stages
        self.tabs = QTabWidget()

        # 1. COHORT SETUP - Create cohort with initial weights (first thing you do)
        self.cohort_tab = CohortSetupTab(self.db)
        self.tabs.addTab(self.cohort_tab, "1. Cohort Setup")

        # 2. RAMP ENTRY - Days 0-3 food deprivation phase (body weight + tray weights)
        self.ramp_tab = RampEntryTab(self.db)
        self.tabs.addTab(self.ramp_tab, "2. Ramp Entry")

        # 3. TESTING ENTRY - Pellet scores for testing days (primary data entry)
        self.testing_tab = BulkTrayEntryTab(self.db)
        self.tabs.addTab(self.testing_tab, "3. Testing Entry")

        # 4. SURGERY ENTRY - Pre-surgery weight, surgery params, outcome
        self.surgery_tab = SurgeryEntryTab(self.db)
        self.tabs.addTab(self.surgery_tab, "4. Surgery Entry")

        # 5. VIRUS PREP - Injection calculations for tracing surgery
        self.virus_prep_tab = VirusPrepTab(self.db)
        self.tabs.addTab(self.virus_prep_tab, "5. Virus Prep")

        # 6. DASHBOARD - View stats and summaries
        self.dashboard_tab = DashboardTab(self.db)
        self.tabs.addTab(self.dashboard_tab, "6. Dashboard")

        # 7. VISUALIZATIONS - Charts and plots
        self.viz_tab = VisualizationTab(self.db)
        self.tabs.addTab(self.viz_tab, "7. Visualizations")

        # 8. DATABASE BROWSER - View raw data (great for showing PIs)
        self.browser_tab = DatabaseBrowserTab(self.db)
        self.tabs.addTab(self.browser_tab, "8. Database Browser")

        # LEGACY TABS - kept for backward compatibility
        # Weight entry tab (for general weight tracking outside ramp)
        self.bulk_weight_tab = BulkWeightEntryTab(self.db)
        self.tabs.addTab(self.bulk_weight_tab, "Extra: Weight Entry")

        # Single animal entry (for special cases)
        self.pellet_tab = PelletEntryTab(self.db)
        self.tabs.addTab(self.pellet_tab, "Extra: Single Animal")

        main_layout.addWidget(self.tabs)

        # Status bar
        self.statusBar = QStatusBar()
        self.setStatusBar(self.statusBar)
        self._update_status("Ready")

    def _update_status(self, message: str):
        """Update status bar message."""
        self.statusBar.showMessage(f"{datetime.now().strftime('%H:%M:%S')} - {message}")


def main():
    """Main entry point for GUI."""
    app = QApplication(sys.argv)
    app.setStyle('Fusion')

    window = DataEntryWindow()
    window.show()

    sys.exit(app.exec_())


if __name__ == '__main__':
    main()
