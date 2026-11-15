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
    watcher_scope_lines,
    WatcherBridge, ARCHIVE_STATE_DISPLAY,
)


class ArchiveProgressBar(QWidget):
    """Stacked horizontal bar showing archive state distribution.

    Segments: green=current, blue=crystallized, orange=outdated,
    gray=in_pipeline, purple=in_review, light gray=not on this computer,
    deep orange=quarantined, red=failed, brown=other.
    """

    def __init__(self, parent=None):
        super().__init__(parent)
        self._archived = 0
        self._outdated = 0
        self._crystallized = 0
        self._in_pipeline = 0
        self._failed = 0
        self._in_review = 0
        self._not_on_this_node = 0
        self._other = 0
        self._quarantined = 0
        self._total = 0
        self.setMinimumHeight(24)
        self.setMaximumHeight(24)

    def set_data(self, archived, outdated, crystallized, in_pipeline, failed, total,
                 in_review=0, not_on_this_node=0, other=0, quarantined=0):
        self._quarantined = quarantined
        self._in_review = in_review
        self._not_on_this_node = not_on_this_node
        self._other = other
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
            (self._in_review,    ARCHIVE_STATE_DISPLAY['in_review']['color']),
            (self._not_on_this_node, ARCHIVE_STATE_DISPLAY['not_on_this_node']['color']),
            (self._quarantined,  ARCHIVE_STATE_DISPLAY['quarantined']['color']),
            (self._failed,       ARCHIVE_STATE_DISPLAY['failed']['color']),
            (self._other,        ARCHIVE_STATE_DISPLAY['other']['color']),
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

    Every video is in exactly one count column, so on every row of both
    tables the columns from Archived to Other add up to Total. WHY: a video
    in a state left out of every column (a quarantined one used to be shown
    as Failed; a state added by a newer MouseReach was in no column at all)
    made a row's Total larger than its parts with nothing on the row saying
    why.

    Pattern follows VideoStatusTab:
    - __init__(db) -> _setup_ui() -> initial load
    - Refresh button to reload from watcher.db
    - Graceful empty state when watcher.db not found
    """

    # (header, attribute of ArchiveCohortSummary / ArchiveAnimalSummary,
    # highlight colour when non-zero). 'other' is the sum of the states
    # mousedb does not know (named on the line under the cards, and in the
    # cell's tooltip).
    COUNT_COLUMNS = [
        ('Archived', 'archived', None),
        ('Outdated', 'outdated', '#FFF3E0'),
        ('Crystallized', 'crystallized', '#E3F2FD'),
        ('In Pipeline', 'in_pipeline', None),
        ('In Review', 'in_review', None),
        ('Not Here', 'not_on_this_node', None),
        ('Quarantined', 'quarantined', '#FFE0B2'),
        ('Failed', 'failed', '#FFCDD2'),
        ('Other', 'other', '#D7CCC8'),
    ]
    COHORT_COLUMNS = (['Cohort', 'Total'] + [c[0] for c in COUNT_COLUMNS]
                      + ['Progress'])
    ANIMAL_COLUMNS = ['Subject', 'Total'] + [c[0] for c in COUNT_COLUMNS]

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

        # Whose records these are. WHY: the watcher database holds only
        # what THIS computer's watcher did; other computers' work is not in
        # it, so its counts are not the pipeline's state (that is the
        # pipeline census on the Where Is My Data tab). See
        # watcher_bridge.watcher_scope_lines.
        self.scope_label = QLabel("")
        self.scope_label.setWordWrap(True)
        self.scope_label.setStyleSheet(
            "background: #FFF8E1; border: 1px solid #FFB300;"
            " border-radius: 4px; padding: 6px; color: #5D4037;")
        main_layout.addWidget(self.scope_label)

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
            ('in_review', 'In Review'),
            ('not_on_this_node', 'Not on this computer'),
            ('quarantined', 'Quarantined'),
            ('failed', 'Failed'),
            ('other', 'Other'),
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

        # States mousedb does not know: named, never folded into a count
        # that means something else.
        self.other_label = QLabel("")
        self.other_label.setStyleSheet("color: #795548; font-size: 9pt;")
        content_layout.addWidget(self.other_label)

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
        self.cohort_table.setColumnCount(len(self.COHORT_COLUMNS))
        self.cohort_table.setHorizontalHeaderLabels(self.COHORT_COLUMNS)
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
        self.animal_table.setColumnCount(len(self.ANIMAL_COLUMNS))
        self.animal_table.setHorizontalHeaderLabels(self.ANIMAL_COLUMNS)
        self.animal_table.horizontalHeader().setSectionResizeMode(
            QHeaderView.Stretch)
        self.animal_table.setSelectionBehavior(QTableWidget.SelectRows)
        animal_layout.addWidget(self.animal_table)
        splitter.addWidget(animal_group)

        content_layout.addWidget(splitter, 1)

        # State legend
        legend = QHBoxLayout()
        legend.addWidget(QLabel("Legend:"))
        for key in ['current', 'crystallized', 'outdated', 'in_pipeline',
                    'in_review', 'not_on_this_node', 'quarantined', 'failed',
                    'other']:
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
            self.scope_label.hide()
            return

        self.unavailable_frame.hide()
        self.content_widget.show()
        self.scope_label.setText("\n".join(watcher_scope_lines(self.bridge.status)))
        self.scope_label.show()

        summary = self.bridge.get_archive_summary()

        # Status line (which database: on the scope line above)
        parts = []
        if summary.versions_path:
            parts.append(f"Versions: {summary.versions_path}")
        parts.append(f"Refreshed: {datetime.now().strftime('%H:%M:%S')}")
        self.status_label.setText('  |  '.join(parts))

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
        # Held for a reviewer / no file on this computer: their own cards
        # (they used to be counted as 'In Pipeline').
        self.summary_labels['in_review'].setText(
            f"In Review\n{summary.in_review}")
        self.summary_labels['not_on_this_node'].setText(
            f"Not on this computer\n{summary.not_on_this_node}")
        # Quarantined: held until a person fixes the recording, so not
        # Failed. Other: states mousedb does not know (named below).
        self.summary_labels['quarantined'].setText(
            f"Quarantined\n{summary.quarantined}")
        self.summary_labels['other'].setText(
            f"Other\n{sum(summary.other.values())}")
        self.other_label.setText(
            "States mousedb does not know: " + ", ".join(
                f"{st} = {n}" for st, n in sorted(summary.other.items()))
            if summary.other else "")

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
            summary.in_pipeline, summary.failed, summary.total_videos,
            in_review=summary.in_review,
            not_on_this_node=summary.not_on_this_node,
            other=sum(summary.other.values()),
            quarantined=summary.quarantined,
        )

        # Cohort table
        cohort_rollup = self.bridge.get_archive_cohort_rollup()
        self._cohorts_data = cohort_rollup
        self.cohort_table.setRowCount(len(cohort_rollup))
        for row, c in enumerate(cohort_rollup):
            self.cohort_table.setItem(row, 0, QTableWidgetItem(c.cohort_id))
            self.cohort_table.setItem(row, 1,
                                      QTableWidgetItem(str(c.total_videos)))
            self._fill_counts(self.cohort_table, row, 2, c)
            self.cohort_table.setItem(
                row, len(self.COHORT_COLUMNS) - 1,
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
            self._fill_counts(self.animal_table, row, 2, a)

    def _fill_counts(self, table, row, first_col, summary):
        """Write the COUNT_COLUMNS of one cohort/animal summary into a row,
        starting at ``first_col``."""
        for i, (_, attr, colour) in enumerate(self.COUNT_COLUMNS):
            value = getattr(summary, attr)
            if attr == 'other':
                value = sum(value.values())
            item = QTableWidgetItem(str(value))
            if colour and value > 0:
                item.setBackground(QColor(colour))
            if attr == 'other' and value:
                item.setToolTip(", ".join(
                    f"{st} = {n}" for st, n in sorted(summary.other.items())))
            table.setItem(row, first_col + i, item)
