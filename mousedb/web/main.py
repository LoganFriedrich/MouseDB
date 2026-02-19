"""MouseDB Web Dashboard - FastAPI application."""

from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import config
from .dependencies import get_database
from .routers import plots, dashboard


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Initialize database on startup."""
    db = get_database()
    db.init_db()
    yield


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    app = FastAPI(
        title="MouseDB Dashboard",
        description="Lab data visualization and pipeline monitoring",
        version="0.1.0",
        lifespan=lifespan,
    )

    # Mount static files
    app.mount("/static", StaticFiles(directory=str(config.STATIC_DIR)), name="static")

    # Set up templates
    templates = Jinja2Templates(directory=str(config.TEMPLATES_DIR))
    app.state.templates = templates

    # Include routers
    app.include_router(plots.router)
    app.include_router(dashboard.router)

    # Landing page
    @app.get("/")
    async def index(request: Request):
        db = get_database()
        stats = db.get_stats()
        return templates.TemplateResponse("index.html", {
            "request": request,
            "stats": stats,
        })

    return app


app = create_app()


def run_server():
    """Entry point for `mousedb-web` command."""
    import argparse
    parser = argparse.ArgumentParser(description="MouseDB Web Dashboard")
    parser.add_argument("--host", default=config.HOST, help="Bind host")
    parser.add_argument("--port", type=int, default=config.PORT, help="Bind port")
    parser.add_argument("--reload", action="store_true", help="Auto-reload on changes")
    args = parser.parse_args()

    uvicorn.run(
        "mousedb.web.main:app",
        host=args.host,
        port=args.port,
        reload=args.reload,
    )


if __name__ == "__main__":
    run_server()
