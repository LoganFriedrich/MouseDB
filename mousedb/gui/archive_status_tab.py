"""
Archive Status tab for the MouseDB GUI.

Shows archive processing progress and version compliance status,
merged with data from the MouseReach watcher.db.
"""

from datetime import datetime

from PyQt5.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QGroupBox,
    QPushButton, QTableWidget, QTableWidgetItem, QHeaderView,
    QSplitter, QFrame, QGridLayout,
)
from PyQt5.QtCore import Qt
from PyQt5.QtGui import QColor, QPainter, QPen

from ..watcher_bridge import (
    WatcherBridge, ARCHIVE_STATE_DISPLAY,
)


class ArchiveProgressBar(QWidget):
    """Stacked horizontal bar showing archive state distribution.

    Segments: green=current, blue=crystallized, orange=outdated,
    gray=in_pipeline, red=failed.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._archived = 0
        self._outdated = 0
        self._crystallized = 0
        self._in_pipeline = 0
        self._failed = 0
        self._total = 0
        self.setMinimumHeight(24)
        self.setMaximumHeight(24)

    def set_data(self, archived, outdated, crystallized, in_pipeline, failed, total):
        self._archived = archived
        self._outdated = outdated
        self._crystallized = crystallized
        self._in_pipeline = in_pipeline
        self._failed = failed
        self._total = total
        self.update()

    def paintEvent(self, event):
        if self._total == 0:
            return
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)
        w = self.width() - 2
        h = self.height() - 2
        x = 1.0

        segments = [
            (self._archived,     ARCHIVE_STATE_DISPLAY['current']['color']),
            (self._crystallized, ARCHIVE_STATE_DISPLAY['crystallized']['color']),
            (self._outdated,     ARCHIVE_STATE_DISPLAY['outdated']['color']),
            (self._in_pipeline,  ARCHIVE_STATE_DISPLAY['in_pipeline']['color']),
            (self._failed,       ARCHIVE_STATE_DISPLAY['failed']['color']),
        ]
        for count, color in segments:
            if count == 0:
                continue
            seg_w = max(2, w * count / self._total)
            painter.fillRect(int(x), 1, int(seg_w), h, QColor(color))
            x += seg_w

        painter.setPen(QPen(QColor('#CCCCCC')))
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)
        painter.end()


class ArchiveStatusTab(QWidget):
    """
    Archive status tab showing version compliance and processing progress.

    Pattern follows VideoStatusTab:
    - __init__(db) -> _setup_ui() -> initial load
    - Refresh button to reload from watcher.db
    - Graceful empty state when watcher.db not found
    """

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.bridge = WatcherBridge()
        self._all_animals = []
        self._setup_ui()
        self._refresh()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)

        # --- Header ---
        header = QHBoxLayout()
        title = QLabel("Archive Processing Status")
        title.setStyleSheet("font-size: 12pt; font-weight: bold;")
        header.addWidget(title)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #757575; font-style: italic;")
        header.addWidget(self.status_label)
        header.addStretch()

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setStyleSheet(
            "QPushButton { background-color: #388E3C; color: white;"
            " font-weight: bold; padding: 6px 14px; border-radius: 4px; }"
            " QPushButton:hover { background-color: #2E7D32; }"
        )
        self.refresh_btn.clicked.connect(self._refresh)
        header.addWidget(self.refresh_btn)
        main_layout.addLayout(header)

        # --- Unavailable placeholder ---
        self.unavailable_frame = QFrame()
        unavail_layout = QVBoxLayout(self.unavailable_frame)
        self.unavailable_label = QLabel(
            "Archive status is not available.\n\n"
            "The watcher system tracks video processing state in watcher.db.\n"
            "This data will appear here after the watcher processes its first video.\n\n"
            "To configure: set MOUSEDB_WATCHER_DB environment variable\n"
            "or ensure mousereach-setup has been run."
        )
        self.unavailable_label.setAlignment(Qt.AlignCenter)
        self.unavailable_label.setStyleSheet(
            "color: #757575; font-size: 11pt; padding: 40px;")
        unavail_layout.addWidget(self.unavailable_label)
        main_layout.addWidget(self.unavailable_frame)

        # --- Main content ---
        self.content_widget = QWidget()
        content_layout = QVBoxLayout(self.content_widget)
        content_layout.setContentsMargins(0, 0, 0, 0)

        # Summary cards
        summary_group = QGroupBox("Archive Summary")
        summary_group.setStyleSheet(
            "QGroupBox { font-size: 10pt; font-weight: bold;"
            " background-color: #E8F5E9; border-radius: 6px;"
            " padding-top: 16px; }"
        )
        summary_grid = QGridLayout(summary_group)

        self.summary_labels = {}
        card_defs = [
            ('total', 'Total Videos'),
            ('archived', 'Current'),
            ('outdated', 'Outdated'),
            ('crystallized', 'Crystallized'),
            ('in_pipeline', 'In Pipeline'),
            ('failed', 'Failed'),
        ]
        for col, (key, label) in enumerate(card_defs):
            card = QLabel(f"{label}\n--")
            card.setAlignment(Qt.AlignCenter)
            card.setStyleSheet(
                "background: white; border: 1px solid #E0E0E0;"
                " border-radius: 4px; padding: 8px; font-size: 10pt;"
            )
            self.summary_labels[key] = card
            summary_grid.addWidget(card, 0, col)

        content_layout.addWidget(summary_group)

        # Version info label
        self.versions_label = QLabel("")
        self.versions_label.setStyleSheet("color: #757575; font-size: 9pt;")
        content_layout.addWidget(self.versions_label)

        # Overall progress bar
        self.overall_progress = ArchiveProgressBar()
        content_layout.addWidget(self.overall_progress)

        # Cohort + Animal tables in splitter
        splitter = QSplitter(Qt.Vertical)

        # Cohort table
        cohort_group = QGroupBox("By Cohort")
        cohort_layout = QVBoxLayout(cohort_group)
        self.cohort_table = QTableWidget()
        self.cohort_table.setColumnCount(8)
        self.cohort_table.setHorizontalHeaderLabels([
            'Cohort', 'Total', 'Archived', 'Outdated',
            'Crystallized', 'In Pipeline', 'Failed', 'Progress'
        ])
        self.cohort_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        self.cohort_table.setSelectionBehavior(QTableWidget.SelectRows)
        self.cohort_table.setSelectionMode(QTableWidget.SingleSelection)
        self.cohort_table.currentCellChanged.connect(self._on_cohort_selected)
        cohort_layout.addWidget(self.cohort_table)
        splitter.addWidget(cohort_group)

        # Animal table
        animal_group = QGroupBox("By Animal")
        animal_layout = QVBoxLayout(animal_group)
        self.animal_table = QTableWidget()
        self.animal_table.setColumnCount(7)
        self.animal_table.setHorizontalHeaderLabels([
            'Subject', 'Total', 'Archived', 'Outdated',
            'Crystallized', 'In Pipeline', 'Failed'
        ])
        self.animal_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        self.animal_table.setSelectionBehavior(QTableWidget.SelectRows)
        animal_layout.addWidget(self.animal_table)
        splitter.addWidget(animal_group)

        content_layout.addWidget(splitter, 1)

        # State legend
        legend = QHBoxLayout()
        legend.addWidget(QLabel("Legend:"))
        for key in ['current', 'crystallized', 'outdated', 'in_pipeline', 'failed']:
            info = ARCHIVE_STATE_DISPLAY[key]
            lbl = QLabel(f" {info['label']} ")
            lbl.setStyleSheet(
                f"background-color: {info['color']};"
                f" color: white; padding: 2px 5px; border-radius: 3px;"
                f" font-size: 8pt;"
            )
            legend.addWidget(lbl)
        legend.addStretch()
        content_layout.addLayout(legend)

        main_layout.addWidget(self.content_widget)

    def _refresh(self):
        """Reload all data from watcher.db."""
        self.bridge = WatcherBridge()

        if not self.bridge.is_available:
            self.unavailable_frame.show()
            self.content_widget.hide()
            self.unavailable_label.setText(
                f"Archive status is not available.\n\n"
                f"{self.bridge.status.message}\n\n"
                f"This tab will populate once the watcher starts processing videos."
            )
            self.status_label.setText("Watcher not available")
            return

        self.unavailable_frame.hide()
        self.content_widget.show()

        summary = self.bridge.get_archive_summary()

        # Status line
        parts = [f"Source: {self.bridge.status.db_path}"]
        if summary.versions_path:
            parts.append(f"Versions: {summary.versions_path}")
        self.status_label.setText(
            '  |  '.join(parts)
            + f"  |  Updated: {datetime.now().strftime('%H:%M:%S')}"
        )

        # Version info
        if summary.versions_info and summary.versions_info.get('versions'):
            v = summary.versions_info['versions']
            ver_text = '  |  '.join(f"{k}: {val}" for k, val in v.items())
            self.versions_label.setText(f"Pipeline versions: {ver_text}")
            self.versions_label.setStyleSheet("color: #1B5E20; font-size: 9pt;")
        else:
            self.versions_label.setText("Pipeline versions file not found")
            self.versions_label.setStyleSheet("color: #FF6F00; font-size: 9pt;")

        # Summary cards
        self.summary_labels['total'].setText(
            f"Total Videos\n{summary.total_videos}")
        self.summary_labels['archived'].setText(
            f"Current\n{summary.archived}")
        self.summary_labels['outdated'].setText(
            f"Outdated\n{summary.outdated}")
        self.summary_labels['crystallized'].setText(
            f"Crystallized\n{summary.crystallized}")
        self.summary_labels['in_pipeline'].setText(
            f"In Pipeline\n{summary.in_pipeline}")
        self.summary_labels['failed'].setText(
            f"Failed\n{summary.failed}")

        # Highlight outdated card
        if summary.outdated > 0:
            self.summary_labels['outdated'].setStyleSheet(
                "background: #FFF3E0; border: 2px solid #FF9800;"
                " border-radius: 4px; padding: 8px; font-size: 10pt;"
                " font-weight: bold;"
            )
        else:
            self.summary_labels['outdated'].setStyleSheet(
                "background: white; border: 1px solid #E0E0E0;"
                " border-radius: 4px; padding: 8px; font-size: 10pt;"
            )

        # Highlight failed card
        if summary.failed > 0:
            self.summary_labels['failed'].setStyleSheet(
                "background: #FFCDD2; border: 2px solid #F44336;"
                " border-radius: 4px; padding: 8px; font-size: 10pt;"
                " font-weight: bold;"
            )
        else:
            self.summary_labels['failed'].setStyleSheet(
                "background: white; border: 1px solid #E0E0E0;"
                " border-radius: 4px; padding: 8px; font-size: 10pt;"
            )

        # Overall progress bar
        self.overall_progress.set_data(
            summary.archived, summary.outdated, summary.crystallized,
            summary.in_pipeline, summary.failed, summary.total_videos
        )

        # Cohort table
        cohort_rollup = self.bridge.get_archive_cohort_rollup()
        self._cohorts_data = cohort_rollup
        self.cohort_table.setRowCount(len(cohort_rollup))
        for row, c in enumerate(cohort_rollup):
            self.cohort_table.setItem(row, 0, QTableWidgetItem(c.cohort_id))
            self.cohort_table.setItem(row, 1,
                                      QTableWidgetItem(str(c.total_videos)))
            self.cohort_table.setItem(row, 2,
                                      QTableWidgetItem(str(c.archived)))

            outdated_item = QTableWidgetItem(str(c.outdated))
            if c.outdated > 0:
                outdated_item.setBackground(QColor('#FFF3E0'))
            self.cohort_table.setItem(row, 3, outdated_item)

            crystal_item = QTableWidgetItem(str(c.crystallized))
            if c.crystallized > 0:
                crystal_item.setBackground(QColor('#E3F2FD'))
            self.cohort_table.setItem(row, 4, crystal_item)

            self.cohort_table.setItem(row, 5,
                                      QTableWidgetItem(str(c.in_pipeline)))

            failed_item = QTableWidgetItem(str(c.failed))
            if c.failed > 0:
                failed_item.setBackground(QColor('#FFCDD2'))
            self.cohort_table.setItem(row, 6, failed_item)

            self.cohort_table.setItem(row, 7,
                                      QTableWidgetItem(f"{c.completion_pct:.1f}%"))

        # Load all animals initially
        self._all_animals = self.bridge.get_archive_animal_rollup()
        self._load_animal_table()

    def _on_cohort_selected(self, row, col, prev_row, prev_col):
        """Filter animal table when cohort row selected."""
        if row < 0:
            return
        item = self.cohort_table.item(row, 0)
        if item:
            self._load_animal_table(cohort_filter=item.text())

    def _load_animal_table(self, cohort_filter=None):
        """Load animal data, optionally filtered by cohort."""
        animals = self._all_animals
        if cohort_filter:
            animals = [a for a in animals if a.cohort_id == cohort_filter]

        self.animal_table.setRowCount(len(animals))
        for row, a in enumerate(animals):
            self.animal_table.setItem(row, 0,
                                      QTableWidgetItem(a.subject_id))
            self.animal_table.setItem(row, 1,
                                      QTableWidgetItem(str(a.total_videos)))
            self.animal_table.setItem(row, 2,
                                      QTableWidgetItem(str(a.archived)))

            outdated_item = QTableWidgetItem(str(a.outdated))
            if a.outdated > 0:
                outdated_item.setBackground(QColor('#FFF3E0'))
            self.animal_table.setItem(row, 3, outdated_item)

            crystal_item = QTableWidgetItem(str(a.crystallized))
            if a.crystallized > 0:
                crystal_item.setBackground(QColor('#E3F2FD'))
            self.animal_table.setItem(row, 4, crystal_item)

            self.animal_table.setItem(row, 5,
                                      QTableWidgetItem(str(a.in_pipeline)))

            failed_item = QTableWidgetItem(str(a.failed))
            if a.failed > 0:
                failed_item.setBackground(QColor('#FFCDD2'))
            self.animal_table.setItem(row, 6, failed_item)
