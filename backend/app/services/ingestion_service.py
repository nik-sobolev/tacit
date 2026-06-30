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
    if host in ("tiktok.com", "vm.tiktok.com", "vt.tiktok.com") or "tiktok.com" in host:
        return "tiktok"
    if host in ("instagram.com",) or "instagram.com" in host:
        return "instagram"
    if host in ("x.com", "twitter.com", "t.co"):
        return "tweet"
    return "webpage"


class IngestionService:
    """Handles URL ingestion: extraction, transcription, and node creation."""

    def __init__(self):
        self.db = get_database()
        self._whisper_model = None  # lazy-load

    def ingest_url(self, url: str, canvas_x: float = 100.0, canvas_y: float = 100.0, user_id: str = None) -> NodeDB:
        """Main entry point: detect type, extract content, create NodeDB record."""
        content_type = detect_url_type(url)
        logger.info("ingesting_url", url=url, type=content_type)

        try:
            if content_type == "youtube":
                data = self._extract_youtube(url)
            elif content_type in ("tiktok", "instagram"):
                data = self._extract_social_video(url)
            elif content_type == "tweet":
                data = self._extract_tweet(url)
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

        extraction_failed = data.get("metadata", {}).get("extraction_failed", False)
        node_id = str(uuid.uuid4())
        node = NodeDB(
            id=node_id,
            user_id=user_id,
            type=content_type,
            title=data.get("title") or url[:200],
            content=data.get("content", ""),
            url=url,
            thumbnail_url=data.get("thumbnail_url"),
            canvas_x=canvas_x,
            canvas_y=canvas_y,
            status="error" if extraction_failed else "processing",
            error_message="TikTok extraction failed — video may be private, deleted, or region-blocked" if extraction_failed else None,
            tags=[],
            node_meta=data.get("metadata", {}),
            created_at=datetime.utcnow(),
        )

        with self.db.session_scope() as session:
            session.add(node)
            session.flush()
            session.refresh(node)
            # Detach from session so caller can use the object after session closes
            session.expunge(node)
            logger.info("node_created", node_id=node_id, type=content_type)

        return node

    # ==================== YOUTUBE ====================

    def _extract_youtube(self, url: str) -> Dict[str, Any]:
        """Extract YouTube video transcript and metadata."""
        video_id = self._parse_youtube_id(url)
        if not video_id:
            raise ValueError(f"Could not parse YouTube video ID from: {url}")

        # Get transcript via youtube-transcript-api (preserves timestamps)
        transcript_text = ""
        transcript_segments = []
        try:
            from youtube_transcript_api import YouTubeTranscriptApi
            transcript_list = YouTubeTranscriptApi.get_transcript(video_id)
            transcript_text = " ".join(entry["text"] for entry in transcript_list)
            transcript_segments = [
                {"start": round(entry["start"], 1), "text": entry["text"]}
                for entry in transcript_list
            ]
        except Exception as e:
            logger.warning("youtube_transcript_api_failed", video_id=video_id, error=str(e))
            # Fall back to yt-dlp subtitle extraction
            transcript_text = self._get_yt_dlp_subtitles(url) or ""

        # Final fallback: download audio and transcribe via cloud Whisper API
        if not transcript_text:
            logger.info("youtube_falling_back_to_audio_transcription", video_id=video_id)
            transcript_text, transcript_segments = self._download_and_transcribe_audio(url)

        # Get metadata via yt-dlp (no download)
        metadata = self._get_video_metadata(url)
        title = metadata.get("title", f"YouTube Video {video_id}")
        thumbnail_url = metadata.get("thumbnail") or f"https://img.youtube.com/vi/{video_id}/mqdefault.jpg"

        result_meta = {
            "video_id": video_id,
            "duration": metadata.get("duration"),
            "uploader": metadata.get("uploader"),
            "upload_date": metadata.get("upload_date"),
            "view_count": metadata.get("view_count"),
            "description": (metadata.get("description") or "")[:500],
        }
        if transcript_segments:
            result_meta["transcript_segments"] = transcript_segments

        return {
            "title": title,
            "content": transcript_text,
            "thumbnail_url": thumbnail_url,
            "metadata": result_meta,
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
        """Download video via yt-dlp + whisper, fall back to oEmbed if yt-dlp fails."""
        # Attempt 1: yt-dlp audio download + faster-whisper transcription
        try:
            import yt_dlp
            metadata = self._get_video_metadata(url)
            title = metadata.get("title", "")
            thumbnail_url = metadata.get("thumbnail")
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
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

                actual_path = audio_path
                for fname in os.listdir(tmpdir):
                    if fname.startswith("audio"):
                        actual_path = os.path.join(tmpdir, fname)
                        break

                if os.path.exists(actual_path):
                    transcript_text, transcript_segments = self._transcribe(actual_path)
                    if not transcript_text:
                        transcript_text, transcript_segments = self._transcribe_cloud(actual_path)

            if transcript_text:
                meta = {
                    "duration": metadata.get("duration"),
                    "uploader": metadata.get("uploader"),
                    "upload_date": metadata.get("upload_date"),
                    "description": (metadata.get("description") or "")[:500],
                }
                if transcript_segments:
                    meta["transcript_segments"] = transcript_segments
                return {
                    "title": title or url,
                    "content": transcript_text,
                    "thumbnail_url": thumbnail_url,
                    "metadata": meta,
                }
            logger.warning("social_video_empty_transcript", url=url)
        except Exception as e:
            logger.warning("social_video_yt_dlp_failed", url=url, error=str(e))

        # Attempt 2: TikTok oEmbed (no auth, returns title + author + thumbnail)
        parsed = urlparse(url)
        host = parsed.netloc.lower().replace("www.", "")
        if "tiktok.com" in host:
            try:
                return self._extract_tiktok_oembed(url)
            except Exception as e:
                logger.warning("tiktok_oembed_failed", url=url, error=str(e))

        # Both paths failed — signal complete failure via sentinel title
        return {"title": url, "content": "", "thumbnail_url": None, "metadata": {"extraction_failed": True}}

    def _extract_tiktok_oembed(self, url: str) -> Dict[str, Any]:
        """Fetch TikTok title + author + thumbnail via the public oEmbed endpoint."""
        oembed_url = f"https://www.tiktok.com/oembed?url={url}"
        with httpx.Client(timeout=10, follow_redirects=True) as client:
            resp = client.get(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
            resp.raise_for_status()
            data = resp.json()

        author = data.get("author_name", "")
        title = data.get("title") or (f"TikTok by {author}" if author else url)
        thumbnail_url = data.get("thumbnail_url")

        # oEmbed gives no transcript — use title + author as searchable content
        content = f"{title}\n\nBy {author}" if author else title

        return {
            "title": title[:400],
            "content": content,
            "thumbnail_url": thumbnail_url,
            "metadata": {
                "author": author,
                "provider": "tiktok_oembed",
            },
        }

    def _transcribe_cloud(self, audio_path: str):
        """Transcribe via Groq Whisper API (free) or OpenAI Whisper API. Returns (text, segments_list)."""
        import os
        groq_key = os.getenv("GROQ_API_KEY", "")
        openai_key = os.getenv("OPENAI_API_KEY", "")
        if not groq_key and not openai_key:
            logger.warning("no_cloud_transcription_key_set")
            return "", []
        try:
            with open(audio_path, "rb") as f:
                audio_bytes = f.read()
            if groq_key:
                api_url = "https://api.groq.com/openai/v1/audio/transcriptions"
                api_key = groq_key
                model = "whisper-large-v3-turbo"
            else:
                api_url = "https://api.openai.com/v1/audio/transcriptions"
                api_key = openai_key
                model = "whisper-1"
            resp = httpx.post(
                api_url,
                headers={"Authorization": f"Bearer {api_key}"},
                files={"file": ("audio.mp3", audio_bytes, "audio/mpeg")},
                data={"model": model, "response_format": "verbose_json"},
                timeout=300.0,
            )
            resp.raise_for_status()
            result = resp.json()
            text = result.get("text", "").strip()
            segs = [
                {"start": round(s["start"], 1), "text": s["text"].strip()}
                for s in result.get("segments", [])
            ]
            logger.info("cloud_transcription_ok", chars=len(text), segments=len(segs))
            return text, segs
        except Exception as e:
            logger.error("cloud_transcription_error", error=str(e))
            return "", []

    def _download_and_transcribe_audio(self, url: str):
        """Download audio at low bitrate and transcribe via cloud API. Returns (text, segments_list)."""
        try:
            import yt_dlp
            with tempfile.TemporaryDirectory() as tmpdir:
                audio_path = os.path.join(tmpdir, "audio.mp3")
                ydl_opts = {
                    "format": "worstaudio/worst",
                    "outtmpl": audio_path,
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "32",
                    }],
                    "postprocessor_args": {"FFmpegExtractAudio": ["-ac", "1"]},
                    "quiet": True,
                    "no_warnings": True,
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

                actual_path = audio_path
                for fname in os.listdir(tmpdir):
                    if fname.startswith("audio"):
                        actual_path = os.path.join(tmpdir, fname)
                        break

                if os.path.exists(actual_path):
                    return self._transcribe_cloud(actual_path)
        except Exception as e:
            logger.warning("download_and_transcribe_audio_failed", url=url, error=str(e))
        return "", []

    def _transcribe(self, audio_path: str):
        """Transcribe audio using faster-whisper. Returns (text, segments_list). Disabled via DISABLE_WHISPER=true."""
        import os
        if os.getenv("DISABLE_WHISPER", "").lower() in ("1", "true", "yes"):
            logger.info("whisper_disabled_by_env")
            return "", []
        try:
            if self._whisper_model is None:
                from faster_whisper import WhisperModel
                model_size = os.getenv("WHISPER_MODEL", "tiny")
                logger.info("loading_whisper_model", model=model_size)
                self._whisper_model = WhisperModel(model_size, device="cpu", compute_type="int8")

            raw_segments, _ = self._whisper_model.transcribe(audio_path, beam_size=5)
            segments_list = [{"start": round(seg.start, 1), "text": seg.text.strip()} for seg in raw_segments]
            text = " ".join(s["text"] for s in segments_list)
            return text, segments_list
        except Exception as e:
            logger.error("whisper_transcription_error", error=str(e))
            return "", []

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

    # ==================== TWITTER / X ====================

    def _extract_tweet(self, url: str) -> Dict[str, Any]:
        """Extract tweet content via Twitter oEmbed API (no auth required)."""
        import re as _re
        try:
            oembed_url = f"https://publish.twitter.com/oembed?url={url}&omit_script=true"
            with httpx.Client(timeout=10, follow_redirects=True) as client:
                resp = client.get(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                data = resp.json()

            author = data.get("author_name", "")
            html = data.get("html", "")

            # Extract plain text from blockquote HTML
            text = _re.sub(r"<a[^>]*>.*?</a>", "", html, flags=_re.DOTALL)
            text = _re.sub(r"<[^>]+>", "", text).strip()
            text = _re.sub(r"\s+", " ", text)

            # Extract handle from author_url
            author_url = data.get("author_url", "")
            handle = author_url.rstrip("/").split("/")[-1] if author_url else ""
            handle_str = f"@{handle}" if handle else ""

            title = f"{author} {handle_str}: {text[:120]}" if text else f"Tweet by {author}"

            return {
                "title": title[:400],
                "content": text,
                "thumbnail_url": f"https://unavatar.io/twitter/{handle}" if handle else None,
                "metadata": {
                    "author": author,
                    "handle": handle,
                    "tweet_url": url,
                },
            }
        except Exception as e:
            logger.warning("tweet_oembed_failed", url=url, error=str(e))
            return {
                "title": f"Tweet: {url}",
                "content": "",
                "thumbnail_url": None,
                "metadata": {"tweet_url": url},
            }

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
