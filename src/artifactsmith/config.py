"""Runtime config from env. Every value also has a NAME_FILE variant that reads a file.
Secrets are read from files and never logged."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
from dataclasses import dataclass, field
from pathlib import Path


def _read_file(path: str) -> str:
    p = Path(path)
    return p.read_text().strip() if p.is_file() else ""


def _setting(name: str, default: str = "") -> str:
    """Resolve a setting: NAME wins, then NAME_FILE (a path whose contents are the value), else default."""
    v = os.environ.get(name)
    if v is not None and v != "":
        return v.strip()
    fp = os.environ.get(f"{name}_FILE")
    if fp:
        v = _read_file(fp)
        if v:
            return v
    return default


def _setting_int(name: str, default: int) -> int:
    raw = _setting(name, "")
    if raw == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def _setting_bool(name: str, default: bool) -> bool:
    raw = _setting(name, "").lower()
    if raw == "":
        return default
    return raw in ("1", "true", "on", "yes")


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path(_setting("AM_DATA_DIR", "/data")))
    secrets_dir: Path = field(default_factory=lambda: Path(_setting("AM_SECRETS_DIR", "/secrets")))
    host: str = field(default_factory=lambda: _setting("AM_HOST", "0.0.0.0"))
    api_port: int = field(default_factory=lambda: _setting_int("AM_API_PORT", 8780))
    preview_port: int = field(default_factory=lambda: _setting_int("AM_PREVIEW_PORT", 8781))
    api_url: str = field(default_factory=lambda: _setting("AM_API_URL", "http://127.0.0.1:8780"))
    preview_url: str = field(default_factory=lambda: _setting("AM_PREVIEW_URL", "http://127.0.0.1:8781"))
    # Public share base. Defaults to the preview origin. A reverse proxy can front only /s/*.
    share_url: str = field(default_factory=lambda: _setting("AM_SHARE_URL", "") or _setting("AM_PREVIEW_URL", "http://127.0.0.1:8781"))

    # Share lifetime. 0 = until revoked (far-future expiry). Positive capped by AM_SHARE_TTL_MAX_DAYS.
    share_ttl_days: int = field(default_factory=lambda: _setting_int("AM_SHARE_TTL_DAYS", 30))
    share_ttl_max_days: int = field(default_factory=lambda: _setting_int("AM_SHARE_TTL_MAX_DAYS", 365))

    # LLM adapter. AM_LLM_API=chat (default) or "responses". Base defaults to the standard OpenAI host.
    llm_api: str = field(default_factory=lambda: _setting("AM_LLM_API", "chat").lower())
    llm_base: str = field(default_factory=lambda: _setting("AM_LLM_BASE", "https://api.openai.com"))
    default_model: str = field(default_factory=lambda: _setting("AM_DEFAULT_MODEL", "gpt-4o-mini"))

    # Object store (S3-compatible). rustfs in compose.
    store_endpoint: str = field(default_factory=lambda: _setting("AM_STORE_ENDPOINT", ""))
    store_bucket: str = field(default_factory=lambda: _setting("AM_STORE_BUCKET", "artifacts"))

    # Output policy.
    max_concurrent_builds: int = field(default_factory=lambda: _setting_int("AM_MAX_BUILDS", 2))
    builds_per_hour: int = field(default_factory=lambda: _setting_int("AM_BUILDS_PER_HOUR", 20))
    build_timeout_s: int = field(default_factory=lambda: _setting_int("AM_BUILD_TIMEOUT", 900))
    render_timeout_s: int = field(default_factory=lambda: _setting_int("AM_RENDER_TIMEOUT", 120))
    max_output_bytes: int = field(default_factory=lambda: _setting_int("AM_MAX_OUTPUT_BYTES", 50 * 1024 * 1024))
    # Link allow-list (comma-separated hostnames). Empty by default.
    allowed_link_domains: list[str] = field(default_factory=lambda: [h.strip() for h in _setting("AM_ALLOWED_LINK_DOMAINS", "").split(",") if h.strip()])
    block_private_links: bool = field(default_factory=lambda: _setting_bool("AM_BLOCK_PRIVATE_LINKS", True))

    @property
    def db_path(self) -> Path:
        return self.data_dir / "state.sqlite"

    @property
    def audit_path(self) -> Path:
        return self.data_dir / "audit.jsonl"

    def store_credentials(self) -> tuple[str, str]:
        # Access/secret keys for the object store. NAME + NAME_FILE variants.
        ak = _setting("AM_STORE_KEY") or _setting("AM_STORE_ACCESS_KEY")
        sk = _setting("AM_STORE_SECRET") or _setting("AM_STORE_SECRET_KEY")
        return ak, sk

    def llm_key(self) -> str:
        return _setting("AM_LLM_KEY") or _setting("OPENAI_API_KEY")

    def signing_key(self) -> bytes:
        """Stable HMAC key generated on first boot and persisted under the secrets dir."""
        p = self.secrets_dir / "signing.key"
        existing = _read_file(str(p))
        if existing:
            return existing.encode()
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        key = secrets.token_urlsafe(32)
        p.write_text(key)
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
        return key.encode()


CFG = Config()
