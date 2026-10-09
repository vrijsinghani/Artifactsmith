"""S3-compatible byte store. Layout: <workspace>/<artifact_id>/v<NNN>/<file>."""

from __future__ import annotations

import logging

import boto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from .config import CFG

log = logging.getLogger("artifactsmith.store")


def vdir(version: int) -> str:
    return f"v{version:03d}"


class StoreError(RuntimeError):
    """Object-store configuration or capability failure."""


class Store:
    def __init__(self) -> None:
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
        """Create the bucket and enable versioning. Returns True if newly created.

        Versioning is required. Failures enabling it raise StoreError.
        """
        created = False
        try:
            self.s3.head_bucket(Bucket=self.bucket)
        except ClientError as e:
            code = str(e.response.get("Error", {}).get("Code", ""))
            # Only missing-bucket signals warrant create. 403/400 are auth/config errors.
            if code in ("404", "NoSuchBucket", "NotFound") or code.endswith("404"):
                self.s3.create_bucket(Bucket=self.bucket)
                created = True
            else:
                raise StoreError(f"head_bucket failed: {code or type(e).__name__}") from e
        except Exception as e:  # noqa: BLE001 — boto can raise ConnectionError etc.
            # Missing bucket often surfaces as a generic exception against local stores.
            try:
                self.s3.create_bucket(Bucket=self.bucket)
                created = True
            except Exception as create_err:  # noqa: BLE001
                raise StoreError(f"object store bucket unavailable: {type(e).__name__}") from create_err
        try:
            self.s3.put_bucket_versioning(Bucket=self.bucket, VersioningConfiguration={"Status": "Enabled"})
        except Exception as e:  # noqa: BLE001
            raise StoreError(f"failed to enable bucket versioning: {type(e).__name__}: {e}") from e
        # Verify versioning actually stuck.
        try:
            status = self.s3.get_bucket_versioning(Bucket=self.bucket).get("Status")
        except Exception as e:  # noqa: BLE001
            raise StoreError(f"could not verify bucket versioning: {type(e).__name__}") from e
        if status != "Enabled":
            raise StoreError(f"bucket versioning is {status!r}, expected 'Enabled'")
        return created

    def put(self, key: str, data: bytes, content_type: str) -> str:
        r = self.s3.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        vid = r.get("VersionId")
        return "" if vid is None else str(vid)

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
