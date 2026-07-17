# Chrome Web Store listing — copy-paste reference

**Live listing:** https://chromewebstore.google.com/detail/save-to-tacit/ipmhnnngmpnafgemmbihpiifpkenpffj

**The Overview/description text on the live listing right now is stale** — it
still leads with X Articles and has picked up a line about "publish on social
media of your choice," which isn't a real feature of this extension. Replace
it with the text below in the Developer Dashboard's store listing editor,
then submit the update for review (text-only listing edits typically review
faster than a new package).

## Name
Save to Tacit

## Category
Productivity

## Short description (≤132 chars)
Clip any page to your Tacit account in one click — including pages you need to be logged in to view.

## Detailed description
Tacit is a personal knowledge canvas — save articles, tweets, videos, and PDFs, and Tacit transcribes, summarizes, and connects them to everything else you've saved.

"Save to Tacit" is a one-click page clipper, like a web clipper extension: click the icon, the current page lands on your canvas. It also handles something the main app can't do on its own — pages that require you to be logged in to view. Instead of Tacit's servers trying to fetch the page, the extension captures it exactly as your own browser already renders it — after you're logged in, after the page has loaded — and sends that directly to your Tacit account. No separate login step, no credentials shared with Tacit, nothing captured unless you click the button.

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
