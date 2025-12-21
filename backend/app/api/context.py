"""Context API endpoints"""

from fastapi import APIRouter, HTTPException, Request
from typing import List
import uuid
from datetime import datetime

from ..models.context import Context, ContextCreate, ContextUpdate, ContextSearchQuery, ContextType
from ..db.database import get_database, ContextDB

router = APIRouter()

# Get database instance
db = get_database()


@router.post("/context", response_model=Context)
async def create_context(request: Request, context_data: ContextCreate):
    """Create a new context"""
    try:
        engine = request.app.state.engine
        session = db.get_session()

        # Create context object
        context_id = str(uuid.uuid4())
        context = Context(
            id=context_id,
            title=context_data.title,
            type=context_data.type,
            content=context_data.content,
            tags=context_data.tags,
            related_to=context_data.related_to,
            created_at=datetime.utcnow()
        )

        # Store in SQLite
        db_context = ContextDB(
            id=context_id,
            title=context.title,
            type=context.type.value,
            content=context.content,
            tags=context.tags,
            related_to=context.related_to,
            created_at=context.created_at,
            extra_metadata={}
        )
        session.add(db_context)
        session.commit()
        session.close()

        # Add to vector database
        engine.vector_service.add_context(
            context_id=context_id,
            content=f"{context.title}\n\n{context.content}",
            metadata={
                'title': context.title,
                'type': context.type.value,
                'tags': ','.join(context.tags) if context.tags else '',
                'related_to': context.related_to or '',
                'created_at': context.created_at.isoformat()
            }
        )

        return context

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/context", response_model=List[Context])
async def list_contexts(
    type: ContextType = None,
    limit: int = 50
):
    """List all contexts with optional filtering"""
    try:
        session = db.get_session()
        query = session.query(ContextDB)

        # Filter by type if specified
        if type:
            query = query.filter(ContextDB.type == type.value)

        # Sort by created_at descending
        query = query.order_by(ContextDB.created_at.desc())

        db_contexts = query.limit(limit).all()

        # Convert to Pydantic models
        contexts = [
            Context(
                id=ctx.id,
                title=ctx.title,
                type=ContextType(ctx.type),
                content=ctx.content,
                tags=ctx.tags or [],
                related_to=ctx.related_to,
                created_at=ctx.created_at,
                updated_at=ctx.updated_at,
                metadata=ctx.extra_metadata or {}
            )
            for ctx in db_contexts
        ]

        session.close()
        return contexts

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/context/{context_id}", response_model=Context)
async def get_context(context_id: str):
    """Get a specific context by ID"""
    try:
        session = db.get_session()
        db_context = session.query(ContextDB).filter(ContextDB.id == context_id).first()
        session.close()

        if not db_context:
            raise HTTPException(status_code=404, detail="Context not found")

        return Context(
            id=db_context.id,
            title=db_context.title,
            type=ContextType(db_context.type),
            content=db_context.content,
            tags=db_context.tags or [],
            related_to=db_context.related_to,
            created_at=db_context.created_at,
            updated_at=db_context.updated_at,
            metadata=db_context.extra_metadata or {}
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.put("/context/{context_id}", response_model=Context)
async def update_context(request: Request, context_id: str, update_data: ContextUpdate):
    """Update an existing context"""
    try:
        engine = request.app.state.engine
        session = db.get_session()

        db_context = session.query(ContextDB).filter(ContextDB.id == context_id).first()
        if not db_context:
            session.close()
            raise HTTPException(status_code=404, detail="Context not found")

        # Update fields
        if update_data.title is not None:
            db_context.title = update_data.title
        if update_data.type is not None:
            db_context.type = update_data.type.value
        if update_data.content is not None:
            db_context.content = update_data.content
        if update_data.tags is not None:
            db_context.tags = update_data.tags
        if update_data.related_to is not None:
            db_context.related_to = update_data.related_to

        db_context.updated_at = datetime.utcnow()

        session.commit()

        # Update in vector database
        engine.vector_service.update_context(
            context_id=context_id,
            content=f"{db_context.title}\n\n{db_context.content}",
            metadata={
                'title': db_context.title,
                'type': db_context.type,
                'tags': ','.join(db_context.tags) if db_context.tags else '',
                'related_to': db_context.related_to or '',
                'created_at': db_context.created_at.isoformat(),
                'updated_at': db_context.updated_at.isoformat()
            }
        )

        # Convert to Pydantic model
        context = Context(
            id=db_context.id,
            title=db_context.title,
            type=ContextType(db_context.type),
            content=db_context.content,
            tags=db_context.tags or [],
            related_to=db_context.related_to,
            created_at=db_context.created_at,
            updated_at=db_context.updated_at,
            metadata=db_context.extra_metadata or {}
        )

        session.close()
        return context

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/context/{context_id}")
async def delete_context(request: Request, context_id: str):
    """Delete a context"""
    try:
        engine = request.app.state.engine
        session = db.get_session()

        db_context = session.query(ContextDB).filter(ContextDB.id == context_id).first()
        if not db_context:
            session.close()
            raise HTTPException(status_code=404, detail="Context not found")

        # Delete from SQLite
        session.delete(db_context)
        session.commit()
        session.close()

        # Delete from vector database
        engine.vector_service.delete_context(context_id)

        return {"success": True, "context_id": context_id}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/context/search")
async def search_contexts(request: Request, query: ContextSearchQuery):
    """Search contexts semantically"""
    try:
        engine = request.app.state.engine
        session = db.get_session()

        # Build filter
        filter_dict = {}
        if query.type:
            filter_dict['type'] = query.type.value

        # Search in vector database
        results = engine.vector_service.search_contexts(
            query=query.query,
            limit=query.limit,
            filter=filter_dict if filter_dict else None
        )

        # Enrich with full context data from SQLite
        enriched_results = []
        for result in results:
            context_id = result['id']
            db_context = session.query(ContextDB).filter(ContextDB.id == context_id).first()

            if db_context:
                context = Context(
                    id=db_context.id,
                    title=db_context.title,
                    type=ContextType(db_context.type),
                    content=db_context.content,
                    tags=db_context.tags or [],
                    related_to=db_context.related_to,
                    created_at=db_context.created_at,
                    updated_at=db_context.updated_at,
                    metadata=db_context.metadata or {}
                )
                enriched_results.append({
                    **context.dict(),
                    'relevance_score': result['relevance_score']
                })

        session.close()

        return {
            "query": query.query,
            "results": enriched_results,
            "count": len(enriched_results)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/context/types/list")
async def list_context_types():
    """Get list of all available context types"""
    return {
        "types": [
            {"value": t.value, "label": t.value.replace('_', ' ').title()}
            for t in ContextType
        ]
    }
