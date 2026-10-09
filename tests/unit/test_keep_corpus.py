"""KEEP corpus: legitimate markdown must survive sanitize byte-for-byte.

Exact outputs match c76cb2b (pre-pretreat regressions). No pandoc dependency —
these always run in CI so fail-closed / mention / fence regressions are caught.
"""

from __future__ import annotations

import pytest

from artifactsmith.renderers.md_sanitize import sanitize_markdown

# (id, input, exact expected output) — goldens from c76cb2b sanitize_markdown.
_KEEP_CASES: list[tuple[str, str, str]] = [
    (
        "inline-link",
        "See [docs](https://example.com/docs) please.",
        "See [docs](https://example.com/docs) please.",
    ),
    (
        "titled-link",
        'See [docs](https://example.com/docs "Title") please.',
        'See [docs](https://example.com/docs "Title") please.',
    ),
    (
        "refdef",
        "[docs][r]\n\n[r]: https://example.com/docs\n",
        "[docs](https://example.com/docs)\n",
    ),
    (
        "fragment",
        "Jump [here](#section) now.",
        "Jump [here](#section) now.",
    ),
    (
        "cpp-percent",
        "See [C++](https://en.wikipedia.org/wiki/C%2B%2B) lang.",
        "See [C++](https://en.wikipedia.org/wiki/C%2B%2B) lang.",
    ),
    (
        "http-only",
        "See [x](http://example.com/) here.",
        "See [x](http://example.com/) here.",
    ),
    (
        "image",
        "![alt](https://example.com/a.png)",
        "[alt](https://example.com/a.png)",
    ),
    (
        "image-in-link",
        "[![alt](https://example.com/a.png)](https://example.com/)",
        "[alt](https://example.com/)",
    ),
    (
        "bare-https",
        "Visit https://example.com/path today.",
        "Visit [https://example.com/path](https://example.com/path) today.",
    ),
    (
        "bare-www",
        "See www.example.com today.\n",
        "See [www.example.com](http://www.example.com) today.\n",
    ),
    (
        "bare-url-space-mention",
        "Docs at https://example.com/docs @alice please review.",
        "Docs at [https://example.com/docs](https://example.com/docs) @alice please review.",
    ),
    (
        "bare-url-lf-mention",
        "Docs at https://example.com/docs\n@alice please review.",
        "Docs at [https://example.com/docs](https://example.com/docs)\n@alice please review.",
    ),
    (
        "bare-url-nbsp-mention",
        "See https://example.com/x\u00a0@ref here.",
        "See [https://example.com/x](https://example.com/x)\u00a0@ref here.",
    ),
    (
        "list-url-mention",
        "- https://github.com/org/repo @owner",
        "- [https://github.com/org/repo](https://github.com/org/repo) @owner",
    ),
    (
        "heading-link-email",
        "# T\n\nSee [N](https://www.nsf.gov/).\n\nContact: me@example.com\n",
        "# T\n\nSee [N](https://www.nsf.gov/).\n\nContact: `me@example.com`\n",
    ),
    (
        "link-then-email",
        "Visit https://example.com/a, then email x@y.com.\n",
        "Visit [https://example.com/a](https://example.com/a), then email `x@y.com`.\n",
    ),
    (
        "bare-email",
        "Email: a.b@example.org\n",
        "Email: `a.b@example.org`\n",
    ),
    (
        "fence-tilde-private",
        "~~~\ncurl http://127.0.0.1:8080/x\n~~~",
        "~~~\ncurl http://127.0.0.1:8080/x\n~~~",
    ),
    (
        "indented-code-private",
        "    curl http://127.0.0.1:8080/x",
        "    curl http://127.0.0.1:8080/x",
    ),
    (
        "indented-code-mention",
        "    git log https://github.com/x @bob",
        "    git log https://github.com/x @bob",
    ),
]


@pytest.mark.parametrize(
    ("case_id", "raw", "expected"),
    _KEEP_CASES,
    ids=[c[0] for c in _KEEP_CASES],
)
def test_keep_corpus_exact_output(case_id: str, raw: str, expected: str) -> None:
    out = sanitize_markdown(raw)
    assert out == expected, (case_id, out, expected)
    # Fail-closed whole-document escape must not have fired.
    assert r"\:" not in out and r"\@" not in out, case_id
