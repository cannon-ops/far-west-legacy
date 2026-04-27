"""Application version + changelog loader."""

from pathlib import Path

APP_VERSION = "0.5.1"

_CHANGELOG_PATH = Path(__file__).parent.parent / "CHANGELOG.md"


def load_changelog() -> str:
    """Read CHANGELOG.md from repo root. Returns empty string if missing."""
    try:
        return _CHANGELOG_PATH.read_text(encoding="utf-8")
    except FileNotFoundError:
        return ""


# Bundle changelog at startup (read once, served from memory)
CHANGELOG_TEXT = load_changelog()
