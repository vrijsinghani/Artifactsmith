"""fetch-upstream.sh must fail closed and reject dirty or ignored cache trees."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
SCRIPT = ROOT / ".cursor" / "skills" / "fetch-upstream.sh"

GIT_ENV = {
    "GIT_AUTHOR_NAME": "test",
    "GIT_AUTHOR_EMAIL": "test@example.com",
    "GIT_COMMITTER_NAME": "test",
    "GIT_COMMITTER_EMAIL": "test@example.com",
}


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    env = {**os.environ, **GIT_ENV}
    return subprocess.run(
        ["git", "-c", "commit.gpgsign=false", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )


def _fake_tob(tmp: Path) -> tuple[Path, str]:
    repo = tmp / "tob.git"
    (repo / "plugins/static-analysis/skills/semgrep/scripts").mkdir(parents=True)
    (repo / "plugins/static-analysis/skills/semgrep/SKILL.md").write_text("# semgrep\n")
    (repo / "plugins/static-analysis/skills/semgrep/scripts/run-scans.sh").write_text("#!/bin/sh\n")
    (repo / "plugins/static-analysis/skills/semgrep/scripts/merge_sarif.py").write_text("#\n")
    _git(tmp, "init", "-q", "-b", "main", str(repo))
    _git(repo, "add", ".")
    _git(repo, "commit", "-q", "-m", "init")
    sha = _git(repo, "rev-parse", "HEAD").stdout.strip()
    return repo, sha


def _run(cache: Path, tob: Path, sha: str, *names: str) -> subprocess.CompletedProcess[str]:
    env = {
        **os.environ,
        "ARTIFACTSMITH_SKILL_CACHE": str(cache),
        "ARTIFACTSMITH_TOB_URL": tob.resolve().as_uri(),
        "ARTIFACTSMITH_TOB_SHA": sha,
    }
    return subprocess.run(
        ["bash", str(SCRIPT), *names],
        capture_output=True,
        text=True,
        check=False,
        env=env,
    )


@pytest.fixture
def tob_cache(tmp_path: Path) -> tuple[Path, Path, str]:
    tob, sha = _fake_tob(tmp_path)
    cache = tmp_path / "cache"
    cache.mkdir()
    return cache, tob, sha


def test_unknown_name_prints_nothing(tob_cache: tuple[Path, Path, str]):
    cache, tob, sha = tob_cache
    result = _run(cache, tob, sha, "nosuch")
    assert result.returncode != 0
    assert result.stdout.strip() == ""


def test_failed_second_name_prints_no_paths(tob_cache: tuple[Path, Path, str]):
    cache, tob, sha = tob_cache
    result = _run(cache, tob, sha, "semgrep", "nosuch")
    assert result.returncode != 0
    assert result.stdout.strip() == ""


def test_semgrep_fetch_prints_skill_path(tob_cache: tuple[Path, Path, str]):
    cache, tob, sha = tob_cache
    result = _run(cache, tob, sha, "semgrep")
    assert result.returncode == 0, result.stderr
    dest = Path(result.stdout.strip())
    assert dest.is_dir()
    assert (dest / "SKILL.md").is_file()
    assert (dest / "scripts" / "run-scans.sh").is_file()


def test_untracked_file_forces_refetch(tob_cache: tuple[Path, Path, str]):
    cache, tob, sha = tob_cache
    first = _run(cache, tob, sha, "semgrep")
    assert first.returncode == 0, first.stderr
    clone = cache / "semgrep"
    junk = clone / "UNTRACKED"
    junk.write_text("nope")
    second = _run(cache, tob, sha, "semgrep")
    assert second.returncode == 0, second.stderr
    assert not junk.exists()
    assert (Path(second.stdout.strip()) / "SKILL.md").is_file()


def test_ignored_file_forces_refetch(tob_cache: tuple[Path, Path, str]):
    cache, tob, sha = tob_cache
    first = _run(cache, tob, sha, "semgrep")
    assert first.returncode == 0, first.stderr
    clone = cache / "semgrep"
    exclude = clone / ".git" / "info" / "exclude"
    exclude.parent.mkdir(parents=True, exist_ok=True)
    exclude.write_text(exclude.read_text() + "\n*.ignoreme\n" if exclude.exists() else "*.ignoreme\n")
    ignored = clone / "foo.ignoreme"
    ignored.write_text("secret")
    second = _run(cache, tob, sha, "semgrep")
    assert second.returncode == 0, second.stderr
    assert not ignored.exists()


def test_helper_is_the_repo_copy():
    assert SCRIPT.is_file()
    assert shutil.which("bash")
