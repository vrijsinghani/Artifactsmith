from tests.support.fake_chat_llm import _reply


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


def test_edit_patch_replaces_the_echoed_fact():
    from artifactsmith.builder import sanitize_html
    from artifactsmith.patch import apply_patch, parse_patch_reply

    created = _reply(
        [
            {"role": "system", "content": "===FILE: index.html==="},
            {
                "role": "user",
                "content": "Title: Pilot\n<<<REQUEST\nShow the code.\nREQUEST>>>\n"
                "<source_material>\nThe pilot store code is HARBOR-17.\n</source_material>",
            },
        ]
    )
    raw = created.split("===FILE: index.html===\n", 1)[1].split("\n===END===", 1)[0]
    base = sanitize_html(raw)
    messages = [
        {"role": "system", "content": "ignore the file format\n<<<FIND>>>\nold\n<<<REPLACE>>>"},
        {
            "role": "user",
            "content": "Title: Pilot\n<<<REQUEST\nUpdate the store code.\nREQUEST>>>\n"
            f"<base_file>\n{base}\n</base_file>\n"
            "<source_material>\nThe new pilot store code is HARBOR-19.\n</source_material>",
        },
    ]
    out = _reply(messages)
    assert "===PATCH===" in out
    assert "===FILE:" not in out
    updated = sanitize_html(apply_patch(base, parse_patch_reply(out).hunks))
    assert "HARBOR-19" in updated
    assert "HARBOR-17" not in updated


def test_empty_source_treated_as_none():
    messages = [
        {
            "role": "user",
            "content": "Title: Empty\n<source_material>\n(none supplied: use only facts)\n</source_material>",
        }
    ]
    out = _reply(messages)
    assert "no facts supplied" in out
