"""
Timeline/Gantt view widget for visualizing cohort schedules.

Displays horizontal bars for each cohort showing their protocol phases,
with color-coding, current day marker, and interactive navigation.
"""

from datetime import date, timedelta
from typing import Optional, List, Dict, Callable

from qtpy.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QFrame, QPushButton, QComboBox, QGridLayout, QSizePolicy
)
from qtpy.QtCore import Qt, Signal, QRect, QPoint
from qtpy.QtGui import QPainter, QColor, QPen, QBrush, QFont, QFontMetrics


# Phase color scheme - consistent with protocol system
PHASE_COLORS = {
    'Ramp': '#FF9800',           # Orange
    'Training_Flat': '#4CAF50',  # Green
    'Training_Pillar': '#2196F3', # Blue
    'Pre-Injury_Test': '#9C27B0', # Purple
    'Surgery': '#F44336',         # Red
    'Recovery': '#795548',        # Brown
    'Post-Injury_Test': '#E91E63', # Pink
    'Easy_Rehab': '#00BCD4',      # Cyan
    'Flat_Rehab': '#8BC34A',      # Light Green
    'Pillar_Rehab': '#3F51B5',    # Indigo
    'default': '#9E9E9E',         # Gray for unknown phases
}

# Tray type colors (for mini indicators)
TRAY_COLORS = {
    'R': '#FF9800',  # Orange - Ramp
    'E': '#00BCD4',  # Cyan - Easy
    'F': '#4CAF50',  # Green - Flat
    'P': '#2196F3',  # Blue - Pillar
}


class TimelineGanttWidget(QWidget):
    """
    Gantt-style timeline widget showing cohort schedules.

    Features:
    - Horizontal bars per cohort
    - Color-coded phases
    - Current day marker
    - Click to navigate to data entry
    - Multi-cohort view
    - Legend
    """

    # Signal emitted when user clicks on a cohort/date
    cohort_date_clicked = Signal(str, object)  # cohort_id, date

    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self.db = db
        self._cohort_schedules = {}  # cohort_id -> list of phase dicts
        self._view_start = date.today() - timedelta(days=7)
        self._view_days = 60  # Show 60 days by default
        self._row_height = 40
        self._header_height = 50
        self._left_margin = 120  # Space for cohort labels
        self._day_width = 20

        self._setup_ui()

    def _setup_ui(self):
        """Set up the widget UI."""
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)

        # Controls row
        controls = QHBoxLayout()
        controls.setContentsMargins(5, 5, 5, 0)

        # Navigation buttons
        self.prev_btn = QPushButton("<< Prev Month")
        self.prev_btn.clicked.connect(self._prev_month)
        controls.addWidget(self.prev_btn)

        self.today_btn = QPushButton("Today")
        self.today_btn.clicked.connect(self._go_to_today)
        controls.addWidget(self.today_btn)

        self.next_btn = QPushButton("Next Month >>")
        self.next_btn.clicked.connect(self._next_month)
        controls.addWidget(self.next_btn)

        controls.addStretch()

        # View range selector
        controls.addWidget(QLabel("View:"))
        self.range_combo = QComboBox()
        self.range_combo.addItems(["30 days", "60 days", "90 days", "120 days"])
        self.range_combo.setCurrentIndex(1)  # Default 60 days
        self.range_combo.currentIndexChanged.connect(self._on_range_changed)
        controls.addWidget(self.range_combo)

        layout.addLayout(controls)

        # Main timeline area with scroll
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.timeline_canvas = TimelineCanvas(self)
        self.scroll_area.setWidget(self.timeline_canvas)

        layout.addWidget(self.scroll_area, 1)

        # Legend
        legend_frame = QFrame()
        legend_frame.setFrameStyle(QFrame.StyledPanel)
        legend_layout = QHBoxLayout(legend_frame)
        legend_layout.setContentsMargins(10, 5, 10, 5)

        legend_layout.addWidget(QLabel("Legend:"))

        # Add phase color indicators
        for phase_name, color in list(PHASE_COLORS.items())[:8]:  # Show first 8
            if phase_name == 'default':
                continue
            indicator = QLabel(f"  {phase_name.replace('_', ' ')}  ")
            indicator.setStyleSheet(f"""
                background-color: {color};
                color: white;
                padding: 2px 5px;
                border-radius: 3px;
                font-size: 10px;
            """)
            legend_layout.addWidget(indicator)

        legend_layout.addStretch()

        # Today marker indicator
        today_label = QLabel("| Today")
        today_label.setStyleSheet("color: #FF0000; font-weight: bold;")
        legend_layout.addWidget(today_label)

        layout.addWidget(legend_frame)

    def set_database(self, db):
        """Set the database connection."""
        self.db = db
        self.refresh()

    def refresh(self):
        """Refresh the timeline with current data."""
        if not self.db:
            return

        self._load_cohort_schedules()
        self.timeline_canvas.update()

    def _load_cohort_schedules(self):
        """Load schedule data for all active cohorts."""
        from ..schema import Cohort, Protocol, ProtocolPhase
        from .. import protocols

        self._cohort_schedules = {}

        with self.db.session() as session:
            # Get all active cohorts with start dates
            cohorts = session.query(Cohort).filter(
                Cohort.start_date.isnot(None),
                Cohort.is_archived == 0
            ).order_by(Cohort.start_date.desc()).all()

            for cohort in cohorts:
                if cohort.protocol_id is not None:
                    # Generate schedule from protocol
                    try:
                        schedule = protocols.generate_schedule(
                            session, cohort.cohort_id
                        )
                        if schedule:
                            self._cohort_schedules[cohort.cohort_id] = {
                                'start_date': cohort.start_date,
                                'protocol_name': schedule.get('protocol_name', 'Unknown'),
                                'phases': schedule.get('phases', [])
                            }
                    except Exception as e:
                        print(f"Error generating schedule for {cohort.cohort_id}: {e}")
                else:
                    # No protocol - infer from TIMELINE
                    try:
                        inferred = protocols.generate_schedule_from_timeline(
                            session, cohort.cohort_id
                        )
                        if inferred and inferred.get('phases'):
                            self._cohort_schedules[cohort.cohort_id] = {
                                'start_date': cohort.start_date,
                                'protocol_name': 'Inferred from Timeline',
                                'phases': inferred['phases']
                            }
                    except Exception as e:
                        print(f"Error inferring schedule for {cohort.cohort_id}: {e}")

    def _prev_month(self):
        """Move view back one month."""
        self._view_start -= timedelta(days=30)
        self.timeline_canvas.update()

    def _next_month(self):
        """Move view forward one month."""
        self._view_start += timedelta(days=30)
        self.timeline_canvas.update()

    def _go_to_today(self):
        """Center view on today."""
        self._view_start = date.today() - timedelta(days=7)
        self.timeline_canvas.update()

    def _on_range_changed(self, index):
        """Handle view range change."""
        ranges = [30, 60, 90, 120]
        self._view_days = ranges[index]
        self.timeline_canvas.update()

    def get_phase_color(self, phase_name: str) -> str:
        """Get color for a phase name."""
        # Check for exact match
        if phase_name in PHASE_COLORS:
            return PHASE_COLORS[phase_name]

        # Check for partial match
        for key, color in PHASE_COLORS.items():
            if key.lower() in phase_name.lower():
                return color

        return PHASE_COLORS['default']


class TimelineCanvas(QWidget):
    """Canvas widget for drawing the Gantt chart."""

    def __init__(self, parent_widget: TimelineGanttWidget):
        super().__init__()
        self.parent_widget = parent_widget
        self.setMouseTracking(True)
        self._hover_info = None

        # Ensure minimum size
        self.setMinimumHeight(200)

    def paintEvent(self, event):
        """Draw the timeline."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        pw = self.parent_widget

        # Calculate required size
        num_cohorts = len(pw._cohort_schedules)
        required_height = pw._header_height + (num_cohorts * pw._row_height) + 20
        required_width = pw._left_margin + (pw._view_days * pw._day_width) + 20

        self.setMinimumSize(required_width, max(200, required_height))

        # Background
        painter.fillRect(self.rect(), QColor('#FFFFFF'))

        # Draw header (dates)
        self._draw_header(painter)

        # Draw cohort rows
        self._draw_cohort_rows(painter)

        # Draw today marker
        self._draw_today_marker(painter)

        # Draw hover tooltip
        if self._hover_info:
            self._draw_tooltip(painter)

    def _draw_header(self, painter):
        """Draw the date header."""
        pw = self.parent_widget

        # Header background
        painter.fillRect(0, 0, self.width(), pw._header_height, QColor('#F5F5F5'))

        # Draw month labels and day numbers
        font = QFont()
        font.setPointSize(8)
        painter.setFont(font)

        current_month = None
        for i in range(pw._view_days):
            day = pw._view_start + timedelta(days=i)
            x = pw._left_margin + (i * pw._day_width)

            # Month label (when month changes)
            if current_month != day.month:
                current_month = day.month
                painter.setPen(QPen(QColor('#333333')))
                font.setBold(True)
                painter.setFont(font)
                painter.drawText(x, 15, day.strftime('%b %Y'))
                font.setBold(False)
                painter.setFont(font)

            # Day number
            painter.setPen(QPen(QColor('#666666')))

            # Highlight weekends
            if day.weekday() >= 5:  # Saturday or Sunday
                painter.fillRect(x, pw._header_height, pw._day_width,
                               self.height() - pw._header_height, QColor('#F0F0F0'))

            painter.drawText(x + 2, 35, str(day.day))

            # Vertical grid line
            painter.setPen(QPen(QColor('#E0E0E0')))
            painter.drawLine(x, pw._header_height, x, self.height())

        # Header bottom border
        painter.setPen(QPen(QColor('#CCCCCC')))
        painter.drawLine(0, pw._header_height, self.width(), pw._header_height)

    def _draw_cohort_rows(self, painter):
        """Draw cohort rows with phase bars."""
        pw = self.parent_widget

        font = QFont()
        font.setPointSize(9)
        painter.setFont(font)

        row = 0
        for cohort_id, schedule_data in pw._cohort_schedules.items():
            y = pw._header_height + (row * pw._row_height)

            # Row background (alternating)
            if row % 2 == 0:
                painter.fillRect(0, y, self.width(), pw._row_height, QColor('#FAFAFA'))

            # Cohort label
            painter.setPen(QPen(QColor('#333333')))
            painter.drawText(5, y + (pw._row_height // 2) + 5, cohort_id)

            # Draw phase bars
            for phase in schedule_data.get('phases', []):
                self._draw_phase_bar(painter, phase, y, cohort_id)

            # Row separator
            painter.setPen(QPen(QColor('#E0E0E0')))
            painter.drawLine(0, y + pw._row_height, self.width(), y + pw._row_height)

            row += 1

    def _draw_phase_bar(self, painter, phase: dict, row_y: int, cohort_id: str):
        """Draw a single phase bar."""
        pw = self.parent_widget

        start_date = phase.get('start_date')
        end_date = phase.get('end_date')
        phase_name = phase.get('phase_name', '')

        if not start_date or not end_date:
            return

        # Calculate x positions
        start_offset = (start_date - pw._view_start).days
        end_offset = (end_date - pw._view_start).days

        # Skip if completely outside view
        if end_offset < 0 or start_offset > pw._view_days:
            return

        # Clamp to view bounds
        start_offset = max(0, start_offset)
        end_offset = min(pw._view_days, end_offset)

        x = pw._left_margin + (start_offset * pw._day_width)
        width = (end_offset - start_offset + 1) * pw._day_width

        # Bar dimensions
        bar_y = row_y + 8
        bar_height = pw._row_height - 16

        # Get phase color
        color = QColor(pw.get_phase_color(phase_name))

        # Draw bar
        painter.setPen(Qt.NoPen)
        painter.setBrush(QBrush(color))
        painter.drawRoundedRect(int(x), int(bar_y), int(width), int(bar_height), 3, 3)

        # Draw phase name if bar is wide enough
        if width > 60:
            painter.setPen(QPen(QColor('#FFFFFF')))
            font = QFont()
            font.setPointSize(8)
            painter.setFont(font)

            # Truncate text if needed
            display_name = phase_name.replace('_', ' ')
            fm = QFontMetrics(font)
            if fm.horizontalAdvance(display_name) > width - 10:
                display_name = display_name[:8] + '...'

            painter.drawText(int(x + 5), int(bar_y + bar_height - 5), display_name)

        # Draw tray type indicator
        tray_type = phase.get('tray_type_code')
        if tray_type and tray_type in TRAY_COLORS:
            indicator_size = 10
            indicator_x = x + width - indicator_size - 3
            indicator_y = bar_y + 3

            painter.setBrush(QBrush(QColor(TRAY_COLORS[tray_type])))
            painter.setPen(QPen(QColor('#FFFFFF'), 1))
            painter.drawEllipse(int(indicator_x), int(indicator_y),
                              indicator_size, indicator_size)

            # Tray letter
            painter.setPen(QPen(QColor('#FFFFFF')))
            font = QFont()
            font.setPointSize(6)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(int(indicator_x + 2), int(indicator_y + 8), tray_type)

    def _draw_today_marker(self, painter):
        """Draw vertical line for today."""
        pw = self.parent_widget
        today = date.today()

        offset = (today - pw._view_start).days
        if 0 <= offset <= pw._view_days:
            x = pw._left_margin + (offset * pw._day_width)

            # Red vertical line
            painter.setPen(QPen(QColor('#FF0000'), 2))
            painter.drawLine(int(x), int(pw._header_height), int(x), self.height())

            # "Today" label
            painter.setPen(QPen(QColor('#FF0000')))
            font = QFont()
            font.setPointSize(8)
            font.setBold(True)
            painter.setFont(font)
            painter.drawText(int(x + 3), int(pw._header_height - 5), "Today")

    def _draw_tooltip(self, painter):
        """Draw hover tooltip."""
        if not self._hover_info:
            return

        text = self._hover_info.get('text', '')
        pos = self._hover_info.get('pos', QPoint(0, 0))

        font = QFont()
        font.setPointSize(9)
        painter.setFont(font)

        fm = QFontMetrics(font)
        text_rect = fm.boundingRect(text)

        # Tooltip background
        padding = 5
        tooltip_rect = QRect(
            pos.x() + 10,
            pos.y() - text_rect.height() - padding * 2,
            text_rect.width() + padding * 2,
            text_rect.height() + padding * 2
        )

        # Keep tooltip on screen
        if tooltip_rect.right() > self.width():
            tooltip_rect.moveRight(self.width() - 5)
        if tooltip_rect.top() < 0:
            tooltip_rect.moveTop(pos.y() + 20)

        painter.fillRect(tooltip_rect, QColor('#333333'))
        painter.setPen(QPen(QColor('#FFFFFF')))
        painter.drawText(tooltip_rect.adjusted(padding, padding, -padding, -padding),
                        Qt.AlignCenter, text)

    def mouseMoveEvent(self, event):
        """Handle mouse movement for hover effects."""
        pw = self.parent_widget
        pos = event.pos()

        # Check if over a phase bar
        self._hover_info = None

        row = 0
        for cohort_id, schedule_data in pw._cohort_schedules.items():
            y = pw._header_height + (row * pw._row_height)

            if y <= pos.y() <= y + pw._row_height:
                # Check phases
                for phase in schedule_data.get('phases', []):
                    start_date = phase.get('start_date')
                    end_date = phase.get('end_date')

                    if not start_date or not end_date:
                        continue

                    start_offset = (start_date - pw._view_start).days
                    end_offset = (end_date - pw._view_start).days

                    x_start = pw._left_margin + (start_offset * pw._day_width)
                    x_end = pw._left_margin + ((end_offset + 1) * pw._day_width)

                    if x_start <= pos.x() <= x_end:
                        phase_name = phase.get('phase_name', 'Unknown')
                        tray = phase.get('tray_type_code', '')
                        sessions = phase.get('sessions_per_day', 1)

                        self._hover_info = {
                            'text': f"{phase_name} | Tray: {tray} | {sessions}/day | {start_date} - {end_date}",
                            'pos': pos,
                            'cohort': cohort_id,
                            'phase': phase
                        }
                        break
                break
            row += 1

        self.update()

    def mousePressEvent(self, event):
        """Handle mouse click for navigation."""
        if event.button() == Qt.LeftButton and self._hover_info:
            cohort = self._hover_info.get('cohort')
            phase = self._hover_info.get('phase')
            if cohort and phase:
                start_date = phase.get('start_date')
                self.parent_widget.cohort_date_clicked.emit(cohort, start_date)


class MiniTimelineWidget(QWidget):
    """
    Compact timeline widget for embedding in other tabs.
    Shows a simplified view of cohort schedules.
    """

    cohort_clicked = Signal(str)  # cohort_id

    def __init__(self, db=None, parent=None):
        super().__init__(parent)
        self.db = db
        self._cohort_schedules = {}
        self._view_start = date.today() - timedelta(days=7)
        self._view_days = 45

        self.setMinimumHeight(100)
        self.setMaximumHeight(200)

    def set_database(self, db):
        """Set database connection."""
        self.db = db
        self.refresh()

    def refresh(self):
        """Refresh the mini timeline."""
        if not self.db:
            return

        from ..schema import Cohort
        from .. import protocols

        self._cohort_schedules = {}

        with self.db.session() as session:
            # Get recent active cohorts (limit to 8 for mini view)
            cohorts = session.query(Cohort).filter(
                Cohort.start_date.isnot(None),
                Cohort.is_archived == 0
            ).order_by(Cohort.start_date.desc()).limit(8).all()

            for cohort in cohorts:
                if cohort.protocol_id is not None:
                    try:
                        schedule = protocols.generate_schedule(
                            session, cohort.cohort_id
                        )
                        if schedule:
                            self._cohort_schedules[cohort.cohort_id] = {
                                'start_date': cohort.start_date,
                                'phases': schedule.get('phases', [])
                            }
                    except Exception:
                        pass
                else:
                    # No protocol - infer from TIMELINE
                    try:
                        inferred = protocols.generate_schedule_from_timeline(
                            session, cohort.cohort_id
                        )
                        if inferred and inferred.get('phases'):
                            self._cohort_schedules[cohort.cohort_id] = {
                                'start_date': cohort.start_date,
                                'phases': inferred['phases']
                            }
                    except Exception:
                        pass

        # Auto-fit view to show all cohorts
        if self._cohort_schedules:
            all_starts = [s['start_date'] for s in self._cohort_schedules.values()]
            earliest = min(all_starts)
            self._view_start = earliest - timedelta(days=3)
            all_ends = []
            for s in self._cohort_schedules.values():
                phases = s.get('phases', [])
                if phases:
                    last_end = max(p.get('end_date', s['start_date']) for p in phases)
                    all_ends.append(last_end)
                else:
                    all_ends.append(s['start_date'] + timedelta(days=69))
            latest_end = max(all_ends) if all_ends else earliest + timedelta(days=69)
            self._view_days = max(45, (latest_end - self._view_start).days + 7)

        self.update()

    def paintEvent(self, event):
        """Draw the mini timeline."""
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        # Background
        painter.fillRect(self.rect(), QColor('#FFFFFF'))

        if not self._cohort_schedules:
            painter.setPen(QPen(QColor('#999999')))
            painter.drawText(self.rect(), Qt.AlignCenter,
                           "No cohorts with protocols")
            return

        # Calculate dimensions
        left_margin = 80
        day_width = (self.width() - left_margin - 10) / self._view_days
        row_height = min(30, (self.height() - 20) / max(1, len(self._cohort_schedules)))

        # Draw cohorts
        row = 0
        for cohort_id, schedule_data in self._cohort_schedules.items():
            y = 10 + (row * row_height)

            # Cohort label
            painter.setPen(QPen(QColor('#333333')))
            font = QFont()
            font.setPointSize(8)
            painter.setFont(font)
            painter.drawText(5, int(y + row_height / 2 + 3), cohort_id)

            # Phase bars
            for phase in schedule_data.get('phases', []):
                start_date = phase.get('start_date')
                end_date = phase.get('end_date')

                if not start_date or not end_date:
                    continue

                start_offset = (start_date - self._view_start).days
                end_offset = (end_date - self._view_start).days

                if end_offset < 0 or start_offset > self._view_days:
                    continue

                start_offset = max(0, start_offset)
                end_offset = min(self._view_days, end_offset)

                x = left_margin + (start_offset * day_width)
                width = max(2, (end_offset - start_offset + 1) * day_width)

                # Get color
                phase_name = phase.get('phase_name', '')
                color = QColor(PHASE_COLORS.get(phase_name, PHASE_COLORS['default']))

                painter.setPen(Qt.NoPen)
                painter.setBrush(QBrush(color))
                painter.drawRoundedRect(int(x), int(y + 4), int(width),
                                       int(row_height - 8), 2, 2)

            row += 1

        # Today marker
        today_offset = (date.today() - self._view_start).days
        if 0 <= today_offset <= self._view_days:
            x = left_margin + (today_offset * day_width)
            painter.setPen(QPen(QColor('#FF0000'), 2))
            painter.drawLine(int(x), 0, int(x), self.height())
