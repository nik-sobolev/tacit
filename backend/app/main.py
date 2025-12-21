"""Tacit FastAPI application"""

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import HTMLResponse
from pathlib import Path
import os

from .api import chat, context, documents
from .core.config import TacitConfig
from .core.engine import TacitEngine

# Configure logging
structlog.configure(
    processors=[
        structlog.processors.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.dev.ConsoleRenderer()
    ]
)

logger = structlog.get_logger()

# Initialize FastAPI app
app = FastAPI(
    title="Tacit",
    description="Your Personal Work Twin",
    version="0.1.0"
)

# CORS middleware
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # In production, specify actual origins
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Initialize global instances
config = TacitConfig.load()
engine = TacitEngine(config)

# Make engine available to routes
app.state.engine = engine
app.state.config = config

# Include API routers
app.include_router(chat.router, prefix="/api", tags=["chat"])
app.include_router(context.router, prefix="/api", tags=["context"])
app.include_router(documents.router, prefix="/api", tags=["documents"])

# Serve frontend
frontend_path = Path(__file__).parent.parent.parent / "frontend" / "static"
if frontend_path.exists():
    app.mount("/static", StaticFiles(directory=str(frontend_path)), name="static")


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

    # Ensure data directories exist
    os.makedirs("./data/uploads", exist_ok=True)
    os.makedirs("./data/chroma", exist_ok=True)


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
