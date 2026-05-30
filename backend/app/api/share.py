"""Share token API — generate read-only canvas links"""

import uuid
import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from typing import Optional

from ..db.database import get_database, ShareTokenDB

logger = structlog.get_logger()
router = APIRouter()


class ShareCreateRequest(BaseModel):
    label: Optional[str] = None


@router.post("/share")
async def create_share_token(body: ShareCreateRequest):
    from datetime import datetime
    token = str(uuid.uuid4())
    created_at = datetime.utcnow()
    db = get_database()
    with db.session_scope() as session:
        session.add(ShareTokenDB(token=token, label=body.label, created_at=created_at))
    logger.info("share_token_created", token=token[:8], label=body.label)
    return {"token": token, "label": body.label, "created_at": created_at.isoformat()}


@router.get("/share")
async def list_share_tokens():
    db = get_database()
    with db.session_scope() as session:
        rows = session.query(ShareTokenDB).all()
        return [
            {
                "token": r.token,
                "label": r.label,
                "created_at": r.created_at,
                "revoked": r.revoked,
            }
            for r in rows
        ]


@router.delete("/share/{token}")
async def revoke_share_token(token: str):
    db = get_database()
    with db.session_scope() as session:
        row = session.query(ShareTokenDB).filter_by(token=token).first()
        if not row:
            raise HTTPException(status_code=404, detail="Token not found")
        row.revoked = 1
    logger.info("share_token_revoked", token=token[:8])
    return {"ok": True}
