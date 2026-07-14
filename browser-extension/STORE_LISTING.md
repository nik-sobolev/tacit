# Chrome Web Store listing — copy-paste reference

## Name
Save to Tacit

## Category
Productivity

## Short description (≤132 chars)
Save any page to your Tacit account — including pages you need to be logged in to view, like X Articles.

## Detailed description
Tacit is a personal knowledge canvas — save articles, tweets, videos, and PDFs, and Tacit transcribes, summarizes, and connects them to everything else you've saved.

This extension adds one thing the main app can't do on its own: save pages that require you to be logged in to view. X Articles are the main example — X has no way for an outside server to fetch that content, even with valid credentials, since it's built to only render for an active, real browser session.

"Save to Tacit" solves this by working the other way around: instead of Tacit's servers trying to fetch the page, the extension captures the page exactly as your own browser already renders it — after you're logged in, after the page has loaded — and sends that directly to your Tacit account. No separate login step, no credentials shared with Tacit, nothing captured unless you click the button.

Requires a free or paid Tacit account (trytacit.app) and a one-time personal access token from your account's Browser Extension settings.

## Single purpose description
Captures the currently active browser tab's rendered page content, on user request, and sends it to the user's own Tacit account for saving and summarization.

## Permission justifications
- **activeTab** — needed to read the content of the tab the user is actively viewing when they click "Save this page." Only granted per-click, not persistent.
- **scripting** — needed to run a one-line script in the active tab (`chrome.scripting.executeScript`) that reads `document.documentElement.outerHTML`, `location.href`, and `document.title`. Runs only on demand, never automatically.
- **storage** — needed to save the user's personal Tacit access token locally (`chrome.storage.local`) so they don't have to re-enter it every time.
- **host_permissions (trytacit.app only)** — needed so the background service worker can send the captured page to Tacit's own API. No other destination is contacted.

## Privacy policy URL
https://www.trytacit.app/privacy

## Screenshots needed (take manually before submitting)
1. The popup with "Save this page" button visible (idle state)
2. The popup mid-save or showing a success status message

## Support email
support@trytacit.app
