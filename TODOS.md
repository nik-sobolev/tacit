# TODOS

## Ingestion

**Title:** Add AssemblyAI diarized transcription fallback for TikTok / caption-less YouTube
**Priority:** P1
**Context:** yt-dlp's audio path stays bot-blocked for TikTok (and caption-less YouTube) even via residential proxy. Scoped in ROADMAP.md under "Next: AssemblyAI diarized fallback". Current mitigation (v1.0.0.0) degrades TikTok to an oEmbed title/author/thumbnail card instead of a hard failure, but doesn't produce a transcript.

**Title:** Refresh X_COOKIES_B64 in Render
**Priority:** P0
**Context:** The configured X session cookie has been logged out remotely (health check reports "ok" but X still rejects the session). Blocks X Article extraction for any account-gated content. Re-export per docs/x-cookies-setup.md and update the Render env var.

## Completed
