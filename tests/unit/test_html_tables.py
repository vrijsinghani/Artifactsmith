"""Phone cards for wide tables in bold full HTML."""

from __future__ import annotations

from artifactsmith.renderers.html_tables import stack_wide_tables

_FOUR = """<!DOCTYPE html><html><head><style>
td{white-space:nowrap}
</style></head><body>
<table><tr><th>A</th><th>B</th><th>C</th><th>D</th></tr>
<tr><td><a href="https://example.com">one</a></td><td>2</td><td>3</td><td>4</td></tr>
</table>
<table><tr><td>a</td><td>b</td><td>c</td></tr></table>
</body></html>"""

_NO_HEAD = """<table><tbody>
<tr><th>Month</th><th>Invoices</th><th>Billed</th><th>Rebate</th></tr>
<tr><td>July</td><td>45</td><td>$1</td><td>$2</td></tr>
</tbody></table>"""


def test_four_column_table_becomes_a_phone_card():
    html = stack_wide_tables(_FOUR)
    assert 'class="stack"' in html
    assert 'data-label="A"' in html
    assert 'data-label="D"' in html
    assert "@media (max-width:40rem)" in html
    assert "table.stack thead{display:none}" in html
    assert "white-space:normal" in html
    assert "--line:#ddd6ee" in html
    assert "--muted:#3b3550" in html
    assert 'href="https://example.com"' in html
    narrow = html.split("<table", 2)[2]
    assert "stack" not in narrow.split(">", 1)[0]


def test_header_row_without_thead_becomes_thead():
    html = stack_wide_tables(_NO_HEAD)
    assert "<thead>" in html
    assert 'data-label="Month"' in html
    assert html.count("<thead>") == 1


def test_checked_render_stacks_only_when_asked():
    import asyncio

    from artifactsmith.builder import render_checked_body

    html = (
        "<!DOCTYPE html><html><head><title>T</title></head><body>"
        "<table><tr><th>A</th><th>B</th><th>C</th><th>D</th></tr>"
        "<tr><td>1</td><td>2</td><td>3</td><td>4</td></tr></table></body></html>"
    )

    async def run(stack: bool) -> str:
        result = await render_checked_body(
            fmt="html",
            display_name="T",
            body=html,
            summary="A page.",
            assumptions=["none"],
            render_timeout=30,
            model="test",
            raw_chars=len(html),
            notes=[],
            progress=lambda _message: None,
            stack_wide_tables=stack,
        )
        return result.files["index.html"].decode()

    assert 'class="stack"' not in asyncio.run(run(False))
    assert 'class="stack"' in asyncio.run(run(True))


def test_header_entities_are_escaped_once_and_rowspan_keeps_later_labels():
    html = stack_wide_tables(
        "<table><tr><th>R&amp;D</th><th>B</th><th>C</th><th>D</th></tr>"
        '<tr><td rowspan="2">July</td><td>1</td><td>2</td><td>3</td></tr>'
        "<tr><td>4</td><td>5</td><td>6</td></tr></table>"
    )
    assert 'data-label="R&amp;D"' in html
    assert "R&amp;amp;D" not in html
    assert 'data-label="B">4' in html
    assert 'data-label="C">5' in html
    assert 'data-label="D">6' in html


def test_second_pass_does_not_double_the_card_rules():
    once = stack_wide_tables(_FOUR)
    twice = stack_wide_tables(once)
    assert twice.count("table.stack{--line:") == 1
    assert twice.count('class="stack"') == once.count('class="stack"')
