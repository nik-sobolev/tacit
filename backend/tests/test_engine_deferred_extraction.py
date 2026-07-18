"""Tests for engine.py's ``_safe_process`` closure inside ``_execute_tool``'s
"ingest_url" branch (added in the "extract deferred-type nodes when added via chat"
fix). Before this fix, the chat "ingest_url" tool always scheduled
``graph_service.process_node()`` directly, even for tweet/webpage/tiktok/instagram
URLs -- types whose ``ingest_url()`` call returns a placeholder with empty content and
expects a follow-up ``extract_deferred()`` call. Those nodes got stuck at
status="processing" forever. This file confirms:

  - types in DEFERRED_EXTRACTION_TYPES call extract_deferred() before process_node()
  - a False return from extract_deferred() (already marked status="error" internally)
    skips process_node() entirely
  - a non-deferred type skips extract_deferred() and calls process_node() directly
  - an exception raised by extract_deferred() marks the node status="error" with a
    message, and never calls process_node()

TacitEngine is built via object.__new__ to skip __init__, which stands up a real
Anthropic client and a real ChromaDB PersistentClient -- neither of which
_execute_tool's "ingest_url" branch actually needs. Only the attributes that branch
touches are set: db, ingestion_service, graph_service, _current_user_id.

threading.Thread.start is monkeypatched to run the target synchronously (via
self.run()) so the background thread's work is observable immediately, matching the
mocking style already used for stripe.Webhook.construct_event in test_webhook.py.
"""

import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from backend.app.core.engine import TacitEngine
from backend.app.db.database import NodeDB


@pytest.fixture(autouse=True)
def synchronous_threads(monkeypatch):
    monkeypatch.setattr(threading.Thread, "start", lambda self: self.run())


def _make_engine(db, ingestion_service, graph_service, user_id="u1"):
    engine = object.__new__(TacitEngine)
    engine.db = db
    engine.ingestion_service = ingestion_service
    engine.graph_service = graph_service
    engine._current_user_id = user_id
    return engine


def test_deferred_type_success_runs_extract_deferred_then_process_node(db):
    ingestion = Mock()
    fake_node = SimpleNamespace(id="n1", url="https://x.com/foo/status/1", type="tweet", title="A tweet")
    ingestion.ingest_url.return_value = fake_node
    ingestion.extract_deferred.return_value = True
    graph = Mock()

    engine = _make_engine(db, ingestion, graph)
    result = engine._execute_tool("ingest_url", {"url": "https://x.com/foo/status/1"}, [])

    ingestion.extract_deferred.assert_called_once_with("n1", "https://x.com/foo/status/1", "tweet")
    graph.process_node.assert_called_once_with("n1")
    assert result["success"] is True


def test_deferred_type_extract_failure_skips_process_node(db):
    """extract_deferred() returning False means it already marked the node
    status="error" itself -- process_node() must not run over it."""
    ingestion = Mock()
    fake_node = SimpleNamespace(id="n2", url="https://tiktok.com/@a/video/2", type="tiktok", title="A tiktok")
    ingestion.ingest_url.return_value = fake_node
    ingestion.extract_deferred.return_value = False
    graph = Mock()

    engine = _make_engine(db, ingestion, graph)
    engine._execute_tool("ingest_url", {"url": "https://tiktok.com/@a/video/2"}, [])

    ingestion.extract_deferred.assert_called_once_with("n2", "https://tiktok.com/@a/video/2", "tiktok")
    graph.process_node.assert_not_called()


def test_non_deferred_type_skips_extract_deferred_and_processes_directly(db):
    """A type not in DEFERRED_EXTRACTION_TYPES (e.g. youtube) never had the placeholder
    problem -- extract_deferred() must not be called for it."""
    ingestion = Mock()
    fake_node = SimpleNamespace(id="n3", url="https://youtube.com/watch?v=abc", type="youtube", title="A video")
    ingestion.ingest_url.return_value = fake_node
    graph = Mock()

    engine = _make_engine(db, ingestion, graph)
    engine._execute_tool("ingest_url", {"url": "https://youtube.com/watch?v=abc"}, [])

    ingestion.extract_deferred.assert_not_called()
    graph.process_node.assert_called_once_with("n3")


def test_extract_deferred_exception_marks_node_error_and_skips_process_node(db):
    """If extract_deferred() itself raises (vs. returning False), _safe_process's
    except block must mark the node as errored rather than leaving it stuck at
    status="processing" forever, and must not run process_node() over it."""
    with db.session_scope() as s:
        # Different URL than the tool call below, so the ingest_url branch's
        # duplicate-URL check doesn't short-circuit before ingest_url() is even called.
        s.add(NodeDB(id="n4", user_id="u1", type="webpage",
                      url="https://example.com/preexisting-n4", status="processing"))

    ingestion = Mock()
    fake_node = SimpleNamespace(id="n4", url="https://example.com/a", type="webpage", title="A page")
    ingestion.ingest_url.return_value = fake_node
    ingestion.extract_deferred.side_effect = RuntimeError("boom")
    graph = Mock()

    engine = _make_engine(db, ingestion, graph)
    engine._execute_tool("ingest_url", {"url": "https://example.com/a"}, [])

    graph.process_node.assert_not_called()
    with db.session_scope() as s:
        n = s.query(NodeDB).filter_by(id="n4").first()
        assert n.status == "error"
        assert "boom" in n.error_message
