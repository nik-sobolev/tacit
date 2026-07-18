# TODOS

## Ingestion

**Title:** Add AssemblyAI diarized transcription fallback for TikTok / caption-less YouTube
**Priority:** P1
**Context:** yt-dlp's audio path stays bot-blocked for TikTok (and caption-less YouTube) even via residential proxy. Scoped in ROADMAP.md under "Next: AssemblyAI diarized fallback". Current mitigation (v1.0.0.0) degrades TikTok to an oEmbed title/author/thumbnail card instead of a hard failure, but doesn't produce a transcript.

**Title:** Refresh X_COOKIES_B64 in Render
**Priority:** P0
**Context:** The configured X session cookie has been logged out remotely (health check reports "ok" but X still rejects the session). Blocks X Article extraction for any account-gated content. Re-export per docs/x-cookies-setup.md and update the Render env var.

**Title:** Investigate tweet nodes reaching status="done" with empty content
**Priority:** P1
**Context:** Found during post-deploy verification of v1.0.0.0 (2026-07-18). Re-ingested a previously-failing plain tweet (x.com/i/status/2076382497005281541, oEmbed had no body text) after the TWEET_PLAYWRIGHT_FALLBACK fix deployed. Node resolved to status="done" (no longer "error" — confirms the core fix works) but with zero content, and the LLM summarizer generated a "Tweet Content Missing" placeholder title from blank input. `_extract_tweet()`'s docstring explicitly says it's designed to always raise on empty content specifically to prevent this exact symptom (a prior fix), and every code path I traced (GraphQL, oEmbed, video/Whisper, Playwright fallback) validates content length before returning success — so this needs a live debug session to find where an empty-but-"successful" result is actually slipping through. `/api/debug/tweet` 502s at Render/Cloudflare's edge on this specific URL (long-running synchronous request), so couldn't get the internal component breakdown to pinpoint it directly.

## Completed
