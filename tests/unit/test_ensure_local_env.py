"""scripts/ensure-local-env.sh must not corrupt .env when the file lacks a trailing newline."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "ensure-local-env.sh"


def _prepare(env_dir: Path) -> Path:
    """Mirror repo layout so the script's dirname/../.env resolves inside env_dir."""
    scripts = env_dir / "scripts"
    scripts.mkdir()
    dest = scripts / "ensure-local-env.sh"
    dest.write_bytes(SCRIPT.read_bytes())
    dest.chmod(0o755)
    return dest


def _run(script: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "PATH": os.environ.get("PATH", "")},
    )


def test_appends_after_missing_trailing_newline(tmp_path: Path):
    script = _prepare(tmp_path)
    # No trailing newline after OPENAI_API_KEY — previous bug glued AM_STORE_KEY onto it.
    (tmp_path / ".env").write_bytes(b"OPENAI_API_KEY=sk-test\nAM_SHARE_URL=http://x")
    result = _run(script)
    assert result.returncode == 0, result.stderr
    text = (tmp_path / ".env").read_text()
    assert "OPENAI_API_KEY=sk-test" in text
    assert "AM_SHARE_URL=http://x" in text
    assert "sk-testAM_STORE" not in text
    assert "http://xAM_STORE" not in text
    lines = {ln.split("=", 1)[0]: ln.split("=", 1)[1] for ln in text.splitlines() if "=" in ln}
    assert lines["OPENAI_API_KEY"] == "sk-test"
    assert lines["AM_SHARE_URL"] == "http://x"
    assert len(lines["AM_STORE_KEY"]) == 32
    assert len(lines["AM_STORE_SECRET"]) == 32


def test_strips_unquoted_hash_comment_as_empty(tmp_path: Path):
    script = _prepare(tmp_path)
    (tmp_path / ".env").write_text("AM_STORE_KEY= # fill me\nAM_STORE_SECRET=\n")
    result = _run(script)
    assert result.returncode == 0, result.stderr
    text = (tmp_path / ".env").read_text()
    key_line = [ln for ln in text.splitlines() if ln.startswith("AM_STORE_KEY=")][-1]
    assert key_line != "AM_STORE_KEY= # fill me"
    assert len(key_line.split("=", 1)[1].strip()) == 32


def test_writes_through_symlink(tmp_path: Path):
    script = _prepare(tmp_path)
    real = tmp_path / ".env.real"
    real.write_text("AM_STORE_KEY=\nAM_STORE_SECRET=\n")
    (tmp_path / ".env").symlink_to(real)
    result = _run(script)
    assert result.returncode == 0, result.stderr
    assert (tmp_path / ".env").is_symlink()
    text = real.read_text()
    assert "AM_STORE_KEY=" in text
    assert len(text.split("AM_STORE_KEY=")[1].splitlines()[0]) == 32
