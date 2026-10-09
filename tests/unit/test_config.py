import pytest

from artifactsmith.config import Config, ConfigError, _read_file, _setting, _setting_bool, _setting_int
from artifactsmith.renderers import EXTENSION, get_renderer, source_name


def test_source_name_by_format():
    assert source_name("web_static", "html") == "index.html"
    assert source_name("web_static", "pdf") == "content.md"
    assert source_name("web_static", "markdown") == "document.md"
    assert EXTENSION["docx"] == "docx"


def test_get_renderer_rejects_unknown():
    try:
        get_renderer("rtf")
    except ValueError as e:
        assert "unsupported" in str(e)
    else:
        raise AssertionError("expected ValueError")


def test_setting_helpers(monkeypatch):
    monkeypatch.setenv("AM_TEST_INT", "9")
    assert _setting_int("AM_TEST_INT", 1) == 9
    monkeypatch.setenv("AM_TEST_BOOL", "yes")
    assert _setting_bool("AM_TEST_BOOL", False) is True
    monkeypatch.delenv("AM_TEST_INT", raising=False)
    assert _setting_int("AM_TEST_INT", 3) == 3
    monkeypatch.setenv("AM_TEST_INT", "nope")
    with pytest.raises(ConfigError, match="integer"):
        _setting_int("AM_TEST_INT", 4)
    monkeypatch.setenv("AM_TEST_INT", "-1")
    with pytest.raises(ConfigError, match=">="):
        _setting_int("AM_TEST_INT", 4, minimum=0)
    monkeypatch.delenv("AM_TEST_BOOL", raising=False)
    assert _setting_bool("AM_TEST_BOOL", True) is True
    monkeypatch.setenv("AM_TEST_BOOL", "off")
    assert _setting_bool("AM_TEST_BOOL", True) is False
    monkeypatch.setenv("AM_TEST_BOOL", "maybe")
    with pytest.raises(ConfigError, match="true/false"):
        _setting_bool("AM_TEST_BOOL", True)


def test_setting_file_variant(monkeypatch, tmp_path):
    secret = tmp_path / "key"
    secret.write_text(" from-file \n")
    monkeypatch.delenv("AM_FILE_VAL", raising=False)
    monkeypatch.setenv("AM_FILE_VAL_FILE", str(secret))
    assert _setting("AM_FILE_VAL") == "from-file"
    monkeypatch.setenv("AM_FILE_VAL", "env-wins")
    assert _setting("AM_FILE_VAL") == "env-wins"
    assert _read_file(str(tmp_path / "missing")) == ""


def test_config_reads_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AM_BLOCK_PRIVATE_LINKS", "false")
    monkeypatch.setenv("AM_ALLOWED_LINK_DOMAINS", "example.com, other.test")
    monkeypatch.setenv("AM_LLM_KEY", "llm-key")
    monkeypatch.setenv("AM_STORE_KEY", "ak")
    monkeypatch.setenv("AM_STORE_SECRET", "sk")
    cfg = Config()
    assert cfg.db_path == tmp_path / "state.sqlite"
    assert cfg.audit_path == tmp_path / "audit.jsonl"
    assert cfg.block_private_links is False
    assert cfg.allowed_link_domains == ["example.com", "other.test"]
    assert cfg.llm_key() == "llm-key"
    assert cfg.store_credentials() == ("ak", "sk")


def test_llm_key_falls_back_to_openai(monkeypatch):
    monkeypatch.delenv("AM_LLM_KEY", raising=False)
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    assert Config().llm_key() == "openai-key"


def test_store_credentials_alt_names(monkeypatch):
    monkeypatch.delenv("AM_STORE_KEY", raising=False)
    monkeypatch.delenv("AM_STORE_SECRET", raising=False)
    monkeypatch.setenv("AM_STORE_ACCESS_KEY", "access")
    monkeypatch.setenv("AM_STORE_SECRET_KEY", "secret")
    assert Config().store_credentials() == ("access", "secret")


def test_signing_key_persists(tmp_path, monkeypatch):
    monkeypatch.setenv("AM_SECRETS_DIR", str(tmp_path / "secrets"))
    cfg = Config()
    first = cfg.signing_key()
    second = cfg.signing_key()
    assert first == second
    assert (tmp_path / "secrets" / "signing.key").is_file()


def test_normalized_llm_base_strips_v1(monkeypatch):
    monkeypatch.setenv("AM_LLM_BASE", "https://api.openai.com/v1/")
    assert Config().normalized_llm_base() == "https://api.openai.com"
    monkeypatch.setenv("AM_LLM_BASE", "https://gateway.example/v1")
    assert Config().normalized_llm_base() == "https://gateway.example"
    monkeypatch.setenv("AM_LLM_BASE", "https://api.openai.com")
    assert Config().normalized_llm_base() == "https://api.openai.com"
