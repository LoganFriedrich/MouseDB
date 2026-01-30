"""
Visualization tools for Connectome Data Entry system.

Provides publication-quality plots and statistics that go beyond Excel capabilities:
- Learning curves with confidence intervals
- Group comparisons with statistical tests
- Recovery trajectories post-injury
- Heatmaps of performance by pellet position
- Statistical summaries with effect sizes
"""

import numpy as np
import pandas as pd
from datetime import date, timedelta
from typing import List, Dict, Optional, Tuple, Any
from pathlib import Path
from io import BytesIO

# We'll use matplotlib for plotting - it can render to PyQt via canvas
import matplotlib
matplotlib.use('Agg')  # Use non-interactive backend by default
import matplotlib.pyplot as plt
import matplotlib.dates as mdates
from matplotlib.figure import Figure
from matplotlib.backends.backend_agg import FigureCanvasAgg

from .database import get_db
from .schema import Subject, PelletScore, Weight, Surgery, Cohort, INJURY_DAY
from .stats import calculate_daily_stats, calculate_subject_summary


# =============================================================================
# COLOR SCHEMES
# =============================================================================

COLORS = {
    'retrieved': '#2ecc71',    # Green
    'displaced': '#f39c12',    # Yellow/Orange
    'miss': '#e74c3c',         # Red
    'contacted': '#3498db',    # Blue
    'weight': '#9b59b6',       # Purple
    'pre_injury': '#3498db',   # Blue
    'post_injury': '#e74c3c',  # Red
    'rehab': '#2ecc71',        # Green
    'grid': '#ecf0f1',         # Light gray
    'text': '#2c3e50',         # Dark gray
}

PHASE_COLORS = {
    'Ramp': '#95a5a6',
    'Flat': '#3498db',
    'Pillar': '#e74c3c',
    'Easy': '#2ecc71',
    'Pre-Injury': '#9b59b6',
    'Post-Injury': '#e67e22',
    'Rehab': '#1abc9c',
}


# =============================================================================
# DATA EXTRACTION
# =============================================================================

def get_cohort_data(cohort_id: str, db=None) -> Dict[str, pd.DataFrame]:
    """
    Extract all data for a cohort in analysis-ready format.

    Returns dict with DataFrames:
    - subjects: Subject metadata
    - weights: Daily weights
    - pellets: All pellet scores
    - sessions: Session-level summaries
    - surgeries: Surgery details
    """
    db = db or get_db()

    with db.session() as session:
        # Get subjects
        subjects = session.query(Subject).filter(
            Subject.cohort_id == cohort_id
        ).all()

        subject_ids = [s.subject_id for s in subjects]

        subjects_df = pd.DataFrame([{
            'subject_id': s.subject_id,
            'sex': s.sex,
            'is_active': s.is_active,
            'date_of_death': s.date_of_death,
        } for s in subjects])

        # Get weights
        weights = session.query(Weight).filter(
            Weight.subject_id.in_(subject_ids)
        ).all()

        weights_df = pd.DataFrame([{
            'subject_id': w.subject_id,
            'date': w.date,
            'weight_grams': w.weight_grams,
            'weight_percent': w.weight_percent,
        } for w in weights])

        # Get pellet scores
        pellets = session.query(PelletScore).filter(
            PelletScore.subject_id.in_(subject_ids)
        ).all()

        pellets_df = pd.DataFrame([{
            'subject_id': p.subject_id,
            'date': p.session_date,
            'test_phase': p.test_phase,
            'tray_type': p.tray_type,
            'tray_number': p.tray_number,
            'pellet_number': p.pellet_number,
            'score': p.score,
        } for p in pellets])

        # Get surgeries
        surgeries = session.query(Surgery).filter(
            Surgery.subject_id.in_(subject_ids)
        ).all()

        surgeries_df = pd.DataFrame([{
            'subject_id': s.subject_id,
            'surgery_date': s.surgery_date,
            'surgery_type': s.surgery_type,
            'force_kdyn': s.force_kdyn,
            'displacement_um': s.displacement_um,
            'velocity_mm_s': s.velocity_mm_s,
        } for s in surgeries])

        # Calculate session summaries
        sessions_data = []
        if not pellets_df.empty:
            for (subj, dt), group in pellets_df.groupby(['subject_id', 'date']):
                total = len(group)
                retrieved = (group['score'] == 2).sum()
                displaced = (group['score'] == 1).sum()
                miss = (group['score'] == 0).sum()
                contacted = retrieved + displaced

                sessions_data.append({
                    'subject_id': subj,
                    'date': dt,
                    'test_phase': group['test_phase'].iloc[0],
                    'tray_type': group['tray_type'].iloc[0],
                    'n_trays': group['tray_number'].nunique(),
                    'n_pellets': total,
                    'retrieved': retrieved,
                    'displaced': displaced,
                    'miss': miss,
                    'contacted': contacted,
                    'retrieved_pct': 100 * retrieved / total if total > 0 else 0,
                    'contacted_pct': 100 * contacted / total if total > 0 else 0,
                })

        sessions_df = pd.DataFrame(sessions_data)

        return {
            'subjects': subjects_df,
            'weights': weights_df,
            'pellets': pellets_df,
            'sessions': sessions_df,
            'surgeries': surgeries_df,
        }


def get_cohort_start_date(cohort_id: str, db=None) -> Optional[date]:
    """Get the start date for a cohort."""
    db = db or get_db()
    with db.session() as session:
        cohort = session.query(Cohort).filter_by(cohort_id=cohort_id).first()
        return cohort.start_date if cohort else None


# =============================================================================
# LEARNING CURVES
# =============================================================================

def plot_learning_curves(cohort_id: str, metric: str = 'retrieved_pct',
                        show_individual: bool = True,
                        show_mean: bool = True,
                        show_ci: bool = True,
                        figsize: Tuple[int, int] = (12, 6),
                        db=None) -> Figure:
    """
    Plot learning curves showing performance over time.

    Args:
        cohort_id: Cohort to plot
        metric: 'retrieved_pct', 'contacted_pct', or 'miss'
        show_individual: Show individual animal traces
        show_mean: Show group mean line
        show_ci: Show 95% confidence interval
        figsize: Figure size

    Returns:
        matplotlib Figure object
    """
    data = get_cohort_data(cohort_id, db)
    sessions = data['sessions']
    surgeries = data['surgeries']

    if sessions.empty:
        fig, ax = plt.subplots(figsize=figsize)
        ax.text(0.5, 0.5, 'No session data available',
                ha='center', va='center', transform=ax.transAxes)
        return fig

    fig, ax = plt.subplots(figsize=figsize)

    # Get injury date for reference line
    injury_date = None
    if not surgeries.empty:
        contusion = surgeries[surgeries['surgery_type'] == 'contusion']
        if not contusion.empty:
            injury_date = pd.to_datetime(contusion['surgery_date'].iloc[0])

    # Sort by date
    sessions = sessions.sort_values('date')
    dates = sessions['date'].unique()

    # Plot individual animals
    if show_individual:
        for subject_id in sessions['subject_id'].unique():
            subj_data = sessions[sessions['subject_id'] == subject_id]
            ax.plot(subj_data['date'], subj_data[metric],
                   alpha=0.3, linewidth=1, color=COLORS['contacted'])

    # Calculate and plot mean
    if show_mean or show_ci:
        daily_stats = sessions.groupby('date')[metric].agg(['mean', 'std', 'count'])
        daily_stats['sem'] = daily_stats['std'] / np.sqrt(daily_stats['count'])
        daily_stats['ci95'] = 1.96 * daily_stats['sem']

        if show_mean:
            ax.plot(daily_stats.index, daily_stats['mean'],
                   linewidth=2.5, color=COLORS['contacted'], label='Group Mean')

        if show_ci:
            ax.fill_between(daily_stats.index,
                           daily_stats['mean'] - daily_stats['ci95'],
                           daily_stats['mean'] + daily_stats['ci95'],
                           alpha=0.3, color=COLORS['contacted'], label='95% CI')

    # Add injury line
    if injury_date:
        ax.axvline(injury_date, color=COLORS['miss'], linestyle='--',
                  linewidth=2, label='Injury')

    # Formatting
    metric_labels = {
        'retrieved_pct': 'Retrieved (%)',
        'contacted_pct': 'Contacted (%)',
        'miss': 'Miss (%)',
    }

    ax.set_xlabel('Date', fontsize=12, color=COLORS['text'])
    ax.set_ylabel(metric_labels.get(metric, metric), fontsize=12, color=COLORS['text'])
    ax.set_title(f'{cohort_id} Learning Curve', fontsize=14, fontweight='bold', color=COLORS['text'])
    ax.legend(loc='best')
    ax.set_ylim(0, 100)
    ax.grid(True, alpha=0.3, color=COLORS['grid'])

    # Format x-axis dates
    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    ax.xaxis.set_major_locator(mdates.WeekdayLocator(interval=1))
    plt.xticks(rotation=45)

    plt.tight_layout()
    return fig


def plot_phase_comparison(cohort_id: str, metric: str = 'retrieved_pct',
                         figsize: Tuple[int, int] = (10, 6),
                         db=None) -> Figure:
    """
    Plot bar chart comparing performance across phases.

    Shows mean ± SEM for each phase with individual data points overlaid.
    """
    data = get_cohort_data(cohort_id, db)
    sessions = data['sessions']

    if sessions.empty:
        fig, ax = plt.subplots(figsize=figsize)
        ax.text(0.5, 0.5, 'No session data available',
                ha='center', va='center', transform=ax.transAxes)
        return fig

    # Group by simplified phase
    def simplify_phase(phase):
        if 'Pre-Injury' in str(phase):
            return 'Pre-Injury'
        elif 'Post-Injury' in str(phase):
            return 'Post-Injury'
        elif 'Rehab' in str(phase):
            return 'Rehab'
        elif 'Training' in str(phase):
            return 'Training'
        else:
            return 'Other'

    sessions['phase_group'] = sessions['test_phase'].apply(simplify_phase)

    # Calculate stats per phase
    phase_stats = sessions.groupby('phase_group')[metric].agg(['mean', 'std', 'count', 'sem'])
    phase_stats = phase_stats.reindex(['Training', 'Pre-Injury', 'Post-Injury', 'Rehab', 'Other'])
    phase_stats = phase_stats.dropna(subset=['mean'])

    fig, ax = plt.subplots(figsize=figsize)

    x = np.arange(len(phase_stats))
    colors = [PHASE_COLORS.get(p, '#95a5a6') for p in phase_stats.index]

    # Bar plot
    bars = ax.bar(x, phase_stats['mean'], yerr=phase_stats['sem'],
                 color=colors, edgecolor='white', linewidth=1.5,
                 capsize=5, error_kw={'linewidth': 2})

    # Overlay individual points
    for i, phase in enumerate(phase_stats.index):
        phase_data = sessions[sessions['phase_group'] == phase][metric]
        jitter = np.random.normal(0, 0.1, len(phase_data))
        ax.scatter(np.full(len(phase_data), i) + jitter, phase_data,
                  alpha=0.5, s=30, color='black', zorder=5)

    # Formatting
    ax.set_xticks(x)
    ax.set_xticklabels(phase_stats.index, fontsize=11)
    ax.set_ylabel(f'{metric.replace("_pct", " (%)")}', fontsize=12)
    ax.set_title(f'{cohort_id} Performance by Phase', fontsize=14, fontweight='bold')
    ax.set_ylim(0, 100)
    ax.grid(True, axis='y', alpha=0.3)

    # Add count annotations
    for i, (phase, row) in enumerate(phase_stats.iterrows()):
        ax.annotate(f'n={int(row["count"])}', xy=(i, row['mean'] + row['sem'] + 3),
                   ha='center', fontsize=9, color=COLORS['text'])

    plt.tight_layout()
    return fig


# =============================================================================
# HEATMAPS
# =============================================================================

def plot_pellet_heatmap(cohort_id: str, subject_id: Optional[str] = None,
                       date_filter: Optional[date] = None,
                       figsize: Tuple[int, int] = (14, 6),
                       db=None) -> Figure:
    """
    Plot heatmap showing retrieval success by pellet position.

    Helps identify spatial patterns (e.g., better performance on certain sides).
    """
    data = get_cohort_data(cohort_id, db)
    pellets = data['pellets']

    if pellets.empty:
        fig, ax = plt.subplots(figsize=figsize)
        ax.text(0.5, 0.5, 'No pellet data available',
                ha='center', va='center', transform=ax.transAxes)
        return fig

    # Filter if specified
    if subject_id:
        pellets = pellets[pellets['subject_id'] == subject_id]
    if date_filter:
        pellets = pellets[pellets['date'] == date_filter]

    # Create position matrix (4 trays x 20 pellets)
    # Value = mean retrieval rate (score=2) at each position
    heatmap_data = np.zeros((4, 20))
    counts = np.zeros((4, 20))

    for _, row in pellets.iterrows():
        tray_idx = row['tray_number'] - 1
        pellet_idx = row['pellet_number'] - 1
        heatmap_data[tray_idx, pellet_idx] += (row['score'] == 2)
        counts[tray_idx, pellet_idx] += 1

    # Convert to percentages
    with np.errstate(divide='ignore', invalid='ignore'):
        heatmap_pct = 100 * heatmap_data / counts
        heatmap_pct = np.nan_to_num(heatmap_pct)

    fig, ax = plt.subplots(figsize=figsize)

    # Create heatmap
    im = ax.imshow(heatmap_pct, cmap='RdYlGn', aspect='auto', vmin=0, vmax=100)

    # Add colorbar
    cbar = fig.colorbar(im, ax=ax, label='Retrieved (%)')

    # Labels
    ax.set_xticks(np.arange(20))
    ax.set_xticklabels([str(i+1) for i in range(20)])
    ax.set_yticks(np.arange(4))
    ax.set_yticklabels(['Tray 1', 'Tray 2', 'Tray 3', 'Tray 4'])

    ax.set_xlabel('Pellet Position', fontsize=12)
    ax.set_ylabel('Tray', fontsize=12)

    title = f'{cohort_id} Pellet Retrieval by Position'
    if subject_id:
        title += f' ({subject_id})'
    ax.set_title(title, fontsize=14, fontweight='bold')

    # Add text annotations for values
    for i in range(4):
        for j in range(20):
            if counts[i, j] > 0:
                text = ax.text(j, i, f'{heatmap_pct[i, j]:.0f}',
                              ha='center', va='center', fontsize=8,
                              color='white' if heatmap_pct[i, j] < 50 else 'black')

    plt.tight_layout()
    return fig


# =============================================================================
# WEIGHT TRACKING
# =============================================================================

def plot_weight_curves(cohort_id: str, show_baseline_pct: bool = True,
                      figsize: Tuple[int, int] = (12, 6),
                      db=None) -> Figure:
    """
    Plot weight tracking over time.

    Shows individual animals and mean with confidence interval.
    Can show as raw weight or percentage of baseline.
    """
    data = get_cohort_data(cohort_id, db)
    weights = data['weights']
    surgeries = data['surgeries']

    if weights.empty:
        fig, ax = plt.subplots(figsize=figsize)
        ax.text(0.5, 0.5, 'No weight data available',
                ha='center', va='center', transform=ax.transAxes)
        return fig

    fig, ax = plt.subplots(figsize=figsize)

    # Get injury date
    injury_date = None
    if not surgeries.empty:
        contusion = surgeries[surgeries['surgery_type'] == 'contusion']
        if not contusion.empty:
            injury_date = pd.to_datetime(contusion['surgery_date'].iloc[0])

    # Calculate baseline weight per animal (first 3 days average)
    weights = weights.sort_values(['subject_id', 'date'])

    if show_baseline_pct:
        # Calculate baseline for each animal
        baselines = weights.groupby('subject_id').apply(
            lambda x: x.nsmallest(3, 'date')['weight_grams'].mean()
        )
        weights = weights.copy()
        weights['baseline'] = weights['subject_id'].map(baselines)
        weights['weight_pct'] = 100 * weights['weight_grams'] / weights['baseline']
        y_col = 'weight_pct'
        ylabel = 'Weight (% of baseline)'
    else:
        y_col = 'weight_grams'
        ylabel = 'Weight (g)'

    # Plot individual animals
    for subject_id in weights['subject_id'].unique():
        subj_data = weights[weights['subject_id'] == subject_id]
        ax.plot(pd.to_datetime(subj_data['date']), subj_data[y_col],
               alpha=0.3, linewidth=1, color=COLORS['weight'])

    # Plot mean with CI
    daily_stats = weights.groupby('date')[y_col].agg(['mean', 'std', 'count'])
    daily_stats['sem'] = daily_stats['std'] / np.sqrt(daily_stats['count'])
    daily_stats['ci95'] = 1.96 * daily_stats['sem']

    dates = pd.to_datetime(daily_stats.index)
    ax.plot(dates, daily_stats['mean'],
           linewidth=2.5, color=COLORS['weight'], label='Group Mean')
    ax.fill_between(dates,
                   daily_stats['mean'] - daily_stats['ci95'],
                   daily_stats['mean'] + daily_stats['ci95'],
                   alpha=0.3, color=COLORS['weight'], label='95% CI')

    # Add injury line
    if injury_date:
        ax.axvline(injury_date, color=COLORS['miss'], linestyle='--',
                  linewidth=2, label='Injury')

    # Add 80% baseline threshold line (food deprivation limit)
    if show_baseline_pct:
        ax.axhline(80, color='orange', linestyle=':', linewidth=1.5, label='80% threshold')

    # Formatting
    ax.set_xlabel('Date', fontsize=12)
    ax.set_ylabel(ylabel, fontsize=12)
    ax.set_title(f'{cohort_id} Weight Tracking', fontsize=14, fontweight='bold')
    ax.legend(loc='best')
    ax.grid(True, alpha=0.3)

    ax.xaxis.set_major_formatter(mdates.DateFormatter('%m/%d'))
    plt.xticks(rotation=45)

    plt.tight_layout()
    return fig


# =============================================================================
# RECOVERY ANALYSIS
# =============================================================================

def plot_recovery_trajectory(cohort_id: str, metric: str = 'retrieved_pct',
                            figsize: Tuple[int, int] = (10, 6),
                            db=None) -> Figure:
    """
    Plot recovery trajectory relative to injury day.

    X-axis is days post-injury (DPI), allowing comparison across cohorts
    that may have different calendar dates.
    """
    data = get_cohort_data(cohort_id, db)
    sessions = data['sessions']
    surgeries = data['surgeries']

    if sessions.empty or surgeries.empty:
        fig, ax = plt.subplots(figsize=figsize)
        ax.text(0.5, 0.5, 'Insufficient data for recovery analysis',
                ha='center', va='center', transform=ax.transAxes)
        return fig

    # Get injury dates per animal
    contusion = surgeries[surgeries['surgery_type'] == 'contusion']
    if contusion.empty:
        fig, ax = plt.subplots(figsize=figsize)
        ax.text(0.5, 0.5, 'No contusion surgery data found',
                ha='center', va='center', transform=ax.transAxes)
        return fig

    injury_dates = contusion.set_index('subject_id')['surgery_date'].to_dict()

    # Calculate DPI for each session
    sessions = sessions.copy()
    sessions['dpi'] = sessions.apply(
        lambda row: (row['date'] - injury_dates.get(row['subject_id'], row['date'])).days
        if row['subject_id'] in injury_dates else None,
        axis=1
    )
    sessions = sessions.dropna(subset=['dpi'])
    sessions['dpi'] = sessions['dpi'].astype(int)

    fig, ax = plt.subplots(figsize=figsize)

    # Plot individual animals
    for subject_id in sessions['subject_id'].unique():
        subj_data = sessions[sessions['subject_id'] == subject_id].sort_values('dpi')
        ax.plot(subj_data['dpi'], subj_data[metric],
               alpha=0.3, linewidth=1, color=COLORS['post_injury'])

    # Plot mean with CI
    dpi_stats = sessions.groupby('dpi')[metric].agg(['mean', 'std', 'count'])
    dpi_stats['sem'] = dpi_stats['std'] / np.sqrt(dpi_stats['count'])
    dpi_stats['ci95'] = 1.96 * dpi_stats['sem']

    ax.plot(dpi_stats.index, dpi_stats['mean'],
           linewidth=2.5, color=COLORS['post_injury'], label='Group Mean')
    ax.fill_between(dpi_stats.index,
                   dpi_stats['mean'] - dpi_stats['ci95'],
                   dpi_stats['mean'] + dpi_stats['ci95'],
                   alpha=0.3, color=COLORS['post_injury'], label='95% CI')

    # Add injury line at DPI 0
    ax.axvline(0, color=COLORS['miss'], linestyle='--', linewidth=2, label='Injury')

    # Formatting
    ax.set_xlabel('Days Post-Injury (DPI)', fontsize=12)
    ax.set_ylabel(f'{metric.replace("_pct", " (%)")}', fontsize=12)
    ax.set_title(f'{cohort_id} Recovery Trajectory', fontsize=14, fontweight='bold')
    ax.legend(loc='best')
    ax.set_ylim(0, 100)
    ax.grid(True, alpha=0.3)

    plt.tight_layout()
    return fig


# =============================================================================
# STATISTICAL ANALYSIS
# =============================================================================

def calculate_cohort_statistics(cohort_id: str, db=None) -> Dict[str, Any]:
    """
    Calculate comprehensive statistics for a cohort.

    Returns dict with:
    - sample_size: Number of animals
    - phase_stats: Performance by phase
    - recovery_stats: Pre/post injury comparison
    - individual_stats: Per-animal summaries
    """
    data = get_cohort_data(cohort_id, db)
    sessions = data['sessions']
    surgeries = data['surgeries']

    if sessions.empty:
        return {'error': 'No session data available'}

    stats = {
        'sample_size': sessions['subject_id'].nunique(),
        'total_sessions': len(sessions),
        'total_pellets': sessions['n_pellets'].sum(),
    }

    # Overall performance
    stats['overall_retrieved_pct'] = sessions['retrieved_pct'].mean()
    stats['overall_contacted_pct'] = sessions['contacted_pct'].mean()

    # Phase statistics
    phase_stats = sessions.groupby('test_phase').agg({
        'retrieved_pct': ['mean', 'std', 'count'],
        'contacted_pct': ['mean', 'std', 'count'],
    }).round(2)
    stats['phase_stats'] = phase_stats.to_dict()

    # Pre/Post injury comparison if surgery data available
    if not surgeries.empty:
        contusion = surgeries[surgeries['surgery_type'] == 'contusion']
        if not contusion.empty:
            injury_dates = contusion.set_index('subject_id')['surgery_date'].to_dict()

            def classify_session(row):
                injury_date = injury_dates.get(row['subject_id'])
                if injury_date is None:
                    return 'unknown'
                if row['date'] < injury_date:
                    return 'pre_injury'
                else:
                    return 'post_injury'

            sessions['injury_phase'] = sessions.apply(classify_session, axis=1)

            pre = sessions[sessions['injury_phase'] == 'pre_injury']['retrieved_pct']
            post = sessions[sessions['injury_phase'] == 'post_injury']['retrieved_pct']

            if len(pre) > 0 and len(post) > 0:
                # Calculate effect size (Cohen's d)
                pooled_std = np.sqrt((pre.std()**2 + post.std()**2) / 2)
                cohens_d = (pre.mean() - post.mean()) / pooled_std if pooled_std > 0 else 0

                # T-test (paired if same subjects)
                from scipy import stats as scipy_stats
                try:
                    t_stat, p_value = scipy_stats.ttest_ind(pre, post)
                except:
                    t_stat, p_value = None, None

                stats['recovery_stats'] = {
                    'pre_injury_mean': pre.mean(),
                    'pre_injury_std': pre.std(),
                    'pre_injury_n': len(pre),
                    'post_injury_mean': post.mean(),
                    'post_injury_std': post.std(),
                    'post_injury_n': len(post),
                    'difference': pre.mean() - post.mean(),
                    'cohens_d': cohens_d,
                    't_statistic': t_stat,
                    'p_value': p_value,
                }

    # Individual animal stats
    individual = sessions.groupby('subject_id').agg({
        'retrieved_pct': ['mean', 'std'],
        'contacted_pct': ['mean', 'std'],
        'n_pellets': 'sum',
    }).round(2)
    stats['individual_stats'] = individual.to_dict()

    return stats


# =============================================================================
# EXPORT UTILITIES
# =============================================================================

def save_figure(fig: Figure, path: Path, dpi: int = 150):
    """Save figure to file."""
    fig.savefig(path, dpi=dpi, bbox_inches='tight', facecolor='white')
    plt.close(fig)


def figure_to_bytes(fig: Figure, format: str = 'png', dpi: int = 100) -> bytes:
    """Convert figure to bytes for embedding in GUI."""
    buf = BytesIO()
    fig.savefig(buf, format=format, dpi=dpi, bbox_inches='tight', facecolor='white')
    buf.seek(0)
    data = buf.read()
    plt.close(fig)
    return data


def generate_all_plots(cohort_id: str, output_dir: Path, db=None):
    """
    Generate all standard plots for a cohort and save to directory.

    Creates:
    - learning_curve.png
    - phase_comparison.png
    - pellet_heatmap.png
    - weight_tracking.png
    - recovery_trajectory.png
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    plots = [
        ('learning_curve.png', plot_learning_curves),
        ('phase_comparison.png', plot_phase_comparison),
        ('pellet_heatmap.png', plot_pellet_heatmap),
        ('weight_tracking.png', plot_weight_curves),
        ('recovery_trajectory.png', plot_recovery_trajectory),
    ]

    generated = []
    for filename, plot_func in plots:
        try:
            fig = plot_func(cohort_id, db=db)
            path = output_dir / filename
            save_figure(fig, path)
            generated.append(path)
        except Exception as e:
            print(f"Warning: Failed to generate {filename}: {e}")

    return generated
