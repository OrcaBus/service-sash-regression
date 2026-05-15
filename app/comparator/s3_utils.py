import logging
from pathlib import Path
from urllib.parse import urlparse

import boto3

logger = logging.getLogger(__name__)


def parse_s3_uri(s3_uri: str) -> tuple[str, str]:
    parsed = urlparse(s3_uri)
    return parsed.netloc, parsed.path.lstrip("/")


def download_s3_dir(s3_uri: str, local_dir: Path) -> None:
    """Recursively download all objects under s3_uri prefix into local_dir."""
    bucket, prefix = parse_s3_uri(s3_uri)
    s3 = boto3.client("s3")
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            key = obj["Key"]
            rel = key[len(prefix):].lstrip("/")
            dest = local_dir / rel
            dest.parent.mkdir(parents=True, exist_ok=True)
            logger.debug(f"Downloading s3://{bucket}/{key} → {dest}")
            s3.download_file(bucket, key, str(dest))


def upload_file(local_path: Path, s3_uri: str) -> None:
    bucket, key = parse_s3_uri(s3_uri)
    s3 = boto3.client("s3")
    logger.info(f"Uploading {local_path} → s3://{bucket}/{key}")
    s3.upload_file(str(local_path), bucket, key)
