"""Runtime config from env. Every value also has a NAME_FILE variant that reads a file.
Secrets are read from files and never logged. Invalid settings fail closed at load time."""

from __future__ import annotations

import os
import secrets
import tempfile
from dataclasses import dataclass, field
from pathlib import Path


class ConfigError(ValueError):
    """Raised when an environment setting is present but invalid."""


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


def _setting_int(name: str, default: int, *, minimum: int | None = None, maximum: int | None = None) -> int:
    raw = _setting(name, "")
    if raw == "":
        return default
    try:
        value = int(raw)
    except ValueError as e:
        raise ConfigError(f"{name} must be an integer, got {raw!r}") from e
    if minimum is not None and value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    if maximum is not None and value > maximum:
        raise ConfigError(f"{name} must be <= {maximum}, got {value}")
    return value


def _setting_bool(name: str, default: bool) -> bool:
    raw = _setting(name, "").lower()
    if raw == "":
        return default
    if raw in ("1", "true", "on", "yes"):
        return True
    if raw in ("0", "false", "off", "no"):
        return False
    raise ConfigError(f"{name} must be true/false (or 1/0/on/off/yes/no), got {raw!r}")


def _default_allowed_hosts() -> list[str]:
    raw = _setting("AM_ALLOWED_HOSTS", "")
    if raw.strip():
        return [h.strip() for h in raw.split(",") if h.strip()]
    port = _setting_int("AM_API_PORT", 8780, minimum=1, maximum=65535)
    return [f"127.0.0.1:{port}", f"localhost:{port}"]


@dataclass
class Config:
    data_dir: Path = field(default_factory=lambda: Path(_setting("AM_DATA_DIR", "/data")))
    secrets_dir: Path = field(default_factory=lambda: Path(_setting("AM_SECRETS_DIR", "/secrets")))
    # Loopback by default so `artifactsmith serve` outside compose is not world-reachable.
    # Compose / the container image set AM_HOST=0.0.0.0 for in-container listen.
    host: str = field(default_factory=lambda: _setting("AM_HOST", "127.0.0.1"))
    api_port: int = field(default_factory=lambda: _setting_int("AM_API_PORT", 8780, minimum=1, maximum=65535))
    preview_port: int = field(default_factory=lambda: _setting_int("AM_PREVIEW_PORT", 8781, minimum=1, maximum=65535))
    api_url: str = field(default_factory=lambda: _setting("AM_API_URL", "http://127.0.0.1:8780"))
    preview_url: str = field(default_factory=lambda: _setting("AM_PREVIEW_URL", "http://127.0.0.1:8781"))
    # Public share base. Defaults to the preview origin. A reverse proxy can front only /s/*.
    share_url: str = field(
        default_factory=lambda: _setting("AM_SHARE_URL", "") or _setting("AM_PREVIEW_URL", "http://127.0.0.1:8781")
    )

    # Share lifetime. 0 = until revoked (far-future expiry). Positive capped by AM_SHARE_TTL_MAX_DAYS.
    share_ttl_days: int = field(default_factory=lambda: _setting_int("AM_SHARE_TTL_DAYS", 30, minimum=0))
    share_ttl_max_days: int = field(default_factory=lambda: _setting_int("AM_SHARE_TTL_MAX_DAYS", 365, minimum=1))

    # LLM adapter. AM_LLM_API=chat (default) or "responses". Base defaults to the standard OpenAI host.
    llm_api: str = field(default_factory=lambda: _setting("AM_LLM_API", "chat").lower())
    llm_base: str = field(default_factory=lambda: _setting("AM_LLM_BASE", "https://api.openai.com"))
    default_model: str = field(default_factory=lambda: _setting("AM_DEFAULT_MODEL", "gpt-4o-mini"))

    # Object store (S3-compatible). rustfs in compose.
    store_endpoint: str = field(default_factory=lambda: _setting("AM_STORE_ENDPOINT", ""))
    store_bucket: str = field(default_factory=lambda: _setting("AM_STORE_BUCKET", "artifacts"))

    # Output policy.
    max_concurrent_builds: int = field(default_factory=lambda: _setting_int("AM_MAX_BUILDS", 2, minimum=1))
    builds_per_hour: int = field(default_factory=lambda: _setting_int("AM_BUILDS_PER_HOUR", 20, minimum=1))
    build_timeout_s: int = field(default_factory=lambda: _setting_int("AM_BUILD_TIMEOUT", 900, minimum=1))
    render_timeout_s: int = field(default_factory=lambda: _setting_int("AM_RENDER_TIMEOUT", 120, minimum=1))
    max_output_bytes: int = field(
        default_factory=lambda: _setting_int("AM_MAX_OUTPUT_BYTES", 50 * 1024 * 1024, minimum=1)
    )
    # Link allow-list (comma-separated hostnames). Empty by default.
    allowed_link_domains: list[str] = field(
        default_factory=lambda: [h.strip() for h in _setting("AM_ALLOWED_LINK_DOMAINS", "").split(",") if h.strip()]
    )
    block_private_links: bool = field(default_factory=lambda: _setting_bool("AM_BLOCK_PRIVATE_LINKS", True))
    # MCP Host/Origin allow-lists (comma-separated). Protection is on by default via loopback hosts.
    allowed_hosts: list[str] = field(default_factory=lambda: _default_allowed_hosts())
    allowed_origins: list[str] = field(
        default_factory=lambda: [h.strip() for h in _setting("AM_ALLOWED_ORIGINS", "").split(",") if h.strip()]
    )

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

    def normalized_llm_base(self) -> str:
        """Host root without a trailing slash or trailing /v1.

        Callers append /v1/chat/completions or /v1/responses themselves, so
        both https://api.openai.com and https://api.openai.com/v1 are accepted.
        """
        base = self.llm_base.strip().rstrip("/")
        if base.endswith("/v1"):
            base = base[:-3].rstrip("/")
        return base

    def signing_key(self) -> bytes:
        """Stable HMAC key generated on first boot and persisted under the secrets dir."""
        p = self.secrets_dir / "signing.key"
        existing = _read_file(str(p))
        if existing:
            return existing.encode()
        self.secrets_dir.mkdir(parents=True, exist_ok=True)
        key = secrets.token_urlsafe(32)
        # Atomic create with restrictive mode so a crash cannot leave a world-readable key.
        fd, tmp_name = tempfile.mkstemp(dir=self.secrets_dir, prefix=".signing.", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                fh.write(key)
            os.chmod(tmp_name, 0o600)
            os.replace(tmp_name, p)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise
        return key.encode()


CFG = Config()
