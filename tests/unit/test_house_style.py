"""Regression: house style is in the system prompt; response delimiters unchanged; safety checks still fire."""

import pathlib
import sys
import types

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2] / "src"))
try:
    import httpx  # noqa: F401
except ImportError:  # builder imports llm, which imports httpx; not needed for these checks
    sys.modules["httpx"] = types.ModuleType("httpx")
from artifactsmith import builder as b


def test_prompt():
    s = b.SYSTEM
    for d in ("===ASSUMPTIONS===", "===SUMMARY===", "===FILE: index.html===", "===END===", "===NEEDS_INPUT==="):
        assert d in s, d
    for phrase in (
        "7. Follow the house style",
        "Result or answer first".lower(),
        "Preserve explicitly verbatim text",
        "never override factual accuracy",
        "Visual design:",
        "Spend boldness in one place",
    ):
        assert phrase.lower() in s.lower(), phrase


def test_safety_checks_unchanged():
    assert b.check_source("<html><script>x</script></html>")
    assert b.check_source('<html><a href="https://example.com/">x</a></html>')
    assert not b.check_source("<html><p>ok</p></html>")


if __name__ == "__main__":
    test_prompt()
    test_safety_checks_unchanged()
    print("test_prompt PASSED; test_safety_checks_unchanged PASSED")
