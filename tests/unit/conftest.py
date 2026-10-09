from __future__ import annotations

import pytest

from artifactsmith.config import CFG


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch):
    """Keep unit tests off the container default /data path."""
    monkeypatch.setattr(CFG, "data_dir", tmp_path)
    monkeypatch.setattr(CFG, "secrets_dir", tmp_path / "secrets")
    (tmp_path / "secrets").mkdir(parents=True, exist_ok=True)
