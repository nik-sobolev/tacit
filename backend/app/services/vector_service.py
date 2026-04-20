"""Vector database service using ChromaDB"""

import os
import structlog
from typing import List, Dict, Any, Optional
import chromadb
from chromadb.utils import embedding_functions

logger = structlog.get_logger()


class VectorService:
    """Service for managing vector embeddings and semantic search"""

    def __init__(self, persist_directory: str = "./data/chroma"):
        """Initialize ChromaDB client"""

        # Resolve to absolute path so it persists regardless of cwd
        abs_dir = os.path.abspath(persist_directory)
        os.makedirs(abs_dir, exist_ok=True)
        self.client = chromadb.PersistentClient(path=abs_dir)

        # Use default embedding function (faster startup, no extra dependencies)
        self.embedding_function = embedding_functions.DefaultEmbeddingFunction()

        # Initialize collections
        self.contexts_collection = self._get_or_create_collection("contexts")
        self.documents_collection = self._get_or_create_collection("documents")
        self.nodes_collection = self._get_or_create_collection("nodes")

        logger.info("vector_service_initialized", persist_directory=persist_directory)

    def _get_or_create_collection(self, name: str):
        """Get or create a ChromaDB collection"""
        try:
            return self.client.get_or_create_collection(
                name=name,
                embedding_function=self.embedding_function,
                metadata={"hnsw:space": "cosine"}
            )
        except Exception as e:
            logger.error("collection_creation_error", name=name, error=str(e))
            raise

    # ==================== CONTEXT OPERATIONS ====================

    def add_context(
        self,
        context_id: str,
        content: str,
        metadata: Dict[str, Any]
    ) -> None:
        """Add a context to the vector database"""
        try:
            self.contexts_collection.add(
                ids=[context_id],
                documents=[content],
                metadatas=[metadata]
            )
            logger.debug("context_added_to_vector_db", context_id=context_id)
        except Exception as e:
            logger.error("context_add_error", context_id=context_id, error=str(e))
            raise

    def search_contexts(
        self,
        query: str,
        limit: int = 10,
        filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Search contexts semantically"""
        try:
            results = self.contexts_collection.query(
                query_texts=[query],
                n_results=limit,
                where=filter if filter else None
            )

            # Format results
            formatted_results = []
            if results and results['ids'] and len(results['ids'][0]) > 0:
                for i in range(len(results['ids'][0])):
                    formatted_results.append({
                        'id': results['ids'][0][i],
                        'content': results['documents'][0][i],
                        'metadata': results['metadatas'][0][i],
                        'distance': results['distances'][0][i] if 'distances' in results else None,
                        'relevance_score': 1 - results['distances'][0][i] if 'distances' in results else 1.0
                    })

            logger.debug("context_search_completed", query_length=len(query), results_count=len(formatted_results))
            return formatted_results

        except Exception as e:
            logger.error("context_search_error", error=str(e))
            return []

    def update_context(
        self,
        context_id: str,
        content: str,
        metadata: Dict[str, Any]
    ) -> None:
        """Update a context in the vector database"""
        try:
            self.contexts_collection.update(
                ids=[context_id],
                documents=[content],
                metadatas=[metadata]
            )
            logger.debug("context_updated_in_vector_db", context_id=context_id)
        except Exception as e:
            logger.error("context_update_error", context_id=context_id, error=str(e))
            raise

    def delete_context(self, context_id: str) -> None:
        """Delete a context from the vector database"""
        try:
            self.contexts_collection.delete(ids=[context_id])
            logger.debug("context_deleted_from_vector_db", context_id=context_id)
        except Exception as e:
            logger.error("context_delete_error", context_id=context_id, error=str(e))
            raise

    # ==================== DOCUMENT OPERATIONS ====================

    def add_document_chunks(
        self,
        document_id: str,
        chunks: List[Dict[str, Any]]
    ) -> None:
        """Add document chunks to the vector database"""
        try:
            ids = [f"{document_id}_chunk_{i}" for i in range(len(chunks))]
            documents = [chunk['content'] for chunk in chunks]
            metadatas = [chunk.get('metadata', {}) for chunk in chunks]

            # Add document_id to all metadatas
            for metadata in metadatas:
                metadata['document_id'] = document_id

            self.documents_collection.add(
                ids=ids,
                documents=documents,
                metadatas=metadatas
            )
            logger.info("document_chunks_added", document_id=document_id, chunks_count=len(chunks))
        except Exception as e:
            logger.error("document_chunks_add_error", document_id=document_id, error=str(e))
            raise

    def search_documents(
        self,
        query: str,
        limit: int = 5,
        filter: Optional[Dict[str, Any]] = None
    ) -> List[Dict[str, Any]]:
        """Search document chunks semantically"""
        try:
            results = self.documents_collection.query(
                query_texts=[query],
                n_results=limit,
                where=filter if filter else None
            )

            # Format results
            formatted_results = []
            if results and results['ids'] and len(results['ids'][0]) > 0:
                for i in range(len(results['ids'][0])):
                    formatted_results.append({
                        'chunk_id': results['ids'][0][i],
                        'content': results['documents'][0][i],
                        'metadata': results['metadatas'][0][i],
                        'distance': results['distances'][0][i] if 'distances' in results else None,
                        'relevance_score': 1 - results['distances'][0][i] if 'distances' in results else 1.0
                    })

            logger.debug("document_search_completed", query_length=len(query), results_count=len(formatted_results))
            return formatted_results

        except Exception as e:
            logger.error("document_search_error", error=str(e))
            return []

    def delete_document_chunks(self, document_id: str) -> None:
        """Delete all chunks for a document"""
        try:
            # Query all chunks for this document
            results = self.documents_collection.get(
                where={"document_id": document_id}
            )

            if results and results['ids']:
                self.documents_collection.delete(ids=results['ids'])
                logger.info("document_chunks_deleted", document_id=document_id, chunks_count=len(results['ids']))
        except Exception as e:
            logger.error("document_chunks_delete_error", document_id=document_id, error=str(e))
            raise

    # ==================== NODE OPERATIONS ====================

    def add_node(self, node_id: str, content: str, metadata: Dict[str, Any]) -> None:
        """Add or update a canvas node in the vector database"""
        try:
            self.nodes_collection.upsert(
                ids=[node_id],
                documents=[content[:8000]],  # cap at 8k chars
                metadatas=[metadata]
            )
            logger.debug("node_upserted_to_vector_db", node_id=node_id)
        except Exception as e:
            logger.error("node_add_error", node_id=node_id, error=str(e))
            raise

    def search_nodes(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        """Search canvas nodes semantically"""
        try:
            count = self.nodes_collection.count()
            if count == 0:
                return []
            results = self.nodes_collection.query(
                query_texts=[query],
                n_results=min(limit, count)
            )
            formatted = []
            if results and results['ids'] and len(results['ids'][0]) > 0:
                for i in range(len(results['ids'][0])):
                    formatted.append({
                        'id': results['ids'][0][i],
                        'content': results['documents'][0][i],
                        'metadata': results['metadatas'][0][i],
                        'relevance_score': 1 - results['distances'][0][i] if 'distances' in results else 1.0
                    })
            return formatted
        except Exception as e:
            logger.error("node_search_error", error=str(e))
            return []

    def delete_node(self, node_id: str) -> None:
        """Delete a node from vector database"""
        try:
            self.nodes_collection.delete(ids=[node_id])
        except Exception as e:
            logger.error("node_delete_error", node_id=node_id, error=str(e))

    # ==================== HYBRID SEARCH ====================

    def search_all(
        self,
        query: str,
        context_limit: int = 5,
        document_limit: int = 3,
        node_limit: int = 5
    ) -> Dict[str, List[Dict[str, Any]]]:
        """Search contexts, documents, and graph nodes"""
        return {
            'contexts': self.search_contexts(query, limit=context_limit),
            'documents': self.search_documents(query, limit=document_limit),
            'nodes': self.search_nodes(query, limit=node_limit)
        }

    # ==================== STATS & MANAGEMENT ====================

    def get_stats(self) -> Dict[str, Any]:
        """Get statistics about the vector database"""
        try:
            context_count = self.contexts_collection.count()
            document_count = self.documents_collection.count()

            node_count = self.nodes_collection.count()
            return {
                'contexts_count': context_count,
                'document_chunks_count': document_count,
                'nodes_count': node_count,
                'total_items': context_count + document_count + node_count
            }
        except Exception as e:
            logger.error("stats_retrieval_error", error=str(e))
            return {'error': str(e)}

    def reset_collection(self, collection_name: str) -> None:
        """Reset a collection (delete all items)"""
        try:
            self.client.delete_collection(collection_name)
            if collection_name == "contexts":
                self.contexts_collection = self._get_or_create_collection("contexts")
            elif collection_name == "documents":
                self.documents_collection = self._get_or_create_collection("documents")
            logger.warning("collection_reset", collection_name=collection_name)
        except Exception as e:
            logger.error("collection_reset_error", collection_name=collection_name, error=str(e))
            raise
