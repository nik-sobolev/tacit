"""
Migrate local Tacit data to production (trytacit.app).

Run:
    python3 migrate.py

You'll be prompted for a fresh Bearer token. Get it from trytacit.app DevTools console:
    await window.Clerk.session.getToken()
"""

import sqlite3
import json
import urllib.request
import urllib.parse
import time

LOCAL_DB = "./backend/data/tacit.db"
PROD_URL = "https://www.trytacit.app"


def api(token, method, path, body=None):
    url = f"{PROD_URL}/api{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(
        url,
        data=data,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        }
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()
    except Exception as e:
        return 0, str(e)


def get_token():
    t = input("\nPaste fresh token (run in browser console: await window.Clerk.session.getToken())\n> ").strip()
    return t[7:] if t.startswith("Bearer ") else t


def main():
    token = get_token()

    # Verify token works
    status, data = api(token, "GET", "/graph")
    if status != 200:
        print(f"Token invalid or expired ({status}). Try again.")
        return
    print(f"Connected. Production canvas has {len(data.get('nodes', []))} nodes.\n")

    db = sqlite3.connect(LOCAL_DB)
    db.row_factory = sqlite3.Row

    # --- Migrate URL nodes (webpages) ---
    nodes = db.execute("SELECT * FROM nodes WHERE type='webpage' AND url IS NOT NULL AND url != ''").fetchall()
    print(f"Migrating {len(nodes)} webpage nodes...")
    ok, skipped, failed = 0, 0, 0
    for n in nodes:
        status, resp = api(token, "POST", "/ingest", {"url": n["url"]})
        if status == 401:
            print(f"\n  Token expired. Get a fresh one...")
            token = get_token()
            status, resp = api(token, "POST", "/ingest", {"url": n["url"]})
        if status == 200:
            if isinstance(resp, dict) and resp.get("duplicate"):
                print(f"  skip (duplicate): {n['url'][:60]}")
                skipped += 1
            else:
                print(f"  queued: {n['url'][:60]}")
                ok += 1
        elif status == 0:
            print(f"  timeout, skipping: {n['url'][:60]}")
            failed += 1
        else:
            print(f"  FAILED ({status}): {n['url'][:60]}")
            failed += 1
    print(f"  → {ok} queued, {skipped} duplicates, {failed} failed\n")

    # --- Migrate text notes as contexts ---
    notes = db.execute("SELECT * FROM nodes WHERE type='note'").fetchall()
    print(f"Migrating {len(notes)} text notes as contexts...")
    for n in notes:
        body = {
            "title": n["title"] or "Untitled Note",
            "type": "insight",
            "content": n["content"] or n["summary"] or "",
            "tags": json.loads(n["tags"]) if n["tags"] else [],
        }
        status, resp = api(token, "POST", "/context", body)
        if status == 401:
            print(f"\n  Token expired. Get a fresh one...")
            token = get_token()
            status, resp = api(token, "POST", "/context", body)
        if status == 200:
            print(f"  ok: {body['title'][:60]}")
        else:
            print(f"  FAILED ({status}): {body['title'][:60]}")
    print()

    # --- Migrate contexts ---
    contexts = db.execute("SELECT * FROM contexts").fetchall()
    contexts = [c for c in contexts if c["title"] and len(c["title"]) > 3 and "asdf" not in c["title"].lower()]
    print(f"Migrating {len(contexts)} contexts...")
    for c in contexts:
        body = {
            "title": c["title"],
            "type": c["type"] or "insight",
            "content": c["content"] or "",
            "tags": json.loads(c["tags"]) if c["tags"] else [],
        }
        status, resp = api(token, "POST", "/context", body)
        if status == 401:
            print(f"\n  Token expired. Get a fresh one...")
            token = get_token()
            status, resp = api(token, "POST", "/context", body)
        if status == 200:
            print(f"  ok: {c['title'][:60]}")
        else:
            print(f"  FAILED ({status}): {c['title'][:60]}")
    print()

    # --- People summary ---
    people = db.execute("SELECT name, role, organization FROM people").fetchall()
    if people:
        print(f"People ({len(people)}) — no create API, mention them in chat to re-capture:")
        for p in people:
            print(f"  - {p['name']} | {p['role'] or 'no role'} | {p['organization'] or 'no org'}")
    print("\nDone. URL nodes process in the background — check your canvas in ~1 min.")


if __name__ == "__main__":
    main()
