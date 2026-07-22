# Changelog

All notable changes to Tacit are documented here.

## [1.0.0.1] - 2026-07-22

### Fixed
- X Articles shared via a `/status/{id}` link (not the canonical
  `/i/article/{id}` shape) were misreported as deleted tweets. The Tweet
  GraphQL API returns the same "not found" shape for a genuinely deleted
  tweet and for an Article (Articles aren't Tweet objects), so that
  verdict alone is no longer trusted — the page is rendered directly
  before concluding a post is gone, and an anonymous "not found" now
  retries with the configured X session before giving up.

## [1.0.0.0] - 2026-07-18

First tracked version. Tacit has been in production at trytacit.app without
a VERSION/CHANGELOG before this release — this entry covers what shipped
in this PR.

### Fixed
- Tweets stopped failing when oEmbed returned no body text and no video —
  the Playwright browser fallback is now on by default (it shipped off
  after a suspected OOM crash; the concurrency-safety fixes that landed
  since make it safe to re-enable).
- Pages behind a bot-challenge (Cloudflare, X's Article gate) get a
  longer render timeout (15s -> 30s) and a lighter headless fingerprint,
  cutting down false "processing failed" results on those sites.
- TikTok videos that can't be transcribed (bot-blocked audio download)
  now show a title/author/thumbnail card with a "transcript unavailable"
  notice instead of failing outright.
- URLs (tweets, webpages, TikTok, Instagram) added from chat now actually
  get their content extracted — previously they were silently stuck at
  "processing" forever with no content.
- Semantic search and auto-linking are now scoped to the current user at
  the database query level (previously relied entirely on a secondary
  ownership check), including the main chat search path. Closes a gap
  where another user's more-similar canvas nodes could crowd out your
  own in search results.
- A Playwright resource-contention fix: browser-based extraction now
  gives up cleanly after 45s instead of blocking indefinitely if the
  single shared browser slot is busy, preventing a burst of extractions
  from starving other background work.
- Canvas tools (link, search, focus) are now always available to chat,
  not just when a message contains specific keywords.
