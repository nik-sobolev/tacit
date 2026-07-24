"""Tests for the multi-account X cookie fallback (ingestion_service.py).

Before this fix, X Article extraction used exactly one account's cookies
(X_COOKIES_B64) -- if that session got logged out remotely (a real, recurring
failure, see TODOS.md), every Article hard-failed with X_LOGIN_REQUIRED until
someone noticed and re-exported cookies by hand. This confirms:

  - _x_cookie_accounts() reads X_COOKIES_B64 plus numbered X_COOKIES_B64_2..5,
    each getting its own tempfile
  - _extract_x_article() tries the next configured account when one hits the
    login wall, instead of failing on the first
  - it still raises X_LOGIN_REQUIRED (naming every account tried) if all of
    them are logged out
  - a genuinely deleted/not-found post fails fast without trying every account
"""

import base64
from unittest.mock import patch

from app.services.ingestion_service import IngestionService


def _svc():
    return object.__new__(IngestionService)


def test_x_cookie_accounts_reads_primary_and_numbered(monkeypatch, tmp_path):
    monkeypatch.setenv("X_COOKIES_B64", base64.b64encode(b"primary").decode())
    monkeypatch.setenv("X_COOKIES_B64_2", base64.b64encode(b"second").decode())
    monkeypatch.delenv("X_COOKIES_B64_3", raising=False)
    monkeypatch.delenv("X_COOKIES_B64_4", raising=False)
    monkeypatch.delenv("X_COOKIES_B64_5", raising=False)

    accounts = _svc()._x_cookie_accounts()

    assert [a["env_name"] for a in accounts] == ["X_COOKIES_B64", "X_COOKIES_B64_2"]
    # Each account gets a distinct file so trying account 2 can't clobber account 1.
    assert accounts[0]["cookiefile"] != accounts[1]["cookiefile"]
    assert open(accounts[0]["cookiefile"], "rb").read() == b"primary"
    assert open(accounts[1]["cookiefile"], "rb").read() == b"second"


def test_x_cookie_accounts_empty_when_unconfigured(monkeypatch):
    for name in IngestionService.X_COOKIE_ENV_NAMES:
        monkeypatch.delenv(name, raising=False)

    assert _svc()._x_cookie_accounts() == []


def test_extract_x_article_falls_through_to_second_account(monkeypatch):
    monkeypatch.setenv("X_COOKIES_B64", base64.b64encode(b"stale").decode())
    monkeypatch.setenv("X_COOKIES_B64_2", base64.b64encode(b"fresh").decode())
    monkeypatch.delenv("X_COOKIES_B64_3", raising=False)
    monkeypatch.delenv("X_COOKIES_B64_4", raising=False)
    monkeypatch.delenv("X_COOKIES_B64_5", raising=False)

    svc = _svc()
    calls = []

    def fake_render(url, use_proxy=False, cookies_opts=None):
        calls.append(cookies_opts)
        if len(calls) == 1:
            # First (primary) account: logged out.
            return {"content": "JavaScript is disabled in this browser. Log in to X.", "title": ""}
        # Second account: real article content.
        return {"content": "x" * 300, "title": "A real article"}

    with patch.object(svc, "_extract_webpage_browser", side_effect=fake_render):
        result = svc._extract_x_article("https://x.com/i/article/123")

    assert len(calls) == 2, "should have tried both accounts, not stopped at the first"
    assert result["content"] == "x" * 300
    assert result["metadata"]["provider"] == "x_article"


def test_extract_x_article_raises_when_every_account_logged_out(monkeypatch):
    monkeypatch.setenv("X_COOKIES_B64", base64.b64encode(b"stale1").decode())
    monkeypatch.setenv("X_COOKIES_B64_2", base64.b64encode(b"stale2").decode())
    monkeypatch.delenv("X_COOKIES_B64_3", raising=False)
    monkeypatch.delenv("X_COOKIES_B64_4", raising=False)
    monkeypatch.delenv("X_COOKIES_B64_5", raising=False)

    svc = _svc()

    def always_login_wall(url, use_proxy=False, cookies_opts=None):
        return {"content": "JavaScript is disabled in this browser.", "title": ""}

    with patch.object(svc, "_extract_webpage_browser", side_effect=always_login_wall):
        try:
            svc._extract_x_article("https://x.com/i/article/123")
            assert False, "expected ValueError"
        except ValueError as e:
            assert svc.X_LOGIN_REQUIRED_MARKER in str(e)
            assert "2 account" in str(e)


def test_extract_x_article_not_found_fails_fast_without_trying_every_account(monkeypatch):
    monkeypatch.setenv("X_COOKIES_B64", base64.b64encode(b"stale1").decode())
    monkeypatch.setenv("X_COOKIES_B64_2", base64.b64encode(b"stale2").decode())
    monkeypatch.delenv("X_COOKIES_B64_3", raising=False)
    monkeypatch.delenv("X_COOKIES_B64_4", raising=False)
    monkeypatch.delenv("X_COOKIES_B64_5", raising=False)

    svc = _svc()
    calls = []

    def not_found(url, use_proxy=False, cookies_opts=None):
        calls.append(cookies_opts)
        return {"content": "Post Not Found", "title": ""}

    with patch.object(svc, "_extract_webpage_browser", side_effect=not_found):
        try:
            svc._extract_x_article("https://x.com/i/article/123")
            assert False, "expected ValueError"
        except ValueError as e:
            assert svc.TWEET_NOT_FOUND_MARKER in str(e)

    assert len(calls) == 1, "a genuinely deleted post shouldn't retry other accounts"
