"""Client address for login limits.

Forwarded headers count only when the TCP peer is listed in AM_TRUSTED_PROXIES.
Any other peer is its own bucket, even if it sends X-Forwarded-For.
"""

from __future__ import annotations

import ipaddress

from starlette.requests import Request

from ..config import CFG


def _parse_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        return ipaddress.ip_address(value.strip())
    except ValueError:
        return None


def _peer(request: Request) -> str:
    client = request.client
    host = client.host if client is not None else ""
    return host or "unknown"


def peer_is_trusted(peer: str) -> bool:
    addr = _parse_ip(peer)
    if addr is None:
        return False
    return any(addr in net for net in CFG.trusted_proxies)


def _bucket(value: str, prefix: int) -> str:
    """IPv6 shares one prefix. IPv4 stays one address. Mapped IPv6 is that IPv4.

    Anything that is not an IP stays as given, so a missing peer is not unlimited.
    """
    addr = _parse_ip(value)
    if isinstance(addr, ipaddress.IPv6Address):
        mapped = addr.ipv4_mapped
        if mapped is not None:
            return str(mapped)
        network = ipaddress.ip_network(f"{addr}/{prefix}", strict=False)
        return f"{network.network_address}/{prefix}"
    if isinstance(addr, ipaddress.IPv4Address):
        return str(addr)
    return value or "unknown"


def limit_key(value: str) -> str:
    """Bucket for a login attempt. IPv6 shares a /64."""
    return _bucket(value, 64)


def signup_key(value: str) -> str:
    """Bucket for a trial sign-up. IPv6 shares a /48."""
    return _bucket(value, 48)


def client_host(request: Request) -> str:
    """Client address before a rate-limit bucket.

    A trusted peer may name the browser with X-Real-IP, or with the rightmost
    X-Forwarded-For hop. A missing or invalid header stays on the peer.
    """
    peer = _peer(request)
    if not peer_is_trusted(peer):
        return peer
    real = _parse_ip(request.headers.get("x-real-ip", ""))
    if real is not None:
        return str(real)
    hops = [part.strip() for part in request.headers.get("x-forwarded-for", "").split(",") if part.strip()]
    if hops:
        forwarded = _parse_ip(hops[-1])
        if forwarded is not None:
            return str(forwarded)
    return peer


def client_ip(request: Request) -> str:
    """Rate-limit key for this login attempt. IPv6 keys are the /64."""
    return limit_key(client_host(request))
