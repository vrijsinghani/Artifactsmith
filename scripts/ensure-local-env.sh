#!/usr/bin/env bash
# Create .env from .env.example if needed, and fill empty object-store keys.
# The main compose file refuses to start when AM_STORE_KEY / AM_STORE_SECRET are unset.
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"

if [ ! -f .env ]; then
  cp .env.example .env
  echo "wrote .env from .env.example"
fi

python3 - <<'PY'
from pathlib import Path
import re
import secrets

path = Path(".env")
text = path.read_text()


def fill(name: str, value: str, body: str) -> tuple[str, bool]:
    if re.search(rf"^{re.escape(name)}=.+$", body, re.M):
        return body, False
    if re.search(rf"^{re.escape(name)}=$", body, re.M):
        return re.sub(rf"^{re.escape(name)}=$", f"{name}={value}", body, count=1, flags=re.M), True
    return body + f"\n{name}={value}\n", True


text, key_written = fill("AM_STORE_KEY", secrets.token_hex(16), text)
text, secret_written = fill("AM_STORE_SECRET", secrets.token_hex(24), text)
path.write_text(text)
if key_written or secret_written:
    print("generated AM_STORE_KEY / AM_STORE_SECRET in .env")
else:
    print(".env already has object-store credentials")
PY
