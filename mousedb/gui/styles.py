"""
Consistent styling for Connectome Data Entry GUI.

Uses a clean, high-contrast design optimized for data entry.
"""

# Color palette
COLORS = {
    'primary': '#2196F3',      # Blue
    'success': '#4CAF50',      # Green
    'warning': '#FF9800',      # Orange
    'error': '#F44336',        # Red
    'background': '#FFFFFF',   # White
    'surface': '#F5F5F5',      # Light gray
    'text': '#212121',         # Dark gray
    'text_secondary': '#757575',  # Medium gray
    'border': '#E0E0E0',       # Light border
}

# Score colors for pellet entry
SCORE_COLORS = {
    0: '#FFCDD2',  # Light red - miss
    1: '#FFF9C4',  # Light yellow - displaced
    2: '#C8E6C9',  # Light green - retrieved
    None: '#FFFFFF',  # White - not entered
}

SCORE_LABELS = {
    0: 'Miss',
    1: 'Displaced',
    2: 'Retrieved',
}

# Main stylesheet
STYLESHEET = """
QMainWindow {
    background-color: #FFFFFF;
}

QWidget {
    font-family: 'Segoe UI', Arial, sans-serif;
    font-size: 9pt;
}

QLabel {
    color: #212121;
}

QLabel#header {
    font-size: 11pt;
    font-weight: bold;
    color: #2196F3;
    padding: 5px 0;
}

QLabel#step_label {
    font-size: 10pt;
    font-weight: bold;
    color: #424242;
    padding: 3px 0;
}

QComboBox {
    padding: 4px;
    border: 1px solid #E0E0E0;
    border-radius: 3px;
    background-color: white;
    min-width: 150px;
}

QComboBox:focus {
    border-color: #2196F3;
}

QComboBox::drop-down {
    border: none;
    padding-right: 10px;
}

QLineEdit, QSpinBox, QDoubleSpinBox {
    padding: 4px;
    border: 1px solid #E0E0E0;
    border-radius: 3px;
    background-color: white;
}

QLineEdit:focus, QSpinBox:focus, QDoubleSpinBox:focus {
    border-color: #2196F3;
}

QLineEdit:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled {
    background-color: #F5F5F5;
    color: #757575;
}

QPushButton {
    padding: 5px 12px;
    border: none;
    border-radius: 3px;
    background-color: #2196F3;
    color: white;
    font-weight: bold;
}

QPushButton:hover {
    background-color: #1976D2;
}

QPushButton:pressed {
    background-color: #0D47A1;
}

QPushButton:disabled {
    background-color: #BDBDBD;
}

QPushButton#success_button {
    background-color: #4CAF50;
}

QPushButton#success_button:hover {
    background-color: #388E3C;
}

QPushButton#secondary_button {
    background-color: #757575;
}

QPushButton#secondary_button:hover {
    background-color: #616161;
}

QGroupBox {
    border: 1px solid #E0E0E0;
    border-radius: 4px;
    margin-top: 8px;
    padding-top: 8px;
    background-color: #FAFAFA;
}

QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    padding: 5px 10px;
    color: #424242;
    font-weight: bold;
}

QTableWidget {
    border: 2px solid #E0E0E0;
    border-radius: 4px;
    gridline-color: #E0E0E0;
}

QTableWidget::item {
    padding: 5px;
}

QTableWidget::item:selected {
    background-color: #BBDEFB;
    color: #212121;
}

QStatusBar {
    background-color: #F5F5F5;
    color: #424242;
}

QMessageBox {
    background-color: white;
}

/* Pellet score button styles */
QPushButton#pellet_button {
    min-width: 26px;
    min-height: 26px;
    padding: 0;
    font-size: 9pt;
    font-weight: bold;
    border-radius: 3px;
}

QPushButton#pellet_button[score="0"] {
    background-color: #FFCDD2;
    color: #C62828;
    border: 2px solid #EF9A9A;
}

QPushButton#pellet_button[score="1"] {
    background-color: #FFF9C4;
    color: #F57F17;
    border: 2px solid #FFF59D;
}

QPushButton#pellet_button[score="2"] {
    background-color: #C8E6C9;
    color: #2E7D32;
    border: 2px solid #A5D6A7;
}

QPushButton#pellet_button[score="none"] {
    background-color: #FFFFFF;
    color: #9E9E9E;
    border: 2px solid #E0E0E0;
}

/* Focus indicator for keyboard navigation */
QPushButton#pellet_button:focus {
    border: 3px solid #2196F3;
    outline: none;
}

QTabWidget::pane {
    border: 1px solid #E0E0E0;
    border-radius: 4px;
    background-color: white;
}

QTabBar::tab {
    background-color: #F5F5F5;
    border: 1px solid #E0E0E0;
    border-bottom: none;
    padding: 4px 10px;
    margin-right: 2px;
    border-top-left-radius: 4px;
    border-top-right-radius: 4px;
}

QTabBar::tab:selected {
    background-color: white;
    border-bottom: 2px solid white;
    margin-bottom: -1px;
}

QTabBar::tab:hover:!selected {
    background-color: #E3F2FD;
}

QScrollArea {
    border: none;
    background-color: white;
}
"""
