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

## Multiple accounts (recommended)

A single account's session can get logged out remotely at any time (X-side
security action, password change elsewhere, or just the session expiring) —
when that happens with only one account configured, every Article extraction
hard-fails until someone notices and re-exports cookies by hand.

`_extract_x_article()` tries every configured account in order and only gives
up once all of them hit the login wall, so a second (or third) account keeps
Articles working while you get around to refreshing the first. Set them up
the same way as the primary account (steps below), then set the *value* on
`X_COOKIES_B64_2` instead of `X_COOKIES_B64`. Supported env var names:

```
X_COOKIES_B64      (primary — required for any of this to work)
X_COOKIES_B64_2
X_COOKIES_B64_3
X_COOKIES_B64_4
X_COOKIES_B64_5
```

Any subset can be set — gaps are fine (e.g. only `X_COOKIES_B64` and
`X_COOKIES_B64_3`), each configured var is read independently. One extra
account is usually enough; more just buys more headroom before every session
is dead at once.

## Steps

1. **Create the dedicated X account** and log into it in a real browser (not the
   headless one Tacit uses internally). Repeat this whole process per account
   if you're setting up more than one (see "Multiple accounts" above).

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

5. **Set the env var** in Render's dashboard (your service → Environment) to the
   contents of `x_cookies_b64.txt` — `X_COOKIES_B64` for the primary account,
   `X_COOKIES_B64_2` / `_3` / etc. for additional ones.

6. **Redeploy.** Env var changes trigger this automatically. On startup, check the
   logs for a line like:
   ```
   x_cookie_health accounts={'X_COOKIES_B64': 'ok', 'X_COOKIES_B64_2': 'expired'}
   ```
   See "Reading the health check" below for what each status means.

## Reading the health check

Logged once on every startup (`backend/app/main.py`, near the other startup
checks) — check Render's logs after any deploy to know cookie health, per
account, without needing to test-save an Article. `/api/debug/tweet` reports
the same per-account breakdown at runtime.

| Status | Meaning | What to do |
|---|---|---|
| `not_configured` | No `X_COOKIES_B64*` var is set at all | Follow the steps above |
| `missing_auth_cookie` | Cookies are set, but no `auth_token` cookie found | Re-export — likely exported while logged out, or wrong domain selected |
| `expired` | `auth_token` is present but its expiry has already passed | Re-export a fresh cookie file and update the env var |
| `ok` | Looks valid | Article extraction should work through this account — if extraction still fails with `X_LOGIN_REQUIRED`, every configured account was rejected (see the per-account guidance in that error) |

With multiple accounts configured, one showing `expired` isn't an outage by
itself — extraction just falls through to the next account. Treat it as
"refresh this one when convenient," not urgent, unless every configured
account shows the same status.

## How long do these last?

X session cookies aren't permanent — expect to need to re-export periodically
(exact lifetime isn't published by X and can vary). The health check above is
what makes this maintainable: instead of a mysterious "Processing failed" card
days or weeks from now, Render's startup logs will say `expired` or
`missing_auth_cookie` directly.
