"""Visualization endpoints - spaghetti plots and trajectory overlays."""

import logging
from typing import Optional

import numpy as np
import plotly.graph_objects as go
from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse

from ...database import get_db
from ...schema import Subject, Cohort
from ..config import PROCESSING_ROOT
from ..trajectory import (
    PHASE_GROUPS,
    SubjectTrajectories,
    align_trajectories,
    compute_mean_trajectory,
    extract_trajectories_for_subject,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/plots", tags=["visualizations"])


def _get_cohort_start_date(subject_id: str) -> Optional[str]:
    """Look up cohort start date for a subject."""
    db = get_db()
    with db.session() as session:
        subject = session.query(Subject).filter(Subject.subject_id == subject_id).first()
        if subject:
            cohort = session.query(Cohort).filter(Cohort.cohort_id == subject.cohort_id).first()
            if cohort and cohort.start_date:
                return str(cohort.start_date)
    return None


@router.get("/spaghetti")
async def spaghetti_page(request: Request):
    """Spaghetti plot configuration page."""
    templates = request.app.state.templates
    db = get_db()

    with db.session() as session:
        cohorts = session.query(Cohort).order_by(Cohort.cohort_id).all()
        cohort_data = []
        for c in cohorts:
            subjects = session.query(Subject).filter(
                Subject.cohort_id == c.cohort_id,
                Subject.is_active == True
            ).order_by(Subject.subject_id).all()
            cohort_data.append({
                'cohort_id': c.cohort_id,
                'start_date': str(c.start_date) if c.start_date else None,
                'subjects': [{'subject_id': s.subject_id, 'sex': s.sex} for s in subjects],
            })

    return templates.TemplateResponse("spaghetti.html", {
        "request": request,
        "cohorts": cohort_data,
        "phase_groups": PHASE_GROUPS,
    })


@router.get("/spaghetti/render")
async def render_spaghetti(
    subject_id: str = Query(..., description="Subject ID (e.g. CNT_01_15)"),
    phases: Optional[str] = Query(None, description="Comma-separated phase groups"),
    outcome: str = Query("retrieved", description="Outcome filter"),
    align: bool = Query(True, description="Align trajectories to start position"),
    show_mean: bool = Query(True, description="Show mean trajectory per phase"),
    alpha: float = Query(0.3, description="Individual trajectory transparency"),
):
    """Render spaghetti plot as interactive Plotly HTML."""
    phase_list = [p.strip() for p in phases.split(',')] if phases else None
    cohort_start_date = _get_cohort_start_date(subject_id)

    data = extract_trajectories_for_subject(
        subject_id=subject_id,
        processing_dir=PROCESSING_ROOT,
        phase_groups=phase_list,
        outcome_filter=outcome,
        cohort_start_date=cohort_start_date,
    )

    html = _render_spaghetti_plotly(data, align=align, show_mean=show_mean, alpha=alpha)
    return HTMLResponse(html)


@router.get("/spaghetti/stats")
async def spaghetti_stats(
    subject_id: str = Query(...),
    phases: Optional[str] = Query(None),
    outcome: str = Query("retrieved"),
):
    """Return reach counts and summary stats per phase."""
    phase_list = [p.strip() for p in phases.split(',')] if phases else None
    cohort_start_date = _get_cohort_start_date(subject_id)

    data = extract_trajectories_for_subject(
        subject_id=subject_id,
        processing_dir=PROCESSING_ROOT,
        phase_groups=phase_list,
        outcome_filter=outcome,
        cohort_start_date=cohort_start_date,
    )

    stats = {}
    for phase, trajectories in data.by_phase.items():
        durations = [t.duration_frames for t in trajectories]
        stats[phase] = {
            'count': len(trajectories),
            'mean_duration': float(np.mean(durations)) if durations else 0,
            'color': PHASE_GROUPS.get(phase, {}).get('color', '#999'),
        }

    return {
        'subject_id': subject_id,
        'total_reaches': data.total_reaches,
        'by_phase': stats,
        'errors': data.errors[:5],
    }


@router.get("/subjects/{cohort_id}")
async def get_subjects_for_cohort(cohort_id: str):
    """HTMX endpoint: return subject options for a cohort."""
    db = get_db()
    with db.session() as session:
        subjects = session.query(Subject).filter(
            Subject.cohort_id == cohort_id,
            Subject.is_active == True
        ).order_by(Subject.subject_id).all()

        html = ""
        for s in subjects:
            html += f'<option value="{s.subject_id}">{s.subject_id} ({s.sex})</option>\n'
        return HTMLResponse(html)


def _render_spaghetti_plotly(data: SubjectTrajectories,
                              align: bool = True,
                              show_mean: bool = True,
                              alpha: float = 0.3) -> str:
    """Render spaghetti plot as interactive Plotly HTML div."""
    fig = go.Figure()

    if not data.by_phase:
        # Empty state
        fig.add_annotation(
            text=f"No matching reaches found for {data.subject_id}",
            xref="paper", yref="paper", x=0.5, y=0.5,
            showarrow=False, font=dict(size=14, color="#757575"),
        )
        if data.errors:
            fig.add_annotation(
                text="<br>".join(data.errors[:3]),
                xref="paper", yref="paper", x=0.5, y=0.35,
                showarrow=False, font=dict(size=11, color="#999"),
            )
        return fig.to_html(include_plotlyjs='cdn', full_html=False, div_id='spaghetti-plot')

    # Plot each phase group
    for phase_name in PHASE_GROUPS:
        if phase_name not in data.by_phase:
            continue

        trajectories = data.by_phase[phase_name]
        color = PHASE_GROUPS[phase_name]['color']
        n = len(trajectories)

        if align:
            trajectories = align_trajectories(trajectories)

        # Plot individual trajectories (semi-transparent)
        for i, t in enumerate(trajectories):
            valid = ~(np.isnan(t.x) | np.isnan(t.y))
            if valid.sum() < 2:
                continue
            fig.add_trace(go.Scatter(
                x=t.x[valid], y=t.y[valid],
                mode='lines',
                line=dict(color=color, width=0.8),
                opacity=alpha,
                showlegend=False,
                hoverinfo='skip',
                legendgroup=phase_name,
            ))

        # Plot mean trajectory (bold)
        if show_mean and n >= 2:
            mean = compute_mean_trajectory(trajectories)
            if mean is not None:
                mean_x, mean_y = mean
                fig.add_trace(go.Scatter(
                    x=mean_x, y=mean_y,
                    mode='lines',
                    line=dict(color=color, width=3),
                    name=f"{phase_name} (n={n})",
                    legendgroup=phase_name,
                    hovertemplate=f"{phase_name}<br>x: %{{x:.1f}}<br>y: %{{y:.1f}}<extra></extra>",
                ))
            else:
                fig.add_trace(go.Scatter(
                    x=[None], y=[None], mode='lines',
                    line=dict(color=color, width=3),
                    name=f"{phase_name} (n={n})",
                    legendgroup=phase_name,
                ))
        else:
            fig.add_trace(go.Scatter(
                x=[None], y=[None], mode='lines',
                line=dict(color=color, width=3),
                name=f"{phase_name} (n={n})",
                legendgroup=phase_name,
            ))

    # Layout
    fig.update_layout(
        title=dict(
            text=f"Reach Trajectories - {data.subject_id}",
            font=dict(size=16),
        ),
        xaxis=dict(
            title="Lateral Position (px)",
            scaleanchor="y",
            scaleratio=1,
        ),
        yaxis=dict(
            title="Extension (px)",
            autorange="reversed",  # Video coords: y increases downward
        ),
        legend=dict(
            yanchor="top", y=0.99,
            xanchor="left", x=0.01,
            bgcolor="rgba(255,255,255,0.9)",
            bordercolor="#E0E0E0",
            borderwidth=1,
        ),
        template="plotly_white",
        height=550,
        margin=dict(l=60, r=20, t=50, b=50),
    )

    # Add crosshairs at origin if aligned
    if align:
        fig.add_hline(y=0, line_dash="dot", line_color="#ddd", line_width=1)
        fig.add_vline(x=0, line_dash="dot", line_color="#ddd", line_width=1)

    return fig.to_html(include_plotlyjs='cdn', full_html=False, div_id='spaghetti-plot')
