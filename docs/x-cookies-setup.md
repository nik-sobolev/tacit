# Setting up X_COOKIES_B64 for X Article extraction

## Why this is needed

X has no unauthenticated path to Article content (`x.com/i/article/{id}`) at all —
confirmed directly, not assumed:

- A real browser visiting an Article URL while logged out gets redirected straight
  to a login page (`x.com/i/jf/onboarding/web?...&mode=login`), regardless of the
  specific article.
- Bot/crawler user agents (`Twitterbot`, `Googlebot`, `facebookexternalhit` — the
  ones X would need to serve *something* to in order to generate link-preview
  cards when an Article is shared elsewhere) get a flat 404, not even a preview.

This isn't a bug in Tacit's extraction code — it's X's actual access policy for
this content type. The only way to read Article content is with a real, logged-in
session's cookies.

## Use a dedicated account, not your personal one

Create a separate X account just for this. The cookies live in Render's
environment variables — a dedicated account isolates any risk (leaked cookie,
automated-traffic flags from X) from your real account. No special account
requirements — a normal free account works; only *being logged in* matters.

## Steps

1. **Create the dedicated X account** and log into it in a real browser (not the
   headless one Tacit uses internally).

2. **Install a cookie-export extension.** "Get cookies.txt LOCALLY" (Chrome/Firefox)
   is a well-known one that exports in the exact Netscape format this codebase
   already expects — the same format already used for `TIKTOK_COOKIES_B64` (see
   `backend/app/services/ingestion_service.py`'s `_tiktok_cookies_opts()` /
   `_x_cookies_opts()`), so no new parsing logic was needed for this.

3. **Export cookies for `x.com`** while logged into the dedicated account. Save
   the exported file (typically `x.com_cookies.txt` or similar).

4. **Base64-encode the file:**
   ```bash
   base64 -i x.com_cookies.txt | tr -d '\n' > x_cookies_b64.txt
   ```
   (macOS `base64` doesn't wrap lines by default the same way everywhere — the
   `tr -d '\n'` strips any newlines so the result is one clean line for the env var.)

5. **Set `X_COOKIES_B64`** in Render's dashboard (your service → Environment)
   to the contents of `x_cookies_b64.txt`.

6. **Redeploy.** Env var changes trigger this automatically. On startup, check the
   logs for a line like:
   ```
   x_cookie_health status=ok
   ```
   See "Reading the health check" below for what each status means.

## Reading the health check

Logged once on every startup (`backend/app/main.py`, near the other startup
checks) — check Render's logs after any deploy to know cookie health without
needing to test-save an Article:

| Status | Meaning | What to do |
|---|---|---|
| `not_configured` | `X_COOKIES_B64` isn't set | Follow the steps above |
| `missing_auth_cookie` | Cookies are set, but no `auth_token` cookie found | Re-export — likely exported while logged out, or wrong domain selected |
| `expired` | `auth_token` is present but its expiry has already passed | Re-export a fresh cookie file and update the env var |
| `ok` | Looks valid | Article extraction should work — if it still fails with `X_LOGIN_REQUIRED`, X may have invalidated the session remotely (logged out elsewhere, password change, etc.) — re-export |

## How long do these last?

X session cookies aren't permanent — expect to need to re-export periodically
(exact lifetime isn't published by X and can vary). The health check above is
what makes this maintainable: instead of a mysterious "Processing failed" card
days or weeks from now, Render's startup logs will say `expired` or
`missing_auth_cookie` directly.
