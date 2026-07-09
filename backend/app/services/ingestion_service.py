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
import time

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
                "metadata": {"error": str(e), "extraction_failed": True},
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
            error_message=data.get("metadata", {}).get("error") if extraction_failed else None,
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

        def _retry(func, *args, **kwargs):
            """Retry a function call up to N times on exception, where N is from YT_DLP_RETRIES env var."""
            max_attempts = self._get_yt_dlp_retries()
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    if attempt == max_attempts:
                        raise
                    logger.warning(
                        f"Attempt {attempt} failed for {getattr(func, '__name__', str(func))}, retrying...",
                        error=str(e),
                        video_id=video_id,
                    )
                    time.sleep(0.5 * attempt)  # backoff

        # Get transcript via youtube-transcript-api (preserves timestamps)
        transcript_text = ""
        transcript_segments = []
        try:
            def _fetch_transcript():
                # 1.x instance API, routed through the residential proxy when set
                # (YouTube blocks datacenter/cloud IPs — Render/AWS/GCP/Azure).
                api = self._transcript_api()
                try:
                    raw = api.fetch(video_id, languages=["en", "en-US", "en-GB"])
                except Exception:
                    raw = api.fetch(video_id)
                return raw

            raw = _retry(_fetch_transcript)
            entries = [{"text": s.text, "start": s.start} for s in raw]
            transcript_text = " ".join(e["text"] for e in entries)
            transcript_segments = [{"start": round(e["start"], 1), "text": e["text"]} for e in entries]
            logger.info("youtube_transcript_api_ok", video_id=video_id, segments=len(entries))
        except Exception as e:
            logger.warning("youtube_transcript_api_failed", video_id=video_id, error=str(e))

        # Fallback: yt-dlp subtitle extraction
        if not transcript_text:
            try:
                transcript_text = _retry(self._get_yt_dlp_subtitles, url) or ""
                if transcript_text:
                    logger.info("yt_dlp_subtitles_ok", video_id=video_id)
            except Exception as e:
                logger.warning("yt_dlp_subtitles_failed", video_id=video_id, error=str(e))

        # Final fallback: download audio and transcribe via cloud Whisper API
        if not transcript_text:
            try:
                transcript_text, transcript_segments = _retry(self._download_and_transcribe_audio, url)
                if transcript_text:
                    logger.info("youtube_audio_transcription_ok", video_id=video_id)
                else:
                    logger.warning("youtube_audio_transcription_empty", video_id=video_id)
            except Exception as e:
                logger.error("youtube_audio_transcription_failed", video_id=video_id, error=str(e))
                # If we still have no transcript, we will raise later

        # Get metadata via yt-dlp (no download)
        try:
            metadata = _retry(self._get_video_metadata, url)
        except Exception as e:
            logger.error("youtube_metadata_failed", video_id=video_id, error=str(e))
            metadata = {}

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

        # Ensure we have some transcript; if not, raise an error so the caller marks the node as error
        if not transcript_text:
            raise ValueError(f"No transcript could be extracted for YouTube video {video_id}")

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
        def _download_and_parse():
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
                    **self._yt_dlp_cookies_opts(),
                    **self._yt_dlp_proxy_opts(),
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

                # Find and parse VTT file
                for fname in os.listdir(tmpdir):
                    if fname.endswith(".vtt"):
                        vtt_path = os.path.join(tmpdir, fname)
                        return self._parse_vtt(vtt_path)
            return ""

        max_attempts = self._get_yt_dlp_retries()
        for attempt in range(1, max_attempts + 1):
            try:
                result = _download_and_parse()
                if result:
                    return result
                # If result is empty string, treat as failure and retry? Maybe not, because empty might mean no subtitles.
                # We'll break if we got empty string (no subtitles) because retrying won't help.
                if result == "":
                    break
            except Exception as e:
                if attempt == max_attempts:
                    logger.warning("yt_dlp_subtitles_failed after retries", url=url, error=str(e))
                    return ""
                wait = 0.5 * attempt
                logger.warning(
                    f"Attempt {attempt} failed for yt-dlp subtitles, retrying in {wait}s",
                    url=url,
                    error=str(e),
                )
                time.sleep(wait)
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
        metadata = self._get_video_metadata(url)
        title = metadata.get("title", "")
        thumbnail_url = metadata.get("thumbnail")

        # Attempt 1: yt-dlp audio download + transcription (local then cloud)
        def _download_audio_with_retry():
            max_attempts = 3
            for attempt in range(1, max_attempts + 1):
                try:
                    import yt_dlp
                    with tempfile.TemporaryDirectory() as tmpdir:
                        audio_path = os.path.join(tmpdir, "audio.mp3")
                        ydl_opts = {
                            "format": "bestaudio[ext=m4a]/bestaudio/best",
                            "outtmpl": audio_path,
                            "postprocessors": [{
                                "key": "FFmpegExtractAudio",
                                "preferredcodec": "mp3",
                                "preferredquality": "128",
                            }],
                            "postprocessor_args": {"FFmpegExtractAudio": ["-ac", "1"]},
                            "quiet": True,
                            "no_warnings": True,
                            "http_headers": {
                                "User-Agent": "com.zhiliaoapp.musically/2022600050 (Linux; U; Android 7.1.2; es_ES; SM-G988N; Build/NRD90M;tt-ok/3.12.13.1)"
                            },
                            "extractor_args": {"tiktok": {"app_name": "musically", "app_version": "2022600050"}},
                            **self._yt_dlp_cookies_opts(),
                            **self._tiktok_cookies_opts(),
                            **self._yt_dlp_proxy_opts(),
                        }
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            ydl.download([url])

                        actual_path = audio_path
                        for fname in os.listdir(tmpdir):
                            if fname.startswith("audio"):
                                actual_path = os.path.join(tmpdir, fname)
                                break

                        if os.path.exists(actual_path):
                            fsize = os.path.getsize(actual_path)
                            logger.info("tiktok_audio_download_ok", url=url, path=actual_path, size_mb=round(fsize/1024/1024, 2))
                            return actual_path
                        else:
                            logger.warning("tiktok_audio_file_not_found", url=url, tmpdir_contents=os.listdir(tmpdir))
                            raise FileNotFoundError("Audio file not found after download")
                except Exception as e:
                    if attempt == max_attempts:
                        raise
                    wait = 0.5 * attempt
                    logger.warning(
                        f"TikTok audio download attempt {attempt} failed, retrying in {wait}s",
                        url=url,
                        error=str(e),
                    )
                    time.sleep(wait)
            raise RuntimeError("Unexpected error in retry loop")

        transcript_text = ""
        transcript_segments = []
        try:
            audio_path = _download_audio_with_retry()
            # Try local transcription first (faster-whisper)
            try:
                transcript_text, transcript_segments = self._transcribe(audio_path)
                if transcript_text and len(transcript_text.strip()) >= 10:
                    logger.info("tiktok_local_transcription_ok", url=url, length=len(transcript_text))
                else:
                    raise ValueError("Local transcription empty or too short")
            except Exception as e:
                logger.warning("tiktok_local_transcription_failed", url=url, error=str(e))
                # Fallback to cloud transcription
                transcript_text, transcript_segments = self._transcribe_cloud(audio_path)
                if not transcript_text or len(transcript_text.strip()) < 10:
                    raise ValueError("Cloud transcription empty or too short")
                logger.info("tiktok_cloud_transcription_ok", url=url, length=len(transcript_text))
        except Exception as e:
            logger.error("tiktok_transcription_failed", url=url, error=str(e))
            # Re-raise to ensure node is marked as error and can be retried
            # Do NOT fall back to oEmbed only - if we can't get a transcript, treat as failure
            raise

        if transcript_text and len(transcript_text.strip()) >= 10:
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
        else:
            # This should not happen due to the checks above, but just in case
            logger.warning("tiktok_transcription_insufficient", url=url)
            raise ValueError("Transcript insufficient after all attempts")

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

    def _webshare_creds(self):
        """Webshare residential proxy credentials, if configured."""
        return (
            os.getenv("WEBSHARE_PROXY_USERNAME", "").strip(),
            os.getenv("WEBSHARE_PROXY_PASSWORD", "").strip(),
        )

    def _proxy_url(self) -> str:
        """Effective residential proxy URL for yt-dlp.

        YouTube blocks datacenter/cloud IPs (Render/AWS/GCP/Azure); a residential
        proxy makes traffic look like a home connection. Prefers Webshare creds
        (auto-builds the rotating-residential endpoint so the URL can't be
        mis-formatted), else falls back to a raw YOUTUBE_PROXY_URL.
        """
        user, pw = self._webshare_creds()
        if user and pw:
            # '-rotate' suffix = new residential IP per request
            return f"http://{user}-rotate:{pw}@p.webshare.io:80"
        return os.getenv("YOUTUBE_PROXY_URL", "").strip()

    def _yt_dlp_proxy_opts(self) -> dict:
        """Return yt-dlp proxy option when a residential proxy is configured."""
        url = self._proxy_url()
        return {"proxy": url} if url else {}

    def _transcript_api(self):
        """Build a YouTubeTranscriptApi routed through the residential proxy if set."""
        from youtube_transcript_api import YouTubeTranscriptApi
        user, pw = self._webshare_creds()
        if user and pw:
            from youtube_transcript_api.proxies import WebshareProxyConfig
            return YouTubeTranscriptApi(
                proxy_config=WebshareProxyConfig(proxy_username=user, proxy_password=pw)
            )
        url = os.getenv("YOUTUBE_PROXY_URL", "").strip()
        if url:
            from youtube_transcript_api.proxies import GenericProxyConfig
            return YouTubeTranscriptApi(
                proxy_config=GenericProxyConfig(http_url=url, https_url=url)
            )
        return YouTubeTranscriptApi()

    def _yt_dlp_cookies_opts(self) -> dict:
        """Return cookiefile option if YOUTUBE_COOKIES env var is set (Netscape format)."""
        import base64
        cookies_b64 = os.getenv("YOUTUBE_COOKIES_B64", "")
        if not cookies_b64:
            return {}
        try:
            cookie_path = os.path.join(tempfile.gettempdir(), "yt_cookies.txt")
            with open(cookie_path, "wb") as f:
                f.write(base64.b64decode(cookies_b64))
            return {"cookiefile": cookie_path}
        except Exception as e:
            logger.warning("yt_dlp_cookie_load_failed", error=str(e))
            return {}

    def _tiktok_cookies_opts(self) -> dict:
        """Return cookiefile option if TIKTOK_COOKIES env var is set (Netscape format)."""
        import base64
        cookies_b64 = os.getenv("TIKTOK_COOKIES_B64", "")
        if not cookies_b64:
            return {}
        try:
            cookie_path = os.path.join(tempfile.gettempdir(), "tiktok_cookies.txt")
            with open(cookie_path, "wb") as f:
                f.write(base64.b64decode(cookies_b64))
            return {"cookiefile": cookie_path}
        except Exception as e:
            logger.warning("tiktok_cookie_load_failed", error=str(e))
            return {}

    def _x_cookies_opts(self) -> dict:
        """Return cookiefile option if X_COOKIES_B64 env var is set (Netscape format)."""
        import base64
        cookies_b64 = os.getenv("X_COOKIES_B64", "")
        if not cookies_b64:
            return {}
        try:
            cookie_path = os.path.join(tempfile.gettempdir(), "x_cookies.txt")
            with open(cookie_path, "wb") as f:
                f.write(base64.b64decode(cookies_b64))
            return {"cookiefile": cookie_path}
        except Exception as e:
            logger.warning("x_cookie_load_failed", error=str(e))
            return {}

    def _get_yt_dlp_retries(self) -> int:
        """Get number of retry attempts for yt-dlp operations from environment variable."""
        try:
            return int(os.getenv("YT_DLP_RETRIES", "3"))
        except ValueError:
            logger.warning("invalid_yt_dlp_retries_env, using default of 3", env_var=os.getenv("YT_DLP_RETRIES"))
            return 3

    def _download_and_transcribe_audio(self, url: str):
        """Download audio at low bitrate and transcribe via cloud API. Returns (text, segments_list)."""
        def _download_with_retry():
            max_attempts = self._get_yt_dlp_retries()
            for attempt in range(1, max_attempts + 1):
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
                            **self._yt_dlp_cookies_opts(),
                            **self._yt_dlp_proxy_opts(),
                        }
                        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                            ydl.download([url])

                        actual_path = audio_path
                        for fname in os.listdir(tmpdir):
                            if fname.startswith("audio"):
                                actual_path = os.path.join(tmpdir, fname)
                                break

                        if os.path.exists(actual_path):
                            fsize = os.path.getsize(actual_path)
                            logger.info("audio_download_ok", url=url, path=actual_path, size_mb=round(fsize/1024/1024, 2))
                            return actual_path
                        else:
                            logger.warning("audio_file_not_found", url=url, tmpdir_contents=os.listdir(tmpdir))
                            raise FileNotFoundError("Audio file not found after download")
                except Exception as e:
                    if attempt == max_attempts:
                        raise
                    logger.warning(
                        f"Audio download attempt {attempt} failed, retrying...",
                        error=str(e),
                        url=url,
                    )
                    time.sleep(0.5 * attempt)
            # Should not reach here
            raise RuntimeError("Unexpected error in retry loop")

        try:
            audio_path = _download_with_retry()
            # Now transcribe via cloud API
            return self._transcribe_cloud(audio_path)
        except Exception as e:
            logger.error("download_and_transcribe_audio_failed", url=url, error=str(e))
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
                model_size = os.getenv("WHISPER_MODEL", "base")
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
                **self._yt_dlp_cookies_opts(),
                **self._yt_dlp_proxy_opts(),
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
        """Extract tweet content: oEmbed text + yt-dlp/whisper video transcript when present.

        oEmbed and video are independent sources (most tweets have no video, some
        have video with no caption text) so they're merged rather than treated as
        a fallback chain. Raises if nothing usable comes back from any path, so
        ingest_url() marks the node status="error" instead of letting an empty
        node reach LLM enrichment (which previously fabricated a plausible-looking
        title from nothing).
        """
        oembed = self._extract_tweet_oembed(url)
        transcript_text, transcript_segments, video_meta = self._extract_tweet_video(url)

        author = oembed.get("author", "")
        handle = oembed.get("handle", "")
        text = oembed.get("text", "")

        if not text and not transcript_text:
            # oEmbed gave no real body text (media-only tweet with no video we
            # could find, or an unsupported content type like an X Article) and
            # there's no video transcript either — try rendering the page directly.
            fallback_error = None
            try:
                # use_proxy=False: unlike yt-dlp/httpx, routing Playwright's browser
                # through the Webshare residential proxy hangs badly in production
                # (proxy handshake at the Chromium level, not a simple HTTP request).
                # No evidence yet that x.com blocks plain browser traffic the way it
                # blocks scraper/API traffic, so go direct here.
                page = self._extract_webpage_browser(url, use_proxy=False)
                page_text = (page.get("content") or "") + " " + (page.get("title") or "")
                is_x_error_page = any(marker in page_text for marker in (
                    "Post Not Found", "This page doesn’t exist", "This page doesn't exist",
                    "We're unable to show this content", "content may be private, deleted",
                ))
                if page.get("content") and len(page["content"].strip()) >= 20 and not is_x_error_page:
                    page["metadata"] = {
                        **page.get("metadata", {}),
                        "provider": "playwright_fallback",
                        "tweet_url": url,
                        "author": author,
                        "handle": handle,
                    }
                    # Prefer oEmbed's profile picture over the generic site favicon
                    page["thumbnail_url"] = oembed.get("thumbnail_url") or page.get("thumbnail_url")
                    return page
                elif is_x_error_page:
                    fallback_error = "playwright rendered an X error/not-found page"
                else:
                    fallback_error = "playwright returned insufficient content"
            except Exception as e:
                logger.warning("tweet_browser_fallback_failed", url=url, error=str(e))
                fallback_error = str(e)
            raise ValueError(f"Could not extract any real content for tweet {url} ({fallback_error})")

        content_parts = [p for p in (text, transcript_text) if p]
        content = "\n\n".join(content_parts) if content_parts else (text or transcript_text)

        title = oembed.get("title") or (f"Tweet by {author}" if author else f"Tweet: {url}")
        thumbnail_url = video_meta.get("thumbnail") or oembed.get("thumbnail_url")

        meta: Dict[str, Any] = {"tweet_url": url, "author": author, "handle": handle}
        if video_meta.get("duration"):
            meta["duration"] = video_meta["duration"]
            meta["has_video"] = True
        if transcript_segments:
            meta["transcript_segments"] = transcript_segments

        return {
            "title": title[:400],
            "content": content,
            "thumbnail_url": thumbnail_url,
            "metadata": meta,
        }

    def _extract_tweet_oembed(self, url: str) -> Dict[str, Any]:
        """Fetch tweet author/text via the public oEmbed endpoint. Returns {} on any failure
        (never raises) — the caller decides what "no oEmbed data" means."""
        import re as _re
        try:
            oembed_url = f"https://publish.twitter.com/oembed?url={url}&omit_script=true"
            proxy = self._proxy_url() or None
            with httpx.Client(timeout=10, follow_redirects=True, proxy=proxy) as client:
                resp = client.get(oembed_url, headers={"User-Agent": "Mozilla/5.0"})
                resp.raise_for_status()
                data = resp.json()

            author = data.get("author_name", "")
            html = data.get("html", "")

            # Real tweet body lives inside <p>...</p>. Media-only tweets (no
            # caption) and unsupported content like X Articles leave oEmbed
            # with just the "— Name (@handle)" attribution and no <p> — that's
            # not real content, so don't count it as extracted text.
            body_match = _re.search(r"<p[^>]*>(.*?)</p>", html, _re.DOTALL)
            if body_match:
                text = _re.sub(r"<a[^>]*>.*?</a>", "", body_match.group(1), flags=_re.DOTALL)
                text = _re.sub(r"<[^>]+>", "", text).strip()
                text = _re.sub(r"\s+", " ", text)
            else:
                text = ""

            # Extract handle from author_url
            author_url = data.get("author_url", "")
            handle = author_url.rstrip("/").split("/")[-1] if author_url else ""
            handle_str = f"@{handle}" if handle else ""

            title = f"{author} {handle_str}: {text[:120]}" if text else (f"Tweet by {author}" if author else "")

            return {
                "title": title[:400] if title else "",
                "text": text,
                "author": author,
                "handle": handle,
                "thumbnail_url": f"https://unavatar.io/twitter/{handle}" if handle else None,
            }
        except Exception as e:
            logger.warning("tweet_oembed_failed", url=url, error=str(e))
            return {}

    def _extract_tweet_video(self, url: str):
        """Attempt to download + transcribe a tweet's video via yt-dlp. Never raises —
        most tweets have no video at all, which is expected, not a failure.
        Returns (transcript_text, transcript_segments, video_meta)."""
        video_meta: Dict[str, Any] = {}
        try:
            info = self._get_video_metadata(url)
            if info:
                video_meta = {"thumbnail": info.get("thumbnail"), "duration": info.get("duration")}
        except Exception as e:
            logger.info("tweet_video_metadata_skip", url=url, error=str(e))

        try:
            import yt_dlp
            with tempfile.TemporaryDirectory() as tmpdir:
                audio_path = os.path.join(tmpdir, "audio.mp3")
                ydl_opts = {
                    "format": "bestaudio[ext=m4a]/bestaudio/best",
                    "outtmpl": audio_path,
                    "postprocessors": [{
                        "key": "FFmpegExtractAudio",
                        "preferredcodec": "mp3",
                        "preferredquality": "128",
                    }],
                    "postprocessor_args": {"FFmpegExtractAudio": ["-ac", "1"]},
                    "quiet": True,
                    "no_warnings": True,
                    **self._yt_dlp_cookies_opts(),
                    **self._x_cookies_opts(),
                    **self._yt_dlp_proxy_opts(),
                }
                with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                    ydl.download([url])

                actual_path = None
                for fname in os.listdir(tmpdir):
                    if fname.startswith("audio"):
                        actual_path = os.path.join(tmpdir, fname)
                        break

                if not actual_path or not os.path.exists(actual_path):
                    logger.info("tweet_no_video_found", url=url)
                    return "", [], video_meta

                try:
                    text, segments = self._transcribe(actual_path)
                    if not text or len(text.strip()) < 10:
                        raise ValueError("Local transcription empty or too short")
                except Exception as e:
                    logger.warning("tweet_local_transcription_failed", url=url, error=str(e))
                    text, segments = self._transcribe_cloud(actual_path)

                if text and len(text.strip()) >= 10:
                    logger.info("tweet_video_transcription_ok", url=url, length=len(text))
                    return text, segments, video_meta
                return "", [], video_meta
        except Exception as e:
            logger.info("tweet_video_extraction_skip", url=url, error=str(e))
            return "", [], video_meta

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

            # Binary content (PDFs, images, etc.) decoded as text produces
            # garbage — including raw NUL bytes that Postgres rejects outright
            # in text columns, crashing the whole request. Detect it up front
            # via the content-type header or the PDF magic bytes and fail
            # cleanly instead of feeding binary data through an HTML parser.
            content_type_header = response.headers.get("content-type", "").lower()
            if "application/pdf" in content_type_header or response.content[:5] == b"%PDF-":
                raise ValueError("PDF content is not yet supported for extraction")

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

    def _extract_webpage_browser(self, url: str, use_proxy: bool = False) -> Dict[str, Any]:
        """Extract webpage content using Playwright headless browser.

        use_proxy routes the browser through the residential proxy (same one used
        for YouTube/yt-dlp) — for sites like x.com that block datacenter IPs.
        Ordinary webpage rendering doesn't need this and leaves it off by default.
        """
        parsed = urlparse(url)
        domain = parsed.netloc.replace("www.", "")

        try:
            from playwright.sync_api import sync_playwright

            launch_kwargs = {"headless": True}
            if use_proxy:
                proxy_url = self._proxy_url()
                if proxy_url:
                    from urllib.parse import urlsplit
                    parsed_proxy = urlsplit(proxy_url)
                    launch_kwargs["proxy"] = {
                        "server": f"{parsed_proxy.scheme}://{parsed_proxy.hostname}:{parsed_proxy.port}",
                        "username": parsed_proxy.username,
                        "password": parsed_proxy.password,
                    }

            with sync_playwright() as p:
                browser = p.chromium.launch(**launch_kwargs)
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
