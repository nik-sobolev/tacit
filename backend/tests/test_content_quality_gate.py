"""Regression test for a live prod bug: a node titled "Tweet Content Unavailable
or Invalid URL" reached status="done" with an LLM-generated summary describing
that no content could be found, instead of the extraction properly failing
with status="error". Root cause: a bare ``len(text.strip()) >= 20`` check
treated any 20+ character fragment as "real content" -- including X's own
short unavailable/error boilerplate -- so a failed scrape was accepted as a
success and handed to the summarizer, which faithfully described the
emptiness. (We don't have the exact raw string X returned for that node --
only the LLM's output -- so this test checks representative short boilerplate
phrases, not one exact byte-for-byte string.)

is_real_content() (ingestion_service.py) replaces that bare length check with
a 150-char floor for full articles/pages, high enough that short boilerplate
can't accidentally clear it while still being well below any genuine article.
"""

from app.services.ingestion_service import is_real_content, MIN_REAL_CONTENT_CHARS


def test_short_boilerplate_is_not_real_content():
    assert not is_real_content("Content Unavailable")


def test_other_common_error_boilerplate_is_not_real_content():
    for phrase in [
        "Tweet Content Unavailable or Invalid URL",
        "This post is no longer available",
        "Post Not Found",
    ]:
        assert not is_real_content(phrase), f"{phrase!r} incorrectly passed the content-quality gate"


def test_empty_and_none_are_not_real_content():
    assert not is_real_content("")
    assert not is_real_content(None)
    assert not is_real_content("   ")


def test_substantial_real_content_passes():
    real_article = "x" * MIN_REAL_CONTENT_CHARS
    assert is_real_content(real_article)


def test_content_just_under_threshold_is_rejected():
    assert not is_real_content("x" * (MIN_REAL_CONTENT_CHARS - 1))
