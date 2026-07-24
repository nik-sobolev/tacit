"""Regression test for the empty-content-reaches-the-LLM bug.

Live incident: a tweet/X-Article extraction that came back empty (a
transient rate-limit/timeout on X's side, not "the content doesn't exist")
still reached graph_service.process_node(), which called the LLM with an
empty content field. The LLM described its own missing input ("the content
could not be retrieved... this entry serves as a placeholder") and that
got saved as a real, status="done" summary — a confidently wrong card that
also block retries at the URL-dedup layer. process_node() must refuse to
synthesize over empty content and instead mark the node "error" so the
existing resave-to-retry path (ingest.py / app.js) can pick it up.
"""

import uuid
from datetime import datetime
from unittest.mock import Mock

from app.db.database import NodeDB
from app.services.graph_service import GraphService


def _service(db):
    gs = object.__new__(GraphService)
    gs.db = db
    # Any call past the empty-content guard is a bug in this test's scenario —
    # fail loudly instead of silently hitting the network/LLM.
    gs.vector_service = Mock(side_effect=AssertionError("should not reach vector_service"))
    gs.client = Mock(side_effect=AssertionError("should not reach the LLM client"))
    return gs


def _make_node(db, content, status="processing", node_type="tweet"):
    node_id = str(uuid.uuid4())
    with db.session_scope() as session:
        session.add(NodeDB(
            id=node_id,
            user_id="test-user",
            type=node_type,
            title="placeholder",
            content=content,
            url="https://x.com/someone/status/123",
            status=status,
            tags=[],
            node_meta={},
            created_at=datetime.utcnow(),
        ))
    return node_id


def test_process_node_marks_error_on_empty_content(db):
    node_id = _make_node(db, content="")
    gs = _service(db)

    gs.process_node(node_id)

    with db.session_scope() as session:
        node = session.query(NodeDB).filter_by(id=node_id).first()
        assert node.status == "error"
        assert node.error_message
        assert node.summary is None, "must not have synthesized anything"


def test_process_node_marks_error_on_whitespace_only_content(db):
    node_id = _make_node(db, content="   \n\t  ")
    gs = _service(db)

    gs.process_node(node_id)

    with db.session_scope() as session:
        node = session.query(NodeDB).filter_by(id=node_id).first()
        assert node.status == "error"


def test_process_node_does_not_touch_already_done_node(db):
    """The guard uses n.status != 'done' before overwriting — a node that
    finished successfully between the read and the write (shouldn't happen
    with this single-threaded call, but matches the same guard used by the
    node-write path elsewhere) must not be clobbered back to error."""
    node_id = _make_node(db, content="", status="done")

    with db.session_scope() as session:
        node = session.query(NodeDB).filter_by(id=node_id).first()
        node.status = "done"

    gs = _service(db)
    gs.process_node(node_id)

    with db.session_scope() as session:
        node = session.query(NodeDB).filter_by(id=node_id).first()
        assert node.status == "done"
