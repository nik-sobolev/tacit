"""Tacit FastAPI application"""
# Force redeploy

import html
import json
import httpx
import structlog
from fastapi import FastAPI, Request, Depends, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse, PlainTextResponse, Response
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
    gemini_api_key=config.gemini_api_key,
    summarization_provider=config.summarization_provider,
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


# LLM crawlers (training/retrieval) that check for an explicit named block
# before crawling, rather than falling back to "User-agent: *". Per the REP
# spec, a crawler that finds a block matching its own name uses ONLY that
# block — it does NOT also inherit the "*" rules — so each bot needs its own
# copy of the same allow/disallow rules, not just to appear in a comment.
LLM_CRAWLER_AGENTS = [
    "GPTBot",         # OpenAI
    "ClaudeBot",      # Anthropic — crawling
    "anthropic-ai",   # Anthropic — training
    "Google-Extended",  # Gemini/Bard training
    "PerplexityBot",
    "CCBot",          # Common Crawl — feeds many third-party LLM training sets
]


SOCIAL_PREVIEW_AGENTS = [
    "LinkedInBot",        # LinkedIn link-preview card fetch
    "facebookexternalhit",  # Facebook/Messenger
    "Twitterbot",
    "Slackbot",
    "TelegramBot",
    "WhatsApp",
    "Discordbot",
]


@app.get("/robots.txt", response_class=PlainTextResponse)
async def robots_txt():
    """Allow crawling of the landing page and public /yt transcript pages (our
    usetranscribe.io-style SEO surface). /s/{node_id} and /t/{node_id} are
    unguessable-by-design private share links (see public_node_transcript /
    transcript_md docstrings) and must stay out of robots/sitemap so we don't
    publish an index of them. Named LLM crawler blocks (LLM_CRAWLER_AGENTS)
    get the identical rule set — see that constant's docstring for why.

    SOCIAL_PREVIEW_AGENTS get a narrower carve-out: Allow: /s/ but not /t/
    (markdown, no OG tags to unfurl) so that sharing a /s/{node_id} link to
    LinkedIn/X/Facebook/Slack/etc. still renders a link-preview card. These
    bots fetch a single named URL a user just shared, not an index of
    unguessable ones, so this doesn't reintroduce the "don't publish an
    index" concern the blanket Disallow: /s/ above exists for."""
    rule_block = [
        "Allow: /yt/",
        "Disallow: /api/",
        "Disallow: /app",
        "Disallow: /sign-in",
        "Disallow: /sign-up",
        "Disallow: /share",
        "Disallow: /s/",
        "Disallow: /t/",
        "Disallow: /uploads/",
        "",
    ]
    lines = ["User-agent: *", *rule_block]
    for agent in LLM_CRAWLER_AGENTS:
        lines += [f"User-agent: {agent}", *rule_block]
    for agent in SOCIAL_PREVIEW_AGENTS:
        lines += [
            f"User-agent: {agent}",
            "Allow: /yt/",
            "Allow: /s/",
            "Disallow: /api/",
            "Disallow: /app",
            "Disallow: /sign-in",
            "Disallow: /sign-up",
            "Disallow: /share",
            "Disallow: /t/",
            "Disallow: /uploads/",
            "",
        ]
    lines += [
        "Sitemap: https://www.trytacit.app/sitemap.xml",
        "# AI agents (API access): see https://www.trytacit.app/AGENTS.md",
        "# LLM summary: see https://www.trytacit.app/llms.txt",
    ]
    return PlainTextResponse("\n".join(lines))


@app.get("/privacy", response_class=HTMLResponse)
async def privacy_policy():
    """Privacy policy for the 'Save to Tacit' Chrome extension (browser-extension/).
    Required by the Chrome Web Store for any listing requesting host permissions
    or handling user data — this is the URL that goes in that listing's privacy
    policy field."""
    return HTMLResponse("""<!DOCTYPE html>
<html><head><meta charset="utf-8"><title>Privacy Policy — Save to Tacit</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif; max-width: 640px; margin: 60px auto; padding: 0 20px; line-height: 1.6; color: #222; }
  h1 { font-size: 22px; } h2 { font-size: 16px; margin-top: 32px; }
</style></head>
<body>
<h1>Privacy Policy — Save to Tacit (browser extension)</h1>
<p>This page covers the "Save to Tacit" Chrome extension specifically. For the main Tacit app's privacy practices, see your account settings.</p>

<h2>What it captures</h2>
<p>Only when you click "Save this page" in the extension popup, it reads the current tab's page content (the rendered HTML) and URL. It does not run in the background, does not read any other tabs, and does not capture anything without you clicking the button.</p>

<h2>What it's used for</h2>
<p>The captured page content and URL are sent to Tacit's API and used solely to create a saved item in your own Tacit account, tied to your personal access token. This is what lets the extension save pages that require you to be logged in to view — like X Articles — since it uses the page exactly as your browser already renders it, rather than Tacit fetching the page itself.</p>

<h2>What it's not used for</h2>
<p>Captured content is never sold, shared with third parties, or used for advertising. It isn't used to train any model beyond the same AI processing (summarization, tagging) already applied to anything else you save in Tacit.</p>

<h2>Where it's sent</h2>
<p>Directly to Tacit's own API (trytacit.app) over HTTPS. No other destination.</p>

<h2>Questions</h2>
<p>Contact <a href="mailto:support@trytacit.app">support@trytacit.app</a>.</p>
</body></html>""")


@app.get("/llms.txt")
async def llms_txt():
    """llms.txt — emerging convention (llmstxt.org) for a plain-language product
    summary aimed at LLM training/retrieval crawlers, distinct from AGENTS.md
    (which documents the API for agents making live requests, not a training
    corpus)."""
    content = """# Tacit

> Tacit is a personal knowledge canvas: drop in any URL — a YouTube video, article, tweet, or PDF — and it transcribes, summarizes, and connects it to everything else you've saved, so you can ask questions across everything you've read and watched.

Tacit runs in the browser and installs as a PWA. Supported sources: YouTube, TikTok, articles, tweets, and PDFs. Every saved item lands on an infinite visual canvas and is automatically linked to related items already in the library, based on meaning rather than folders or tags.

## Docs

- [For AI agents](https://www.trytacit.app/AGENTS.md): endpoints for programmatic access to public transcript pages
- [Sitemap](https://www.trytacit.app/sitemap.xml): public YouTube transcript pages Tacit has indexed

## Examples

- [Public YouTube transcripts](https://www.trytacit.app/yt/{video_id}): human-readable transcript, AI summary, and key points for a YouTube video Tacit has processed — append `?format=md` for raw markdown
"""
    return Response(content, media_type="text/plain")


# Sitemaps are capped at 50k URLs per the sitemaps.org spec.
SITEMAP_MAX_URLS = 50_000


@app.get("/sitemap.xml")
async def sitemap_xml():
    """Dynamic sitemap: landing page plus every publicly indexable /yt transcript
    page (one entry per distinct YouTube video_id, newest completed ingestion).
    Capped at SITEMAP_MAX_URLS and cached for an hour — this scans the nodes
    table on every miss, and crawlers refetch sitemaps frequently."""
    from .db.database import get_database, NodeDB

    db = get_database()

    def _get(session):
        nodes = (
            session.query(NodeDB)
            .filter(NodeDB.type == "youtube", NodeDB.status == "done")
            .order_by(NodeDB.created_at.desc())
            .limit(SITEMAP_MAX_URLS * 2)  # headroom for per-video_id dedup below
            .all()
        )
        seen, entries = set(), []
        for node in nodes:
            video_id = (node.node_meta or {}).get("video_id")
            if not video_id or video_id in seen:
                continue
            seen.add(video_id)
            entries.append((video_id, node.title, node.processed_at or node.created_at))
            if len(entries) >= SITEMAP_MAX_URLS:
                break
        return entries

    try:
        entries = db.run_with_retry(_get)
    except Exception as e:
        logger.error("sitemap_db_error", error=str(e))
        entries = []

    parts = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">',
        "<url><loc>https://www.trytacit.app/</loc><changefreq>daily</changefreq><priority>1.0</priority></url>",
    ]
    for video_id, title, dt in entries:
        loc = html.escape(f"https://www.trytacit.app/yt/{video_id}/{_slugify(title)}")
        lastmod = f"<lastmod>{dt.strftime('%Y-%m-%d')}</lastmod>" if dt else ""
        parts.append(f"<url><loc>{loc}</loc>{lastmod}</url>")
    parts.append("</urlset>")

    return Response(
        "\n".join(parts),
        media_type="application/xml",
        headers={"Cache-Control": "public, max-age=3600"},
    )


@app.get("/AGENTS.md")
async def agents_md():
    """Machine-readable API summary for AI agents/LLM crawlers — same intent as
    usetranscribe.io's AGENTS.md. Only documents endpoints that are actually
    public and unauthenticated; does not enumerate or link specific content
    (that's what /sitemap.xml is for, and it deliberately excludes private
    per-item share links — see robots_txt docstring)."""
    content = """# Summary of Tacit (trytacit.app) for AI Agents

**Service**: Drop any URL — YouTube, TikTok, articles, tweets, PDFs — into a personal knowledge canvas. Tacit transcribes it, summarizes it, and connects it to related saved content.

## Public Endpoints

1. **YouTube transcript, HTML** (`GET /yt/{video_id}`) — Human-readable page: title, summary, key points, full timestamped transcript with clickable links back into the video.

2. **YouTube transcript, Markdown** (`GET /yt/{video_id}?format=md`) — Same content as raw markdown, no HTML/CSS. Prefer this over scraping the HTML page.

3. **Sitemap** (`GET /sitemap.xml`) — Every publicly indexed YouTube transcript page, one entry per video.

## Notes

- Canonical host is `https://www.trytacit.app` (apex `trytacit.app` redirects to it).
- `/yt/{video_id}` is keyed by the public YouTube video ID (from `youtube.com/watch?v={video_id}`) — the same video resolves to the same page regardless of which Tacit user ingested it, since the source video is already public.
- No API key required; no rate limit on these read endpoints.
- Content that is *not* derived from an already-public YouTube video — personal web saves, PDFs, tweets a user dropped into their own canvas — is served behind unguessable per-item links (`/s/{id}`, `/t/{id}`) and is intentionally not linked from this file, the sitemap, or robots.txt. Tacit does not publish an index of user-saved content.
- Tacit is a product for humans to save and connect content, not a transcription API for third parties — there is no bulk/programmatic ingestion endpoint.
"""
    return Response(content, media_type="text/markdown")


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


@app.get("/api/graph/public/{token}")
async def public_graph_by_token(token: str):
    """Token-scoped graph data for the anonymous /share/{token} viewer — deliberately
    outside graph_api's router (which has auth_dep applied to every route on it) since
    this is a public, unauthenticated read keyed by the share token instead of a Clerk JWT."""
    from .db.database import ShareTokenDB, get_database
    db = get_database()

    def _resolve(session):
        row = session.query(ShareTokenDB).filter_by(token=token).first()
        if not row or row.revoked:
            return None
        return row.user_id

    try:
        user_id = db.run_with_retry(_resolve)
    except Exception as e:
        logger.error("public_graph_by_token_db_error", error=str(e))
        raise HTTPException(status_code=503, detail="Temporary error — please refresh in a moment")

    if not user_id:
        raise HTTPException(status_code=404, detail="Link not found or revoked")

    return graph_service.get_graph(user_id=user_id)


def _group_segments(segments: list) -> list:
    """Group fine-grained (~2s) caption segments into natural paragraphs: break on
    the ">>" speaker-change marker YouTube embeds in captions, with a sentence-
    boundary time fallback for long monologues / solo videos. Shared by the
    markdown (/t) and HTML (/yt, /s) transcript renderers — don't re-copy this."""
    paras, cur = [], None
    for seg in segments:
        start = seg.get("start", 0)
        text = seg.get("text", "").strip()
        if not text:
            continue
        elapsed = (start - cur["start"]) if cur else float("inf")
        last = cur["texts"][-1] if (cur and cur["texts"]) else ""
        sentence_end = last[-1:] in (".", "?", "!", '"')
        speaker_break = ">>" in text and elapsed >= 25
        fallback_break = elapsed >= 60 and sentence_end
        if cur is None or speaker_break or fallback_break:
            cur = {"start": start, "texts": []}
            paras.append(cur)
        cur["texts"].append(text)
    return [{"start": p["start"], "text": " ".join(p["texts"])} for p in paras]


def _slugify(title: str) -> str:
    """URL-friendly slug for the pretty share URLs (/yt/{id}/{slug}, /s/{id}/{slug}).
    Decorative only — routes resolve by id, the slug is never looked up."""
    import re
    s = re.sub(r"[^a-z0-9]+", "-", (title or "").lower()).strip("-")
    return s[:60] or "video"


def build_transcript_html(data: dict, canonical_url: str, md_url: str) -> str:
    """Public, usetranscribe.io-style HTML transcript page: video/thumbnail, title,
    summary, key points, timestamped transcript, OG/Twitter meta for rich link
    previews, and a social-share row. Every dynamic field is html.escape()'d before
    interpolation — title/summary/transcript/uploader originate from YouTube
    captions/metadata and are untrusted input (XSS guard). md_url is this same
    page's raw-markdown counterpart (build_transcript_md) — linked in <head> for
    crawlers and in the footer for humans/agents, alongside /AGENTS.md."""
    from urllib.parse import quote

    meta = data["meta"]
    raw_title = data["title"] or "Untitled"
    title = html.escape(raw_title)
    summary = html.escape(data["summary"] or "")
    key_points = [html.escape(p) for p in (meta.get("key_points") or [])]
    segments = meta.get("transcript_segments") or []
    video_id = meta.get("video_id") or ""
    uploader = html.escape(meta.get("uploader") or "")
    source_url = data["url"] or ""
    thumb = data.get("thumbnail_url") or (
        f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg" if video_id else ""
    )

    desc_raw = (data["summary"] or "").strip()
    og_description = html.escape((desc_raw[:200] + "…") if len(desc_raw) > 200 else desc_raw)
    md_url_esc = html.escape(md_url)

    if video_id:
        media_html = (
            f'<div class="tc-embed"><iframe src="https://www.youtube.com/embed/{html.escape(video_id)}" '
            f'title="{title}" frameborder="0" '
            f'allow="accelerometer; autoplay; clipboard-write; encrypted-media; gyroscope; picture-in-picture" '
            f'allowfullscreen loading="lazy"></iframe></div>'
        )
    elif thumb:
        media_html = f'<img class="tc-thumb" src="{html.escape(thumb)}" alt="" loading="lazy" />'
    else:
        media_html = ""

    key_points_html = ""
    if key_points:
        items = "".join(f"<li>{p}</li>" for p in key_points)
        key_points_html = f'<ul class="tc-key-points">{items}</ul>'

    paras = _group_segments(segments)
    transcript_html = ""
    if paras:
        rows = []
        for p in paras:
            secs = int(p["start"])
            mins, s = secs // 60, secs % 60
            label = f"{mins}:{str(s).zfill(2)}"
            text = html.escape(p["text"])
            if video_id:
                ts = (
                    f'<a class="tc-ts" href="https://www.youtube.com/watch?v={html.escape(video_id)}&t={secs}s" '
                    f'target="_blank" rel="noopener">{label}</a>'
                )
            else:
                ts = f'<span class="tc-ts">{label}</span>'
            rows.append(f'<p class="tc-para">{ts} {text}</p>')
        transcript_html = "".join(rows)
    elif data["content"]:
        transcript_html = f'<p class="tc-para">{html.escape(data["content"])}</p>'

    share_targets = [
        ("X", f"https://twitter.com/intent/tweet?text={quote(raw_title)}&url={quote(canonical_url)}"),
        ("LinkedIn", f"https://www.linkedin.com/sharing/share-offsite/?url={quote(canonical_url)}"),
        ("Facebook", f"https://www.facebook.com/sharer/sharer.php?u={quote(canonical_url)}"),
        ("WhatsApp", f"https://wa.me/?text={quote(raw_title + ' ' + canonical_url)}"),
        ("Reddit", f"https://www.reddit.com/submit?url={quote(canonical_url)}&title={quote(raw_title)}"),
    ]
    share_html = "".join(
        f'<a class="tc-share-btn" href="{u}" target="_blank" rel="noopener">{n}</a>' for n, u in share_targets
    )

    source_line = (
        f'<a class="tc-source" href="{html.escape(source_url)}" target="_blank" rel="noopener">{html.escape(source_url)}</a>'
        if source_url else ""
    )
    uploader_line = f'<span class="tc-uploader">{uploader}</span>' if uploader else ""

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} — Tacit</title>
<meta name="description" content="{og_description}">
<link rel="canonical" href="{html.escape(canonical_url)}">
<link rel="alternate" type="text/markdown" href="{md_url_esc}">
<meta property="og:title" content="{title}">
<meta property="og:description" content="{og_description}">
<meta property="og:image" content="{html.escape(thumb)}">
<meta property="og:url" content="{html.escape(canonical_url)}">
<meta property="og:type" content="article">
<meta name="twitter:card" content="summary_large_image">
<meta name="twitter:title" content="{title}">
<meta name="twitter:description" content="{og_description}">
<meta name="twitter:image" content="{html.escape(thumb)}">
<link href="https://fonts.googleapis.com/css2?family=Newsreader:opsz,wght@6..72,400;6..72,600&family=Hanken+Grotesk:wght@400;500;600&family=IBM+Plex+Mono:wght@400;500&display=swap" rel="stylesheet">
<style>
:root{{--primary:#C26C33;--bg:#15171C;--surface:#13151A;--text:#ECE6D6;--text-secondary:#C7C2B2;--border:rgba(236,230,214,0.12);}}
*{{box-sizing:border-box;}}
body{{margin:0;background:var(--bg);color:var(--text);font-family:'Hanken Grotesk',sans-serif;line-height:1.6;}}
.tc-wrap{{max-width:720px;margin:0 auto;padding:32px 20px 80px;}}
.tc-embed{{position:relative;padding-top:56.25%;border-radius:12px;overflow:hidden;margin-bottom:24px;background:var(--surface);}}
.tc-embed iframe{{position:absolute;inset:0;width:100%;height:100%;border:0;}}
.tc-thumb{{width:100%;border-radius:12px;margin-bottom:24px;display:block;}}
h1{{font-family:'Newsreader',serif;font-size:28px;font-weight:600;margin:0 0 8px;}}
.tc-meta{{font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--text-secondary);margin-bottom:24px;word-break:break-all;}}
.tc-source{{color:var(--primary);text-decoration:none;}}
.tc-uploader{{margin-right:12px;}}
h2{{font-family:'Newsreader',serif;font-size:18px;margin:32px 0 12px;}}
.tc-key-points{{padding-left:20px;color:var(--text-secondary);}}
.tc-key-points li{{margin-bottom:6px;}}
.tc-para{{margin:0 0 16px;color:var(--text-secondary);}}
.tc-ts{{font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--primary);text-decoration:none;margin-right:8px;}}
.tc-share{{display:flex;flex-wrap:wrap;gap:8px;margin:8px 0 28px;padding:16px 0;border-top:1px solid var(--border);border-bottom:1px solid var(--border);}}
.tc-share-btn{{font-family:'IBM Plex Mono',monospace;font-size:11px;text-transform:uppercase;letter-spacing:0.04em;color:var(--text);border:1px solid var(--border);border-radius:6px;padding:8px 12px;text-decoration:none;}}
.tc-share-btn:hover{{border-color:var(--primary);color:var(--primary);}}
.tc-cta{{text-align:center;font-family:'IBM Plex Mono',monospace;font-size:12px;color:var(--text-secondary);margin-top:40px;}}
.tc-cta a{{color:var(--primary);text-decoration:none;}}
.tc-agents{{text-align:center;font-family:'IBM Plex Mono',monospace;font-size:11px;color:var(--text-secondary);opacity:0.65;margin-top:8px;}}
.tc-agents a{{color:var(--text-secondary);text-decoration:none;border-bottom:1px solid var(--border);}}
</style>
</head>
<body>
<div class="tc-wrap">
{media_html}
<h1>{title}</h1>
<div class="tc-meta">{uploader_line}{source_line}</div>
<div class="tc-share">{share_html}</div>
{f'<h2>Summary</h2><p class="tc-para">{summary}</p>' if summary else ''}
{f'<h2>Key Points</h2>{key_points_html}' if key_points_html else ''}
{f'<h2>Transcript</h2>{transcript_html}' if transcript_html else ''}
<div class="tc-cta">Transcribed with <a href="https://www.trytacit.app">Tacit</a></div>
<div class="tc-agents"><a href="{md_url_esc}">View as markdown</a> · <a href="/AGENTS.md">For AI agents</a></div>
</div>
<script>
// LinkedIn's share-offsite endpoint only accepts a `url` param — it dropped
// support for prefilling post text years ago, so its composer always opens
// blank. Copy the title to the clipboard as the best available workaround.
document.querySelectorAll('.tc-share-btn').forEach(function(a) {{
    if (a.href.indexOf('linkedin.com') === -1) return;
    a.addEventListener('click', function() {{
        navigator.clipboard?.writeText({json.dumps(raw_title)}).then(function() {{
            var original = a.textContent;
            a.textContent = 'Copied caption!';
            setTimeout(function() {{ a.textContent = original; }}, 2500);
        }}).catch(function() {{}});
    }});
}});
</script>
</body>
</html>"""


def build_transcript_md(data: dict) -> str:
    """Public, agent-facing markdown transcript body — same underlying data as
    build_transcript_html, minus HTML/CSS. Shared by /t/{node_id} (private
    UUID-keyed nodes) and /yt/{video_id}?format=md (public YouTube pages), so
    agents get one consistent markdown shape wherever they fetch it."""
    meta = data["meta"]
    key_points = meta.get("key_points") or []
    segments = meta.get("transcript_segments") or []
    video_id = meta.get("video_id") or ""

    lines = [f"# {data['title'] or 'Untitled'}"]
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
        for p in _group_segments(segments):
            secs = int(p["start"])
            mins = secs // 60
            s = secs % 60
            label = f"{mins}:{str(s).zfill(2)}"
            if video_id:
                ts = f"[[{label}]](https://www.youtube.com/watch?v={video_id}&t={secs}s)"
            else:
                ts = f"**[{label}]**"
            lines.append(f"{ts} {p['text']}")
            lines.append("")
    elif data["content"]:
        lines.append("## Transcript")
        lines.append("")
        lines.append(data["content"])

    lines.append("")
    lines.append("---")
    lines.append("*Transcribed with [Tacit](https://www.trytacit.app)*")

    return "\n".join(lines).strip() + "\n"


@app.get("/t/{node_id}")
async def transcript_md(node_id: str):
    """Public transcript endpoint — returns raw markdown for a video node (like usetranscribe.io ?format=md).
    Keyed by UUID node_id (unguessable). No auth required so agents and browsers can fetch directly."""
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
        return PlainTextResponse("# Error\n\nTemporary error — please try again.", status_code=503)

    if not data:
        return PlainTextResponse("# Not Found\n\nThis transcript does not exist.", status_code=404)

    return PlainTextResponse(build_transcript_md(data), media_type="text/markdown")


@app.api_route("/yt/{video_id}", methods=["GET", "HEAD"])
@app.api_route("/yt/{video_id}/{slug}", methods=["GET", "HEAD"])
async def public_youtube_transcript(video_id: str, slug: str = "", format: str = "html"):
    """Public, usetranscribe.io-style transcript page keyed by YouTube video_id
    (publicly known — the video itself is already public, so this is intentionally
    enumerable). Tacit is multi-tenant, so a video_id can map to multiple nodes
    across users; resolves to the newest completed ingestion of that video — a
    valid transcript of the video, not necessarily "your" ingestion of it.

    ?format=md returns the same content as raw markdown (build_transcript_md) —
    the agent-facing counterpart to the human-facing HTML page, so agents don't
    have to scrape rendered HTML for something already public."""
    from .db.database import get_database, NodeDB

    db = get_database()

    def _get(session):
        # node_meta is a JSON blob; JSON-path filtering isn't portable between
        # SQLite and Postgres, so pre-filter candidates with a plain substring
        # match on the (indexed-searchable) url column, then confirm the exact
        # video_id in Python.
        candidates = (
            session.query(NodeDB)
            .filter(NodeDB.type == "youtube", NodeDB.url.contains(video_id))
            .order_by(NodeDB.created_at.desc())
            .all()
        )
        matches = [n for n in candidates if (n.node_meta or {}).get("video_id") == video_id]
        if not matches:
            return None
        # Prefer the newest completed ingestion (preserves "valid transcript of
        # the video" behavior when multiple users have ingested it); only fall
        # back to a non-done match so a freshly-shared link can show a "still
        # processing"/"couldn't be processed" page instead of a bare 404.
        node = next((n for n in matches if n.status == "done"), matches[0])
        return {
            "title": node.title,
            "url": node.url,
            "summary": node.summary,
            "content": node.content,
            "thumbnail_url": node.thumbnail_url,
            "meta": node.node_meta or {},
            "status": node.status,
        }

    try:
        data = db.run_with_retry(_get)
    except Exception as e:
        logger.error("public_youtube_transcript_db_error", video_id=video_id, error=str(e))
        if format == "md":
            return PlainTextResponse("# Error\n\nTemporary error — please try again.", status_code=503)
        return HTMLResponse(
            "<h1 style='font-family:sans-serif;padding:40px'>Temporary error — please try again.</h1>",
            status_code=503,
        )

    if not data:
        if format == "md":
            return PlainTextResponse("# Not Found\n\nThis transcript does not exist.", status_code=404)
        return HTMLResponse(
            "<h1 style='font-family:sans-serif;padding:40px'>This transcript does not exist.</h1>",
            status_code=404,
        )

    if data["status"] in ("pending", "processing"):
        if format == "md":
            return PlainTextResponse(
                "# Still Processing\n\nThis transcript is still being processed — check back in a minute.",
                status_code=200,
            )
        return HTMLResponse(
            "<h1 style='font-family:sans-serif;padding:40px'>This is still being processed — check back in a minute.</h1>",
            status_code=200,
        )
    if data["status"] == "error":
        if format == "md":
            return PlainTextResponse("# Not Available\n\nThis item couldn't be processed.", status_code=404)
        return HTMLResponse(
            "<h1 style='font-family:sans-serif;padding:40px'>This item couldn't be processed.</h1>",
            status_code=404,
        )

    if format == "md":
        return PlainTextResponse(build_transcript_md(data), media_type="text/markdown")

    canonical_url = f"https://www.trytacit.app/yt/{video_id}/{_slugify(data['title'])}"
    md_url = f"https://www.trytacit.app/yt/{video_id}?format=md"
    return HTMLResponse(build_transcript_html(data, canonical_url, md_url))


@app.api_route("/s/{node_id}", methods=["GET", "HEAD"], response_class=HTMLResponse)
@app.api_route("/s/{node_id}/{slug}", methods=["GET", "HEAD"], response_class=HTMLResponse)
async def public_node_transcript(node_id: str, slug: str = ""):
    """Public transcript page for non-YouTube nodes (TikTok/Instagram/web pages),
    keyed by the unguessable node UUID — same privacy model as /t/{node_id}."""
    from .db.database import get_database, NodeDB

    db = get_database()

    def _get(session):
        node = session.query(NodeDB).filter_by(id=node_id).first()
        if not node:
            return None
        return {
            "title": node.title,
            "url": node.url,
            "summary": node.summary,
            "content": node.content,
            "thumbnail_url": node.thumbnail_url,
            "meta": node.node_meta or {},
            "status": node.status,
        }

    try:
        data = db.run_with_retry(_get)
    except Exception as e:
        logger.error("public_node_transcript_db_error", node_id=node_id, error=str(e))
        return HTMLResponse(
            "<h1 style='font-family:sans-serif;padding:40px'>Temporary error — please try again.</h1>",
            status_code=503,
        )

    if not data:
        return HTMLResponse(
            "<h1 style='font-family:sans-serif;padding:40px'>This transcript does not exist.</h1>",
            status_code=404,
        )

    if data["status"] in ("pending", "processing"):
        return HTMLResponse(
            "<h1 style='font-family:sans-serif;padding:40px'>This is still being processed — check back in a minute.</h1>",
            status_code=200,
        )
    if data["status"] == "error":
        return HTMLResponse(
            "<h1 style='font-family:sans-serif;padding:40px'>This item couldn't be processed.</h1>",
            status_code=404,
        )

    canonical_url = f"https://www.trytacit.app/s/{node_id}/{_slugify(data['title'])}"
    md_url = f"https://www.trytacit.app/t/{node_id}"
    return HTMLResponse(build_transcript_html(data, canonical_url, md_url))


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
    """Serve branded Clerk sign-up — catches all sub-routes (verify, continue, etc.)"""
    html_file = frontend_path / "sign-up.html"
    if html_file.exists():
        return html_file.read_text()
    return HTMLResponse('<script>window.location="/sign-in"</script>')


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


@app.get("/api/debug/webpage")
async def debug_webpage(url: str):
    """Diagnostic: test each webpage extraction step exactly as the real pipeline
    does, isolating whether Playwright's Chromium binary is actually launchable
    in this environment (as opposed to a per-site extraction issue). Mirrors
    /api/debug/tweet and /api/debug/youtube/{video_id}. Runs in a thread
    executor — Playwright's sync API can't run on an already-running asyncio
    loop, same reason those two debug endpoints do the same. Also reports
    agent_config: which summarization provider/API keys are configured, since
    "Processing failed" on a node can originate from graph_service's LLM
    summarization step (which runs after extraction) rather than extraction
    itself — this reports config presence only, no LLM call is made."""
    import asyncio, os, traceback
    results = {
        "agent_config": {
            "summarization_provider": graph_service.summarization_provider,
            "gemini_key_present": bool(os.getenv("GEMINI_API_KEY", "").strip()),
            "anthropic_key_present": bool(os.getenv("ANTHROPIC_API_KEY", "").strip()),
            "client_initialized": graph_service.client is not None,
        },
    }

    def _run_debug():
        # Test 1: chromium launch in isolation — the thing most likely to be
        # broken in prod if the Dockerfile's `playwright install chromium
        # --with-deps` step didn't run (e.g. Render building via a native
        # buildpack instead of the repo's Dockerfile).
        try:
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                version = browser.version
                browser.close()
            results["chromium_launch"] = {"ok": True, "version": version}
        except Exception as e:
            results["chromium_launch"] = {"ok": False, "error": str(e)[:500], "trace": traceback.format_exc()[-1000:]}

        # Test 2: trafilatura + bs4 (no browser needed)
        try:
            import trafilatura
            response = httpx.get(url, timeout=15, follow_redirects=True, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            })
            response.raise_for_status()
            html = response.text
            content = trafilatura.extract(html, include_comments=False, include_tables=True)
            results["trafilatura"] = {"ok": bool(content), "chars": len(content or "")}
        except Exception as e:
            results["trafilatura"] = {"ok": False, "error": str(e)[:400]}

        # Test 3: full composed extraction (what ingest_url() actually calls)
        try:
            full = ingestion_service._extract_webpage(url)
            results["full_extraction"] = {
                "ok": True,
                "title": full.get("title"),
                "content_chars": len(full.get("content") or ""),
                "extracted_via": full.get("metadata", {}).get("extracted_via", "trafilatura/bs4"),
            }
        except Exception as e:
            results["full_extraction"] = {"ok": False, "error": str(e)[:400], "trace": traceback.format_exc()[-1000:]}

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _run_debug)
    return results


@app.get("/api/debug/tweet")
async def debug_tweet(url: str):
    """Diagnostic: test each tweet extraction path exactly as the real pipeline does,
    without touching the DB or LLM enrichment. Mirrors /api/debug/youtube/{video_id}.

    Runs in a thread executor, not directly on the event loop — the Playwright
    fallback path uses Playwright's sync API, which raises if called while an
    asyncio loop is already running (same reason ingest_url() itself is always
    dispatched via run_in_executor in ingest.py).
    """
    import os, asyncio, traceback
    _webshare = bool(os.getenv("WEBSHARE_PROXY_USERNAME", "").strip() and os.getenv("WEBSHARE_PROXY_PASSWORD", "").strip())
    results = {
        "cookies_present": bool(os.getenv("X_COOKIES_B64")),
        "proxy_mode": "webshare" if _webshare else ("generic" if os.getenv("YOUTUBE_PROXY_URL", "").strip() else "none"),
    }

    def _run_debug():
        # Test 1: oEmbed (text/author)
        try:
            oembed = ingestion_service._extract_tweet_oembed(url)
            results["oembed"] = {"ok": bool(oembed), "has_text": bool(oembed.get("text"))} if oembed else {"ok": False}
        except Exception as e:
            results["oembed"] = {"ok": False, "error": str(e)[:400], "trace": traceback.format_exc()[-400:]}

        # Test 2: yt-dlp video + transcription
        try:
            text, segments, video_meta = ingestion_service._extract_tweet_video(url)
            results["yt_dlp_video"] = {
                "ok": True,
                "has_video": bool(video_meta.get("duration")),
                "has_transcript": bool(text),
                "transcript_chars": len(text),
            }
        except Exception as e:
            results["yt_dlp_video"] = {"ok": False, "error": str(e)[:400], "trace": traceback.format_exc()[-400:]}

        # Test 3: full composed extraction (what ingest_url() actually calls)
        try:
            full = ingestion_service._extract_tweet(url)
            results["full_extraction"] = {
                "ok": True,
                "title": full.get("title"),
                "content_chars": len(full.get("content", "")),
                "has_video": full.get("metadata", {}).get("has_video", False),
            }
        except Exception as e:
            results["full_extraction"] = {"ok": False, "error": str(e)[:400]}

    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, _run_debug)
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

    # X Article extraction needs a real logged-in session (confirmed: X has no
    # unauthenticated path to Article content at all, see docs/x-cookies-setup.md)
    # — log cookie health once here so staleness is visible in deploy logs
    # instead of only surfacing as a confusing card failure days later.
    # Diagnostic only — never blocks startup, X Articles are one content type
    # among many.
    try:
        x_cookie_status = ingestion_service._x_cookie_health()
        logger.info("x_cookie_health", status=x_cookie_status)
    except Exception as e:
        logger.warning("x_cookie_health_check_failed", error=str(e))

    # Usage v2: reconcile UserUsageDB.plan against live Stripe state before the
    # entitlement gate is allowed to enforce anything (see core/entitlements.py).
    # Defaults on — set FEATURE_USAGE_V2=false to roll back to the v1 token system
    # (see docs/usage-v2-migration.md).
    if os.getenv("FEATURE_USAGE_V2", "true").lower() == "true":
        _migrate_backfill_tier_from_stripe()

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


def _migrate_backfill_tier_from_stripe():
    """Usage v2: for every UserUsageDB row with a stripe_subscription_id, fetch the
    live Stripe subscription and correct usage.plan if it disagrees with the DB —
    catches drift from any webhook that was missed before usage v2 shipped. Writes
    a UsageAuditLogDB row (source="migration") for every correction made.

    Idempotent and safe to run on every startup (only paying users are checked, a
    small set) — this is deliberately NOT a one-shot migration, it doubles as
    ongoing drift correction. Must complete before real users are gated by the new
    entitlement checks, so no Core/Operator subscriber is briefly misread as
    free-tier during the transition (see docs/usage-v2-migration.md)."""
    from uuid import uuid4
    from .db.database import get_database, UserUsageDB, UsageAuditLogDB
    from .core import tiers
    import stripe

    stripe.api_key = os.getenv("STRIPE_SECRET_KEY")
    if not stripe.api_key:
        logger.warning("backfill_tier_skipped_no_stripe_key")
        return

    try:
        db = get_database()
        with db.session_scope() as session:
            rows = session.query(UserUsageDB).filter(UserUsageDB.stripe_subscription_id.isnot(None)).all()
            corrected = 0
            for usage in rows:
                try:
                    subscription = stripe.Subscription.retrieve(usage.stripe_subscription_id)
                    if subscription.get("status") != "active":
                        live_tier = "free"
                    else:
                        items = subscription.get("items", {}).get("data", [])
                        price_id = items[0]["price"]["id"] if items else None
                        live_tier = tiers.price_id_to_tier(price_id) if price_id else "free"
                except Exception as e:
                    logger.warning("backfill_tier_lookup_failed", user_id=usage.user_id, error=str(e))
                    continue  # leave this user's plan untouched, don't guess

                db_tier = tiers.normalize_tier(usage.plan)
                if db_tier != live_tier and usage.plan != "superadmin":
                    session.add(UsageAuditLogDB(
                        id=str(uuid4()), user_id=usage.user_id, event_type="tier_reconciled",
                        from_value=usage.plan, to_value=live_tier, source="migration",
                        detail="startup backfill vs live Stripe subscription state",
                    ))
                    usage.plan = live_tier
                    corrected += 1
            if corrected:
                logger.info("backfill_tier_corrections_applied", count=corrected)
    except Exception as e:
        logger.error("backfill_tier_from_stripe_failed", error=str(e))


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
