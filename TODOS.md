# TODOS

## Ingestion

**Title:** Add AssemblyAI diarized transcription fallback for TikTok / caption-less YouTube
**Priority:** P1
**Context:** yt-dlp's audio path stays bot-blocked for TikTok (and caption-less YouTube) even via residential proxy. Scoped in ROADMAP.md under "Next: AssemblyAI diarized fallback". Current mitigation (v1.0.0.0) degrades TikTok to an oEmbed title/author/thumbnail card instead of a hard failure, but doesn't produce a transcript.

**Title:** Refresh X_COOKIES_B64 in Render
**Priority:** P0
**Context:** The configured X session cookie has been logged out remotely (health check reports "ok" but X still rejects the session). Blocks X Article extraction for any account-gated content. Re-export per docs/x-cookies-setup.md and update the Render env var.

**Title:** Bare-t.co-link tweets that resolve to an X Article can produce a placeholder "Tweet Content Missing" summary
**Priority:** P2
**Context:** Found during post-deploy verification of v1.0.0.0 (2026-07-18), re-ingesting x.com/i/status/2076382497005281541. Traced the actual mechanism: this tweet's body is *only* a bare `t.co` link (no caption); `_extract_tweet()` detects that, resolves it, finds it points at `x.com/i/article/...`, and delegates to `_extract_x_article()`. That node landed on `status="done"` with a "Tweet Content Missing" LLM-generated title — real evidence content was blank when the summarizer ran, since that's not a title a real tweet would produce. Locally (no X_COOKIES_B64 configured), the same URL correctly raises `X_LOGIN_REQUIRED` — so the failure mode is specific to prod's cookie/session state, not a code path that's blindly broken. The node itself has since disappeared from `/api/graph` (128 nodes total either way) — likely deleted via the UI while testing, so there's nothing left to inspect further. Scope is narrower than first thought: only affects bare-link tweets that resolve to an Article, not tweets generally (3 other tweet re-tries checked in the same window all landed with real, substantive titles). Needs a fresh repro (or prod log/DB access) to actually pin down, rather than more guessing.

## Completed
