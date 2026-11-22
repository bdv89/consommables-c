"""FastAPI application entry point."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter, _rate_limit_exceeded_handler
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address

from app.config import get_settings

# Rate limiter instance
limiter = Limiter(key_func=get_remote_address)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    settings = get_settings()
    print(f"Server starting on {settings.host}:{settings.port}")
    print(f"DB: {settings.db_path}")
    print(f"Media root: {settings.media_root}")
    if settings.editing_enabled:
        print(f"Edit mode: ENABLED (backup dir: {settings.backup_dir})")
    else:
        print("Edit mode: DISABLED (use --edit-password to enable)")

    # Initialize metrics database
    from app.routes.metrics import init_metrics
    await init_metrics()

    yield

    print("Server shutting down")


app = FastAPI(
    title="Inventaire consommable bâtiment C",
    description="Inventaire consommable bâtiment C (recherche FTS5, journal de mouvements, seuils par article)",
    version="1.0.0",
    lifespan=lifespan,
)

# Register rate limiter
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, _rate_limit_exceeded_handler)


@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    """Add security headers to all responses."""
    response: Response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-XSS-Protection"] = "1; mode=block"
    return response


def setup_routes():
    """Import and include all routers."""
    from app.routes import search, inventory, movements, metrics, files, export

    app.include_router(search.router, prefix="/api", tags=["search"])
    app.include_router(inventory.router, prefix="/api", tags=["inventory"])
    app.include_router(movements.router, prefix="/api", tags=["movements"])
    app.include_router(metrics.router, prefix="/api/metrics", tags=["metrics"])
    app.include_router(files.router, tags=["files"])
    app.include_router(export.router, prefix="/api", tags=["export"])


def setup_static_files():
    """Mount static files directory."""
    static_dir = Path(__file__).parent.parent / "static"
    if static_dir.exists():
        # Mount at root with html=True to serve index.html for /
        app.mount("/", StaticFiles(directory=str(static_dir), html=True), name="static")


# Setup routes and static files on import
setup_routes()
setup_static_files()
