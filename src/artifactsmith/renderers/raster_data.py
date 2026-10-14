"""Base64 raster images allowed on ``img`` src and srcset only.

``classify_href`` still returns ``blocked`` for every ``data:`` URL. Callers
that are not an ``img`` attribute must keep using that result.
"""

from __future__ import annotations

import base64
import binascii
import re

from .links import attr_value_confused, deobfuscate_href

MAX_DECODED_BYTES = 512 * 1024
_HEADER = re.compile(r"^data:image/(png|jpe?g|gif|webp|avif);base64,", re.I)
_B64_RUN = re.compile(r"[A-Za-z0-9+/=]+")
_DESCRIPTOR = re.compile(r"^[0-9.]+[wx]\Z", re.I)
_ASCII_WS = "\t\n\f\r "


def raster_data_image(value: str) -> bool:
    """True when ``value`` is one allowed raster data URL."""
    return inspect_raster_data_image(value)[0]


def inspect_raster_data_image(value: str) -> tuple[bool, str]:
    """Return whether ``value`` is an allowed raster, and a short reason when it is not.

    The reason is empty when the URL is allowed. It does not include the payload.
    """
    raw = value.strip()
    match = _HEADER.match(raw)
    if match is None:
        return False, "not a base64 raster"
    payload = raw[match.end() :]
    if not payload:
        return False, "malformed base64"
    try:
        decoded = base64.b64decode(payload, validate=True)
    except (ValueError, binascii.Error):
        return False, "malformed base64"
    if len(decoded) > MAX_DECODED_BYTES:
        return False, "larger than 512 KiB"
    subtype = match.group(1).lower()
    if subtype == "jpg":
        subtype = "jpeg"
    if not _magic_ok(subtype, decoded):
        return False, f"not a valid {subtype}"
    return True, ""


def _magic_ok(subtype: str, data: bytes) -> bool:
    if subtype == "png":
        return data.startswith(b"\x89PNG\r\n\x1a\n")
    if subtype == "jpeg":
        return data.startswith(b"\xff\xd8\xff")
    if subtype == "gif":
        return data.startswith((b"GIF87a", b"GIF89a"))
    if subtype == "webp":
        return len(data) >= 12 and data.startswith(b"RIFF") and data[8:12] == b"WEBP"
    if subtype == "avif":
        return _avif_brand(data)
    return False


def _avif_brand(data: bytes) -> bool:
    if len(data) < 12 or data[4:8] != b"ftyp":
        return False
    if data[8:12] in (b"avif", b"avis"):
        return True
    size = int.from_bytes(data[:4], "big")
    end = size if 16 <= size <= len(data) else min(len(data), 32)
    return b"avif" in data[16:end] or b"avis" in data[16:end]


def inline_image_problem(verdict: str | None) -> str | None:
    """The hard job error for a ``hard:`` verdict, without the payload."""
    if verdict and verdict.startswith("hard:"):
        return f"inline image rejected: {verdict[5:]}"
    return None


def img_url_verdict(value: str) -> str | None:
    """``keep`` for an allowed raster. ``hard:`` is an inline-image rejection.

    None means the ordinary URL policy still applies. A confused value stays
    on that path so the authority check is unchanged. A matching raster is
    decided before de-obfuscation so a large payload is not scanned twice.
    """
    if raster_data_image(value):
        return "keep"
    if attr_value_confused(value):
        return None
    view = deobfuscate_href(value).lower()
    if view.startswith(("javascript:", "vbscript:")):
        return "hard:script URL"
    if view.startswith("data:"):
        _ok, reason = inspect_raster_data_image(value)
        return f"hard:{reason}"
    return None


def img_attr_verdict(name: str, value: str) -> str | None:
    """Verdict for one ``img`` ``src`` or ``srcset`` value."""
    if name.lower() != "srcset":
        return img_url_verdict(value)
    urls = srcset_candidates(value)
    if urls is None:
        return "hard:not a base64 raster"
    if not urls:
        return None
    verdicts = [img_url_verdict(url) for url in urls]
    hard = next((item for item in verdicts if item and item.startswith("hard:")), None)
    if hard:
        return hard
    if all(item == "keep" for item in verdicts):
        return "keep"
    return None


def markdown_image_problems(src: str) -> list[str]:
    """Hard problems for a markdown image destination that must not publish."""
    problem = inline_image_problem(img_url_verdict(src))
    return [problem] if problem else []


def srcset_candidates(value: str) -> list[str] | None:
    """URL tokens in a srcset. None when a ``data:`` candidate is not a clean raster URL.

    The comma after ``;base64,`` stays inside the URL. A descriptor (``1x``, ``2x``,
    ``100w``) may follow the URL. Other ``data:`` values fail closed instead of
    being split on a comma inside the payload.
    """
    rest = value.strip(_ASCII_WS)
    urls: list[str] = []
    while rest:
        rest = rest.lstrip(_ASCII_WS)
        if not rest:
            break
        if rest.lower().startswith("data:"):
            match = _HEADER.match(rest)
            if match is None:
                return None
            run = _B64_RUN.match(rest, match.end())
            index = run.end() if run else match.end()
            urls.append(rest[:index])
            rest = rest[index:].lstrip(_ASCII_WS)
            if not rest:
                break
            if rest.startswith(","):
                rest = rest[1:]
                continue
            piece, sep, after = rest.partition(",")
            token = piece.strip(_ASCII_WS)
            if not token or _DESCRIPTOR.fullmatch(token) is None:
                return None
            rest = after
            continue
        piece, _sep, after = rest.partition(",")
        token = piece.strip(_ASCII_WS)
        if token:
            url = token.split()[0]
            if url:
                urls.append(url)
        rest = after
    return urls
