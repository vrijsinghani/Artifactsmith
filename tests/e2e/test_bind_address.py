"""Prove AM_BIND_ADDRESS=0.0.0.0 publishes on a non-loopback host IP and public URLs stick.

Run after ensure-local-env and with docker available:

    python -m tests.e2e.test_bind_address
"""

from __future__ import annotations

import asyncio
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

from tests.e2e.stack import Fail, check, compose, phase, provision, wait_health

REPO = Path(__file__).resolve().parents[2]


def _host_ipv4() -> str:
    """Pick a non-loopback IPv4 the host can answer on."""
    # Prefer the address used for outbound traffic (works on CI runners and laptops).
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        if ip and not ip.startswith("127."):
            return ip
    except OSError:
        pass
    out = subprocess.check_output(["hostname", "-I"], text=True)
    for part in out.split():
        if part.count(".") == 3 and not part.startswith("127."):
            return part
    raise Fail("no non-loopback IPv4 address found for bind smoke")


def _set_env(path: Path, updates: dict[str, str]) -> None:
    lines: list[str] = []
    if path.is_file():
        lines = path.read_text().splitlines()
    seen: set[str] = set()
    out: list[str] = []
    for line in lines:
        if "=" in line and not line.lstrip().startswith("#"):
            key = line.split("=", 1)[0].strip()
            if key in updates:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
                continue
        out.append(line)
    for key, value in updates.items():
        if key not in seen:
            out.append(f"{key}={value}")
    path.write_text("\n".join(out) + "\n")


async def main() -> int:
    ip = _host_ipv4()
    api = f"http://{ip}:8780"
    preview = f"http://{ip}:8781"
    print(f"bind smoke: host={ip}")

    env_path = REPO / ".env"
    subprocess.run(["bash", "scripts/ensure-local-env.sh"], cwd=REPO, check=True, capture_output=True)
    backup = Path(tempfile.mkstemp(prefix="am-env-", suffix=".bak")[1])
    shutil.copy2(env_path, backup)

    def restore_env() -> None:
        if backup.is_file():
            shutil.copy2(backup, env_path)
            backup.unlink(missing_ok=True)

    try:
        _set_env(
            env_path,
            {
                "AM_BIND_ADDRESS": "0.0.0.0",
                "AM_API_URL": api,
                "AM_PREVIEW_URL": preview,
                "AM_SHARE_URL": preview,
                "AM_ALLOWED_HOSTS": f"{ip}:8780,127.0.0.1:8780,localhost:8780",
            },
        )

        compose("down", "-v")
        compose("up", "-d", "--build")
        try:
            await wait_health(api, tries=120)
            r = httpx.get(f"{api}/healthz", timeout=10)
            check(r.status_code == 200, f"/healthz via non-loopback {api} returned 200")

            token = provision("bind-agent", "alpha", "create,read,edit,export,share,delete")

            async def build(s):  # type: ignore[no-untyped-def]
                from tests.e2e.stack import call

                created = await call(
                    s,
                    "create",
                    {
                        "slug": "bind-smoke",
                        "display_name": "Bind smoke",
                        "kind": "web_static",
                        "verbatim_request": "One short page that says BIND-OK.",
                        "source_content": "The marker is BIND-OK.",
                        "format": "html",
                    },
                )
                check("job_id" in created, "create returned job_id")
                status = await call(s, "status", {"job_id": created["job_id"], "wait": 90}, read_timeout=120)
                check(status.get("status") == "done", f"build finished: {status.get('status')}")
                preview_url = status.get("preview_url") or (status.get("card") or {}).get("preview_url")
                check(isinstance(preview_url, str) and preview_url, "status includes preview_url")
                check(
                    preview_url.startswith(preview + "/"),
                    f"preview_url uses AM_PREVIEW_URL ({preview}), got {preview_url}",
                )
                page = httpx.get(preview_url, timeout=10)
                check(page.status_code == 200, "preview page reachable on non-loopback preview URL")
                check("BIND-OK" in page.text, "preview body contains marker")

            os.environ["AM_E2E_API"] = api
            os.environ["AM_E2E_PREVIEW"] = preview
            import tests.e2e.stack as stack

            stack.API = api
            stack.PREVIEW = preview
            stack.MCP_URL = f"{api}/mcp"
            await phase(token, build)
            print("bind smoke: PASS")
            return 0
        finally:
            compose("down", "-v")
    finally:
        restore_env()


if __name__ == "__main__":
    try:
        raise SystemExit(asyncio.run(main()))
    except Fail as e:
        print(f"FAIL: {e}", file=sys.stderr)
        raise SystemExit(1) from e
