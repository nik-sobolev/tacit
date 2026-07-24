"""Quick-add API — long-lived token for iOS Shortcuts and mobile capture"""

import uuid
import asyncio
import structlog
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request, Depends
from pydantic import BaseModel

from ..core.auth import get_current_user
from ..db.database import get_database, UserQuickTokenDB, NodeDB
from ..services.ingestion_service import detect_url_type, DEFERRED_EXTRACTION_TYPES

logger = structlog.get_logger()
router = APIRouter()

# Rate limiting for token validation attempts (prevent brute force)
TOKEN_ATTEMPTS = {}

# An Article page's rendered HTML has no legitimate reason to exceed this —
# caps the browser-extension endpoint against a runaway/misbehaving client.
MAX_HTML_BYTES = 5 * 1024 * 1024


class QuickAddHtmlRequest(BaseModel):
    token: str
    url: str
    html: str
    title: str = None


@router.get("/quickadd/token")
async def get_or_create_token(current_user: dict = Depends(get_current_user)):
    """Get (or create) the user's long-lived quick-add token."""
    db = get_database()
    token = None
    with db.session_scope() as session:
        existing = session.query(UserQuickTokenDB).filter_by(user_id=current_user["id"]).first()
        if existing:
            token = existing.token
        else:
            token = str(uuid.uuid4())
            session.add(UserQuickTokenDB(token=token, user_id=current_user["id"]))
    # Return after session commits
    return {"token": token, "user_id": current_user["id"]}


@router.post("/quickadd/rotate")
async def rotate_token(current_user: dict = Depends(get_current_user)):
    """Generate a new token (invalidates the old one)."""
    db = get_database()
    new_token = str(uuid.uuid4())
    with db.session_scope() as session:
        existing = session.query(UserQuickTokenDB).filter_by(user_id=current_user["id"]).first()
        if existing:
            existing.token = new_token
        else:
            session.add(UserQuickTokenDB(token=new_token, user_id=current_user["id"]))
    return {"token": new_token}


@router.post("/quickadd")
async def quick_add(request: Request, token: str, url: str = None):
    """Add a URL to the user's canvas. No Clerk auth — token-based. POST only.
    Rate-limited to prevent token brute forcing."""
    import time

    # Rate limiting: track failed attempts per IP address
    client_ip = request.client.host if request.client else "unknown"
    now = time.time()

    # Clean old attempts (older than 60 seconds)
    if client_ip in TOKEN_ATTEMPTS:
        TOKEN_ATTEMPTS[client_ip] = [t for t in TOKEN_ATTEMPTS[client_ip] if now - t < 60]

    # Check if rate limited (max 5 attempts per minute)
    if client_ip in TOKEN_ATTEMPTS and len(TOKEN_ATTEMPTS[client_ip]) >= 5:
        logger.warning("quickadd_rate_limited", client_ip=client_ip)
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")

    db = get_database()

    with db.session_scope() as session:
        row = session.query(UserQuickTokenDB).filter_by(token=token).first()
        if not row:
            # Track failed attempt
            if client_ip not in TOKEN_ATTEMPTS:
                TOKEN_ATTEMPTS[client_ip] = []
            TOKEN_ATTEMPTS[client_ip].append(now)
            raise HTTPException(status_code=403, detail="Invalid token")
        user_id = row.user_id

    # Get URL from query param or request body
    if not url:
        try:
            form = await request.form()
            url = form.get("url") or form.get("text")
        except Exception:
            pass
    if not url:
        try:
            body = await request.json()
            url = body.get("url") or body.get("text")
        except Exception:
            pass

    if not url or not url.startswith("http"):
        raise HTTPException(status_code=400, detail="url required")

    # Per-user duplicate check
    with db.session_scope() as session:
        existing = session.query(NodeDB).filter_by(url=url, user_id=user_id).first()
        if existing:
            return {"status": "duplicate", "node_id": existing.id}

    ingestion_service = request.app.state.ingestion_service
    graph_service = request.app.state.graph_service

    loop = asyncio.get_event_loop()
    # Pass user_id directly — no second-session patch needed
    node = await loop.run_in_executor(None, lambda: ingestion_service.ingest_url(url=url, user_id=user_id))

    def _process(node_id):
        try:
            if node.type in DEFERRED_EXTRACTION_TYPES:
                if not ingestion_service.extract_deferred(node_id, node.url, node.type):
                    return
            graph_service.process_node(node_id)
        except Exception as e:
            logger.error("quickadd_process_failed", node_id=node_id, error=str(e))

    loop.run_in_executor(None, _process, node.id)

    logger.info("quickadd_success", user_id=user_id, url=url)
    return {"status": "queued", "node_id": node.id}


@router.post("/quickadd/html")
async def quick_add_html(request: Request, body: QuickAddHtmlRequest):
    """Add a URL using HTML already rendered by the caller's own browser (the
    browser extension) instead of fetching it server-side. This is the path
    for pages that gate content behind a real login wall — X Articles are the
    motivating case: X has no unauthenticated access to them at all, and
    server-side session cookies keep getting invalidated by X's bot detection.
    A real, already-logged-in browser sidesteps that entirely since there's no
    server-side fetch to detect. Token-based auth, same as POST /quickadd."""
    import time

    client_ip = request.client.host if request.client else "unknown"
    now = time.time()
    if client_ip in TOKEN_ATTEMPTS:
        TOKEN_ATTEMPTS[client_ip] = [t for t in TOKEN_ATTEMPTS[client_ip] if now - t < 60]
    if client_ip in TOKEN_ATTEMPTS and len(TOKEN_ATTEMPTS[client_ip]) >= 5:
        logger.warning("quickadd_html_rate_limited", client_ip=client_ip)
        raise HTTPException(status_code=429, detail="Too many attempts. Try again later.")

    if len(body.html.encode("utf-8")) > MAX_HTML_BYTES:
        raise HTTPException(status_code=413, detail="HTML payload too large")

    db = get_database()

    with db.session_scope() as session:
        row = session.query(UserQuickTokenDB).filter_by(token=body.token).first()
        if not row:
            if client_ip not in TOKEN_ATTEMPTS:
                TOKEN_ATTEMPTS[client_ip] = []
            TOKEN_ATTEMPTS[client_ip].append(now)
            raise HTTPException(status_code=403, detail="Invalid token")
        user_id = row.user_id

    if not body.url or not body.url.startswith("http"):
        raise HTTPException(status_code=400, detail="url required")

    # Per-user duplicate/retry check — unlike POST /quickadd's plain duplicate
    # check, a failed node gets replaced so re-saving from the extension after
    # a prior failure actually retries instead of silently no-oping.
    with db.session_scope() as session:
        existing = session.query(NodeDB).filter_by(url=body.url, user_id=user_id).first()
        if existing:
            if existing.status != "error":
                return {"status": "duplicate", "node_id": existing.id}
            session.delete(existing)

    ingestion_service = request.app.state.ingestion_service
    graph_service = request.app.state.graph_service

    content_type = detect_url_type(body.url)
    loop = asyncio.get_event_loop()
    data = await loop.run_in_executor(
        None, lambda: ingestion_service.extract_from_html(body.url, body.html, body.title)
    )

    # extract_from_html() never raises — a page that yields nothing (captured
    # mid-load, a login/interstitial wall, an unsupported layout) just comes
    # back with content="". Left unchecked, that used to reach process_node()
    # and get "summarized" by the LLM as if the emptiness were the actual
    # content — see graph_service.process_node()'s empty-content guard, which
    # is the backstop; this is the same check applied at the source so the
    # node is created honestly-failed from the start instead of a transient
    # "processing" card that flips to error moments later.
    extraction_failed = not (data.get("content") or "").strip()

    node_id = str(uuid.uuid4())
    with db.session_scope() as session:
        session.add(NodeDB(
            id=node_id,
            user_id=user_id,
            type=content_type,
            title=data.get("title") or body.url[:200],
            content=data.get("content", ""),
            url=body.url,
            thumbnail_url=data.get("thumbnail_url"),
            canvas_x=100.0,
            canvas_y=100.0,
            status="error" if extraction_failed else "processing",
            error_message="Could not extract any content from this page" if extraction_failed else None,
            tags=[],
            node_meta=data.get("metadata", {}),
            created_at=datetime.utcnow(),
        ))

    if not extraction_failed:
        loop.run_in_executor(None, graph_service.process_node, node_id)

    logger.info("quickadd_html_success", user_id=user_id, url=body.url, type=content_type, extraction_failed=extraction_failed)
    return {"status": "error" if extraction_failed else "queued", "node_id": node_id}
