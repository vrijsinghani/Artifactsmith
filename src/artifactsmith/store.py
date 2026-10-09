"""S3-compatible byte store. Layout: <workspace>/<artifact_id>/v<NNN>/<file>."""

from __future__ import annotations

import boto3
from botocore.config import Config as BotoConfig

from .config import CFG


def vdir(version: int) -> str:
    return f"v{version:03d}"


class Store:
    def __init__(self):
        ak, sk = CFG.store_credentials()
        if not CFG.store_endpoint or not ak or not sk:
            raise RuntimeError("object store is not configured (AM_STORE_ENDPOINT / AM_STORE_KEY / AM_STORE_SECRET)")
        self.bucket = CFG.store_bucket
        self.s3 = boto3.client(
            "s3",
            endpoint_url=CFG.store_endpoint,
            aws_access_key_id=ak,
            aws_secret_access_key=sk,
            region_name="us-east-1",
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3},
                connect_timeout=10,
                read_timeout=60,
            ),
        )

    @staticmethod
    def prefix(workspace: str, artifact_id: str, version: int | None = None) -> str:
        p = f"{workspace}/{artifact_id}/"
        return p + (vdir(version) + "/" if version is not None else "")

    def ensure_bucket(self) -> bool:
        """Create the bucket and enable versioning. Returns True if newly created."""
        try:
            self.s3.head_bucket(Bucket=self.bucket)
            created = False
        except Exception:
            self.s3.create_bucket(Bucket=self.bucket)
            created = True
        try:
            self.s3.put_bucket_versioning(Bucket=self.bucket, VersioningConfiguration={"Status": "Enabled"})
        except Exception:
            pass
        return created

    def put(self, key: str, data: bytes, content_type: str) -> str:
        r = self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        return r.get("VersionId", "")

    def get(self, key: str) -> tuple[bytes, str]:
        r = self.s3.get_object(Bucket=self.bucket, Key=key)
        return r["Body"].read(), r.get("ContentType", "application/octet-stream")

    def purge_prefix(self, prefix: str) -> int:
        """Hard-delete every object version and delete marker under a prefix."""
        n = 0
        paginator = self.s3.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            for item in page.get("Versions", []) + page.get("DeleteMarkers", []):
                self.s3.delete_object(Bucket=self.bucket, Key=item["Key"], VersionId=item["VersionId"])
                n += 1
        return n

    def count_prefix_versions(self, prefix: str) -> int:
        n = 0
        paginator = self.s3.get_paginator("list_object_versions")
        for page in paginator.paginate(Bucket=self.bucket, Prefix=prefix):
            n += len(page.get("Versions", [])) + len(page.get("DeleteMarkers", []))
        return n
