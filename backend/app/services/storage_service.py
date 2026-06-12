"""Storage service — saves uploaded images to DATA_DIR/uploads/images/.

On Render, DATA_DIR points to a persistent disk mount so images survive redeploys.
Locally it falls back to ~/.tacit/data.
"""

from pathlib import Path
from ..db.database import DEFAULT_DATA_DIR


def upload_image(content: bytes, filename: str) -> str:
    """Save image bytes to disk, return the URL path."""
    images_dir = DEFAULT_DATA_DIR / "uploads" / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    (images_dir / filename).write_bytes(content)
    return f"/uploads/images/{filename}"
