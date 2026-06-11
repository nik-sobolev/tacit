"""Storage service — local filesystem (dev) or S3 (production).

When S3_BUCKET is set, images are uploaded to S3 and the public S3 URL is
returned. Otherwise files are saved to the local uploads/ directory and a
relative /uploads/... URL is returned.
"""

import os

_S3_BUCKET = os.getenv("S3_BUCKET")
_S3_REGION = os.getenv("AWS_DEFAULT_REGION", "us-west-2")

_EXT_CONTENT_TYPE = {
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "png": "image/png",
    "gif": "image/gif",
    "webp": "image/webp",
}


def upload_image(content: bytes, filename: str, file_ext: str = "jpg") -> str:
    """Upload image bytes and return the URL to store as thumbnail_url."""
    content_type = _EXT_CONTENT_TYPE.get(file_ext.lower(), "image/jpeg")

    if _S3_BUCKET:
        import boto3
        s3 = boto3.client("s3")
        key = f"images/{filename}"
        s3.put_object(
            Bucket=_S3_BUCKET,
            Key=key,
            Body=content,
            ContentType=content_type,
        )
        return f"https://{_S3_BUCKET}.s3.{_S3_REGION}.amazonaws.com/{key}"
    else:
        from ..db.database import DEFAULT_DATA_DIR
        images_dir = DEFAULT_DATA_DIR / "uploads" / "images"
        images_dir.mkdir(parents=True, exist_ok=True)
        path = images_dir / filename
        with open(path, "wb") as f:
            f.write(content)
        return f"/uploads/images/{filename}"
