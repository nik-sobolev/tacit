"""Tacit Twin Engine - Combines coaching, context, and document intelligence"""

import structlog
from typing import List, Dict, Any, Optional
from datetime import datetime
from anthropic import Anthropic

from .config import TacitConfig
from ..services.vector_service import VectorService
from ..models.chat import ChatMode

logger = structlog.get_logger()


class TacitEngine:
    """
    Main Tacit engine that orchestrates:
    - Executive coaching
    - Context retrieval
    - Document search
    - Unified intelligent responses
    """

    def __init__(self, config: Optional[TacitConfig] = None):
        """Initialize the Tacit engine"""

        self.config = config or TacitConfig.load()
        self.client = Anthropic(api_key=self.config.anthropic_api_key)
        self.vector_service = VectorService(self.config.chroma_persist_dir)

        # In-memory conversation storage (simplified for MVP)
        self.conversations: Dict[str, List[Dict[str, Any]]] = {}

        logger.info(
            "tacit_engine_initialized",
            model=self.config.default_model,
            user=self.config.user_name
        )

    def process_message(
        self,
        session_id: str,
        user_message: str,
        mode: Optional[ChatMode] = None
    ) -> Dict[str, Any]:
        """
        Process a user message and generate twin response

        Args:
            session_id: Session identifier
            user_message: Message from user
            mode: Optional chat mode (general, coaching, query)

        Returns:
            Dict with response, sources, and metadata
        """

        # Initialize conversation if needed
        if session_id not in self.conversations:
            self.conversations[session_id] = []

        # Add user message to conversation
        self.conversations[session_id].append({
            "role": "user",
            "content": user_message,
            "timestamp": datetime.utcnow()
        })

        # Determine mode if not specified
        if mode is None:
            mode = self._determine_mode(user_message)

        # Retrieve relevant context and documents
        knowledge = self._retrieve_knowledge(user_message)

        # Build enhanced prompt with knowledge
        system_prompt = self._build_prompt(mode, knowledge)

        # Generate response
        response_text = self._generate_response(
            system_prompt,
            self.conversations[session_id]
        )

        # Add assistant response to conversation
        self.conversations[session_id].append({
            "role": "assistant",
            "content": response_text,
            "timestamp": datetime.utcnow(),
            "mode": mode,
            "sources": knowledge.get('sources', [])
        })

        logger.info(
            "message_processed",
            session_id=session_id,
            mode=mode,
            sources_count=len(knowledge.get('sources', []))
        )

        return {
            "response": response_text,
            "mode": mode,
            "sources": knowledge.get('sources', []),
            "metadata": {
                "contexts_found": len(knowledge.get('contexts', [])),
                "documents_found": len(knowledge.get('documents', []))
            }
        }

    def _determine_mode(self, message: str) -> ChatMode:
        """Determine the appropriate chat mode based on message content"""

        message_lower = message.lower()

        # Coaching keywords
        coaching_keywords = [
            "help me think", "struggle", "challenge", "decision", "should i",
            "advice", "coach", "grow", "develop", "feedback", "improve"
        ]

        # Query keywords
        query_keywords = [
            "what did", "when did", "have i", "did i", "my decision",
            "tell me about", "find", "show me", "search"
        ]

        coaching_score = sum(1 for keyword in coaching_keywords if keyword in message_lower)
        query_score = sum(1 for keyword in query_keywords if keyword in message_lower)

        if coaching_score > query_score:
            return ChatMode.COACHING
        elif query_score > 0:
            return ChatMode.QUERY
        else:
            return ChatMode.GENERAL

    def _retrieve_knowledge(self, query: str) -> Dict[str, Any]:
        """Retrieve relevant contexts and documents"""

        try:
            # Search both contexts and documents
            results = self.vector_service.search_all(
                query=query,
                context_limit=self.config.context_top_k,
                document_limit=self.config.search_top_k
            )

            # Filter by relevance score
            relevant_contexts = [
                ctx for ctx in results.get('contexts', [])
                if ctx.get('relevance_score', 0) > self.config.min_relevance_score
            ]

            relevant_documents = [
                doc for doc in results.get('documents', [])
                if doc.get('relevance_score', 0) > self.config.min_relevance_score
            ]

            # Build sources list
            sources = []

            for ctx in relevant_contexts:
                sources.append({
                    'type': 'context',
                    'id': ctx['id'],
                    'title': ctx['metadata'].get('title', 'Untitled Context'),
                    'context_type': ctx['metadata'].get('type', 'unknown'),
                    'date': ctx['metadata'].get('created_at', None),
                    'relevance': round(ctx['relevance_score'], 2)
                })

            for doc in relevant_documents:
                sources.append({
                    'type': 'document',
                    'id': doc['metadata'].get('document_id', 'unknown'),
                    'filename': doc['metadata'].get('filename', 'Unknown Document'),
                    'page': doc['metadata'].get('page_number', None),
                    'relevance': round(doc['relevance_score'], 2)
                })

            return {
                'contexts': relevant_contexts,
                'documents': relevant_documents,
                'sources': sources
            }

        except Exception as e:
            logger.error("knowledge_retrieval_error", error=str(e))
            return {'contexts': [], 'documents': [], 'sources': []}

    def _build_prompt(self, mode: ChatMode, knowledge: Dict[str, Any]) -> str:
        """Build system prompt with retrieved knowledge"""

        base_prompt = self.config.get_system_prompt()

        # Add knowledge context if available
        knowledge_section = []

        if knowledge.get('contexts'):
            knowledge_section.append("\n## Relevant Contexts from Your Knowledge Base\n")
            for i, ctx in enumerate(knowledge['contexts'][:5], 1):  # Top 5
                title = ctx['metadata'].get('title', 'Untitled')
                ctx_type = ctx['metadata'].get('type', 'unknown')
                created = ctx['metadata'].get('created_at', 'unknown date')
                content = ctx['content']

                knowledge_section.append(f"{i}. **{title}** ({ctx_type}) - {created}")
                knowledge_section.append(f"   {content}\n")

        if knowledge.get('documents'):
            knowledge_section.append("\n## Relevant Document Excerpts\n")
            for i, doc in enumerate(knowledge['documents'][:3], 1):  # Top 3
                filename = doc['metadata'].get('filename', 'Unknown')
                page = doc['metadata'].get('page_number', '?')
                content = doc['content'][:500]  # Truncate if too long

                knowledge_section.append(f"{i}. **{filename}** (page {page})")
                knowledge_section.append(f"   {content}...\n")

        # Add mode-specific guidance
        mode_guidance = ""
        if mode == ChatMode.COACHING:
            mode_guidance = self.config.get_coaching_prompt_addition()
        elif mode == ChatMode.QUERY:
            mode_guidance = """
## Query Mode

The user is looking for specific information from their knowledge base.
- Answer directly based on the provided contexts and documents
- Cite specific sources
- If information is not in the knowledge base, say so clearly
- Be concise and factual
"""

        return "\n".join([
            base_prompt,
            "".join(knowledge_section) if knowledge_section else "",
            mode_guidance
        ])

    def _generate_response(
        self,
        system_prompt: str,
        conversation: List[Dict[str, Any]]
    ) -> str:
        """Generate response using Claude"""

        # Convert conversation to Claude format
        messages = [
            {"role": msg["role"], "content": msg["content"]}
            for msg in conversation
            if msg["role"] in ["user", "assistant"]
        ]

        try:
            response = self.client.messages.create(
                model=self.config.default_model,
                max_tokens=self.config.max_tokens,
                temperature=self.config.temperature,
                system=system_prompt,
                messages=messages
            )

            return response.content[0].text

        except Exception as e:
            logger.error("claude_api_error", error=str(e))
            return (
                "I apologize, but I'm having a technical issue at the moment. "
                "Could you please try rephrasing your question?"
            )

    def get_conversation(self, session_id: str) -> List[Dict[str, Any]]:
        """Get conversation history for a session"""
        return self.conversations.get(session_id, [])

    def clear_conversation(self, session_id: str) -> None:
        """Clear conversation history for a session"""
        if session_id in self.conversations:
            del self.conversations[session_id]
            logger.info("conversation_cleared", session_id=session_id)

    def get_stats(self) -> Dict[str, Any]:
        """Get engine statistics"""
        vector_stats = self.vector_service.get_stats()

        return {
            'active_conversations': len(self.conversations),
            'total_messages': sum(len(conv) for conv in self.conversations.values()),
            'vector_db': vector_stats,
            'config': {
                'user': self.config.user_name,
                'model': self.config.default_model
            }
        }
