"""Document processing service"""

import structlog
from typing import List, Dict, Any, Optional
from pathlib import Path
import io
import PyPDF2
import docx
from datetime import datetime
import os

logger = structlog.get_logger()


class DocumentProcessor:
    """Service for processing and extracting text from documents"""

    def __init__(self, upload_dir: str = str(Path(os.getenv("DATA_DIR", str(Path.home() / ".tacit" / "data"))) / "uploads")):
        self.upload_dir = Path(upload_dir)
        self.upload_dir.mkdir(parents=True, exist_ok=True)

    def extract_text(self, file_path: Path, file_type: str) -> Dict[str, Any]:
        """
        Extract text from a document

        Returns:
            Dict with text, page_count, word_count, and chunks
        """
        try:
            if file_type == "pdf":
                return self._extract_pdf(file_path)
            elif file_type == "docx":
                return self._extract_docx(file_path)
            elif file_type in ["txt", "md"]:
                return self._extract_text_file(file_path)
            else:
                raise ValueError(f"Unsupported file type: {file_type}")

        except Exception as e:
            logger.error("text_extraction_error", file_path=str(file_path), error=str(e))
            raise

    def extract_from_bytes(self, content: bytes, file_type: str, title_hint: str = None) -> Dict[str, Any]:
        """Extract text from an in-memory document (bytes) without persisting it.

        URL-first counterpart to extract_text(): used when a dropped URL resolves
        to a PDF/DOCX/TXT/MD, so the source of record stays the URL and Tacit never
        stores the file. Returns the same shape as extract_text plus an optional
        'title' pulled from document metadata when available.
        """
        buf = io.BytesIO(content)
        if file_type == "pdf":
            result = self._extract_pdf(buf)
        elif file_type == "docx":
            result = self._extract_docx(buf)
        elif file_type in ("txt", "md"):
            result = self._extract_text_file(buf)
        else:
            raise ValueError(f"Unsupported file type: {file_type}")
        result.setdefault("title", title_hint)
        return result

    def _extract_pdf(self, source) -> Dict[str, Any]:
        """Extract text from a PDF given a path or a file-like/BytesIO object."""
        try:
            file = open(source, 'rb') if isinstance(source, (str, Path)) else source
            try:
                pdf_reader = PyPDF2.PdfReader(file)
                page_count = len(pdf_reader.pages)

                # Prefer the PDF's embedded document title when present
                doc_title = None
                try:
                    doc_title = (pdf_reader.metadata or {}).title
                except Exception:
                    doc_title = None

                # Extract text from all pages
                pages_text = []
                for page_num, page in enumerate(pdf_reader.pages):
                    text = page.extract_text()
                    if text.strip():
                        pages_text.append({
                            'page_number': page_num + 1,
                            'text': text
                        })

                # Combine all text
                full_text = "\n\n".join([p['text'] for p in pages_text])
                word_count = len(full_text.split())

                # Create chunks
                chunks = self._create_chunks(pages_text)

                return {
                    'text': full_text,
                    'title': (doc_title or None),
                    'page_count': page_count,
                    'word_count': word_count,
                    'chunks': chunks
                }
            finally:
                if isinstance(source, (str, Path)):
                    file.close()

        except Exception as e:
            logger.error("pdf_extraction_error", source=str(source)[:200], error=str(e))
            raise

    def _extract_docx(self, source) -> Dict[str, Any]:
        """Extract text from a DOCX given a path or a file-like/BytesIO object."""
        try:
            # python-docx accepts a path string or a file-like object directly
            doc = docx.Document(source)

            # Extract paragraphs
            paragraphs = [para.text for para in doc.paragraphs if para.text.strip()]
            full_text = "\n\n".join(paragraphs)
            word_count = len(full_text.split())

            # Create chunks (no page numbers for docx)
            chunks = self._create_chunks([{'page_number': None, 'text': full_text}])

            return {
                'text': full_text,
                'page_count': None,
                'word_count': word_count,
                'chunks': chunks
            }

        except Exception as e:
            logger.error("docx_extraction_error", source=str(source)[:200], error=str(e))
            raise

    def _extract_text_file(self, source) -> Dict[str, Any]:
        """Extract text from a plain-text/markdown path or file-like/BytesIO object."""
        try:
            if isinstance(source, (str, Path)):
                with open(source, 'r', encoding='utf-8') as file:
                    full_text = file.read()
            else:
                # BytesIO / raw bytes buffer — decode leniently
                raw = source.read()
                full_text = raw.decode('utf-8', errors='replace') if isinstance(raw, bytes) else raw

            word_count = len(full_text.split())

            # Create chunks
            chunks = self._create_chunks([{'page_number': None, 'text': full_text}])

            return {
                'text': full_text,
                'page_count': None,
                'word_count': word_count,
                'chunks': chunks
            }

        except Exception as e:
            logger.error("text_file_extraction_error", source=str(source)[:200], error=str(e))
            raise

    def _create_chunks(
        self,
        pages_text: List[Dict[str, Any]],
        chunk_size: int = 500,
        overlap: int = 50
    ) -> List[Dict[str, Any]]:
        """
        Split text into overlapping chunks for vector embedding

        Args:
            pages_text: List of dicts with page_number and text
            chunk_size: Target words per chunk
            overlap: Words to overlap between chunks
        """
        chunks = []
        chunk_index = 0

        for page in pages_text:
            page_number = page['page_number']
            words = page['text'].split()

            # Split page into chunks
            i = 0
            while i < len(words):
                # Get chunk with overlap
                chunk_words = words[i:i + chunk_size]
                chunk_text = " ".join(chunk_words)

                if chunk_text.strip():
                    chunks.append({
                        'chunk_index': chunk_index,
                        'content': chunk_text,
                        'page_number': page_number,
                        'word_count': len(chunk_words),
                        'metadata': {}
                    })
                    chunk_index += 1

                # Move forward by chunk_size - overlap
                i += chunk_size - overlap

        logger.info("chunks_created", total_chunks=len(chunks))
        return chunks

    def save_file(self, filename: str, content: bytes) -> Path:
        """Save uploaded file to disk"""
        try:
            # Generate unique filename
            timestamp = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
            safe_filename = f"{timestamp}_{filename}"
            file_path = self.upload_dir / safe_filename

            # Write file
            with open(file_path, 'wb') as f:
                f.write(content)

            logger.info("file_saved", filename=safe_filename, size=len(content))
            return file_path

        except Exception as e:
            logger.error("file_save_error", filename=filename, error=str(e))
            raise

    def delete_file(self, file_path: Path) -> None:
        """Delete a file from disk"""
        try:
            if file_path.exists():
                os.remove(file_path)
                logger.info("file_deleted", file_path=str(file_path))
        except Exception as e:
            logger.error("file_delete_error", file_path=str(file_path), error=str(e))
            raise
