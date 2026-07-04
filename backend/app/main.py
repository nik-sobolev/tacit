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


@app.get("/t/{node_id}")
async def transcript_md(node_id: str):
    """Public transcript endpoint — returns raw markdown for a video node (like usetranscribe.io ?format=md).
    Keyed by UUID node_id (unguessable). No auth required so agents and browsers can fetch directly."""
    from fastapi.responses import PlainTextResponse
    from .db.database import get_database, NodeDB

    db = get_database()

    def _get(session):
        node = session.query(NodeDB).filter_by(id=node_id).first()
        if not node:
            return None
        meta = node.node_meta or {}
        return {
            "title": node.title,
            "url": node.url,
            "summary": node.summary,
            "content": node.content,
            "meta": meta,
        }

    try:
        data = db.run_with_retry(_get)
    except Exception as e:
        logger.error("transcript_md_db_error", node_id=node_id, error=str(e))
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse("# Error\n\nTemporary error — please try again.", status_code=503)

    if not data:
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse("# Not Found\n\nThis transcript does not exist.", status_code=404)

    meta = data["meta"]
    key_points = meta.get("key_points") or []
    segments = meta.get("transcript_segments") or []
    video_id = meta.get("video_id") or ""

    lines = []
    lines.append(f"# {data['title'] or 'Untitled'}")
    if data["url"]:
        lines.append(f"**Source:** {data['url']}")
    lines.append("")

    if data["summary"]:
        lines.append("## Summary")
        lines.append("")
        lines.append(data["summary"])
        lines.append("")

    if key_points:
        for point in key_points:
            lines.append(f"- {point}")
        lines.append("")

    if segments:
        lines.append("## Transcript")
        lines.append("")
        # Group fine-grained (~2s) segments into readable ~30s paragraphs.
        paras, cur = [], None
        for seg in segments:
            start = seg.get("start", 0)
            if cur is None or (start - cur["start"]) >= 30:
                cur = {"start": start, "texts": []}
                paras.append(cur)
            cur["texts"].append(seg.get("text", "").strip())
        for p in paras:
            secs = int(p["start"])
            mins = secs // 60
            s = secs % 60
            label = f"{mins}:{str(s).zfill(2)}"
            if video_id:
                ts = f"[[{label}]](https://www.youtube.com/watch?v={video_id}&t={secs}s)"
            else:
                ts = f"**[{label}]**"
            lines.append(f"{ts} {' '.join(p['texts'])}")
            lines.append("")
    elif data["content"]:
        lines.append("## Transcript")
        lines.append("")
        lines.append(data["content"])

    md = "\n".join(lines).strip() + "\n"
    return PlainTextResponse(md, media_type="text/plain; charset=utf-8")


@app.get("/", response_class=HTMLResponse)
async def root():
    """Serve the Tacit landing page"""
    html_file = frontend_path / "landing.html"
    if html_file.exists():
        return html_file.read_text()
    # Fallback if landing not ready
    return HTMLResponse('<script>window.location="/app"</script>')


@app.get("/app", response_class=HTMLResponse)
async def app_canvas():
    """Serve the main Tacit canvas app"""
    html_file = frontend_path / "index.html"
    if html_file.exists():
        return html_file.read_text()
    return HTMLResponse('<script>window.location="/"</script>')


@app.get("/sign-in", response_class=HTMLResponse)
@app.get("/sign-in/{rest:path}", response_class=HTMLResponse)
async def sign_in(rest: str = ""):
    """Serve branded Clerk sign-in — catches all sub-routes (verify, factor-one, etc.)"""
    html_file = frontend_path / "sign-in.html"
    if html_file.exists():
        return html_file.read_text()
    return HTMLResponse('<script>window.location="/app"</script>')


@app.get("/sign-up", response_class=HTMLResponse)
@app.get("/sign-up/{rest:path}", response_class=HTMLResponse)
async def sign_up(rest: str = ""):
    """Redirect all /sign-up routes to /sign-in — Clerk handles sign-up inline"""
    from fastapi.responses import RedirectResponse
    return RedirectResponse('/sign-in', status_code=301)


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


@app.get("/api/debug/youtube/{video_id}")
async def debug_youtube(video_id: str):
    """Diagnostic: test each transcript method exactly as the real pipeline does.

    Mirrors IngestionService: correct instance-based youtube-transcript-api,
    and yt-dlp calls that pass YOUTUBE_COOKIES_B64 cookies (no stale player_client).
    """
    import os, base64, traceback
    _webshare = bool(os.getenv("WEBSHARE_PROXY_USERNAME", "").strip() and os.getenv("WEBSHARE_PROXY_PASSWORD", "").strip())
    results = {
        "code_version": "debug-v4-webshare",
        "cookies_present": bool(os.getenv("YOUTUBE_COOKIES_B64")),
        "proxy_mode": "webshare" if _webshare else ("generic" if os.getenv("YOUTUBE_PROXY_URL", "").strip() else "none"),
    }
    url = f"https://www.youtube.com/watch?v={video_id}"

    # Test 1: youtube-transcript-api (0.6.x/1.x instance API — matches real code)
    try:
        api = ingestion_service._transcript_api()
        try:
            raw = api.fetch(video_id, languages=["en", "en-US", "en-GB"])
        except Exception:
            raw = api.fetch(video_id)
        entries = list(raw)
        results["transcript_api"] = {"ok": True, "segments": len(entries)}
    except Exception as e:
        results["transcript_api"] = {"ok": False, "error": str(e)[:400]}

    # Test 2: yt-dlp metadata WITH cookies (matches real _get_video_metadata)
    try:
        meta = ingestion_service._get_video_metadata(url)
        if meta and meta.get("title"):
            results["yt_dlp_metadata"] = {"ok": True, "title": meta.get("title"), "duration": meta.get("duration")}
        else:
            results["yt_dlp_metadata"] = {"ok": False, "error": "empty metadata (see logs)"}
    except Exception as e:
        results["yt_dlp_metadata"] = {"ok": False, "error": str(e)[:400], "trace": traceback.format_exc()[-400:]}

    # Test 3: yt-dlp subtitles WITH cookies (matches real _get_yt_dlp_subtitles)
    try:
        subs = ingestion_service._get_yt_dlp_subtitles(url) or ""
        results["yt_dlp_subtitles"] = {"ok": bool(subs), "chars": len(subs)}
    except Exception as e:
        results["yt_dlp_subtitles"] = {"ok": False, "error": str(e)[:400]}

    results["groq_key"] = {"present": bool(os.getenv("GROQ_API_KEY"))}
    results["ffmpeg"] = {"present": os.system("ffmpeg -version > /dev/null 2>&1") == 0}
    return results


@app.on_event("startup")
async def startup_event():
    """Run on application startup"""
    logger.info(
        "tacit_startup",
        version="0.1.0",
        user=config.user_name,
        model=config.default_model
    )

    # User settings are now per-user (keyed by Clerk user_id), not loaded at startup

    # Ensure data directories exist (absolute paths anchored to backend/)
    from .db.database import DEFAULT_DATA_DIR
    (DEFAULT_DATA_DIR / "uploads").mkdir(parents=True, exist_ok=True)
    (DEFAULT_DATA_DIR / "chroma").mkdir(parents=True, exist_ok=True)

    # Add user_id column to contexts table if missing (Postgres migration)
    _migrate_add_user_id_to_contexts()

    # Reset interrupted nodes back to pending (don't mark as error — they'll be retried)
    _recover_stuck_nodes()

    # Re-index nodes missing from ChromaDB and process pending — both in background
    # to avoid OOM at startup (ChromaDB downloads 79MB ONNX model on first use)
    import threading
    threading.Thread(target=_reindex_missing_nodes, daemon=True).start()
    threading.Thread(target=_process_pending_nodes, daemon=True).start()
    threading.Thread(target=_watchdog_stuck_nodes, daemon=True).start()


def _watchdog_stuck_nodes():
    """Periodically reset nodes stuck in processing for >15 min to error state."""
    from .db.database import NodeDB, get_database
    from datetime import timedelta
    import time
    from datetime import datetime
    time.sleep(60)  # Initial delay before first check
    while True:
        try:
            cutoff = datetime.utcnow() - timedelta(minutes=15)
            with get_database().session_scope() as session:
                stuck = session.query(NodeDB).filter(
                    NodeDB.status == "processing",
                    NodeDB.created_at < cutoff
                ).all()
                for node in stuck:
                    node.status = "error"
                    node.error_message = "Processing timed out — re-add the URL to retry"
                    logger.warning("node_processing_timeout", node_id=node.id, title=node.title)
                if stuck:
                    logger.info("watchdog_reset_stuck_nodes", count=len(stuck))
        except Exception as e:
            logger.error("watchdog_error", error=str(e))
        time.sleep(300)  # Check every 5 minutes


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
    """Add user_id column to any tables missing it, and add documents.user_id."""
    from .db.database import get_database
    try:
        db = get_database()
        with db.engine.connect() as conn:
            for table, col in [
                ("nodes", "user_id VARCHAR"),
                ("contexts", "user_id VARCHAR"),
                ("documents", "user_id VARCHAR"),
            ]:
                try:
                    conn.execute(text(f"ALTER TABLE {table} ADD COLUMN {col}"))
                    conn.commit()
                    logger.info("migrated_column_added", table=table, col=col)
                except Exception:
                    pass  # Column already exists
    except Exception as e:
        logger.warning("migrate_schema_failed", error=str(e))


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
