"""scripts/run-security-semgrep.sh: fetch failure stops the step; scans.json must be complete."""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "scripts" / "run-security-semgrep.sh"


def _run(*args: str, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["bash", str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, **(env or {})},
    )


def _clean_scans() -> dict[str, object]:
    return {
        "failed": [],
        "skipped": [],
        "coveredNothing": [],
        "oversized": [],
        "scans": [{"lang": "python", "ruleset": "p/python", "partial": False, "findings": 0}],
    }


def test_summarize_clean(tmp_path: Path):
    scans = tmp_path / "scans.json"
    scans.write_text(json.dumps(_clean_scans()))
    result = _run("--summarize", str(scans))
    assert result.returncode == 0, result.stderr
    assert "summary is clean" in result.stdout


def test_summarize_fails_on_failed(tmp_path: Path):
    scans = tmp_path / "scans.json"
    data = _clean_scans()
    data["failed"] = [{"lang": "all", "ruleset": "p/secrets", "error": "boom"}]
    scans.write_text(json.dumps(data))
    result = _run("--summarize", str(scans))
    assert result.returncode != 0
    assert "not complete" in result.stderr


def test_summarize_fails_on_skipped(tmp_path: Path):
    scans = tmp_path / "scans.json"
    data = _clean_scans()
    data["skipped"] = [{"ruleset": "https://example.com/rules.git", "reason": "clone failed"}]
    scans.write_text(json.dumps(data))
    result = _run("--summarize", str(scans))
    assert result.returncode != 0


def test_summarize_fails_on_covered_nothing(tmp_path: Path):
    scans = tmp_path / "scans.json"
    data = _clean_scans()
    data["coveredNothing"] = ["yaml/p/docker-compose"]
    scans.write_text(json.dumps(data))
    result = _run("--summarize", str(scans))
    assert result.returncode != 0


def test_summarize_fails_on_oversized(tmp_path: Path):
    scans = tmp_path / "scans.json"
    data = _clean_scans()
    data["oversized"] = ["vendor/huge.bin"]
    scans.write_text(json.dumps(data))
    result = _run("--summarize", str(scans))
    assert result.returncode != 0


def test_summarize_fails_on_partial(tmp_path: Path):
    scans = tmp_path / "scans.json"
    data = _clean_scans()
    data["scans"] = [{"lang": "python", "ruleset": "p/python", "partial": True}]
    scans.write_text(json.dumps(data))
    result = _run("--summarize", str(scans))
    assert result.returncode != 0


def test_failed_fetch_stops_the_step(tmp_path: Path):
    fake = tmp_path / "fetch-upstream.sh"
    fake.write_text('#!/usr/bin/env bash\necho "should-not-leak"\nexit 1\n')
    fake.chmod(0o755)
    result = _run(env={"FETCH_UPSTREAM": str(fake)})
    assert result.returncode != 0
    assert "should-not-leak" not in result.stdout
    assert "failed to fetch" in result.stderr
