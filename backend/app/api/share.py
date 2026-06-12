"""Share token API — generate read-only canvas links"""

import uuid
import structlog
from fastapi import APIRouter, HTTPException, Depends
from pydantic import BaseModel
from typing import Optional

from ..db.database import get_database, ShareTokenDB
from ..core.auth import get_current_user

logger = structlog.get_logger()
router = APIRouter()


class ShareCreateRequest(BaseModel):
    label: Optional[str] = None


@router.post("/share")
async def create_share_token(body: ShareCreateRequest, current_user: dict = Depends(get_current_user)):
    """Create a share token (requires authentication)"""
    from datetime import datetime
    token = str(uuid.uuid4())
    created_at = datetime.utcnow()
    db = get_database()
    with db.session_scope() as session:
        session.add(ShareTokenDB(
            token=token,
            user_id=current_user["id"],
            label=body.label,
            created_at=created_at
        ))
    logger.info("share_token_created", token=token[:8], label=body.label, user_id=current_user["id"])
    return {"token": token, "label": body.label, "created_at": created_at.isoformat()}


@router.get("/share")
async def list_share_tokens(current_user: dict = Depends(get_current_user)):
    """List share tokens created by current user"""
    db = get_database()
    with db.session_scope() as session:
        rows = session.query(ShareTokenDB).filter_by(user_id=current_user["id"]).all()
        return [
            {
                "token": r.token,
                "label": r.label,
                "created_at": r.created_at.isoformat() if r.created_at else None,
                "revoked": bool(r.revoked),
            }
            for r in rows
        ]


@router.delete("/share/{token}")
async def revoke_share_token(token: str, current_user: dict = Depends(get_current_user)):
    """Revoke a share token (must be owner)"""
    db = get_database()
    with db.session_scope() as session:
        row = session.query(ShareTokenDB).filter_by(token=token, user_id=current_user["id"]).first()
        if not row:
            raise HTTPException(status_code=404, detail="Token not found or you don't have permission")
        row.revoked = 1
        session.commit()
    logger.info("share_token_revoked", token=token[:8], user_id=current_user["id"])
    return {"ok": True}
