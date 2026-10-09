from tests.support.mock_llm import _reply


def test_html_reply_echoes_fact():
    messages = [
        {"role": "system", "content": "===FILE: index.html==="},
        {
            "role": "user",
            "content": "Title: Pilot\n<<<REQUEST\nShow the code.\nREQUEST>>>\n"
            "<source_material>\nThe pilot store code is HARBOR-17.\n</source_material>",
        },
    ]
    out = _reply(messages)
    assert "===FILE: index.html===" in out
    assert "HARBOR-17" in out


def test_markdown_reply_when_system_asks_for_md():
    messages = [
        {"role": "system", "content": "===FILE: content.md==="},
        {
            "role": "user",
            "content": "Title: Pilot\n<<<REQUEST\nShow the code.\nREQUEST>>>\n"
            "<source_material>\nThe pilot store code is HARBOR-17.\n</source_material>",
        },
    ]
    out = _reply(messages)
    assert "===FILE: content.md===" in out
    assert "HARBOR-17" in out
    assert "<html" not in out


def test_missing_sentinel():
    messages = [{"role": "user", "content": "FACTS_MISSING please"}]
    out = _reply(messages)
    assert "===NEEDS_INPUT===" in out


def test_empty_source_treated_as_none():
    messages = [
        {
            "role": "user",
            "content": "Title: Empty\n<source_material>\n(none supplied: use only facts)\n</source_material>",
        }
    ]
    out = _reply(messages)
    assert "no facts supplied" in out
