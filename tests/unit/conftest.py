from __future__ import annotations

import pytest

from artifactsmith.config import CFG


def clear_model_key(monkeypatch: pytest.MonkeyPatch) -> None:
    """Drop every name llm_key() reads, including the *_FILE variants."""
    for name in ("AM_LLM_KEY", "OPENAI_API_KEY", "AM_LLM_KEY_FILE", "OPENAI_API_KEY_FILE"):
        monkeypatch.delenv(name, raising=False)


@pytest.fixture(autouse=True)
def _isolate_data_dir(tmp_path, monkeypatch):
    """Keep unit tests off the container default /data path."""
    monkeypatch.setattr(CFG, "data_dir", tmp_path)
    monkeypatch.setattr(CFG, "secrets_dir", tmp_path / "secrets")
    (tmp_path / "secrets").mkdir(parents=True, exist_ok=True)


@pytest.fixture(autouse=True)
def _openai_key_for_unit_jobs(monkeypatch):
    """Create and edit jobs call the model when the host is api.openai.com.

    The suite's default base is that host. A key here lets those jobs reach the
    mocked model. A test that covers the missing-key error calls clear_model_key.
    """
    if CFG.llm_key():
        return
    monkeypatch.setenv("AM_LLM_KEY", "test-key")
