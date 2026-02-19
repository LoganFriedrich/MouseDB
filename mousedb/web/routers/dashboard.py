"""Pipeline dashboard endpoints."""

import logging
from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from ...database import get_db
from ...watcher_bridge import WatcherBridge, STATE_DISPLAY, find_watcher_db
from ..dependencies import get_watcher

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


@router.get("/")
async def dashboard_page(request: Request):
    """Pipeline dashboard main page."""
    templates = request.app.state.templates
    watcher = get_watcher()

    summary = watcher.get_pipeline_summary()
    animals = watcher.get_animal_rollup()

    return templates.TemplateResponse("dashboard.html", {
        "request": request,
        "summary": summary,
        "animals": animals,
        "state_display": STATE_DISPLAY,
    })


@router.get("/summary-cards")
async def summary_cards(request: Request):
    """HTMX partial: refreshable summary cards."""
    templates = request.app.state.templates
    watcher = get_watcher()
    summary = watcher.get_pipeline_summary()

    return templates.TemplateResponse("components/summary_cards.html", {
        "request": request,
        "summary": summary,
    })


@router.get("/animal/{subject_id}")
async def animal_detail(request: Request, subject_id: str):
    """HTMX partial: detailed video list for one animal."""
    watcher = get_watcher()
    videos = watcher.get_videos_for_animal(subject_id)

    html = '<div class="animal-detail">'
    html += f'<h4>{subject_id} - {len(videos)} videos</h4>'
    html += '<table class="data-table"><thead><tr>'
    html += '<th>Video</th><th>State</th><th>Updated</th></tr></thead><tbody>'

    for v in videos:
        state = v.get('state', 'unknown')
        color = STATE_DISPLAY.get(state, {}).get('color', '#999')
        label = STATE_DISPLAY.get(state, {}).get('label', state)
        html += f'<tr><td>{v.get("video_name", "?")}</td>'
        html += f'<td><span class="state-badge" style="background:{color}">{label}</span></td>'
        html += f'<td>{v.get("updated_at", "")}</td></tr>'

    html += '</tbody></table></div>'
    return HTMLResponse(html)
