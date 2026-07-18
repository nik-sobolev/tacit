"""Tests for vector_service.py's search_nodes() user_id filter (added in the "scope
ChromaDB vector search to the querying user" fix). Before this fix, semantic search,
auto-linking, and the chat search/focus tools queried ChromaDB across every tenant,
relying entirely on a downstream SQL ownership re-check to keep results private.
This confirms the new `filter` kwarg is actually threaded through to Chroma's
`where=` query parameter (and that an absent filter still passes where=None, so
existing behavior is unchanged for callers that don't pass one).

VectorService is built via object.__new__ to skip __init__, which stands up a real
ChromaDB PersistentClient and downloads an ONNX embedding model on first use --
neither of which search_nodes() needs. nodes_collection is replaced with a Mock that
mimics Chroma's query() response shape.
"""

from unittest.mock import Mock

from backend.app.services.vector_service import VectorService


def _make_service(nodes_collection):
    service = object.__new__(VectorService)
    service.nodes_collection = nodes_collection
    return service


def _chroma_response():
    return {
        "ids": [["node-1"]],
        "documents": [["some content"]],
        "metadatas": [[{"user_id": "u1", "title": "Node One"}]],
        "distances": [[0.1]],
    }


def test_search_nodes_passes_user_id_filter_as_where_clause():
    collection = Mock()
    collection.count.return_value = 3
    collection.query.return_value = _chroma_response()

    service = _make_service(collection)
    results = service.search_nodes("hello", limit=5, filter={"user_id": "u1"})

    collection.query.assert_called_once_with(
        query_texts=["hello"], n_results=3, where={"user_id": "u1"}
    )
    assert results[0]["id"] == "node-1"


def test_search_nodes_without_filter_passes_where_none():
    """Callers that don't pass a filter (none remain in this codebase after the
    fix, but the parameter is optional) must not silently get an empty where={}."""
    collection = Mock()
    collection.count.return_value = 3
    collection.query.return_value = _chroma_response()

    service = _make_service(collection)
    service.search_nodes("hello", limit=5)

    collection.query.assert_called_once_with(
        query_texts=["hello"], n_results=3, where=None
    )


def test_search_nodes_returns_empty_list_when_collection_is_empty():
    collection = Mock()
    collection.count.return_value = 0

    service = _make_service(collection)
    results = service.search_nodes("hello", filter={"user_id": "u1"})

    assert results == []
    collection.query.assert_not_called()
