"""Documents API endpoints"""

from fastapi import APIRouter, HTTPException, Request, UploadFile, File
from typing import List
import uuid
from datetime import datetime
from pathlib import Path

from ..models.document import Document, DocumentType, DocumentSearchQuery
from ..services.document_service import DocumentProcessor
from ..db.database import get_database, DocumentDB

router = APIRouter()

# Get database instance
db = get_database()

# Initialize document processor
doc_processor = DocumentProcessor()


@router.post("/documents/upload", response_model=Document)
async def upload_document(
    request: Request,
    file: UploadFile = File(...)
):
    """Upload a document to the knowledge base"""
    try:
        engine = request.app.state.engine

        # Validate file type
        file_ext = Path(file.filename).suffix.lower().lstrip('.')
        if file_ext not in ['pdf', 'docx', 'txt', 'md']:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported file type: {file_ext}. Supported: PDF, DOCX, TXT, MD"
            )

        # Read file content
        content = await file.read()
        file_size = len(content)

        # Check file size (50MB limit)
        max_size = 50 * 1024 * 1024  # 50MB in bytes
        if file_size > max_size:
            raise HTTPException(
                status_code=400,
                detail=f"File too large. Maximum size: 50MB"
            )

        # Save file
        saved_path = doc_processor.save_file(file.filename, content)

        # Extract text and create chunks
        extraction_result = doc_processor.extract_text(saved_path, file_ext)

        # Create document record
        document_id = str(uuid.uuid4())
        document = Document(
            id=document_id,
            filename=saved_path.name,
            original_filename=file.filename,
            type=DocumentType(file_ext),
            size_bytes=file_size,
            page_count=extraction_result.get('page_count'),
            word_count=extraction_result.get('word_count'),
            upload_date=datetime.utcnow(),
            metadata={
                'file_path': str(saved_path)
            }
        )

        # Store in SQLite
        session = db.get_session()
        db_document = DocumentDB(
            id=document_id,
            filename=saved_path.name,
            original_filename=file.filename,
            type=file_ext,
            size_bytes=file_size,
            page_count=extraction_result.get('page_count'),
            word_count=extraction_result.get('word_count'),
            upload_date=datetime.utcnow(),
            tags=[],
            description=None,
            metadata={'file_path': str(saved_path)}
        )
        session.add(db_document)
        session.commit()
        session.close()

        # Add chunks to vector database
        chunks = extraction_result['chunks']
        for chunk in chunks:
            chunk['metadata']['document_id'] = document_id
            chunk['metadata']['filename'] = file.filename
            chunk['metadata']['type'] = file_ext

        engine.vector_service.add_document_chunks(
            document_id=document_id,
            chunks=chunks
        )

        return document

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/documents", response_model=List[Document])
async def list_documents(
    type: DocumentType = None,
    limit: int = 50
):
    """List all uploaded documents"""
    try:
        session = db.get_session()
        query = session.query(DocumentDB)

        # Filter by type if specified
        if type:
            query = query.filter(DocumentDB.type == type.value)

        # Sort by upload date descending
        query = query.order_by(DocumentDB.upload_date.desc())

        db_documents = query.limit(limit).all()

        # Convert to Pydantic models
        documents = [
            Document(
                id=doc.id,
                filename=doc.filename,
                original_filename=doc.original_filename,
                type=DocumentType(doc.type),
                size_bytes=doc.size_bytes,
                page_count=doc.page_count,
                word_count=doc.word_count,
                upload_date=doc.upload_date,
                tags=doc.tags or [],
                description=doc.description,
                metadata=doc.extra_metadata or {}
            )
            for doc in db_documents
        ]

        session.close()
        return documents

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/documents/{document_id}", response_model=Document)
async def get_document(document_id: str):
    """Get a specific document by ID"""
    try:
        session = db.get_session()
        db_document = session.query(DocumentDB).filter(DocumentDB.id == document_id).first()
        session.close()

        if not db_document:
            raise HTTPException(status_code=404, detail="Document not found")

        return Document(
            id=db_document.id,
            filename=db_document.filename,
            original_filename=db_document.original_filename,
            type=DocumentType(db_document.type),
            size_bytes=db_document.size_bytes,
            page_count=db_document.page_count,
            word_count=db_document.word_count,
            upload_date=db_document.upload_date,
            tags=db_document.tags or [],
            description=db_document.description,
            metadata=db_document.extra_metadata or {}
        )

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.delete("/documents/{document_id}")
async def delete_document(request: Request, document_id: str):
    """Delete a document"""
    try:
        engine = request.app.state.engine
        session = db.get_session()

        db_document = session.query(DocumentDB).filter(DocumentDB.id == document_id).first()
        if not db_document:
            session.close()
            raise HTTPException(status_code=404, detail="Document not found")

        # Delete from vector database
        engine.vector_service.delete_document_chunks(document_id)

        # Delete file from disk
        file_path = Path(db_document.extra_metadata.get('file_path', ''))
        if file_path.exists():
            doc_processor.delete_file(file_path)

        # Delete from SQLite
        session.delete(db_document)
        session.commit()
        session.close()

        return {"success": True, "document_id": document_id}

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/documents/search")
async def search_documents(request: Request, query: DocumentSearchQuery):
    """Search documents semantically"""
    try:
        engine = request.app.state.engine

        # Build filter
        filter_dict = {}
        if query.document_types:
            filter_dict['type'] = [t.value for t in query.document_types]

        # Search in vector database
        results = engine.vector_service.search_documents(
            query=query.query,
            limit=query.limit,
            filter=filter_dict if filter_dict else None
        )

        # Enrich with document data from SQLite
        session = db.get_session()
        enriched_results = []
        for result in results:
            document_id = result['metadata'].get('document_id')
            if document_id:
                db_document = session.query(DocumentDB).filter(DocumentDB.id == document_id).first()
                if db_document:
                    enriched_results.append({
                        'document_id': document_id,
                        'filename': db_document.original_filename,
                        'chunk_content': result['content'],
                        'page_number': result['metadata'].get('page_number'),
                        'relevance_score': result['relevance_score'],
                        'metadata': result['metadata']
                    })

        session.close()

        return {
            "query": query.query,
            "results": enriched_results,
            "count": len(enriched_results)
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.get("/documents/stats/summary")
async def get_documents_stats():
    """Get statistics about uploaded documents"""
    try:
        session = db.get_session()
        all_docs = session.query(DocumentDB).all()

        total_docs = len(all_docs)
        total_size = sum(doc.size_bytes for doc in all_docs)
        total_words = sum(doc.word_count or 0 for doc in all_docs)

        # Group by type
        by_type = {}
        for doc in all_docs:
            type_val = doc.type
            if type_val not in by_type:
                by_type[type_val] = 0
            by_type[type_val] += 1

        session.close()

        return {
            'total_documents': total_docs,
            'total_size_mb': round(total_size / (1024 * 1024), 2),
            'total_words': total_words,
            'by_type': by_type
        }

    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
