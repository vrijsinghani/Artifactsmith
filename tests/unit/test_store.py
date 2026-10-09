from __future__ import annotations

import pytest

from artifactsmith import store as store_mod
from artifactsmith.store import Store, StoreError, vdir


class _FakeBody:
    def __init__(self, data: bytes):
        self._data = data

    def read(self) -> bytes:
        return self._data


class _FakeS3:
    def __init__(self):
        self.buckets: set[str] = set()
        self.objects: dict[str, tuple[bytes, str]] = {}
        self.versioning: dict[str, str] = {}
        self.pages: list[dict] = []

    def head_bucket(self, Bucket):
        if Bucket not in self.buckets:
            raise RuntimeError("missing")

    def create_bucket(self, Bucket):
        self.buckets.add(Bucket)

    def put_bucket_versioning(self, Bucket, VersioningConfiguration):
        self.versioning[Bucket] = VersioningConfiguration["Status"]

    def get_bucket_versioning(self, Bucket):
        status = self.versioning.get(Bucket)
        return {"Status": status} if status else {}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[Key] = (Body, ContentType)
        return {"VersionId": "vid-1"}

    def get_object(self, Bucket, Key):
        data, ct = self.objects[Key]
        return {"Body": _FakeBody(data), "ContentType": ct}

    def delete_object(self, Bucket, Key, VersionId):
        self.objects.pop(Key, None)

    def get_paginator(self, name):
        assert name == "list_object_versions"
        pages = self.pages

        class _P:
            def paginate(self, Bucket, Prefix):
                yield from pages

        return _P()


def test_vdir_and_prefix():
    assert vdir(3) == "v003"
    assert Store.prefix("ws", "art_1") == "ws/art_1/"
    assert Store.prefix("ws", "art_1", 2) == "ws/art_1/v002/"


def test_store_requires_config(monkeypatch):
    monkeypatch.setattr(store_mod.CFG, "store_endpoint", "")
    monkeypatch.setattr(store_mod.CFG, "store_credentials", lambda: ("", ""))
    with pytest.raises(RuntimeError, match="not configured"):
        Store()


def test_ensure_put_get_purge(monkeypatch):
    fake = _FakeS3()
    monkeypatch.setattr(store_mod.CFG, "store_endpoint", "http://store.test")
    monkeypatch.setattr(store_mod.CFG, "store_credentials", lambda: ("ak", "sk"))
    monkeypatch.setattr(store_mod.CFG, "store_bucket", "artifacts")
    monkeypatch.setattr(store_mod.boto3, "client", lambda *a, **k: fake)

    s = Store()
    assert s.ensure_bucket() is True
    assert fake.versioning["artifacts"] == "Enabled"
    assert s.ensure_bucket() is False

    vid = s.put("ws/a/v001/index.html", b"hi", "text/html")
    assert vid == "vid-1"
    data, ct = s.get("ws/a/v001/index.html")
    assert data == b"hi" and ct == "text/html"

    fake.pages = [
        {
            "Versions": [{"Key": "ws/a/v001/index.html", "VersionId": "vid-1"}],
            "DeleteMarkers": [{"Key": "ws/a/gone", "VersionId": "d1"}],
        }
    ]
    assert s.count_prefix_versions("ws/a/") == 2
    assert "ws/a/v001/index.html" in fake.objects
    assert s.purge_prefix("ws/a/") == 2
    assert "ws/a/v001/index.html" not in fake.objects


def test_ensure_bucket_requires_versioning(monkeypatch):
    class _NoVersion(_FakeS3):
        def put_bucket_versioning(self, Bucket, VersioningConfiguration):
            raise RuntimeError("versioning unsupported")

    fake = _NoVersion()
    monkeypatch.setattr(store_mod.CFG, "store_endpoint", "http://store.test")
    monkeypatch.setattr(store_mod.CFG, "store_credentials", lambda: ("ak", "sk"))
    monkeypatch.setattr(store_mod.CFG, "store_bucket", "artifacts")
    monkeypatch.setattr(store_mod.boto3, "client", lambda *a, **k: fake)
    with pytest.raises(StoreError, match="versioning"):
        Store().ensure_bucket()


def test_ensure_bucket_does_not_create_on_403(monkeypatch):
    from botocore.exceptions import ClientError

    class _Forbidden(_FakeS3):
        def head_bucket(self, Bucket):
            raise ClientError({"Error": {"Code": "403", "Message": "Forbidden"}}, "HeadBucket")

        def create_bucket(self, Bucket):
            raise AssertionError("must not create on 403")

    fake = _Forbidden()
    monkeypatch.setattr(store_mod.CFG, "store_endpoint", "http://store.test")
    monkeypatch.setattr(store_mod.CFG, "store_credentials", lambda: ("ak", "sk"))
    monkeypatch.setattr(store_mod.CFG, "store_bucket", "artifacts")
    monkeypatch.setattr(store_mod.boto3, "client", lambda *a, **k: fake)
    with pytest.raises(StoreError, match="head_bucket failed: 403"):
        Store().ensure_bucket()
