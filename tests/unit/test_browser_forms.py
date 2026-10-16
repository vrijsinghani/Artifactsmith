"""A real Chromium submits the trial form and the My pages login form."""

from __future__ import annotations

import os
import socket
import subprocess
import threading
import time
from pathlib import Path

import uvicorn
from playwright.sync_api import sync_playwright
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


def test_chromium_submits_trial_and_login_forms(monkeypatch):
    chrome = _chrome()
    assert chrome, "CHROME_BIN or a system chromium is required"
    port = _free_port()
    origin = f"http://127.0.0.1:{port}"
    monkeypatch.setattr("artifactsmith.service.Store", FakeStore)
    monkeypatch.setattr(CFG, "trial_enabled", True)
    monkeypatch.setattr(CFG, "cookie_secure", False)
    monkeypatch.setattr(CFG, "allowed_hosts", [f"127.0.0.1:{port}", f"localhost:{port}"])
    monkeypatch.setattr(CFG, "api_url", origin)
    monkeypatch.setattr(CFG, "smtp_host", "smtp.example")
    monkeypatch.setattr(CFG, "smtp_from", "trial@example.com")
    box: dict[str, str] = {}

    def fake_send(to: str, url: str) -> None:
        box["to"] = to
        box["url"] = url

    monkeypatch.setattr("artifactsmith.web.trial_pages.send_magic_link", fake_send)
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
            page.goto(origin + "/trial")
            assert f"{int(CFG.trial_build_limit)} free builds" in page.content()
            page.locator("#email").fill("person@example.com")
            page.get_by_role("button", name="Email me a link").click()
            page.get_by_text("sign-up link is on its way").wait_for()
            assert "form expired" not in page.content().lower()

            page.goto(box["url"])
            page.get_by_role("button", name="Show my token").click()
            page.get_by_text("Your trial token").wait_for()
            fits = page.evaluate(
                "() => document.documentElement.scrollWidth <= document.documentElement.clientWidth + 1"
            )
            assert fits

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
            browser.close()
    finally:
        instance.should_exit = True
        thread.join(timeout=5)
        server.SVC = None
