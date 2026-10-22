"""Chromium checks that the share credit opens, and that the page stays within 390 px."""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
import uvicorn
from playwright.sync_api import Page, sync_playwright
from tests.unit.test_server import FakeStore
from tests.unit.test_share_credit import _PAGE, _seed

import artifactsmith.server as server
from artifactsmith.service import Service
from artifactsmith.share_credit import REPO


@pytest.fixture
def svc(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Service]:
    monkeypatch.setattr("artifactsmith.service.Store", FakeStore)
    monkeypatch.setattr("artifactsmith.server.CFG.data_dir", tmp_path)
    monkeypatch.setattr("artifactsmith.server.CFG.secrets_dir", tmp_path / "secrets")
    (tmp_path / "secrets").mkdir(parents=True, exist_ok=True)
    service = Service()
    server.SVC = service
    yield service
    server.SVC = None


def _chrome() -> str | None:
    env = os.environ.get("CHROME_BIN") or os.environ.get("CHROMIUM_BIN")
    if env and Path(env).is_file():
        return env
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        found = subprocess.run(["which", name], capture_output=True, text=True, check=False)
        if found.returncode == 0 and found.stdout.strip():
            return found.stdout.strip()
    return None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _overflow(page: Page) -> int:
    return int(page.evaluate("() => document.documentElement.scrollWidth - document.documentElement.clientWidth"))


@contextmanager
def _browser(port: int) -> Iterator[Page]:
    chrome = _chrome()
    assert chrome, "CHROME_BIN or a system chromium is required"
    config = uvicorn.Config(server.preview_app, host="127.0.0.1", port=port, log_level="warning", access_log=False)
    instance = uvicorn.Server(config)
    thread = threading.Thread(target=instance.run, daemon=True)
    thread.start()
    deadline = time.time() + 15
    while not instance.started and time.time() < deadline:
        time.sleep(0.05)
    assert instance.started
    try:
        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(
                executable_path=chrome,
                headless=True,
                args=["--no-sandbox", "--disable-dev-shm-usage"],
            )
            context = browser.new_context(viewport={"width": 390, "height": 800})

            def _route(route) -> None:  # type: ignore[no-untyped-def]
                url = route.request.url
                if url.startswith("http://127.0.0.1"):
                    route.continue_()
                    return
                route.fulfill(
                    status=200,
                    content_type="text/html",
                    body=f"<!DOCTYPE html><html><title>landed</title><body>{url}</body></html>",
                )

            context.route("**/*", _route)
            page = context.new_page()
            try:
                yield page
            finally:
                browser.close()
    finally:
        instance.should_exit = True
        thread.join(timeout=5)


def test_credit_link_opens_in_a_new_tab_and_does_not_widen_the_page(svc: Service) -> None:
    from artifactsmith.layout.render import render_layout

    sid = _seed(svc, aid="art_credit_click", body=_PAGE)
    fast, _notes = render_layout(
        "# Harbor watch\n\nSee the [company calendar](https://example.com/docs).\n\n"
        "| Name | Strike |\n|---|---|\n| AMAT | $535 |\n\n```chart bar unit=$\nAMAT: 535\n```\n",
        style="house",
        title="Harbor watch",
    )
    fast_sid = _seed(svc, aid="art_credit_fast", body=fast.encode())
    hostile = (
        b"<html><head><style>footer{display:none!important}body *{display:none!important}"
        b"a{pointer-events:none!important}</style></head><body><p>HARBOR-17</p></body></html>"
    )
    hostile_sid = _seed(svc, aid="art_credit_hide", body=hostile)
    port = _free_port()
    origin = f"http://127.0.0.1:{port}"
    with _browser(port) as page:
        opened = page.goto(f"{origin}/s/{sid}/")
        assert opened is not None
        assert "allow-popups-to-escape-sandbox" in opened.headers.get("content-security-policy", "")
        assert page.locator("table").count() == 1
        assert page.locator("img[alt=mark]").count() == 1
        assert _overflow(page) <= 1
        page.set_viewport_size({"width": 1280, "height": 800})
        assert _overflow(page) <= 1
        page.set_viewport_size({"width": 390, "height": 800})
        with page.expect_popup() as credit_popup:
            page.locator("#am-share-credit a").click()
        popup = credit_popup.value
        popup.wait_for_load_state("domcontentloaded")
        assert popup.url.startswith(REPO)
        assert popup.evaluate("() => window.opener === null") is True
        with page.expect_popup() as other_popup:
            page.locator('a[href="https://example.com/other"]').click()
        other = other_popup.value
        other.wait_for_load_state("domcontentloaded")
        assert other.url.startswith("https://example.com/other")
        with page.expect_navigation():
            page.locator('a[href="https://example.com/docs"]').click()
        assert page.url.startswith("https://example.com/docs")
        fast_page = page.goto(f"{origin}/s/{fast_sid}/")
        assert fast_page is not None and fast_page.ok
        assert page.locator(".asl-chart").count() == 1
        assert page.locator("#am-share-credit a").count() == 1
        assert _overflow(page) <= 1
        page.set_viewport_size({"width": 1280, "height": 800})
        assert _overflow(page) <= 1
        page.set_viewport_size({"width": 390, "height": 800})
        with page.expect_navigation():
            page.locator('a[href="https://example.com/docs"]').click()
        assert page.url.startswith("https://example.com/docs")
        hidden = page.goto(f"{origin}/s/{hostile_sid}/")
        assert hidden is not None and hidden.ok
        box = page.locator("#am-share-credit").bounding_box()
        assert box is not None and box["height"] > 0 and box["width"] > 0
        assert _overflow(page) <= 1
