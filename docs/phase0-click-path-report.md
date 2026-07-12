# Phase 0 — Click-Path Report: Capture → Canvas → Share

> Read-only investigation of Tacit as it exists on branch `claude/click-path-investigation-5f4rt3`. Every behavioral claim cites a file. Where the code cannot answer a question, that is stated explicitly. No code was changed.

## Architecture in one paragraph

Tacit is a FastAPI backend (`backend/app/main.py` + routers under `backend/app/api/`) serving a single-file vanilla-JS frontend (`frontend/static/app.js`) inside an app shell (`frontend/static/index.html`, served at `/app`). There is **one implicit canvas per user**: every saved item is a `NodeDB` row carrying its own `canvas_x`/`canvas_y` (`backend/app/db/database.py:94-113`). There is no separate "canvas" object, no `canvas_id`, and no inbox/library — **saving an item and placing it on the canvas are the same operation.** Public sharing has two unrelated mechanisms: whole-canvas read-only tokens (`backend/app/api/share.py`) and per-item public transcript pages (`/s`, `/t`, `/yt` in `main.py`) that are rendered on demand straight from the node row.

> **Desktop vs mobile is a hard split** (`@media (max-width:768px)` in `frontend/static/styles.css`). Nearly everything below describes the **desktop** experience. On mobile the spatial canvas does not exist, the URL bar is hidden, and capture happens through a bottom-tab sheet or the OS share sheet. See **"Platform differences: desktop vs mobile"** before drawing redesign conclusions — the headline "paste-on-canvas, card-in-place" target is a **desktop-only** concept.

## Current-state click counts

Counts are discrete user actions (click / tap / keypress / paste / drag) from **"URL already in clipboard"** (Flows A/B) or **"looking at the canvas"** (Flow C) to the completed action.

| Flow | Entry point | Clicks/taps | Screen transitions | Mutations fired |
|---|---|---|---|---|
| A — Save URL | **Desktop only** — URL bar (`#urlInput` + Add/Enter); hidden on mobile (`styles.css:1723`) | 3 (focus bar, paste, Enter/Add) | 0 (stays on canvas) | `POST /api/ingest`; async `process_node`; `GET /api/ingest/{id}/status` (poll) |
| A — Save URL | **Desktop only** — drag-drop URL onto page (no touch drag on mobile) | 1 (drag gesture) | 0 | same as above |
| A — Save URL | **Mobile primary** — "+" tab add sheet (`index.html:222`) | 4 (tap +, tap "Add URL", paste, tap "Add to Canvas") | 1 (modal open) | same as above |
| A — Save URL | PWA share sheet (Android/iOS) | 2–3 OS taps (share → Tacit) | 1 (app launch → auto-submit) | `POST /share` → redirect `/?share_url=` → same `POST /api/ingest` |
| A — Save URL | iOS Shortcut (quick-add token) | 1 (run shortcut) | 0 (background) | `POST /api/quickadd` → same pipeline |
| A — Save note | Desktop/mobile note composer | 3+ (open, type, save) | 1 (modal, mobile) | `POST /api/ingest/note` (status `done` immediately) |
| B — Place saved item on canvas | New item | **0 extra** — placement is bundled into Flow A save | 0 | (position sent in the `POST /api/ingest` body) |
| B — Reposition existing card | Drag card | 1 (drag) | 0 | `PUT /api/nodes/{id}` (`canvas_x`,`canvas_y`) |
| C — Share item publicly | Card → detail → Share popover | 3 (click card, click "Share", click network/Copy) | 1 (detail panel opens) | **none** — public URL built client-side; page rendered on request |

**Against the targets:** save+canvas is already effectively **1 mutation** but costs 3 desktop actions via the bar (target: ≤1 click, ideally 0 via paste-on-canvas). Card→posted is **3 clicks today** (target: ≤2). Mobile counts differ — see next section.

## Platform differences: desktop vs mobile

The frontend is one codebase, but a `@media (max-width:768px)` block in `frontend/static/styles.css` plus `isMobile()` branches in `app.js` produce two materially different products. **This section is the single biggest correction to a naive reading of the flows above.**

**1. Mobile is not a spatial canvas — it's a stacked, scrolling list.** Under the mobile media query: `#canvasSurface { transform:none !important; display:flex; flex-direction:column; gap:12px }` (`styles.css:1758-1769`) and `.canvas-card { position:relative !important; left:auto !important; top:auto !important; width:100% !important }` (`styles.css:1771-1781`). Reinforced in JS: `applyTransform()` no-ops on mobile (`app.js:352-356`), `createCard()` clears inline `left/top` on mobile (`app.js:423-425`), and `submitUrl()` assigns throwaway stacked coordinates `100 + n*10` on mobile (`app.js:658-659`). **Consequence: `canvas_x`/`canvas_y` have no visual effect on mobile; cards render in creation order and you scroll the list.** The auto-generated edges are hidden entirely (`#edgesLayer { display:none }`, `styles.css:1784`).

**2. The desktop capture surfaces don't exist on mobile.** The URL bar and header action buttons are hidden — `.url-bar-wrap { display:none }` and `.header-actions { display:none }` (`styles.css:1723-1724`). So the desktop "URL bar" and "drag-drop" rows in the table above are **desktop-only**. Mobile capture is exactly two paths: the **bottom-tab "+" sheet** (`mobileShowAdd()`, wired at `index.html:222` → `app.js:1472-1528`) and the **PWA/OS share sheet** (`manifest.json:24-33` share_target → `POST /share` → `?share_url=` → `submitUrl`).

**3. Mobile navigation is a bottom tab bar, not a canvas.** Canvas / Add / Chat / Me tabs (`index.html:216-234`); `mobileTab()` just toggles the chat panel's visibility over the list (`app.js:1386-1399`), and "Me" opens an account sheet (`mobileOpenProfile`, `app.js:1401`).

**4. App shell (for reference):** header + desktop URL bar (`index.html:43-66`), slide-in node detail panel (`index.html:173-179`), empty state and onboarding tour (`index.html:150-211`).

**Redesign implication:** the target "paste-on-canvas → card in place (0 clicks)" is coherent only on desktop, where there is an (x,y) surface and a real `paste` event to hook. On mobile there is **no spatial surface to place a card "in place" and no visible paste target** — the fastest existing capture is already the OS share sheet (2–3 OS taps, fully wired). Mobile and desktop need **separate capture targets**, not one unified click-count goal.

## Flow A — Save a URL

**Entry points (all verified):**
1. Desktop URL bar — `initIngestion()` binds the Add button click and Enter key (`frontend/static/app.js:585-592`) → `submitUrl()`.
2. Drag-and-drop — a document-level `drop` handler accepts a dropped URL or image file (`frontend/static/app.js:595-610`); URLs go to `submitUrl()`, images to `POST /api/images/upload` (`app.js:826-856`).
3. Bulk-add modal — paste many URLs, looped through `submitUrl()` (`app.js:724-779`).
4. Mobile add sheet — `mobileShowAdd()` "Add URL" → `submitUrl()` (`app.js:1472-1528`).
5. PWA share target — OS share posts to `POST /share`, which validates the URL and redirects to `/?share_url=…` (`backend/app/main.py:270-287`); on load the boot sequence reads `?share_url` and calls `submitUrl()` (`app.js:275-281`). Declared in `frontend/static/manifest.json` `share_target`.
6. iOS Shortcut / mobile quick-add — long-lived token endpoint `POST /api/quickadd?token=…&url=…` (`backend/app/api/quickadd.py:48-118`).
7. Chat-driven — the chat stream can emit `ingest_started` / `node_created` actions that create cards (`app.js:2094-2136`).
8. Note (not a URL) — `POST /api/ingest/note` creates a `type="note"` node with status `done` immediately (`backend/app/api/ingest.py:116-155`).

**Step trace (primary desktop bar path):**
1. User focuses `#urlInput`, pastes URL, presses Enter / clicks Add — `initIngestion()` (`app.js:585-592`).
2. `submitUrl()` (`app.js:613-722`): validates `http…`; runs an **in-memory duplicate check** via `normalizeUrl()` (`app.js:626-627`); computes a canvas position at the current viewport center with jitter (`app.js:655-659`).
3. `POST /api/ingest` with `{ url, canvas_x, canvas_y }` (`app.js:662-666`).
4. Endpoint `ingest_url` (`backend/app/api/ingest.py:37-113`): per-user exact-URL duplicate check (`ingest.py:44-69`); then **synchronously** runs `ingestion_service.ingest_url(...)` in a thread executor and **awaits it** (`ingest.py:76-84`).
5. `IngestionService.ingest_url` (`backend/app/services/ingestion_service.py:44-94`) detects type (`detect_url_type`, `ingestion_service.py:20-34`), **extracts content synchronously** (transcript / webpage / whisper), and writes a `NodeDB` row with `status="processing"` (or `"error"` if extraction failed). Returns the node.
6. Back in the endpoint, `process_node` is dispatched **fire-and-forget** in another executor (`ingest.py:86-100`) — this is the async enrichment.
7. Response returns `{ node_id, type, title, status, canvas_x, canvas_y }` (`ingest.py:102-109`).
8. Frontend creates a placeholder card at the chosen position and begins polling (`app.js:692-713`); `pollNodeStatus` hits `GET /api/ingest/{id}/status` until `done` (`app.js:858-…`, `ingest.py:158-179`).

**Sync vs async:** content extraction (incl. TikTok/Instagram audio download + whisper) is **synchronous inside the request** (`ingest.py:76-84`; `ingestion_service.py:44-94`). LLM enrichment — summary, tags, category, key_points, embedding, auto-edges — is **async** in `process_node` (`backend/app/services/graph_service.py:44-141`).

**Where the item lands:** directly on the single canvas at the supplied `canvas_x`/`canvas_y`. There is no inbox/library/unsorted queue — `get_graph` returns all of the user's nodes (`graph_service.py:367`, `graph.py:168-180`).

## Flow B — Add a saved item to a canvas

**There is no separate "add existing item to canvas" action, because there is only one canvas and every node already lives on it** with its own position (`database.py:94-113`). Consequences:

- **New item:** placement is part of the save — `POST /api/ingest` carries `canvas_x`/`canvas_y` (`ingest.py:24-27`, request built at `app.js:655-666`). One call, no second mutation.
- **Reposition an existing card:** drag → on `mouseup`, `saveNodePosition()` fires `PUT /api/nodes/{id}` with new `canvas_x`/`canvas_y` (`app.js:318-332`, `571-581`; handler `graph.py:215-237`). This is the only "placement" mutation distinct from save.
- **Save with a canvas target in one call?** Position: **yes, already** (`canvas_x`/`canvas_y` on `IngestRequest`). A *canvas selector* (which canvas): **not applicable** — no multi-canvas model exists.
- **Canvas state storage:** positions are columns on `NodeDB` (`canvas_x`,`canvas_y`, `database.py:106-107`); edges are `EdgeDB` rows (`database.py:116-127`). No positions/viewport blob, no per-canvas record.
- **"Last-active canvas" state:** none. There is exactly one canvas; the client only persists chat session id in `localStorage`, not canvas identity.
- **Mobile caveat:** `canvas_x`/`canvas_y` are still written on save but have **no visual effect on mobile** — the mobile view is a stacked, scrollable list (`styles.css:1758-1781`), so "placement" has no spatial meaning there (see Platform differences). Repositioning by drag is a desktop-only interaction.

## Flow C — Share an item publicly

**Public page generation:** none. Per-item public pages are rendered **on request, directly from the node row**, with no token and no stored artifact:
- `/s/{node_id}` (+ optional slug) → `public_node_transcript` (`main.py:644-683`) renders `build_transcript_html` for TikTok/Instagram/web nodes. **No status filter** — renders any node it can find by UUID.
- `/t/{node_id}` → `transcript_md` (`main.py:545-575`) — same data as raw markdown. No status filter.
- `/yt/{video_id}` (+ slug, `?format=md`) → `public_youtube_transcript` (`main.py:578-641`) — public, enumerable, keyed by YouTube video id. **Filters `status == "done"`** (`main.py:601`).

**Share affordance path:** open a card's detail panel (`openDetail`, card click at `app.js:447-455`) → the detail panel renders a **Share** button (`app.js:988`) → `openSharePopover()` (`app.js:1175-1255`) builds the public URL client-side via `buildPublicShareUrl()` (`app.js:1161-1168`) and shows: native `navigator.share`, X / LinkedIn / Facebook / WhatsApp / Reddit intents, and **Copy link**. The same social-share row is also baked into the public HTML page itself (`build_transcript_html`, `main.py:415-424`). **No API call is made when sharing** — the URL is deterministic from the node.

**When the page is "generated":** on every request, lazily and idempotently (it's a pure function of the node row). Nothing is created on save or on first share.

## Answers to the seven questions

**1. Can the existing save endpoint accept an optional canvas ID + position, or would paste-on-canvas need a new parameter / second call?**
Position: **already supported in one call** — `IngestRequest` has `canvas_x`/`canvas_y` (`ingest.py:24-27`), used today by `submitUrl` (`app.js:655-666`). Paste-on-canvas needs **no new parameter and no second call** for placement. A *canvas ID* is **not applicable**: there is no canvas entity or `canvas_id` column anywhere (`database.py:94-113`); true multi-canvas targeting would require a schema change, not a parameter add.

**2. Is there a paste event handler on the canvas surface today? What does it do?**
**No.** `initCanvas()` binds only `mousedown`/`mousemove`/`mouseup`/`wheel` (`app.js:286-350`). There is a document-level `drop` handler for dragged URLs/images (`app.js:595-610`) and an Enter handler on the URL input (`app.js:590-592`), but **no `paste` listener** exists anywhere (grep for `paste`/`clipboard` finds only help text and copy-to-clipboard helpers). Clipboard *in* only arrives via the PWA share target (`main.py:270-287`, `app.js:275-281`).

**3. Is public-page generation idempotent and safe to trigger lazily on first share?**
**Yes, trivially** — there is nothing to generate. `/s`, `/t`, `/yt` render on request from the node row (`main.py:545-575`, `578-641`, `644-683`); the client just constructs the URL (`buildPublicShareUrl`, `app.js:1161-1168`). Repeated requests are pure reads. "Trigger on first share" is a client-side no-op.

**4. Do public pages render acceptably for items where transcription failed or is pending?**
Mixed — **this is a real gap:**
- `/s/{node_id}` and `/t/{node_id}`: render regardless of status. `build_transcript_html` tolerates empty segments/content (`main.py:394-413`) — a failed/pending node yields a **sparse but non-broken** page (title + empty transcript, summary blank until `process_node` runs). No exception.
- `/yt/{video_id}`: **filters `status == "done"` (`main.py:601`)**. A pending or failed YouTube node is **not found → 404 "This transcript does not exist"** (`main.py:628-634`).
- **Critical interaction:** `buildPublicShareUrl` routes every `type === "youtube"` node to `/yt/{video_id}` (`app.js:1164-1166`). So sharing a YouTube item whose transcription is still processing or failed produces a link that **404s**. Given YouTube/TikTok transcription is currently broken, YouTube share links are unreliable today. (TikTok/web route to `/s/` and degrade gracefully.)

**5. Where would clipboard detection on app open/focus hook in? Platform constraints?**
- **App shell:** the boot sequence in the `DOMContentLoaded` handler (`app.js:255-282`) already runs init + handles `?share_url`; a `navigator.clipboard.readText()` probe or a window `focus`/`visibilitychange` listener would attach here.
- **Canvas mount:** a canvas-level `paste` handler would attach in `initCanvas()` (`app.js:286`).
- **Constraints:** the web Clipboard API `readText()` requires a user gesture + permission and is blocked on bare page load in Chrome, and is unavailable/again gated in Safari/Firefox — so **silent auto-read on open is not reliable**. A `paste` event (Cmd/Ctrl-V) does deliver clipboard text without a permission prompt and is the sane hook — **but that is a desktop keyboard gesture.** On mobile there is neither a URL bar nor a canvas paste surface (both hidden/absent — `styles.css:1723`, `1758-1781`), so clipboard-in on mobile is effectively **only** the PWA `share_target` (`manifest.json:24-33`, `main.py:270-287`). Cannot be made fully automatic cross-platform — state this in design.

**6. Does the Chrome extension save path (per existing spec) hit the same endpoint as in-app save?**
**Cannot determine from code — no Chrome extension exists in this repo.** There is no `manifest_version`, `chrome.runtime`, or `content_script` anywhere (grep returns nothing; the only "chrome" hit is UI-chrome prose in `DESIGN.md:12`), and no spec document for one. What *can* be confirmed: there is a **single ingestion pipeline**, and the one external save path that exists — the quick-add token endpoint (`quickadd.py:48-118`) — hits it identically to in-app save. Both `POST /api/ingest` (`ingest.py:78-100`) and `POST /api/quickadd` (`quickadd.py:107-115`) call the same `ingestion_service.ingest_url(...)` then `graph_service.process_node(...)`. Any future extension pointed at `/api/ingest` or `/api/quickadd` would share that one pipeline.

**7. What card metadata is available immediately at save time (before async enrichment)?**
`ingest_url` sets, before returning (`ingestion_service.py:69-94`): **title, content (full transcript/page text), url, thumbnail_url, type, `node_meta` (e.g. `video_id`, `uploader`, `duration`), status="processing"**. The `POST /api/ingest` response exposes `node_id, type, title, status, canvas_x, canvas_y` (`ingest.py:102-109`). The async `process_node` adds **summary, tags, category, key_points** and flips status to `done` (`graph_service.py:83-107`).
Caveat for a "0-click paste" skeleton: because `ingest_url` is synchronous (`ingest.py:76-84`), title/thumbnail are only available **after** extraction finishes (seconds, longer for whisper). Truly instant (pre-round-trip) skeletons can show only client-known data — the pasted **URL string** and a **client-detectable type** (the same host logic as `detect_url_type`, `ingestion_service.py:20-34`).

## Gap list (to reach target: save+canvas ≤1 click, card→posted ≤2 clicks)

| Gap | Effort | Justification |
|---|---|---|
| Paste-on-canvas handler (desktop) | **S** | Add a `paste` listener (canvas surface/document, near `initCanvas` `app.js:286`) that extracts a URL and calls the existing `submitUrl()`/`POST /api/ingest` with cursor-position `canvas_x`/`canvas_y`. Reuses everything; no backend change. **Desktop only** — mobile has no paste surface. |
| Mobile capture target (separate from paste-on-canvas) | **M** | Mobile has no spatial canvas or URL bar (`styles.css:1723`,`1758-1781`); "card in place" is meaningless. Fastest win is promoting the already-wired PWA share sheet (`manifest.json:24-33`) as the hero and/or a paste-detect button in the "+" sheet (`app.js:1472`). |
| Save-with-canvas-target on save | **S** (position) / **L** (multi-canvas) | Position already ships in one call (`canvas_x`/`canvas_y`, `ingest.py:24-27`). A real "which canvas" target needs a new canvas entity + `canvas_id` on `NodeDB` + migration (none exists today). |
| Lazy public-page generation | **S / none** | Already lazy, stateless, idempotent (`main.py:644-683`). Remaining work is only to make YouTube shares not 404 (see next). |
| Fix `/yt` 404 for non-done nodes | **S** | Either relax the `status=="done"` filter (`main.py:601`) for share links or route failed/pending YouTube nodes to `/s/{node_id}` in `buildPublicShareUrl` (`app.js:1164-1166`) so the shared link renders. |
| Card-level share affordance (≤2 clicks) | **S/M** | Move/duplicate the Share trigger (`openSharePopover`, wired at `app.js:988`) onto the card itself (`buildCardHTML`, `app.js:461-501`) so card→popover→network is 2 clicks instead of 3 through the detail panel. |
| Clipboard detection on open/focus | **M** | Constrained by the web Clipboard permission model (no silent read on load; mobile mostly blocked). Needs gesture-based UX (real `paste` event) plus platform branching; PWA `share_target` already covers mobile capture. |

## Risk notes

- **YouTube share links 404 while broken.** `/yt` requires `status=="done"` (`main.py:601`) and `buildPublicShareUrl` sends all YouTube nodes there (`app.js:1164-1166`). With YT/TikTok transcription currently broken, a one-click card share on a YouTube item can hand users a dead link. Any card-level share affordance must handle non-`done` nodes.
- **`ingest_url` blocks the request.** Extraction (esp. whisper) is synchronous (`ingest.py:76-84`, `ingestion_service.py:44-94`); the "instant card" is not instant. A 0-click paste flow must render an optimistic skeleton *before* the round-trip, then reconcile on response/poll.
- **Duplicate detection is exact-URL, two-layered.** Client `normalizeUrl` in-memory check (`app.js:626-627`) + server per-user exact-URL check (`ingest.py:44-69`, `quickadd.py:97-100`). A new paste path must run the same dedup or it will create duplicate cards.
- **Single implicit canvas.** Positions live on the node (`database.py:106-107`); there is no canvas record. Do not conflate the existing `canvas_x/y` "target" with a "which canvas" target — the latter is an L-sized schema change, not covered anywhere today.
- **Enrichment is fire-and-forget with no persistence.** `process_node` runs in a detached thread (`ingest.py:86-100`); a restart orphans nodes at `status="processing"` (mitigated only by the startup recovery + 15-min watchdog, `main.py:874-897`, `900-912`). Any new save path inherits this fragility.
- **One-click public share is a privacy edge.** `/s/{node_id}` is unauthenticated (unguessable UUID, kept out of robots/sitemap — `main.py:128-156`, `264`). Collapsing share to ≤2 clicks makes "this content is now public at a live URL" easier to trigger accidentally; the affordance must make the public-exposure consequence explicit.
- **Desktop-only redesign risk.** The "paste-on-canvas, card-in-place" target assumes a spatial (x,y) surface and a `paste` keyboard event — both **desktop-only** (`styles.css:1723`,`1758-1784`). Designing a single unified click-count goal will silently exclude mobile, where cards stack in creation order and capture is the "+" sheet / OS share sheet. Treat mobile capture as a distinct workstream.
- **Canvas-wide share is a separate surface.** `/share/{token}` serves `read_only.html`, which independently fetches `/api/graph` and re-renders cards + chat (`read_only.html:177,197-201,293`) — distinct from the per-item `/s`·`/t`·`/yt` pages this report's Flow C covers. It was not deep-audited here; note it exists so redesign work doesn't assume a single share surface.

## Cannot-determine-from-code items

- **Chrome extension** save path / spec — no extension code and no spec doc exist in this repo (Q6). Reported as such rather than guessed.
