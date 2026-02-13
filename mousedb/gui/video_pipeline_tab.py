"""
Video Pipeline Status tab for the MouseDB GUI.

Shows the state of video processing from the MouseReach watcher,
merged with subject/cohort data from connectome.db.
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
    WatcherBridge, STATE_DISPLAY, PIPELINE_STATES, ERROR_STATES, DONE_STATES,
)


class PipelineProgressBar(QWidget):
    """Stacked horizontal bar showing pipeline state distribution."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._data = {}
        self._total = 0
        self.setMinimumHeight(24)
        self.setMaximumHeight(24)

    def set_data(self, by_state: dict, total: int):
        self._data = by_state
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
        for state in PIPELINE_STATES + ERROR_STATES:
            count = self._data.get(state, 0)
            if count == 0:
                continue
            seg_w = max(2, w * count / self._total)
            color = QColor(STATE_DISPLAY.get(state, {}).get('color', '#9E9E9E'))
            painter.fillRect(int(x), 1, int(seg_w), h, color)
            x += seg_w
        painter.setPen(QPen(QColor('#CCCCCC')))
        painter.drawRect(0, 0, self.width() - 1, self.height() - 1)
        painter.end()


class VideoStatusTab(QWidget):
    """
    Video pipeline status tab.

    Pattern follows DashboardTab:
    - __init__(db) -> _setup_ui() -> initial load
    - Refresh button to reload from watcher.db
    - Graceful empty state when watcher.db not found
    """

    def __init__(self, db, parent=None):
        super().__init__(parent)
        self.db = db
        self.bridge = WatcherBridge()
        self._setup_ui()
        self._refresh()

    def _setup_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setSpacing(10)

        # --- Header ---
        header = QHBoxLayout()
        title = QLabel("Video Pipeline Status")
        title.setStyleSheet("font-size: 12pt; font-weight: bold;")
        header.addWidget(title)

        self.status_label = QLabel("")
        self.status_label.setStyleSheet("color: #757575; font-style: italic;")
        header.addWidget(self.status_label)
        header.addStretch()

        self.refresh_btn = QPushButton("Refresh")
        self.refresh_btn.setStyleSheet(
            "QPushButton { background-color: #1976D2; color: white;"
            " font-weight: bold; padding: 6px 14px; border-radius: 4px; }"
            " QPushButton:hover { background-color: #1565C0; }"
        )
        self.refresh_btn.clicked.connect(self._refresh)
        header.addWidget(self.refresh_btn)
        main_layout.addLayout(header)

        # --- Unavailable placeholder ---
        self.unavailable_frame = QFrame()
        unavail_layout = QVBoxLayout(self.unavailable_frame)
        self.unavailable_label = QLabel(
            "Video pipeline status is not available.\n\n"
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
        summary_group = QGroupBox("Pipeline Summary")
        summary_group.setStyleSheet(
            "QGroupBox { font-size: 10pt; font-weight: bold;"
            " background-color: #E3F2FD; border-radius: 6px;"
            " padding-top: 16px; }"
        )
        summary_grid = QGridLayout(summary_group)

        self.summary_labels = {}
        card_defs = [
            ('total', 'Total Videos'),
            ('done', 'Completed'),
            ('in_progress', 'In Progress'),
            ('failed', 'Failed'),
            ('pct', 'Completion %'),
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

        # Overall progress bar
        self.overall_progress = PipelineProgressBar()
        content_layout.addWidget(self.overall_progress)

        # Cohort + Animal tables in splitter
        splitter = QSplitter(Qt.Vertical)

        # Cohort table
        cohort_group = QGroupBox("By Cohort")
        cohort_layout = QVBoxLayout(cohort_group)
        self.cohort_table = QTableWidget()
        self.cohort_table.setColumnCount(6)
        self.cohort_table.setHorizontalHeaderLabels([
            'Cohort', 'Animals', 'Videos', 'Completed', 'Failed', 'Progress'
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
        self.animal_table.setColumnCount(6)
        self.animal_table.setHorizontalHeaderLabels([
            'Subject', 'Total', 'Completed', 'In Progress', 'Failed',
            'Last Activity'
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
        # Show a representative subset to avoid clutter
        legend_states = [
            'discovered', 'validated', 'dlc_running', 'dlc_complete',
            'processing', 'processed', 'archived', 'quarantined', 'failed',
        ]
        for state in legend_states:
            info = STATE_DISPLAY.get(state, {})
            lbl = QLabel(f" {info.get('label', state)} ")
            lbl.setStyleSheet(
                f"background-color: {info.get('color', '#999')};"
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
                f"Video pipeline status is not available.\n\n"
                f"{self.bridge.status.message}\n\n"
                f"This tab will populate once the watcher starts processing videos."
            )
            self.status_label.setText("Watcher not available")
            return

        self.unavailable_frame.hide()
        self.content_widget.show()
        self.status_label.setText(
            f"Source: {self.bridge.status.db_path}  |  "
            f"Updated: {datetime.now().strftime('%H:%M:%S')}"
        )

        # Summary
        summary = self.bridge.get_pipeline_summary()
        done = sum(summary.by_state.get(s, 0) for s in DONE_STATES)
        in_progress = (summary.total_videos - done
                       - summary.failed_count - summary.quarantined_count)

        self.summary_labels['total'].setText(
            f"Total Videos\n{summary.total_videos}")
        self.summary_labels['done'].setText(
            f"Completed\n{done}")
        self.summary_labels['in_progress'].setText(
            f"In Progress\n{in_progress}")
        self.summary_labels['failed'].setText(
            f"Failed\n{summary.failed_count}")
        self.summary_labels['pct'].setText(
            f"Completion %\n{summary.fully_processed_pct:.1f}%")

        # Highlight failed card if there are failures
        if summary.failed_count > 0:
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

        # Progress bar
        self.overall_progress.set_data(summary.by_state, summary.total_videos)

        # Cohort table
        cohorts = self.bridge.get_cohort_rollup()
        self._cohorts_data = cohorts
        self.cohort_table.setRowCount(len(cohorts))
        for row, (cid, data) in enumerate(sorted(cohorts.items())):
            self.cohort_table.setItem(row, 0, QTableWidgetItem(cid))
            self.cohort_table.setItem(row, 1,
                                      QTableWidgetItem(str(data['animals'])))
            self.cohort_table.setItem(row, 2,
                                      QTableWidgetItem(str(data['total_videos'])))
            self.cohort_table.setItem(row, 3,
                                      QTableWidgetItem(str(data['fully_processed'])))

            failed_item = QTableWidgetItem(str(data['failed']))
            if data['failed'] > 0:
                failed_item.setBackground(QColor('#FFCDD2'))
            self.cohort_table.setItem(row, 4, failed_item)

            pct = (data['fully_processed'] / data['total_videos'] * 100
                   if data['total_videos'] > 0 else 0)
            self.cohort_table.setItem(row, 5,
                                      QTableWidgetItem(f"{pct:.1f}%"))

        # Load all animals initially
        self._all_animals = self.bridge.get_animal_rollup()
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
        animals = self._all_animals if hasattr(self, '_all_animals') else []
        if cohort_filter:
            animals = [a for a in animals if a.cohort_id == cohort_filter]

        self.animal_table.setRowCount(len(animals))
        for row, a in enumerate(animals):
            done = sum(a.by_state.get(s, 0) for s in DONE_STATES)
            in_progress = a.total_videos - done - a.failed_videos

            self.animal_table.setItem(row, 0,
                                      QTableWidgetItem(a.subject_id))
            self.animal_table.setItem(row, 1,
                                      QTableWidgetItem(str(a.total_videos)))
            self.animal_table.setItem(row, 2,
                                      QTableWidgetItem(str(done)))
            self.animal_table.setItem(row, 3,
                                      QTableWidgetItem(str(in_progress)))

            failed_item = QTableWidgetItem(str(a.failed_videos))
            if a.failed_videos > 0:
                failed_item.setBackground(QColor('#FFCDD2'))
            self.animal_table.setItem(row, 4, failed_item)

            activity = a.latest_activity or '-'
            if activity != '-':
                try:
                    dt = datetime.fromisoformat(activity)
                    activity = dt.strftime('%Y-%m-%d %H:%M')
                except Exception:
                    pass
            self.animal_table.setItem(row, 5,
                                      QTableWidgetItem(activity))
