"""Tacit FastAPI application"""
# Force redeploy

import structlog
from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pathlib import Path
import os
from slowapi import Limiter
from slowapi.util import get_remote_address
from slowapi.errors import RateLimitExceeded
from fastapi.exceptions import RequestValidationError

from .api import chat, context, documents
from .api import ingest, graph as graph_api, share as share_api, images as images_api, billing as billing_api, recover as recover_api, migrate as migrate_api, quickadd as quickadd_api, features as features_api
from .core.auth import get_current_user
from .core.config import TacitConfig
from .core.engine import TacitEngine
from .services.ingestion_service import IngestionService
from .services.graph_service import GraphService
from .db.database import DEFAULT_DATA_DIR

# Configure logging
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
    ]
)

logger = structlog.get_logger()

# Initialize rate limiter
limiter = Limiter(key_func=get_remote_address)

# Initialize FastAPI app
app = FastAPI(
    title="Tacit",
    description="Your Personal Work Twin",
    version="0.1.0"
)
app.state.limiter = limiter
app.add_exception_handler(RateLimitExceeded, lambda request, exc: {"detail": "Rate limit exceeded"})

# CORS middleware — restrict to configured domains
ALLOWED_ORIGINS = os.getenv("ALLOWED_ORIGINS", "https://www.trytacit.app,https://trytacit.app,http://localhost:3000").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in ALLOWED_ORIGINS],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Content-Type", "Authorization"],
)

# Initialize global instances
config = TacitConfig.load()
engine = TacitEngine(config)
ingestion_service = IngestionService()
graph_service = GraphService(
    vector_service=engine.vector_service,
    client=engine.client,
    model=config.default_model,
)
engine.graph_service = graph_service
engine.ingestion_service = ingestion_service

# Make services available to routes
app.state.engine = engine
app.state.config = config
app.state.ingestion_service = ingestion_service
app.state.graph_service = graph_service

# Auth dependency applied to all protected routers
auth_dep = [Depends(get_current_user)]

# Include API routers — protected by Clerk JWT
app.include_router(chat.router, prefix="/api", tags=["chat"], dependencies=auth_dep)
app.include_router(context.router, prefix="/api", tags=["context"], dependencies=auth_dep)
app.include_router(documents.router, prefix="/api", tags=["documents"], dependencies=auth_dep)
app.include_router(images_api.router, prefix="/api", tags=["images"], dependencies=auth_dep)
app.include_router(ingest.router, prefix="/api", tags=["ingest"], dependencies=auth_dep)
app.include_router(graph_api.router, prefix="/api", tags=["graph"], dependencies=auth_dep)
# Share tokens are public (unauthenticated by design)
app.include_router(share_api.router, prefix="/api", tags=["share"])
# Billing — webhook is unsigned, but other routes require Clerk auth
app.include_router(billing_api.router, prefix="/api", tags=["billing"])
# Recovery — protected by key only, for emergency data restoration
app.include_router(recover_api.router, prefix="/api", tags=["recover"])
# Migration — protected by secret header, no Clerk auth
app.include_router(migrate_api.router, prefix="/api", tags=["migrate"])
# Features — public endpoint (no auth required)
app.include_router(features_api.router, prefix="/api", tags=["features"])
# Quick-add — no auth_dep here (endpoints use Clerk or token auth internally)
app.include_router(quickadd_api.router, prefix="/api", tags=["quickadd"])

# Serve user uploads (images, etc.)
uploads_path = DEFAULT_DATA_DIR / "uploads"
uploads_path.mkdir(parents=True, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=str(uploads_path)), name="uploads")

# Serve frontend
frontend_path = Path(__file__).parent.parent.parent / "frontend" / "static"
if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path)), name="static")


@app.post("/share", response_class=HTMLResponse)
async def pwa_share_target(request: Request):
    """PWA share target — receives URLs shared from Android/iOS to the installed PWA."""
    from urllib.parse import quote, urlparse
    try:
        form = await request.form()
        url = form.get("url") or form.get("text") or ""
        # Validate it's a real http/https URL before embedding
        parsed = urlparse(url)
        if parsed.scheme in ("http", "https") and parsed.netloc:
            safe_url = quote(url, safe="/:@?=&%#+-.")
            return HTMLResponse(
                f'<script>window.location="/?share_url={safe_url}"</script>',
                status_code=200
            )
    except Exception:
        pass
    return HTMLResponse('<script>window.location="/"</script>', status_code=200)


@app.get("/share/{token}", response_class=HTMLResponse)
async def share_canvas(token: str):
    from .db.database import ShareTokenDB, get_database
    db = get_database()

    def _check(session):
        row = session.query(ShareTokenDB).filter_by(token=token).first()
        if not row:
            return ("not_found", None)
        return ("ok", int(row.revoked or 0))

    try:
        status, revoked = db.run_with_retry(_check)
    except Exception as e:
        logger.error("share_canvas_db_error", error=str(e))
        return HTMLResponse(
            "<h1 style='font-family:sans-serif;padding:40px'>Temporary error — please refresh in a moment</h1>",
            status_code=503,
        )

    if status == "not_found":
        return HTMLResponse("<h1 style='font-family:sans-serif;padding:40px'>Link not found</h1>", status_code=404)
    if revoked:
        return HTMLResponse("<h1 style='font-family:sans-serif;padding:40px'>This link has been revoked</h1>", status_code=403)

    html_file = frontend_path / "read_only.html"
    return HTMLResponse(html_file.read_text())


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the main Tacit interface"""
    html_file = frontend_path / "index.html"

    if html_file.exists():
        return html_file.read_text()

    # Temporary landing page if frontend not ready
    return """
    <!DOCTYPE html>
    <html>
    <head>
        <title>Tacit - Your Personal Work Twin</title>
        <style>
            body {
                font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
                max-width: 800px;
                margin: 100px auto;
                padding: 20px;
                text-align: center;
            }
            h1 { font-size: 48px; margin-bottom: 20px; }
            p { font-size: 20px; color: #666; }
            a { color: #007bff; text-decoration: none; }
        </style>
    </head>
    <body>
        <h1>🧠 Tacit</h1>
        <p>Your Personal Work Twin</p>
        <p style="margin-top: 40px;">
            <a href="/api/health">API Health Check</a> |
            <a href="/docs">API Documentation</a>
        </p>
    </body>
    </html>
    """


@app.get("/api/health")
async def health_check():
    """Health check endpoint"""
    stats = engine.get_stats()

    return {
        "status": "healthy",
        "version": "0.1.0",
        "user": config.user_name,
        "model": config.default_model,
        "stats": stats
    }


@app.on_event("startup")
async def startup_event():
    """Run on application startup"""
    logger.info(
        "tacit_startup",
        version="0.1.0",
        user=config.user_name,
        model=config.default_model
    )

    # Load user settings from DB into engine config
    from .db.database import UserSettingsDB, get_database as _get_db
    try:
        with _get_db().session_scope() as s:
            row = s.query(UserSettingsDB).filter_by(id="default").first()
            if row:
                if row.user_name:
                    config.user_name = row.user_name
                if row.user_role:
                    config.user_role = row.user_role
                if row.organization:
                    config.user_organization = row.organization
    except Exception:
        pass

    # Ensure data directories exist (absolute paths anchored to backend/)
    from .db.database import DEFAULT_DATA_DIR
    (DEFAULT_DATA_DIR / "uploads").mkdir(parents=True, exist_ok=True)
    (DEFAULT_DATA_DIR / "chroma").mkdir(parents=True, exist_ok=True)

    # Add user_id column to contexts table if missing (Postgres migration)
    _migrate_add_user_id_to_contexts()

    # Reset interrupted nodes back to pending (don't mark as error — they'll be retried)
    _recover_stuck_nodes()

    # Re-index any processed nodes that are missing from ChromaDB
    _reindex_missing_nodes()

    # Process any pending nodes in background (handles restart-interrupted nodes)
    import threading
    threading.Thread(target=_process_pending_nodes, daemon=True).start()


def _recover_stuck_nodes():
    """Reset nodes stuck in processing state back to pending so they get retried."""
    from .db.database import NodeDB, get_database
    try:
        with get_database().session_scope() as session:
            stuck = session.query(NodeDB).filter(NodeDB.status == "processing").all()
            if stuck:
                for node in stuck:
                    node.status = "pending"
                    node.error_message = None
                logger.info("recovered_stuck_nodes_to_pending", count=len(stuck))
    except Exception as e:
        logger.error("recover_stuck_nodes_failed", error=str(e))


def _process_pending_nodes():
    """Process all pending nodes in background after startup."""
    from .db.database import NodeDB, get_database
    import time
    time.sleep(3)  # Let server fully start first
    try:
        with get_database().session_scope() as session:
            pending = session.query(NodeDB).filter(NodeDB.status == "pending").all()
            node_ids = [n.id for n in pending]
        if node_ids:
            logger.info("processing_pending_nodes_on_startup", count=len(node_ids))
            for node_id in node_ids:
                try:
                    graph_service.process_node(node_id)
                except Exception as e:
                    logger.error("startup_process_node_failed", node_id=node_id, error=str(e))
    except Exception as e:
        logger.error("process_pending_nodes_failed", error=str(e))


def _migrate_add_user_id_to_contexts():
    """Add user_id column to contexts table if it doesn't exist yet."""
    from .db.database import get_database
    try:
        db = get_database()
        # Use raw SQL to add column if missing — works for both SQLite and Postgres
        with db.engine.connect() as conn:
            try:
                conn.execute(text("ALTER TABLE contexts ADD COLUMN user_id VARCHAR"))
                conn.commit()
                logger.info("migrated_contexts_user_id_column")
            except Exception:
                pass  # Column already exists — that's fine
    except Exception as e:
        logger.warning("migrate_contexts_user_id_failed", error=str(e))


def _reindex_missing_nodes():
    """Index nodes that are in SQLite (status=done) but missing from ChromaDB."""
    from .db.database import NodeDB
    db_session = engine.vector_service.client  # just to check count
    node_count_in_chroma = engine.vector_service.nodes_collection.count()

    sql_session = engine.db.get_session()
    try:
        done_nodes = sql_session.query(NodeDB).filter_by(status="done").all()
        if not done_nodes:
            return

        if node_count_in_chroma >= len(done_nodes):
            logger.info("vector_db_up_to_date", nodes=node_count_in_chroma)
            return

        logger.info(
            "reindexing_nodes",
            in_chroma=node_count_in_chroma,
            in_sqlite=len(done_nodes)
        )
        indexed = 0
        for node in done_nodes:
            try:
                # Check if already indexed
                existing = engine.vector_service.nodes_collection.get(ids=[node.id])
                if existing and existing.get("ids"):
                    continue
            except Exception:
                pass

            embed_text = f"{node.title or ''}\n{node.summary or ''}\n{(node.content or '')[:3000]}"
            try:
                engine.vector_service.add_node(
                    node_id=node.id,
                    content=embed_text,
                    metadata={
                        "title": node.title or "",
                        "type": node.type,
                        "url": node.url or "",
                        "tags": ", ".join(node.tags or []),
                    }
                )
                indexed += 1
            except Exception as e:
                logger.warning("reindex_node_failed", node_id=node.id, error=str(e))

        logger.info("reindex_complete", indexed=indexed)
    finally:
        sql_session.close()


@app.on_event("shutdown")
async def shutdown_event():
    """Run on application shutdown"""
    logger.info("tacit_shutdown")


if __name__ == "__main__":
    import uvicorn
    port = config.port
    host = config.host

    print("\n" + "="*60)
    print("  TACIT - Your Personal Work Twin")
    print("="*60)
    print(f"\n  🌐 Open your browser to: http://{host}:{port}")
    print(f"  📊 Health check: http://{host}:{port}/api/health")
    print(f"  📚 API docs: http://{host}:{port}/docs")
    print(f"  👤 User: {config.user_name}")
    print(f"  🤖 Model: {config.default_model}")
    print("\n  Press CTRL+C to stop\n")
    print("="*60 + "\n")

    uvicorn.run(app, host=host, port=port, log_level="info")
