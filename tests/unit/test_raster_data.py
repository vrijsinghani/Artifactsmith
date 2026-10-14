"""Inline raster data URLs on img src and srcset."""

from __future__ import annotations

import base64

import pytest

from artifactsmith.builder import RetryableCheck, publish_page
from artifactsmith.markdown_view import markdown_share_html
from artifactsmith.renderers.html_export import EXPORT_CSP
from artifactsmith.renderers.html_sanitize import sanitize_html_document
from artifactsmith.renderers.links import classify_href
from artifactsmith.renderers.md_html import find_bad_html_element_attrs
from artifactsmith.renderers.md_sanitize import sanitize_markdown
from artifactsmith.renderers.raster_data import MAX_DECODED_BYTES, raster_data_image
from artifactsmith.renderers.safety import check_content
from artifactsmith.server import CSP

_PNG = b"\x89PNG\r\n\x1a\n"
_JPEG = b"\xff\xd8\xff"
_GIF = b"GIF89a"
_WEBP = b"RIFF\x00\x00\x00\x00WEBP"
_AVIF = b"\x00\x00\x00\x10ftypavif\x00\x00\x00\x00"
_KINDS = {
    "png": _PNG,
    "jpeg": _JPEG,
    "jpg": _JPEG,
    "gif": _GIF,
    "webp": _WEBP,
    "avif": _AVIF,
}


def _url(kind: str, payload: bytes | None = None) -> str:
    data = _KINDS[kind] if payload is None else payload
    return f"data:image/{kind};base64,{base64.b64encode(data).decode()}"


def _page(inner: str) -> str:
    return f"<html><body>{inner}</body></html>"


def test_each_raster_survives_src_and_srcset() -> None:
    for kind in _KINDS:
        url = _url(kind)
        srcset = f"{url} 1x, {url} 2x"
        body = _page(f'<img alt="shot" src="{url}" srcset="{srcset}">')
        assert find_bad_html_element_attrs(body) == []
        out = sanitize_html_document(body)
        assert f'src="{url}"' in out
        assert f'srcset="{srcset}"' in out
        assert check_content(out, fmt="html") == []


def test_signature_only_png_still_survives() -> None:
    url = "data:image/png;base64,iVBORw0KGgo="
    assert raster_data_image(url)
    out = sanitize_html_document(_page(f'<img alt="dot" src="{url}">'))
    assert url in out


def test_srcset_keeps_the_base64_comma() -> None:
    url = _url("png")
    body = _page(f'<img alt="shot" srcset="{url} 1x">')
    out = sanitize_html_document(body)
    assert url in out
    assert "srcset=" in out


def test_blocked_img_urls_are_hard_and_anchors_stay_soft() -> None:
    samples = [
        "data:image/svg+xml;base64,PHN2Zy8+",
        "data:text/html,hi",
        "javascript:alert(1)",
    ]
    for sample in samples:
        img = find_bad_html_element_attrs(_page(f'<img alt="x" src="{sample}">'))
        assert img and img[0].startswith("inline image rejected")
        assert len(img[0]) < 160
        srcset = find_bad_html_element_attrs(_page(f'<img alt="x" srcset="{sample}">'))
        assert srcset and srcset[0].startswith("inline image rejected")
        href = find_bad_html_element_attrs(_page(f'<a href="{sample}">x</a>'))
        assert href and href[0].startswith("blocked or authority-confused")


def test_remote_srcset_is_not_kept() -> None:
    body = _page('<img alt="far" srcset="https://example.com/a.png 1x">')
    out = sanitize_html_document(body)
    assert "srcset=" not in out
    assert "example.com" not in out or "<img" not in out


def test_non_raster_srcset_is_dropped_beside_a_raster_src() -> None:
    url = _url("png")
    for srcset in (
        "https://evil.example/a.png 1x",
        "/a.png 1x",
        "file:///tmp/a.png",
    ):
        body = _page(f'<img alt="shot" src="{url}" srcset="{srcset}">')
        out = sanitize_html_document(body)
        assert f'src="{url}"' in out
        assert "srcset=" not in out
        assert "evil.example" not in out
        assert "file:" not in out
        assert check_content(out, fmt="html") == []


def test_raster_data_url_on_href_stays_blocked() -> None:
    url = _url("png")
    problems = find_bad_html_element_attrs(_page(f'<a href="{url}">x</a>'))
    assert problems and problems[0].startswith("blocked or authority-confused")
    out = sanitize_html_document(_page(f'<a href="{url}">x</a>'))
    assert "data:image" not in out


def test_confused_img_src_is_rejected() -> None:
    problems = find_bad_html_element_attrs(_page('<img src="https://example.com\t@127.0.0.1/x">'))
    assert problems
    assert problems[0].startswith("blocked or authority-confused")


def test_classify_href_still_blocks_raster_data_urls() -> None:
    assert classify_href(_url("png")) == "blocked"


def test_csp_constants_are_unchanged() -> None:
    assert "img-src data:" in EXPORT_CSP
    assert "script-src 'none'" in EXPORT_CSP
    assert EXPORT_CSP == (
        "default-src 'none'; script-src 'none'; style-src 'unsafe-inline'; "
        "img-src data:; font-src data:; object-src 'none'; base-uri 'none'; form-action 'none'"
    )
    assert CSP.startswith(
        "default-src 'none'; img-src data:; media-src data:; style-src 'unsafe-inline'; script-src 'none';"
    )


def test_malformed_and_oversized_rasters_fail() -> None:
    bad = "data:image/png;base64,@@@@"
    assert not raster_data_image(bad)
    problems = find_bad_html_element_attrs(_page(f'<img alt="x" src="{bad}">'))
    assert problems[0].startswith("inline image rejected: malformed base64")
    assert "@@@@" not in problems[0]
    huge = _PNG + b"A" * (MAX_DECODED_BYTES + 1)
    over = _url("png", huge)
    over_problems = find_bad_html_element_attrs(_page(f'<img alt="x" src="{over}">'))
    assert over_problems[0].startswith("inline image rejected: larger than 512 KiB")
    assert len(over_problems[0]) < 200


def test_markdown_keeps_a_raster_and_rejects_html_data() -> None:
    png = _url("png")
    kept = sanitize_markdown(f"![shot]({png})\n")
    assert png in kept
    assert "![" in kept
    rejected = find_bad_html_element_attrs("nope")
    assert rejected == []
    from artifactsmith.renderers.md_html import find_bad_markdown_raw_html_attrs

    bad = find_bad_markdown_raw_html_attrs("![shot](data:text/html,x)\n")
    assert bad and bad[0].startswith("inline image rejected")


@pytest.mark.parametrize("kind", ["jpg", "avif"])
def test_markdown_share_renders_jpg_and_avif(kind: str) -> None:
    url = _url(kind)
    page = markdown_share_html(f"![shot]({url})\n".encode(), title="Shot", style="house")
    text = page.decode()
    assert f'<img alt="shot" src="{url}">' in text
    assert "img{max-width:100%;height:auto" in text


def test_markdown_https_image_stays_a_link() -> None:
    page = markdown_share_html(b"![shot](https://example.com/a.png)\n", title="Shot", style="house")
    text = page.decode()
    assert "<img" not in text
    assert "https://example.com/a.png" in text


@pytest.mark.asyncio
async def test_publish_html_keeps_inline_png() -> None:
    url = _url("png")
    result = await publish_page(
        fmt="html",
        display_name="Shots",
        html=_page(f'<img alt="shot" src="{url}">'),
    )
    stored = result.files["index.html"].decode()
    assert url in stored


@pytest.mark.asyncio
async def test_publish_rejects_text_html_svg_and_script_img() -> None:
    samples = [
        "data:text/html,hi",
        "data:image/svg+xml,<svg></svg>",
        "javascript:alert(1)",
    ]
    for sample in samples:
        with pytest.raises(RetryableCheck, match="inline image rejected"):
            await publish_page(
                fmt="html",
                display_name="Shots",
                html=_page(f'<img alt="x" src="{sample}">'),
            )


def test_code_span_data_image_is_not_a_hard_problem() -> None:
    from artifactsmith.renderers.md_html import find_bad_markdown_raw_html_attrs

    text = 'Use `<img src="data:image/png;base64,AAAA">` in docs.\n'
    assert find_bad_markdown_raw_html_attrs(text) == []
    assert "AAAA" in sanitize_markdown(text)
