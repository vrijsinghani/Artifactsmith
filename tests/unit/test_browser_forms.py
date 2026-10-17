"""A real Chromium submits the trial form, the My pages login form, and OAuth."""

from __future__ import annotations

import base64
import hashlib
import os
import socket
import subprocess
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlencode

import uvicorn
from playwright.sync_api import Page, sync_playwright
from tests.unit.test_service import FakeStore

import artifactsmith.server as server
from artifactsmith import trial
from artifactsmith.config import CFG
from artifactsmith.service import Service


def _chrome() -> str | None:
    env = os.environ.get("CHROME_BIN") or os.environ.get("CHROMIUM_BIN")
    if env and Path(env).is_file():
        return env
    for name in ("google-chrome", "chromium", "chromium-browser", "chrome"):
        path = subprocess.run(["which", name], capture_output=True, text=True, check=False)
        if path.returncode == 0 and path.stdout.strip():
            return path.stdout.strip()
    return None


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


@contextmanager
def _chromium(monkeypatch) -> Iterator[tuple[Page, str, Service]]:
    chrome = _chrome()
    assert chrome, "CHROME_BIN or a system chromium is required"
    port = _free_port()
    origin = f"http://127.0.0.1:{port}"
    monkeypatch.setattr("artifactsmith.service.Store", FakeStore)
    monkeypatch.setattr(CFG, "trial_enabled", True)
    monkeypatch.setattr(CFG, "cookie_secure", False)
    monkeypatch.setattr(CFG, "allowed_hosts", [f"127.0.0.1:{port}", f"localhost:{port}"])
    monkeypatch.setattr(CFG, "api_url", origin)
    svc = Service()
    server.SVC = svc
    config = uvicorn.Config(server.ApiApp(), host="127.0.0.1", port=port, log_level="warning", access_log=False)
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
            page = browser.new_page(viewport={"width": 390, "height": 800})
            try:
                yield page, origin, svc
            finally:
                browser.close()
    finally:
        instance.should_exit = True
        thread.join(timeout=5)
        server.SVC = None


def test_chromium_submits_trial_and_login_forms(monkeypatch):
    monkeypatch.setattr(CFG, "trial_email_verify", True)
    monkeypatch.setattr(CFG, "smtp_host", "smtp.example")
    monkeypatch.setattr(CFG, "smtp_from", "trial@example.com")
    box: dict[str, str] = {}

    def fake_send(to: str, url: str) -> None:
        box["to"] = to
        box["url"] = url

    monkeypatch.setattr("artifactsmith.web.trial_pages.send_magic_link", fake_send)
    with _chromium(monkeypatch) as (page, origin, svc):
        page.goto(origin + "/trial")
        assert "Try it before you install" in page.content()
        assert "Open source, MIT" in page.content()
        page.locator("#email").fill("person@example.com")
        page.get_by_role("button", name="Email me a link").click()
        page.get_by_text("sign-up link is on its way").wait_for()
        assert "form expired" not in page.content().lower()

        page.goto(box["url"])
        page.get_by_role("button", name="Show my token").click()
        page.get_by_text("Your token").wait_for()
        fits = page.evaluate("() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1")
        assert fits
        assert svc.db.one("SELECT verified FROM trial_signups")["verified"] == 1

        page.goto(origin + "/app/login")
        page.locator("#token").fill("not-a-real-token")
        page.get_by_role("button", name="Sign in").click()
        page.get_by_text("not valid").wait_for()
        assert "form expired" not in page.content().lower()

        hostile = (
            b"<html><head><style>"
            b"footer,#am-trial-report,body *{display:none!important;visibility:hidden!important;opacity:0!important}"
            b"</style></head><body><p>Hi</p></body></html>"
        )
        page.set_content(trial.stamp_trial_chrome(hostile, "abuse@example.com").decode())
        assert page.locator("#am-trial-report").is_visible()
        assert "Report abuse" in page.locator("#am-trial-report").inner_text()

        sent = dict(box)
        monkeypatch.setattr(CFG, "trial_email_verify", False)
        page.goto(origin + "/trial")
        page.locator("#email").fill("other@example.com")
        page.get_by_role("button", name="Show my token").click()
        page.get_by_text("Your token").wait_for()
        assert "form expired" not in page.content().lower()
        assert "sign-up link is on its way" not in page.content()
        assert page.locator("input[readonly]").input_value().startswith("asmb_")
        assert box == sent
        row = svc.db.one("SELECT verified FROM trial_signups WHERE verified=0")
        assert row is not None and row["verified"] == 0
        _oauth_redirects_away(page, origin, monkeypatch)


def _oauth_redirects_away(page: Page, origin: str, monkeypatch) -> None:
    """Headless Chromium must leave /oauth/authorize for an allowlisted loopback callback."""

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self.send_response(200)
            self.send_header("content-type", "text/plain")
            self.end_headers()
            self.wfile.write(b"arrived")

        def log_message(self, fmt: str, *args: object) -> None:
            return

    callback = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=callback.serve_forever, daemon=True)
    thread.start()
    try:
        port = int(callback.server_address[1])
        verifier = "b" * 50
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
        redirect = f"http://127.0.0.1:{port}/callback"
        query = urlencode(
            {
                "response_type": "code",
                "client_id": "artifactsmith",
                "redirect_uri": redirect,
                "state": "state-1",
                "code_challenge": challenge,
                "code_challenge_method": "S256",
            }
        )
        monkeypatch.setattr(CFG, "api_url", "https://trial.example")
        monkeypatch.setattr(CFG, "trial_email_verify", False)
        opened = page.goto(origin + "/oauth/authorize?" + query)
        assert opened is not None
        policy = opened.headers.get("content-security-policy", "")
        assert "form-action 'self' http://127.0.0.1:*" in policy
        assert "form-action *" not in policy
        page.locator("#email").fill("oauth-browser@example.com")
        page.get_by_role("button", name="Continue").click()
        page.wait_for_url(f"http://127.0.0.1:{port}/callback**", timeout=15000)
        assert "/oauth/authorize" not in page.url
        assert "code=" in page.url
        assert "arrived" in page.content()
    finally:
        callback.shutdown()
        thread.join(timeout=5)
