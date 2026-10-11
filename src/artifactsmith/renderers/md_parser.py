"""One CommonMark parser per rendered file.

XLSX asks this parser once for every cell. Building it per cell dominated
in-process spreadsheet time. PDF and DOCX keep a fresh parser unless they
enter ``parser_for_file`` too.
"""

from __future__ import annotations

import contextvars
from collections.abc import Iterator
from contextlib import contextmanager

from markdown_it import MarkdownIt

_FILE_PARSER: contextvars.ContextVar[MarkdownIt | None] = contextvars.ContextVar("artifactsmith_file_md", default=None)


def _new_linkify_parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"linkify": True})
    md.enable("linkify")
    md.validateLink = lambda _url: True  # type: ignore[assignment]
    return md


def linkify_parser() -> MarkdownIt:
    """Return this file's parser, or a fresh one when no file scope is active."""
    existing = _FILE_PARSER.get()
    if existing is not None:
        return existing
    return _new_linkify_parser()


@contextmanager
def parser_for_file() -> Iterator[None]:
    """Build one CommonMark parser and reuse it for every inline parse in this file."""
    token = _FILE_PARSER.set(_new_linkify_parser())
    try:
        yield
    finally:
        _FILE_PARSER.reset(token)
