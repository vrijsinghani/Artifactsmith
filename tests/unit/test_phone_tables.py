"""Bold full-mode tables of four or more columns become phone cards."""

from artifactsmith.renderers.phone_tables import stack_wide_tables

_PAGE = """<!DOCTYPE html>
<html><head><title>Bold</title><style>table{border-collapse:collapse}</style></head>
<body>
<p>See <a href="https://example.com/report">the report</a>.</p>
<img alt="logo" src="data:image/gif;base64,R0lGODlhAQABAAAAACw=">
<svg role="img" aria-label="Bars"><rect width="4" height="4"></rect></svg>
<table>
<thead><tr><th>A</th><th>B</th><th>C</th><th>D</th></tr></thead>
<tbody><tr><td>1</td><td>2</td><td><a href="https://example.com/cell">3</a></td><td>4</td></tr></tbody>
</table>
<table>
<thead><tr><th>Only</th><th>Three</th><th>Cols</th></tr></thead>
<tbody><tr><td>a</td><td>b</td><td>c</td></tr></tbody>
</table>
</body></html>
"""


def test_four_column_table_stacks_and_keeps_links_images_charts():
    html = stack_wide_tables(_PAGE)
    assert "table.am-stack" in html
    assert "@media (max-width:40rem)" in html
    assert 'class="am-stack"' in html
    assert 'data-label="A"' in html
    assert 'data-label="D"' in html
    assert html.count("<table") == 2
    narrow = html.split("<table", 2)[2]
    assert "am-stack" not in narrow.split("</table>", 1)[0]
    assert 'href="https://example.com/report"' in html
    assert 'href="https://example.com/cell"' in html
    assert "<img" in html
    assert 'aria-label="Bars"' in html
    assert "table{border-collapse:collapse}" in html


def test_injected_css_resets_page_min_width():
    page = """<!DOCTYPE html><html><head><style>table{min-width:720px}</style></head>
<body><table><thead><tr><th>A</th><th>B</th><th>C</th><th>D</th></tr></thead>
<tbody><tr><td>1</td><td>2</td><td>3</td><td>4</td></tr></tbody></table></body></html>"""
    html = stack_wide_tables(page)
    assert "table{min-width:720px}" in html
    assert "table.am-stack{display:block;min-width:0!important;width:100%" in html
    assert "table.am-stack tbody{display:block" in html


def test_three_column_table_is_unchanged():
    page = """<table><tr><th>A</th><th>B</th><th>C</th></tr><tr><td>1</td><td>2</td><td>3</td></tr></table>"""
    assert stack_wide_tables(page) == page
