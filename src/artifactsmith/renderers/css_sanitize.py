"""Allowlisted CSS for standalone HTML exports (tinycss2).

Hosted preview CSP cannot be assumed for downloads, so values that can fetch
(url, image-set, …) are still stripped. Custom properties and var() are kept
when their tokens pass the same checks.
"""

from __future__ import annotations

import tinycss2  # type: ignore[import-untyped]
from tinycss2 import ast as css_ast

_STYLE_PROPS: set[str] = set(
    """
    accent-color align-content align-items align-self aspect-ratio background
    background-color background-image background-position background-repeat
    background-size border border-bottom border-collapse border-color border-left
    border-radius border-right border-spacing border-style border-top border-width
    bottom box-shadow box-sizing caption-side color column-count column-gap columns
    content counter-increment counter-reset cursor display flex flex-basis
    flex-direction flex-flow flex-grow flex-shrink flex-wrap font font-family
    font-size font-style font-variant-numeric font-weight gap grid-area
    grid-auto-flow grid-column grid-row grid-template-areas grid-template-columns
    grid-template-rows height hyphens inset justify-content justify-items
    justify-self left letter-spacing line-height list-style list-style-position
    list-style-type margin margin-bottom margin-left margin-right margin-top
    max-height max-width min-height min-width object-fit object-position opacity
    order outline outline-offset overflow overflow-wrap overflow-x overflow-y
    padding padding-bottom padding-left padding-right padding-top place-content
    place-items place-self position right row-gap scroll-margin-top table-layout
    text-align text-decoration text-decoration-color text-decoration-thickness
    text-overflow text-transform text-underline-offset text-wrap top transform
    transition vertical-align visibility white-space width word-break word-wrap
    z-index
    """.split()
)

# Rejected CSS function names (after tinycss2 escape resolution). var() is allowed.
_BAD_FUNCTIONS: frozenset[str] = frozenset(
    """
    url image-set -webkit-image-set image cross-fade src element -moz-element
    expression attr
    """.split()
)

_GRADIENT_FUNCTIONS: frozenset[str] = frozenset(
    """
    linear-gradient radial-gradient repeating-linear-gradient
    repeating-radial-gradient conic-gradient repeating-conic-gradient
    """.split()
)

_NESTED_AT_RULES: frozenset[str] = frozenset({"media", "supports"})
_KEYFRAME_AT_RULES: frozenset[str] = frozenset({"keyframes", "-webkit-keyframes"})


def _is_custom_property(name: str) -> bool:
    return name.startswith("--") and len(name) > 2


def _skip_ws(tokens: list[object], index: int) -> int:
    while index < len(tokens) and isinstance(tokens[index], css_ast.WhitespaceToken):
        index += 1
    return index


def _var_args_safe(arguments: list[object]) -> bool:
    """First argument must be a custom-property ident; fallback is checked recursively."""
    i = _skip_ws(arguments, 0)
    if i >= len(arguments) or not isinstance(arguments[i], css_ast.IdentToken):
        return False
    ident = getattr(arguments[i], "value", "") or ""
    if not _is_custom_property(ident):
        return False
    i = _skip_ws(arguments, i + 1)
    if i >= len(arguments):
        return True
    comma = arguments[i]
    if not isinstance(comma, css_ast.LiteralToken) or comma.value != ",":
        return False
    return _tokens_safe(arguments[i + 1 :])


def _tokens_safe(tokens: list[object]) -> bool:
    """False if any token can fetch remote content or break out of a style element."""
    for tok in tokens:
        if isinstance(tok, css_ast.URLToken):
            return False
        if isinstance(tok, css_ast.FunctionBlock):
            name = (tok.lower_name or "").lower()
            if name == "var":
                if not _var_args_safe(list(tok.arguments)):
                    return False
                continue
            if name in _BAD_FUNCTIONS:
                return False
            if not _tokens_safe(list(tok.arguments)):
                return False
        if isinstance(tok, css_ast.SquareBracketsBlock):
            if not _tokens_safe(list(tok.content)):
                return False
        if isinstance(tok, css_ast.ParenthesesBlock):
            if not _tokens_safe(list(tok.content)):
                return False
        if isinstance(tok, css_ast.CurlyBracketsBlock):
            if not _tokens_safe(list(tok.content)):
                return False
        if isinstance(tok, css_ast.LiteralToken):
            if "<" in (tok.value or "") or ">" in (tok.value or ""):
                # Combinator '>' is a single-char literal and is allowed.
                if tok.value != ">":
                    return False
        if isinstance(tok, (css_ast.StringToken, css_ast.IdentToken)):
            value = getattr(tok, "value", "") or ""
            if "<" in value or ">" in value:
                return False
            low = value.lower()
            if "://" in low or low.startswith("//"):
                return False
    return True


def _background_image_ok(tokens: list[object]) -> bool:
    """Allow none, var(), and gradient functions only (no url/image-set)."""
    expecting_layer = True
    saw_layer = False
    for tok in tokens:
        if isinstance(tok, css_ast.WhitespaceToken):
            continue
        if expecting_layer:
            if isinstance(tok, css_ast.IdentToken) and (tok.value or "").lower() == "none":
                expecting_layer = False
                saw_layer = True
                continue
            if isinstance(tok, css_ast.FunctionBlock):
                name = (tok.lower_name or "").lower()
                if name == "var" or name in _GRADIENT_FUNCTIONS:
                    expecting_layer = False
                    saw_layer = True
                    continue
            return False
        if isinstance(tok, css_ast.LiteralToken) and tok.value == ",":
            expecting_layer = True
            continue
        return False
    return saw_layer and not expecting_layer


def _content_ok(tokens: list[object]) -> bool:
    """content: strings only (no url/attr/counters)."""
    for tok in tokens:
        if isinstance(tok, css_ast.WhitespaceToken):
            continue
        if isinstance(tok, css_ast.StringToken):
            continue
        return False
    return True


def _value_allowed(name: str, tokens: list[object]) -> bool:
    if not _tokens_safe(tokens):
        return False
    if name == "background-image":
        return _background_image_ok(tokens)
    if name == "content":
        return _content_ok(tokens)
    return True


def _serialize_safe(nodes: list[object]) -> str:
    text = str(tinycss2.serialize(nodes))
    # '<' is a style-element breakout; '>' is a CSS combinator and must stay.
    if "<" in text:
        return ""
    return text


def _safe_declarations(content: list[object]) -> list[object]:
    decls = tinycss2.parse_declaration_list(content, skip_comments=True, skip_whitespace=True)
    kept: list[object] = []
    for decl in decls:
        if not isinstance(decl, css_ast.Declaration):
            continue
        name = (decl.lower_name or "").lower()
        if name not in _STYLE_PROPS and not _is_custom_property(name):
            continue
        if not _value_allowed(name, list(decl.value)):
            continue
        kept.append(decl)
    return kept


def _sanitize_qualified_rule(rule: css_ast.QualifiedRule) -> str | None:
    prelude = list(rule.prelude)
    if not _tokens_safe(prelude):
        return None
    safe_decls = _safe_declarations(list(rule.content))
    if not safe_decls:
        return None
    body = _serialize_safe(safe_decls)
    if not body.strip():
        return None
    prelude_text = _serialize_safe(prelude).strip()
    if not prelude_text:
        return None
    return f"{prelude_text}{{{body}}}"


def _sanitize_at_rule(rule: css_ast.AtRule) -> str | None:
    keyword = (rule.lower_at_keyword or "").lower()
    if keyword not in _NESTED_AT_RULES and keyword not in _KEYFRAME_AT_RULES:
        return None
    if rule.content is None:
        return None
    if not _tokens_safe(list(rule.prelude)):
        return None
    inner_rules = tinycss2.parse_rule_list(rule.content, skip_comments=True, skip_whitespace=True)
    inner = "".join(_sanitize_rules(inner_rules))
    if not inner:
        return None
    prelude_text = _serialize_safe(list(rule.prelude)).strip()
    if prelude_text:
        return f"@{keyword} {prelude_text}{{{inner}}}"
    return f"@{keyword}{{{inner}}}"


def _sanitize_rules(rules: list[object]) -> list[str]:
    kept: list[str] = []
    for rule in rules:
        if isinstance(rule, css_ast.ParseError):
            continue
        if isinstance(rule, css_ast.AtRule):
            text = _sanitize_at_rule(rule)
        elif isinstance(rule, css_ast.QualifiedRule):
            text = _sanitize_qualified_rule(rule)
        else:
            continue
        if text:
            kept.append(text)
    return kept


def sanitize_css(css: str) -> str:
    """Keep allowlisted declarations and custom properties; drop fetch-capable values.

    Filters per declaration so one unsafe value does not drop the rest of the rule.
    Preserves ``@media`` / ``@supports`` and trivial ``@keyframes``. Drops
    ``@import``, ``@font-face``, ``@namespace``, and ``@charset``.

    Does not HTML-unescape the input: entity-encoded ``</style>`` must stay inert
    text that tinycss2 will not turn into markup.
    """
    if not css or "<" in css:
        # Raw '<' in a style block is always treated as a breakout attempt.
        return ""

    rules = tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True)
    out = "".join(_sanitize_rules(rules))
    if "<" in out:
        return ""
    return out
