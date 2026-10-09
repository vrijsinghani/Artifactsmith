from artifactsmith.config import Config, _setting_bool, _setting_int
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


def test_config_reads_env(monkeypatch, tmp_path):
    monkeypatch.setenv("AM_DATA_DIR", str(tmp_path))
    monkeypatch.setenv("AM_BLOCK_PRIVATE_LINKS", "false")
    monkeypatch.setenv("AM_ALLOWED_LINK_DOMAINS", "example.com, other.test")
    cfg = Config()
    assert cfg.db_path == tmp_path / "state.sqlite"
    assert cfg.block_private_links is False
    assert cfg.allowed_link_domains == ["example.com", "other.test"]
