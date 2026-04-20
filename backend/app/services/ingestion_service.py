"""Content ingestion service — YouTube, TikTok, Instagram, web pages"""

import os
import re
import uuid
import tempfile
import structlog
from typing import Dict, Any, Optional
from datetime import datetime
from urllib.parse import urlparse

import httpx

from ..db.database import get_database, NodeDB

logger = structlog.get_logger()


def detect_url_type(url: str) -> str:
    """Detect the type of content at a URL."""
    url_lower = url.lower()
    parsed = urlparse(url)
    host = parsed.netloc.lower().replace("www.", "")

    if host in ("youtube.com", "youtu.be") or "youtube.com" in host:
        return "youtube"
    if host in ("tiktok.com",) or "tiktok.com" in host:
        return "tiktok"
    if host in ("instagram.com",) or "instagram.com" in host:
        return "instagram"
    return "webpage"


class IngestionService:
    """Handles URL ingestion: extraction, transcription, and node creation."""

    def __init__(self):
        self.db = get_database()
        self._whisper_model = None  # lazy-load

    def ingest_url(self, url: str, canvas_x: float = 100.0, canvas_y: float = 100.0) -> NodeDB:
        """Main entry point: detect type, extract content, create NodeDB record."""
        content_type = detect_url_type(url)
        logger.info("ingesting_url", url=url, type=content_type)

        try:
            if content_type == "youtube":
                data = self._extract_youtube(url)
            elif content_type in ("tiktok", "instagram"):
                data = self._extract_social_video(url)
            else:
                data = self._extract_webpage(url)
        except Exception as e:
            logger.error("ingestion_extraction_error", url=url, error=str(e))
            data = {
                "title": url,
                "content": "",
                "thumbnail_url": None,
                "metadata": {"error": str(e)},
            }

        node_id = str(uuid.uuid4())
        node = NodeDB(
            id=node_id,
            type=content_type,
            title=data.get("title") or url[:200],
            content=data.get("content", ""),
            url=url,
            thumbnail_url=data.get("thumbnail_url"),
            canvas_x=canvas_x,
            canvas_y=canvas_y,
            status="processing",
            tags=[],
            node_meta=data.get("metadata", {}),
            created_at=datetime.utcnow(),
        )

        session = self.db.get_session()
        try:
            session.add(node)
            session.commit()
            session.refresh(node)
            logger.info("node_created", node_id=node_id, type=content_type)
        finally:
            session.close()

        return node

    # ==================== YOUTUBE ====================

    def _extract_youtube(self, url: str) -> Dict[str, Any]:
        """Extract YouTube video transcript and metadata."""
        video_id = self._parse_youtube_id(url)
        if not video_id:
            raise ValueError(f"Could not parse YouTube video ID from: {url}")

        # Get transcript via youtube-transcript-api
        transcript_text = ""
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
            transcript_text = " ".join(entry["text"] for entry in transcript_list)
        except Exception as e:
            logger.warning("youtube_transcript_api_failed", video_id=video_id, error=str(e))
            # Fall back to yt-dlp subtitle extraction
            transcript_text = self._get_yt_dlp_subtitles(url) or ""

        # Get metadata via yt-dlp (no download)
        metadata = self._get_video_metadata(url)
        title = metadata.get("title", f"YouTube Video {video_id}")
        thumbnail_url = metadata.get("thumbnail") or f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"

        return {
            "title": title,
            "content": transcript_text,
            "thumbnail_url": thumbnail_url,
            "metadata": {
                "video_id": video_id,
                "duration": metadata.get("duration"),
                "uploader": metadata.get("uploader"),
                "upload_date": metadata.get("upload_date"),
                "view_count": metadata.get("view_count"),
                "description": (metadata.get("description") or "")[:500],
            },
        }

    def _parse_youtube_id(self, url: str) -> Optional[str]:
        """Extract YouTube video ID from various URL formats."""
        patterns = [
            r"(?:v=|youtu\.be/)([a-zA-Z0-9_-]{11})",
            r"(?:embed/)([a-zA-Z0-9_-]{11})",
            r"(?:shorts/)([a-zA-Z0-9_-]{11})",
        ]
        for pattern in patterns:
            match = re.search(pattern, url)
            if match:
                return match.group(1)
        return None

    def _get_yt_dlp_subtitles(self, url: str) -> str:
        """Try to get subtitles via yt-dlp (auto-generated captions)."""
        try:
            import yt_dlp
            with tempfile.TemporaryDirectory() as tmpdir:
                ydl_opts = {
                    "skip_download": True,
                    "writeautomaticsub": True,
                    "writesubtitles": True,
                    "subtitleslangs": ["en"],
                    "subtitlesformat": "vtt",
                    "outtmpl": os.path.join(tmpdir, "%(id)s"),
                    "quiet": True,
                    "no_warnings": True,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

                # Find and parse VTT file
                for fname in os.listdir(tmpdir):
                    if fname.endswith(".vtt"):
                        vtt_path = os.path.join(tmpdir, fname)
                        return self._parse_vtt(vtt_path)
        except Exception as e:
            logger.warning("yt_dlp_subtitles_failed", url=url, error=str(e))
        return ""

    def _parse_vtt(self, vtt_path: str) -> str:
        """Parse VTT subtitle file to plain text."""
        lines = []
        with open(vtt_path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("WEBVTT") or "-->" in line or re.match(r"^\d+$", line):
                    continue
                # Strip HTML tags
                line = re.sub(r"<[^>]+>", "", line)
                if line:
                    lines.append(line)
        return " ".join(lines)

    # ==================== SOCIAL VIDEO (TikTok, Instagram) ====================

    def _extract_social_video(self, url: str) -> Dict[str, Any]:
        """Download video via yt-dlp and transcribe with faster-whisper."""
        try:
            import yt_dlp
        except ImportError:
            raise RuntimeError("yt-dlp not installed. Run: pip install yt-dlp")

        metadata = self._get_video_metadata(url)
        title = metadata.get("title", url)
        thumbnail_url = metadata.get("thumbnail")

        # Download audio only
        transcript_text = ""
        with tempfile.TemporaryDirectory() as tmpdir:
            audio_path = os.path.join(tmpdir, "audio.mp3")
            ydl_opts = {
                "format": "bestaudio/best",
                "outtmpl": audio_path,
                "postprocessors": [{
                    "key": "FFmpegExtractAudio",
                    "preferredcodec": "mp3",
                    "preferredquality": "128",
                }],
                "quiet": True,
                "no_warnings": True,
            }
            try:
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

                # Find the actual output file (yt-dlp may add extension)
                actual_path = audio_path
                for fname in os.listdir(tmpdir):
                    if fname.startswith("audio"):
                        actual_path = os.path.join(tmpdir, fname)
                        break

                if os.path.exists(actual_path):
                    transcript_text = self._transcribe(actual_path)
            except Exception as e:
                logger.error("social_video_download_error", url=url, error=str(e))

        return {
            "title": title,
            "content": transcript_text,
            "thumbnail_url": thumbnail_url,
            "metadata": {
                "duration": metadata.get("duration"),
                "uploader": metadata.get("uploader"),
                "upload_date": metadata.get("upload_date"),
                "description": (metadata.get("description") or "")[:500],
            },
        }

    def _transcribe(self, audio_path: str) -> str:
        """Transcribe audio using faster-whisper (lazy-loads base model)."""
        try:
            if self._whisper_model is None:
                from faster_whisper import WhisperModel
                logger.info("loading_whisper_model", model="base")
                self._whisper_model = WhisperModel("base", device="cpu", compute_type="int8")

            segments, _ = self._whisper_model.transcribe(audio_path, beam_size=5)
            return " ".join(segment.text.strip() for segment in segments)
        except Exception as e:
            logger.error("whisper_transcription_error", error=str(e))
            return ""

    def _get_video_metadata(self, url: str) -> Dict[str, Any]:
        """Get video metadata via yt-dlp without downloading."""
        try:
            import yt_dlp
            ydl_opts = {
                "quiet": True,
                "no_warnings": True,
                "extract_flat": False,
            }
            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(url, download=False)
                return info or {}
        except Exception as e:
            logger.warning("yt_dlp_metadata_error", url=url, error=str(e))
            return {}

    # Sites that require JS rendering or block simple HTTP requests
    BROWSER_REQUIRED_DOMAINS = {"x.com", "twitter.com", "instagram.com", "threads.net", "facebook.com"}

    # ==================== WEB PAGES ====================

    def _extract_webpage(self, url: str) -> Dict[str, Any]:
        """Extract text content from a web page, using browser rendering as fallback."""
        parsed = urlparse(url)
        domain = parsed.netloc.lower().replace("www.", "")

        # Use browser directly for domains known to block simple requests
        if domain in self.BROWSER_REQUIRED_DOMAINS:
            return self._extract_webpage_browser(url)

        try:
            import trafilatura
            response = httpx.get(url, timeout=15, follow_redirects=True, headers={
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            })
            response.raise_for_status()
            html = response.text

            # Extract main content with trafilatura
            content = trafilatura.extract(html, include_comments=False, include_tables=True)

            # Fallback to BeautifulSoup title + text if trafilatura gets nothing
            title = self._extract_html_title(html)
            if not content:
                content = self._bs4_extract(html)

            # If we still got nothing useful, try browser rendering
            if not content or len(content.strip()) < 50:
                logger.info("trafilatura_empty_trying_browser", url=url)
                return self._extract_webpage_browser(url)

            favicon_url = self._get_favicon_url(url)

            return {
                "title": title or domain,
                "content": content or "",
                "thumbnail_url": favicon_url,
                "metadata": {
                    "domain": domain,
                    "word_count": len((content or "").split()),
                },
            }
        except Exception as e:
            logger.warning("trafilatura_failed_trying_browser", url=url, error=str(e))
            # Fall back to browser rendering
            return self._extract_webpage_browser(url)

    def _extract_webpage_browser(self, url: str) -> Dict[str, Any]:
        """Extract webpage content using Playwright headless browser."""
        parsed = urlparse(url)
        domain = parsed.netloc.replace("www.", "")

        try:
            from playwright.sync_api import sync_playwright

            with sync_playwright() as p:
                browser = p.chromium.launch(headless=True)
                page = browser.new_page(
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
                )
                # Use domcontentloaded — sites like x.com never reach networkidle
                page.goto(url, wait_until="domcontentloaded", timeout=15000)
                page.wait_for_timeout(3000)

                title = page.title()
                html = page.content()
                browser.close()

            import trafilatura
            content = trafilatura.extract(html, include_comments=False, include_tables=True)

            if not content:
                content = self._bs4_extract(html)

            if not title:
                title = self._extract_html_title(html)

            favicon_url = self._get_favicon_url(url)

            logger.info("browser_extraction_success", url=url, content_len=len(content or ""))
            return {
                "title": title or domain,
                "content": content or "",
                "thumbnail_url": favicon_url,
                "metadata": {
                    "domain": domain,
                    "word_count": len((content or "").split()),
                    "extracted_via": "playwright",
                },
            }
        except Exception as e:
            logger.error("browser_extraction_error", url=url, error=str(e))
            raise

    def _extract_html_title(self, html: str) -> str:
        """Extract <title> tag from HTML."""
        match = re.search(r"<title[^>]*>([^<]+)</title>", html, re.IGNORECASE)
        return match.group(1).strip() if match else ""

    def _bs4_extract(self, html: str) -> str:
        """Fallback text extraction with BeautifulSoup."""
        try:
            from bs4 import BeautifulSoup
            soup = BeautifulSoup(html, "html.parser")
            for tag in soup(["script", "style", "nav", "footer", "header"]):
                tag.decompose()
            return soup.get_text(separator=" ", strip=True)[:10000]
        except Exception:
            return ""

    def _get_favicon_url(self, url: str) -> str:
        """Return a Google favicon URL for the domain."""
        parsed = urlparse(url)
        return f"https://www.google.com/s2/favicons?domain={parsed.netloc}&sz=64"
