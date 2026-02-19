"""
Lab Meeting Presentation Figure Generator.

Generates 15 high-resolution PNG figures covering all domains of the Connectome project.
Single-script, turnkey: run once, get all figures.

Usage:
    python lab_figures.py
    python lab_figures.py --only 04,05,10
    python lab_figures.py --output-dir /custom/path
"""

import matplotlib
matplotlib.use('Agg')
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.patches import FancyBboxPatch, FancyArrowPatch, Polygon
import matplotlib.gridspec as gridspec
import numpy as np
import pandas as pd
import sqlite3
import argparse
import traceback
from pathlib import Path
from datetime import datetime, date

# =============================================================================
# PATHS
# =============================================================================

BASE = Path(r'Y:\2_Connectome')
DATA_SUMMARY = BASE / 'Tissue' / 'MouseBrain_Pipeline' / '3D_Cleared' / '2_Data_Summary'
BEHAVIOR = BASE / 'Behavior' / 'MouseReach_Pipeline'
DATABASES = BASE / 'Databases'
SLICE_DATA = BASE / 'Tissue' / 'MouseBrain_Pipeline' / '2D_Slices' / 'ENCR' / 'batch_results'

BRAIN_IDS = [
    '349_CNT_01_02',
    '357_CNT_02_08',
    '367_CNT_03_07',
    '368_CNT_03_08',
]
BRAIN_LABELS = ['Brain 349', 'Brain 357', 'Brain 367', 'Brain 368']

# =============================================================================
# STYLE
# =============================================================================

# Domain colors
C_BRAIN = '#2980B9'
C_REACH = '#E67E22'
C_CAM = '#8E44AD'
C_DB = '#27AE60'
C_SLICE = '#E74C3C'

# Brain palette
BRAIN_COLORS = ['#5DADE2', '#58D68D', '#EC7063', '#F4D03F']

# Outcome colors
OUTCOME_COLORS = {
    'retrieved': '#27AE60',
    'displaced_sa': '#F39C12',
    'displaced_outside': '#E67E22',
    'untouched': '#E74C3C',
    'uncertain_outside': '#BDC3C7',
}

# Schematic colors
C_INPUT = '#E8F4FD'
C_PROCESS = '#F0F4C3'
C_OUTPUT = '#E1BEE7'
C_DECISION = '#FFECB3'
C_STAGE = '#2C3E50'
C_ARROW = '#455A64'
C_BORDER = '#78909C'
C_TEXT = '#2C3E50'
C_WHITE = '#FFFFFF'
C_LIGHT_BG = '#F8F9FA'


def apply_style():
    """Set global matplotlib style."""
    plt.rcParams.update({
        'font.family': 'Segoe UI',
        'font.size': 12,
        'axes.titlesize': 18,
        'axes.titleweight': 'bold',
        'axes.labelsize': 14,
        'xtick.labelsize': 11,
        'ytick.labelsize': 11,
        'legend.fontsize': 11,
        'figure.facecolor': 'white',
        'axes.facecolor': 'white',
        'axes.spines.top': False,
        'axes.spines.right': False,
        'axes.grid': False,
        'savefig.dpi': 300,
        'savefig.facecolor': 'white',
    })


# =============================================================================
# SCHEMATIC HELPERS
# =============================================================================

def draw_box(ax, x, y, w, h, text, color, text_color=C_TEXT, fontsize=10,
             fontweight='normal', border_color=C_BORDER, alpha=1.0,
             style='round', linewidth=1.0, zorder=2):
    """Draw a rounded rectangle with centered text."""
    boxstyle = 'round,pad=0.02' if style == 'round' else 'square,pad=0.01'
    box = FancyBboxPatch((x - w/2, y - h/2), w, h,
                         boxstyle=boxstyle,
                         facecolor=color, edgecolor=border_color,
                         linewidth=linewidth, alpha=alpha, zorder=zorder)
    ax.add_patch(box)
    ax.text(x, y, text, ha='center', va='center', fontsize=fontsize,
            color=text_color, fontweight=fontweight, zorder=zorder+1,
            linespacing=1.3)
    return box


def draw_arrow(ax, x1, y1, x2, y2, label='', color=C_ARROW, fontsize=9,
               linewidth=1.5, style='->'):
    """Draw an arrow between two points."""
    ax.annotate('', xy=(x2, y2), xytext=(x1, y1),
                arrowprops=dict(arrowstyle=style, color=color, lw=linewidth),
                zorder=1)
    if label:
        mx, my = (x1 + x2) / 2, (y1 + y2) / 2
        ax.text(mx, my + 0.015, label, ha='center', va='bottom',
                fontsize=fontsize, color=color, style='italic')


def draw_stat_card(ax, x, y, w, h, number, label, accent_color, fontsize_num=28,
                   fontsize_label=10):
    """Draw an infographic stat card."""
    box = FancyBboxPatch((x - w/2, y - h/2), w, h,
                         boxstyle='round,pad=0.02',
                         facecolor='white', edgecolor='#E0E0E0',
                         linewidth=1.5, zorder=2)
    ax.add_patch(box)
    # Accent bar on left
    bar = FancyBboxPatch((x - w/2, y - h/2), w * 0.03, h,
                         boxstyle='square,pad=0',
                         facecolor=accent_color, edgecolor='none',
                         zorder=3)
    ax.add_patch(bar)
    # Number
    ax.text(x + w * 0.02, y + h * 0.12, str(number), ha='center', va='center',
            fontsize=fontsize_num, fontweight='bold', color=C_TEXT, zorder=3)
    # Label
    ax.text(x + w * 0.02, y - h * 0.25, label, ha='center', va='center',
            fontsize=fontsize_label, color='#666666', zorder=3)


# =============================================================================
# DATA LOADERS
# =============================================================================

def load_brain_counts():
    """Load cell counts for all brains. Returns dict of brain_id -> DataFrame."""
    counts = {}
    for bid in BRAIN_IDS:
        # Filename has doubled brain ID
        pattern = f'{bid}_{bid}_1p625x_z4_counts.csv'
        path = DATA_SUMMARY / pattern
        if path.exists():
            df = pd.read_csv(path)
            counts[bid] = df
    return counts


def load_elife_comparison():
    """Load eLife comparison data."""
    path = DATA_SUMMARY / 'elife_comparison_with_reference.csv'
    if path.exists():
        return pd.read_csv(path)
    return None


def load_laterality():
    """Load hemisphere laterality analysis."""
    path = DATA_SUMMARY / 'laterality' / 'hemisphere_laterality_analysis.csv'
    if path.exists():
        return pd.read_csv(path)
    return None


def load_batch_2d():
    """Load 2D slice batch summary."""
    path = SLICE_DATA / 'batch_summary.csv'
    if path.exists():
        return pd.read_csv(path)
    return None


def load_reach_kinematics():
    """Load reach kinematics data."""
    path = BEHAVIOR / 'reach_kinematics.csv'
    if path.exists():
        return pd.read_csv(path)
    return None


def load_reach_summary():
    """Load PI summary."""
    path = BEHAVIOR / 'summary_for_PI.csv'
    if path.exists():
        return pd.read_csv(path)
    return None


def load_calibration_runs():
    """Load calibration runs CSV."""
    path = DATA_SUMMARY / 'calibration_runs.csv'
    if path.exists():
        return pd.read_csv(path)
    return None


def query_db_stats(db_path):
    """Query database for summary statistics - all tables."""
    stats = {}
    try:
        conn = sqlite3.connect(str(db_path))
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        for (name,) in tables:
            try:
                count = conn.execute(f'SELECT COUNT(*) FROM [{name}]').fetchone()[0]
                stats[name] = count
            except Exception:
                stats[name] = 0
        conn.close()
    except Exception:
        pass
    return stats


def query_pellet_phases(db_path):
    """Query pellet scores with test_phase already classified."""
    try:
        conn = sqlite3.connect(str(db_path))
        query = """
        SELECT
            p.subject_id,
            p.session_date,
            p.score,
            p.test_phase,
            sub.cohort_id
        FROM pellet_scores p
        JOIN subjects sub ON p.subject_id = sub.subject_id
        WHERE sub.cohort_id IN ('CNT_01', 'CNT_02', 'CNT_03')
        """
        df = pd.read_sql_query(query, conn)
        conn.close()
        return df
    except Exception as e:
        print(f'    DB query error: {e}')
        return None


# =============================================================================
# FIGURE FUNCTIONS
# =============================================================================

def fig_01_project_overview(output_dir):
    """Connectome Project Overview schematic."""
    fig, ax = plt.subplots(1, 1, figsize=(16, 9))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')

    # Title
    ax.text(0.5, 0.95, 'Connectome Project: Supraspinal Control of Skilled Reaching',
            ha='center', va='top', fontsize=22, fontweight='bold', color=C_TEXT)
    ax.text(0.5, 0.90, 'Integrated Multi-Tool Platform for Behavior + Tissue Analysis',
            ha='center', va='top', fontsize=14, color='#666666')

    # Tool boxes - arranged in a flow
    # MouseCam (left)
    draw_box(ax, 0.15, 0.65, 0.22, 0.18, '', C_CAM, alpha=0.15,
             border_color=C_CAM, linewidth=2)
    ax.text(0.15, 0.72, 'MouseCam', ha='center', va='center',
            fontsize=16, fontweight='bold', color=C_CAM)
    ax.text(0.15, 0.66, 'Video Collection', ha='center', va='center',
            fontsize=11, color='#666666')
    ax.text(0.15, 0.60, '8-camera Raspberry Pi\nPyQt5 control GUI',
            ha='center', va='center', fontsize=9, color='#888888')

    # MouseReach (center-left)
    draw_box(ax, 0.42, 0.65, 0.22, 0.18, '', C_REACH, alpha=0.15,
             border_color=C_REACH, linewidth=2)
    ax.text(0.42, 0.72, 'MouseReach', ha='center', va='center',
            fontsize=16, fontweight='bold', color=C_REACH)
    ax.text(0.42, 0.66, 'Behavior Analysis', ha='center', va='center',
            fontsize=11, color='#666666')
    ax.text(0.42, 0.60, '6-step pipeline\n2,770 reaches | 48 features',
            ha='center', va='center', fontsize=9, color='#888888')

    # MouseBrain (center-right)
    draw_box(ax, 0.69, 0.65, 0.22, 0.18, '', C_BRAIN, alpha=0.15,
             border_color=C_BRAIN, linewidth=2)
    ax.text(0.69, 0.72, 'MouseBrain', ha='center', va='center',
            fontsize=16, fontweight='bold', color=C_BRAIN)
    ax.text(0.69, 0.66, 'Tissue Analysis', ha='center', va='center',
            fontsize=11, color='#666666')
    ax.text(0.69, 0.60, '3D cleared + 2D slices\n4 brains | 238 regions',
            ha='center', va='center', fontsize=9, color='#888888')

    # mousedb (right)
    draw_box(ax, 0.90, 0.65, 0.16, 0.18, '', C_DB, alpha=0.15,
             border_color=C_DB, linewidth=2)
    ax.text(0.90, 0.72, 'mousedb', ha='center', va='center',
            fontsize=16, fontweight='bold', color=C_DB)
    ax.text(0.90, 0.66, 'Central DB', ha='center', va='center',
            fontsize=11, color='#666666')
    ax.text(0.90, 0.60, '19 tables\n112k scores',
            ha='center', va='center', fontsize=9, color='#888888')

    # Arrows between tools
    draw_arrow(ax, 0.265, 0.68, 0.31, 0.68, 'video', C_ARROW, linewidth=2)
    draw_arrow(ax, 0.535, 0.68, 0.58, 0.68, '', C_ARROW, linewidth=2)
    ax.text(0.555, 0.71, 'kinematics', ha='center', fontsize=9,
            color=C_ARROW, style='italic')
    draw_arrow(ax, 0.80, 0.68, 0.82, 0.68, '', C_ARROW, linewidth=2)
    ax.text(0.815, 0.71, 'cell counts', ha='center', fontsize=9,
            color=C_ARROW, style='italic')

    # Bottom summary stats - 2 rows of 4 for better spacing
    stats_top = [
        ('117', 'Mice', C_DB),
        ('7', 'Cohorts', C_DB),
        ('112,140', 'Pellet Scores', C_REACH),
        ('2,770', 'Reaches Analyzed', C_REACH),
    ]
    stats_bottom = [
        ('~96,000', 'Cells Detected', C_BRAIN),
        ('238', 'Brain Regions', C_BRAIN),
        ('235', 'Slice Samples', C_SLICE),
        ('133', 'Calibration Runs', C_BRAIN),
    ]
    for row_idx, stats_row in enumerate([stats_top, stats_bottom]):
        y = 0.39 - row_idx * 0.18
        for i, (num, label, color) in enumerate(stats_row):
            x = 0.14 + i * 0.22
            draw_stat_card(ax, x, y, 0.18, 0.13, num, label, color,
                           fontsize_num=18, fontsize_label=9)

    # Bottom text
    ax.text(0.5, 0.10, 'CNT (Control) + ENCR (Enhancer) Projects',
            ha='center', va='center', fontsize=13, color='#888888')
    ax.text(0.5, 0.05, 'Full pipeline: Video Recording -> Pose Estimation -> '
            'Reach Detection -> Kinematics | Brain Clearing -> Registration -> '
            'Cell Detection -> Region Counts',
            ha='center', va='center', fontsize=10, color='#AAAAAA',
            style='italic')

    save_fig(fig, output_dir, '01_connectome_project_overview.png')


def fig_02_data_organization(output_dir):
    """Data organization and code/data separation schematic."""
    fig, ax = plt.subplots(1, 1, figsize=(16, 9))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')

    ax.text(0.5, 0.96, 'Data Architecture: Code vs. Data Separation',
            ha='center', va='top', fontsize=20, fontweight='bold', color=C_TEXT)

    # Three columns: Collection -> Processing -> Outputs
    col_w = 0.28
    cols = [0.17, 0.50, 0.83]

    # Column headers
    headers = ['Raw Data Collection', 'Processing Pipelines', 'Integrated Outputs']
    header_colors = ['#E3F2FD', '#FFF8E1', '#E8F5E9']
    for i, (cx, header, hc) in enumerate(zip(cols, headers, header_colors)):
        draw_box(ax, cx, 0.87, col_w, 0.06, header, hc,
                 fontsize=14, fontweight='bold', border_color='#BDBDBD')

    # Column 1: Raw data
    sources = [
        ('MouseCam\n8-cam video (.mkv)', C_CAM, 0.15),
        ('Microscope\nCleared brains (.ims)', C_BRAIN, 0.15),
        ('ND2 Scanner\n2D Slices (.nd2)', C_SLICE, 0.15),
        ('Manual Entry\nPellet scores, weights', C_DB, 0.15),
    ]
    y_start = 0.75
    y_step = 0.14
    for i, (text, color, alpha) in enumerate(sources):
        draw_box(ax, cols[0], y_start - i * y_step, 0.24, 0.10, text,
                 color, alpha=0.2, border_color=color, fontsize=9)

    # Column 2: Processing
    pipelines = [
        ('MouseReach Pipeline\nDLC -> Segment -> Reach -> Outcome -> Kinematics', C_REACH),
        ('MouseBrain Pipeline\nExtract -> Register -> Detect -> Classify -> Count', C_BRAIN),
        ('SliceAtlas Pipeline\nDetect nuclei -> Classify -> Quantify', C_SLICE),
        ('mousedb Importers\nValidate -> Import -> Audit', C_DB),
    ]
    for i, (text, color) in enumerate(pipelines):
        draw_box(ax, cols[1], y_start - i * y_step, 0.26, 0.10, text,
                 color, alpha=0.2, border_color=color, fontsize=9)

    # Column 3: Outputs
    outputs = [
        ('reach_kinematics.csv\n2,770 reaches, 48 features', C_REACH),
        ('region_counts.csv\n4 brains, 238 regions each', C_BRAIN),
        ('batch_summary.csv\n235 samples quantified', C_SLICE),
        ('connectome.db\n19 tables, 112k+ records', C_DB),
    ]
    for i, (text, color) in enumerate(outputs):
        draw_box(ax, cols[2], y_start - i * y_step, 0.24, 0.10, text,
                 color, alpha=0.2, border_color=color, fontsize=9)

    # Arrows between columns
    for i in range(4):
        y = y_start - i * y_step
        draw_arrow(ax, cols[0] + 0.13, y, cols[1] - 0.14, y, linewidth=1.2)
        draw_arrow(ax, cols[1] + 0.14, y, cols[2] - 0.13, y, linewidth=1.2)

    # Bottom: Code vs Data rule
    rule_y = 0.18
    draw_box(ax, 0.30, rule_y, 0.32, 0.12,
             'CODE Directories\n(Git repos, pip-installable)\nMouseBrain/ | MouseReach/\nmousedb/ | MouseCam/',
             '#E3F2FD', fontsize=9, border_color='#1976D2', linewidth=2)
    draw_box(ax, 0.70, rule_y, 0.32, 0.12,
             'DATA Directories\n(Pipeline outputs, NOT in git)\nMouseBrain_Pipeline/\nMouseReach_Pipeline/',
             '#FFF3E0', fontsize=9, border_color='#E65100', linewidth=2)
    ax.text(0.50, rule_y, 'vs.', ha='center', va='center',
            fontsize=16, fontweight='bold', color='#888888')
    ax.text(0.50, rule_y - 0.10,
            'Rule: New code goes in Tool dirs. Pipeline dirs hold data only -- no exceptions.',
            ha='center', va='center', fontsize=11, color='#888888', style='italic')

    save_fig(fig, output_dir, '02_data_organization.png')


def fig_03_mousebrain_pipeline(output_dir):
    """MouseBrain 6-step pipeline schematic."""
    fig, ax = plt.subplots(1, 1, figsize=(16, 9))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')

    ax.text(0.5, 0.95, 'MouseBrain: 3D Cleared Brain Processing Pipeline',
            ha='center', va='top', fontsize=20, fontweight='bold', color=C_TEXT)

    # 6 pipeline steps
    steps = [
        ('1. Organize', 'Create folder\nstructure', C_INPUT),
        ('2. Extract', 'Extract channels\nAuto-crop', C_PROCESS),
        ('3. Register', 'BrainGlobe\nAllen Atlas 10um', '#BBDEFB'),
        ('4. Detect', 'cellfinder\nball_xy, ball_z,\nthreshold', '#C8E6C9'),
        ('5. Classify', 'CNN classifier\nStarDist model', '#FFF9C4'),
        ('6. Count', '238 brain regions\nL/R hemisphere', C_OUTPUT),
    ]

    step_y = 0.68
    step_w = 0.12
    step_h = 0.18
    x_start = 0.10
    x_step = 0.145

    for i, (title, desc, color) in enumerate(steps):
        x = x_start + i * x_step
        # Step box
        draw_box(ax, x, step_y, step_w, step_h, '', color,
                 border_color=C_BRAIN, linewidth=1.5)
        ax.text(x, step_y + 0.06, title, ha='center', va='center',
                fontsize=11, fontweight='bold', color=C_TEXT)
        ax.text(x, step_y - 0.03, desc, ha='center', va='center',
                fontsize=8, color='#555555')
        # Arrow to next
        if i < 5:
            draw_arrow(ax, x + step_w/2 + 0.005, step_y,
                       x + x_step - step_w/2 - 0.005, step_y,
                       linewidth=2, color=C_BRAIN)

    # Calibration tracker bar
    tracker_y = 0.40
    draw_box(ax, 0.50, tracker_y, 0.82, 0.08,
             'Experiment Tracker: 133 calibration runs logged | 49-column CSV | '
             'Full parameter reproducibility',
             '#E3F2FD', fontsize=10, border_color=C_BRAIN, linewidth=2)

    # Output summary
    results = [
        ('4 Brains Processed', '349, 357, 367, 368 (CNT cohorts)'),
        ('~96,000 Total Cells', 'Range: 5,611 - 34,818 per brain'),
        ('238 Brain Regions', 'Allen Mouse Atlas, bilateral mapping'),
        ('25 eLife Groups', 'Functional circuit organization'),
    ]
    res_y = 0.22
    for i, (title, desc) in enumerate(results):
        x = 0.14 + i * 0.24
        ax.text(x, res_y + 0.03, title, ha='center', va='center',
                fontsize=12, fontweight='bold', color=C_BRAIN)
        ax.text(x, res_y - 0.03, desc, ha='center', va='center',
                fontsize=9, color='#888888')

    save_fig(fig, output_dir, '03_mousebrain_pipeline.png')


def fig_04_brain_region_counts(output_dir, brain_counts):
    """Top 15 brain regions across all brains - grouped bar chart."""
    if not brain_counts:
        print('  [SKIP] fig_04: No brain count data')
        return

    fig, ax = plt.subplots(1, 1, figsize=(16, 9))

    # Merge all brains on region_acronym
    merged = None
    valid_brains = []
    for i, bid in enumerate(BRAIN_IDS):
        if bid in brain_counts:
            df = brain_counts[bid][['region_acronym', 'cell_count']].copy()
            df = df.rename(columns={'cell_count': bid})
            if merged is None:
                merged = df
            else:
                merged = merged.merge(df, on='region_acronym', how='outer')
            valid_brains.append(bid)

    if merged is None or len(valid_brains) == 0:
        print('  [SKIP] fig_04: No valid brain data')
        return

    # Fill NaN with 0, compute mean, sort, take top 15
    merged = merged.fillna(0)
    merged['mean'] = merged[valid_brains].mean(axis=1)
    merged = merged.sort_values('mean', ascending=False).head(15)

    # Plot grouped bar
    x = np.arange(len(merged))
    n_brains = len(valid_brains)
    width = 0.8 / n_brains

    for i, bid in enumerate(valid_brains):
        idx = BRAIN_IDS.index(bid)
        total = brain_counts[bid]['cell_count'].sum()
        label = f'{BRAIN_LABELS[idx]} ({total:,} cells)'
        ax.bar(x + i * width - (n_brains - 1) * width / 2,
               merged[bid].values, width, label=label,
               color=BRAIN_COLORS[idx], edgecolor='white', linewidth=0.5)

    ax.set_xticks(x)
    ax.set_xticklabels(merged['region_acronym'].values, rotation=45, ha='right',
                        fontsize=11)
    ax.set_ylabel('Cell Count')
    ax.set_title('Top 15 Brain Regions by Cell Count Across Processed Brains',
                 pad=12)
    ax.legend(loc='upper right', framealpha=0.9)
    ax.set_xlim(-0.5, len(merged) - 0.5)

    plt.tight_layout(pad=1.5)
    save_fig(fig, output_dir, '04_brain_region_counts.png')


def fig_05_elife_comparison(output_dir, elife_df):
    """eLife functional group comparison: heatmap + bar chart."""
    if elife_df is None:
        print('  [SKIP] fig_05: No eLife comparison data')
        return

    fig = plt.figure(figsize=(16, 9))
    gs = gridspec.GridSpec(1, 2, width_ratios=[1, 1.3], wspace=0.4)

    # Panel A: Heatmap (transposed - groups as rows for readability)
    ax1 = fig.add_subplot(gs[0])

    # Get group names (columns after 'Brain')
    groups = elife_df.columns[1:].tolist()

    # Rows: eLife ref, then our brains
    heatmap_data = elife_df.set_index(elife_df.columns[0])
    heatmap_data = heatmap_data.apply(pd.to_numeric, errors='coerce')

    # Normalize each row to % of total
    row_sums = heatmap_data.sum(axis=1)
    heatmap_pct = heatmap_data.div(row_sums, axis=0) * 100

    import seaborn as sns

    # Transpose so functional groups are rows (readable) and brains are columns
    heatmap_T = heatmap_pct.T
    # Shorten row labels (functional groups)
    short_row_names = [g[:22] for g in heatmap_T.index]
    # Shorten column labels (brain IDs)
    short_col_names = [str(c)[:15] for c in heatmap_T.columns]

    sns.heatmap(heatmap_T, ax=ax1, cmap='YlOrRd', annot=True, fmt='.1f',
                annot_kws={'fontsize': 7},
                xticklabels=short_col_names, yticklabels=short_row_names,
                linewidths=0.5, cbar_kws={'label': '% of Brain Total',
                                           'shrink': 0.8})
    ax1.set_title('A) eLife Region Distribution\n(% of total)', fontsize=14)
    ax1.set_xticklabels(ax1.get_xticklabels(), rotation=45, ha='right', fontsize=9)
    ax1.set_yticklabels(ax1.get_yticklabels(), fontsize=8)

    # Panel B: % difference from eLife reference
    ax2 = fig.add_subplot(gs[1])

    elife_ref = heatmap_data.iloc[0]  # First row is eLife reference
    our_mean = heatmap_data.iloc[1:].mean()  # Mean of our brains

    # Compute % change
    pct_change = ((our_mean - elife_ref) / elife_ref.replace(0, np.nan)) * 100
    pct_change = pct_change.dropna().sort_values()

    colors = ['#E74C3C' if v < 0 else '#27AE60' for v in pct_change.values]
    short_labels = [g[:22] for g in pct_change.index]

    ax2.barh(range(len(pct_change)), pct_change.values, color=colors,
             edgecolor='white', linewidth=0.5, height=0.7)
    ax2.set_yticks(range(len(pct_change)))
    ax2.set_yticklabels(short_labels, fontsize=8)
    ax2.set_xlabel('% Change from eLife Reference')
    ax2.set_title('B) Our Brains vs. eLife L1 Cervical\n(Wang et al. 2022)', fontsize=14)
    ax2.axvline(x=0, color='black', linewidth=0.8)
    ax2.set_xlim(min(pct_change.values.min() * 1.1, -100),
                 max(pct_change.values.max() * 1.1, 100))

    plt.tight_layout(pad=1.5)
    save_fig(fig, output_dir, '05_elife_comparison.png')


def fig_06_hemisphere_laterality(output_dir, lat_df):
    """Hemisphere laterality butterfly chart."""
    if lat_df is None:
        print('  [SKIP] fig_06: No laterality data')
        return

    fig, ax = plt.subplots(1, 1, figsize=(16, 9))

    # Sort by mean_total descending
    lat_df = lat_df.sort_values('mean_total', ascending=True).copy()

    groups = lat_df['elife_group'].values
    left = lat_df['mean_left'].values
    right = lat_df['mean_right'].values
    sig = lat_df['LR_sig'].values if 'LR_sig' in lat_df.columns else [''] * len(lat_df)

    y_pos = np.arange(len(groups))

    # Left hemisphere (negative direction)
    ax.barh(y_pos, -left, color='#2196F3', edgecolor='white', linewidth=0.5,
            label='Left Hemisphere')
    # Right hemisphere (positive direction)
    ax.barh(y_pos, right, color='#F44336', edgecolor='white', linewidth=0.5,
            label='Right Hemisphere')

    # Add individual brain dots
    for brain_idx, bid in enumerate(['349', '357', '367']):
        l_col = f'{bid}_left'
        r_col = f'{bid}_right'
        if l_col in lat_df.columns and r_col in lat_df.columns:
            ax.scatter(-lat_df[l_col].values, y_pos, s=15, color='#1565C0',
                       alpha=0.5, zorder=3, marker='o')
            ax.scatter(lat_df[r_col].values, y_pos, s=15, color='#C62828',
                       alpha=0.5, zorder=3, marker='o')

    # Significance markers
    for i, s in enumerate(sig):
        if s and s not in ('ns', ''):
            ax.text(max(right) * 1.05, i, s, va='center', fontsize=9,
                    color='#333333')

    ax.set_yticks(y_pos)
    ax.set_yticklabels(groups, fontsize=8)
    ax.axvline(x=0, color='black', linewidth=0.8)
    ax.set_xlabel('Mean Cell Count')
    ax.set_title('Hemisphere Laterality: Left vs Right Cell Counts by eLife Region\n'
                 '(n=3 brains, small dots = individual brains)',
                 fontsize=15, pad=20)
    ax.legend(loc='lower right', fontsize=11)

    # Labels for LEFT / RIGHT above plot area
    xlim = ax.get_xlim()
    ax.text(xlim[0] * 0.4, len(groups) + 1.0, 'LEFT', ha='center',
            fontsize=14, fontweight='bold', color='#2196F3')
    ax.text(xlim[1] * 0.4, len(groups) + 1.0, 'RIGHT', ha='center',
            fontsize=14, fontweight='bold', color='#F44336')
    ax.set_ylim(-0.5, len(groups) + 1.5)

    plt.tight_layout(pad=1.5)
    save_fig(fig, output_dir, '06_hemisphere_laterality.png')


def fig_07_slice_quantification(output_dir, batch_df):
    """2D slice enhancer-positive cell fraction."""
    if batch_df is None:
        print('  [SKIP] fig_07: No 2D batch data')
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 9),
                                    gridspec_kw={'width_ratios': [1.5, 1]})

    # Parse subject from sample name (e.g., E01_01_S1_R3 -> E01_01)
    batch_df = batch_df.copy()
    batch_df['subject'] = batch_df['sample'].str.extract(r'^(E\d+_\d+)')
    batch_df['slice_info'] = batch_df['sample'].str.extract(r'(S\d+_.+)$')

    # Panel A: Fraction positive by subject
    subjects = sorted(batch_df['subject'].dropna().unique())
    subject_means = []
    subject_sems = []
    subject_counts = []
    for subj in subjects:
        vals = batch_df[batch_df['subject'] == subj]['fraction_positive']
        subject_means.append(vals.mean())
        subject_sems.append(vals.std() / np.sqrt(len(vals)) if len(vals) > 1 else 0)
        subject_counts.append(len(vals))

    colors_subj = plt.cm.Set2(np.linspace(0, 1, len(subjects)))
    bars = ax1.bar(range(len(subjects)), subject_means, yerr=subject_sems,
                   color=colors_subj, edgecolor='white', linewidth=0.8,
                   capsize=4, error_kw={'linewidth': 1.5})

    # Overlay individual points
    for i, subj in enumerate(subjects):
        vals = batch_df[batch_df['subject'] == subj]['fraction_positive']
        jitter = np.random.normal(0, 0.08, size=len(vals))
        ax1.scatter(np.full(len(vals), i) + jitter, vals,
                    color=colors_subj[i], edgecolor='#333333', linewidth=0.5,
                    s=25, alpha=0.7, zorder=3)

    ax1.set_xticks(range(len(subjects)))
    ax1.set_xticklabels(subjects, rotation=45, ha='right', fontsize=10)
    ax1.set_ylabel('Fraction Positive (%)')
    ax1.set_title('A) Enhancer-Positive Cell Fraction by Subject', fontsize=14)
    ax1.set_ylim(0, 105)

    # Panel B: Distribution of nuclei counts
    ax2.hist(batch_df['nuclei'], bins=25, color=C_SLICE, alpha=0.7,
             edgecolor='white', linewidth=0.8)
    ax2.axvline(batch_df['nuclei'].median(), color='black', linestyle='--',
                linewidth=1.5, label=f'Median: {batch_df["nuclei"].median():.0f}')
    ax2.set_xlabel('Nuclei Detected per Sample')
    ax2.set_ylabel('Count')
    ax2.set_title('B) Detection Throughput', fontsize=14)
    ax2.legend()

    # Summary annotation
    fig.text(0.5, 0.02,
             f'N={len(batch_df)} samples | GMM-based classification | '
             f'Mean processing: {batch_df["duration_s"].mean():.1f}s/sample | '
             f'Overall fraction positive: {batch_df["fraction_positive"].mean():.1f}%',
             ha='center', fontsize=11, color='#666666')

    plt.tight_layout(rect=[0, 0.06, 1, 0.98], pad=1.0)
    save_fig(fig, output_dir, '07_slice_quantification.png')


def fig_08_mousereach_pipeline(output_dir):
    """MouseReach 6-step pipeline schematic."""
    fig, ax = plt.subplots(1, 1, figsize=(16, 9))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')

    ax.text(0.5, 0.95, 'MouseReach: Automated Reach Analysis Pipeline',
            ha='center', va='top', fontsize=20, fontweight='bold', color=C_TEXT)

    # 6 pipeline steps
    steps = [
        ('1. Video Prep', '8-cam collage\n-> single animal', C_INPUT),
        ('2. DLC Tracking', '18 bodyparts\nResNet50', C_PROCESS),
        ('3. Segmentation', '21 trials\nper video', '#FFECB3'),
        ('4. Reach Detection', 'State machine\nIDLE->REACHING', '#C8E6C9'),
        ('5. Pellet Outcomes', 'Retrieved\nDisplaced\nUntouched', '#FFCDD2'),
        ('6. Kinematics', '48 features\nper reach', C_OUTPUT),
    ]

    step_y = 0.67
    step_w = 0.12
    step_h = 0.20
    x_start = 0.10
    x_step = 0.145

    for i, (title, desc, color) in enumerate(steps):
        x = x_start + i * x_step
        draw_box(ax, x, step_y, step_w, step_h, '', color,
                 border_color=C_REACH, linewidth=1.5)
        ax.text(x, step_y + 0.07, title, ha='center', va='center',
                fontsize=11, fontweight='bold', color=C_TEXT)
        ax.text(x, step_y - 0.02, desc, ha='center', va='center',
                fontsize=8, color='#555555')
        if i < 5:
            draw_arrow(ax, x + step_w/2 + 0.005, step_y,
                       x + x_step - step_w/2 - 0.005, step_y,
                       linewidth=2, color=C_REACH)

    # Validation bar
    draw_box(ax, 0.50, 0.42, 0.70, 0.07,
             'Human Ground Truth Validation: Reach boundaries + Pellet outcomes verified per-video',
             '#FFF3E0', fontsize=10, border_color=C_REACH, linewidth=2)

    # Output stats
    stats = [
        ('19 Videos', 'Processed end-to-end'),
        ('2,770 Reaches', 'Detected and classified'),
        ('48 Features', 'Per reach (spatial, temporal, trajectory)'),
        ('3 Outcome Types', 'Retrieved | Displaced | Untouched'),
    ]
    for i, (title, desc) in enumerate(stats):
        x = 0.14 + i * 0.24
        ax.text(x, 0.26, title, ha='center', va='center',
                fontsize=13, fontweight='bold', color=C_REACH)
        ax.text(x, 0.21, desc, ha='center', va='center',
                fontsize=9, color='#888888')

    save_fig(fig, output_dir, '08_mousereach_pipeline.png')


def fig_09_reach_outcomes(output_dir, kin_df, summary_df):
    """Reach outcome distribution: donut + stacked bar per video."""
    if kin_df is None:
        print('  [SKIP] fig_09: No reach kinematics data')
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 9),
                                    gridspec_kw={'width_ratios': [1, 1.5]})

    # Panel A: Donut chart
    outcome_counts = kin_df['outcome'].value_counts()
    labels = []
    sizes = []
    colors = []
    for outcome in ['displaced_sa', 'untouched', 'retrieved',
                    'displaced_outside', 'uncertain_outside']:
        if outcome in outcome_counts.index:
            labels.append(outcome.replace('_', ' ').title())
            sizes.append(outcome_counts[outcome])
            colors.append(OUTCOME_COLORS.get(outcome, '#BDC3C7'))

    wedges, texts, autotexts = ax1.pie(
        sizes, labels=None, colors=colors, autopct='%1.1f%%',
        startangle=90, pctdistance=0.82, textprops={'fontsize': 9})
    # Make it a donut
    centre = plt.Circle((0, 0), 0.55, fc='white')
    ax1.add_artist(centre)
    ax1.text(0, 0, f'{sum(sizes):,}\nreaches', ha='center', va='center',
             fontsize=16, fontweight='bold', color=C_TEXT)
    ax1.set_title('A) Overall Outcome Distribution', fontsize=14, pad=10)
    # External legend instead of inline labels (avoids overlap)
    ax1.legend(wedges, [f'{l} ({s:,})' for l, s in zip(labels, sizes)],
               loc='lower center', fontsize=9, ncol=2,
               bbox_to_anchor=(0.5, -0.08), frameon=False)

    # Panel B: Per-video stacked bar
    if summary_df is not None:
        summary = summary_df.copy()
        # Sort by success rate
        summary['success_pct'] = summary['success_rate'].str.rstrip('%').astype(float)
        summary = summary.sort_values('success_pct', ascending=True)

        # Shorten video names
        summary['short_name'] = summary['video'].str.extract(
            r'(\d{8}_\w+_P\d)')[0].fillna(summary['video'])

        y_pos = np.arange(len(summary))
        outcome_cols = ['retrieved', 'displaced_sa', 'displaced_outside', 'untouched']
        available_cols = [c for c in outcome_cols if c in summary.columns]

        left = np.zeros(len(summary))
        for col in available_cols:
            vals = summary[col].fillna(0).values
            color = OUTCOME_COLORS.get(col, '#BDC3C7')
            label = col.replace('_', ' ').title()
            ax2.barh(y_pos, vals, left=left, color=color, label=label,
                     edgecolor='white', linewidth=0.3)
            left += vals

        ax2.set_yticks(y_pos)
        ax2.set_yticklabels(summary['short_name'].values, fontsize=7)
        ax2.set_xlabel('Number of Reaches')
        ax2.set_title('B) Per-Video Outcome Breakdown\n(sorted by success rate)',
                       fontsize=14, pad=10)
        ax2.legend(loc='lower right', fontsize=9, framealpha=0.9)

    plt.tight_layout(pad=1.5)
    save_fig(fig, output_dir, '09_reach_outcomes.png')


def fig_10_kinematic_comparison(output_dir, kin_df):
    """Kinematic comparison: retrieved vs non-retrieved."""
    if kin_df is None:
        print('  [SKIP] fig_10: No reach kinematics data')
        return

    fig = plt.figure(figsize=(16, 9))
    gs = gridspec.GridSpec(2, 3, hspace=0.45, wspace=0.35,
                           top=0.90, bottom=0.08, left=0.07, right=0.97)
    fig.suptitle('Reach Kinematics: Retrieved vs. Non-Retrieved',
                 fontsize=18, fontweight='bold', y=0.96)

    # Classify
    df = kin_df.copy()
    df['group'] = df['outcome'].apply(
        lambda x: 'Retrieved' if x == 'retrieved' else 'Non-Retrieved')

    # Compute absolute extent (raw values are signed displacements)
    if 'extent_pixels' in df.columns:
        df['reach_extent_px'] = df['extent_pixels'].abs()
    if 'extent_mm' in df.columns:
        df['reach_extent_mm'] = df['extent_mm'].abs()
        # Remove extreme outliers (>99th percentile) for display
        p99 = df['reach_extent_mm'].quantile(0.99)
        df.loc[df['reach_extent_mm'] > p99, 'reach_extent_mm'] = np.nan

    # Features to compare (using cleaned columns)
    features = [
        ('duration_ms', 'Duration (ms)', fig.add_subplot(gs[0, 0])),
        ('duration_frames', 'Duration (frames)', fig.add_subplot(gs[0, 1])),
        ('reach_extent_px', 'Reach Extent (pixels)', fig.add_subplot(gs[0, 2])),
        ('reach_extent_mm', 'Reach Extent (mm)', fig.add_subplot(gs[1, 0])),
    ]

    import seaborn as sns
    palette = {'Retrieved': '#27AE60', 'Non-Retrieved': '#95A5A6'}

    for col, title, ax in features:
        if col not in df.columns:
            ax.text(0.5, 0.5, f'{col}\nnot available', ha='center',
                    va='center', transform=ax.transAxes)
            ax.set_title(title)
            continue

        data = df[[col, 'group']].dropna()
        if len(data) == 0:
            continue

        # Clip display to 95th percentile to prevent outlier axis stretching
        p95 = data[col].quantile(0.95)
        display_data = data[data[col] <= p95 * 1.3].copy()

        sns.boxplot(x='group', y=col, data=display_data, ax=ax,
                    hue='group', palette=palette, legend=False,
                    showfliers=False, width=0.5)
        sns.stripplot(x='group', y=col, data=display_data, ax=ax,
                      hue='group', palette=palette, legend=False,
                      alpha=0.1, size=1.5, jitter=True)
        ax.set_title(title, fontsize=13, pad=8)
        ax.set_xlabel('')
        ax.set_ylabel(col.replace('_', ' '), fontsize=10)

        # Set y-axis to focus on IQR range
        q75 = data[col].quantile(0.75)
        ax.set_ylim(bottom=0, top=p95 * 1.4)

        # Add median annotations offset to avoid overlap with box
        for i, grp in enumerate(['Non-Retrieved', 'Retrieved']):
            vals = data[data['group'] == grp][col]
            if len(vals) > 0:
                med = vals.median()
                ax.annotate(f'{med:.1f}', xy=(i, min(med, p95 * 1.3)),
                            xytext=(8, 8), textcoords='offset points',
                            fontsize=9, color='#333333', fontweight='bold',
                            arrowprops=dict(arrowstyle='-', color='#999999',
                                            lw=0.5))

    # Panel E: Outcome distribution
    ax_pie = fig.add_subplot(gs[1, 1])
    counts = df['group'].value_counts()
    wedges, texts, autotexts = ax_pie.pie(
        counts.values,
        labels=[f'{k}\n(n={v:,})' for k, v in counts.items()],
        colors=['#95A5A6', '#27AE60'],
        autopct='%1.1f%%', startangle=90,
        textprops={'fontsize': 10},
        pctdistance=0.75)
    for t in autotexts:
        t.set_fontsize(9)
    ax_pie.set_title('Outcome Distribution', fontsize=13, pad=8)

    # Panel F: Summary stats
    ax_text = fig.add_subplot(gs[1, 2])
    ax_text.axis('off')
    n_ret = len(df[df['group'] == 'Retrieved'])
    n_nonret = len(df[df['group'] == 'Non-Retrieved'])
    n_videos = df['video'].nunique()

    # Compute actual medians for summary
    med_dur_ret = df.loc[df['group'] == 'Retrieved', 'duration_ms'].median()
    med_dur_non = df.loc[df['group'] == 'Non-Retrieved', 'duration_ms'].median()
    med_ext_ret = df.loc[df['group'] == 'Retrieved', 'reach_extent_px'].median() \
        if 'reach_extent_px' in df.columns else 0
    med_ext_non = df.loc[df['group'] == 'Non-Retrieved', 'reach_extent_px'].median() \
        if 'reach_extent_px' in df.columns else 0

    summary_text = (
        f'KINEMATIC SUMMARY\n'
        f'{"=" * 28}\n\n'
        f'Total Reaches: {len(df):,}\n'
        f'Videos: {n_videos}\n\n'
        f'Retrieved: {n_ret:,} ({n_ret/len(df)*100:.1f}%)\n'
        f'Non-Retrieved: {n_nonret:,} ({n_nonret/len(df)*100:.1f}%)\n\n'
        f'Median Duration:\n'
        f'  Ret: {med_dur_ret:.0f} ms\n'
        f'  Non: {med_dur_non:.0f} ms\n\n'
        f'Median Extent:\n'
        f'  Ret: {med_ext_ret:.1f} px\n'
        f'  Non: {med_ext_non:.1f} px'
    )
    ax_text.text(0.05, 0.95, summary_text, transform=ax_text.transAxes,
                 fontsize=10, va='top', fontfamily='monospace',
                 bbox=dict(boxstyle='round', facecolor='#F5F5F5', alpha=0.8))

    save_fig(fig, output_dir, '10_kinematic_comparison.png')


def fig_11_behavior_by_phase(output_dir, db_path):
    """Behavioral performance across injury phases from database."""
    pellet_df = query_pellet_phases(db_path)
    if pellet_df is None or len(pellet_df) == 0:
        print('  [SKIP] fig_11: No pellet data from database')
        return

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(16, 9))
    fig.suptitle('Behavioral Performance Across Experimental Phases\n(Manual Pellet Scoring)',
                 fontsize=18, fontweight='bold', y=1.0)

    df = pellet_df.copy()

    # Map test_phase to major phases
    def map_phase(tp):
        tp_lower = tp.lower().replace('-', '_').replace(' ', '_')
        if 'pre_injury' in tp_lower:
            return 'Pre-Injury'
        elif 'post_injury_test_1' in tp_lower or 'post_injury test 1' in tp.lower():
            return 'Post-Injury 1'
        elif any(f'post_injury_test_{i}' in tp_lower or f'post_injury test {i}' in tp.lower()
                 for i in [2, 3, 4]):
            return 'Post-Injury 2-4'
        elif 'rehab' in tp_lower:
            return 'Rehab'
        elif 'training' in tp_lower:
            return 'Training'
        return 'Other'

    df['phase'] = df['test_phase'].apply(map_phase)
    df = df[df['phase'].isin(['Pre-Injury', 'Post-Injury 1', 'Post-Injury 2-4', 'Rehab'])]

    if len(df) == 0:
        print('  [SKIP] fig_11: No data after phase classification')
        plt.close(fig)
        return

    # Score: 0=miss, 1=displaced, 2=retrieved
    phase_order = ['Pre-Injury', 'Post-Injury 1', 'Post-Injury 2-4', 'Rehab']
    df['retrieved'] = (df['score'] == 2).astype(int)
    df['contacted'] = (df['score'] >= 1).astype(int)

    subj_phase = df.groupby(['subject_id', 'phase']).agg(
        total=('score', 'count'),
        retrieved=('retrieved', 'sum'),
        contacted=('contacted', 'sum')
    ).reset_index()
    subj_phase['retrieved_pct'] = subj_phase['retrieved'] / subj_phase['total'] * 100
    subj_phase['contacted_pct'] = subj_phase['contacted'] / subj_phase['total'] * 100

    # Panel A: Retrieved %
    phase_colors = ['#3498DB', '#E74C3C', '#E67E22', '#2ECC71']
    for i, phase in enumerate(phase_order):
        data = subj_phase[subj_phase['phase'] == phase]['retrieved_pct']
        if len(data) > 0:
            ax1.bar(i, data.mean(), yerr=data.std()/np.sqrt(len(data)),
                    color=phase_colors[i], alpha=0.7, capsize=5,
                    edgecolor='white', linewidth=1)
            jitter = np.random.normal(0, 0.1, size=len(data))
            ax1.scatter(np.full(len(data), i) + jitter, data,
                        color=phase_colors[i], edgecolor='#333333',
                        linewidth=0.5, s=30, alpha=0.6, zorder=3)

    ax1.set_xticks(range(len(phase_order)))
    ax1.set_xticklabels(phase_order, rotation=20, ha='right', fontsize=10)
    ax1.set_ylabel('% Pellets Retrieved')
    ax1.set_title('A) Retrieved Rate by Phase', fontsize=14, pad=10)
    ax1.set_ylim(0, max(subj_phase['retrieved_pct'].max() * 1.2, 30))

    # Panel B: Contacted %
    for i, phase in enumerate(phase_order):
        data = subj_phase[subj_phase['phase'] == phase]['contacted_pct']
        if len(data) > 0:
            ax2.bar(i, data.mean(), yerr=data.std()/np.sqrt(len(data)),
                    color=phase_colors[i], alpha=0.7, capsize=5,
                    edgecolor='white', linewidth=1)
            jitter = np.random.normal(0, 0.1, size=len(data))
            ax2.scatter(np.full(len(data), i) + jitter, data,
                        color=phase_colors[i], edgecolor='#333333',
                        linewidth=0.5, s=30, alpha=0.6, zorder=3)

    ax2.set_xticks(range(len(phase_order)))
    ax2.set_xticklabels(phase_order, rotation=20, ha='right', fontsize=10)
    ax2.set_ylabel('% Pellets Contacted')
    ax2.set_title('B) Contacted Rate by Phase', fontsize=14, pad=10)
    ax2.set_ylim(0, max(subj_phase['contacted_pct'].max() * 1.2, 60))

    # Summary
    n_subjects = df['subject_id'].nunique()
    n_scores = len(df)
    fig.text(0.5, 0.02,
             f'N={n_subjects} subjects | {n_scores:,} pellet scores | '
             f'Cohorts: CNT_01, CNT_02, CNT_03 | Error bars = SEM',
             ha='center', fontsize=11, color='#666666')

    plt.tight_layout(rect=[0, 0.05, 1, 0.93], pad=1.0)
    save_fig(fig, output_dir, '11_behavior_by_phase.png')


def fig_12_mousecam_system(output_dir):
    """MouseCam system schematic."""
    fig, ax = plt.subplots(1, 1, figsize=(16, 9))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')

    ax.text(0.5, 0.95, 'MouseCam: Multi-Camera Video Collection System',
            ha='center', va='top', fontsize=20, fontweight='bold', color=C_TEXT)

    # Hardware section
    draw_box(ax, 0.20, 0.65, 0.30, 0.30, '', C_CAM, alpha=0.1,
             border_color=C_CAM, linewidth=2)
    ax.text(0.20, 0.78, 'Hardware Setup', ha='center', va='center',
            fontsize=14, fontweight='bold', color=C_CAM)

    # Camera grid (2x4)
    cam_x_start = 0.10
    cam_y_start = 0.68
    for row in range(2):
        for col in range(4):
            cx = cam_x_start + col * 0.055
            cy = cam_y_start - row * 0.055
            draw_box(ax, cx, cy, 0.045, 0.04, f'Cam\n{row*4+col+1}',
                     '#CE93D8', fontsize=6, border_color=C_CAM)
    ax.text(0.20, 0.56, '8 Raspberry Pi Cameras\nSimultaneous recording',
            ha='center', va='center', fontsize=10, color='#666666')

    # Control section
    draw_box(ax, 0.55, 0.65, 0.22, 0.30, '', '#F3E5F5',
             border_color=C_CAM, linewidth=1.5)
    ax.text(0.55, 0.77, 'PyQt5 Control GUI', ha='center', va='center',
            fontsize=14, fontweight='bold', color=C_CAM)
    ax.text(0.55, 0.68, 'Preview all cameras\nStart/stop recording\nAutomatic file transfer\nNetwork discovery',
            ha='center', va='center', fontsize=9, color='#555555')
    ax.text(0.55, 0.56, 'Windows Desktop App', ha='center', va='center',
            fontsize=10, color='#888888')

    # Output section
    draw_box(ax, 0.85, 0.65, 0.20, 0.30, '', C_REACH, alpha=0.1,
             border_color=C_REACH, linewidth=1.5)
    ax.text(0.85, 0.77, 'Output', ha='center', va='center',
            fontsize=14, fontweight='bold', color=C_REACH)
    ax.text(0.85, 0.66, '8-camera collage\nMKV video files\n\nTransferred to\nDLC_Queue/ for\nMouseReach pipeline',
            ha='center', va='center', fontsize=9, color='#555555')

    # Arrows
    draw_arrow(ax, 0.36, 0.65, 0.44, 0.65, linewidth=2, color=C_CAM)
    draw_arrow(ax, 0.66, 0.65, 0.75, 0.65, linewidth=2, color=C_REACH)

    # Bottom: workflow
    flow_y = 0.30
    flow_items = [
        ('Record', 'Start session\nin GUI'),
        ('Capture', '8 cameras\nsimultaneously'),
        ('Transfer', 'Auto-copy to\nlab storage'),
        ('Queue', 'Files placed in\nDLC_Queue/'),
        ('Process', 'MouseReach\npipeline begins'),
    ]
    for i, (title, desc) in enumerate(flow_items):
        x = 0.12 + i * 0.19
        draw_box(ax, x, flow_y, 0.14, 0.12, '', '#F3E5F5',
                 border_color=C_CAM, linewidth=1)
        ax.text(x, flow_y + 0.03, title, ha='center', va='center',
                fontsize=11, fontweight='bold', color=C_CAM)
        ax.text(x, flow_y - 0.02, desc, ha='center', va='center',
                fontsize=8, color='#666666')
        if i < 4:
            draw_arrow(ax, x + 0.08, flow_y, x + 0.11, flow_y,
                       linewidth=1.5, color=C_CAM)

    save_fig(fig, output_dir, '12_mousecam_system.png')


def fig_13_database_schema(output_dir, db_stats):
    """Database schema overview."""
    fig, ax = plt.subplots(1, 1, figsize=(16, 9))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')

    ax.text(0.5, 0.96, 'connectome.db: Central Database Schema',
            ha='center', va='top', fontsize=20, fontweight='bold', color=C_TEXT)

    # Central DB box
    draw_box(ax, 0.5, 0.82, 0.22, 0.10,
             'connectome.db\n(SQLite)', C_DB, alpha=0.3,
             border_color=C_DB, linewidth=3, fontsize=14, fontweight='bold')

    # Table clusters - positioned to avoid overlap
    clusters = [
        # (center_x, center_y, title, tables, color, arrow_target_y)
        (0.17, 0.55, 'Behavioral Data', [
            ('pellet_scores', db_stats.get('pellet_scores', '?')),
            ('reach_data', db_stats.get('reach_data', '?')),
            ('weights', db_stats.get('weights', '?')),
            ('ladder_entries', db_stats.get('ladder_entries', '?')),
        ], C_REACH, 0.77),
        (0.50, 0.50, 'Subject Management', [
            ('subjects', db_stats.get('subjects', '?')),
            ('cohorts', db_stats.get('cohorts', '?')),
            ('surgeries', db_stats.get('surgeries', '?')),
            ('protocols', db_stats.get('protocols', '?')),
        ], C_DB, 0.77),
        (0.83, 0.55, 'Tissue / Imaging', [
            ('brain_samples', db_stats.get('brain_samples', '?')),
            ('calibration_runs', db_stats.get('calibration_runs', '?')),
            ('region_counts', db_stats.get('region_counts', '?')),
            ('detected_cells', db_stats.get('detected_cells', '?')),
        ], C_BRAIN, 0.77),
        (0.17, 0.22, 'Pipeline / Audit', [
            ('pipeline_data', db_stats.get('pipeline_data', '?')),
            ('audit_log', db_stats.get('audit_log', '?')),
            ('archived_summaries', db_stats.get('archived_summaries', '?')),
        ], '#9E9E9E', 0.77),
        (0.83, 0.22, 'Protocol System', [
            ('protocol_phases', db_stats.get('protocol_phases', '?')),
            ('tray_types', db_stats.get('tray_types', '?')),
            ('session_exceptions', db_stats.get('session_exceptions', '?')),
        ], '#9E9E9E', 0.77),
    ]

    for cx, cy, title, tables, color, arrow_ty in clusters:
        # Cluster background
        row_h = 0.042
        cluster_h = 0.05 + len(tables) * row_h
        draw_box(ax, cx, cy, 0.26, cluster_h, '', color, alpha=0.08,
                 border_color=color, linewidth=1.5)
        ax.text(cx, cy + cluster_h/2 - 0.02, title, ha='center', va='center',
                fontsize=12, fontweight='bold', color=color)

        for i, (tname, tcount) in enumerate(tables):
            ty = cy + cluster_h/2 - 0.065 - i * row_h
            count_str = f'{tcount:,}' if isinstance(tcount, int) else str(tcount)
            ax.text(cx - 0.10, ty, tname, ha='left', va='center',
                    fontsize=9, color=C_TEXT, fontfamily='monospace')
            ax.text(cx + 0.10, ty, count_str, ha='right', va='center',
                    fontsize=9, color='#888888')

        # Line (not arrow) from cluster to central DB
        ax.plot([cx, 0.5], [cy + cluster_h/2, arrow_ty],
                color='#BDBDBD', linewidth=0.8, linestyle='-', alpha=0.5,
                zorder=0)

    # Key features
    ax.text(0.5, 0.06,
            'Features: Foreign key integrity | CHECK constraints | Audit logging | '
            'Automatic DPI calculation | Cascading deletes',
            ha='center', va='center', fontsize=10, color='#888888')

    save_fig(fig, output_dir, '13_database_schema.png')


def fig_14_project_scale(output_dir, db_stats, brain_counts, kin_df, batch_df, cal_df):
    """Project scale infographic with big-number stat cards."""
    fig, ax = plt.subplots(1, 1, figsize=(16, 9))
    ax.set_xlim(0, 1)
    ax.set_ylim(0, 1)
    ax.axis('off')

    ax.text(0.5, 0.95, 'Connectome Project: By the Numbers',
            ha='center', va='top', fontsize=24, fontweight='bold', color=C_TEXT)

    # Compute stats
    n_mice = db_stats.get('subjects', 117)
    n_cohorts = db_stats.get('cohorts', 7)
    n_pellets = db_stats.get('pellet_scores', 112140)
    n_cells = sum(bc['cell_count'].sum() for bc in brain_counts.values()) if brain_counts else 96000
    n_reaches = len(kin_df) if kin_df is not None else 2770
    n_regions = 238
    n_slices = len(batch_df) if batch_df is not None else 235
    n_cal = len(cal_df) if cal_df is not None else 133

    cards = [
        (f'{n_mice:,}', 'Mice Tracked', C_DB),
        (f'{n_cohorts}', 'Cohorts', C_DB),
        (f'{n_pellets:,}', 'Pellet Scores', C_REACH),
        (f'{n_reaches:,}', 'Reaches Analyzed', C_REACH),
        (f'{n_cells:,}', 'Cells Detected', C_BRAIN),
        (f'{n_regions}', 'Brain Regions', C_BRAIN),
        (f'{n_slices}', 'Slice Samples', C_SLICE),
        (f'{n_cal}', 'Calibration Runs', C_BRAIN),
    ]

    # 2x4 grid
    for i, (number, label, color) in enumerate(cards):
        row = i // 4
        col = i % 4
        x = 0.14 + col * 0.22
        y = 0.62 - row * 0.30
        draw_stat_card(ax, x, y, 0.18, 0.20, number, label, color,
                       fontsize_num=26, fontsize_label=11)

    # Bottom context
    ax.text(0.5, 0.15,
            'CNT (Control, 3 cohorts) + ENCR (Enhancer, injury model)',
            ha='center', va='center', fontsize=13, color='#888888')
    ax.text(0.5, 0.10,
            'All data tracked in connectome.db with full audit trail',
            ha='center', va='center', fontsize=11, color='#AAAAAA')

    save_fig(fig, output_dir, '14_project_scale.png')


def fig_15_processing_progress(output_dir, cal_df):
    """Processing timeline and milestones."""
    fig, ax = plt.subplots(1, 1, figsize=(16, 9))

    if cal_df is not None and 'created_at' in cal_df.columns:
        # Parse timestamps
        cal = cal_df.copy()
        cal['date'] = pd.to_datetime(cal['created_at'], errors='coerce')
        cal = cal.dropna(subset=['date'])
        cal['date_only'] = cal['date'].dt.date

        # Cumulative runs over time
        daily_counts = cal.groupby('date_only').size().reset_index(name='count')
        daily_counts['date_only'] = pd.to_datetime(daily_counts['date_only'])
        daily_counts = daily_counts.sort_values('date_only')
        daily_counts['cumulative'] = daily_counts['count'].cumsum()

        ax.fill_between(daily_counts['date_only'], daily_counts['cumulative'],
                        alpha=0.3, color=C_BRAIN)
        ax.plot(daily_counts['date_only'], daily_counts['cumulative'],
                color=C_BRAIN, linewidth=2.5, marker='o', markersize=4)
        ax.set_xlabel('Date')
        ax.set_ylabel('Cumulative Calibration Runs')
        ax.set_title('Processing Progress: Calibration Run History',
                     fontsize=18, fontweight='bold')

        # Annotate milestones
        if len(daily_counts) > 0:
            last = daily_counts.iloc[-1]
            ax.annotate(f'{int(last["cumulative"])} total runs',
                        xy=(last['date_only'], last['cumulative']),
                        xytext=(30, 20), textcoords='offset points',
                        fontsize=12, fontweight='bold', color=C_BRAIN,
                        arrowprops=dict(arrowstyle='->', color=C_BRAIN))

        # Brain processing milestones (from calibration runs)
        if 'brain' in cal.columns:
            brain_firsts = cal.groupby('brain')['date'].min().sort_values()
            milestone_brains = []
            for brain, first_date in brain_firsts.items():
                if isinstance(brain, str) and brain.startswith(('349', '357', '367', '368')):
                    short_name = brain[:3]
                    # Deduplicate by brain number
                    if short_name not in [m[0] for m in milestone_brains]:
                        cum_at_date = daily_counts[daily_counts['date_only'] <= pd.Timestamp(first_date)]['cumulative']
                        if len(cum_at_date) > 0:
                            milestone_brains.append((short_name, first_date, cum_at_date.iloc[-1]))

            # Stagger annotation y-offsets to avoid overlap
            y_max = daily_counts['cumulative'].max()
            for idx, (short_name, first_date, y_val) in enumerate(milestone_brains):
                y_offset = 25 + idx * 18  # Stagger vertically
                ax.axvline(first_date, color=BRAIN_COLORS[idx % len(BRAIN_COLORS)],
                           linestyle='--', linewidth=1.0, alpha=0.5)
                ax.annotate(f'Brain {short_name}',
                            xy=(first_date, y_val),
                            xytext=(-15, y_offset), textcoords='offset points',
                            fontsize=9, fontweight='bold',
                            color=BRAIN_COLORS[idx % len(BRAIN_COLORS)],
                            arrowprops=dict(arrowstyle='->', lw=0.8,
                                            color=BRAIN_COLORS[idx % len(BRAIN_COLORS)]),
                            ha='center')

        fig.autofmt_xdate(rotation=30, ha='right')
    else:
        # Fallback: simple milestone chart
        ax.axis('off')
        ax.text(0.5, 0.5, 'Processing Progress\n(calibration_runs.csv not available)',
                ha='center', va='center', fontsize=16, color='#888888')

    plt.tight_layout(pad=1.5)
    save_fig(fig, output_dir, '15_processing_progress.png')


# =============================================================================
# SAVE AND MAIN
# =============================================================================

def save_fig(fig, output_dir, filename):
    """Save figure to output directory."""
    path = output_dir / filename
    fig.savefig(str(path), dpi=300, bbox_inches='tight',
                facecolor='white', edgecolor='none')
    plt.close(fig)
    print(f'  [OK] {filename}')


def main():
    parser = argparse.ArgumentParser(description='Generate lab meeting figures')
    parser.add_argument('--output-dir', type=str, default=None,
                        help='Output directory for figures')
    parser.add_argument('--only', type=str, default=None,
                        help='Comma-separated figure numbers to generate (e.g., 04,05,10)')
    args = parser.parse_args()

    # Output directory
    if args.output_dir:
        output_dir = Path(args.output_dir)
    else:
        today = datetime.now().strftime('%Y%m%d')
        output_dir = DATABASES / 'figures' / f'lab_meeting_{today}'
    output_dir.mkdir(parents=True, exist_ok=True)

    # Parse --only
    only = None
    if args.only:
        only = [int(x.strip()) for x in args.only.split(',')]

    print('Lab Meeting Figure Generator')
    print('=' * 40)
    print(f'Output: {output_dir}')
    print()

    # Apply style
    apply_style()

    # Load data
    print('Loading data sources...')
    brain_counts = load_brain_counts()
    print(f'  Brain counts: {len(brain_counts)} brains loaded')

    elife_df = load_elife_comparison()
    print(f'  eLife comparison: {"loaded" if elife_df is not None else "not found"}')

    lat_df = load_laterality()
    print(f'  Laterality: {"loaded" if lat_df is not None else "not found"}')

    batch_df = load_batch_2d()
    print(f'  2D batch: {len(batch_df) if batch_df is not None else 0} samples')

    kin_df = load_reach_kinematics()
    print(f'  Reach kinematics: {len(kin_df) if kin_df is not None else 0} reaches')

    summary_df = load_reach_summary()
    print(f'  PI summary: {len(summary_df) if summary_df is not None else 0} videos')

    cal_df = load_calibration_runs()
    print(f'  Calibration runs: {len(cal_df) if cal_df is not None else 0} runs')

    db_path = DATABASES / 'connectome.db'
    db_stats = query_db_stats(db_path)
    print(f'  Database: {len(db_stats)} tables queried')

    print()
    print('Generating figures...')

    def should_gen(num):
        return only is None or num in only

    # Section 1: Project Overview
    if should_gen(1):
        try:
            fig_01_project_overview(output_dir)
        except Exception as e:
            print(f'  [FAIL] fig_01: {e}')
            traceback.print_exc()

    if should_gen(2):
        try:
            fig_02_data_organization(output_dir)
        except Exception as e:
            print(f'  [FAIL] fig_02: {e}')
            traceback.print_exc()

    # Section 2: 3D Brain
    if should_gen(3):
        try:
            fig_03_mousebrain_pipeline(output_dir)
        except Exception as e:
            print(f'  [FAIL] fig_03: {e}')
            traceback.print_exc()

    if should_gen(4):
        try:
            fig_04_brain_region_counts(output_dir, brain_counts)
        except Exception as e:
            print(f'  [FAIL] fig_04: {e}')
            traceback.print_exc()

    if should_gen(5):
        try:
            fig_05_elife_comparison(output_dir, elife_df)
        except Exception as e:
            print(f'  [FAIL] fig_05: {e}')
            traceback.print_exc()

    if should_gen(6):
        try:
            fig_06_hemisphere_laterality(output_dir, lat_df)
        except Exception as e:
            print(f'  [FAIL] fig_06: {e}')
            traceback.print_exc()

    # Section 3: 2D Slices
    if should_gen(7):
        try:
            fig_07_slice_quantification(output_dir, batch_df)
        except Exception as e:
            print(f'  [FAIL] fig_07: {e}')
            traceback.print_exc()

    # Section 4: Behavior
    if should_gen(8):
        try:
            fig_08_mousereach_pipeline(output_dir)
        except Exception as e:
            print(f'  [FAIL] fig_08: {e}')
            traceback.print_exc()

    if should_gen(9):
        try:
            fig_09_reach_outcomes(output_dir, kin_df, summary_df)
        except Exception as e:
            print(f'  [FAIL] fig_09: {e}')
            traceback.print_exc()

    if should_gen(10):
        try:
            fig_10_kinematic_comparison(output_dir, kin_df)
        except Exception as e:
            print(f'  [FAIL] fig_10: {e}')
            traceback.print_exc()

    if should_gen(11):
        try:
            fig_11_behavior_by_phase(output_dir, db_path)
        except Exception as e:
            print(f'  [FAIL] fig_11: {e}')
            traceback.print_exc()

    # Section 5: Video Collection
    if should_gen(12):
        try:
            fig_12_mousecam_system(output_dir)
        except Exception as e:
            print(f'  [FAIL] fig_12: {e}')
            traceback.print_exc()

    # Section 6: Cross-Pipeline
    if should_gen(13):
        try:
            fig_13_database_schema(output_dir, db_stats)
        except Exception as e:
            print(f'  [FAIL] fig_13: {e}')
            traceback.print_exc()

    if should_gen(14):
        try:
            fig_14_project_scale(output_dir, db_stats, brain_counts, kin_df,
                                  batch_df, cal_df)
        except Exception as e:
            print(f'  [FAIL] fig_14: {e}')
            traceback.print_exc()

    if should_gen(15):
        try:
            fig_15_processing_progress(output_dir, cal_df)
        except Exception as e:
            print(f'  [FAIL] fig_15: {e}')
            traceback.print_exc()

    print()
    print(f'Done! Figures saved to {output_dir}')


if __name__ == '__main__':
    main()
