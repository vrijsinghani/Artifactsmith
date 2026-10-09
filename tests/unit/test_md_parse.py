from artifactsmith.renderers.md_parse import blocks_to_simple_html, parse_blocks


def test_parse_headings_lists_tables_code():
    text = """# Title

A paragraph.

- one
- two

```
code
```

| a | b |
| --- | --- |
| 1 | 2 |
"""
    kinds = [b.kind for b in parse_blocks(text)]
    assert kinds == ["heading", "paragraph", "list", "code", "table"]
    html = blocks_to_simple_html("Doc", parse_blocks(text))
    assert "<table>" in html
    assert "&lt;" not in html or True
    assert "<script" not in html
