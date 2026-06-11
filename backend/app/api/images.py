"""Images API — drag-drop image upload to canvas"""

import uuid
from pathlib import Path
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request, UploadFile, File, Depends

from ..core.auth import get_current_user
from ..db.database import get_database, NodeDB
from ..services.storage_service import upload_image as store_image

router = APIRouter()


@router.post("/images/upload")
async def upload_image(
    request: Request,
    file: UploadFile = File(...),
    current_user: dict = Depends(get_current_user),
):
    """Upload an image and create a canvas node"""
    try:
        # Validate file type
        file_ext = Path(file.filename).suffix.lower().lstrip('.')
        if file_ext not in ['jpg', 'jpeg', 'png', 'gif', 'webp']:
            raise HTTPException(
                status_code=400,
                detail=f"Unsupported image type: {file_ext}. Supported: JPG, PNG, GIF, WebP"
            )

        # Read and validate file size
        content = await file.read()
        file_size = len(content)
        max_size = 20 * 1024 * 1024  # 20MB
        if file_size > max_size:
            raise HTTPException(
                status_code=400,
                detail="Image too large. Maximum size: 20MB"
            )

        node_id = str(uuid.uuid4())
        sanitized_name = file.filename.replace(" ", "_")
        saved_filename = f"{node_id}_{sanitized_name}"

        thumbnail_url = store_image(content, saved_filename, file_ext)

        db = get_database()
        node = NodeDB(
            id=node_id,
            user_id=current_user["id"],
            type="image",
            title=file.filename,
            thumbnail_url=thumbnail_url,
            canvas_x=100.0,
            canvas_y=100.0,
            status="done",
            created_at=datetime.utcnow(),
            processed_at=datetime.utcnow(),
        )

        with db.session_scope() as session:
            session.add(node)

        return {
            "node_id": node_id,
            "type": "image",
            "title": file.filename,
            "thumbnail_url": thumbnail_url,
            "status": "done",
            "canvas_x": 100.0,
            "canvas_y": 100.0,
        }

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
