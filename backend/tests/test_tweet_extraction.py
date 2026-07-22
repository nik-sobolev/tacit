"""Tests for IngestionService's tweet/X-Article extraction fallback logic.

Regression coverage for a bug where a real, live X Article shared via a
`/status/{id}` URL (not the canonical `/i/article/{id}` shape) was
misreported as a deleted tweet. The Tweet GraphQL API returns the same
"no user object" shape for a genuinely deleted tweet AND for an Article
(Articles aren't Tweet objects at all), so `not_found` from that API alone
is not authoritative. Confirmed live against x.com/Av1dlive/status/207960774396994789.

Fix has two parts:
  1. `_extract_tweet_graphql`'s anonymous `not_found` no longer short-circuits
     before trying the authenticated (cookie) lookup.
  2. `_extract_tweet` no longer treats `not_found` as final -- it renders the
     page via `_extract_x_article` first, and only falls back to the
     "not available" verdict if that render also confirms the content is
     genuinely gone (or fails for an unrelated reason).
"""

from unittest.mock import patch

import pytest

from backend.app.services.ingestion_service import IngestionService


@pytest.fixture
def svc():
    return IngestionService()


TWEET_URL = "https://x.com/Av1dlive/status/207960774396994789"


def test_not_found_falls_back_to_article_render_and_succeeds(svc):
    """The core fix: a not_found verdict from the Tweet API doesn't end the
    story -- rendering the page can still turn up real content (an Article)."""
    article_result = {"title": "How to Build a Company OS", "content": "real article text" * 5}
    with patch.object(svc, "_extract_tweet_graphql", return_value={"not_found": True}), \
         patch.object(svc, "_extract_x_article", return_value=article_result) as mock_article:
        result = svc._extract_tweet(TWEET_URL)

    mock_article.assert_called_once_with(TWEET_URL)
    assert result == article_result


def test_not_found_and_article_confirms_deleted_raises_not_found(svc):
    """If the rendered page itself shows X's error page, the original
    not_found verdict stands -- this really is gone."""
    with patch.object(svc, "_extract_tweet_graphql", return_value={"not_found": True}), \
         patch.object(
             svc, "_extract_x_article",
             side_effect=ValueError(f"{svc.TWEET_NOT_FOUND_MARKER} This post is no longer available on X ({TWEET_URL})"),
         ):
        with pytest.raises(ValueError, match=svc.TWEET_NOT_FOUND_MARKER):
            svc._extract_tweet(TWEET_URL)


def test_not_found_and_article_requires_login_surfaces_that_not_deleted(svc):
    """If rendering hits a login wall, the user gets an actionable
    "needs login" message instead of a false "deleted" claim."""
    with patch.object(svc, "_extract_tweet_graphql", return_value={"not_found": True}), \
         patch.object(
             svc, "_extract_x_article",
             side_effect=ValueError(f"{svc.X_LOGIN_REQUIRED_MARKER} X requires a signed-in session ({TWEET_URL})"),
         ):
        with pytest.raises(ValueError, match=svc.X_LOGIN_REQUIRED_MARKER):
            svc._extract_tweet(TWEET_URL)


def test_not_found_and_article_render_other_failure_falls_back_to_not_found(svc):
    """If the render fails for an unrelated reason (disabled via env var,
    insufficient content), don't mask that with a confusing error -- fall
    back to the original not_found verdict."""
    with patch.object(svc, "_extract_tweet_graphql", return_value={"not_found": True}), \
         patch.object(
             svc, "_extract_x_article",
             side_effect=ValueError(f"Could not extract article content from {TWEET_URL}"),
         ):
        with pytest.raises(ValueError, match=svc.TWEET_NOT_FOUND_MARKER):
            svc._extract_tweet(TWEET_URL)


def test_graphql_anonymous_not_found_still_retries_with_cookies(svc):
    """Before the fix: an anonymous not_found short-circuited and the
    authenticated (cookie) lookup was never attempted, even when cookies
    were configured. Now it must still retry."""
    anon_result = {"not_found": True}
    cookie_result = {"text": "the real tweet body", "author": "Someone", "handle": "someone"}

    def fake_try(tweet_id, url, use_cookies):
        return cookie_result if use_cookies else anon_result

    with patch.object(svc, "_try_tweet_graphql", side_effect=fake_try), \
         patch.object(svc, "_x_cookies_opts", return_value={"cookiefile": "/tmp/fake-cookies"}):
        result = svc._extract_tweet_graphql(TWEET_URL)

    assert result == cookie_result


def test_graphql_both_anonymous_and_cookie_not_found_returns_not_found(svc):
    """If even the authenticated lookup says not_found, that's the real
    verdict from the Tweet API's perspective -- return it as before."""
    with patch.object(svc, "_try_tweet_graphql", return_value={"not_found": True}), \
         patch.object(svc, "_x_cookies_opts", return_value={"cookiefile": "/tmp/fake-cookies"}):
        result = svc._extract_tweet_graphql(TWEET_URL)

    assert result == {"not_found": True}
