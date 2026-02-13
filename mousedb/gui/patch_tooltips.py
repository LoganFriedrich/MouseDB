#!/usr/bin/env python3
"""
Patch script to add tooltips and HelpButton to 4 tab classes in app.py.
Reads file once, makes all edits, writes once.
"""

import re

APP_PY = r"y:\2_Connectome\Databases\mousedb\mousedb\gui\app.py"

def main():
    with open(APP_PY, 'r', encoding='utf-8') as f:
        content = f.read()

    # Dashboard tab edits
    # 1. Add HelpButton after "header_layout = QHBoxLayout()"
    content = content.replace(
        '        # Cohort Selection Header\n        header_layout = QHBoxLayout()\n        header_layout.addWidget(QLabel("Cohort:"))',
        '''        # Cohort Selection Header
        header_layout = QHBoxLayout()

        # Help button
        help_btn = HelpButton("Dashboard",
            "View real-time statistics for the selected cohort: pellet retrieval "
            "rates by phase, per-animal performance, weight tracking, and BrainGlobe "
            "detection progress. Click 'Generate Analysis' to refresh.")
        header_layout.addWidget(help_btn)

        header_layout.addWidget(QLabel("Cohort:"))'''
    )

    # 2. Add tooltip to cohort_combo (Dashboard)
    content = re.sub(
        r'(        self\.cohort_combo = QComboBox\(\)\n        self\.cohort_combo\.setMinimumWidth\(150\))\n(        self\.cohort_combo\.currentIndexChanged\.connect\(self\._on_cohort_changed\))',
        r'\1\n        self.cohort_combo.setToolTip("Select cohort to view statistics for")\n\2',
        content, count=1
    )

    # 3. Add tooltip to refresh_btn
    content = re.sub(
        r'(        self\.refresh_btn\.clicked\.connect\(self\._refresh_stats\))',
        r'        self.refresh_btn.setToolTip("Recalculate all statistics from the database")\n\1',
        content, count=1
    )

    # 4. Add tooltip to phase_table
    content = re.sub(
        r'(        self\.phase_table = QTableWidget\(\)\n        self\.phase_table\.setColumnCount\(7\)\n        self\.phase_table\.setHorizontalHeaderLabels\(\[\n            \'Phase\', \'Sessions\', \'Pellets\', \'Retrieved %\', \'Contacted %\', \'Mean ± SD\', \'N Animals\'\n        \]\))',
        r'\1\n        self.phase_table.setToolTip("Pellet retrieval and contact rates broken down by experimental phase")',
        content, count=1
    )

    # 5. Add tooltip to subjects_table (Dashboard - line ~1672)
    content = re.sub(
        r'(        self\.subjects_table = QTableWidget\(\)\n        self\.subjects_table\.setColumnCount\(11\)\n        self\.subjects_table\.setHorizontalHeaderLabels\(\[\n            \'Subject\', \'Sex\', \'Sessions\', \'Pellets\',\n            \'Baseline %\', \'Post-Injury %\', \'Change\',\n            \'Injury \(kDyn\)\', \'Disp \(µm\)\', \'Weight %\', \'Status\'\n        \]\))',
        r'\1\n        self.subjects_table.setToolTip("Per-animal performance summary with pre/post injury comparison")',
        content, count=1
    )

    # 6. Add tooltip to sessions_table
    content = re.sub(
        r'(        self\.sessions_table = QTableWidget\(\)\n        self\.sessions_table\.setColumnCount\(10\)\n        self\.sessions_table\.setHorizontalHeaderLabels\(\[\n            \'Date\', \'Phase\', \'DPI\', \'Weight \(g\)\', \'Weight %\',\n            \'Miss\', \'Displaced\', \'Retrieved\', \'Retrieved %\', \'Contacted %\'\n        \]\))',
        r'\1\n        self.sessions_table.setToolTip("Detailed per-session pellet score metrics")',
        content, count=1
    )

    # 7. Add tooltip to brains_table
    content = re.sub(
        r'(        self\.brains_table = QTableWidget\(\)\n        self\.brains_table\.setColumnCount\(7\)\n        self\.brains_table\.setHorizontalHeaderLabels\(\[\n            \'Subject\', \'Brain ID\', \'Status\', \'Cells Detected\',\n            \'Regions\', \'Best Run\', \'Mag/Z-step\'\n        \]\))',
        r'\1\n        self.brains_table.setToolTip("BrainGlobe detection status and cell counts per brain")',
        content, count=1
    )

    # 8. Add tooltip to regions_table
    content = re.sub(
        r'(        self\.regions_table = QTableWidget\(\)\n        self\.regions_table\.setColumnCount\(5\)\n        self\.regions_table\.setHorizontalHeaderLabels\(\[\n            \'Region\', \'Acronym\', \'Hemisphere\', \'Cell Count\', \'Density\'\n        \]\))\n(        self\.regions_table\.horizontalHeader\(\)\.setStretchLastSection\(True\))',
        r'\1\n        self.regions_table.setToolTip("Cell counts per brain region from BrainGlobe analysis")\n\2',
        content, count=1
    )

    # BulkTrayEntryTab edits
    # 9. Add HelpButton (around line 2591)
    content = re.sub(
        r'(    def _setup_ui\(self\):\n        main_layout = QVBoxLayout\(self\)\n        main_layout\.setSpacing\(8\)\n\n        # Header controls - Row 1\n        header_layout = QHBoxLayout\(\))\n\n(        # Cohort selection\n        header_layout\.addWidget\(QLabel\("Cohort:"\)\))',
        r'''\1

        # Help button
        help_btn = HelpButton("Testing Entry",
            "Score pellet retrieval for up to 8 mice at once. Click pellet buttons "
            "to cycle through scores: 0 (miss), 1 (displaced), 2 (retrieved). "
            "Use keyboard 0/1/2 for fast entry. Arrow keys or Tab to navigate.")
        header_layout.addWidget(help_btn)

\2''',
        content, count=1
    )

    # 10. Add tooltips for BulkTrayEntryTab widgets
    # cohort_combo
    content = re.sub(
        r'(        self\.cohort_combo = QComboBox\(\)\n        self\.cohort_combo\.setMinimumWidth\(120\))\n(        self\.cohort_combo\.currentIndexChanged\.connect\(self\._on_cohort_changed\))',
        r'\1\n        self.cohort_combo.setToolTip("Select the cohort for pellet scoring")\n\2',
        content, count=1  # First occurrence after Dashboard
    )

    # date_combo
    content = re.sub(
        r'(        self\.date_combo = QComboBox\(\)\n        self\.date_combo\.setMinimumWidth\(200\))\n(        self\.date_combo\.currentIndexChanged\.connect\(self\._on_date_changed\))',
        r'\1\n        self.date_combo.setToolTip("Select the testing session date")\n\2',
        content, count=1
    )

    # mouse_group_combo
    content = re.sub(
        r'(        self\.mouse_group_combo = QComboBox\(\)\n        self\.mouse_group_combo\.setMinimumWidth\(100\))\n(        self\.mouse_group_combo\.currentIndexChanged\.connect\(self\._on_mouse_group_changed\))',
        r'\1\n        self.mouse_group_combo.setToolTip("Choose which group of mice to display (for cohorts with more than 8 mice)")\n\2',
        content, count=1
    )

    # save_btn (BulkTrayEntryTab)
    content = re.sub(
        r'(        self\.save_btn = QPushButton\("Save All"\)\n        self\.save_btn\.setObjectName\("success_button"\)\n        self\.save_btn\.clicked\.connect\(self\._save_all\)\n        self\.save_btn\.setMinimumWidth\(120\))',
        r'\1\n        self.save_btn.setToolTip("Save all entered pellet scores for this session")',
        content, count=1
    )

    # BulkWeightEntryTab edits
    # 11. Add HelpButton (around line 3360)
    content = re.sub(
        r'(    def _setup_ui\(self\):\n        main_layout = QVBoxLayout\(self\)\n        main_layout\.setSpacing\(10\)\n\n        # Header controls\n        header_layout = QHBoxLayout\(\))\n\n(        # Cohort selection\n        header_layout\.addWidget\(QLabel\("Cohort:"\)\))',
        r'''\1

        # Help button
        help_btn = HelpButton("Weight Entry",
            "Enter body weights for all mice in a cohort on a specific date. "
            "Baseline weights are calculated from the first 3 days of the baseline phase. "
            "Weight percentages are color-coded: green (healthy), orange (warning), red (critical).")
        header_layout.addWidget(help_btn)

\2''',
        content, count=1
    )

    # 12. Add tooltips for BulkWeightEntryTab widgets
    # cohort_combo (second occurrence - in BulkWeightEntryTab)
    content = re.sub(
        r'(class BulkWeightEntryTab.*?self\.cohort_combo = QComboBox\(\)\n        self\.cohort_combo\.setMinimumWidth\(120\))\n(        self\.cohort_combo\.currentIndexChanged\.connect\(self\._on_cohort_changed\))',
        r'\1\n        self.cohort_combo.setToolTip("Select the cohort for weight entry")\n\2',
        content, flags=re.DOTALL, count=1
    )

    # date_edit
    content = re.sub(
        r'(        self\.date_edit = QDateEdit\(\)\n        self\.date_edit\.setCalendarPopup\(True\)\n        self\.date_edit\.setDate\(QDate\.currentDate\(\)\))\n(        self\.date_edit\.dateChanged\.connect\(self\._on_date_changed\))',
        r'\1\n        self.date_edit.setToolTip("Select the date these weights were recorded")\n\2',
        content, count=1
    )

    # save_btn (BulkWeightEntryTab - find second occurrence)
    content = re.sub(
        r'(        self\.save_btn = QPushButton\("Save All Weights"\)\n        self\.save_btn\.setObjectName\("success_button"\)\n        self\.save_btn\.clicked\.connect\(self\._save_all\)\n        self\.save_btn\.setMinimumWidth\(150\))',
        r'\1\n        self.save_btn.setToolTip("Save all entered weights to the database")',
        content, count=1
    )

    # RampEntryTab edits
    # 13. Add HelpButton (around line 3762)
    content = re.sub(
        r'(        # Header with helpful context\n        header_layout = QHBoxLayout\(\)\n\n        info_label = QLabel\(\n            "Ramp Phase \(Days 0-3\): Enter body weights and food tray weights for all mice"\n        \)\n        info_label\.setStyleSheet\("font-weight: bold; color: #2196F3; font-size: 9pt;"\)\n        header_layout\.addWidget\(info_label\))',
        r'''\1

        # Help button
        help_btn = HelpButton("Ramp Entry",
            "Enter body weights and food consumption during the food deprivation "
            "ramp phase (Days 0-3). Weights are recorded daily and food consumed "
            "is calculated from tray start and end weights.")
        header_layout.addWidget(help_btn)''',
        content, count=1
    )

    # 14. Add tooltips for RampEntryTab widgets
    # cohort_combo (third occurrence - in RampEntryTab)
    content = re.sub(
        r'(class RampEntryTab.*?self\.cohort_combo = QComboBox\(\)\n        self\.cohort_combo\.setMinimumWidth\(120\))\n(        self\.cohort_combo\.currentIndexChanged\.connect\(self\._on_cohort_changed\))',
        r'\1\n        self.cohort_combo.setToolTip("Select the cohort for ramp phase data entry")\n\2',
        content, flags=re.DOTALL, count=1
    )

    # day_combo
    content = re.sub(
        r'(        self\.day_combo = QComboBox\(\)\n        self\.day_combo\.addItems\(\["Day 0 \(First FD\)", "Day 1", "Day 2", "Day 3"\]\))\n(        self\.day_combo\.currentIndexChanged\.connect\(self\._on_day_changed\))',
        r'\1\n        self.day_combo.setToolTip("Select the ramp day (0-3) to enter data for")\n\2',
        content, count=1
    )

    # save_btn (RampEntryTab - find third occurrence, after "Save All")
    content = re.sub(
        r'(        self\.save_btn = QPushButton\("Save All"\)\n        self\.save_btn\.setObjectName\("success_button"\)\n        self\.save_btn\.clicked\.connect\(self\._save_all\)\n        self\.save_btn\.setMinimumWidth\(150\))',
        r'\1\n        self.save_btn.setToolTip("Save all ramp data for this day")',
        content, count=1
    )

    with open(APP_PY, 'w', encoding='utf-8') as f:
        f.write(content)

    print("✓ All tooltips and HelpButtons added successfully")

if __name__ == '__main__':
    main()
