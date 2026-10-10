"""Allowlisted CSS for standalone HTML exports (tinycss2).

Hosted preview CSP cannot be assumed for downloads, so values that can fetch
(url, image-set, …) are still stripped. Custom properties and var() are kept
when their tokens pass the same checks.
"""

from __future__ import annotations

import re
from html import unescape

import tinycss2  # type: ignore[import-untyped]
from tinycss2 import ast as css_ast

_STYLE_PROPS: set[str] = set(
    """
    accent-color align-content align-items align-self animation animation-delay
    animation-direction animation-duration animation-fill-mode animation-iteration-count
    animation-name animation-timing-function aspect-ratio background background-color
    background-image background-position background-repeat background-size border
    border-bottom border-bottom-color border-bottom-left-radius border-bottom-right-radius
    border-bottom-style border-bottom-width border-collapse border-color
    border-inline-end border-inline-start border-left border-left-color border-left-style
    border-left-width border-radius border-right border-right-color border-right-style
    border-right-width border-spacing border-style border-top border-top-color
    border-top-left-radius border-top-right-radius border-top-style border-top-width
    border-width border-block bottom box-shadow box-sizing break-after break-before
    break-inside caption-side color color-scheme
    column-count column-gap columns content counter-increment counter-reset cursor
    display filter backdrop-filter flex flex-basis flex-direction flex-flow flex-grow flex-shrink
    flex-wrap font font-family font-feature-settings font-size font-style font-variant
    font-variant-numeric font-weight gap grid-area grid-auto-flow grid-column grid-row
    grid-template-areas grid-template-columns grid-template-rows height hyphens inset
    inset-inline justify-content justify-items justify-self left letter-spacing
    line-height list-style list-style-position list-style-type margin margin-block
    margin-bottom margin-inline margin-left margin-right margin-top max-height max-width
    min-height min-width object-fit object-position opacity order outline outline-offset
    overflow overflow-wrap overflow-x overflow-y padding padding-block padding-bottom
    padding-inline padding-left padding-right padding-top page-break-inside place-content
    place-items place-self pointer-events position print-color-adjust
    -webkit-print-color-adjust resize right row-gap scroll-behavior scroll-margin-top
    table-layout text-align text-anchor text-decoration text-decoration-color
    -webkit-overflow-scrolling
    text-decoration-thickness text-indent text-overflow text-shadow text-transform
    text-underline-offset text-wrap top transform transition vertical-align visibility
    white-space width word-break word-wrap z-index
    dominant-baseline fill fill-opacity stroke stroke-dasharray stroke-dashoffset
    stroke-linecap stroke-linejoin stroke-opacity stroke-width
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
_MAX_AT_NESTING = 8
_BREAKOUT_RE = re.compile(r"</|<!", re.I)
_FETCH_LEAK_RE = re.compile(
    r"(?:url|image-set|-webkit-image-set|(?<![\w-])image|src|expression)\s*\(",
    re.I,
)
_SAFE_CMP_LITERALS = frozenset({"<", ">", "<=", ">=", "="})


def _has_breakout(text: str) -> bool:
    """True for style-element breakout markers, not comparison operators."""
    return bool(_BREAKOUT_RE.search(text))


def _has_fetch_leak(text: str) -> bool:
    """True if serialized CSS still contains a fetch-capable function."""
    stripped = re.sub(r"/\*.*?\*/", "", text, flags=re.S)
    compact = re.sub(r"\s+", "", stripped)
    return bool(_FETCH_LEAK_RE.search(compact))


def _function_name(tok: css_ast.FunctionBlock) -> str:
    return re.sub(r"\s+", "", (tok.lower_name or "").lower())


def _flat_css_text(tokens: list[object]) -> str:
    """Concatenate component values so comment-split ``u/**/rl(`` becomes ``url(``."""
    parts: list[str] = []
    for tok in tokens:
        if isinstance(tok, css_ast.WhitespaceToken):
            continue
        if isinstance(tok, (css_ast.IdentToken, css_ast.LiteralToken, css_ast.StringToken, css_ast.HashToken)):
            parts.append(getattr(tok, "value", "") or "")
        elif isinstance(tok, (css_ast.NumberToken, css_ast.PercentageToken, css_ast.DimensionToken)):
            # Keep a separator so ``1 / 2 / 3 / 4`` cannot collapse into ``//``.
            parts.append(" " + (getattr(tok, "representation", None) or str(getattr(tok, "value", ""))) + " ")
        elif isinstance(tok, css_ast.URLToken):
            parts.append("url(")
        elif isinstance(tok, css_ast.FunctionBlock):
            parts.append(_function_name(tok) + "(")
            parts.append(_flat_css_text(list(tok.arguments)))
            parts.append(")")
        elif isinstance(tok, css_ast.ParenthesesBlock):
            parts.append("(")
            parts.append(_flat_css_text(list(tok.content)))
            parts.append(")")
        elif isinstance(tok, css_ast.SquareBracketsBlock):
            parts.append("[")
            parts.append(_flat_css_text(list(tok.content)))
            parts.append("]")
        elif isinstance(tok, css_ast.CurlyBracketsBlock):
            parts.append("{")
            parts.append(_flat_css_text(list(tok.content)))
            parts.append("}")
    return "".join(parts)


def _flat_text_safe(tokens: list[object]) -> bool:
    blob = re.sub(r"\s+", "", _flat_css_text(tokens)).lower().replace("\\", "")
    if "://" in blob:
        return False
    return not _has_fetch_leak(blob)


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


def _attr_data_label_only(arguments: list[object]) -> bool:
    """True when ``attr()`` has exactly the ident ``data-label``."""
    i = _skip_ws(arguments, 0)
    if i >= len(arguments) or not isinstance(arguments[i], css_ast.IdentToken):
        return False
    if (getattr(arguments[i], "value", "") or "") != "data-label":
        return False
    return _skip_ws(arguments, i + 1) >= len(arguments)


def _tokens_safe(tokens: list[object], *, allow_attr_data_label: bool = False) -> bool:
    """False if any token can fetch remote content or break out of a style element."""
    for tok in tokens:
        if isinstance(tok, css_ast.ParseError):
            return False
        if isinstance(tok, css_ast.URLToken):
            return False
        if isinstance(tok, css_ast.FunctionBlock):
            name = _function_name(tok)
            if name == "var":
                if not _var_args_safe(list(tok.arguments)):
                    return False
                continue
            if name in _BAD_FUNCTIONS:
                if allow_attr_data_label and name == "attr" and _attr_data_label_only(list(tok.arguments)):
                    continue
                return False
            if not _tokens_safe(list(tok.arguments), allow_attr_data_label=allow_attr_data_label):
                return False
        if isinstance(tok, css_ast.SquareBracketsBlock):
            if not _tokens_safe(list(tok.content), allow_attr_data_label=allow_attr_data_label):
                return False
        if isinstance(tok, css_ast.ParenthesesBlock):
            if not _tokens_safe(list(tok.content), allow_attr_data_label=allow_attr_data_label):
                return False
        if isinstance(tok, css_ast.CurlyBracketsBlock):
            if not _tokens_safe(list(tok.content), allow_attr_data_label=allow_attr_data_label):
                return False
        if isinstance(tok, css_ast.LiteralToken):
            val = tok.value or ""
            if ("<" in val or ">" in val) and val not in _SAFE_CMP_LITERALS:
                return False
        if isinstance(tok, (css_ast.StringToken, css_ast.IdentToken)):
            value = getattr(tok, "value", "") or ""
            if "<" in value or ">" in value:
                return False
            low = value.lower()
            if "://" in low or low.startswith("//"):
                return False
    return _flat_text_safe(tokens)


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
                name = _function_name(tok)
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
    """content: strings, none, counter()/counters(), and attr(data-label)."""
    for tok in tokens:
        if isinstance(tok, css_ast.WhitespaceToken):
            continue
        if isinstance(tok, css_ast.StringToken):
            continue
        if isinstance(tok, css_ast.IdentToken) and (tok.value or "").lower() == "none":
            continue
        if isinstance(tok, css_ast.FunctionBlock):
            name = _function_name(tok)
            if name in {"counter", "counters"} and _tokens_safe(list(tok.arguments)):
                continue
            if name == "attr" and _attr_data_label_only(list(tok.arguments)):
                continue
        return False
    return True


def _value_allowed(name: str, tokens: list[object]) -> bool:
    if name == "content":
        if not _tokens_safe(tokens, allow_attr_data_label=True):
            return False
        return _content_ok(tokens)
    if not _tokens_safe(tokens):
        return False
    if name == "background-image":
        return _background_image_ok(tokens)
    return True


def _serialize_safe(nodes: list[object]) -> str:
    text = str(tinycss2.serialize(nodes))
    if _has_breakout(text):
        return ""
    return text


def _filter_declarations(decls: list[object]) -> list[object]:
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


def _safe_declarations(content: list[object]) -> list[object]:
    return _filter_declarations(tinycss2.parse_declaration_list(content, skip_comments=True, skip_whitespace=True))


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


def _sanitize_at_rule(rule: css_ast.AtRule, depth: int) -> str | None:
    if depth >= _MAX_AT_NESTING:
        return None
    keyword = (rule.lower_at_keyword or "").lower()
    if keyword not in _NESTED_AT_RULES and keyword not in _KEYFRAME_AT_RULES:
        return None
    if rule.content is None:
        return None
    if not _tokens_safe(list(rule.prelude)):
        return None
    inner_rules = tinycss2.parse_rule_list(rule.content, skip_comments=True, skip_whitespace=True)
    inner = "".join(_sanitize_rules(inner_rules, depth + 1))
    if not inner:
        return None
    prelude_text = _serialize_safe(list(rule.prelude)).strip()
    if prelude_text:
        return f"@{keyword} {prelude_text}{{{inner}}}"
    return f"@{keyword}{{{inner}}}"


def _sanitize_rules(rules: list[object], depth: int) -> list[str]:
    kept: list[str] = []
    for rule in rules:
        if isinstance(rule, css_ast.ParseError):
            continue
        if isinstance(rule, css_ast.AtRule):
            text = _sanitize_at_rule(rule, depth)
        elif isinstance(rule, css_ast.QualifiedRule):
            text = _sanitize_qualified_rule(rule)
        else:
            continue
        if text:
            kept.append(text)
    return kept


def sanitize_inline_style(value: str) -> str:
    """Sanitize a ``style`` attribute with the same per-declaration rules as blocks.

    Returns an empty string when nothing remains (caller should drop the attribute).
    HTML entities are decoded so ``&#117;rl(`` is treated as ``url(``.
    """
    if not value:
        return ""
    text = unescape(value)
    if _has_breakout(re.sub(r"/\*.*?\*/", "", text, flags=re.S)):
        return ""
    try:
        decls = tinycss2.parse_declaration_list(text, skip_comments=True, skip_whitespace=True)
        kept = _filter_declarations(decls)
        if not kept:
            return ""
        out = _serialize_safe(kept).strip()
        if not out or _has_breakout(out) or _has_fetch_leak(out):
            return ""
        return out
    except RecursionError:
        return ""


def sanitize_css(css: str) -> str:
    """Keep allowlisted declarations and custom properties; drop fetch-capable values.

    Filters per declaration so one unsafe value does not drop the rest of the rule.
    Preserves ``@media`` / ``@supports`` and trivial ``@keyframes`` up to a nesting
    cap. Drops ``@import``, ``@font-face``, ``@namespace``, and ``@charset``.

    Does not HTML-unescape the input: entity-encoded ``</style>`` must stay inert
    text that tinycss2 will not turn into markup.
    """
    if not css:
        return ""
    # Comments are skipped by the parser; only leftover ``</`` / ``<!`` fail closed.
    if _has_breakout(re.sub(r"/\*.*?\*/", "", css, flags=re.S)):
        return ""
    try:
        rules = tinycss2.parse_stylesheet(css, skip_comments=True, skip_whitespace=True)
        out = "".join(_sanitize_rules(rules, 0))
        if _has_breakout(out) or _has_fetch_leak(out):
            return ""
        return out
    except RecursionError:
        return ""
