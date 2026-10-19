"""Cline install notes name the hosted server and stay off RFC1918 addresses."""

from __future__ import annotations

import ipaddress
import re
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]
_IPV4 = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
_RFC1918 = (
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
)


def _is_private_ip(text: str) -> bool:
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False
    if not isinstance(ip, ipaddress.IPv4Address):
        return False
    return any(ip in network for network in _RFC1918)


def test_llms_install_exists_and_has_hosted_url_without_private_ips() -> None:
    path = _ROOT / "llms-install.md"
    assert path.is_file()
    text = path.read_text(encoding="utf-8")
    assert "https://artifactsmith.neuralami.ai/mcp" in text
    lowered = text.lower()
    assert "trial" not in lowered
    assert "free builds" not in lowered
    assert "no card" not in lowered
    # 127.0.0.1 is the local MCP URL. Private means RFC1918.
    for match in _IPV4.findall(text):
        assert not _is_private_ip(match), match
    readme = (_ROOT / "README.md").read_text(encoding="utf-8")
    assert "Cline follows [llms-install.md](llms-install.md)." in readme
